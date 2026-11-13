"""Optionale Backends (RapidOCR, Docling): sauberer Import, "nicht verfügbar"-Pfad, Abbildungslogik."""

from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from insurance_app.core.models import ExtractionMethod
from insurance_app.ocr import docling_backend, rapidocr_backend
from insurance_app.ocr.base import OcrUnavailableError
from insurance_app.ocr.docling_backend import DoclingBackend, docling_document_to_pages, offline_environment
from insurance_app.ocr.rapidocr_backend import RapidOcrBackend, detections_to_words

RAPID_INSTALLED = importlib.util.find_spec("rapidocr_onnxruntime") is not None
DOCLING_INSTALLED = importlib.util.find_spec("docling") is not None


def test_modules_import_cleanly():
    importlib.reload(rapidocr_backend)
    importlib.reload(docling_backend)
    assert RapidOcrBackend.method == ExtractionMethod.RAPID_OCR
    assert DoclingBackend.method == ExtractionMethod.DOCLING_OCR


@pytest.mark.skipif(RAPID_INSTALLED, reason="RapidOCR ist installiert")
def test_rapidocr_unavailable():
    backend = RapidOcrBackend()
    ok, reason = backend.is_available()
    assert not ok
    assert reason == "RapidOCR ist nicht installiert (optional: pip install rapidocr_onnxruntime)."
    assert backend.version() is None
    with pytest.raises(OcrUnavailableError):
        backend.recognize(Image.new("RGB", (10, 10), "white"), 1, 10.0, 10.0, 72.0)


@pytest.mark.skipif(DOCLING_INSTALLED, reason="Docling ist installiert")
def test_docling_unavailable(tmp_path):
    backend = DoclingBackend()
    ok, reason = backend.is_available()
    assert not ok
    assert reason == "Docling ist nicht installiert (optional: pip install docling)."
    assert backend.version() is None
    with pytest.raises(OcrUnavailableError):
        backend.recognize(Image.new("RGB", (10, 10), "white"), 1, 10.0, 10.0, 72.0)
    pdf = tmp_path / "a.pdf"
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    with pytest.raises(OcrUnavailableError):
        backend.convert_document(pdf, "D-1")


def test_rapidocr_detection_mapping():
    detections = [
        [[[200, 100], [600, 100], [600, 140], [200, 140]], "4711-0815", 0.9],
        [[[100, 20], [500, 20], [500, 60], [100, 60]], "MUSTER Lebensversicherung", 0.8],
        ["kaputt"],
        [[[0, 0], [1, 0], [1, 1], [0, 1]], "   ", 0.5],
    ]
    words = detections_to_words(detections, 0.5, 0.5, 1000.0, 1000.0)
    assert [w.text for w in words] == ["MUSTER", "Lebensversicherung", "4711-0815"]
    first, second, number = words
    assert first.line_no == second.line_no == 0 and number.line_no == 1
    assert first.bbox.x0 == pytest.approx(50.0)
    assert second.bbox.x1 == pytest.approx(250.0)
    assert first.bbox.x1 < second.bbox.x0
    assert number.confidence == pytest.approx(0.9)
    assert number.bbox.y0 == pytest.approx(50.0)


def test_docling_document_mapping():
    def bbox(left, top, right, bottom):
        return SimpleNamespace(l=left, t=top, r=right, b=bottom)

    doc = SimpleNamespace(
        pages={1: SimpleNamespace(size=SimpleNamespace(width=200.0, height=100.0))},
        texts=[
            SimpleNamespace(
                text="Vertragsnummer 4711-0815", prov=[SimpleNamespace(page_no=1, bbox=bbox(10, 20, 110, 30))]
            ),
            SimpleNamespace(text="MUSTER", prov=[SimpleNamespace(page_no=1, bbox=bbox(10, 5, 50, 15))]),
        ],
        tables=[],
    )
    pages = docling_document_to_pages(doc, {1: (400.0, 200.0)}, {1: 0.8})
    words = pages[1]
    assert [w.text for w in words] == ["MUSTER", "Vertragsnummer", "4711-0815"]
    number = words[2]
    assert number.bbox.x1 == pytest.approx(220.0)  # 110 * 2
    assert number.bbox.y0 == pytest.approx(40.0)
    assert number.confidence == pytest.approx(0.8)


def test_offline_environment_sets_and_restores(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    with offline_environment():
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert "HF_HUB_OFFLINE" not in os.environ
    assert os.environ["TRANSFORMERS_OFFLINE"] == "0"


def test_docling_missing_artifacts_path(tmp_path, monkeypatch):
    backend = DoclingBackend(artifacts_path=tmp_path / "fehlt")
    monkeypatch.setattr(docling_backend.importlib.util, "find_spec", lambda name: object())
    ok, reason = backend.is_available()
    assert not ok
    assert "Docling-Modelle" in reason


# ---------------------------------------------------------------------------
# PRIV-10: Zwischendateien von Docling nur im privaten Temp-Ordner der App
# ---------------------------------------------------------------------------


def _fake_docling(monkeypatch):
    """Minimales Ersatzmodul ``docling.datamodel.base_models`` (Docling selbst ist optional)."""
    import sys
    import types

    pkg = types.ModuleType("docling")
    datamodel = types.ModuleType("docling.datamodel")
    base_models = types.ModuleType("docling.datamodel.base_models")
    base_models.DocumentStream = lambda name, stream: SimpleNamespace(name=name, stream=stream)
    monkeypatch.setitem(sys.modules, "docling", pkg)
    monkeypatch.setitem(sys.modules, "docling.datamodel", datamodel)
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", base_models)


class _TempWritingConverter:
    """Verhält sich wie Doclings Tesseract-CLI-Anbindung: Seitenbild als NamedTemporaryFile."""

    def __init__(self, crash: bool = False):
        self.crash = crash
        self.seen: list[str] = []

    def convert(self, source):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(b"MUSTER")
        self.seen.append(fh.name)
        if self.crash:
            raise RuntimeError("Absturz simuliert")
        return SimpleNamespace(document=SimpleNamespace(pages={}, texts=[], tables=[]))


def test_priv_10_docling_temp_files_stay_in_private_app_temp_dir(tmp_path, monkeypatch):
    import tempfile

    _fake_docling(monkeypatch)
    base = tmp_path / "user_data" / "tmp"
    backend = DoclingBackend(temp_base=base)
    monkeypatch.setattr(backend, "is_available", lambda: (True, "Docling installiert."))
    converter = _TempWritingConverter()
    backend._converter = converter
    before = tempfile.tempdir
    backend._convert("seite.png", b"x")
    assert tempfile.tempdir == before  # wiederhergestellt
    assert len(converter.seen) == 1
    written = converter.seen[0]
    assert os.path.dirname(os.path.dirname(written)) == str(base)
    assert os.path.basename(os.path.dirname(written)).startswith("docling-")
    assert not os.path.exists(os.path.dirname(written))  # Arbeitsordner wieder entfernt
    # auch bei einem Fehler wird aufgeräumt und tempdir wiederhergestellt
    backend._converter = _TempWritingConverter(crash=True)
    with pytest.raises(docling_backend.OcrError):
        backend._convert("seite.png", b"x")
    assert tempfile.tempdir == before
    assert list(base.iterdir()) == []


def test_priv_10_without_temp_base_nothing_changes(tmp_path, monkeypatch):
    import tempfile

    _fake_docling(monkeypatch)
    backend = DoclingBackend()
    monkeypatch.setattr(backend, "is_available", lambda: (True, "Docling installiert."))
    backend._converter = SimpleNamespace(
        convert=lambda source: SimpleNamespace(tempdir=tempfile.tempdir, document=None)
    )
    before = tempfile.tempdir
    assert backend._convert("seite.png", b"x").tempdir == before
