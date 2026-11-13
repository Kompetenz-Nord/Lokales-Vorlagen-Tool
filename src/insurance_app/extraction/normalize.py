"""Normalisierung gelesener Rohwerte in kanonische Zeichenketten.

Kanonische Formen (siehe ``core.models``):
* Datum ``YYYY-MM-DD``, Monat ``YYYY-MM``
* Betrag ``1234.56`` (Währung getrennt)
* IBAN kompakt in Großbuchstaben
* Bool ``true``/``false``, Auswahl = interner Schlüssel

Alle Funktionen sind rein (keine Seiteneffekte, kein Logging von Inhalten) und
geben bei nicht eindeutig lesbaren Werten ``None`` zurück, statt zu raten.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

from insurance_app.core.fields import FREQUENCY_CHOICES, FieldDef, FieldType
from insurance_app.core.textutil import fold, normalize_ws, parse_german_date, parse_month

__all__ = [
    "COMPANY_SUFFIXES",
    "NameSplit",
    "compact_key",
    "frequency_from_text",
    "is_placeholder",
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

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def compact_key(text: str) -> str:
    """Vergleichsschlüssel: gefaltet (``fold``), nur Buchstaben a–z und Ziffern."""
    return _NON_ALNUM.sub("", fold(text))


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
    "entfaellt",
}


def is_placeholder(raw: str | None) -> bool:
    """``True`` für Platzhalter wie „Unbekannt“, „-“, „k.A.“, „keine Angabe“ oder leeren Text."""
    if raw is None:
        return True
    key = re.sub(r"[\s.:()]", "", fold(raw))
    return key in _PLACEHOLDER_KEYS


# ---------------------------------------------------------------------------
# Datum / Monat
# ---------------------------------------------------------------------------


def normalize_date(raw: str | None) -> str | None:
    """Deutsches Datum (``01.02.2022``, ``13. März 2025`` …) -> ``YYYY-MM-DD``."""
    if not raw or is_placeholder(raw):
        return None
    d = parse_german_date(raw)
    return d.isoformat() if d else None


_ISO_MONTH = re.compile(r"(?<!\d)(\d{4})-(\d{2})(?![\d-])")


def normalize_month(raw: str | None) -> str | None:
    """Monat/Jahr (``03.2025``, ``März 2025``, ``2025-03``, auch volles Datum) -> ``YYYY-MM``."""
    if not raw or is_placeholder(raw):
        return None
    m = _ISO_MONTH.search(raw)
    if m and 1 <= int(m.group(2)) <= 12 and 1900 <= int(m.group(1)) <= 2200:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    d = parse_german_date(raw)
    if d:
        return f"{d.year:04d}-{d.month:02d}"
    ym = parse_month(raw)
    if ym:
        return f"{ym[0]:04d}-{ym[1]:02d}"
    return None


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
                if parts[0].isdigit() and parts[1].isdigit() and 1 <= len(parts[1]) <= 3:
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


def parse_amount(raw: str | None) -> tuple[Decimal, str | None] | None:
    """Ersten Geldbetrag lesen: ``1.234,56 EUR``, ``1234,56 €``, ``EUR 1.234,56``, ``150,-``.

    Rückgabe: (Betrag, Währungscode oder ``None``) bzw. ``None``, wenn kein Betrag erkennbar ist.
    """
    if not raw or is_placeholder(raw):
        return None
    text = normalize_ws(raw)
    text = _SPACE_GROUP.sub(lambda m: m.group(1) + re.sub(r"\s", "", m.group(2)), text)
    for m in _NUMBER_TOKEN.finditer(text):
        value = _interpret_number(m.group(2))
        if value is None:
            continue
        if m.group(3) and value == value.to_integral_value():
            value = value.quantize(Decimal("0.01"))
        if m.group(1):
            value = -value
        return value, _detect_currency(text)
    return None


def normalize_amount(raw: str | None) -> tuple[str | None, str | None]:
    """Betrag -> (``"1234.56"``, Währung). Nicht lesbar -> (``None``, ``None``)."""
    parsed = parse_amount(raw)
    if parsed is None:
        return None, None
    value, currency = parsed
    return f"{value.quantize(Decimal('0.01')):.2f}", currency


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


def _company_suffix_index(tokens: list[str]) -> int | None:
    for idx, tok in enumerate(tokens):
        if idx == 0:
            continue  # Ein Firmenname beginnt nicht mit der Rechtsform
        stripped = tok.strip(",;()")
        if stripped in _EXACT_SUFFIXES or stripped.lower() in _CI_SUFFIXES:
            return idx
    return None


def split_person_name(raw: str) -> NameSplit:
    """Namen in Nachname/Vorname aufteilen (Firmen werden nicht aufgeteilt).

    * „Nachname, Vorname“ -> Konfidenz 0,9
    * „Vorname Nachname“ -> 0,6 mit Hinweis
    * mehr als zwei Teile -> letzter Teil als Nachname, 0,4 mit Hinweis
    * Anrede (Herr/Frau) und Titel (Dr., Prof.) werden abgetrennt
    """
    text = normalize_ws(raw).strip(" ,;")
    if not text:
        return NameSplit(last="", confidence=0.0, note=NOTE_UNREADABLE)

    tokens = text.split(" ")
    suffix_idx = _company_suffix_index(tokens)
    if suffix_idx is not None:
        legal = " ".join(tokens[suffix_idx:]).strip(" ,")
        return NameSplit(last=text, is_company=True, confidence=0.9, legal_form=legal or None)

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
    rest = " ".join(tokens).strip(" ,")
    if not rest:
        return NameSplit(last="", salutation=salutation, confidence=0.0, note=NOTE_UNREADABLE)

    if "," in rest:
        last, first = (p.strip() for p in rest.split(",", 1))
        first = first.strip(" ,") or None
        if last:
            return NameSplit(last=last, first=first, salutation=salutation, confidence=0.9)

    parts = [p if len(p) <= 3 else p.rstrip(".;:") for p in rest.split(" ")]
    if len(parts) == 1:
        return NameSplit(last=parts[0], salutation=salutation, confidence=0.5, note=NOTE_NAME_SINGLE)
    if len(parts) == 2:
        return NameSplit(
            last=parts[1], first=parts[0], salutation=salutation, confidence=0.6, note=NOTE_NAME_TWO_TOKENS
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


def normalize_value(fdef: FieldDef, raw: str) -> tuple[str | None, str | None]:
    """Rohwert für ein Feld normalisieren -> (normalisierter Wert, Hinweis).

    Ist der Wert nicht eindeutig lesbar, ist der normalisierte Wert ``None`` und
    der Hinweis lautet „Wert nicht eindeutig lesbar“.
    """
    if raw is None or is_placeholder(raw):
        return None, None
    text = normalize_ws(raw)
    dtype = fdef.dtype
    result: str | None
    note: str | None = None
    if dtype in (FieldType.TEXT, FieldType.NAME):
        result = text.strip(" ,;:")
        if result.endswith(")."):
            result = result[:-1]  # Punkt nach Klammer ist meist ein Scan-Artefakt
        result = result or None
    elif dtype == FieldType.DATE:
        result = normalize_date(text)
    elif dtype == FieldType.MONTH:
        result = normalize_month(text)
    elif dtype == FieldType.AMOUNT:
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
