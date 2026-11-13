"""Optionales RapidOCR-Backend (``rapidocr_onnxruntime``, Modelle im Paket enthalten).

Nur ``rapidocr_onnxruntime`` wird unterstützt: Es bringt seine Modelle im
Paket mit und lädt zur Laufzeit nichts aus dem Netz. Das Nachfolgepaket
``rapidocr`` (ab 2.x) lädt Modelle beim ersten Start herunter und wird daher
bewusst nicht verwendet.

RapidOCR liefert Zeilen (Box mit vier Eckpunkten, Text, Score). Wörter werden
daraus durch Aufteilen am Leerzeichen gebildet; ihre Boxen werden anteilig
nach Zeichenzahl über die Zeilenbreite verteilt (Näherung). Jede erkannte
Zeile erhält ``block_no = 0`` und eine fortlaufende ``line_no``.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import threading
import time
from collections.abc import Sequence
from importlib import metadata

import numpy as np
from PIL import Image

from ..core.models import ExtractionMethod, PageText, TextLayerOrigin, Word
from ..core.settings import Settings
from ..documents.pages import to_rgb
from .base import OcrError, OcrUnavailableError, scale_bbox, weighted_mean_confidence, words_to_text
from .page_quality import assess_text_quality

log = logging.getLogger(__name__)

PACKAGE = "rapidocr_onnxruntime"
MSG_NOT_INSTALLED = "RapidOCR ist nicht installiert (optional: pip install rapidocr_onnxruntime)."
MSG_RUNTIME_MISSING = "Für RapidOCR fehlt ONNX Runtime (optional: pip install onnxruntime)."


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _parse_detection(item: object) -> tuple[list[tuple[float, float]], str, float] | None:
    """Ein RapidOCR-Ergebnis ``[box, text, score]`` robust lesen."""
    if not isinstance(item, Sequence) or len(item) < 3:
        return None
    box, text, score = item[0], item[1], item[2]
    try:
        points = [(float(p[0]), float(p[1])) for p in box]  # type: ignore[union-attr]
        confidence = float(score)  # type: ignore[arg-type]
    except (TypeError, ValueError, IndexError):
        return None
    if not points or not isinstance(text, str) or not text.strip():
        return None
    return points, text.strip(), max(0.0, min(1.0, confidence))


def detections_to_words(
    detections: Sequence[object],
    scale_x: float,
    scale_y: float,
    page_width: float,
    page_height: float,
) -> list[Word]:
    """RapidOCR-Zeilen in Wörter (Seitenkoordinaten) umwandeln."""
    parsed = [d for d in (_parse_detection(item) for item in detections) if d is not None]
    parsed.sort(key=lambda d: (min(p[1] for p in d[0]), min(p[0] for p in d[0])))
    words: list[Word] = []
    for line_no, (points, text, confidence) in enumerate(parsed):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        total = len(text)
        width = right - left
        position = 0
        for token in text.split():
            start = text.index(token, position)
            end = start + len(token)
            position = end
            x0 = left + width * start / total
            x1 = left + width * end / total
            words.append(
                Word(
                    text=token,
                    bbox=scale_bbox(x0, top, x1, bottom, scale_x, scale_y, page_width, page_height),
                    confidence=round(confidence, 4),
                    block_no=0,
                    line_no=line_no,
                )
            )
    return words


class RapidOcrBackend:
    """RapidOCR (ONNX) – optional, nur wenn installiert."""

    name = "rapidocr"
    method = ExtractionMethod.RAPID_OCR

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._engine: object | None = None
        self._lock = threading.Lock()

    def is_available(self) -> tuple[bool, str]:
        if not _module_available(PACKAGE):
            return False, MSG_NOT_INSTALLED
        if not _module_available("onnxruntime"):
            return False, MSG_RUNTIME_MISSING
        version = self.version()
        return True, (f"RapidOCR {version} bereit." if version else "RapidOCR bereit.")

    def version(self) -> str | None:
        try:
            return metadata.version(PACKAGE)
        except metadata.PackageNotFoundError:
            return None

    def _get_engine(self) -> object:
        with self._lock:
            if self._engine is None:
                try:
                    module = importlib.import_module(PACKAGE)
                    self._engine = module.RapidOCR()
                except ImportError as exc:
                    raise OcrUnavailableError(MSG_NOT_INSTALLED) from exc
                except Exception as exc:  # Modell-/Laufzeitfehler beim Start
                    log.warning("RapidOCR konnte nicht gestartet werden (%s)", type(exc).__name__)
                    raise OcrUnavailableError("RapidOCR konnte nicht gestartet werden.") from exc
            return self._engine

    def recognize(
        self,
        image: Image.Image,
        page_number: int,
        page_width: float,
        page_height: float,
        dpi: float,
    ) -> PageText:
        ok, reason = self.is_available()
        if not ok:
            raise OcrUnavailableError(reason)
        if image.width < 1 or image.height < 1 or page_width <= 0 or page_height <= 0:
            raise OcrError("Das Seitenbild ist leer.")
        engine = self._get_engine()
        started = time.perf_counter()
        array = np.asarray(to_rgb(image))
        try:
            output = engine(array)  # type: ignore[operator]
        except Exception as exc:  # Fehler der Fremdbibliothek kapseln
            log.warning("RapidOCR fehlgeschlagen (%s)", type(exc).__name__)
            raise OcrError("Die Texterkennung ist fehlgeschlagen.") from exc
        detections = output[0] if isinstance(output, tuple) else output
        words = detections_to_words(
            detections or [],
            page_width / image.width,
            page_height / image.height,
            page_width,
            page_height,
        )
        text = words_to_text(words)
        mean = weighted_mean_confidence(words)
        quality, warnings = assess_text_quality(text, words, TextLayerOrigin.OCR, mean, self.settings)
        log.info(
            "RapidOCR Seite %d: woerter=%d dauer=%.2fs",
            page_number,
            len(words),
            time.perf_counter() - started,
        )
        return PageText(
            page_number=page_number,
            width=page_width,
            height=page_height,
            method=ExtractionMethod.RAPID_OCR,
            origin=TextLayerOrigin.OCR,
            words=words,
            text=text,
            mean_confidence=mean,
            quality=quality,
            warnings=warnings,
            engine_version=f"rapidocr_onnxruntime {self.version() or '?'}",
            source_dpi=float(dpi),
            rotation=0,
        )
