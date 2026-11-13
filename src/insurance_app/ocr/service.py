"""Texterkennung für ein ganzes Dokument (Stufe 3 "Texterkennung").

Ablauf
------
* **PDF**: Zuerst die vorhandene Textebene je Seite (``ocr.text_layer``).
  OCR nur für Seiten, die
  - keinen brauchbaren Text haben (Qualität ``EMPTY``),
  - eine unzuverlässige Textebene haben (Qualität ``LOW``; betrifft vor allem
    fremde OCR-Ebenen ``EMBEDDED_OCR``, aber auch digital erzeugten Text mit
    defekter Zeichenkodierung). Dann werden beide Ergebnisse verglichen; das
    bessere bleibt, und ein Hinweis nennt die verwendete Quelle,
  - in ``force_ocr_pages`` stehen (OCR ersetzt dann die Textebene).
  Seiten werden dafür im Speicher mit ``settings.ocr_dpi`` gerendert.
* **Bilder** (PNG/JPEG/TIFF): OCR für alle Seiten.
* Backend: ``settings.ocr_backend``, wenn verfügbar, sonst Tesseract, sonst
  ein anderes verfügbares Backend. Ist keines verfügbar, entsteht für
  betroffene Seiten ein ``PageText`` mit Qualität ``EMPTY`` und dem Hinweis
  "Keine Texterkennung verfügbar – Seite bitte manuell prüfen." (kein Absturz).
* ``PageText.rotation`` ist die Gesamtdrehung gegenüber der Rohseite
  (PDF ``/Rotate`` bzw. EXIF + ggf. automatische Ausrichtung durch die OCR).
* Keine temporären Dateien. Logs enthalten nur Zähler und Zeiten.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..core.models import (
    DocumentCheckResult,
    DocumentText,
    ExtractionMethod,
    PageQuality,
    PageText,
    TextLayerOrigin,
)
from ..core.settings import Settings
from ..documents.pages import DocumentAccessError, PageSource, image_effective_dpi
from .base import OcrBackend, OcrError, OcrUnavailableError
from .docling_backend import DoclingBackend
from .page_quality import text_statistics
from .rapidocr_backend import RapidOcrBackend
from .tesseract_backend import TesseractBackend
from .text_layer import UNREADABLE_PAGE_WARNING, extract_page_text

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]

NO_OCR_WARNING = "Keine Texterkennung verfügbar – Seite bitte manuell prüfen."
OCR_FAILED_WARNING = "Die Texterkennung für diese Seite ist fehlgeschlagen – Seite bitte manuell prüfen."
USED_OCR_WARNING = "Für diese Seite wurde die eigene Texterkennung verwendet, weil die vorhandene Textebene unzuverlässig war."
KEPT_LAYER_WARNING = (
    "Die vorhandene Textebene wurde beibehalten; die eigene Texterkennung war nicht besser. "
    "Werte bitte sorgfältig prüfen."
)

_QUALITY_RANK = {PageQuality.EMPTY: 0, PageQuality.LOW: 1, PageQuality.GOOD: 2}
_SCORE_MARGIN = 0.02

# Gründe für OCR einer PDF-Seite
_REASON_EMPTY = "empty"
_REASON_LOW = "low"
_REASON_FORCED = "forced"


class RecognitionCancelled(Exception):
    """Die Texterkennung wurde auf Wunsch abgebrochen."""

    def __init__(self, message: str = "Die Texterkennung wurde abgebrochen.") -> None:
        super().__init__(message)


class RecognitionError(RuntimeError):
    """Das Dokument kann nicht verarbeitet werden (Meldung auf Deutsch, ohne Inhalte)."""


def _merge_warnings(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for warning in group:
            if warning and warning not in merged:
                merged.append(warning)
    return merged


class TextRecognitionService:
    """Erzeugt ``DocumentText`` aus PDF/Bild – Textebene zuerst, OCR nur wo nötig."""

    def __init__(self, settings: Settings, backends: dict[str, OcrBackend] | None = None) -> None:
        self.settings = settings
        self.backends: dict[str, OcrBackend] = backends if backends is not None else self._default_backends()

    def _default_backends(self) -> dict[str, OcrBackend]:
        languages = self.settings.ocr_languages
        try:
            tesseract = TesseractBackend(
                languages=languages, tesseract_cmd=self.settings.tesseract_cmd, settings=self.settings
            )
        except ValueError:
            log.warning("Ungültige OCR-Sprachangabe in den Einstellungen – Standard deu+eng wird verwendet")
            languages = "deu+eng"
            tesseract = TesseractBackend(
                languages=languages, tesseract_cmd=self.settings.tesseract_cmd, settings=self.settings
            )
        return {
            "tesseract": tesseract,
            "rapidocr": RapidOcrBackend(settings=self.settings),
            "docling": DoclingBackend(
                languages=languages, tesseract_cmd=self.settings.tesseract_cmd, settings=self.settings
            ),
        }

    # -- Backends ---------------------------------------------------------
    def available_backends(self) -> dict[str, tuple[bool, str]]:
        """Verfügbarkeit je Backend: ``{name: (bereit?, Begründung)}``."""
        status: dict[str, tuple[bool, str]] = {}
        for name, backend in self.backends.items():
            try:
                status[name] = backend.is_available()
            except Exception as exc:  # defektes Backend darf die Übersicht nicht verhindern
                log.warning("Verfügbarkeitsprüfung %s fehlgeschlagen (%s)", name, type(exc).__name__)
                status[name] = (False, "Verfügbarkeit konnte nicht geprüft werden.")
        return status

    def select_backend(self) -> tuple[OcrBackend | None, str]:
        """(gewähltes Backend oder None, Begründung, falls keines verfügbar)."""
        order = [self.settings.ocr_backend, "tesseract", *self.backends]
        reasons: list[str] = []
        seen: set[str] = set()
        for name in order:
            if name in seen or name not in self.backends:
                continue
            seen.add(name)
            backend = self.backends[name]
            try:
                ok, reason = backend.is_available()
            except Exception as exc:  # defektes Backend überspringen
                log.warning("Verfügbarkeitsprüfung %s fehlgeschlagen (%s)", name, type(exc).__name__)
                ok, reason = False, ""
            if ok:
                if name != self.settings.ocr_backend:
                    log.info("OCR-Backend %s nicht verfügbar – verwende %s", self.settings.ocr_backend, name)
                return backend, ""
            if reason and reason not in reasons:
                reasons.append(reason)
        return None, (reasons[0] if reasons else "")

    # -- Hauptfunktion ----------------------------------------------------
    def recognize_document(
        self,
        path: Path,
        document_id: str,
        check: DocumentCheckResult,
        password: str | None = None,
        force_ocr_pages: set[int] | None = None,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> DocumentText:
        """Text aller Seiten erkennen (siehe Moduldokumentation).

        Fehler: ``RecognitionCancelled`` bei Abbruch, ``RecognitionError`` bei
        nicht bestandener Eingangsprüfung oder veränderter Datei,
        ``DocumentAccessError`` (inkl. ``PasswordRequiredError``) beim Öffnen.
        """
        if not check.ok or check.file_type not in ("pdf", "png", "jpeg", "tiff"):
            raise RecognitionError(
                "Das Dokument hat die Eingangsprüfung nicht bestanden und kann nicht gelesen werden."
            )
        started = time.perf_counter()
        forced = set(force_ocr_pages or ())
        notify = progress or (lambda _current, _total, _message: None)
        self._check_cancel(cancel)
        notify(0, max(check.page_count, 1), "Dokument wird geöffnet …")

        with PageSource(Path(path), password) as source:
            if check.sha256 and source.sha256 != check.sha256:
                raise RecognitionError("Die Datei wurde seit der Prüfung verändert. Bitte erneut prüfen.")
            total = source.page_count
            context = _RunContext(self, source, cancel)
            pages: list[PageText] = []
            for number in range(1, total + 1):
                self._check_cancel(cancel)
                prefix = f"Seite {number} von {total}"
                if source.is_pdf:
                    layer = self._layer_page(source, number)
                    reason = self._ocr_reason(layer, number in forced)
                    if reason is None:
                        pages.append(layer)
                        notify(number, total, f"{prefix}: Text gelesen")
                        continue
                    notify(number - 1, total, f"{prefix}: Texterkennung läuft …")
                    ocr_page, failure = context.ocr(number)
                    pages.append(self._combine(layer, ocr_page, failure, reason))
                else:
                    notify(number - 1, total, f"{prefix}: Texterkennung läuft …")
                    ocr_page, failure = context.ocr(number)
                    pages.append(ocr_page if ocr_page is not None else context.placeholder(number, failure))
                notify(number, total, f"{prefix}: fertig")

        notify(total, total, "Texterkennung abgeschlossen")
        log.info(
            "Texterkennung: seiten=%d ocr_seiten=%d backend=%s dauer=%.2fs",
            total,
            context.ocr_pages,
            context.backend_name or "-",
            time.perf_counter() - started,
        )
        return DocumentText(document_id=document_id, pages=pages)

    # -- Hilfen -----------------------------------------------------------
    @staticmethod
    def _check_cancel(cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise RecognitionCancelled()

    def _layer_page(self, source: PageSource, number: int) -> PageText:
        """Textebene einer PDF-Seite; eine unlesbare Seite bricht das Dokument nicht ab."""
        try:
            return extract_page_text(source.pdf_page(number), number, self.settings)
        except DocumentAccessError as exc:
            log.warning("Seite %d nicht lesbar (%s)", number, type(exc).__name__)
            return PageText(
                page_number=number,
                width=0.0,
                height=0.0,
                method=ExtractionMethod.PDF_TEXT,
                origin=TextLayerOrigin.NONE,
                quality=PageQuality.EMPTY,
                warnings=[UNREADABLE_PAGE_WARNING],
            )

    @staticmethod
    def _ocr_reason(layer: PageText, forced: bool) -> str | None:
        if forced:
            return _REASON_FORCED
        if layer.quality == PageQuality.EMPTY:
            return _REASON_EMPTY
        if layer.quality == PageQuality.LOW:
            return _REASON_LOW
        return None

    @staticmethod
    def _ocr_is_better(layer: PageText, ocr_page: PageText) -> bool:
        layer_rank, ocr_rank = _QUALITY_RANK[layer.quality], _QUALITY_RANK[ocr_page.quality]
        if ocr_rank != layer_rank:
            return ocr_rank > layer_rank
        if layer.quality == PageQuality.EMPTY:
            return True
        layer_score = text_statistics(layer.text, layer.words).score
        ocr_score = text_statistics(ocr_page.text, ocr_page.words).score
        return ocr_score > layer_score + _SCORE_MARGIN

    def _combine(
        self, layer: PageText, ocr_page: PageText | None, failure: list[str], reason: str
    ) -> PageText:
        if ocr_page is None:
            layer.warnings = _merge_warnings(layer.warnings, failure)
            return layer
        if reason == _REASON_FORCED:
            return ocr_page
        if reason == _REASON_EMPTY:
            return ocr_page if self._ocr_is_better(layer, ocr_page) else layer
        # LOW: vergleichen und Hinweis auf die verwendete Quelle
        if self._ocr_is_better(layer, ocr_page):
            ocr_page.warnings = _merge_warnings([USED_OCR_WARNING], ocr_page.warnings)
            return ocr_page
        layer.warnings = _merge_warnings(layer.warnings, [KEPT_LAYER_WARNING])
        return layer


class _RunContext:
    """Zustand eines Durchlaufs: Backend-Auswahl (einmalig, erst bei Bedarf) und OCR je Seite."""

    def __init__(
        self, service: TextRecognitionService, source: PageSource, cancel: threading.Event | None
    ) -> None:
        self.service = service
        self.source = source
        self.cancel = cancel
        self.ocr_pages = 0
        self._selected = False
        self._backend: OcrBackend | None = None
        self._unavailable_reason = ""

    @property
    def backend_name(self) -> str | None:
        return self._backend.name if self._backend is not None else None

    def _ensure_backend(self) -> OcrBackend | None:
        if not self._selected:
            self._backend, self._unavailable_reason = self.service.select_backend()
            self._selected = True
        return self._backend

    def _no_backend_warnings(self) -> list[str]:
        return _merge_warnings(
            [NO_OCR_WARNING], [self._unavailable_reason] if self._unavailable_reason else []
        )

    def ocr(self, number: int) -> tuple[PageText | None, list[str]]:
        """OCR einer Seite; bei Problemen (None, Hinweise)."""
        backend = self._ensure_backend()
        if backend is None:
            return None, self._no_backend_warnings()
        try:
            image = self.source.render(number, self.service.settings.ocr_dpi)
            width, height = self.source.page_size_points(number)
        except DocumentAccessError as exc:
            log.warning("Seite %d konnte nicht gerendert werden (%s)", number, type(exc).__name__)
            return None, [OCR_FAILED_WARNING]
        try:
            TextRecognitionService._check_cancel(self.cancel)
            page = backend.recognize(image, number, width, height, image_effective_dpi(image))
        except RecognitionCancelled:
            raise
        except OcrUnavailableError as exc:
            log.warning("OCR-Backend %s nicht verfügbar (Seite %d)", backend.name, number)
            reason = str(exc)
            return None, _merge_warnings([NO_OCR_WARNING], [reason] if reason else [])
        except OcrError as exc:
            log.warning("OCR Seite %d fehlgeschlagen (%s)", number, type(exc).__name__)
            return None, [OCR_FAILED_WARNING]
        except Exception as exc:  # Fehler eines Backends darf das Dokument nicht abbrechen
            log.warning("OCR Seite %d: unerwarteter Fehler (%s)", number, type(exc).__name__)
            return None, [OCR_FAILED_WARNING]
        finally:
            image.close()
        self.ocr_pages += 1
        page.rotation = (self.source.page_rotation(number) + page.rotation) % 360
        return page, []

    def placeholder(self, number: int, warnings: list[str]) -> PageText:
        """Leere Seite mit Hinweis (Bildseite ohne verfügbare Texterkennung)."""
        try:
            width, height = self.source.page_size_points(number)
            rotation = self.source.page_rotation(number)
        except DocumentAccessError:
            width, height, rotation = 0.0, 0.0, 0
        method = self._backend.method if self._backend is not None else ExtractionMethod.TESSERACT
        return PageText(
            page_number=number,
            width=width,
            height=height,
            method=method,
            origin=TextLayerOrigin.NONE,
            quality=PageQuality.EMPTY,
            warnings=warnings or [NO_OCR_WARNING],
            rotation=rotation,
        )
