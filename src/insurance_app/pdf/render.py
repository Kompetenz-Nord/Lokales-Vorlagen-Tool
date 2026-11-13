"""Seiten rendern – MuPDF (PyMuPDF) und PDFium (pypdfium2, Chrome-Engine).

Beide Renderer zeichnen Formularfelder mit (MuPDF: ``annots=True``; PDFium:
``init_forms()`` vor dem Laden der Seiten und ``may_draw_forms=True``).
Seitennummern sind 1-basiert. Ergebnis sind RGB-Bilder (Pillow).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf
from PIL import Image

__all__ = [
    "RENDERERS",
    "RenderError",
    "available_renderers",
    "render_all",
    "render_page",
    "save_previews",
]

log = logging.getLogger(__name__)

RENDERERS = ("mupdf", "pdfium")
MAX_DPI = 600

try:  # optional, aber in den Abhängigkeiten vorgesehen
    import pypdfium2 as _pdfium
except ImportError:  # pragma: no cover - abhängig von der Installation
    _pdfium = None


class RenderError(Exception):
    """Seite konnte nicht gerendert werden."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message = message


def available_renderers() -> list[str]:
    """Verfügbare Renderer in fester Reihenfolge (``mupdf`` immer)."""
    return [r for r in RENDERERS if r == "mupdf" or _pdfium is not None]


def _check_args(renderer: str, dpi: int) -> None:
    if renderer not in RENDERERS:
        raise ValueError(f"Unbekannter Renderer: {renderer}")
    if renderer not in available_renderers():
        raise RenderError("Der Renderer PDFium (pypdfium2) ist nicht installiert.")
    if not 10 <= dpi <= MAX_DPI:
        raise ValueError("dpi muss zwischen 10 und 600 liegen")


def _mupdf_pages(path: Path, dpi: int, pages: list[int] | None, password: str | None) -> list[Image.Image]:
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as exc:
        raise RenderError("Die PDF kann nicht geöffnet werden.") from exc
    try:
        if doc.needs_pass and not (password and doc.authenticate(password)):
            raise RenderError("Die PDF ist passwortgeschützt.")
        numbers = pages if pages is not None else list(range(1, doc.page_count + 1))
        images: list[Image.Image] = []
        for number in numbers:
            if not 1 <= number <= doc.page_count:
                raise RenderError("Seitenzahl außerhalb des Dokuments.")
            pix = doc[number - 1].get_pixmap(dpi=dpi, annots=True, alpha=False)
            images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        return images
    except RenderError:
        raise
    except Exception as exc:
        log.warning("MuPDF-Rendering fehlgeschlagen (%s)", type(exc).__name__)
        raise RenderError("Die Seite konnte mit MuPDF nicht dargestellt werden.") from exc
    finally:
        doc.close()


def _pdfium_pages(path: Path, dpi: int, pages: list[int] | None, password: str | None) -> list[Image.Image]:
    assert _pdfium is not None
    try:
        pdf = _pdfium.PdfDocument(str(path), password=password)
    except Exception as exc:
        raise RenderError("Die PDF kann mit PDFium nicht geöffnet werden.") from exc
    try:
        pdf.init_forms()  # vor dem ersten Seitenzugriff
        count = len(pdf)
        numbers = pages if pages is not None else list(range(1, count + 1))
        images: list[Image.Image] = []
        for number in numbers:
            if not 1 <= number <= count:
                raise RenderError("Seitenzahl außerhalb des Dokuments.")
            page = pdf[number - 1]
            try:
                bitmap = page.render(scale=dpi / 72, may_draw_forms=True)
                try:
                    images.append(bitmap.to_pil().convert("RGB"))
                finally:
                    bitmap.close()
            finally:
                page.close()
        return images
    except RenderError:
        raise
    except Exception as exc:
        log.warning("PDFium-Rendering fehlgeschlagen (%s)", type(exc).__name__)
        raise RenderError("Die Seite konnte mit PDFium nicht dargestellt werden.") from exc
    finally:
        pdf.close()


def _render(
    path: Path, dpi: int, renderer: str, pages: list[int] | None, password: str | None
) -> list[Image.Image]:
    _check_args(renderer, dpi)
    path = Path(path)
    if not path.is_file():
        raise RenderError("Die PDF-Datei wurde nicht gefunden.")
    if renderer == "mupdf":
        return _mupdf_pages(path, dpi, pages, password)
    return _pdfium_pages(path, dpi, pages, password)


def render_page(
    path: Path,
    page_number: int,
    dpi: int = 110,
    renderer: str = "mupdf",
    password: str | None = None,
) -> Image.Image:
    """Eine Seite (1-basiert) als RGB-Bild rendern."""
    return _render(path, dpi, renderer, [page_number], password)[0]


def render_all(
    path: Path,
    dpi: int = 110,
    renderer: str = "mupdf",
    password: str | None = None,
) -> list[Image.Image]:
    """Alle Seiten als RGB-Bilder rendern."""
    return _render(path, dpi, renderer, None, password)


def save_previews(
    path: Path,
    out_dir: Path,
    dpi: int = 110,
    renderer: str = "mupdf",
    password: str | None = None,
    prefix: str = "seite",
) -> list[Path]:
    """Alle Seiten als PNG speichern (``<prefix>_01.png`` …). Gibt die Pfade zurück."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, image in enumerate(render_all(path, dpi, renderer, password), start=1):
        target = out_dir / f"{prefix}_{index:02d}.png"
        image.save(target, format="PNG", optimize=False)
        paths.append(target)
    return paths
