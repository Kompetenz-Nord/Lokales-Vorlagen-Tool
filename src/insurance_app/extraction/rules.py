"""Feldregeln: Label-Synonyme, Abschnitte und Muster-Regeln.

Nur VERTRAGSDATEN (``FieldCategory.CONTRACT``) werden hier zugeordnet.
Auftragsdaten (``order.*``) dürfen nie aus Dokumenten stammen – das wird beim
Import dieses Moduls geprüft.

Neben echten Feldschlüsseln gibt es zusammengesetzte Schlüssel
(``COMPOSITE_KEYS``), deren Wert der Extraktor aufteilt, z. B. „Vertragsinhaber
Max Beispiel“ -> ``policyholder.first_name`` + ``policyholder.name``.
"""

from __future__ import annotations

import re

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory
from insurance_app.core.models import PageText, Word
from insurance_app.core.textutil import fold, normalize_ws

from .layout import LayoutMatch, Line, page_geometry, union_bbox
from .normalize import compact_key

__all__ = [
    "BANK_FIELDS",
    "COMPOSITE_KEYS",
    "FOOTER_FRACTION",
    "HEADER_FRACTION",
    "KNOWN_INSURERS",
    "LABELS",
    "RULE_CONFIDENCE",
    "SECTION_LABELS",
    "STOP_LABELS",
    "find_bav_keyword",
    "find_creditor_line_ys",
    "find_inline_contract_numbers",
    "find_insurer_names",
    "find_laufzeit_ranges",
    "find_regex_matches",
    "in_footer",
    "in_header",
]

HEADER_FRACTION = 0.15  # oberer Seitenbereich (Briefkopf)
FOOTER_FRACTION = 0.18  # unterer Seitenbereich (Fußzeile mit Bankdaten des Versicherers)

RULE_CONFIDENCE: dict[str, float] = {
    "inline_contract_number": 0.75,
    "laufzeit_range": 0.8,
    "bav_keyword": 0.5,
    "insurer_header": 0.6,
    "insurer_known_name": 0.45,
    "premium_frequency_from_label": 0.6,
}

# ---------------------------------------------------------------------------
# Zusammengesetzte Schlüssel
# ---------------------------------------------------------------------------

COMPOSITE_KEYS: dict[str, tuple[str, ...]] = {
    "policyholder": (
        "policyholder.name",
        "policyholder.first_name",
        "policyholder.salutation",
        "policyholder.legal_form",
    ),
    "insured": ("insured.last_name", "insured.first_name"),
    "policyholder.address": (
        "policyholder.street",
        "policyholder.house_number",
        "policyholder.postal_code",
        "policyholder.city",
    ),
    "policyholder.postal_city": ("policyholder.postal_code", "policyholder.city"),
    "insured.address": ("insured.street", "insured.house_number", "insured.postal_code", "insured.city"),
    "insured.postal_city": ("insured.postal_code", "insured.city"),
    "contract.term": ("contract.start_date", "contract.end_date"),
}

BANK_FIELDS = ("bank.account_holder", "bank.iban", "bank.institution")

# ---------------------------------------------------------------------------
# Globale Labels (gelten überall auf der Seite)
# ---------------------------------------------------------------------------

LABELS: dict[str, list[str]] = {
    "contract.number": [
        "Versicherung Nr.",
        "Versicherung Nummer",
        "Versicherungs-Nr.",
        "Versicherungsnummer",
        "Vertragsnummer",
        "Vertrags-Nr.",
        "Vertragsnr.",
        "Vertrag Nr.",
        "Vers.-Nr.",
        "Versicherungsschein-Nr.",
        "Versicherungsscheinnummer",
        "Policennummer",
        "Policen-Nr.",
        "Police Nr.",
    ],
    "contract.application_number": ["Antragsnummer", "Antragsnummer(n)", "Antrags-Nr.", "Antrag Nr."],
    "insurer.name": [
        "Unternehmen",
        "Versicherer",
        "Gesellschaft",
        "Versicherungsgesellschaft",
        "Versicherungsunternehmen",
    ],
    "contract.product": ["Produktbezeichnung", "Produkt", "Produktname", "Versicherungsart"],
    "contract.contract_type": ["Vertragstyp", "Vertragsart", "Durchführungsweg"],
    "contract.tariff": ["Tarif", "Tarifbezeichnung"],
    "contract.start_date": [
        "Versicherungsbeginn",
        "Vertragsbeginn",
        "Beginn der Versicherung",
        "Beginn des Vertrags",
    ],
    # Achtung: NIE "Rentenbeginn" (eigenes Feld contract.pension_start_date)
    "contract.end_date": [
        "Ablauf",
        "Ablauf der Versicherung",
        "Ablaufdatum",
        "Vertragsablauf",
        "Versicherungsablauf",
        "Versicherungsende",
        "Ende der Versicherung",
        "Vertragsende",
    ],
    "contract.end_age": ["Endalter", "Ablaufalter"],
    "contract.premium_end_date": [
        "Letzte Beitragsfälligkeit",
        "Beitragszahlung bis",
        "Beitragszahlungsende",
        "Ende der Beitragszahlung",
    ],
    "contract.pension_start_date": [
        "Geplanter Rentenbeginn",
        "Rentenbeginn",
        "Beginn der Rentenzahlung",
        "Rentenzahlungsbeginn",
    ],
    "contract.status": ["Status", "Vertragsstatus"],
    "contract.intermediary": ["Abschlussvermittler", "Vermittler", "Bestandsbetreuer"],
    "contract.changes": ["Art der Änderung", "Vertragsänderung"],
    "contract.term": ["Laufzeit", "Vertragslaufzeit"],
    "policyholder": [
        "Versicherungsnehmer",
        "Versicherungsnehmerin",
        "Vertragsinhaber",
        "Vertragsinhaberin",
    ],
    "insured": ["Versicherte Person", "Versicherter", "Versicherte"],
    "insured.birth_date": ["Geburtsdatum", "geb. am", "geboren am"],
    "insured.birth_place": ["Geburtsort"],
    "insured.nationality": ["Staatsangehörigkeit", "Nationalität"],
    "premium.amount": [
        "Beitrag",
        "Monatsbeitrag",
        "Jahresbeitrag",
        "Halbjahresbeitrag",
        "Vierteljahresbeitrag",
        "zu zahlender Beitrag",
        "Gesamtbeitrag",
        "neuer Beitrag",
        "Beitrag (monatlich)",
        "Beitrag (jährlich)",
        "monatlicher Beitrag",
        "jährlicher Beitrag",
        "Zahlbeitrag",
        "Beitragshöhe",
    ],
    "premium.frequency": ["Zahlweise", "Zahlungsweise", "Beitragszahlungsweise", "Zahlungsrhythmus"],
    "premium.start_date": ["Zahlungsbeginn", "Beitragsbeginn"],
    # Bankdaten nur in ausdrücklichem Kundenkontext (Abschnitte siehe unten)
    "bank.account_holder": ["Kontoinhaber", "Kontoinhaberin", "Name des Kontoinhabers"],
    "bank.iban": ["Ihre Bankverbindung", "Bankverbindung"],
    "employer.employment_start": [
        "Beginn der Betriebszugehörigkeit",
        "Eintrittsdatum",
        "Eintritt in das Unternehmen",
    ],
    "employer.employment_end": [
        "Ende der Betriebszugehörigkeit",
        "Austrittsdatum",
        "Austritt aus dem Unternehmen",
    ],
}

# ---------------------------------------------------------------------------
# Abschnitte (Überschrift -> Labels im Abschnitt)
# ---------------------------------------------------------------------------

_ADDRESS_LABELS = [
    "Anschrift",
    "Adresse",
    "Straße",
    "Straße, Hausnummer",
    "Straße und Hausnummer",
    "Straße / Hausnummer",
    "Straße, Hausnr.",
]
_POSTAL_CITY_LABELS = ["PLZ/Ort", "PLZ, Ort", "PLZ und Ort", "Postleitzahl, Ort", "PLZ / Wohnort"]


def _person_section(prefix: str, name_key: str, last_key: str) -> dict[str, list[str]]:
    return {
        name_key: ["Name", "Name, Vorname", "Vorname Name", "Vorname, Name"],
        f"{prefix}.first_name": ["Vorname"],
        last_key: ["Nachname", "Familienname"],
        f"{prefix}.address": list(_ADDRESS_LABELS),
        f"{prefix}.house_number": ["Hausnummer", "Hausnr."],
        f"{prefix}.postal_city": list(_POSTAL_CITY_LABELS),
        f"{prefix}.postal_code": ["PLZ", "Postleitzahl"],
        f"{prefix}.city": ["Ort", "Wohnort"],
        f"{prefix}.country": ["Land", "Staat"],
    }


_VN_SECTION: dict[str, list[str]] = {
    **_person_section("policyholder", "policyholder", "policyholder.name"),
    "policyholder.salutation": ["Anrede"],
    "policyholder.legal_form": ["Rechtsform"],
    "_ignore": [
        "Geburtsdatum",
        "Geburtsort",
        "Staatsangehörigkeit",
        "Nationalität",
        "Telefon",
        "E-Mail",
        "Beruf",
    ],
}
_VN_SECTION["policyholder"] = [*_VN_SECTION["policyholder"], "Name / Firma", "Firma", "Firmenname"]

_VP_SECTION: dict[str, list[str]] = {
    **_person_section("insured", "insured", "insured.last_name"),
    "insured.birth_date": ["Geburtsdatum", "geb. am", "geboren am"],
    "insured.birth_place": ["Geburtsort"],
    "insured.nationality": ["Staatsangehörigkeit", "Nationalität"],
    "_ignore": ["Anrede", "Telefon", "E-Mail", "Beruf"],
}

_BANK_SECTION: dict[str, list[str]] = {
    "bank.iban": ["IBAN"],
    "bank.account_holder": ["Kontoinhaber", "Kontoinhaberin", "Name des Kontoinhabers", "Name"],
    "bank.institution": ["Kreditinstitut", "Bank", "Geldinstitut", "Name der Bank", "Bankname"],
    "_ignore": ["BIC", "Anschrift", "Adresse", "Mandatsreferenz"],
}

_PAYER_SECTION: dict[str, list[str]] = {
    "bank.iban": ["IBAN"],
    "bank.account_holder": ["Kontoinhaber", "Kontoinhaberin", "Name des Kontoinhabers"],
    "bank.institution": ["Kreditinstitut", "Geldinstitut", "Name der Bank", "Bankname"],
    "_ignore": ["Name", "Anschrift", "Adresse", "BIC", "Geburtsdatum"],
}

_INTERMEDIARY_SECTION: dict[str, list[str]] = {
    "contract.intermediary": ["Name", "Firma"],
    "_ignore": ["Anschrift", "Adresse", "Telefon", "E-Mail", "Vermittlernummer"],
}

SECTION_LABELS: dict[str, dict[str, list[str]]] = {
    **dict.fromkeys(
        [
            "Versicherungsnehmer",
            "Versicherungsnehmerin",
            "Versicherungsnehmer (Arbeitgeber)",
            "Angaben zum Versicherungsnehmer",
            "Vertragsinhaber",
        ],
        _VN_SECTION,
    ),
    **dict.fromkeys(
        [
            "Versicherte Person",
            "Versicherte Person (VP)",
            "Angaben zur versicherten Person",
            "Versicherter",
        ],
        _VP_SECTION,
    ),
    **dict.fromkeys(
        [
            "Bankverbindung",
            "Ihre Bankverbindung",
            "Bankverbindung des Beitragszahlers",
            "SEPA-Lastschriftmandat",
            "Lastschriftmandat",
        ],
        _BANK_SECTION,
    ),
    "Beitragszahler": _PAYER_SECTION,
    **dict.fromkeys(["Vermittler", "Ihr Vermittler", "Ihr Betreuer"], _INTERMEDIARY_SECTION),
    # Neutrale Überschriften: beenden einen Personenabschnitt
    **dict.fromkeys(
        [
            "Vertragsdaten",
            "Vertragsangaben",
            "Angaben zum Vertrag",
            "Beitrag und Zahlweise",
            "Beitragsdaten",
            "Leistungen",
            "Versicherungsleistungen",
            "Bezugsrecht",
            "Hinweise",
        ],
        {},
    ),
}

# Bekannte Beschriftungen ohne Zielfeld: begrenzen Werte, werden aber nie zugeordnet
STOP_LABELS: list[str] = [
    "Rückkaufswert",
    "Rückkaufswert vom",
    "Letzte Aktualisierung",
    "Rente (monatlich)",
    "Garantierte Rente",
    "Garantiertes Kapital",
    "Spätester Rentenbeginn",
    "Frühester Rentenbeginn",
    "Vertragsguthaben",
    "Wert Ihrer Versicherung",
    "Überschussbeteiligung",
    "Todesfallleistung",
    "Sitz der Gesellschaft",
    "Vorstand",
    "Registergericht",
    "Handelsregister",
    "Amtsgericht",
    "Gläubiger-ID",
    "Gläubiger-Identifikationsnummer",
    "BIC",
    "USt-IdNr.",
    "Steuernummer",
    "Telefon",
    "Telefax",
    "E-Mail",
    "Internet",
    "Datum",
    "Seite",
    "Kundennummer",
    "Vermittlernummer",
    "Vermittler-Nr.",
    "Mandatsreferenz",
    "Ihr Zeichen",
    "Unser Zeichen",
    "Aufsichtsrat",
]

# ---------------------------------------------------------------------------
# Schutz: keine Auftragsdaten, nur bekannte Schlüssel
# ---------------------------------------------------------------------------


def _check_key(key: str) -> None:
    if key.startswith("_"):
        return
    members = COMPOSITE_KEYS.get(key, (key,))
    for member in members:
        fdef = FIELD_REGISTRY.get(member)
        if fdef is None:
            raise RuntimeError(f"Unbekannter Feldschlüssel in Extraktionsregeln: {member}")
        if fdef.category != FieldCategory.CONTRACT or not fdef.extractable:
            raise RuntimeError(f"Auftragsdaten dürfen nicht extrahiert werden: {member}")


for _key in LABELS:
    _check_key(_key)
for _mapping in SECTION_LABELS.values():
    for _key in _mapping:
        _check_key(_key)
for _composite, _members in COMPOSITE_KEYS.items():
    _check_key(_composite)
assert not any(k.startswith("order.") for k in LABELS), "Auftragsdaten in LABELS"

# ---------------------------------------------------------------------------
# Seitenbereiche
# ---------------------------------------------------------------------------


def in_footer(page: PageText, y0: float) -> bool:
    """Liegt eine Box (oberer Rand ``y0``) im Fußbereich der Seite?"""
    return y0 >= (1.0 - FOOTER_FRACTION) * page.height


def in_header(page: PageText, y1: float) -> bool:
    return y1 <= HEADER_FRACTION * page.height


def find_creditor_line_ys(lines: list[Line]) -> list[tuple[float, float]]:
    """Vertikale Bereiche von Zeilen mit „Gläubiger-ID“ (Briefkopf/Fußzeile des Versicherers)."""
    out: list[tuple[float, float]] = []
    for line in lines:
        f = fold(line.text)
        if "glaeubiger" in f or "creditor" in f:
            out.append((line.bbox.y0, line.bbox.y1))
    return out


# ---------------------------------------------------------------------------
# Muster-Regeln
# ---------------------------------------------------------------------------


def _hit(
    page: PageText,
    key: str,
    label_words: list[Word],
    value_words: list[Word],
    value_text: str,
    rule_id: str,
) -> LayoutMatch | None:
    label_box = union_bbox(label_words) or union_bbox(value_words)
    if label_box is None:
        return None
    return LayoutMatch(
        field_key=key,
        label_text=normalize_ws(" ".join(w.text for w in label_words)),
        value_text=normalize_ws(value_text),
        value_bbox=union_bbox(value_words),
        label_bbox=label_box,
        page=page.page_number,
        rule_id=rule_id,
        confidence=RULE_CONFIDENCE[rule_id],
        value_words=value_words,
    )


_INLINE_CN = re.compile(
    r"(?i)(?<![\w-])(?:Versicherungsschein|Versicherungs|Versicherung|Vertrags|Vertrag|Vers\.|Policen|Police)"
    r"\s*-?\s*(?:Nr\.?|Nummer)\s*[:.]?\s*(?P<v>[A-Z0-9][A-Z0-9./-]{2,29})"
)


def find_inline_contract_numbers(page: PageText, lines: list[Line]) -> list[LayoutMatch]:
    """„… Versicherung Nr. 9630030-1 …“ im Fließtext oder in Überschriften."""
    out: list[LayoutMatch] = []
    for line in lines:
        for m in _INLINE_CN.finditer(line.text):
            value = m.group("v").rstrip(".,;:/-")
            if sum(c.isdigit() for c in value) < 3:
                continue
            start, end = m.span("v")
            end = start + len(value)
            value_words = line.words_in_span(start, end)
            label_words = line.words_in_span(m.start(), start - 1) if start > m.start() else []
            hit = _hit(page, "contract.number", label_words, value_words, value, "inline_contract_number")
            if hit:
                out.append(hit)
    return out


_DATE = r"\d{1,2}\s?\.\s?\d{1,2}\s?\.\s?\d{2,4}"
_LAUFZEIT = re.compile(
    rf"(?i)\b(?:Vertrags)?laufzeit\b\s*:?\s*(?:vom\s+)?(?P<v>(?P<a>{_DATE})\s*(?:bis|-|–)\s*(?:zum\s+)?(?P<b>{_DATE}))"
)


def find_laufzeit_ranges(page: PageText, lines: list[Line]) -> list[LayoutMatch]:
    """„Laufzeit 01.02.2022 bis 01.08.2065“ -> zusammengesetzter Schlüssel ``contract.term``."""
    out: list[LayoutMatch] = []
    for line in lines:
        for m in _LAUFZEIT.finditer(line.text):
            start, end = m.span("v")
            value_words = line.words_in_span(start, end)
            label_words = line.words_in_span(m.start(), start - 1)
            hit = _hit(page, "contract.term", label_words, value_words, m.group("v"), "laufzeit_range")
            if hit:
                out.append(hit)
    return out


# "§" wird von OCR oft als "8" oder "$" gelesen -> nur "3 Nr. 63 EStG" verlangen
_ESTG_63 = re.compile(r"(?<!\d)3\s*(?:abs\.?\s*\d\s*)?nr\.?\s*63\s*estg")


def find_bav_keyword(page: PageText, lines: list[Line]) -> list[LayoutMatch]:
    """„Direktversicherung“ in der Nähe von „§ 3 Nr. 63 EStG“ -> Vertragstyp (niedrige Konfidenz)."""
    estg_idx = [i for i, ln in enumerate(lines) if _ESTG_63.search(fold(ln.text))]
    if not estg_idx:
        return []
    for i, line in enumerate(lines):
        if not any(abs(i - j) <= 3 for j in estg_idx):
            continue
        for w in line.words:
            if compact_key(w.text).startswith("direktversicherung"):
                hit = _hit(page, "contract.contract_type", [w], [w], "Direktversicherung", "bav_keyword")
                return [hit] if hit else []
    return []


_LEGAL_FORMS = {"AG", "SE", "a.G.", "VVaG", "AöR", "KGaA"}
_INSURER_WORD = re.compile(
    r"^(?:lebens|renten|kranken)?versicherung(?:en)?$|^(?:lebens)?versicherungs?ag$|^pensionskasse$|^pensionsfonds$"
)
_NAME_STOPWORDS = {"Ihre", "Ihr", "Sehr", "Unsere", "Bei", "An"}

# (gefaltetes Muster, Anzeige-Name) – nur im Kopf-/Fußbereich ausgewertet
KNOWN_INSURERS: list[tuple[str, str]] = [
    ("swiss life", "Swiss Life"),
    ("allianz", "Allianz"),
    ("axa", "AXA"),
    ("generali", "Generali"),
    ("ergo", "ERGO"),
    ("r+v", "R+V"),
    ("hdi", "HDI"),
    ("continentale", "Continentale"),
    ("alte leipziger", "Alte Leipziger"),
    ("stuttgarter", "Stuttgarter"),
    ("zurich", "Zurich"),
    ("debeka", "Debeka"),
    ("signal iduna", "SIGNAL IDUNA"),
    ("nuernberger", "Nürnberger"),
    ("volkswohl bund", "VOLKSWOHL BUND"),
    ("canada life", "Canada Life"),
    ("hannoversche", "Hannoversche"),
    ("wwk", "WWK"),
    ("gothaer", "Gothaer"),
    ("barmenia", "Barmenia"),
    ("lv 1871", "LV 1871"),
    ("die bayerische", "die Bayerische"),
    ("condor", "Condor"),
    ("helvetia", "Helvetia"),
    ("basler", "Basler"),
    ("provinzial", "Provinzial"),
    ("wuerttembergische", "Württembergische"),
    ("heidelberger leben", "Heidelberger Leben"),
    ("standard life", "Standard Life"),
    ("tecis", "tecis"),
]
_KNOWN_RE = [(re.compile(rf"(?<![\w+]){re.escape(p)}(?![\w+])"), name) for p, name in KNOWN_INSURERS]


def _in_letterhead(page: PageText, line: Line) -> bool:
    return in_header(page, line.bbox.y1) or in_footer(page, line.bbox.y0)


def find_insurer_names(page: PageText, lines: list[Line]) -> list[LayoutMatch]:
    """Versicherer aus dem Briefkopf/der Fußzeile: „<Name> Lebensversicherung AG|SE|a.G.“."""
    geo = page_geometry(page.words, page.width)
    out: list[LayoutMatch] = []
    for line in lines:
        if not _in_letterhead(page, line):
            continue
        words = line.words
        for idx, w in enumerate(words[:-1]):
            if not _INSURER_WORD.match(compact_key(w.text)):
                continue
            legal = words[idx + 1].text.strip(",;·|()")
            if legal not in _LEGAL_FORMS:
                continue
            if words[idx + 1].bbox.x0 - w.bbox.x1 > geo.seg_gap:
                continue
            start = idx
            while start > 0 and idx - start < 3:
                prev, cur = words[start - 1], words[start]
                text = prev.text
                if cur.bbox.x0 - prev.bbox.x1 > geo.seg_gap:
                    break
                if not text[:1].isupper() and not text[:1].isdigit():
                    break
                if text.endswith((",", ":", ";")) or text in _NAME_STOPWORDS or not compact_key(text):
                    break
                start -= 1
            if start == idx and compact_key(w.text).startswith("versicherung"):
                continue  # "Versicherung AG" ohne Namen ist zu allgemein
            name_words = words[start : idx + 2]
            name = " ".join(x.text for x in name_words[:-1]) + " " + legal
            hit = _hit(page, "insurer.name", name_words, name_words, name, "insurer_header")
            if hit:
                out.append(hit)
    if out:
        return out
    for line in lines:
        if not _in_letterhead(page, line):
            continue
        f = fold(line.text)
        for pattern, name in _KNOWN_RE:
            m = pattern.search(f)
            if m:
                hit = _hit(page, "insurer.name", line.words, line.words, name, "insurer_known_name")
                if hit:
                    out.append(hit)
                break
    return out


def find_regex_matches(page: PageText, lines: list[Line]) -> list[LayoutMatch]:
    """Alle Muster-Regeln einer Seite."""
    return [
        *find_inline_contract_numbers(page, lines),
        *find_laufzeit_ranges(page, lines),
        *find_bav_keyword(page, lines),
        *find_insurer_names(page, lines),
    ]
