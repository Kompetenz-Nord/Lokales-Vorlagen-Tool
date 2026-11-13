"""Schwärzung personenbezogener Muster in Log-Texten.

Logs dürfen nur technische Angaben enthalten. Dieser Filter ist eine zweite
Sicherung: Er ersetzt typische Muster (IBAN, E-Mail, Datum, Telefonnummer,
längere Ziffernfolgen wie Vertragsnummern) durch Platzhalter.

Bewusst geschützt (nicht geschwärzt) werden nur die nicht sprechenden
technischen Kennungen der App: Vorgangs-IDs (``V20261113-A1B2C3``),
Dokument-IDs (``D-1A2B3C4D``) und Job-IDs (``J-1A2B3C4D``). Andere Kennungen
werden wie normaler Text behandelt.
"""

from __future__ import annotations

import logging
import re

__all__ = [
    "PLACEHOLDER_DATE",
    "PLACEHOLDER_EMAIL",
    "PLACEHOLDER_IBAN",
    "PLACEHOLDER_NUMBER",
    "PLACEHOLDER_PHONE",
    "RedactingFilter",
    "redact",
]

PLACEHOLDER_IBAN = "[IBAN]"
PLACEHOLDER_EMAIL = "[E-MAIL]"
PLACEHOLDER_DATE = "[DATUM]"
PLACEHOLDER_PHONE = "[TELEFON]"
PLACEHOLDER_NUMBER = "[ZAHL]"

# Technische, nicht sprechende Kennungen der App (siehe core.case.new_case_id/new_document_id)
_PROTECTED = re.compile(
    r"\bV\d{8}-[0-9A-F]{6}\b"  # Vorgangs-ID (core.case.new_case_id)
    r"|\b[DJ]-[0-9A-F]{8}\b"  # Dokument-ID (core.case.new_document_id) / Job-ID (new_job_id)
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

# IBAN: Ländercode + 2 Prüfziffern + Vierergruppen (kompakt oder mit Leerzeichen)
_IBAN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?\d{1,3})?(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Datumsangaben: TT.MM.JJJJ, T.M.JJ, TT/MM/JJJJ, JJJJ-MM-TT, JJJJ-MM-TTThh:mm
_DATE = re.compile(
    r"(?<!\d)\d{1,2}\s?[./]\s?\d{1,2}\s?[./]\s?(?:\d{4}|\d{2})(?!\d)"
    r"|(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)"
    r"|(?<!\d)\d{4}/\d{1,2}/\d{1,2}(?!\d)"
)

# Internationale Telefonnummern (+49 …, 0049 …)
_PHONE_INTL = re.compile(r"(?<![\w+])(?:\+|00)[1-9]\d{0,3}(?:[ \t/-]{0,2}\(?\d+\)?){2,}")
# Nationale Telefonnummern mit Trennzeichen (089 / 123456, 0171-1234567)
_PHONE_NATIONAL = re.compile(r"(?<![\w])0\d{2,5}(?:[ \t]?[/-][ \t]?|[ \t])\d[\d \t-]{2,}\d(?!\d)")

# Ziffernfolgen mit insgesamt mindestens 5 Ziffern, auch mit Trennzeichen (9630030-1, 1.234,56)
_DIGITS = re.compile(r"(?<!\d)\d(?:[ .,/-]?\d)+(?!\d)")

# Platzhalter für geschützte Kennungen: Zeichen aus dem Unicode-Privatbereich
# (keine Ziffern, keine Wortzeichen -> von keinem Muster erfasst)
_PROTECT_BASE = 0xE000
_PROTECT_RESTORE = re.compile("\x00([\ue000-\uf8ff])\x00")


def _replace_iban(match: re.Match[str]) -> str:
    bban = match.group(0).replace(" ", "")[4:]
    return PLACEHOLDER_IBAN if sum(ch.isdigit() for ch in bban) >= 6 else match.group(0)


def _replace_digit_runs(match: re.Match[str]) -> str:
    text = match.group(0)
    digits = sum(ch.isdigit() for ch in text)
    return PLACEHOLDER_NUMBER if digits >= 5 else text


def redact(text: str) -> str:
    """Typische personenbezogene Muster durch Platzhalter ersetzen.

    Idempotent: Eine bereits geschwärzte Zeichenkette bleibt unverändert.
    """
    if not text:
        return text
    text = text.replace("\x00", "")
    protected: list[str] = []

    def _protect(m: re.Match[str]) -> str:
        if len(protected) >= 0x18FF:  # Privatbereich erschöpft: nicht mehr schützen (sicher: schwärzen)
            return m.group(0)
        protected.append(m.group(0))
        return f"\x00{chr(_PROTECT_BASE + len(protected) - 1)}\x00"

    work = _PROTECTED.sub(_protect, text)
    work = _EMAIL.sub(PLACEHOLDER_EMAIL, work)
    work = _IBAN.sub(_replace_iban, work)
    work = _PHONE_INTL.sub(PLACEHOLDER_PHONE, work)
    work = _DATE.sub(PLACEHOLDER_DATE, work)
    work = _PHONE_NATIONAL.sub(PLACEHOLDER_PHONE, work)
    work = _DIGITS.sub(_replace_digit_runs, work)
    return _PROTECT_RESTORE.sub(lambda m: protected[ord(m.group(1)) - _PROTECT_BASE], work)


class RedactingFilter(logging.Filter):
    """Logging-Filter: formatiert die Nachricht mit ihren Argumenten, schwärzt sie
    und entfernt die Argumente (damit kein Handler die Rohdaten erneut einsetzt).

    Auch Ausnahme-Texte (Traceback) und ``stack_info`` werden geschwärzt.
    Der Filter lässt jeden Datensatz durch (Rückgabe ``True``).
    """

    _exc_formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # fehlerhafte Format-Argumente: nur den Formatstring verwenden
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = self._exc_formatter.formatException(record.exc_info)
            except Exception:
                record.exc_text = "Traceback nicht verfügbar"
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
