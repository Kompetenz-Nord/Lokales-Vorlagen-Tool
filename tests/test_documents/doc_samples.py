"""Synthetische Testdokumente (MUSTER – KEINE ECHTDATEN), erzeugt mit PyMuPDF/PIL."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from insurance_app.core.paths import resource_root

FONT_PATH = resource_root() / "assets" / "fonts" / "LiberationSans-Regular.ttf"
MUSTER = "MUSTER – KEINE ECHTDATEN"
A4 = (595.276, 841.890)

LETTER_LINES = [
    MUSTER,
    "Muster Lebensversicherung AG · Musterweg 1 · 12345 Musterstadt",
    "Musterstadt, 13. März 2025",
    "Nachtrag zum Versicherungsschein",
    "Versicherungsnummer: 4711-0815",
    "Die Änderung ist gültig ab 01.04.2025.",
    "Der monatliche Beitrag beträgt künftig 123,45 EUR.",
]


def make_text_pdf(
    path: Path,
    lines: list[str] | None = None,
    *,
    pages: int = 1,
    rotation: int = 0,
    size: tuple[float, float] = A4,
) -> Path:
    """Digital erzeugtes PDF mit echter Textebene (Liberation Sans, Umlaute)."""
    lines = lines or LETTER_LINES
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_font(fontname="libsans", fontfile=str(FONT_PATH))
        y = 72.0
        for line in lines:
            page.insert_text((72, y), line, fontsize=12, fontname="libsans")
            y += 20
        if rotation:
            page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


def text_image(
    lines: list[str] | None = None,
    *,
    size_px: tuple[int, int] = (1240, 1754),
    font_px: int = 34,
    color: str = "white",
) -> Image.Image:
    """Bild mit gezeichnetem Text (Standard: A4 bei 150 dpi)."""
    lines = lines or LETTER_LINES
    img = Image.new("RGB", size_px, color)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_PATH), font_px)
    y = font_px * 2
    for line in lines:
        draw.text((font_px * 2, y), line, font=font, fill="black")
        y += int(font_px * 1.6)
    return img


def png_bytes(img: Image.Image, **save_kwargs: object) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", **save_kwargs)
    return buffer.getvalue()


def make_image_pdf(path: Path, img: Image.Image | None = None, *, size: tuple[float, float] = A4) -> Path:
    """Scan-ähnliches PDF: ganzseitiges Bild, keine Textebene."""
    img = img or text_image()
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_image(page.rect, stream=png_bytes(img))
    doc.save(path)
    doc.close()
    return path


def make_embedded_ocr_pdf(path: Path, lines: list[str] | None = None) -> Path:
    """Scan mit fremder OCR-Ebene: ganzseitiges Bild + unsichtbarer Text (render_mode=3)."""
    lines = lines or LETTER_LINES
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=png_bytes(text_image(lines)))
    page.insert_font(fontname="libsans", fontfile=str(FONT_PATH))
    y = 72.0
    for line in lines:
        page.insert_text((72, y), line, fontsize=12, fontname="libsans", render_mode=3)
        y += 20
    doc.save(path)
    doc.close()
    return path


def make_encrypted_pdf(path: Path, user_pw: str | None, owner_pw: str = "MUSTER-owner") -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_font(fontname="libsans", fontfile=str(FONT_PATH))
    for i, line in enumerate(LETTER_LINES):
        page.insert_text((72, 72 + 20 * i), line, fontsize=12, fontname="libsans")
    kwargs: dict[str, object] = {
        "encryption": pymupdf.PDF_ENCRYPT_AES_256,
        "owner_pw": owner_pw,
        "permissions": pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_FORM,
    }
    if user_pw is not None:
        kwargs["user_pw"] = user_pw
    doc.save(path, **kwargs)
    doc.close()
    return path
