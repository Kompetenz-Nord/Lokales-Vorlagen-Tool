"""Tesseract-Backend (echte Texterkennung, langsam)."""

from __future__ import annotations

import pytest
from ocr_samples import (
    A4,
    NUMBER_LABEL,
    NUMBER_VALUE,
    NUMBER_VALUE_POS,
    a4_text_image,
    noisy_low_res_image,
)
from PIL import Image

from insurance_app.core.models import ExtractionMethod, PageQuality, TextLayerOrigin
from insurance_app.core.settings import Settings
from insurance_app.ocr.base import OcrUnavailableError
from insurance_app.ocr.page_quality import LOW_QUALITY_WARNING
from insurance_app.ocr.tesseract_backend import TesseractBackend

TESSERACT_OK = TesseractBackend().is_available()[0]
needs_tesseract = pytest.mark.skipif(not TESSERACT_OK, reason="Tesseract mit deu+eng nicht installiert")


@pytest.fixture(scope="module")
def backend() -> TesseractBackend:
    return TesseractBackend(languages="deu+eng")


@pytest.fixture(scope="module")
def page_image() -> Image.Image:
    return a4_text_image(300)


def test_bogus_command_is_unavailable():
    backend = TesseractBackend(tesseract_cmd="/nicht/vorhanden/tesseract")
    ok, reason = backend.is_available()
    assert not ok
    assert reason.startswith("Tesseract ist nicht installiert")
    assert backend.version() is None
    with pytest.raises(OcrUnavailableError):
        backend.recognize(Image.new("RGB", (10, 10), "white"), 1, 10.0, 10.0, 72.0)


@needs_tesseract
def test_missing_language_reason():
    ok, reason = TesseractBackend(languages="deu+zzz").is_available()
    assert not ok
    assert reason.startswith("Sprachpaket 'zzz' fehlt")


def test_invalid_arguments():
    with pytest.raises(ValueError):
        TesseractBackend(languages="deu; rm -rf")
    with pytest.raises(ValueError):
        TesseractBackend(psm=99)


@needs_tesseract
def test_available_and_version(backend: TesseractBackend):
    ok, reason = backend.is_available()
    assert ok
    assert "bereit" in reason
    assert backend.version()
    assert backend.engine_version.startswith("tesseract ")
    assert backend.engine_version.endswith("(deu+eng)")
    assert {"deu", "eng"} <= set(backend.available_languages())


@pytest.mark.slow
@needs_tesseract
def test_recognize_german_page(backend: TesseractBackend, page_image: Image.Image):
    page = backend.recognize(page_image, 1, A4[0], A4[1], 300.0)
    assert page.method == ExtractionMethod.TESSERACT
    assert page.origin == TextLayerOrigin.OCR
    assert page.quality == PageQuality.GOOD
    assert page.source_dpi == 300.0
    assert page.rotation == 0
    assert page.mean_confidence is not None and page.mean_confidence > 0.8
    assert page.engine_version == backend.engine_version
    texts = [w.text for w in page.words]
    assert NUMBER_LABEL in texts
    assert NUMBER_VALUE in texts
    assert "Jährliche" in page.text or "Jahrliche" in page.text
    assert all(w.confidence is not None and 0.0 <= w.confidence <= 1.0 for w in page.words)

    # Box in Seitenpunkten: gezeichnet bei (1300 px, 1000 px) mit 300 dpi -> (312 pt, 240 pt)
    number = next(w for w in page.words if w.text == NUMBER_VALUE)
    assert number.bbox.x0 == pytest.approx(NUMBER_VALUE_POS[0] * 72 / 300, abs=4)
    assert number.bbox.y0 == pytest.approx(NUMBER_VALUE_POS[1] * 72 / 300, abs=6)
    assert number.bbox.x1 <= A4[0] and number.bbox.y1 <= A4[1]
    label = next(w for w in page.words if w.text == NUMBER_LABEL)
    assert label.bbox.x1 < number.bbox.x0


@pytest.mark.slow
@needs_tesseract
def test_rotated_180_is_recovered_by_osd(backend: TesseractBackend, page_image: Image.Image):
    small = page_image.resize((page_image.width // 2, page_image.height // 2))
    upside_down = small.rotate(180)
    page = backend.recognize(upside_down, 1, A4[0], A4[1], 150.0)
    assert page.rotation == 180
    assert NUMBER_VALUE in [w.text for w in page.words]
    assert any("gedreht (180°)" in w for w in page.warnings)
    number = next(w for w in page.words if w.text == NUMBER_VALUE)
    # Koordinaten beziehen sich auf die aufgerichtete Seite
    assert number.bbox.x0 == pytest.approx(NUMBER_VALUE_POS[0] * 72 / 300, abs=6)


@pytest.mark.slow
@needs_tesseract
def test_low_res_noisy_image_is_low_quality():
    img = noisy_low_res_image()
    backend = TesseractBackend(settings=Settings())
    page = backend.recognize(img, 1, img.width * 1.0, img.height * 1.0, 72.0)
    assert page.quality == PageQuality.LOW
    assert LOW_QUALITY_WARNING in page.warnings
    assert page.mean_confidence is not None and page.mean_confidence < 0.7


@pytest.mark.slow
@needs_tesseract
def test_skewed_page_boxes_in_page_coordinates(backend: TesseractBackend, page_image: Image.Image):
    small = page_image.resize((page_image.width // 2, page_image.height // 2))
    skewed = small.rotate(2.0, resample=Image.Resampling.BICUBIC, fillcolor="white")
    page = backend.recognize(skewed, 1, A4[0], A4[1], 150.0)
    assert NUMBER_VALUE in [w.text for w in page.words]
    number = next(w for w in page.words if w.text == NUMBER_VALUE)
    # Erwartete Lage im schiefen Bild (Drehung um die Bildmitte) in Punkten
    import math

    cx, cy = A4[0] / 2, A4[1] / 2
    x, y = NUMBER_VALUE_POS[0] * 72 / 300, NUMBER_VALUE_POS[1] * 72 / 300
    rad = math.radians(2.0)
    ex = cx + (x - cx) * math.cos(rad) + (y - cy) * math.sin(rad)
    ey = cy - (x - cx) * math.sin(rad) + (y - cy) * math.cos(rad)
    assert number.bbox.x0 == pytest.approx(ex, abs=8)
    assert number.bbox.y0 == pytest.approx(ey, abs=10)


@pytest.mark.slow
@needs_tesseract
def test_blank_image_is_empty(backend: TesseractBackend):
    page = backend.recognize(Image.new("RGB", (620, 877), "white"), 3, A4[0], A4[1], 75.0)
    assert page.page_number == 3
    assert page.quality == PageQuality.EMPTY
    assert page.words == []
    assert page.warnings
