"""Schwärzung personenbezogener Muster (synthetische MUSTER-Werte)."""

from __future__ import annotations

import logging

import pytest

from insurance_app.security.redaction import RedactingFilter, redact

MUSTER_IBAN = "DE89370400440532013000"


@pytest.mark.parametrize(
    "text,secret",
    [
        (f"IBAN {MUSTER_IBAN} gelesen", MUSTER_IBAN),
        ("IBAN DE89 3704 0044 0532 0130 00 gelesen", "3704 0044"),
        ("iban de89370400440532013000", "370400440532013000"),
        ("GB82WEST12345698765432", "WEST12345698765432"),
        ("Mail an erika.musterfrau@example.org senden", "erika.musterfrau@example.org"),
        ("geboren am 12.08.1988", "12.08.1988"),
        ("Beginn 1.1.19", "1.1.19"),
        ("Datum 2019-01-01 erkannt", "2019-01-01"),
        ("Datum 01/02/2020", "01/02/2020"),
        ("Vertrag 9630030-1 gefunden", "9630030"),
        ("Nummer 8815201/7", "8815201"),
        ("PLZ 12345", "12345"),
        ("Betrag 1.234,56 EUR", "1.234,56"),
        ("Telefon +49 30 1234567", "1234567"),
        ("Telefon 0049 (0)30 123456", "123456"),
        ("Telefon 030 / 12 34 56", "12 34 56"),
        ("Mobil 0171-1234567", "1234567"),
        ("Kundennummer AB1234567", "1234567"),
    ],
)
def test_patterns_are_redacted(text, secret):
    out = redact(text)
    assert secret not in out
    assert out != text


@pytest.mark.parametrize(
    "text",
    [
        "Seite 3 von 12, 4 Felder erkannt",
        "Dauer 0.53 s",
        "OCR-Qualität 0.87",
        "[job J-1A2B3C4D] Vorgang V20261113-ABC123 Dokument D-12345678 fertig",
        "Tesseract 5.3.4 gefunden",
        "",
    ],
)
def test_technical_text_is_kept(text):
    assert redact(text) == text


def test_redaction_is_idempotent():
    once = redact(f"IBAN {MUSTER_IBAN}, Datum 01.01.2019, Nr. 9630030-1, mail a@b.de")
    assert redact(once) == once
    assert "[IBAN]" in once and "[DATUM]" in once and "[ZAHL]" in once and "[E-MAIL]" in once


def test_protected_ids_do_not_shield_neighbours():
    out = redact("V20261113-ABC123 9630030-1 D-1A2B3C4D")
    assert out == "V20261113-ABC123 [ZAHL] D-1A2B3C4D"
    # Private-Use-Zeichen im Eingangstext stören die Rücksetzung nicht
    assert redact("x\x00\x00 12345") == "x [ZAHL]"


def _record(msg, *args, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, msg, args or None, exc_info)


def test_filter_formats_args_redacts_and_clears():
    record = _record("Vertrag %s, IBAN %s, Seite %d", "9630030-1", MUSTER_IBAN, 2)
    assert RedactingFilter().filter(record) is True
    assert record.args is None
    assert record.getMessage() == "Vertrag [ZAHL], IBAN [IBAN], Seite 2"


def test_filter_handles_bad_format_args():
    record = _record("Wert %d", "kein int 12345")
    RedactingFilter().filter(record)
    assert record.getMessage() == "Wert %d"


def test_filter_redacts_exception_text():
    try:
        raise ValueError(f"IBAN {MUSTER_IBAN} ungültig")
    except ValueError:
        import sys

        record = _record("Fehler", exc_info=sys.exc_info())
    RedactingFilter().filter(record)
    assert record.exc_text and MUSTER_IBAN not in record.exc_text
    formatted = logging.Formatter().format(record)
    assert MUSTER_IBAN not in formatted
    assert "ValueError" in formatted
