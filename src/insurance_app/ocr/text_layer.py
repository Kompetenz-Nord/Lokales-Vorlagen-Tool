"""Native Textebene eines PDFs lesen (PyMuPDF), inkl. Erkennung fremder OCR-Ebenen.

* Wörter aus ``page.get_text("words")``; Boxen werden ins angezeigte
  Seitensystem umgerechnet (``/Rotate`` angewendet, Ursprung oben links) und
  auf die sichtbare Seite begrenzt. ``block_no``/``line_no`` stammen direkt
  von PyMuPDF; eine Zeile ist durch ``(block_no, line_no)`` bestimmt.
* Seitentext aus ``page.get_text("text", sort=True)`` (Lesereihenfolge oben → unten).
* Herkunft: ``NATIVE`` (digital erzeugt), ``EMBEDDED_OCR`` (Scan mit fremder
  Textebene: großes Bild ≥ 85 % der Seite plus Text, oder überwiegend
  unsichtbarer Text – siehe ``documents.pdf_analysis``) oder ``NONE`` (kein Text).
* Qualität über ``ocr.page_quality.assess_text_quality``.
* ``PageText.rotation`` = ``/Rotate`` der Seite (Koordinaten sind bereits gedreht).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pymupdf

from ..core.models import (
    BoundingBox,
    DocumentText,
    ExtractionMethod,
    PageQuality,
    PageText,
    TextLayerOrigin,
    Word,
)
from ..core.settings import Settings
from ..documents.pages import DocumentAccessError, PageSource
from ..documents.pdf_analysis import (
    count_meaningful_chars,
    displayed_rect,
    image_coverage,
    is_embedded_ocr,
    text_visibility,
)
from .page_quality import assess_text_quality

log = logging.getLogger(__name__)

EMBEDDED_OCR_WARNING = (
    "Textebene stammt vermutlich aus einer fremden Texterkennung – Werte besonders sorgfältig prüfen."
)
UNREADABLE_PAGE_WARNING = "Die Textebene dieser Seite konnte nicht gelesen werden."
#: Versionskennung für ``PageText.engine_version`` (ohne Personendaten).
TEXT_LAYER_ENGINE = f"PyMuPDF {pymupdf.VersionBind} (MuPDF {pymupdf.VersionFitz})"


def _to_bbox(rect: pymupdf.Rect) -> BoundingBox:
    return BoundingBox(x0=float(rect.x0), y0=float(rect.y0), x1=float(rect.x1), y1=float(rect.y1))


def page_words(page: pymupdf.Page) -> list[Word]:
    """Wörter der Textebene im angezeigten Seitensystem."""
    page_rect = page.rect
    words: list[Word] = []
    for entry in page.get_text("words"):
        x0, y0, x1, y1, raw, block_no, line_no = entry[:7]
        token = str(raw).strip()
        if not token:
            continue
        rect = displayed_rect(page, (x0, y0, x1, y1))
        if not rect.intersects(page_rect):
            continue  # liegt außerhalb des sichtbaren Bereichs
        rect &= page_rect
        words.append(
            Word(
                text=token, bbox=_to_bbox(rect), confidence=None, block_no=int(block_no), line_no=int(line_no)
            )
        )
    return words


def extract_page_text(page: pymupdf.Page, page_number: int, settings: Settings | None = None) -> PageText:
    """Textebene einer (bereits geöffneten) PDF-Seite auswerten."""
    settings = settings or Settings()
    rect = page.rect
    rotation = int(page.rotation) % 360
    try:
        words = page_words(page)
        text = page.get_text("text", sort=True)
    except (RuntimeError, ValueError) as exc:
        log.info("Textebene Seite %d nicht lesbar (%s)", page_number, type(exc).__name__)
        return PageText(
            page_number=page_number,
            width=float(rect.width),
            height=float(rect.height),
            method=ExtractionMethod.PDF_TEXT,
            origin=TextLayerOrigin.NONE,
            quality=PageQuality.EMPTY,
            warnings=[UNREADABLE_PAGE_WARNING],
            engine_version=TEXT_LAYER_ENGINE,
            rotation=rotation,
        )

    meaningful = count_meaningful_chars(text)
    if not text.strip():
        origin = TextLayerOrigin.NONE
    else:
        invisible, total = text_visibility(page)
        coverage = image_coverage(page)
        embedded = is_embedded_ocr(coverage, invisible, total, meaningful)
        origin = TextLayerOrigin.EMBEDDED_OCR if embedded else TextLayerOrigin.NATIVE

    quality, warnings = assess_text_quality(text, words, origin, None, settings)
    if origin == TextLayerOrigin.EMBEDDED_OCR:
        warnings = [EMBEDDED_OCR_WARNING, *warnings]
    return PageText(
        page_number=page_number,
        width=float(rect.width),
        height=float(rect.height),
        method=ExtractionMethod.PDF_TEXT,
        origin=origin,
        words=words,
        text=text,
        mean_confidence=None,
        quality=quality,
        warnings=warnings,
        engine_version=TEXT_LAYER_ENGINE,
        source_dpi=None,
        rotation=rotation,
    )


def extract_text_layer(
    path: Path,
    document_id: str,
    password: str | None = None,
    settings: Settings | None = None,
) -> DocumentText:
    """Textebene aller Seiten eines PDFs lesen.

    Fehler: ``PasswordRequiredError``/``WrongPasswordError`` bei fehlendem bzw.
    falschem Passwort, ``DocumentAccessError`` bei unlesbaren Dateien oder
    Nicht-PDFs (alle aus ``documents.pages``).
    """
    settings = settings or Settings()
    started = time.perf_counter()
    with PageSource(Path(path), password) as source:
        if not source.is_pdf:
            raise DocumentAccessError("Eine Textebene gibt es nur bei PDF-Dateien.")
        pages = [
            extract_page_text(source.pdf_page(number), number, settings)
            for number in range(1, source.page_count + 1)
        ]
    log.info(
        "Textebene gelesen: seiten=%d leer=%d dauer=%.2fs",
        len(pages),
        sum(1 for p in pages if p.quality == PageQuality.EMPTY),
        time.perf_counter() - started,
    )
    return DocumentText(document_id=document_id, pages=pages)
