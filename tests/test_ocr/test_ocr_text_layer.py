"""Native Textebene (ocr.text_layer)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
import pytest
from ocr_samples import (
    NATIVE_LINES,
    add_embedded_ocr_page,
    add_image_page,
    add_native_page,
    save,
)

from insurance_app.core.models import ExtractionMethod, PageQuality, TextLayerOrigin
from insurance_app.core.settings import Settings
from insurance_app.documents.pages import (
    DocumentAccessError,
    PasswordRequiredError,
    WrongPasswordError,
    load_page_image,
)
from insurance_app.ocr.text_layer import EMBEDDED_OCR_WARNING, extract_text_layer


def native_pdf(tmp_path: Path, rotation: int = 0) -> Path:
    doc = pymupdf.open()
    add_native_page(doc, rotation=rotation)
    return save(doc, tmp_path / "nativ.pdf")


def test_native_words_and_bbox(tmp_path: Path):
    text = extract_text_layer(native_pdf(tmp_path), "D-MUSTER")
    assert text.document_id == "D-MUSTER"
    page = text.pages[0]
    assert page.method == ExtractionMethod.PDF_TEXT
    assert page.origin == TextLayerOrigin.NATIVE
    assert page.quality == PageQuality.GOOD
    assert page.mean_confidence is None
    assert page.engine_version and page.engine_version.startswith("PyMuPDF")
    assert page.width == pytest.approx(595.276, abs=0.01)
    assert "Versicherungsnummer: 4711-0815" in page.text
    assert "Rückkaufswert" in page.text  # Umlaute erhalten

    number = next(w for w in page.words if w.text == "4711-0815")
    label = next(w for w in page.words if w.text == "Versicherungsnummer:")
    baseline = 100 + 22 * 3
    assert label.bbox.x0 == pytest.approx(72, abs=0.6)
    assert label.bbox.y0 < baseline < label.bbox.y1
    assert number.bbox.x0 > label.bbox.x1
    assert (number.block_no, number.line_no) == (label.block_no, label.line_no)
    assert number.confidence is None


def test_text_order_top_to_bottom(tmp_path: Path):
    page = extract_text_layer(native_pdf(tmp_path), "D-1").pages[0]
    positions = [page.text.index(line) for line in NATIVE_LINES]
    assert positions == sorted(positions)


def test_rotated_page_words_in_displayed_coordinates(tmp_path: Path):
    path = native_pdf(tmp_path, rotation=90)
    page = extract_text_layer(path, "D-1").pages[0]
    assert page.rotation == 90
    assert page.width > page.height  # angezeigtes Querformat
    word = next(w for w in page.words if w.text == "Versicherungsnummer:")
    # Wortbox muss dort liegen, wo der gerenderte Text dunkle Pixel hat
    img = load_page_image(path, 1, dpi=72)
    scale = img.width / page.width
    box = word.bbox
    crop = np.asarray(
        img.convert("L").crop(
            (int(box.x0 * scale), int(box.y0 * scale), int(box.x1 * scale) + 1, int(box.y1 * scale) + 1)
        )
    )
    assert crop.size > 0
    assert crop.min() < 100
    # Bei 90° läuft die Schrift senkrecht: Box höher als breit
    assert box.height > box.width


def test_embedded_ocr_detection(tmp_path: Path):
    doc = pymupdf.open()
    add_embedded_ocr_page(doc, NATIVE_LINES)
    path = save(doc, tmp_path / "scan_mit_ocr.pdf")
    page = extract_text_layer(path, "D-1").pages[0]
    assert page.origin == TextLayerOrigin.EMBEDDED_OCR
    assert page.method == ExtractionMethod.PDF_TEXT
    assert EMBEDDED_OCR_WARNING in page.warnings
    assert page.warnings[0] == (
        "Textebene stammt vermutlich aus einer fremden Texterkennung – Werte besonders sorgfältig prüfen."
    )
    assert any(w.text == "4711-0815" for w in page.words)


def test_invisible_text_without_image_is_embedded_ocr(tmp_path: Path):
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(NATIVE_LINES):
        page.insert_text((72, 100 + 20 * i), line, fontsize=12, render_mode=3)
    path = save(doc, tmp_path / "unsichtbar.pdf")
    assert extract_text_layer(path, "D-1").pages[0].origin == TextLayerOrigin.EMBEDDED_OCR


def test_image_page_has_no_text(tmp_path: Path):
    doc = pymupdf.open()
    add_native_page(doc)
    add_image_page(doc)
    path = save(doc, tmp_path / "gemischt.pdf")
    pages = extract_text_layer(path, "D-1").pages
    assert [p.page_number for p in pages] == [1, 2]
    assert pages[1].origin == TextLayerOrigin.NONE
    assert pages[1].quality == PageQuality.EMPTY
    assert pages[1].words == []


def test_garbled_embedded_text_is_low(tmp_path: Path):
    doc = pymupdf.open()
    add_embedded_ocr_page(doc, ["xqzt|~ vbnmk ~~|| qwrtz |l1| ;;,, " * 3] * 6)
    path = save(doc, tmp_path / "schlecht.pdf")
    page = extract_text_layer(path, "D-1", settings=Settings()).pages[0]
    assert page.origin == TextLayerOrigin.EMBEDDED_OCR
    assert page.quality == PageQuality.LOW


def test_password_handling(tmp_path: Path):
    doc = pymupdf.open()
    add_native_page(doc)
    path = tmp_path / "zu.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="MUSTER-pw", owner_pw="MUSTER-owner")
    doc.close()
    with pytest.raises(PasswordRequiredError):
        extract_text_layer(path, "D-1")
    with pytest.raises(WrongPasswordError):
        extract_text_layer(path, "D-1", password="MUSTER-falsch")
    text = extract_text_layer(path, "D-1", password="MUSTER-pw")
    assert "4711-0815" in text.full_text


def test_non_pdf_raises(tmp_path: Path):
    path = tmp_path / "bild.png"
    from PIL import Image

    Image.new("RGB", (10, 10), "white").save(path)
    with pytest.raises(DocumentAccessError):
        extract_text_layer(path, "D-1")
