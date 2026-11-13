"""Label↔Wert-Zuordnung über Wortpositionen.

Warum positionsbasiert? In gescannten Dokumenten mit fremder OCR-Textebene
stehen Beschriftungen und Werte in der Textreihenfolge oft getrennt (erst alle
Labels, dann alle Werte). Die Zuordnung erfolgt daher ausschließlich über die
Lage der Wörter auf der Seite:

* Wert rechts neben dem Label (gleiche Höhe, Toleranz für versetzte Grundlinien),
  auch nach großer Lücke (zweispaltige Tabellen),
* Abbruch bei sehr großer Lücke (> ~8 % der Seitenbreite) oder am nächsten Label,
* ersatzweise Wert in der Zeile darunter (Label über Wert),
* Abschnittsüberschriften („Versicherte Person“, „Versicherungsnehmer“ …)
  ordnen allgemeine Labels („Name“, „Anschrift“ …) dem richtigen Feld zu.

Koordinaten: Seitenkoordinaten, Ursprung oben links (siehe ``core.models``).
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, ConfigDict, Field

from insurance_app.core.models import BoundingBox, PageText, Word
from insurance_app.core.textutil import normalize_ws

from .normalize import compact_key, is_placeholder

__all__ = [
    "LAYOUT_CONFIDENCE",
    "LayoutMatch",
    "Line",
    "PageGeometry",
    "find_label_values",
    "group_lines",
    "estimate_skew",
    "page_geometry",
    "union_bbox",
]

# Heuristische Regel-Konfidenzen (NICHT kalibriert)
LAYOUT_CONFIDENCE: dict[str, float] = {
    "layout_right": 0.85,
    "layout_right_colon_tight": 0.8,
    "layout_inline": 0.6,
    "layout_below": 0.7,
}
FUZZY_FACTOR = 0.9
MAX_LABEL_WORDS = 7
FUZZY_MIN_CHARS = 14
TALL_WORD_FACTOR = 4.0  # Wörter, die viel höher als üblich sind (gedrehte Wasserzeichen), ignorieren
MAX_SKEW_SLOPE = math.tan(math.radians(5.0))
MIN_SKEW_SLOPE = 0.0015  # darunter gilt die Seite als gerade


class Line(BaseModel):
    """Eine visuelle Zeile (Wörter mit ähnlicher vertikaler Mitte, nach x sortiert)."""

    model_config = ConfigDict(extra="forbid")

    words: list[Word]
    bbox: BoundingBox
    text: str

    def word_spans(self) -> list[tuple[int, int]]:
        """Zeichenbereiche (Start, Ende) jedes Wortes in ``text``."""
        spans: list[tuple[int, int]] = []
        pos = 0
        for w in self.words:
            spans.append((pos, pos + len(w.text)))
            pos += len(w.text) + 1
        return spans

    def words_in_span(self, start: int, end: int) -> list[Word]:
        """Wörter, die den Zeichenbereich [start, end) berühren."""
        return [w for w, (s, e) in zip(self.words, self.word_spans(), strict=True) if s < end and e > start]


class LayoutMatch(BaseModel):
    """Ein gefundenes Label mit zugeordnetem Wert (oder Platzhalter)."""

    model_config = ConfigDict(extra="forbid")

    field_key: str
    label_text: str
    value_text: str
    value_bbox: BoundingBox | None = None
    label_bbox: BoundingBox
    page: int
    rule_id: str
    confidence: float
    placeholder: bool = False
    placeholder_text: str | None = None  # z. B. "Unbekannt"; "" bei leerem Wert
    value_words: list[Word] = Field(default_factory=list)
    continuation_text: str | None = None  # Folgezeile in der Wertspalte (z. B. "12345 Musterstadt")
    continuation_words: list[Word] = Field(default_factory=list)
    section: str | None = None  # Abschnittsüberschrift, über die das Label zugeordnet wurde
    tight: bool = False  # Wert folgt ohne Abstand/Doppelpunkt (Fließtext möglich)


def union_bbox(words: Iterable[Word]) -> BoundingBox | None:
    box: BoundingBox | None = None
    for w in words:
        box = w.bbox if box is None else box.union(w.bbox)
    return box


# ---------------------------------------------------------------------------
# Geometrie
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PageGeometry:
    """Abgeleitete Toleranzen einer Seite."""

    median_height: float
    tol_y: float  # vertikale Toleranz für "gleiche Zeile"
    seg_gap: float  # Lücke, die Spalten/Segmente trennt
    max_gap: float  # Lücke, ab der ein Wert endet (~8 % Seitenbreite)
    below_max: float  # maximaler Abstand für "Wert in der Zeile darunter"


def _usable(words: Sequence[Word]) -> list[Word]:
    return [w for w in words if w.text.strip() and w.bbox.height > 0 and w.bbox.width >= 0]


def page_geometry(words: Sequence[Word], page_width: float) -> PageGeometry:
    heights = [w.bbox.height for w in _usable(words)]
    med = statistics.median(heights) if heights else 10.0
    med = max(med, 1.0)
    return PageGeometry(
        median_height=med,
        tol_y=max(0.5 * med, 1.0),
        seg_gap=1.2 * med,
        max_gap=max(0.08 * page_width, 2.0 * med),
        below_max=2.4 * med,
    )


def _regular_words(words: Sequence[Word], geo: PageGeometry) -> list[Word]:
    limit = TALL_WORD_FACTOR * geo.median_height
    return [w for w in _usable(words) if w.bbox.height <= limit]


def estimate_skew(words: Sequence[Word]) -> float:
    """Neigung der Textzeilen (dy/dx) aus benachbarten Wörtern schätzen.

    Für leicht schräg gescannte Seiten (bis ca. 5°). Gibt 0 zurück, wenn die Seite
    gerade ist oder zu wenig Wörter vorhanden sind.
    """
    usable = _usable(words)
    if len(usable) < 8:
        return 0.0
    med = max(statistics.median(w.bbox.height for w in usable), 1.0)
    regular = sorted(
        (w for w in usable if w.bbox.height <= TALL_WORD_FACTOR * med), key=lambda w: w.bbox.center_y
    )
    centers = [w.bbox.center_y for w in regular]
    window = 0.8 * med
    slopes: list[float] = []
    for w in regular:
        cy, cx = w.bbox.center_y, (w.bbox.x0 + w.bbox.x1) / 2
        lo = bisect.bisect_left(centers, cy - window)
        hi = bisect.bisect_right(centers, cy + window)
        best: tuple[float, Word] | None = None
        for o in regular[lo:hi]:
            if o is w:
                continue
            gap = o.bbox.x0 - w.bbox.x1
            if 0 <= gap <= 3.0 * med and (best is None or gap < best[0]):
                best = (gap, o)
        if best is not None:
            o = best[1]
            dx = (o.bbox.x0 + o.bbox.x1) / 2 - cx
            if dx > med:
                slopes.append((o.bbox.center_y - cy) / dx)
    if len(slopes) < 10:
        return 0.0
    slope = statistics.median(slopes)
    if abs(slope) < MIN_SKEW_SLOPE:
        return 0.0
    return max(-MAX_SKEW_SLOPE, min(MAX_SKEW_SLOPE, slope))


def _corrected_cy(w: Word, slope: float) -> float:
    return w.bbox.center_y - slope * (w.bbox.x0 + w.bbox.x1) / 2


def group_lines(words: list[Word], skew: float | None = None) -> list[Line]:
    """Wörter zu visuellen Zeilen gruppieren (robust gegen versetzte Grundlinien).

    Gruppiert wird über die vertikale Mitte mit einer Toleranz von etwa der
    halben mittleren Worthöhe. Leichte Schräglage (Scan) wird herausgerechnet
    (``skew`` = dy/dx; ``None`` = automatisch schätzen). Ungewöhnlich hohe Wörter
    (z. B. gedrehte Wasserzeichen) bilden eigene Zeilen.
    """
    usable = _usable(words)
    if not usable:
        return []
    slope = estimate_skew(usable) if skew is None else skew
    med = max(statistics.median(w.bbox.height for w in usable), 1.0)
    tol = max(0.5 * med, 1.0)
    tall_limit = TALL_WORD_FACTOR * med
    regular = sorted(
        ((w, _corrected_cy(w, slope)) for w in usable if w.bbox.height <= tall_limit), key=lambda wc: wc[1]
    )
    tall = [w for w in usable if w.bbox.height > tall_limit]

    clusters: list[list[Word]] = []
    centers: list[float] = []
    for w, cy in regular:
        if clusters and abs(cy - centers[-1]) <= tol:
            clusters[-1].append(w)
            n = len(clusters[-1])
            centers[-1] = centers[-1] + (cy - centers[-1]) / n
        else:
            clusters.append([w])
            centers.append(cy)
    clusters.extend([w] for w in tall)
    centers.extend(w.bbox.center_y for w in tall)

    lines: list[tuple[float, Line]] = []
    for idx, cluster in enumerate(clusters):
        ws = sorted(cluster, key=lambda w: (w.bbox.x0, w.bbox.y0))
        box = union_bbox(ws)
        assert box is not None
        lines.append((centers[idx], Line(words=ws, bbox=box, text=" ".join(w.text for w in ws))))
    lines.sort(key=lambda cl: (cl[0], cl[1].bbox.x0))
    return [ln for _, ln in lines]


# ---------------------------------------------------------------------------
# Phrasen-Tabelle
# ---------------------------------------------------------------------------


@dataclass
class _Phrase:
    key: str
    text: str
    global_key: str | None = None
    heading: str | None = None
    section_keys: dict[str, str] = field(default_factory=dict)
    stop: bool = False

    @property
    def label_capable(self) -> bool:
        return self.global_key is not None or bool(self.section_keys)


class _PhraseTable:
    def __init__(
        self,
        labels: dict[str, list[str]],
        section_labels: dict[str, dict[str, list[str]]] | None,
        stop_labels: Iterable[str],
    ) -> None:
        self.phrases: dict[str, _Phrase] = {}
        self.generic: set[str] = set()
        for fkey, phrases in labels.items():
            for p in phrases:
                ph = self._get(p)
                if ph.global_key is None:
                    ph.global_key = fkey
        for heading, mapping in (section_labels or {}).items():
            self._get(heading).heading = heading
            for fkey, phrases in mapping.items():
                for p in phrases:
                    ph = self._get(p)
                    ph.section_keys.setdefault(heading, fkey)
                    self.generic.add(ph.key)
        for p in stop_labels:
            self._get(p).stop = True
        self.prefixes: set[str] = set()
        self.max_len = 0
        for k in self.phrases:
            self.max_len = max(self.max_len, len(k))
            for i in range(1, len(k) + 1):
                self.prefixes.add(k[:i])
        self.fuzzy_index: dict[str, list[str]] = {}
        for k in self.phrases:
            if len(k) >= FUZZY_MIN_CHARS:
                self.fuzzy_index.setdefault(k[:3], []).append(k)

    def _get(self, text: str) -> _Phrase:
        key = compact_key(text)
        if not key:
            raise ValueError("Leere Label-Phrase")
        if key not in self.phrases:
            self.phrases[key] = _Phrase(key=key, text=normalize_ws(text))
        return self.phrases[key]


def _within_one_edit(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = edits = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if la == lb:
            i += 1
        j += 1
    return edits + (lb - j) + (la - i) <= 1


# ---------------------------------------------------------------------------
# Token und Treffer
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _Tok:
    word: Word
    ck: str
    x0: float
    x1: float
    cy: float
    colon: bool

    @classmethod
    def of(cls, w: Word, slope: float = 0.0) -> _Tok:
        t = w.text.strip()
        return cls(
            word=w,
            ck=compact_key(t),
            x0=w.bbox.x0,
            x1=w.bbox.x1,
            cy=_corrected_cy(w, slope),
            colon=t.endswith(":"),
        )


@dataclass
class _Hit:
    end: int  # exklusiver Index im Token-Strang
    phrase: _Phrase
    fuzzy: bool


@dataclass
class _Side:
    words: list[_Tok]
    present: bool  # gab es überhaupt Wörter in dieser Richtung?
    first_is_label: bool
    colon: bool = False
    tight: bool = False


class _Matcher:
    def __init__(self, table: _PhraseTable, geo: PageGeometry) -> None:
        self.table = table
        self.geo = geo

    @staticmethod
    def gap(a: _Tok, b: _Tok) -> float:
        return b.x0 - a.x1

    def match_at(self, seq: Sequence[_Tok], i: int) -> _Hit | None:
        if i >= len(seq) or not seq[i].ck:
            return None
        phrases = self.table.phrases
        best: _Hit | None = None
        acc = ""
        accs: list[tuple[int, str]] = []
        for j in range(i, min(len(seq), i + MAX_LABEL_WORDS)):
            if j > i and self.gap(seq[j - 1], seq[j]) > self.geo.seg_gap:
                break
            acc += seq[j].ck
            if not seq[j].ck and j > i:
                continue  # Trenner innerhalb einer Phrase ("Name / Firma")
            accs.append((j + 1, acc))
            if acc in phrases:
                best = _Hit(end=j + 1, phrase=phrases[acc], fuzzy=False)
            if acc in self.table.prefixes:
                continue
            if len(acc) >= 3 and acc[:3] in self.table.fuzzy_index and len(acc) <= self.table.max_len + 1:
                continue
            break
        if best is not None:
            return best
        for end, a in accs:
            if len(a) < FUZZY_MIN_CHARS:
                continue
            for k in self.table.fuzzy_index.get(a[:3], ()):
                if _within_one_edit(a, k):
                    return _Hit(end=end, phrase=phrases[k], fuzzy=True)
        return None

    def separated_after(self, seq: Sequence[_Tok], end: int) -> bool:
        """Endet die Phrase mit Abstand, Doppelpunkt oder am Ende? (typisch für Labels)"""
        last = seq[end - 1]
        if last.colon or end >= len(seq):
            return True
        nxt = seq[end]
        if not nxt.ck:  # Trenner wie ":" oder "|"
            return True
        return self.gap(last, nxt) > self.geo.seg_gap

    def label_start(self, seq: Sequence[_Tok], i: int) -> _Hit | None:
        hit = self.match_at(seq, i)
        if hit is not None and self.separated_after(seq, hit.end):
            return hit
        return None


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------


@dataclass
class _RawHit:
    toks: list[_Tok]
    phrase: _Phrase
    fuzzy: bool
    colon: bool
    bbox: BoundingBox
    kind: str = ""  # "heading" | "label"
    rule: str = ""
    value: list[_Tok] = field(default_factory=list)
    placeholder_empty: bool = False
    continuation: list[_Tok] = field(default_factory=list)
    tight: bool = False

    @property
    def cy(self) -> float:
        """Vertikale Mitte (schräglagenbereinigt)."""
        return sum(t.cy for t in self.toks) / len(self.toks)


def find_label_values(
    page: PageText,
    labels: dict[str, list[str]],
    section_labels: dict[str, dict[str, list[str]]] | None = None,
    *,
    stop_labels: Iterable[str] = (),
) -> list[LayoutMatch]:
    """Labels auf einer Seite finden und ihnen Werte über die Wortposition zuordnen.

    ``labels``: Feldschlüssel -> Label-Phrasen (überall gültig).
    ``section_labels``: Überschrift -> {Feldschlüssel -> Phrasen}; gilt für Labels
    unterhalb der Überschrift (gleiche Spalte) bis zur nächsten Überschrift.
    Feldschlüssel, die mit ``_`` beginnen, unterdrücken ein Label im Abschnitt.
    ``stop_labels``: bekannte Labels ohne Feld; sie begrenzen Werte.
    """
    table = _PhraseTable(labels, section_labels, stop_labels)
    geo = page_geometry(page.words, page.width)
    words = _regular_words(page.words, geo)
    if not words:
        return []
    matcher = _Matcher(table, geo)
    slope = estimate_skew(words)
    toks = [_Tok.of(w, slope) for w in words]
    tok_by_id = {id(t.word): t for t in toks}
    lines = group_lines(words, skew=slope)
    pitch = _line_pitch(lines, slope, geo)
    if pitch is not None:
        below = min(max(geo.below_max, 1.5 * pitch), 4.0 * geo.median_height)
        geo = replace(geo, below_max=below)
        matcher.geo = geo

    raw_hits: list[_RawHit] = []
    for line in lines:
        seq = [tok_by_id[id(w)] for w in line.words]
        i = 0
        while i < len(seq):
            hit = matcher.match_at(seq, i)
            if hit is None:
                i += 1
                continue
            seg_start = i == 0 or not seq[i - 1].ck or matcher.gap(seq[i - 1], seq[i]) > geo.seg_gap
            colon_end = seq[hit.end - 1].colon or (
                hit.end < len(seq) and seq[hit.end].word.text.strip() == ":"
            )
            if not (seg_start or colon_end):
                i = hit.end  # Phrase mitten im Fließtext: verbrauchen, keine Teiltreffer
                continue
            label_toks = seq[i : hit.end]
            box = union_bbox(t.word for t in label_toks)
            assert box is not None
            if not hit.phrase.stop or hit.phrase.label_capable or hit.phrase.heading:
                raw_hits.append(
                    _RawHit(toks=label_toks, phrase=hit.phrase, fuzzy=hit.fuzzy, colon=colon_end, bbox=box)
                )
            i = hit.end

    headings: list[tuple[str, BoundingBox, float]] = []
    for rh in raw_hits:
        if rh.phrase.stop and not rh.phrase.label_capable:
            continue
        _classify(rh, toks, matcher, geo)
        if rh.kind == "heading" and rh.phrase.heading:
            headings.append((rh.phrase.heading, rh.bbox, rh.cy))

    out: list[LayoutMatch] = []
    for rh in raw_hits:
        if rh.kind != "label":
            continue
        section = _section_for(rh.bbox, rh.cy, headings, geo, page.width)
        key, used_section = _resolve_key(rh.phrase, section, table)
        if key is None:
            continue
        out.append(_to_match(rh, key, section if used_section else None, page.page_number))
    return out


def _line_pitch(lines: list[Line], slope: float, geo: PageGeometry) -> float | None:
    """Typischer Zeilenabstand (Median der Abstände benachbarter Zeilen)."""
    centers = sorted(
        sum(_corrected_cy(w, slope) for w in ln.words) / len(ln.words)
        for ln in lines
        if all(w.bbox.height <= TALL_WORD_FACTOR * geo.median_height for w in ln.words)
    )
    diffs = [b - a for a, b in zip(centers, centers[1:], strict=False) if b - a > geo.tol_y]
    if len(diffs) < 3:
        return None
    return statistics.median(diffs)


def _band_right(
    label: BoundingBox, cy: float, exclude: set[int], toks: Sequence[_Tok], geo: PageGeometry
) -> list[_Tok]:
    band = [
        t for t in toks if id(t) not in exclude and abs(t.cy - cy) <= geo.tol_y and t.x0 >= label.x1 - 0.5
    ]
    band.sort(key=lambda t: t.x0)
    return band


def _collect(seq: list[_Tok], matcher: _Matcher, geo: PageGeometry, max_gap: float) -> _Side:
    """Wert ab dem Anfang von ``seq`` einsammeln."""
    colon = False
    k = 0
    while k < len(seq) and not seq[k].ck:
        colon = colon or seq[k].word.text.strip().startswith(":")
        k += 1
    seq = seq[k:]
    if not seq:
        return _Side(words=[], present=k > 0, first_is_label=False, colon=colon)
    if matcher.label_start(seq, 0):
        return _Side(words=[], present=True, first_is_label=True, colon=colon)
    vals = [seq[0]]
    for idx in range(1, len(seq)):
        g = matcher.gap(seq[idx - 1], seq[idx])
        if g > max_gap:
            break
        hit = matcher.match_at(seq, idx) if seq[idx].ck else None
        if hit is not None:
            colon_label = seq[hit.end - 1].colon or (
                hit.end < len(seq) and seq[hit.end].word.text.strip() == ":"
            )
            if colon_label or (g > geo.seg_gap and matcher.separated_after(seq, hit.end)):
                break  # nächstes Label (andere Spalte oder "… Geburtsdatum: …")
        vals.append(seq[idx])
    # abschließende reine Trenner entfernen
    while vals and not vals[-1].ck and vals[-1].word.text.strip() not in {")", "€", "%"}:
        vals.pop()
    return _Side(words=vals, present=True, first_is_label=False, colon=colon)


def _right(rh: _RawHit, toks: Sequence[_Tok], matcher: _Matcher, geo: PageGeometry) -> _Side:
    exclude = {id(t) for t in rh.toks}
    band = _band_right(rh.bbox, rh.cy, exclude, toks, geo)
    side = _collect(band, matcher, geo, geo.max_gap)
    side.colon = side.colon or rh.colon
    if side.words:
        side.tight = (side.words[0].x0 - rh.bbox.x1) <= geo.seg_gap and not side.colon
    return side


def _below(rh: _RawHit, toks: Sequence[_Tok], matcher: _Matcher, geo: PageGeometry) -> _Side:
    lb = rh.bbox
    cy = rh.cy
    tol_x = 0.5 * geo.median_height
    exclude = {id(t) for t in rh.toks}

    def overlaps(t: _Tok) -> bool:
        return t.x1 > lb.x0 - tol_x and t.x0 < lb.x1 + tol_x

    cands = [
        t
        for t in toks
        if id(t) not in exclude and geo.tol_y < (t.cy - cy) <= geo.below_max and overlaps(t) and t.ck
    ]
    if not cands:
        return _Side(words=[], present=False, first_is_label=False)
    top = min(t.cy for t in cands)
    row = sorted(
        (t for t in toks if abs(t.cy - top) <= geo.tol_y and id(t) not in exclude), key=lambda t: t.x0
    )
    start = next(idx for idx, t in enumerate(row) if overlaps(t) and t.ck)
    return _collect(row[start:], matcher, geo, geo.seg_gap)


def _continuation(rh: _RawHit, toks: Sequence[_Tok], matcher: _Matcher, geo: PageGeometry) -> list[_Tok]:
    """Folgezeile in der Wertspalte, wenn die Labelspalte dort leer ist."""
    if not rh.value:
        return []
    v0 = rh.value[0]
    vcy = sum(t.cy for t in rh.value) / len(rh.value)
    tol_x = 1.5 * geo.median_height
    used = {id(t) for t in rh.value} | {id(t) for t in rh.toks}
    starts = [
        t
        for t in toks
        if id(t) not in used
        and t.ck
        and geo.tol_y < (t.cy - vcy) <= geo.below_max
        and abs(t.x0 - v0.x0) <= tol_x
    ]
    if not starts:
        return []
    start = min(starts, key=lambda t: (t.cy, t.x0))
    row = sorted((t for t in toks if abs(t.cy - start.cy) <= geo.tol_y), key=lambda t: t.x0)
    label_col_left = rh.bbox.x0 - 0.5 * geo.median_height
    if any(t.x1 > label_col_left and t.x0 < v0.x0 - 0.5 * geo.median_height for t in row):
        return []  # Labelspalte belegt -> eigene Zeile, keine Fortsetzung
    seq = [t for t in row if t.x0 >= start.x0 - 0.5]
    side = _collect(seq, matcher, geo, geo.seg_gap)
    return side.words


def _classify(rh: _RawHit, toks: Sequence[_Tok], matcher: _Matcher, geo: PageGeometry) -> None:
    ph = rh.phrase
    right = _right(rh, toks, matcher, geo)
    if right.words:
        if not ph.label_capable:
            return  # reine Überschrift mit Text daneben -> Fließtext
        rh.kind = "label"
        rh.value = right.words
        rh.tight = right.tight
        if right.tight:
            rh.rule = "layout_inline"
        elif right.colon and (right.words[0].x0 - rh.bbox.x1) <= geo.seg_gap:
            rh.rule = "layout_right_colon_tight"
        else:
            rh.rule = "layout_right"
        rh.continuation = _continuation(rh, toks, matcher, geo)
        return

    below = _below(rh, toks, matcher, geo) if not right.present or ph.heading else None
    if below is not None and below.words and ph.label_capable and not right.first_is_label:
        rh.kind = "label"
        rh.value = below.words
        rh.rule = "layout_below"
        return
    if ph.heading:
        rh.kind = "heading"
        return
    if right.first_is_label and ph.label_capable:
        rh.kind = "label"  # Wert leer (z. B. Portal: "Beitrag" ohne Angabe)
        rh.placeholder_empty = True
        rh.rule = "layout_right"


def _section_for(
    label: BoundingBox,
    label_cy: float,
    headings: list[tuple[str, BoundingBox, float]],
    geo: PageGeometry,
    page_width: float,
) -> str | None:
    best: tuple[float, str] | None = None
    for name, hb, hcy in headings:
        if hcy >= label_cy - geo.tol_y:
            continue
        if abs(hb.x0 - label.x0) > 0.25 * page_width:
            continue
        if best is None or hcy > best[0]:
            best = (hcy, name)
    return best[1] if best else None


def _resolve_key(phrase: _Phrase, section: str | None, table: _PhraseTable) -> tuple[str | None, bool]:
    if section is not None and section in phrase.section_keys:
        key = phrase.section_keys[section]
        return (None, True) if key.startswith("_") else (key, True)
    if phrase.key in table.generic and section is not None:
        return None, False  # allgemeines Label in fremdem Abschnitt -> nicht zuordnen
    if phrase.global_key is not None and not phrase.global_key.startswith("_"):
        return phrase.global_key, False
    return None, False


def _to_match(rh: _RawHit, key: str, section: str | None, page_no: int) -> LayoutMatch:
    words = [t.word for t in rh.value]
    value_text = normalize_ws(" ".join(w.text for w in words))
    placeholder = rh.placeholder_empty or is_placeholder(value_text)
    placeholder_text: str | None = None
    if placeholder:
        placeholder_text = "" if rh.placeholder_empty else value_text
        value_text = ""
    confidence = LAYOUT_CONFIDENCE.get(rh.rule, 0.6) * (FUZZY_FACTOR if rh.fuzzy else 1.0)
    cont_words = [t.word for t in rh.continuation]
    rule_id = rh.rule if section is None else f"{rh.rule}[{section}]"
    return LayoutMatch(
        field_key=key,
        label_text=normalize_ws(" ".join(t.word.text for t in rh.toks)),
        value_text=value_text,
        value_bbox=union_bbox(words),
        label_bbox=rh.bbox,
        page=page_no,
        rule_id=rule_id,
        confidence=round(confidence, 4),
        placeholder=placeholder,
        placeholder_text=placeholder_text,
        value_words=words,
        continuation_text=normalize_ws(" ".join(w.text for w in cont_words)) or None,
        continuation_words=cont_words,
        section=section,
        tight=rh.tight,
    )
