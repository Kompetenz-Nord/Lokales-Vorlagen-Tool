"""Kandidaten-Extraktion je Dokument (regelbasiert, mit Beleg).

Ablauf je Seite:
1. Zeilen bilden (``layout.group_lines``)
2. Label↔Wert über Wortpositionen (``layout.find_label_values``) und
   Muster-Regeln (``rules.find_regex_matches``)
3. Werte normalisieren, zusammengesetzte Werte aufteilen (Name, Anschrift, Laufzeit)
4. Schutzregeln: Bankdaten nie aus Fußzeile/Briefkopf des Versicherers,
   Fließtext-Treffer nur für eindeutig lesbare strukturierte Werte

Konfidenz = Regel-Konfidenz × mittlere OCR-Wortkonfidenz der Wertwörter
(falls vorhanden) × 0,85 bei fremder OCR-Textebene. Das ist eine Heuristik,
KEINE kalibrierte Wahrscheinlichkeit.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory, FieldType
from insurance_app.core.models import (
    Candidate,
    DocumentText,
    DocumentType,
    Evidence,
    PageText,
    TextLayerOrigin,
    Word,
)
from insurance_app.core.textutil import find_dates, normalize_ws, truncate

from .layout import LAYOUT_CONFIDENCE, LayoutMatch, Line, estimate_skew, find_label_values, group_lines
from .normalize import (
    NOTE_UNREADABLE,
    frequency_from_text,
    normalize_amount,
    normalize_iban,
    normalize_value,
    split_person_name,
    split_postal_city,
    split_street_house,
)
from .rules import (
    BANK_FIELDS,
    COMPOSITE_KEYS,
    LABELS,
    RULE_CONFIDENCE,
    SECTION_LABELS,
    STOP_LABELS,
    find_creditor_line_ys,
    find_regex_matches,
    in_footer,
    in_header,
)

__all__ = ["EMBEDDED_OCR_FACTOR", "ExtractionResult", "SourceDocInfo", "extract_candidates"]

log = logging.getLogger(__name__)

EMBEDDED_OCR_FACTOR = 0.85
UNPARSABLE_FACTOR = 0.5
NOTE_COMPANY_AS_INSURED = "Firmenname bei der versicherten Person – bitte prüfen."

_STRUCTURED_TYPES = {
    FieldType.DATE,
    FieldType.MONTH,
    FieldType.AMOUNT,
    FieldType.IBAN,
    FieldType.POSTAL_CODE,
    FieldType.CONTRACT_NUMBER,
    FieldType.INTEGER,
    FieldType.CURRENCY,
    FieldType.CHOICE,
}
_IBAN_ANYWHERE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}\s?\d{2}(?:\s?[A-Z0-9]){11,30}")


@dataclass(frozen=True)
class SourceDocInfo:
    """Angaben zum Quelldokument (aus Dokumentcheck/Klassifikation)."""

    document_id: str
    file_name: str
    doc_type: DocumentType
    document_date: date | None = None
    effective_date: date | None = None


class ExtractionResult(BaseModel):
    """Kandidaten eines Dokuments je Feld, Platzhalter-Belege und technische Hinweise."""

    model_config = ConfigDict(extra="forbid")

    candidates: dict[str, list[Candidate]] = Field(default_factory=dict)
    placeholders: dict[str, list[Evidence]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def best(self, key: str) -> Candidate | None:
        """Kandidat mit der höchsten Konfidenz (oder ``None``)."""
        cands = self.candidates.get(key) or []
        return max(cands, key=lambda c: c.confidence or 0.0, default=None)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _members(key: str) -> tuple[str, ...]:
    return COMPOSITE_KEYS.get(key, (key,))


def _is_structured(key: str) -> bool:
    if key == "contract.term":
        return True
    fdef = FIELD_REGISTRY.get(key)
    return fdef is not None and fdef.dtype in _STRUCTURED_TYPES


def _word_confidence(words: list[Word]) -> float | None:
    vals = [w.confidence for w in words if w.confidence is not None]
    if not vals:
        return None
    return max(0.0, min(1.0, sum(vals) / len(vals)))


def _overlaps_y(y0: float, y1: float, ranges: list[tuple[float, float]], tol: float) -> bool:
    return any(y0 <= r1 + tol and y1 >= r0 - tol for r0, r1 in ranges)


_ADDRESS_POSTAL = re.compile(r"(?:^|[,;\s])(?:(?:D|DE)\s?-\s?)?(\d{5})\s+(\S.*)$")


def _parse_address(text: str) -> dict[str, str | None]:
    text = normalize_ws(text).strip(" ,;")
    out: dict[str, str | None] = {"street": None, "house_number": None, "postal_code": None, "city": None}
    m = _ADDRESS_POSTAL.search(text)
    street_part = text
    if m:
        out["postal_code"] = m.group(1)
        out["city"] = m.group(2).strip(" ,;") or None
        street_part = text[: m.start()].strip(" ,;")
    if street_part:
        street, house = split_street_house(street_part)
        out["street"] = street or None
        out["house_number"] = house
    return out


class _DocExtractor:
    def __init__(self, info: SourceDocInfo) -> None:
        self.info = info
        self.candidates: dict[str, list[Candidate]] = {}
        self.placeholders: dict[str, list[Evidence]] = {}
        self.warnings: list[str] = []
        self.insurer_ibans: set[str] = set()
        self._bank_dropped_pages: set[int] = set()

    # -- Kandidaten ------------------------------------------------------
    def _evidence(self, page: PageText, m: LayoutMatch, rule_id: str | None = None, quote: str | None = None):
        if quote is None:
            parts = [m.label_text, m.value_text]
            if m.continuation_text and m.field_key.endswith(".address"):
                parts.append(m.continuation_text)
            quote = " ".join(p for p in parts if p)
        return Evidence(
            document_id=self.info.document_id,
            file_name=self.info.file_name,
            page=page.page_number,
            quote=truncate(quote, 160) if quote else None,
            bbox=m.value_bbox if m.value_bbox is not None else m.label_bbox,
            method=page.method,
            model_version=page.engine_version,
            rule_id=rule_id or m.rule_id,
        )

    def _add(
        self,
        key: str,
        raw: str | None,
        normalized: str | None,
        confidence: float,
        evidence: Evidence,
        note: str | None = None,
    ) -> None:
        fdef = FIELD_REGISTRY.get(key)
        if fdef is None or fdef.category != FieldCategory.CONTRACT or not fdef.extractable:
            return  # Auftragsdaten nie automatisch
        raw = normalize_ws(raw or "")
        if not raw and normalized is None:
            return
        if normalized is None:
            note = NOTE_UNREADABLE
            confidence *= UNPARSABLE_FACTOR
        cand = Candidate(
            field_key=key,
            value=raw or (normalized or ""),
            normalized=normalized,
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            evidence=evidence,
            document_type=self.info.doc_type,
            document_date=self.info.document_date,
            effective_date=self.info.effective_date,
            note=note,
        )
        self.candidates.setdefault(key, []).append(cand)

    def _add_simple(self, key: str, raw: str, conf: float, ev: Evidence, note: str | None = None) -> None:
        fdef = FIELD_REGISTRY[key]
        normalized, norm_note = normalize_value(fdef, raw)
        self._add(key, raw, normalized, conf, ev, note or norm_note)

    # -- Seite -----------------------------------------------------------
    def page(self, page: PageText) -> None:
        if not page.words:
            self.warnings.append(f"Seite {page.page_number}: kein lesbarer Text – keine Werte übernommen.")
            return
        lines = group_lines(page.words, skew=estimate_skew(page.words))
        creditor = find_creditor_line_ys(lines)
        self._collect_insurer_ibans(page, lines, creditor)
        matches = find_label_values(page, LABELS, SECTION_LABELS, stop_labels=STOP_LABELS)
        matches.extend(find_regex_matches(page, lines))
        ocr_factor = EMBEDDED_OCR_FACTOR if page.origin == TextLayerOrigin.EMBEDDED_OCR else 1.0
        for m in matches:
            try:
                self._convert(page, lines, creditor, m, ocr_factor)
            except (ValueError, ArithmeticError) as exc:  # robust: eine Regel darf die Seite nicht abbrechen
                log.warning(
                    "Extraktionsregel übersprungen (%s, Seite %d, Regel %s)",
                    type(exc).__name__,
                    page.page_number,
                    m.rule_id,
                )

    def _collect_insurer_ibans(self, page: PageText, lines: list[Line], creditor: list[tuple[float, float]]):
        for line in lines:
            in_letterhead = in_footer(page, line.bbox.y0) or _overlaps_y(
                line.bbox.y0, line.bbox.y1, creditor, 0
            )
            if not in_letterhead:
                continue
            for m in _IBAN_ANYWHERE.finditer(line.text.upper()):
                iban = normalize_iban(m.group(0))
                if iban:
                    self.insurer_ibans.add(iban)

    def _bank_allowed(
        self, page: PageText, lines: list[Line], creditor: list[tuple[float, float]], m: LayoutMatch
    ) -> bool:
        boxes = [m.label_bbox] + ([m.value_bbox] if m.value_bbox else [])
        for box in boxes:
            if in_footer(page, box.y0):
                return False
            if _overlaps_y(box.y0, box.y1, creditor, 1.0):
                return False
            if in_header(page, box.y1):
                for line in lines:
                    if line.bbox.y0 <= box.y1 and line.bbox.y1 >= box.y0 and "BIC" in line.text.upper():
                        return False
        return True

    def _convert(
        self,
        page: PageText,
        lines: list[Line],
        creditor: list[tuple[float, float]],
        m: LayoutMatch,
        ocr_factor: float,
    ) -> None:
        key = m.field_key
        if key in BANK_FIELDS and not self._bank_allowed(page, lines, creditor, m):
            self._bank_dropped_pages.add(page.page_number)
            return
        if m.placeholder:
            quote = f"{m.label_text} {m.placeholder_text or ''}".strip()
            ev = self._evidence(page, m, quote=quote)
            for member in _members(key):
                if member == "policyholder.salutation" or member == "policyholder.legal_form":
                    continue
                self.placeholders.setdefault(member, []).append(ev)
            return
        if m.tight and not _is_structured(key):
            return  # Fließtext ohne Trennzeichen: nur eindeutig strukturierte Werte übernehmen
        words = m.value_words + (m.continuation_words if key.endswith(".address") else [])
        wc = _word_confidence(words)
        base = m.confidence * (wc if wc is not None else 1.0) * ocr_factor
        ev = self._evidence(page, m)
        value = m.value_text

        if key in ("policyholder", "insured"):
            self._person(key, value, base, ev)
        elif key.endswith(".address"):
            self._address(key.split(".")[0], value, m.continuation_text, base, ev)
        elif key.endswith(".postal_city"):
            prefix = key.split(".")[0]
            plz, city = split_postal_city(value)
            if plz is None and city is None:
                self._add(f"{prefix}.postal_code", value, None, base, ev)
                return
            if plz:
                self._add_simple(f"{prefix}.postal_code", plz, base, ev)
            if city:
                self._add_simple(f"{prefix}.city", city, base, ev)
        elif key == "contract.term":
            self._term(value, m, base, page)
        elif key == "premium.amount":
            self._premium(value, m, base, ev, page)
        else:
            fdef = FIELD_REGISTRY[key]
            normalized, note = normalize_value(fdef, value)
            if m.tight and normalized is None:
                return
            self._add(key, value, normalized, base, ev, note)

    def _person(self, key: str, value: str, base: float, ev: Evidence) -> None:
        split = split_person_name(value)
        conf = base * split.confidence
        if key == "policyholder":
            if split.is_company:
                self._add_simple("policyholder.name", value, base * 0.9, ev)
                if split.legal_form:
                    self._add_simple("policyholder.legal_form", split.legal_form, base * 0.8, ev)
                return
            if not split.last:
                self._add("policyholder.name", value, None, base, ev)
                return
            self._add_simple("policyholder.name", split.last, conf, ev, split.note)
            if split.first:
                self._add_simple("policyholder.first_name", split.first, conf, ev, split.note)
            if split.salutation:
                self._add_simple("policyholder.salutation", split.salutation, base * 0.8, ev)
            return
        if split.is_company:
            self._add_simple("insured.last_name", value, base * 0.3, ev, NOTE_COMPANY_AS_INSURED)
            return
        if not split.last:
            self._add("insured.last_name", value, None, base, ev)
            return
        self._add_simple("insured.last_name", split.last, conf, ev, split.note)
        if split.first:
            self._add_simple("insured.first_name", split.first, conf, ev, split.note)

    def _address(self, prefix: str, value: str, continuation: str | None, base: float, ev: Evidence) -> None:
        text = value if not continuation else f"{value}, {continuation}"
        parts = _parse_address(text)
        if not parts["street"] and not parts["postal_code"]:
            self._add(f"{prefix}.street", value, None, base, ev)
            return
        for part, raw in parts.items():
            if raw:
                self._add_simple(f"{prefix}.{part}", raw, base, ev)

    def _term(self, value: str, m: LayoutMatch, base: float, page: PageText) -> None:
        dates = [d for d, _ in find_dates(value)]
        if len(dates) < 2:
            return
        conf = base
        if not m.rule_id.startswith("laufzeit_range"):
            conf = base / max(m.confidence, 1e-6) * min(m.confidence, RULE_CONFIDENCE["laufzeit_range"])
        ev = self._evidence(page, m, rule_id="laufzeit_range")
        self._add("contract.start_date", value, dates[0].isoformat(), conf, ev)
        self._add("contract.end_date", value, dates[1].isoformat(), conf, ev)

    def _premium(self, value: str, m: LayoutMatch, base: float, ev: Evidence, page: PageText) -> None:
        amount, currency = normalize_amount(value)
        if amount is None and m.tight:
            return
        note = f"Währung laut Quelle: {currency}" if currency not in (None, "EUR") else None
        self._add("premium.amount", value, amount, base, ev, note)
        if amount is None:
            return
        if currency:
            self._add("premium.currency", currency, currency, base, ev)
        freq = frequency_from_text(value) or frequency_from_text(m.label_text)
        if freq:
            factor = RULE_CONFIDENCE["premium_frequency_from_label"] / LAYOUT_CONFIDENCE["layout_right"]
            fev = self._evidence(page, m, rule_id="premium_frequency_from_label")
            self._add("premium.frequency", f"{m.label_text} {value}", freq, base * factor, fev)

    # -- Abschluss -------------------------------------------------------
    def finish(self) -> ExtractionResult:
        if "bank.iban" in self.candidates and self.insurer_ibans:
            kept = [c for c in self.candidates["bank.iban"] if c.normalized not in self.insurer_ibans]
            if len(kept) != len(self.candidates["bank.iban"]):
                self.warnings.append(
                    "Eine IBAN entspricht der Bankverbindung des Versicherers (Fußzeile) und wurde nicht übernommen."
                )
            self.candidates["bank.iban"] = kept
        for page_no in sorted(self._bank_dropped_pages):
            self.warnings.append(
                f"Seite {page_no}: Bankangaben aus Briefkopf/Fußzeile des Versicherers wurden ignoriert."
            )
        deduped: dict[str, list[Candidate]] = {}
        for key, cands in self.candidates.items():
            best: dict[tuple[str, str], Candidate] = {}
            for c in cands:
                dk = ("n", c.normalized) if c.normalized is not None else ("r", c.value.casefold())
                prev = best.get(dk)
                if prev is None or (c.confidence or 0.0) > (prev.confidence or 0.0):
                    best[dk] = c
            if best:
                deduped[key] = sorted(best.values(), key=lambda c: -(c.confidence or 0.0))
        placeholders = {k: v for k, v in self.placeholders.items() if k not in deduped}
        return ExtractionResult(candidates=deduped, placeholders=placeholders, warnings=self.warnings)


def extract_candidates(doc_text: DocumentText, info: SourceDocInfo) -> ExtractionResult:
    """Alle Vertragsdaten-Kandidaten eines Dokuments mit Beleg ermitteln.

    Auftragsdaten (``order.*``) werden nie erzeugt. Platzhalter wie „Unbekannt“
    werden als Beleg unter ``placeholders`` geführt (kein Kandidat).
    """
    started = time.perf_counter()
    ex = _DocExtractor(info)
    for page in sorted(doc_text.pages, key=lambda p: p.page_number):
        ex.page(page)
    result = ex.finish()
    log.debug(
        "Extraktion fertig: Dokument %s, %d Seiten, %d Felder, %.0f ms",
        info.document_id,
        len(doc_text.pages),
        len(result.candidates),
        (time.perf_counter() - started) * 1000,
    )
    return result
