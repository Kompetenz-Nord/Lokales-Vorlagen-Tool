"""Feldprüfungen (Format, Prüfziffern, Wertebereiche) und feldübergreifende Prüfungen.

Meldungen sind kurz, in einfacher Sprache und nennen NIE vollständige
Personen- oder Bankdaten (nur Feldbezeichnungen).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from insurance_app.core.fields import FIELD_REGISTRY, FieldDef, FieldType
from insurance_app.core.models import FieldStatus, FieldValue, Severity, ValidationMessage
from insurance_app.core.textutil import parse_german_date

__all__ = [
    "IBAN_LENGTHS",
    "LARGE_AMOUNT_THRESHOLD",
    "PREMIUM_END_CODE",
    "iban_is_valid",
    "postal_code_de_is_valid",
    "validate_case",
    "validate_field",
]

# Länge der IBAN je Land (SEPA-Raum und häufige Nachbarländer)
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24,
    "AT": 20,
    "BE": 16,
    "BG": 22,
    "CH": 21,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "EE": 20,
    "ES": 24,
    "FI": 18,
    "FR": 27,
    "GB": 22,
    "GI": 23,
    "GR": 27,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IS": 26,
    "IT": 27,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MC": 27,
    "MT": 31,
    "NL": 18,
    "NO": 15,
    "PL": 28,
    "PT": 25,
    "RO": 24,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "TR": 26,
    "VA": 22,
}

LARGE_AMOUNT_THRESHOLD = Decimal("100000")
#: Letzte Beitragsfälligkeit nach Ablauf/Rentenbeginn (früher „PREMIUM_END_AFTER_END“)
PREMIUM_END_CODE = "DATE_ORDER_PREMIUM_END"

_IBAN_SHAPE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_CONTRACT_ALLOWED = re.compile(r"^[A-Z0-9./ -]+$")
_OCR_CONFUSABLE = re.compile(r"(?<=\d)[OoIl](?=\d)|(?<=\d)[OoIl]$|^[OoIl](?=\d)")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_DATE_PAIRS: list[tuple[str, str, str, str]] = [
    (
        "contract.start_date",
        "contract.end_date",
        "DATE_ORDER_CONTRACT",
        "Der Versicherungsbeginn liegt nicht vor dem Ablaufdatum. Bitte beide Daten prüfen.",
    ),
    (
        "employer.employment_start",
        "employer.employment_end",
        "DATE_ORDER_EMPLOYMENT",
        "Der Beginn der Betriebszugehörigkeit liegt nicht vor ihrem Ende. Bitte beide Daten prüfen.",
    ),
    (
        "insured.birth_date",
        "contract.start_date",
        "DATE_ORDER_BIRTH_START",
        "Das Geburtsdatum liegt nicht vor dem Versicherungsbeginn. Bitte prüfen.",
    ),
]


def iban_is_valid(iban: str | None) -> bool:
    """IBAN-Prüfung: Form, Länge je Land (falls bekannt) und Prüfziffer (Modulo 97)."""
    if not iban:
        return False
    compact = re.sub(r"\s", "", iban).upper()
    if not _IBAN_SHAPE.match(compact) or len(compact) > 34:
        return False
    expected = IBAN_LENGTHS.get(compact[:2])
    if expected is not None and len(compact) != expected:
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def postal_code_de_is_valid(code: str | None) -> bool:
    """Deutsche Postleitzahl: genau 5 Ziffern im Bereich 01001–99998."""
    if not code or not re.fullmatch(r"\d{5}", code):
        return False
    return 1001 <= int(code) <= 99998


def _msg(severity: Severity, code: str, message: str, key: str | None) -> ValidationMessage:
    return ValidationMessage(severity=severity, code=code, message=message, field_key=key)


def _age_on(birth: date, ref: date) -> int:
    return ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))


def _format_hint(dtype: FieldType) -> str:
    return {
        FieldType.DATE: "TT.MM.JJJJ",
        FieldType.MONTH: "MM.JJJJ",
        FieldType.AMOUNT: "Betrag, z. B. 1.234,56",
        FieldType.IBAN: "IBAN",
        FieldType.POSTAL_CODE: "Postleitzahl",
        FieldType.INTEGER: "ganze Zahl",
        FieldType.BOOL: "Ja/Nein",
        FieldType.CHOICE: "Auswahl aus der Liste",
        FieldType.CURRENCY: "Währungscode, z. B. EUR",
        FieldType.CONTRACT_NUMBER: "Vertragsnummer",
    }.get(dtype, "Text")


def _canonical(fdef: FieldDef, fv: FieldValue) -> tuple[str | None, bool]:
    """Kanonischen Prüfwert ermitteln -> (Wert, wurde_nachträglich_normalisiert)."""
    if fv.normalized_value is not None:
        return fv.normalized_value, False
    if fv.value is None or not fv.value.strip():
        return None, False
    if fdef.dtype in (FieldType.TEXT, FieldType.NAME, FieldType.COUNTRY):
        return fv.value.strip(), False
    from insurance_app.extraction.normalize import normalize_value  # spät: vermeidet Importzyklus

    normalized, _ = normalize_value(fdef, fv.value)
    return normalized, True


def validate_field(fdef: FieldDef, fv: FieldValue, today: date | None = None) -> list[ValidationMessage]:
    """Einzelnes Feld nach Datentyp prüfen.

    Fehlende, nicht relevante und ungelöste Konflikt-Felder werden nicht geprüft
    (Pflichtfelder prüft die Vorlagen-Checkliste).
    """
    today = today or date.today()
    key = fdef.key
    if fv.status in (FieldStatus.MISSING, FieldStatus.NOT_APPLICABLE):
        return []
    value, _ = _canonical(fdef, fv)
    if value is None:
        if fv.value and fv.value.strip():
            severity = Severity.ERROR if fv.status.is_released else Severity.WARNING
            return [
                _msg(
                    severity,
                    "FORMAT_INVALID",
                    f"„{fdef.label}“ hat kein gültiges Format (erwartet: {_format_hint(fdef.dtype)}).",
                    key,
                )
            ]
        return []

    out: list[ValidationMessage] = []
    dtype = fdef.dtype
    if dtype == FieldType.DATE:
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return [_msg(Severity.ERROR, "DATE_INVALID", f"„{fdef.label}“ ist kein gültiges Datum.", key)]
        if not 1900 <= d.year <= 2200:
            return [_msg(Severity.ERROR, "DATE_RANGE", f"„{fdef.label}“: Jahr außerhalb 1900–2200.", key)]
        if key == "insured.birth_date":
            if d >= today:
                out.append(
                    _msg(
                        Severity.WARNING,
                        "BIRTH_DATE_FUTURE",
                        "Das Geburtsdatum liegt nicht in der Vergangenheit. Bitte prüfen.",
                        key,
                    )
                )
            else:
                age = _age_on(d, today)
                if not 14 <= age <= 100:
                    out.append(
                        _msg(
                            Severity.WARNING,
                            "BIRTH_DATE_AGE",
                            "Das Alter der versicherten Person liegt außerhalb von 14 bis 100 Jahren. Bitte prüfen.",
                            key,
                        )
                    )
        elif not 1950 <= d.year <= 2100:
            out.append(
                _msg(Severity.WARNING, "DATE_UNUSUAL", f"„{fdef.label}“ hat ein ungewöhnliches Jahr.", key)
            )
    elif dtype == FieldType.MONTH:
        if not _MONTH.match(value):
            out.append(
                _msg(
                    Severity.ERROR, "MONTH_INVALID", f"„{fdef.label}“ ist kein gültiger Monat (MM.JJJJ).", key
                )
            )
        elif _month_input_has_other_day(fv.value):
            # Altbestand: früher wurde der Tag eines vollen Datums stillschweigend verworfen
            out.append(
                _msg(
                    Severity.WARNING,
                    "MONTH_DAY_NOT_FIRST",
                    f"„{fdef.label}“: Eingegeben wurde ein Tagesdatum, das Formular gilt aber immer "
                    "zum 01. des Monats. Bitte nur Monat und Jahr (MM.JJJJ) eingeben.",
                    key,
                )
            )
    elif dtype == FieldType.AMOUNT:
        try:
            amount = Decimal(value)
        except (InvalidOperation, ValueError):
            return [_msg(Severity.ERROR, "AMOUNT_INVALID", f"„{fdef.label}“ ist kein gültiger Betrag.", key)]
        if not amount.is_finite():
            out.append(
                _msg(Severity.ERROR, "AMOUNT_INVALID", f"„{fdef.label}“ ist kein gültiger Betrag.", key)
            )
        elif amount < 0:
            out.append(
                _msg(Severity.ERROR, "AMOUNT_NEGATIVE", f"„{fdef.label}“ darf nicht negativ sein.", key)
            )
        elif amount > LARGE_AMOUNT_THRESHOLD:
            out.append(
                _msg(
                    Severity.WARNING,
                    "AMOUNT_LARGE",
                    f"„{fdef.label}“ ist ungewöhnlich hoch. Bitte prüfen (Komma/Punkt verwechselt?).",
                    key,
                )
            )
    elif dtype == FieldType.IBAN:
        if not iban_is_valid(value):
            out.append(
                _msg(
                    Severity.ERROR,
                    "IBAN_CHECKSUM",
                    f"„{fdef.label}“ ist ungültig (Prüfziffer oder Länge stimmt nicht).",
                    key,
                )
            )
    elif dtype == FieldType.POSTAL_CODE:
        if not postal_code_de_is_valid(value):
            out.append(
                _msg(
                    Severity.WARNING,
                    "POSTAL_CODE_DE",
                    f"„{fdef.label}“ ist keine gültige deutsche Postleitzahl (5 Ziffern). "
                    "Bei Anschriften im Ausland bitte das Land prüfen.",
                    key,
                )
            )
    elif dtype == FieldType.CONTRACT_NUMBER:
        out.extend(_check_contract_number(fdef, value))
    elif dtype == FieldType.NAME:
        if re.search(r"\d", value):
            out.append(
                _msg(
                    Severity.WARNING,
                    "NAME_DIGITS",
                    f"„{fdef.label}“ enthält Ziffern. Bitte prüfen (möglicher Lesefehler).",
                    key,
                )
            )
    elif dtype == FieldType.BOOL:
        if value not in ("true", "false"):
            out.append(_msg(Severity.ERROR, "BOOL_INVALID", f"„{fdef.label}“ muss Ja oder Nein sein.", key))
    elif dtype == FieldType.CHOICE:
        if not fdef.choices or value not in fdef.choices:
            out.append(
                _msg(
                    Severity.ERROR,
                    "CHOICE_INVALID",
                    f"„{fdef.label}“: Wert ist keine erlaubte Auswahl.",
                    key,
                )
            )
    elif dtype == FieldType.INTEGER:
        if not re.fullmatch(r"\d{1,9}", value):
            out.append(
                _msg(Severity.ERROR, "INTEGER_INVALID", f"„{fdef.label}“ muss eine ganze Zahl sein.", key)
            )
        elif key == "contract.end_age" and not 18 <= int(value) <= 100:
            out.append(
                _msg(
                    Severity.WARNING,
                    "END_AGE_UNUSUAL",
                    "Das Ablaufalter ist ungewöhnlich. Bitte prüfen.",
                    key,
                )
            )
    elif dtype == FieldType.CURRENCY:
        if not re.fullmatch(r"[A-Z]{3}", value):
            out.append(
                _msg(
                    Severity.WARNING,
                    "CURRENCY_INVALID",
                    f"„{fdef.label}“ ist kein Währungscode (z. B. EUR).",
                    key,
                )
            )
    return out


def _month_input_has_other_day(raw: str | None) -> bool:
    """Enthält die Eingabe eines Monatsfelds ein volles Datum mit einem anderen Tag als dem 01.?"""
    if not raw:
        return False
    d = parse_german_date(raw)
    return d is not None and d.day != 1


def _check_contract_number(fdef: FieldDef, value: str) -> list[ValidationMessage]:
    key = fdef.key
    out: list[ValidationMessage] = []
    if not 4 <= len(value) <= 30:
        out.append(
            _msg(
                Severity.WARNING,
                "CONTRACT_NUMBER_LENGTH",
                f"„{fdef.label}“ hat eine ungewöhnliche Länge (erwartet 4 bis 30 Zeichen).",
                key,
            )
        )
    if not _CONTRACT_ALLOWED.match(value.upper()):
        out.append(
            _msg(
                Severity.WARNING,
                "CONTRACT_NUMBER_CHARS",
                f"„{fdef.label}“ enthält ungewöhnliche Zeichen. Bitte prüfen.",
                key,
            )
        )
    if not re.search(r"\d", value):
        out.append(
            _msg(
                Severity.WARNING,
                "CONTRACT_NUMBER_NO_DIGITS",
                f"„{fdef.label}“ enthält keine Ziffern. Bitte prüfen.",
                key,
            )
        )
    if _OCR_CONFUSABLE.search(value):
        out.append(
            _msg(
                Severity.WARNING,
                "CONTRACT_NUMBER_OCR_CONFUSION",
                "Mögliche Verwechslung von O/0 oder I/1 durch die Texterkennung – bitte prüfen.",
                key,
            )
        )
    return out


def _date_of(fields: dict[str, FieldValue], key: str) -> tuple[date | None, bool]:
    fv = fields.get(key)
    if fv is None or fv.status in (FieldStatus.MISSING, FieldStatus.NOT_APPLICABLE, FieldStatus.CONFLICT):
        return None, False
    return fv.as_date(), fv.status.is_released


def validate_case(
    contract: dict[str, FieldValue],
    order: dict[str, FieldValue],
    today: date | None = None,
) -> list[ValidationMessage]:
    """Alle Felder eines Vorgangs prüfen und feldübergreifende Prüfungen ausführen."""
    today = today or date.today()
    out: list[ValidationMessage] = []
    for store in (contract, order):
        for key, fv in store.items():
            fdef = FIELD_REGISTRY.get(key)
            if fdef is None:
                continue
            msgs = validate_field(fdef, fv, today)
            if fdef.dtype == FieldType.POSTAL_CODE:
                msgs = _filter_foreign_postal(key, msgs, store)
            out.extend(msgs)

    all_fields = {**contract, **order}
    for first_key, second_key, code, text in _DATE_PAIRS:
        first, first_released = _date_of(all_fields, first_key)
        second, second_released = _date_of(all_fields, second_key)
        if first and second and first >= second:
            severity = Severity.ERROR if (first_released and second_released) else Severity.WARNING
            out.append(_msg(severity, code, text, second_key))

    premium_end, _ = _date_of(all_fields, "contract.premium_end_date")
    if premium_end:
        for other_key, label in (
            ("contract.end_date", "Ablaufdatum"),
            ("contract.pension_start_date", "Rentenbeginn"),
        ):
            other, _ = _date_of(all_fields, other_key)
            if other and premium_end > other:
                # Feldübergreifende Datumsreihenfolge: Code-Familie DATE_ORDER_* (wird im Vorgang angezeigt)
                out.append(
                    _msg(
                        Severity.WARNING,
                        PREMIUM_END_CODE,
                        f"Die letzte Beitragsfälligkeit liegt nach dem {label}. Bitte prüfen.",
                        "contract.premium_end_date",
                    )
                )
    return out


def _filter_foreign_postal(
    key: str, msgs: list[ValidationMessage], store: dict[str, FieldValue]
) -> list[ValidationMessage]:
    """Die 5-Ziffern-Regel gilt nur für deutsche Anschriften."""
    prefix = key.rsplit(".", 1)[0]
    country = store.get(f"{prefix}.country")
    country_value = (country.effective_value or "").strip().upper() if country else ""
    if country_value and country_value not in ("DE", "DEUTSCHLAND", "D"):
        return [m for m in msgs if m.code != "POSTAL_CODE_DE"]
    return msgs
