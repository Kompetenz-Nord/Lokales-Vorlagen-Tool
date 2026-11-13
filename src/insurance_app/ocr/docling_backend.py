"""Optionales Docling-Backend (Dokumentkonvertierung mit lokaler OCR).

* Docling wird nur verwendet, wenn es installiert ist (Import erst bei Bedarf).
* **Offline-Zwang**: Während Import und Konvertierung sind
  ``HF_HUB_OFFLINE=1``, ``TRANSFORMERS_OFFLINE=1`` und
  ``HF_DATASETS_OFFLINE=1`` gesetzt; Docling darf zur Laufzeit keine Modelle
  herunterladen. Fehlen die Modelle lokal, gibt es eine klare deutsche
  Fehlermeldung (``OcrUnavailableError``).
* OCR innerhalb von Docling: Tesseract-Kommandozeile (lokal).
* Dateien werden als Bytes-Stream mit neutralem Namen übergeben – der
  Originaldateiname gelangt nicht in Docling.
* Docling liefert Textelemente (Absätze/Zellen) mit Box, keine Einzelwörter.
  Wortboxen werden anteilig nach Zeichenzahl über die Elementbreite verteilt
  (Näherung; bei mehrzeiligen Absätzen ungenau). Jedes Element ist eine
  "Zeile" (``block_no`` = Elementindex, ``line_no = 0``).
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from importlib import metadata
from io import BytesIO
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image

from ..core.models import DocumentText, ExtractionMethod, PageText, TextLayerOrigin, Word
from ..core.settings import Settings
from ..documents.filetypes import detect_file_type_from_bytes
from ..documents.pages import PasswordRequiredError, WrongPasswordError, read_document_bytes, to_rgb
from .base import OcrError, OcrUnavailableError, scale_bbox, weighted_mean_confidence, words_to_text
from .page_quality import assess_text_quality

log = logging.getLogger(__name__)

PACKAGE = "docling"
MSG_NOT_INSTALLED = "Docling ist nicht installiert (optional: pip install docling)."
MSG_MODELS_MISSING = (
    "Die Docling-Modelle sind auf diesem Gerät nicht vorhanden. Docling lädt aus Datenschutzgründen "
    "nichts automatisch herunter. Bitte die Modelle einmalig bewusst lokal einrichten "
    "(docling-tools models download) und den Ordner in den Einstellungen angeben."
)
MSG_FAILED = "Die Docling-Konvertierung ist fehlgeschlagen."

_OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}
_OFFLINE_LOCK = threading.Lock()
_MISSING_MODEL_ERRORS = frozenset(
    {"LocalEntryNotFoundError", "OfflineModeIsEnabled", "EntryNotFoundError", "RepositoryNotFoundError"}
)


@contextlib.contextmanager
def offline_environment() -> Iterator[None]:
    """Hugging-Face-Downloads für die Dauer des Blocks sperren (Umgebung wird danach wiederhergestellt)."""
    with _OFFLINE_LOCK:
        previous = {key: os.environ.get(key) for key in _OFFLINE_ENV}
        os.environ.update(_OFFLINE_ENV)
        constants = sys.modules.get("huggingface_hub.constants")
        previous_constant = getattr(constants, "HF_HUB_OFFLINE", None) if constants else None
        if constants is not None:
            constants.HF_HUB_OFFLINE = True
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if constants is not None and previous_constant is not None:
                constants.HF_HUB_OFFLINE = previous_constant


def _looks_like_missing_model(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if type(current).__name__ in _MISSING_MODEL_ERRORS or isinstance(current, FileNotFoundError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _item_box(prov: Any, page_height: float) -> tuple[float, float, float, float] | None:
    """Box eines Docling-Provenance-Eintrags als (l, t, r, b) mit Ursprung oben links."""
    bbox = getattr(prov, "bbox", None)
    if bbox is None:
        return None
    to_top_left = getattr(bbox, "to_top_left_origin", None)
    if callable(to_top_left):
        bbox = to_top_left(page_height=page_height)
    try:
        left, top, right, bottom = float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b)
    except (AttributeError, TypeError, ValueError):
        return None
    if bottom < top:  # falls noch Ursprung unten links
        top, bottom = page_height - top, page_height - bottom
    return left, min(top, bottom), right, max(top, bottom)


def _split_words(
    text: str,
    box: tuple[float, float, float, float],
    block_no: int,
    scale_x: float,
    scale_y: float,
    page_width: float,
    page_height: float,
    confidence: float | None,
) -> list[Word]:
    left, top, right, bottom = box
    total = len(text)
    words: list[Word] = []
    position = 0
    for token in text.split():
        start = text.index(token, position)
        end = start + len(token)
        position = end
        x0 = left + (right - left) * start / total
        x1 = left + (right - left) * end / total
        words.append(
            Word(
                text=token,
                bbox=scale_bbox(x0, top, x1, bottom, scale_x, scale_y, page_width, page_height),
                confidence=confidence,
                block_no=block_no,
                line_no=0,
            )
        )
    return words


def docling_document_to_pages(
    doc: Any,
    page_sizes: dict[int, tuple[float, float]] | None = None,
    page_confidence: dict[int, float] | None = None,
) -> dict[int, list[Word]]:
    """Textelemente eines ``DoclingDocument`` je Seite (1-basiert) in Wörter umwandeln.

    ``page_sizes`` gibt die Zielgröße je Seite in Punkten an (sonst Docling-Größe).
    """
    result: dict[int, list[Word]] = {}
    docling_sizes: dict[int, tuple[float, float]] = {}
    for page_no, page in (getattr(doc, "pages", None) or {}).items():
        size = getattr(page, "size", None)
        if size is not None:
            docling_sizes[int(page_no)] = (float(size.width), float(size.height))
        result.setdefault(int(page_no), [])

    entries: list[tuple[str, Any]] = []
    for item in getattr(doc, "texts", None) or []:
        entries.append((str(getattr(item, "text", "") or ""), getattr(item, "prov", None) or []))
    for table in getattr(doc, "tables", None) or []:
        provs = getattr(table, "prov", None) or []
        table_page = getattr(provs[0], "page_no", None) if provs else None
        data = getattr(table, "data", None)
        for cell in getattr(data, "table_cells", None) or []:
            cell_box = getattr(cell, "bbox", None)
            if cell_box is not None and table_page is not None:
                fake_prov = type("_Prov", (), {"bbox": cell_box, "page_no": table_page})()
                entries.append((str(getattr(cell, "text", "") or ""), [fake_prov]))

    block_counter: dict[int, int] = {}
    for text, provs in entries:
        text = text.strip()
        if not text:
            continue
        for prov in provs[:1]:
            page_no = int(getattr(prov, "page_no", 1) or 1)
            d_width, d_height = docling_sizes.get(page_no, (0.0, 0.0))
            box = _item_box(prov, d_height)
            if box is None or d_width <= 0 or d_height <= 0:
                continue
            t_width, t_height = (page_sizes or {}).get(page_no, (d_width, d_height))
            block_no = block_counter.get(page_no, 0)
            block_counter[page_no] = block_no + 1
            confidence = (page_confidence or {}).get(page_no)
            result.setdefault(page_no, []).extend(
                _split_words(
                    text, box, block_no, t_width / d_width, t_height / d_height, t_width, t_height, confidence
                )
            )
    for words in result.values():
        words.sort(key=lambda w: (w.bbox.y0, w.bbox.x0))
    return result


class DoclingBackend:
    """Docling (optional). Seitenweise (``recognize``) oder als ganzes Dokument (``convert_document``)."""

    name = "docling"
    method = ExtractionMethod.DOCLING_OCR

    def __init__(
        self,
        *,
        languages: str = "deu+eng",
        tesseract_cmd: str | None = None,
        artifacts_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.languages = languages
        self.tesseract_cmd = tesseract_cmd
        env_path = os.environ.get("DOCLING_ARTIFACTS_PATH")
        self.artifacts_path = artifacts_path or (Path(env_path) if env_path else None)
        self.settings = settings or Settings()
        self._converter: Any | None = None
        self._lock = threading.Lock()

    # -- Verfügbarkeit ----------------------------------------------------
    def is_available(self) -> tuple[bool, str]:
        try:
            installed = importlib.util.find_spec(PACKAGE) is not None
        except (ImportError, ValueError):
            installed = False
        if not installed:
            return False, MSG_NOT_INSTALLED
        if self.artifacts_path is not None and not self.artifacts_path.is_dir():
            return False, MSG_MODELS_MISSING
        version = self.version()
        return True, (f"Docling {version} installiert." if version else "Docling installiert.")

    def version(self) -> str | None:
        try:
            return metadata.version(PACKAGE)
        except metadata.PackageNotFoundError:
            return None

    # -- Konvertierung ----------------------------------------------------
    def _build_converter(self) -> Any:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TesseractCliOcrOptions,
        )
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )

        ocr_kwargs: dict[str, Any] = {"lang": self.languages.split("+")}
        if self.tesseract_cmd:
            ocr_kwargs["tesseract_cmd"] = self.tesseract_cmd
        pipeline_kwargs: dict[str, Any] = {
            "do_ocr": True,
            "ocr_options": TesseractCliOcrOptions(**ocr_kwargs),
        }
        if self.artifacts_path is not None:
            pipeline_kwargs["artifacts_path"] = str(self.artifacts_path)
        pipeline = PdfPipelineOptions(**pipeline_kwargs)
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline),
            },
        )

    def _convert(self, name: str, data: bytes) -> Any:
        ok, reason = self.is_available()
        if not ok:
            raise OcrUnavailableError(reason)
        with offline_environment():
            try:
                with self._lock:
                    if self._converter is None:
                        self._converter = self._build_converter()
                    converter = self._converter
                from docling.datamodel.base_models import DocumentStream

                return converter.convert(DocumentStream(name=name, stream=BytesIO(data)))
            except ImportError as exc:
                raise OcrUnavailableError(MSG_NOT_INSTALLED) from exc
            except Exception as exc:  # Fehler der Fremdbibliothek kapseln
                log.warning("Docling fehlgeschlagen (%s)", type(exc).__name__)
                if _looks_like_missing_model(exc):
                    raise OcrUnavailableError(MSG_MODELS_MISSING) from exc
                raise OcrError(MSG_FAILED) from exc

    @staticmethod
    def _page_confidence(result: Any) -> dict[int, float]:
        scores: dict[int, float] = {}
        pages = getattr(getattr(result, "confidence", None), "pages", None) or {}
        for page_no, report in pages.items():
            value = getattr(report, "ocr_score", None)
            try:
                if value is not None and value == value:  # NaN ausschließen
                    scores[int(page_no)] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue
        return scores

    def _page_text(
        self, page_number: int, width: float, height: float, words: list[Word], dpi: float | None
    ) -> PageText:
        text = words_to_text(words)
        mean = weighted_mean_confidence(words)
        quality, warnings = assess_text_quality(text, words, TextLayerOrigin.OCR, mean, self.settings)
        return PageText(
            page_number=page_number,
            width=width,
            height=height,
            method=ExtractionMethod.DOCLING_OCR,
            origin=TextLayerOrigin.OCR,
            words=words,
            text=text,
            mean_confidence=mean,
            quality=quality,
            warnings=warnings,
            engine_version=f"docling {self.version() or '?'}",
            source_dpi=dpi,
            rotation=0,
        )

    def recognize(
        self,
        image: Image.Image,
        page_number: int,
        page_width: float,
        page_height: float,
        dpi: float,
    ) -> PageText:
        if image.width < 1 or image.height < 1 or page_width <= 0 or page_height <= 0:
            raise OcrError("Das Seitenbild ist leer.")
        buffer = BytesIO()
        to_rgb(image).save(buffer, format="PNG")
        started = time.perf_counter()
        result = self._convert("seite.png", buffer.getvalue())
        pages = docling_document_to_pages(
            result.document, {1: (page_width, page_height)}, self._page_confidence(result)
        )
        log.info("Docling Seite %d: dauer=%.2fs", page_number, time.perf_counter() - started)
        return self._page_text(page_number, page_width, page_height, pages.get(1, []), float(dpi))

    def convert_document(self, path: Path, document_id: str, password: str | None = None) -> DocumentText:
        """Ganzes Dokument mit Docling lesen (PDF oder Bild).

        Fehler: ``OcrUnavailableError`` (Docling/Modelle fehlen), ``OcrError``,
        ``PasswordRequiredError``/``WrongPasswordError``, ``DocumentAccessError``.
        Verschlüsselte PDFs werden mit dem Passwort nur im Speicher entschlüsselt.
        """
        ok, reason = self.is_available()
        if not ok:
            raise OcrUnavailableError(reason)
        data = read_document_bytes(Path(path))
        file_type = detect_file_type_from_bytes(data[:2048])
        page_sizes: dict[int, tuple[float, float]] = {}
        if file_type == "pdf":
            try:
                with pymupdf.open(stream=data, filetype="pdf") as pdf:
                    if pdf.needs_pass:
                        if not password:
                            raise PasswordRequiredError()
                        if not pdf.authenticate(password):
                            raise WrongPasswordError()
                        data = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)  # nur im Speicher
                    page_sizes = {
                        i + 1: (float(p.rect.width), float(p.rect.height)) for i, p in enumerate(pdf)
                    }
            except (RuntimeError, ValueError) as exc:
                if isinstance(exc, PasswordRequiredError):
                    raise
                raise OcrError("Die PDF-Datei kann nicht gelesen werden.") from exc
            name = "dokument.pdf"
        elif file_type in ("png", "jpeg", "tiff"):
            name = {"png": "dokument.png", "jpeg": "dokument.jpg", "tiff": "dokument.tif"}[file_type]
        else:
            raise OcrError("Dateityp wird nicht unterstützt.")
        started = time.perf_counter()
        result = self._convert(name, data)
        words_by_page = docling_document_to_pages(
            result.document, page_sizes or None, self._page_confidence(result)
        )
        doc_pages = getattr(result.document, "pages", None) or {}
        page_numbers = sorted(set(words_by_page) | set(page_sizes))
        pages: list[PageText] = []
        for number in page_numbers:
            if number in page_sizes:
                width, height = page_sizes[number]
            else:
                size = getattr(doc_pages.get(number), "size", None)
                width = float(getattr(size, "width", 0.0) or 0.0)
                height = float(getattr(size, "height", 0.0) or 0.0)
            pages.append(self._page_text(number, width, height, words_by_page.get(number, []), None))
        log.info("Docling Dokument: seiten=%d dauer=%.2fs", len(pages), time.perf_counter() - started)
        return DocumentText(document_id=document_id, pages=pages)
