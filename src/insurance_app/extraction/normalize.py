"""Normalisierung gelesener Rohwerte in kanonische Zeichenketten.

Kanonische Formen (siehe ``core.models``):
* Datum ``YYYY-MM-DD``, Monat ``YYYY-MM``
* Betrag ``1234.56`` (Währung getrennt)
* IBAN kompakt in Großbuchstaben
* Bool ``true``/``false``, Auswahl = interner Schlüssel

Alle Funktionen sind rein (keine Seiteneffekte, kein Logging von Inhalten) und
geben bei nicht eindeutig lesbaren Werten ``None`` zurück, statt zu raten.

Alle Texte werden in Unicode-Normalform NFC gebracht (z. B. ein aus macOS
eingefügtes „u + ¨“ wird zu „ü“), damit Vergleich und PDF-Ausgabe übereinstimmen.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

from insurance_app.core.fields import FREQUENCY_CHOICES, FieldDef, FieldType
from insurance_app.core.textutil import fold, normalize_ws, parse_german_date, parse_month

__all__ = [
    "COMPANY_SUFFIXES",
    "NameSplit",
    "compact_key",
    "frequency_from_text",
    "is_placeholder",
    "is_reference",
    "normalize_amount",
    "normalize_bool",
    "normalize_contract_number",
    "normalize_country",
    "normalize_currency",
    "normalize_date",
    "normalize_frequency",
    "normalize_iban",
    "normalize_integer",
    "normalize_month",
    "normalize_postal_code",
    "normalize_value",
    "parse_amount",
    "to_nfc",
    "split_person_name",
    "split_postal_city",
    "split_street_house",
]

NOTE_UNREADABLE = "Wert nicht eindeutig lesbar"
NOTE_NAME_TWO_TOKENS = "Vor- und Nachname wurden automatisch getrennt – bitte prüfen."
NOTE_NAME_MANY_TOKENS = (
    "Mehrteiliger Name: Der letzte Namensteil wurde als Nachname angenommen – bitte prüfen."
)
NOTE_NAME_SINGLE = "Nur ein Namensbestandteil gefunden – bitte Vor- und Nachname prüfen."
NOTE_NAME_UNCLEAR = "Der Name konnte nicht sicher in Vor- und Nachname aufgeteilt werden – bitte prüfen."
NOTE_MONTH_DAY = "Bitte nur Monat und Jahr angeben (MM.JJJJ) – das Formular gilt immer zum 01."
NOTE_AMOUNT_STRICT = (
    "Bitte einen Betrag wie 1.234,56 eingeben – höchstens zwei Nachkommastellen und ohne weiteren Text."
)
NOTE_TWO_DIGIT_YEAR = "Das Jahr war nur zweistellig angegeben – bitte das Jahrhundert prüfen."

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def compact_key(text: str) -> str:
    """Vergleichsschlüssel: gefaltet (``fold``), nur Buchstaben a–z und Ziffern."""
    return _NON_ALNUM.sub("", fold(text))


def to_nfc(text: str) -> str:
    """Unicode-Normalform NFC (zerlegte Umlaute „u + ¨“ -> „ü“)."""
    return unicodedata.normalize("NFC", text)


# ---------------------------------------------------------------------------
# Platzhalter
# ---------------------------------------------------------------------------

_PLACEHOLDER_KEYS = {
    "",
    "unbekannt",
    "-",
    "--",
    "---",
    "–",
    "—",
    "/",
    "ka",
    "keineangabe",
    "keineangaben",
    "n/a",
    "na",
    "nichtvorhanden",
    "nichtbekannt",
    "nichtangegeben",
    "entfaellt",
    "nv",
    "nn",
    "-/-",
    "keine",
    "keiner",
    "keines",
    "ohne",
    "?",
    "??",
    "???",
    "xxx",
}


def is_placeholder(raw: str | None) -> bool:
    """``True`` für Platzhalter wie „Unbekannt“, „-“, „k.A.“, „n.v.“, „keine Angabe“ oder leeren Text."""
    if raw is None:
        return True
    key = re.sub(r"[\s.:()]", "", fold(raw))
    return key in _PLACEHOLDER_KEYS


# Verweise statt Werten: „wie Versicherungsnehmer“, „siehe oben“, „identisch mit VN“, „= VN“
_REFERENCE_START = re.compile(r"^(?:(?:wie|siehe|vgl|identisch|gleich|entspricht)\b|s\s?\.\s?o\b|=)")
_REFERENCE_TARGETS = {
    "vn",
    "versicherungsnehmer",
    "versicherungsnehmerin",
    "beitragszahler",
    "beitragszahlerin",
    "kontoinhaber",
    "kontoinhaberin",
    "oben",
    "vorstehend",
}
_REFERENCE_FILLERS = {"der", "die", "dem", "den", "des", "wie", "siehe", "s", "o"}


def is_reference(raw: str | None) -> bool:
    """``True``, wenn der Text nur auf eine andere Angabe verweist (z. B. „wie Versicherungsnehmer“).

    Solche Texte sind kein Wert (kein Name, keine Anschrift) und dürfen nicht übernommen werden.
    """
    if not raw:
        return False
    f = fold(raw).strip(" .,;:")
    if not f:
        return False
    if _REFERENCE_START.search(f):
        return True
    tokens = [t.strip(".") for t in re.split(r"[\s,;:()/]+", f) if t.strip(".")]
    return (
        0 < len(tokens) <= 3
        and tokens[-1] in _REFERENCE_TARGETS
        and all(t in _REFERENCE_FILLERS or t in _REFERENCE_TARGETS for t in tokens)
    )


# ---------------------------------------------------------------------------
# Datum / Monat
# ---------------------------------------------------------------------------


#: Zukunftsfenster für zweistellige Jahre außerhalb von Geburtsdaten (``01.08.72`` -> 2072)
TWO_DIGIT_YEAR_FUTURE_WINDOW = 50

_TWO_DIGIT_YEAR = re.compile(r"(?<!\d)\d{1,2}\s?[./]\s?\d{1,2}\s?[./]\s?\d{2}(?!\d)")


def normalize_date(
    raw: str | None,
    *,
    past_only: bool = False,
    max_future_years: int | None = None,
    today: date | None = None,
) -> str | None:
    """Deutsches Datum (``01.02.2022``, ``13. März 2025`` …) -> ``YYYY-MM-DD``.

    ``past_only``/``max_future_years`` steuern zweistellige Jahre (siehe
    ``core.textutil.parse_german_date``); ohne Angabe gilt die feste Grenze 70.
    """
    if not raw or is_placeholder(raw):
        return None
    d = parse_german_date(raw, past_only=past_only, max_future_years=max_future_years, today=today)
    return d.isoformat() if d else None


def _has_two_digit_year(raw: str) -> bool:
    return bool(_TWO_DIGIT_YEAR.search(raw))


_ISO_MONTH = re.compile(r"(?<!\d)(\d{4})-(\d{2})(?![\d-])")


def _month_with_note(raw: str | None) -> tuple[str | None, str | None]:
    """Monat lesen -> (``YYYY-MM`` oder ``None``, Hinweis bei abgelehntem Tagesdatum)."""
    if not raw or is_placeholder(raw):
        return None, None
    m = _ISO_MONTH.search(raw)
    if m and 1 <= int(m.group(2)) <= 12 and 1900 <= int(m.group(1)) <= 2200:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}", None
    d = parse_german_date(raw)
    if d:
        # Das Formular druckt immer „zum 01.“: ein anderer Tag würde stillschweigend verschoben.
        if d.day != 1:
            return None, NOTE_MONTH_DAY
        return f"{d.year:04d}-{d.month:02d}", None
    ym = parse_month(raw)
    if ym:
        return f"{ym[0]:04d}-{ym[1]:02d}", None
    return None, None


def normalize_month(raw: str | None) -> str | None:
    """Monat/Jahr (``03.2025``, ``März 2025``, ``2025-03``) -> ``YYYY-MM``.

    Ein volles Datum wird nur mit Tag 01 angenommen (``01.03.2025`` -> ``2025-03``).
    Ein anderer Tag (``15.03.2025``) ergibt ``None``: Die Formulare gelten immer
    „zum 01.“ des Monats, der Tag würde sonst stillschweigend verschoben.
    """
    return _month_with_note(raw)[0]


# ---------------------------------------------------------------------------
# Beträge
# ---------------------------------------------------------------------------

_CURRENCY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"€|\bEUR\b|\bEuro\b", re.IGNORECASE), "EUR"),
    (re.compile(r"\bCHF\b|\bFr\.", re.IGNORECASE), "CHF"),
    (re.compile(r"\$|\bUSD\b", re.IGNORECASE), "USD"),
    (re.compile(r"£|\bGBP\b", re.IGNORECASE), "GBP"),
]

# Zahl-Token: Ziffern mit . , ' oder schmalen Leerzeichen als Trenner; optional ",-" / ",--"
_NUMBER_TOKEN = re.compile(
    r"(?<![\d.,])([-\u2212]?)(\d(?:[\d.,'\u00a0\u202f]*\d)?)(,\s?[-\u2013]{1,2})?(?!\d)"
)
# "1 234,56" (Leerzeichen als Tausendertrenner) -> "1234,56"
_SPACE_GROUP = re.compile(r"(?<![\d.,])(\d{1,3})((?:[ \u00a0\u202f]\d{3})+)(?=[,.]\d|\s|$|[^\d])")


def _grouped_ok(int_part: str, sep: str) -> bool:
    groups = int_part.split(sep)
    if not groups[0] or len(groups[0]) > 3 or not groups[0].isdigit():
        return False
    return all(len(g) == 3 and g.isdigit() for g in groups[1:])


def _interpret_number(token: str) -> Decimal | None:
    t = token.replace("'", "").replace(" ", "").replace(" ", "")
    if not t or not t[0].isdigit():
        return None
    try:
        if "," in t and "." in t:
            dec, thou = (",", ".") if t.rfind(",") > t.rfind(".") else (".", ",")
            int_part, frac = t.rsplit(dec, 1)
            if not frac.isdigit() or not 1 <= len(frac) <= 4 or dec in int_part:
                return None
            if not _grouped_ok(int_part, thou):
                return None
            return Decimal(int_part.replace(thou, "") + "." + frac)
        if "," in t:
            parts = t.split(",")
            if len(parts) == 2:
                # "1,234" ist mehrdeutig (Tausenderkomma oder drei Nachkommastellen) -> nicht raten
                if parts[0].isdigit() and parts[1].isdigit() and 1 <= len(parts[1]) <= 2:
                    return Decimal(parts[0] + "." + parts[1])
                return None
            if _grouped_ok(t, ","):
                return Decimal(t.replace(",", ""))
            return None
        if "." in t:
            parts = t.split(".")
            if len(parts) == 2:
                if not (parts[0].isdigit() and parts[1].isdigit()):
                    return None
                if len(parts[1]) == 3 and len(parts[0]) <= 3:
                    return Decimal(parts[0] + parts[1])  # 1.234 -> 1234 (Tausenderpunkt)
                if 1 <= len(parts[1]) <= 2:
                    return Decimal(parts[0] + "." + parts[1])  # 1234.56
                return None
            if _grouped_ok(t, "."):
                return Decimal(t.replace(".", ""))
            return None  # z. B. Datum 01.07.2024
        return Decimal(t) if t.isdigit() else None
    except InvalidOperation:
        return None


def _detect_currency(raw: str) -> str | None:
    for pattern, code in _CURRENCY_PATTERNS:
        if pattern.search(raw):
            return code
    return None


_MONTH_WORD = r"(?:jan(?:uar)?|j(?:ae|ä)nner|feb(?:ruar)?|m(?:ae|ä)rz|m(?:ae|ä)r|mrz|apr(?:il)?|mai|jun[i]?|jul[i]?|aug(?:ust)?|sep(?:t(?:ember)?)?|okt(?:ober)?|nov(?:ember)?|dez(?:ember)?)"
# Zahl ist Tag eines ausgeschriebenen Datums ("1. Juli") oder Teil von "07/2025" bzw. "01.07."
_DAY_BEFORE_MONTH = re.compile(rf"^\.?\s*{_MONTH_WORD}\b", re.IGNORECASE)
_SLASH_AFTER = re.compile(r"^\s?/\s?\d")
_SLASH_BEFORE = re.compile(r"\d\s?/\s?$")
_DATE_DOT_AFTER = re.compile(r"^\.(?!\d)")
_PERCENT_AFTER = re.compile(r"^\s?%")
# Jahreszahl nach "ab", "seit", "bis", "im", "Jahr" oder einem Monatsnamen ("ab Juli 2025")
_YEAR_CONTEXT_BEFORE = re.compile(
    rf"\b(?:ab|seit|bis|im|jahr|jahres|stand|vom|zum|{_MONTH_WORD})\.?\s*$", re.I
)
_CURRENCY_NEAR = re.compile(r"€|\beur\b|\beuro\b", re.IGNORECASE)
# Texterkennung liest 0 als O und 1 als l/I: „2O0,00“, „1.5OO,00“ -> nicht raten
_OCR_DIGIT_CONFUSION = re.compile(r"(?<=[\d.,])[OoIl]|[OoIl](?=[.,]?\d)")
_FRACTION = re.compile(r"(?:,\d{1,2}|\.\d{2})$")


def _amount_tokens(text: str) -> list[tuple[Decimal, bool]]:
    """Alle als Geldbetrag deutbaren Zahlen -> (Wert, bevorzugt).

    Bevorzugt sind Zahlen mit Nachkommastellen (``,00``/``,-``) oder direkt
    angrenzender Währung. Datums-, Monats-/Jahres- und Prozentangaben entfallen.
    """
    out: list[tuple[Decimal, bool]] = []
    for m in _NUMBER_TOKEN.finditer(text):
        token = m.group(2)
        value = _interpret_number(token)
        if value is None:
            continue
        before, after = text[: m.start()], text[m.end() :]
        if _SLASH_AFTER.match(after) or _SLASH_BEFORE.search(before) or _PERCENT_AFTER.match(after):
            continue
        has_fraction = bool(m.group(3)) or bool(_FRACTION.search(token))
        if not has_fraction and _DAY_BEFORE_MONTH.match(after):
            continue  # "1. Juli 2025"
        if re.fullmatch(r"\d{1,2}\.\d{1,2}", token) and _DATE_DOT_AFTER.match(after):
            continue  # "01.07." (Datum ohne Jahr)
        is_year = not has_fraction and re.fullmatch(r"(?:19|20|21)\d{2}", token) is not None
        if is_year and _YEAR_CONTEXT_BEFORE.search(before):
            continue
        if m.group(3) and value == value.to_integral_value():
            value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if m.group(1):
            value = -value
        currency_near = bool(_CURRENCY_NEAR.search(before[-5:])) or bool(_CURRENCY_NEAR.search(after[:6]))
        out.append((value, has_fraction or currency_near))
    return out


def parse_amount(raw: str | None) -> tuple[Decimal, str | None] | None:
    """Geldbetrag lesen: ``1.234,56 EUR``, ``1234,56 €``, ``EUR 1.234,56``, ``150,-``.

    Enthält der Text weitere Zahlen (``ab 07/2025: 125,00 EUR``, ``Monatsbeitrag
    2025: 87,50``), zählen Datums-, Monats- und Jahresangaben nicht. Bleiben
    mehrere unterschiedliche Beträge übrig, ist der Wert mehrdeutig -> ``None``.

    Rückgabe: (Betrag, Währungscode oder ``None``) bzw. ``None``, wenn kein Betrag erkennbar ist.
    """
    if not raw or is_placeholder(raw):
        return None
    text = normalize_ws(raw)
    if _OCR_DIGIT_CONFUSION.search(text):
        return None
    text = _SPACE_GROUP.sub(lambda m: m.group(1) + re.sub(r"\s", "", m.group(2)), text)
    tokens = _amount_tokens(text)
    if not tokens:
        return None
    preferred = [v for v, pref in tokens if pref]
    values = preferred or [v for v, _ in tokens]
    if len(set(values)) != 1:
        return None
    return values[0], _detect_currency(text)


def normalize_amount(raw: str | None) -> tuple[str | None, str | None]:
    """Betrag -> (``"1234.56"``, Währung). Nicht lesbar -> (``None``, ``None``).

    Gerundet wird kaufmännisch (``ROUND_HALF_UP``), wie Acrobat und die PDF-Ausgabe.
    """
    parsed = parse_amount(raw)
    if parsed is None:
        return None, None
    value, currency = parsed
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}", currency


# Strenge Eingabe (manuell): genau ein Betrag, optional mit Euro-Zeichen, sonst nichts
_STRICT_AMOUNT = re.compile(
    r"(?:€|EUR|Euro)?\s*[-\u2212]?\s*\d[\d.,'\u00a0\u202f ]*(?:,\s?[-\u2013]{1,2})?\s*(?:€|EUR|Euro)?",
    re.IGNORECASE,
)


def _normalize_amount_strict(text: str) -> tuple[str | None, str | None]:
    """Manuelle Betragseingabe -> (``"1234.56"``, Währung) oder ``None``.

    Nur ein Betrag ohne weiteren Text, höchstens zwei Nachkommastellen (es wird nie gerundet).
    """
    if not _STRICT_AMOUNT.fullmatch(text.strip()):
        return None, None
    parsed = parse_amount(text)
    if parsed is None:
        return None, None
    value, currency = parsed
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        return None, None
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}", currency


def normalize_currency(raw: str | None) -> str | None:
    """Währung -> ISO-Code (``€``/``Euro`` -> ``EUR``)."""
    if not raw or is_placeholder(raw):
        return None
    code = _detect_currency(raw)
    if code:
        return code
    t = raw.strip().upper()
    return t if re.fullmatch(r"[A-Z]{3}", t) else None


# ---------------------------------------------------------------------------
# IBAN / PLZ / Land
# ---------------------------------------------------------------------------

_IBAN_IN_TEXT = re.compile(r"(?<![A-Z0-9])([A-Z]{2}\s?\d{2}(?:\s?[A-Z0-9]){10,30})(?![A-Z0-9])")


def normalize_iban(raw: str | None) -> str | None:
    """IBAN aus Text lesen -> kompakte Großschreibung (ohne Prüfung der Prüfziffer).

    Prüfziffer und Länge prüft ``validation.validators.iban_is_valid``.
    """
    if not raw or is_placeholder(raw):
        return None
    text = raw.upper().replace(" ", " ").replace(" ", " ")
    text = re.sub(r"\bIBAN\b\s*[:.]?", " ", text)
    m = _IBAN_IN_TEXT.search(text)
    if not m:
        return None
    iban = re.sub(r"\s", "", m.group(1))
    # Abschneiden bei bekannter Länge (z. B. wenn danach "BIC" ohne Leerzeichen folgt)
    from insurance_app.validation.validators import IBAN_LENGTHS  # lokale Abhängigkeit, keine Zyklen

    expected = IBAN_LENGTHS.get(iban[:2])
    if expected is not None:
        if len(iban) < expected:
            return None
        iban = iban[:expected]
    if not 15 <= len(iban) <= 34:
        return None
    return iban


_PLZ = re.compile(r"(?<![\d])(?:(?:D|DE|A|AT|CH)\s?-\s?)?(\d{4,5})(?!\d)")


def normalize_postal_code(raw: str | None) -> str | None:
    """Postleitzahl (``12345``, ``D-12345``; 4-stellig für AT/CH) -> nur Ziffern."""
    if not raw or is_placeholder(raw):
        return None
    m = _PLZ.search(raw.strip())
    return m.group(1) if m else None


_COUNTRIES = {
    "deutschland": "DE",
    "bundesrepublikdeutschland": "DE",
    "germany": "DE",
    "de": "DE",
    "d": "DE",
    "oesterreich": "AT",
    "austria": "AT",
    "at": "AT",
    "schweiz": "CH",
    "switzerland": "CH",
    "ch": "CH",
    "niederlande": "NL",
    "nl": "NL",
    "frankreich": "FR",
    "fr": "FR",
    "italien": "IT",
    "it": "IT",
    "spanien": "ES",
    "es": "ES",
    "belgien": "BE",
    "be": "BE",
    "luxemburg": "LU",
    "lu": "LU",
    "polen": "PL",
    "pl": "PL",
}


def normalize_country(raw: str | None) -> str | None:
    """Ländername oder -kürzel -> ISO-3166-Alpha-2 (unbekannte Länder bleiben als Text)."""
    if not raw or is_placeholder(raw):
        return None
    key = compact_key(raw)
    if key in _COUNTRIES:
        return _COUNTRIES[key]
    return normalize_ws(raw)


# ---------------------------------------------------------------------------
# Anschrift
# ---------------------------------------------------------------------------

_HOUSE_TAIL = re.compile(
    r"^(?P<street>.*?[^\d\s])\s*(?P<house>\d{1,5}\s?[a-zA-Z]?(?:\s?[-–/]\s?\d{1,5}\s?[a-zA-Z]?)?)\s*$"
)


def split_street_house(raw: str) -> tuple[str, str | None]:
    """„Große Straße 12a“ -> („Große Straße“, „12a“); ohne Hausnummer -> (Text, ``None``)."""
    text = normalize_ws(raw).rstrip(",;")
    m = _HOUSE_TAIL.match(text)
    if not m:
        return text, None
    street = m.group("street").strip().rstrip(",")
    house = re.sub(r"\s*([-–/])\s*", r"\1", m.group("house").strip()).replace("–", "-")
    if not street or not re.search(r"[A-Za-zÄÖÜäöüß]", street):
        return text, None
    return street, house


_POSTAL_CITY = re.compile(r"^(?:(?:D|DE|A|AT|CH)\s?-\s?)?(\d{4,5})(?:\s+(.+))?$")


def split_postal_city(raw: str) -> tuple[str | None, str | None]:
    """„12345 Musterstadt“ -> („12345“, „Musterstadt“); nur Ort -> (``None``, Ort)."""
    text = normalize_ws(raw).strip(" ,;")
    if not text:
        return None, None
    m = _POSTAL_CITY.match(text)
    if m:
        city = m.group(2).strip(" ,") if m.group(2) else None
        return m.group(1), city or None
    if re.search(r"\d", text):
        return None, None
    return None, text


# ---------------------------------------------------------------------------
# Namen
# ---------------------------------------------------------------------------

COMPANY_SUFFIXES: tuple[str, ...] = (
    "GmbH",
    "AG",
    "KG",
    "SE",
    "mbH",
    "e.K.",
    "OHG",
    "GbR",
    "UG",
    "e.V.",
    "KGaA",
    "eG",
    "PartG",
    "mbB",
    "Ltd.",
    "Inc.",
)
_EXACT_SUFFIXES = set(COMPANY_SUFFIXES) | {"Ltd", "Inc", "gGmbH", "VVaG", "a.G.", "AöR", "e.Kfm.", "eK"}
# Längere Kürzel auch bei abweichender Groß-/Kleinschreibung (Texterkennung)
_CI_SUFFIXES = {"gmbh", "ggmbh", "mbh", "kgaa", "ohg", "gbr", "partg", "vvag"}

_SALUTATIONS = {"herr": "Herr", "herrn": "Herr", "frau": "Frau"}
_TITLES = {"dr", "prof", "dipl", "ing", "med", "rer", "nat", "jur", "phil", "dres"}
_NAME_PARTICLES = {"von", "van", "de", "der", "den", "zu", "zum", "zur", "vom", "di", "da", "del", "le", "la"}


class NameSplit(BaseModel):
    """Ergebnis der Namensaufteilung (heuristisch, Konfidenz NICHT kalibriert)."""

    model_config = ConfigDict(extra="forbid")

    last: str
    first: str | None = None
    salutation: str | None = None
    is_company: bool = False
    confidence: float = 0.0
    note: str | None = None
    legal_form: str | None = None
    birth_date: str | None = None  # abgetrenntes „geb. 12.03.1980“ (``YYYY-MM-DD``)


def _company_suffix_index(tokens: list[str]) -> int | None:
    for idx, tok in enumerate(tokens):
        if idx == 0:
            continue  # Ein Firmenname beginnt nicht mit der Rechtsform
        stripped = tok.strip(",;()")
        if stripped in _EXACT_SUFFIXES or stripped.lower() in _CI_SUFFIXES:
            return idx
    return None


# „…, geb. 12.03.1980“, „… geboren am 12.03.1980“, „… (* 12.03.1980)“
_BIRTH_TAIL = re.compile(
    r"\s*[,;]?\s*\(?\s*(?:geb(?:oren)?\.?|\*)\s*(?:am\s+)?(?=\d)(?P<date>[^)]*)\)?\s*$", re.IGNORECASE
)
# „… geb. Müller“ (Geburtsname) gehört nicht zum aktuellen Namen
_MAIDEN_TAIL = re.compile(r"\s*,?\s*\(?\s*geb(?:oren|\.)\s+(?!am\b)[^\d,]+\)?\s*$", re.IGNORECASE)
_STREET_WORD = re.compile(r"(?:str(?:asse|\.)?|weg|allee|gasse|damm|chaussee)$")


def _strip_name_tail(text: str) -> tuple[str, str | None]:
    """Geburtsdatum, Geburtsname und Anschrift hinter einem Namen abschneiden.

    Rückgabe: (Name ohne Anhang, Geburtsdatum ``YYYY-MM-DD`` oder ``None``).
    """
    birth: str | None = None
    m = _BIRTH_TAIL.search(text)
    if m:
        d = parse_german_date(m.group("date"), past_only=True)
        birth = d.isoformat() if d else None
        text = text[: m.start()]
    m = _MAIDEN_TAIL.search(text)
    if m and m.start() > 0:
        text = text[: m.start()]
    parts = text.split(",")
    for idx in range(1, len(parts)):
        part = parts[idx].strip()
        last_word = re.sub(r"[^a-z.]", "", fold(part.split(" ")[-1])) if part else ""
        if re.search(r"\d", part) or (last_word and _STREET_WORD.search(last_word)):
            text = ",".join(parts[:idx])  # ab hier Anschrift („Musterweg 5, 12345 Musterstadt“)
            break
    return text.strip(" ,;"), birth


def _name_like(part: str, max_tokens: int = 4) -> bool:
    """Nur Buchstaben, Bindestrich, Apostroph, Punkt (Initialen); höchstens ``max_tokens`` Wörter."""
    tokens = part.split()
    if not 1 <= len(tokens) <= max_tokens:
        return False
    return all(re.sub(r"[-'’.]", "", tok).isalpha() for tok in tokens)


def _company_split(tokens: list[str], suffix_idx: int) -> NameSplit:
    """Firma: Name und Rechtsform enden am ersten Komma nach der Rechtsform (danach folgt die Anschrift)."""
    end = len(tokens)
    for j in range(suffix_idx, len(tokens)):
        if j > suffix_idx and re.search(r"\d", tokens[j]):
            end = j
            break
        if tokens[j].endswith((",", ";")):
            end = j + 1
            break
    name = " ".join(tokens[:end]).strip(" ,;")
    legal = " ".join(tokens[suffix_idx:end]).strip(" ,;")
    return NameSplit(last=name, is_company=True, confidence=0.9, legal_form=legal or None)


def split_person_name(raw: str, order: str | None = None) -> NameSplit:
    """Namen in Nachname/Vorname aufteilen (Firmen werden nicht aufgeteilt).

    * „Nachname, Vorname“ -> Konfidenz 0,9 (nur wenn beide Teile wie Namen aussehen)
    * „Vorname Nachname“ -> 0,6 mit Hinweis
    * mehr als zwei Teile -> letzter Teil als Nachname, 0,4 mit Hinweis
    * Anrede (Herr/Frau) und Titel (Dr., Prof.) werden abgetrennt
    * angehängtes Geburtsdatum („, geb. 12.03.1980“) und Anschrift („, Musterweg 5 …“)
      werden abgetrennt; das Geburtsdatum steht in ``birth_date``
    * nicht sicher aufteilbar -> Konfidenz 0,3 mit Hinweis

    ``order``: Reihenfolge laut Beschriftung, ``"last_first"`` („Name, Vorname“)
    oder ``"first_last"`` („Vorname Name“); ``None`` = unbekannt.
    """
    text = normalize_ws(to_nfc(raw)).strip(" ,;")
    if not text:
        return NameSplit(last="", confidence=0.0, note=NOTE_UNREADABLE)

    tokens = text.split(" ")
    suffix_idx = _company_suffix_index(tokens)
    if suffix_idx is not None:
        return _company_split(tokens, suffix_idx)

    salutation_parts: list[str] = []
    while tokens:
        key = re.sub(r"[^a-z]", "", fold(tokens[0]))
        if key in _SALUTATIONS and not salutation_parts:
            salutation_parts.append(_SALUTATIONS[key])
            tokens = tokens[1:]
        elif key in _TITLES and tokens[0].endswith("."):
            salutation_parts.append(tokens[0])
            tokens = tokens[1:]
        else:
            break
    salutation = " ".join(salutation_parts) or None
    rest, birth = _strip_name_tail(" ".join(tokens).strip(" ,"))
    if not rest:
        return NameSplit(
            last="", salutation=salutation, confidence=0.0, note=NOTE_UNREADABLE, birth_date=birth
        )

    if "," in rest:
        pieces = [p.strip(" ,") for p in rest.split(",")]
        if len(pieces) == 2 and all(_name_like(p) for p in pieces):
            first_part, second_part = pieces
            if order == "first_last":  # „Vorname, Name: Max, Mustermann“
                return NameSplit(
                    last=second_part,
                    first=first_part,
                    salutation=salutation,
                    confidence=0.9,
                    birth_date=birth,
                )
            return NameSplit(
                last=first_part, first=second_part, salutation=salutation, confidence=0.9, birth_date=birth
            )
        # Mehrere Kommas oder kein Name: nicht raten, sondern unsicher kennzeichnen
        last, _, first = rest.partition(",")
        return NameSplit(
            last=last.strip(" ,"),
            first=first.strip(" ,") or None,
            salutation=salutation,
            confidence=0.3,
            note=NOTE_NAME_UNCLEAR,
            birth_date=birth,
        )

    parts = [p if len(p) <= 3 else p.rstrip(".;:") for p in rest.split(" ")]
    if len(parts) == 1:
        return NameSplit(
            last=parts[0], salutation=salutation, confidence=0.5, note=NOTE_NAME_SINGLE, birth_date=birth
        )
    if len(parts) == 2:
        first_word, second_word = parts
        if order == "last_first":  # „Name, Vorname: Mustermann Max“ (Komma fehlt)
            first_word, second_word = second_word, first_word
        return NameSplit(
            last=second_word,
            first=first_word,
            salutation=salutation,
            confidence=0.6,
            note=NOTE_NAME_TWO_TOKENS,
            birth_date=birth,
        )
    # >2 Teile: letzter Teil = Nachname, vorangestellte Namenspartikel gehören dazu
    last_start = len(parts) - 1
    while last_start > 1 and parts[last_start - 1].lower() in _NAME_PARTICLES:
        last_start -= 1
    return NameSplit(
        last=" ".join(parts[last_start:]),
        first=" ".join(parts[:last_start]),
        salutation=salutation,
        confidence=0.4,
        note=NOTE_NAME_MANY_TOKENS,
        birth_date=birth,
    )


# ---------------------------------------------------------------------------
# Zahlweise / Vertragsnummer / Ganzzahl / Bool
# ---------------------------------------------------------------------------

_FREQUENCY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"halbjaehr|halbjahr|1/2\s*-?\s*jaehr|semiannual"), "semiannual"),
    (re.compile(r"vierteljaehr|vierteljahr|quartal|1/4\s*-?\s*jaehr|quarterly"), "quarterly"),
    (re.compile(r"monatl|\bmtl\b|monatsbeitrag|pro monat|\bp\.\s?m\b|monthly"), "monthly"),
    (re.compile(r"einmal|single"), "single"),
    (re.compile(r"jaehrl|jahresbeitrag|pro jahr|\bp\.\s?a\b|annual"), "annual"),
]


def normalize_frequency(raw: str | None) -> str | None:
    """Zahlweise -> Schlüssel aus ``FREQUENCY_CHOICES`` (``monthly``, ``annual`` …)."""
    if not raw or is_placeholder(raw):
        return None
    f = fold(raw)
    for key in FREQUENCY_CHOICES:  # interne Schlüssel direkt akzeptieren
        if f == key:
            return key
    for pattern, key in _FREQUENCY_RULES:
        if pattern.search(f):
            return key
    return None


def frequency_from_text(text: str) -> str | None:
    """Zahlweise aus Beschriftung/Text ableiten (z. B. „Monatsbeitrag“, „Beitrag (monatlich)“)."""
    return normalize_frequency(text)


_CN_PREFIX = re.compile(r"^(?:nr|nummer|no)\s*[.:]?\s*", re.IGNORECASE)
_CN_TOKEN = re.compile(r"^[A-Z0-9./-]+$")
_DATE_TOKEN = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$")


def normalize_contract_number(raw: str | None) -> str | None:
    """Vertrags-/Versicherungsnummer: getrimmt, Großbuchstaben, Leerzeichen innerhalb entfernt.

    ``-``, ``/`` und ``.`` bleiben erhalten. Folgt nach der Nummer weiterer Text
    (z. B. „vom 01.01.2019“), wird nur die Nummer übernommen.
    """
    if not raw or is_placeholder(raw):
        return None
    text = _CN_PREFIX.sub("", normalize_ws(raw).strip(" :;,"))
    tokens = text.upper().split(" ")
    taken: list[str] = []
    for idx, tok in enumerate(tokens):
        tok = tok.strip(",;")
        if not tok:
            break
        if not _CN_TOKEN.match(tok) or _DATE_TOKEN.match(tok):
            break
        has_digit = any(c.isdigit() for c in tok)
        if not has_digit:
            # Buchstaben-Präfix (z. B. "LV 123456") nur am Anfang und kurz zulassen
            if idx == 0 and len(tok) <= 4 and len(tokens) > 1 and any(c.isdigit() for c in tokens[1]):
                taken.append(tok)
                continue
            break
        taken.append(tok)
    number = "".join(taken).strip(".-/")
    if not number or not any(c.isdigit() for c in number) or len(number) < 3:
        return None
    return number


def normalize_integer(raw: str | None) -> str | None:
    """Ganzzahl aus Text („67 Jahre“ -> „67“)."""
    if not raw or is_placeholder(raw):
        return None
    m = re.search(r"(?<![\d.,])(\d{1,9})(?![\d.,]\d)", raw)
    return str(int(m.group(1))) if m else None


_TRUE = {"ja", "true", "x", "yes", "j", "1", "zutreffend", "angekreuzt"}
_FALSE = {"nein", "false", "no", "n", "0", "nichtzutreffend"}


def normalize_bool(raw: str | None) -> str | None:
    """Ja/Nein -> ``true``/``false``."""
    if raw is None:
        return None
    key = compact_key(raw) if raw.strip().lower() not in {"x", "1", "0"} else raw.strip().lower()
    if key in _TRUE:
        return "true"
    if key in _FALSE:
        return "false"
    return None


def _normalize_choice(fdef: FieldDef, raw: str) -> str | None:
    if not fdef.choices:
        return None
    if fdef.choices == FREQUENCY_CHOICES:
        return normalize_frequency(raw)
    key = compact_key(raw)
    for choice_key, label in fdef.choices.items():
        if key in (compact_key(choice_key), compact_key(label)):
            return choice_key
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _strict_text(raw: str) -> str | None:
    """Manueller Text: Zeilenumbrüche bleiben, nur Leerraum innerhalb jeder Zeile wird zusammengefasst."""
    lines = [normalize_ws(line) for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n") or None


def _is_birth_date(fdef: FieldDef) -> bool:
    return fdef.key.endswith("birth_date")


def normalize_value(
    fdef: FieldDef,
    raw: str,
    *,
    strict: bool = False,
    today: date | None = None,
) -> tuple[str | None, str | None]:
    """Rohwert für ein Feld normalisieren -> (normalisierter Wert, Hinweis).

    Ist der Wert nicht eindeutig lesbar, ist der normalisierte Wert ``None`` und
    der Hinweis lautet „Wert nicht eindeutig lesbar“ (oder nennt den genauen Grund,
    z. B. bei einem Tagesdatum in einem Monatsfeld).

    ``strict=True`` ist für Eingaben des Nutzers gedacht:

    * Nur leerer Text gilt als leer – Wörter wie „k.A.“ oder „Na“ hat der Nutzer
      bewusst eingegeben und werden nicht als Platzhalter verworfen.
    * Beträge müssen genau ein Betrag mit höchstens zwei Nachkommastellen sein
      (kein weiterer Text, keine Rundung).
    * Text behält Zeilenumbrüche, und Satzzeichen am Rand bleiben erhalten.

    In beiden Modi wird der Text in die Unicode-Normalform NFC gebracht.
    ``today`` ist das Bezugsdatum für zweistellige Jahre (Standard: heute).
    """
    if raw is None:
        return None, None
    raw = to_nfc(raw)
    if strict:
        if not raw.strip():
            return None, None
    elif is_placeholder(raw):
        return None, None
    text = normalize_ws(raw)
    dtype = fdef.dtype
    result: str | None
    note: str | None = None
    if dtype == FieldType.TEXT and strict:
        result = _strict_text(raw)
    elif dtype in (FieldType.TEXT, FieldType.NAME) and strict:
        result = text or None
    elif dtype in (FieldType.TEXT, FieldType.NAME):
        result = text.strip(" ,;:")
        if result.endswith(")."):
            result = result[:-1]  # Punkt nach Klammer ist meist ein Scan-Artefakt
        result = result or None
    elif dtype == FieldType.DATE:
        if _is_birth_date(fdef):
            result = normalize_date(text, past_only=True, today=today)
        else:
            result = normalize_date(text, max_future_years=TWO_DIGIT_YEAR_FUTURE_WINDOW, today=today)
        if result is not None and _has_two_digit_year(text):
            note = NOTE_TWO_DIGIT_YEAR
    elif dtype == FieldType.MONTH:
        result, note = _month_with_note(text)
        if result is None and note is not None:
            return None, note
    elif dtype == FieldType.AMOUNT:
        if strict:
            result, currency = _normalize_amount_strict(text)
            if result is None:
                return None, NOTE_AMOUNT_STRICT
        else:
            result, currency = normalize_amount(text)
        if result is not None and currency not in (None, "EUR"):
            note = f"Währung laut Quelle: {currency}"
    elif dtype == FieldType.CURRENCY:
        result = normalize_currency(text)
    elif dtype == FieldType.IBAN:
        result = normalize_iban(text)
    elif dtype == FieldType.POSTAL_CODE:
        result = normalize_postal_code(text)
    elif dtype == FieldType.COUNTRY:
        result = normalize_country(text)
    elif dtype == FieldType.CONTRACT_NUMBER:
        result = normalize_contract_number(text)
    elif dtype == FieldType.INTEGER:
        result = normalize_integer(text)
    elif dtype == FieldType.BOOL:
        result = normalize_bool(text)
    elif dtype == FieldType.CHOICE:
        result = _normalize_choice(fdef, text)
    else:  # pragma: no cover - neue Feldtypen müssen hier ergänzt werden
        result = text
    if result is None:
        return None, NOTE_UNREADABLE
    return result, note
