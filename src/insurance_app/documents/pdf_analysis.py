"""Analyse-Hilfen für PDF-Seiten und Seitenformate.

Gemeinsam genutzt von der Eingangsprüfung (``documents.intake``) und der
Textebenen-Auswertung (``ocr.text_layer``), damit beide dieselben Regeln
anwenden.

Koordinaten
-----------
PyMuPDF liefert Text- und Bildpositionen im *ungedrehten* Seitensystem
(relativ zur CropBox). Alle Funktionen hier rechnen mit
``page.rotation_matrix`` in das *angezeigte* System um (Ursprung oben links,
PDF-Punkte, ``/Rotate`` angewendet) – dasselbe System, in dem
``page.get_pixmap()`` rendert und in dem alle ``BoundingBox``-Werte der App
liegen.

Heuristiken (bewusst einfach, dokumentiert)
-------------------------------------------
* **Bildabdeckung**: Anteil der Seitenfläche, der von der Vereinigung aller
  Bildplatzierungen bedeckt wird (0..1).
* **Nur-Bild-Seite** (``image_only``): Bildabdeckung > 80 % und weniger als
  ``min_text_chars`` Buchstaben/Ziffern Text.
* **Fremde OCR-Textebene** (``EMBEDDED_OCR``): Es gibt Text und entweder
  mindestens die Hälfte der Zeichen ist unsichtbar (Text-Render-Modus 3 bzw.
  Deckkraft 0 laut ``page.get_texttrace()``) oder ein Bild bedeckt mindestens
  85 % der Seite. Digitale PDFs mit ganzseitigem Hintergrundbild können
  dadurch fälschlich als Scan gelten; Folge ist lediglich ein zusätzlicher
  Prüfhinweis.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pymupdf

log = logging.getLogger(__name__)

#: Standardformate in PDF-Punkten (Breite, Höhe im Hochformat).
PAGE_FORMATS: tuple[tuple[str, float, float], ...] = (
    ("A4", 595.276, 841.890),
    ("A5", 419.528, 595.276),
    ("A3", 841.890, 1190.551),
    ("A6", 297.638, 419.528),
    ("Letter", 612.0, 792.0),
    ("Legal", 612.0, 1008.0),
)
#: Relative Toleranz für die Formaterkennung (2 %).
FORMAT_TOLERANCE = 0.02
#: Name für alle nicht erkannten Formate.
OTHER_FORMAT = "sonstiges"

#: Bildabdeckung, ab der eine Seite ohne Text als reine Bildseite gilt.
IMAGE_ONLY_COVERAGE = 0.80
#: Bildabdeckung, ab der Text auf der Seite als fremde OCR-Ebene gilt.
EMBEDDED_OCR_COVERAGE = 0.85
#: Anteil unsichtbarer Zeichen, ab dem Text als fremde OCR-Ebene gilt.
EMBEDDED_OCR_INVISIBLE_SHARE = 0.5
#: Standard-Mindestzahl an Buchstaben/Ziffern für "brauchbaren Text" (wie ``Settings``).
DEFAULT_MIN_TEXT_CHARS = 40

# Mehr Bildplatzierungen werden über ein grobes Raster statt exakt vereinigt.
_EXACT_UNION_LIMIT = 300
_COARSE_GRID = 200


def page_format_name(width: float, height: float, tolerance: float = FORMAT_TOLERANCE) -> str:
    """Formatname wie ``"A4 hoch"``/``"A4 quer"`` oder ``"sonstiges"`` (Maße in PDF-Punkten)."""
    if not (width > 0 and height > 0) or math.isinf(width) or math.isinf(height):
        return OTHER_FORMAT
    short_side, long_side = sorted((float(width), float(height)))
    orientation = "hoch" if height >= width else "quer"
    for name, fmt_short, fmt_long in PAGE_FORMATS:
        if (
            abs(short_side - fmt_short) / fmt_short <= tolerance
            and abs(long_side - fmt_long) / fmt_long <= tolerance
        ):
            return f"{name} {orientation}"
    return OTHER_FORMAT


def points_to_mm(value: float) -> float:
    """PDF-Punkte in Millimeter."""
    return value * 25.4 / 72.0


def count_meaningful_chars(text: str) -> int:
    """Anzahl Buchstaben und Ziffern (Maß für "brauchbaren" Text)."""
    return sum(1 for c in text if c.isalnum())


def displayed_rect(
    page: pymupdf.Page, bbox: tuple[float, float, float, float] | pymupdf.Rect
) -> pymupdf.Rect:
    """Rechteck aus dem ungedrehten Seitensystem in das angezeigte System umrechnen."""
    rect = pymupdf.Rect(bbox)
    if page.rotation % 360:
        rect = rect * page.rotation_matrix
        rect.normalize()
    return rect


def _union_area(rects: list[tuple[float, float, float, float]]) -> float:
    """Exakte Fläche der Vereinigung achsenparalleler Rechtecke (Koordinatenkompression)."""
    xs = np.unique(np.array([v for r in rects for v in (r[0], r[2])], dtype=float))
    ys = np.unique(np.array([v for r in rects for v in (r[1], r[3])], dtype=float))
    if xs.size < 2 or ys.size < 2:
        return 0.0
    covered = np.zeros((ys.size - 1, xs.size - 1), dtype=bool)
    for x0, y0, x1, y1 in rects:
        ix0, ix1 = np.searchsorted(xs, x0), np.searchsorted(xs, x1)
        iy0, iy1 = np.searchsorted(ys, y0), np.searchsorted(ys, y1)
        covered[iy0:iy1, ix0:ix1] = True
    cell_areas = np.outer(np.diff(ys), np.diff(xs))
    return float((cell_areas * covered).sum())


def _coarse_union_share(rects: list[tuple[float, float, float, float]], width: float, height: float) -> float:
    grid = np.zeros((_COARSE_GRID, _COARSE_GRID), dtype=bool)
    for x0, y0, x1, y1 in rects:
        gx0 = round(x0 / width * _COARSE_GRID)
        gx1 = round(x1 / width * _COARSE_GRID)
        gy0 = round(y0 / height * _COARSE_GRID)
        gy1 = round(y1 / height * _COARSE_GRID)
        grid[gy0:gy1, gx0:gx1] = True
    return float(grid.mean())


def image_coverage(page: pymupdf.Page) -> float:
    """Anteil (0..1) der sichtbaren Seitenfläche, der von Bildern bedeckt ist."""
    page_rect = page.rect
    if page_rect.is_empty or page_rect.is_infinite:
        return 0.0
    try:
        infos = page.get_image_info()
    except (RuntimeError, ValueError) as exc:
        log.debug("Bildinformationen nicht lesbar (%s)", type(exc).__name__)
        return 0.0
    rects: list[tuple[float, float, float, float]] = []
    for info in infos:
        bbox = info.get("bbox")
        if not bbox:
            continue
        rect = displayed_rect(page, bbox) & page_rect
        if rect.is_empty or rect.is_infinite:
            continue
        rects.append(
            (rect.x0 - page_rect.x0, rect.y0 - page_rect.y0, rect.x1 - page_rect.x0, rect.y1 - page_rect.y0)
        )
    if not rects:
        return 0.0
    area = page_rect.width * page_rect.height
    if len(rects) > _EXACT_UNION_LIMIT:
        return min(1.0, _coarse_union_share(rects, page_rect.width, page_rect.height))
    return min(1.0, _union_area(rects) / area)


def text_visibility(page: pymupdf.Page) -> tuple[int, int]:
    """(unsichtbare Zeichen, alle Zeichen) laut Text-Trace; Leerzeichen zählen nicht."""
    try:
        spans = page.get_texttrace()
    except (RuntimeError, ValueError) as exc:
        log.debug("Text-Trace nicht lesbar (%s)", type(exc).__name__)
        return 0, 0
    invisible = 0
    total = 0
    for span in spans:
        chars = span.get("chars") or ()
        count = 0
        for ch in chars:
            code = ch[0]
            if code > 0 and not chr(code).isspace():
                count += 1
        total += count
        opacity = span.get("opacity", 1.0)
        if span.get("type") == 3 or (opacity is not None and opacity <= 0.0):
            invisible += count
    return invisible, total


def is_embedded_ocr(
    coverage: float, invisible_chars: int, total_trace_chars: int, meaningful_chars: int
) -> bool:
    """Heuristik "Textebene stammt aus fremder Texterkennung" (siehe Moduldokumentation)."""
    if meaningful_chars <= 0:
        return False
    if total_trace_chars > 0 and invisible_chars / total_trace_chars >= EMBEDDED_OCR_INVISIBLE_SHARE:
        return True
    return coverage >= EMBEDDED_OCR_COVERAGE


def is_image_only(coverage: float, meaningful_chars: int, min_text_chars: int) -> bool:
    """Reine Bildseite: großes Bild (> 80 %) und kaum Text."""
    return coverage > IMAGE_ONLY_COVERAGE and meaningful_chars < min_text_chars


def format_page_list(pages: list[int]) -> str:
    """Seitenliste kompakt formatieren: ``[1, 2, 3, 5]`` -> ``"1–3, 5"``."""
    if not pages:
        return ""
    ordered = sorted(set(pages))
    parts: list[str] = []
    start = prev = ordered[0]
    for p in ordered[1:]:
        if p == prev + 1:
            prev = p
            continue
        parts.append(str(start) if start == prev else f"{start}–{prev}")
        start = prev = p
    parts.append(str(start) if start == prev else f"{start}–{prev}")
    return ", ".join(parts)


def pages_phrase(pages: list[int]) -> str:
    """``"Seite 3"`` bzw. ``"Seiten 1–3, 5"``."""
    unique = sorted(set(pages))
    return f"Seite {unique[0]}" if len(unique) == 1 else f"Seiten {format_page_list(unique)}"
