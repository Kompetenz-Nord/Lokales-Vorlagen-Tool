"""Seitenqualität (ocr.page_quality) und Basis-Hilfen (ocr.base)."""

from __future__ import annotations

import pytest

from insurance_app.core.models import BoundingBox, PageQuality, TextLayerOrigin, Word
from insurance_app.core.settings import Settings
from insurance_app.ocr.base import (
    OcrBackend,
    OcrUnavailableError,
    scale_bbox,
    weighted_mean_confidence,
    words_to_text,
)
from insurance_app.ocr.docling_backend import DoclingBackend
from insurance_app.ocr.page_quality import (
    EMPTY_OCR_WARNING,
    LOW_QUALITY_WARNING,
    assess_text_quality,
    text_statistics,
)
from insurance_app.ocr.rapidocr_backend import RapidOcrBackend
from insurance_app.ocr.tesseract_backend import TesseractBackend

GOOD_TEXT = (
    "MUSTER – KEINE ECHTDATEN. Versicherungsnummer: 4711-0815. Der Rückkaufswert beträgt "
    "11.000,00 EUR zum 31.12.2024. Überschussbeteiligung und Garantiewert sind enthalten."
)


def w(text: str, conf: float | None = None, block: int = 0, line: int = 0, x: float = 0.0) -> Word:
    return Word(
        text=text,
        bbox=BoundingBox(x0=x, y0=0, x1=x + 10, y1=10),
        confidence=conf,
        block_no=block,
        line_no=line,
    )


def test_good_text():
    quality, warnings = assess_text_quality(GOOD_TEXT, [], TextLayerOrigin.NATIVE, None, Settings())
    assert quality == PageQuality.GOOD
    assert warnings == []


def test_empty_text_native_and_ocr():
    q, warn = assess_text_quality("Seite 1", [], TextLayerOrigin.NATIVE, None, Settings())
    assert q == PageQuality.EMPTY
    assert warn == []
    q, warn = assess_text_quality("", [], TextLayerOrigin.OCR, None, Settings())
    assert q == PageQuality.EMPTY
    assert warn == [EMPTY_OCR_WARNING]


def test_min_chars_from_settings():
    settings = Settings(min_text_chars_per_page=5)
    q, _ = assess_text_quality("MUSTER Text", [], TextLayerOrigin.NATIVE, None, settings)
    assert q == PageQuality.GOOD


def test_low_confidence():
    q, warn = assess_text_quality(GOOD_TEXT, [], TextLayerOrigin.OCR, 0.55, Settings())
    assert q == PageQuality.LOW
    assert warn == ["Niedrige Erkennungsqualität – bitte Werte manuell prüfen."]
    assert warn == [LOW_QUALITY_WARNING]
    q, _ = assess_text_quality(GOOD_TEXT, [], TextLayerOrigin.OCR, 0.9, Settings())
    assert q == PageQuality.GOOD


def test_garbage_characters_are_low():
    garbage = GOOD_TEXT + " " + "�|~^" * 20
    q, warn = assess_text_quality(garbage, [], TextLayerOrigin.EMBEDDED_OCR, None, Settings())
    assert q == PageQuality.LOW
    assert warn == [LOW_QUALITY_WARNING]


def test_non_word_tokens_are_low():
    junk = " ".join(["xqzvbnmklt", "|l1|", "ii,;", "~~", "Wqrtzxcvbn", "'.,", "MUSTER", "a"] * 8)
    stats = text_statistics(junk)
    assert stats.plausible_share < 0.55
    q, _ = assess_text_quality(junk, [], TextLayerOrigin.NATIVE, None, Settings())
    assert q == PageQuality.LOW


def test_statistics_score_orders_quality():
    good = text_statistics(GOOD_TEXT)
    bad = text_statistics("��|~ " * 30 + " MUSTER")
    assert good.score > bad.score
    assert good.meaningful_chars > 100
    assert text_statistics("").score == 0.0


def test_words_to_text_groups_lines_and_blocks():
    words = [
        w("Vertragsnummer", block=0, line=0),
        w("4711-0815", block=0, line=0),
        w("Zeile", block=0, line=1),
    ]
    words += [w("Block", block=1, line=0), w("zwei", block=1, line=0)]
    assert words_to_text(words) == "Vertragsnummer 4711-0815\nZeile\n\nBlock zwei"
    assert words_to_text([]) == ""


def test_weighted_mean_confidence():
    assert weighted_mean_confidence([w("ab", 1.0), w("abcdef", 0.5)]) == pytest.approx(
        (2 * 1.0 + 6 * 0.5) / 8
    )
    assert weighted_mean_confidence([w("ab")]) is None
    assert weighted_mean_confidence([]) is None


def test_scale_bbox_clamps_to_page():
    box = scale_bbox(100, 50, 300, 150, 0.24, 0.24, 50.0, 30.0)
    assert box.x0 == pytest.approx(24.0)
    assert box.y0 == pytest.approx(12.0)
    assert box.x1 == 50.0  # begrenzt
    assert box.y1 == 30.0
    swapped = scale_bbox(300, 150, 100, 50, 1, 1, 1000, 1000)
    assert swapped.x0 < swapped.x1 and swapped.y0 < swapped.y1


def test_backends_follow_protocol():
    for backend in (TesseractBackend(), RapidOcrBackend(), DoclingBackend()):
        assert isinstance(backend, OcrBackend)
        assert isinstance(backend.name, str)


def test_ocr_unavailable_error_is_runtime_error():
    assert issubclass(OcrUnavailableError, RuntimeError)
