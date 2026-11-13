"""Dokumentklassifikation über Schlüsselwörter (regelbasiert, ohne KI).

Vorgehen
--------
* Der Text wird mit ``core.textutil.fold`` verglichen (Kleinschreibung,
  Umlaute ``ä -> ae`` usw.). So treffen "Vertragsänderung",
  "VERTRAGSAENDERUNG" und "Vertragsaenderung" gleichermaßen.
* Jedes Signalwort (``_Cue``) hat ein Gewicht. Fundstellen im Kopfbereich der
  ersten Seite (erste ``max(20, 40 %)`` Zeilen) zählen doppelt, der Rest der
  ersten Seite einfach, Folgeseiten halb. Jedes Signalwort zählt einmal mit
  seiner besten Fundstelle, Wiederholungen geben einen kleinen Bonus.
* Überlappen sich Treffer, gilt nur der längste (z. B. verdrängt
  "Nachtrag zum Versicherungsschein" das kürzere "Versicherungsschein").
* ``confidence`` ist ein **nicht kalibrierter** Heuristikwert 0..1 aus
  Stärke des besten Typs und Abstand zum zweitbesten. ``uncertain`` ist
  ``True``, wenn der beste Wert schwach ist (< 4) oder der zweitbeste mehr
  als 70 % davon erreicht.

Zusätzlich werden Briefdatum, Wirksamkeitsdatum ("gültig ab" …),
Vertragsnummern (nach typischen Bezeichnungen) und ein Versicherer-Hinweis
gelesen. Alle Werte sind Hinweise und werden später vom Menschen geprüft.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta

from ..core.models import ClassificationResult, DocumentText, DocumentType
from ..core.textutil import find_dates, fold, normalize_ws

log = logging.getLogger(__name__)

#: Mindestpunktzahl, ab der überhaupt ein Typ vergeben wird.
MIN_SCORE = 2.0
#: Ab dieser Punktzahl gilt die Zuordnung als "stark".
STRONG_SCORE = 4.0
#: Erreicht der Zweitbeste mehr als diesen Anteil des Besten, ist das Ergebnis unsicher.
AMBIGUITY_RATIO = 0.7

_TITLE_FACTOR = 2.0
_FIRST_PAGE_FACTOR = 1.0
_LATER_PAGE_FACTOR = 0.5
_REPEAT_BONUS = 0.15
_MAX_REPEAT_BONUS_COUNT = 3


@dataclass(frozen=True)
class _Cue:
    doc_type: DocumentType
    label: str
    pattern: re.Pattern[str]
    weight: float


def _cue(doc_type: DocumentType, label: str, pattern: str, weight: float) -> _Cue:
    return _Cue(doc_type, label, re.compile(pattern), weight)


_P = DocumentType.POLICY
_A = DocumentType.AMENDMENT
_N = DocumentType.PREMIUM_NOTICE
_S = DocumentType.STATUS_REPORT
_C = DocumentType.CANCELLATION_CONFIRMATION
_O = DocumentType.PORTAL_OVERVIEW

# Muster arbeiten auf gefaltetem Text (siehe core.textutil.fold).
_NO_NUMBER_LABEL = r"(?!\s*-?\s*(?:nr\b|nr\.|nummer))"
_CUES: tuple[_Cue, ...] = (
    # Police
    _cue(_P, "Versicherungsschein", r"\bversicherungsschein(?:es|s)?\b" + _NO_NUMBER_LABEL, 3.0),
    _cue(_P, "Versicherungspolice", r"\bversicherungspolice\b" + _NO_NUMBER_LABEL, 3.0),
    _cue(_P, "Police", r"\bpolice\b" + _NO_NUMBER_LABEL, 2.0),
    _cue(_P, "Versicherungsbeginn", r"\b(?:versicherungsbeginn|beginn\s+der\s+versicherung)\b", 0.5),
    # Nachtrag
    _cue(
        _A,
        "Nachtrag zum Versicherungsschein",
        r"\bnachtrag\s+zu[mr]?\s+(?:ihrem\s+)?(?:versicherungsschein|police|vertrag)",
        5.0,
    ),
    _cue(_A, "Nachtrag", r"\bnachtrag(?:s|es)?\b" + _NO_NUMBER_LABEL, 3.0),
    _cue(_A, "Vertragsänderung", r"\bvertragsaenderung(?:en)?\b", 2.5),
    _cue(_A, "Änderungsmitteilung", r"\baenderungsmitteilung\b", 1.5),
    _cue(_A, "Änderung zum", r"\baenderung\s+zum\b", 1.0),
    _cue(_A, "Änderung", r"\baenderung(?:en)?\b", 0.75),
    _cue(_A, "gültig ab", r"\bgueltig\s+ab\b", 0.75),
    # Beitragsmitteilung
    _cue(_N, "Beitragsmitteilung", r"\bbeitragsmitteilung\b", 3.0),
    _cue(_N, "Beitragsrechnung", r"\bbeitragsrechnung\b", 3.0),
    _cue(_N, "Beitragsanpassung", r"\bbeitragsanpassung\b", 2.5),
    _cue(_N, "Beitragserhöhung", r"\bbeitragserhoehung\b", 1.5),
    _cue(_N, "Dynamikerhöhung", r"\bdynamik(?:erhoehung|anpassung)\b", 1.5),
    _cue(
        _N,
        "neuer Beitrag",
        r"\bneue[rnms]?\s+(?:monatliche[rn]?\s+|jaehrliche[rn]?\s+|gesamt)?beitrag\b",
        1.5,
    ),
    # Standmitteilung
    _cue(_S, "Standmitteilung", r"\b(?:wert)?stands?mitteilung\b", 3.0),
    _cue(_S, "Jährliche Information", r"\bjaehrliche\s+(?:kunden)?(?:information|mitteilung)\b", 3.0),
    _cue(_S, "Überschussmitteilung", r"\bueberschussmitteilung\b", 3.0),
    _cue(_S, "Wertmitteilung", r"\bwertmitteilung\b", 3.0),
    _cue(_S, "Vertragsguthaben", r"\bvertragsguthaben(?:s)?\b", 1.5),
    _cue(_S, "Stand Ihrer Versicherung", r"\bstand\s+ihre[rs]\s+(?:versicherung|vertrage?s)\b", 1.5),
    _cue(_S, "Fondsguthaben", r"\bfondsguthaben(?:s)?\b", 1.0),
    _cue(_S, "Deckungskapital", r"\bdeckungskapital(?:s)?\b", 1.0),
    _cue(_S, "Überschussbeteiligung", r"\bueberschussbeteiligung\b", 0.5),
    _cue(_S, "Rückkaufswert", r"\brueckkaufswert(?:e|es|s)?\b", 0.75),
    # Kündigungsbestätigung
    _cue(_C, "Kündigungsbestätigung", r"\bkuendigungsbestaetigung\b", 4.0),
    _cue(
        _C,
        "bestätigen wir die Kündigung",
        r"\bbestaetigen\s+wir\s+(?:ihnen\s+)?(?:gerne\s+)?(?:den\s+eingang\s+)?"
        r"(?:ihre[rn]?\s+|die\s+|der\s+)?kuendigung",
        3.5,
    ),
    _cue(_C, "Ihre Kündigung", r"\bihre[rn]?\s+kuendigung\b", 2.0),
    _cue(_C, "Vertragsbeendigung", r"\bvertragsbeendigung\b", 1.0),
    _cue(_C, "Auszahlung des Rückkaufswerts", r"\bauszahlung\s+de[rs]\s+rueckkaufswert(?:e|es|s)?\b", 1.0),
    _cue(_C, "Kündigung", r"\bkuendigung\b", 0.75),
    # Portal-Übersicht
    _cue(_O, "Vertragsdetails", r"\bvertragsdetails\b", 2.0),
    _cue(_O, "Vertragsübersicht", r"\bvertragsuebersicht\b", 2.0),
    _cue(_O, "Letzte Aktualisierung", r"\bletzte\s+aktualisierung\b", 2.0),
    _cue(_O, "Vertragsinhaber", r"\bvertragsinhaber(?:in)?\b", 1.5),
    _cue(_O, "Abschlussvermittler", r"\babschlussvermittler(?:in)?\b", 1.5),
    _cue(_O, "Meine Verträge", r"\bmeine\s+vertraege\b", 1.5),
    _cue(_O, "Rückkaufswert", r"\brueckkaufswert(?:e|es|s)?\b", 1.0),
    _cue(_O, "Vertragsstatus", r"\bvertragsstatus\b", 1.0),
    _cue(_O, "Kundenportal", r"\b(?:kundenportal|online-?service|kundenbereich)\b", 1.0),
    _cue(_O, "Abmelden", r"\b(?:abmelden|logout)\b", 1.0),
)


@dataclass(frozen=True)
class _Region:
    text: str  # gefaltet
    factor: float


def _regions(text: DocumentText) -> list[_Region]:
    """Kopfbereich Seite 1, Rest Seite 1, Folgeseiten (je gefaltet)."""
    regions: list[_Region] = []
    for index, page in enumerate(text.pages):
        lines = [ln for ln in page.text.splitlines() if ln.strip()]
        if not lines:
            continue
        if index == 0:
            head_count = max(20, math.ceil(len(lines) * 0.4))
            regions.append(_Region(fold("\n".join(lines[:head_count])), _TITLE_FACTOR))
            if len(lines) > head_count:
                regions.append(_Region(fold("\n".join(lines[head_count:])), _FIRST_PAGE_FACTOR))
        else:
            regions.append(_Region(fold("\n".join(lines)), _LATER_PAGE_FACTOR))
    return regions


def _accepted_matches(region_text: str) -> list[tuple[_Cue, int, int]]:
    """Alle Treffer einer Region; bei Überlappung gewinnt der längere Treffer."""
    matches: list[tuple[_Cue, int, int]] = []
    for cue in _CUES:
        for m in cue.pattern.finditer(region_text):
            if m.end() > m.start():
                matches.append((cue, m.start(), m.end()))
    matches.sort(key=lambda t: t[2] - t[1], reverse=True)
    accepted: list[tuple[_Cue, int, int]] = []
    for cue, start, end in matches:
        length = end - start
        if any(s < end and start < e and (e - s) > length for _, s, e in accepted):
            continue
        accepted.append((cue, start, end))
    return accepted


def _score(text: DocumentText) -> tuple[dict[DocumentType, float], dict[DocumentType, list[str]]]:
    best_factor: dict[_Cue, float] = {}
    occurrences: dict[_Cue, int] = {}
    for region in _regions(text):
        for cue, _, _ in _accepted_matches(region.text):
            best_factor[cue] = max(best_factor.get(cue, 0.0), region.factor)
            occurrences[cue] = occurrences.get(cue, 0) + 1
    scores: dict[DocumentType, float] = {}
    keywords: dict[DocumentType, list[str]] = {}
    for cue, factor in best_factor.items():
        repeats = min(occurrences[cue] - 1, _MAX_REPEAT_BONUS_COUNT)
        value = cue.weight * factor + cue.weight * _REPEAT_BONUS * repeats
        scores[cue.doc_type] = scores.get(cue.doc_type, 0.0) + value
        keywords.setdefault(cue.doc_type, [])
        if cue.label not in keywords[cue.doc_type]:
            keywords[cue.doc_type].append(cue.label)
    return scores, keywords


# ---------------------------------------------------------------------------
# Datumsangaben
# ---------------------------------------------------------------------------

_DATE_LABEL = re.compile(
    r"(?:^|\W)(?:datum|briefdatum|ausstellungsdatum|erstellungsdatum|druckdatum|erstellt\s+am)\s*:?\s*$"
)
_CITY_PREFIX = re.compile(r"^\s*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\-/() ]{1,40},\s*(?:den\s+)?$")
_EXCLUDED_CONTEXT = re.compile(
    r"(?:geb(?:oren)?\.?(?:\s+am)?|geburtsdatum|geburtstag|beginn|ablauf|ende|laufzeit|"
    r"faellig(?:keit)?|frist|(?:^|\W)(?:gueltig|wirksam)\s+(?:ab|bis|vom|zum|mit)|mit\s+wirkung\s+(?:vom|zum|ab)|"
    r"(?:^|\W)(?:ab|bis|vom|von|zum|seit|zwischen|und)(?:\s+(?:dem|den))?)\s*:?\s*$"
)
_AS_OF_CONTEXT = re.compile(r"(?:stand|letzte\s+aktualisierung|aktualisiert\s+am)\s*:?\s*(?:vom\s+)?$")
_LONG_MONTH = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")
_TOP_LINES = 20


def _letter_date(text: DocumentText) -> date | None:
    latest_allowed = date.today() + timedelta(days=366)
    best: tuple[float, int, int, date] | None = None  # (score, -page, -pos, date)
    for page_index, page in enumerate(text.pages[:2]):
        lines = [ln for ln in page.text.splitlines() if ln.strip()]
        for line_index, line in enumerate(lines):
            for found, (start, end) in find_dates(line):
                if found.year < 1950 or found > latest_allowed:
                    continue
                before_raw = line[:start]
                before = fold(before_raw)[-40:]
                rest = line[end:].strip(" .,;")
                if _DATE_LABEL.search(before):
                    score = 6.0
                elif _CITY_PREFIX.match(before_raw):
                    score = 5.0
                elif not before.strip() and not rest:
                    score = 4.0
                elif _AS_OF_CONTEXT.search(before):
                    score = 0.5
                elif _EXCLUDED_CONTEXT.search(before):
                    continue
                else:
                    score = 2.0 if _LONG_MONTH.search(line[start:end]) else 1.0
                if line_index < _TOP_LINES:
                    score += 2.0
                if page_index > 0:
                    if score < 5.0:
                        continue  # Folgeseiten nur mit eindeutiger Kennzeichnung
                    score -= 3.0
                key = (score, -page_index, -line_index, found)
                if best is None or key[:3] > best[:3]:
                    best = key
    return best[3] if best else None


_EFFECTIVE_TRIGGER = re.compile(
    r"\b(?:g(?:ü|ue|u)ltig\s+(?:ab|vom|zum)|wirksam\s+(?:ab|zum|vom|mit)|mit\s+wirkung\s+(?:vom|zum|ab)|"
    r"(?:ä|ae|a)nderung\s+zum|(?:ä|ae|a)nderungsstichtag|stichtag\s+der\s+(?:ä|ae|a)nderung|ab\s+dem)\b",
    re.IGNORECASE,
)
_EFFECTIVE_WINDOW = 40
_EFFECTIVE_MAX_GAP = 12


def _effective_date(text: DocumentText) -> date | None:
    for page in text.pages:
        flat = normalize_ws(page.text)
        for m in _EFFECTIVE_TRIGGER.finditer(flat):
            window = flat[m.end() : m.end() + _EFFECTIVE_WINDOW]
            found = find_dates(window)
            if found and found[0][1][0] <= _EFFECTIVE_MAX_GAP:
                return found[0][0]
    return None


# ---------------------------------------------------------------------------
# Vertragsnummern
# ---------------------------------------------------------------------------

# Linke Wortgrenze: "Renten-/Kranken-/Sozialversicherungsnummer" sind KEINE Vertragsnummern.
_CONTRACT_LABEL = re.compile(
    r"(?<![A-Za-zÄÖÜäöüß])(?:Versicherungsschein\s*-?\s*(?:Nr\.?|Nummer)|Versicherungsscheinnummer|Versicherungs\s*-\s*(?:Nr\.?|Nummer)|"
    r"Versicherungsnummer|Versicherung\s+Nr\.?|Vertrags\s*-?\s*(?:Nr\.?|Nummer)|Vertragsnummer|Vertrag\s+Nr\.?|"
    r"Vers\.\s*-?\s*Nr\.?|Police(?:n)?\s*-?\s*(?:Nr\.?|Nummer)|Policennummer)",
    re.IGNORECASE,
)
_CONTRACT_SEPARATOR = re.compile(r"\s*(?:[:#]|\.(?!\d))?\s*")
_CONTRACT_VALUE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9./\-]|[ ](?=\d{2,6}(?![\d./])))*")
_MIN_CONTRACT_DIGITS = 3


def _contract_numbers(text: DocumentText) -> list[str]:
    found: list[str] = []
    for page in text.pages:
        flat = normalize_ws(page.text)
        for label in _CONTRACT_LABEL.finditer(flat):
            sep = _CONTRACT_SEPARATOR.match(flat, label.end())
            pos = sep.end() if sep else label.end()
            m = _CONTRACT_VALUE.match(flat, pos)
            if not m:
                continue
            value = m.group(0).strip().rstrip("./-").strip()
            digits = sum(c.isdigit() for c in value)
            if digits < _MIN_CONTRACT_DIGITS or not 4 <= len(value) <= 30:
                continue
            if value not in found:
                found.append(value)
    return found


# ---------------------------------------------------------------------------
# Versicherer
# ---------------------------------------------------------------------------

_INSURERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("Swiss Life", r"\bswiss\s*life\b"),
        ("HDI", r"\bhdi\b"),
        ("Continentale", r"\bcontinentale\b"),
        ("Allianz", r"\ballianz\b"),
        ("AXA", r"\baxa\b"),
        ("Generali", r"\bgenerali\b"),
        ("ERGO", r"\bergo\b"),
        ("R+V", r"(?:^|[^\w+])r\s*\+\s*v\b|\bruv\s+(?:lebens)?versicherung"),
        ("Debeka", r"\bdebeka\b"),
        ("Zurich", r"\bzurich\b"),
        ("Alte Leipziger", r"\balte\s+leipziger\b"),
        (
            "Stuttgarter",
            r"\bstuttgarter\s+(?:lebens)?versicherung|\bstuttgarter\s+leben\b|\bdie\s+stuttgarter\b",
        ),
        ("Canada Life", r"\bcanada\s+life\b"),
        ("Signal Iduna", r"\bsignal\s+iduna\b"),
        (
            "Nürnberger",
            r"\bnuernberger\s+(?:lebens|beamten\s*)?versicherung|\bnuernberger\s+(?:leben|beamten|allgemeine)\b",
        ),
        ("LV 1871", r"\blv\s*1871\b|\blebensversicherung\s+von\s+1871\b"),
        ("Barmenia", r"\bbarmenia\b"),
        ("Gothaer", r"\bgothaer\b"),
        ("VHV", r"\bvhv\b"),
        ("WWK", r"\bwwk\b"),
        ("Die Bayerische", r"\bdie\s+bayerische\b|\bbayerische\s+beamten"),
        ("Volkswohl Bund", r"\bvolkswohl\s*-?\s*bund\b"),
        ("Hannoversche", r"\bhannoversche\s+(?:lebens)?versicherung|\bhannoversche\s+leben\b"),
        ("Condor", r"\bcondor\s+(?:lebens)?versicherung|\bcondor\s+leben\b"),
        ("Dialog", r"\bdialog\s+(?:lebens)?versicherung"),
    )
)
_GENERIC_INSURER = re.compile(
    r"\b((?:[A-ZÄÖÜ][\wÄÖÜäöüß&.+\-]*[ \t]+){1,3}?)Lebensversicherung(?:s-AG|\s*(?:AG|SE|a\.\s?G\.|VVaG))(?!\w)"
)


def _insurer_hint(text: DocumentText) -> str | None:
    scores: dict[str, float] = {}
    for region in _regions(text):
        for name, pattern in _INSURERS:
            hits = len(pattern.findall(region.text))
            if hits:
                scores[name] = scores.get(name, 0.0) + region.factor * (1.0 + 0.25 * min(hits - 1, 4))
    if scores:
        return max(scores.items(), key=lambda kv: kv[1])[0]
    for page in text.pages:
        for line in page.text.splitlines():
            m = _GENERIC_INSURER.search(line)
            if m:
                return normalize_ws(m.group(0))
    return None


# ---------------------------------------------------------------------------
# Öffentliche Funktion
# ---------------------------------------------------------------------------


def classify_document(text: DocumentText) -> ClassificationResult:
    """Dokumenttyp, Datumsangaben, Vertragsnummern und Versicherer-Hinweis bestimmen."""
    scores, keywords = _score(text)
    ranking = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_type, best = ranking[0] if ranking else (DocumentType.UNKNOWN, 0.0)
    second_type, second = ranking[1] if len(ranking) > 1 else (DocumentType.UNKNOWN, 0.0)

    notes: list[str] = []
    if not text.full_text.strip():
        notes.append("Kein Text vorhanden – der Dokumenttyp kann nicht bestimmt werden.")

    if best < MIN_SCORE:
        doc_type = DocumentType.UNKNOWN
        confidence = 0.0
        uncertain = True
        matched: list[str] = []
        if text.full_text.strip():
            notes.append("Kein eindeutiger Dokumenttyp erkannt – bitte den Typ manuell wählen.")
    else:
        doc_type = best_type
        separation = (best - second) / best
        strength = 1.0 - math.exp(-best / STRONG_SCORE)
        confidence = round(max(0.0, min(1.0, strength * (0.5 + 0.5 * separation))), 2)
        uncertain = best < STRONG_SCORE or second > AMBIGUITY_RATIO * best
        matched = keywords.get(doc_type, [])
        if uncertain:
            notes.append("Dokumenttyp nicht sicher erkannt – bitte prüfen.")
            if second > 0:
                notes.append(f"Zweitbeste Einschätzung: {second_type.label_de}.")

    result = ClassificationResult(
        doc_type=doc_type,
        confidence=confidence,
        uncertain=uncertain,
        matched_keywords=matched,
        document_date=_letter_date(text),
        effective_date=_effective_date(text),
        contract_numbers=_contract_numbers(text),
        insurer_hint=_insurer_hint(text),
        notes=notes,
    )
    log.info(
        "Klassifikation: typ=%s konfidenz=%.2f unsicher=%s seiten=%d",
        result.doc_type.value,
        result.confidence,
        result.uncertain,
        len(text.pages),
    )
    return result
