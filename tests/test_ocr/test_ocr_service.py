"""TextRecognitionService: Textebene zuerst, OCR nur wo nötig."""

from __future__ import annotations

import threading
from pathlib import Path

import pymupdf
import pytest
from ocr_samples import (
    A4,
    NATIVE_LINES,
    NUMBER_VALUE,
    FakeBackend,
    a4_text_image,
    add_embedded_ocr_page,
    add_image_page,
    add_native_page,
    save,
)
from PIL import Image

from insurance_app.core.models import ExtractionMethod, PageQuality, TextLayerOrigin
from insurance_app.core.settings import Settings
from insurance_app.documents.intake import check_document
from insurance_app.documents.pages import PasswordRequiredError
from insurance_app.ocr.base import OcrError, OcrUnavailableError
from insurance_app.ocr.service import (
    KEPT_LAYER_WARNING,
    NO_OCR_WARNING,
    OCR_FAILED_WARNING,
    USED_OCR_WARNING,
    RecognitionCancelled,
    RecognitionError,
    TextRecognitionService,
)
from insurance_app.ocr.tesseract_backend import TesseractBackend

TESSERACT_OK = TesseractBackend().is_available()[0]
needs_tesseract = pytest.mark.skipif(not TESSERACT_OK, reason="Tesseract mit deu+eng nicht installiert")

GARBLED = ["xqzt|~ vbnmk ~~|| qwrtz |l1| ;;,, " * 3] * 6


def mixed_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    add_native_page(doc)
    add_image_page(doc)
    return save(doc, tmp_path / "gemischt.pdf")


def service(
    backend: FakeBackend | TesseractBackend | None = None, **settings: object
) -> TextRecognitionService:
    s = Settings(**settings)
    backends = {"tesseract": backend} if backend is not None else None
    return TextRecognitionService(s, backends=backends)


def test_mixed_pdf_with_fake_backend(tmp_path: Path):
    path = mixed_pdf(tmp_path)
    fake = FakeBackend()
    events: list[tuple[int, int, str]] = []
    result = service(fake, ocr_dpi=100).recognize_document(
        path, "D-MUSTER", check_document(path), progress=lambda c, t, m: events.append((c, t, m))
    )
    assert result.document_id == "D-MUSTER"
    p1, p2 = result.pages
    assert p1.method == ExtractionMethod.PDF_TEXT and p1.origin == TextLayerOrigin.NATIVE
    assert p2.method == ExtractionMethod.TESSERACT and p2.origin == TextLayerOrigin.OCR
    assert [c["page"] for c in fake.calls] == [2]
    call = fake.calls[0]
    assert call["page_width"] == pytest.approx(A4[0], abs=0.01)
    assert call["dpi"] == pytest.approx(100, abs=0.5)
    assert call["size"][0] == pytest.approx(A4[0] * 100 / 72, abs=1)
    # Fortschritt: monoton, endet bei (2, 2)
    assert events[-1][:2] == (2, 2)
    currents = [e[0] for e in events]
    assert currents == sorted(currents)
    assert all(isinstance(e[2], str) and e[2] for e in events)


def test_no_backend_available_does_not_crash(tmp_path: Path):
    path = mixed_pdf(tmp_path)
    bogus = TesseractBackend(tesseract_cmd="/nicht/vorhanden/tesseract")
    svc = TextRecognitionService(Settings(), backends={"tesseract": bogus})
    assert svc.available_backends()["tesseract"][0] is False
    result = svc.recognize_document(path, "D-1", check_document(path))
    p1, p2 = result.pages
    assert p1.quality == PageQuality.GOOD
    assert p2.quality == PageQuality.EMPTY
    assert NO_OCR_WARNING in p2.warnings
    assert p2.warnings[0] == "Keine Texterkennung verfügbar – Seite bitte manuell prüfen."
    assert any("Tesseract ist nicht installiert" in w for w in p2.warnings)


def test_no_backend_for_image_returns_placeholder(tmp_path: Path):
    path = tmp_path / "foto.png"
    a4_text_image(100).save(path, dpi=(100, 100))
    svc = service(FakeBackend(available=False))
    result = svc.recognize_document(path, "D-1", check_document(path))
    page = result.pages[0]
    assert page.quality == PageQuality.EMPTY
    assert page.origin == TextLayerOrigin.NONE
    assert page.width == pytest.approx(A4[0], abs=1)
    assert NO_OCR_WARNING in page.warnings


def test_cancel_before_start(tmp_path: Path):
    path = mixed_pdf(tmp_path)
    cancel = threading.Event()
    cancel.set()
    fake = FakeBackend()
    with pytest.raises(RecognitionCancelled):
        service(fake).recognize_document(path, "D-1", check_document(path), cancel=cancel)
    assert fake.calls == []


def test_cancel_during_run(tmp_path: Path):
    doc = pymupdf.open()
    for _ in range(3):
        add_image_page(doc, Image.new("RGB", (100, 140), "white"))
    path = save(doc, tmp_path / "drei.pdf")
    cancel = threading.Event()
    fake = FakeBackend()

    def progress(current: int, total: int, message: str) -> None:
        if current >= 1:
            cancel.set()

    with pytest.raises(RecognitionCancelled):
        service(fake, ocr_dpi=50).recognize_document(
            path, "D-1", check_document(path), progress=progress, cancel=cancel
        )
    assert len(fake.calls) == 1


def test_png_input_uses_ocr(tmp_path: Path):
    path = tmp_path / "screenshot.png"
    a4_text_image(100).save(path, dpi=(100, 100))
    fake = FakeBackend()
    result = service(fake).recognize_document(path, "D-1", check_document(path))
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.method == ExtractionMethod.TESSERACT
    assert fake.calls[0]["dpi"] == pytest.approx(100)
    assert fake.calls[0]["page_width"] == pytest.approx(A4[0], abs=1)


def test_multipage_tiff_all_pages_ocr(tmp_path: Path):
    path = tmp_path / "scan.tif"
    frames = [Image.new("RGB", (200, 280), "white") for _ in range(3)]
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])
    fake = FakeBackend()
    result = service(fake).recognize_document(path, "D-1", check_document(path))
    assert [p.page_number for p in result.pages] == [1, 2, 3]
    assert [c["page"] for c in fake.calls] == [1, 2, 3]


def test_low_embedded_ocr_replaced_when_better(tmp_path: Path):
    doc = pymupdf.open()
    add_embedded_ocr_page(doc, GARBLED)
    path = save(doc, tmp_path / "schlechter_scan.pdf")
    fake = FakeBackend()
    page = service(fake, ocr_dpi=72).recognize_document(path, "D-1", check_document(path)).pages[0]
    assert page.method == ExtractionMethod.TESSERACT
    assert USED_OCR_WARNING in page.warnings


def test_low_embedded_ocr_kept_when_ocr_not_better(tmp_path: Path):
    doc = pymupdf.open()
    add_embedded_ocr_page(doc, GARBLED)
    path = save(doc, tmp_path / "schlechter_scan.pdf")
    fake = FakeBackend(text="", quality=PageQuality.EMPTY)
    page = service(fake, ocr_dpi=72).recognize_document(path, "D-1", check_document(path)).pages[0]
    assert page.method == ExtractionMethod.PDF_TEXT
    assert page.origin == TextLayerOrigin.EMBEDDED_OCR
    assert KEPT_LAYER_WARNING in page.warnings


def test_good_embedded_ocr_is_not_reprocessed(tmp_path: Path):
    doc = pymupdf.open()
    add_embedded_ocr_page(doc, NATIVE_LINES)
    path = save(doc, tmp_path / "guter_scan.pdf")
    fake = FakeBackend()
    page = service(fake).recognize_document(path, "D-1", check_document(path)).pages[0]
    assert page.origin == TextLayerOrigin.EMBEDDED_OCR
    assert fake.calls == []


def test_force_ocr_pages(tmp_path: Path):
    doc = pymupdf.open()
    add_native_page(doc)
    add_native_page(doc)
    path = save(doc, tmp_path / "nativ.pdf")
    fake = FakeBackend()
    result = service(fake, ocr_dpi=72).recognize_document(
        path, "D-1", check_document(path), force_ocr_pages={2}
    )
    assert result.pages[0].method == ExtractionMethod.PDF_TEXT
    assert result.pages[1].method == ExtractionMethod.TESSERACT
    assert [c["page"] for c in fake.calls] == [2]


def test_backend_error_keeps_layer_with_warning(tmp_path: Path):
    path = mixed_pdf(tmp_path)
    fake = FakeBackend(error=OcrError("Die Texterkennung ist fehlgeschlagen."))
    result = service(fake, ocr_dpi=72).recognize_document(path, "D-1", check_document(path))
    assert result.pages[1].quality == PageQuality.EMPTY
    assert OCR_FAILED_WARNING in result.pages[1].warnings


def test_backend_unavailable_during_run(tmp_path: Path):
    path = mixed_pdf(tmp_path)
    fake = FakeBackend(error=OcrUnavailableError("Fake-Backend fehlt."))
    page = service(fake, ocr_dpi=72).recognize_document(path, "D-1", check_document(path)).pages[1]
    assert page.warnings[:2] == [NO_OCR_WARNING, "Fake-Backend fehlt."]


def test_unexpected_backend_exception_is_contained(tmp_path: Path):
    path = mixed_pdf(tmp_path)
    fake = FakeBackend(error=ZeroDivisionError())
    page = service(fake, ocr_dpi=72).recognize_document(path, "D-1", check_document(path)).pages[1]
    assert OCR_FAILED_WARNING in page.warnings


def test_rotation_is_combined(tmp_path: Path):
    doc = pymupdf.open()
    add_image_page(doc, Image.new("RGB", (100, 140), "white"), rotation=90)
    path = save(doc, tmp_path / "quer.pdf")
    fake = FakeBackend()
    page = service(fake, ocr_dpi=72).recognize_document(path, "D-1", check_document(path)).pages[0]
    assert page.rotation == 90
    assert fake.calls[0]["page_width"] > fake.calls[0]["page_height"]


def test_backend_selection_prefers_setting_then_tesseract():
    tess = FakeBackend(name="tesseract")
    rapid = FakeBackend(name="rapidocr")
    svc = TextRecognitionService(
        Settings(ocr_backend="rapidocr"), backends={"tesseract": tess, "rapidocr": rapid}
    )
    assert svc.select_backend()[0] is rapid
    rapid.available = False
    assert svc.select_backend()[0] is tess
    tess.available = False
    backend, reason = svc.select_backend()
    assert backend is None and reason


def test_default_backends_built_from_settings():
    svc = TextRecognitionService(Settings(tesseract_cmd="/nicht/vorhanden/tesseract"))
    assert set(svc.backends) == {"tesseract", "rapidocr", "docling"}
    status = svc.available_backends()
    assert status["tesseract"][0] is False
    assert status["rapidocr"][1]
    assert status["docling"][1]


def test_failed_check_is_rejected(tmp_path: Path):
    path = tmp_path / "leer.pdf"
    path.write_bytes(b"")
    with pytest.raises(RecognitionError):
        service(FakeBackend()).recognize_document(path, "D-1", check_document(path))


def test_changed_file_is_rejected(tmp_path: Path):
    path = mixed_pdf(tmp_path)
    check = check_document(path)
    doc = pymupdf.open()
    add_native_page(doc)
    save(doc, path)  # Datei nach der Prüfung ersetzt
    with pytest.raises(RecognitionError):
        service(FakeBackend()).recognize_document(path, "D-1", check)


def test_password_protected_pdf(tmp_path: Path):
    doc = pymupdf.open()
    add_native_page(doc)
    add_image_page(doc)
    path = tmp_path / "zu.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="MUSTER-pw", owner_pw="MUSTER-o")
    doc.close()
    check = check_document(path, password="MUSTER-pw")
    fake = FakeBackend()
    with pytest.raises(PasswordRequiredError):
        service(fake).recognize_document(path, "D-1", check)
    result = service(fake, ocr_dpi=72).recognize_document(path, "D-1", check, password="MUSTER-pw")
    assert result.pages[0].method == ExtractionMethod.PDF_TEXT
    assert result.pages[1].method == ExtractionMethod.TESSERACT


def test_no_temp_files_written(tmp_path: Path, monkeypatch):
    import tempfile

    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    path = mixed_pdf(tmp_path)
    service(FakeBackend(), ocr_dpi=72).recognize_document(path, "D-1", check_document(path))
    assert list(temp_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Mit echtem Tesseract (langsam)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@needs_tesseract
def test_mixed_pdf_with_tesseract(tmp_path: Path):
    doc = pymupdf.open()
    add_native_page(doc)
    add_image_page(doc, a4_text_image(200))
    path = save(doc, tmp_path / "gemischt.pdf")
    svc = TextRecognitionService(Settings(ocr_dpi=200))
    result = svc.recognize_document(path, "D-1", check_document(path))
    p1, p2 = result.pages
    assert p1.method == ExtractionMethod.PDF_TEXT
    assert p2.method == ExtractionMethod.TESSERACT
    assert p2.origin == TextLayerOrigin.OCR
    assert p2.quality == PageQuality.GOOD
    assert NUMBER_VALUE in [w.text for w in p2.words]
    assert p2.engine_version and p2.engine_version.startswith("tesseract")
    assert p2.source_dpi == pytest.approx(200, abs=1)


@pytest.mark.slow
@needs_tesseract
def test_png_with_tesseract(tmp_path: Path, monkeypatch):
    import tempfile

    temp_dir = tmp_path / "tmp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    path = tmp_path / "screenshot.png"
    a4_text_image(200).save(path, dpi=(200, 200))
    result = TextRecognitionService(Settings()).recognize_document(path, "D-1", check_document(path))
    page = result.pages[0]
    assert page.method == ExtractionMethod.TESSERACT
    assert NUMBER_VALUE in [w.text for w in page.words]
    assert page.width == pytest.approx(A4[0], abs=1)
    assert list(temp_dir.iterdir()) == []  # keine Bilddateien im Temp-Ordner
