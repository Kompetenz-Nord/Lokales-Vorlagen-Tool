"""OCR-Verwechslungen: IBAN (O/0, I/1), Paragrafenzeichen, I/l – nichts wird geraten.

Alle Daten synthetisch („MUSTER – KEINE ECHTDATEN“).
"""

from __future__ import annotations

import pytest

from insurance_app.core.fields import FIELD_REGISTRY
from insurance_app.extraction.extractor import FORCE_UNCLEAR_CONFIDENCE
from insurance_app.extraction.merge import MIN_EXTRACTED_CONFIDENCE
from insurance_app.extraction.normalize import (
    NOTE_GLYPH_CONFUSION,
    NOTE_IBAN_CORRECTED,
    fix_paragraph_sign,
    has_glyph_confusion,
    normalize_value,
    repair_iban_ocr,
)

VALID = "DE02100100100006820101"  # synthetische Test-IBAN mit gültiger Prüfziffer


@pytest.mark.parametrize(
    "raw",
    [
        "DEO2 1001 0010 0006 8201 01",
        "DE02 1OO1 0010 0006 8201 01",
        "DE02 l00l 00l0 0006 820l 0l",
        "IBAN: DEO2100100100006820101",
    ],
)
def test_iban_repair_only_when_checksum_becomes_valid(raw):
    assert repair_iban_ocr(raw) == VALID
    normalized, note = normalize_value(FIELD_REGISTRY["bank.iban"], raw)
    assert normalized == VALID and note == NOTE_IBAN_CORRECTED


@pytest.mark.parametrize(
    "raw",
    [
        "DE02 1001 0010 0006 8201 01",  # schon gültig -> keine Korrektur, kein Hinweis
        "DEO2 1001 0010 0006 8201 02",  # auch nach Korrektur falsche Prüfziffer
        "DEO2 1001 0010",  # zu kurz
        "keine Angabe",
    ],
)
def test_iban_repair_never_guesses(raw):
    assert repair_iban_ocr(raw) is None
    _normalized, note = normalize_value(FIELD_REGISTRY["bank.iban"], raw)
    assert note != NOTE_IBAN_CORRECTED


def test_iban_repair_not_for_user_input():
    """Eingaben des Nutzers werden nie still umgeschrieben (strict)."""
    normalized, note = normalize_value(
        FIELD_REGISTRY["bank.iban"], "DEO2 1001 0010 0006 8201 01", strict=True
    )
    assert normalized != VALID and note != NOTE_IBAN_CORRECTED


def test_corrected_values_can_never_be_confident():
    assert FORCE_UNCLEAR_CONFIDENCE < MIN_EXTRACTED_CONFIDENCE


@pytest.mark.parametrize("wrong", ["8", "3", "5", "S", "$"])
def test_paragraph_sign_restored_in_law_reference(wrong):
    text = f"Direktversicherung gemäß {wrong} 3 Nr. 63 EStG (MUSTER)"
    assert fix_paragraph_sign(text) == "Direktversicherung gemäß § 3 Nr. 63 EStG (MUSTER)"
    normalized, _ = normalize_value(FIELD_REGISTRY["contract.product"], text)
    assert normalized == "Direktversicherung gemäß § 3 Nr. 63 EStG (MUSTER)"


@pytest.mark.parametrize(
    "text",
    ["Tarif 8 3 Jahre Laufzeit", "gemäß § 3 Nr. 63 EStG", "nach 8 Jahren", "Rente nach 5 10 Jahren"],
)
def test_paragraph_fix_leaves_other_text_alone(text):
    assert fix_paragraph_sign(text) == text


def test_glyph_confusion_marks_value_for_review_without_changing_it():
    assert has_glyph_confusion("IImweg") and not has_glyph_confusion("Ilmweg")
    assert not has_glyph_confusion("Haus II") and not has_glyph_confusion("Abschnitt IIa")
    normalized, note = normalize_value(FIELD_REGISTRY["insured.street"], "IImweg")
    assert normalized == "IImweg" and note == NOTE_GLYPH_CONFUSION
