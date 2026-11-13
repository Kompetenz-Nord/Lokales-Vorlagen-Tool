"""Robustheit gegen echte Texterkennung (Tesseract direkt, ohne insurance_app.ocr).

Langsam (je Bild ca. 1 s) – daher als ``slow`` markiert.
"""

import io
import json
import shutil

import pymupdf
import pytest
from extraction_helpers import FIXTURES, info_for, ocr_page_text_from_image
from PIL import Image

from insurance_app.core.models import DocumentText
from insurance_app.extraction.extractor import extract_candidates
from insurance_app.samples.generator import FILE_POLICY_SCAN, FILE_POLICY_SCAN_LOWRES, FILE_PORTAL

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract nicht installiert"),
]


def _truth(name: str) -> dict[str, str]:
    return json.loads((FIXTURES / f"{name}.truth.json").read_text(encoding="utf-8"))


def _extract(name: str, image: Image.Image, dpi: float):
    pytest.importorskip("pytesseract")
    page = ocr_page_text_from_image(image, dpi)
    return extract_candidates(DocumentText(document_id="D-OCR", pages=[page]), info_for(None, name, "D-OCR"))


def _best(result, key):
    best = result.best(key)
    return best.normalized if best else None


def test_rotated_scan_150dpi():
    with pymupdf.open(str(FIXTURES / FILE_POLICY_SCAN)) as doc:
        xref = doc[0].get_images()[0][0]
        image = Image.open(io.BytesIO(doc.extract_image(xref)["image"]))
        image.load()
    res = _extract(FILE_POLICY_SCAN, image, 150)
    truth = _truth(FILE_POLICY_SCAN)
    robust = [
        "contract.number",
        "contract.start_date",
        "contract.end_date",
        "contract.pension_start_date",
        "premium.amount",
        "premium.frequency",
        "policyholder.name",
        "insured.last_name",
        "insured.first_name",
        "insured.birth_date",
        "insured.street",
        "insured.postal_code",
    ]
    for key in robust:
        assert _best(res, key) == truth[key], key
    assert "contract.end_date" in res.candidates and _best(res, "contract.end_date") != "2052-12-01"


def test_noisy_lowres_image():
    with Image.open(FIXTURES / FILE_POLICY_SCAN_LOWRES) as image:
        image.load()
        res = _extract(FILE_POLICY_SCAN_LOWRES, image, 100)
    truth = _truth(FILE_POLICY_SCAN_LOWRES)
    for key in ("contract.start_date", "contract.end_date", "premium.frequency", "insured.birth_date"):
        assert _best(res, key) == truth[key], key
    # Nichts Falsches mit hoher Sicherheit: abweichende Werte nur mit geringer Konfidenz
    for key, cands in res.candidates.items():
        if key in truth and cands[0].normalized != truth[key]:
            assert (cands[0].confidence or 0) < 0.8, key


def test_portal_screenshot_ocr():
    with Image.open(FIXTURES / FILE_PORTAL) as image:
        big = image.convert("L").resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    res = _extract(FILE_PORTAL, big, 192)
    truth = _truth(FILE_PORTAL)
    for key, value in truth.items():
        assert _best(res, key) == value, key
