"""Synthetische Seiten für OCR-Tests (MUSTER – KEINE ECHTDATEN)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from insurance_app.core.models import ExtractionMethod, PageQuality, PageText, TextLayerOrigin, Word
from insurance_app.core.paths import resource_root
from insurance_app.ocr.base import words_to_text

FONT_PATH = resource_root() / "assets" / "fonts" / "LiberationSans-Regular.ttf"
MUSTER = "MUSTER – KEINE ECHTDATEN"
A4 = (595.276, 841.890)

NATIVE_LINES = [
    MUSTER,
    "Muster Lebensversicherung AG",
    "Standmitteilung zum 31.12.2024",
    "Versicherungsnummer: 4711-0815",
    "Vertragsguthaben: 12.345,67 EUR",
    "Rückkaufswert: 11.000,00 EUR",
    "Überschussbeteiligung und Garantiewert für das Jahr",
]

# Zeilen für Bilder: (Text, x in Pixeln) bei 300 dpi; die Vertragsnummer steht an fester Position.
OCR_LINES = [
    "MUSTER KEINE ECHTDATEN",
    "Muster Lebensversicherung AG",
    "Jährliche Information zu Ihrer Versicherung",
    "Vertragsguthaben 12.345,67 EUR",
    "Musterstraße 12, 12345 Musterstadt",
]
NUMBER_LABEL = "Versicherungsnummer"
NUMBER_VALUE = "4711-0815"
NUMBER_LABEL_POS = (300, 1000)  # Pixel bei 300 dpi
NUMBER_VALUE_POS = (1300, 1000)


def font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), px)


def a4_text_image(dpi: int = 300) -> Image.Image:
    """A4-Seite (Hochformat) mit deutschem Text, gezeichnet mit Liberation Sans 12 pt."""
    scale = dpi / 300
    size = (round(A4[0] * dpi / 72), round(A4[1] * dpi / 72))
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    f = font(round(50 * scale))
    for i, line in enumerate(OCR_LINES):
        draw.text((round(300 * scale), round((300 + i * 110) * scale)), line, font=f, fill="black")
    draw.text(
        (round(NUMBER_LABEL_POS[0] * scale), round(NUMBER_LABEL_POS[1] * scale)),
        NUMBER_LABEL,
        font=f,
        fill="black",
    )
    draw.text(
        (round(NUMBER_VALUE_POS[0] * scale), round(NUMBER_VALUE_POS[1] * scale)),
        NUMBER_VALUE,
        font=f,
        fill="black",
    )
    return img


def noisy_low_res_image() -> Image.Image:
    """Kleine, verrauschte Aufnahme (72 dpi) – Tesseract liest nur unsicher."""
    lines = [
        "MUSTER KEINE ECHTDATEN",
        "Versicherungsnummer 4711-0815",
        "Vertragsguthaben 12.345,67 EUR",
        "Beitrag monatlich 123,45 EUR",
        "Rueckkaufswert 9.876,54 EUR",
        "Musterstadt Musterweg 1",
        "Jaehrliche Information 2025",
        "Ueberschussbeteiligung 1.234,00 EUR",
    ]
    img = Image.new("L", (520, 320), 255)
    draw = ImageDraw.Draw(img)
    f = font(12)
    for i, line in enumerate(lines):
        draw.text((15, 15 + i * 26), line, font=f, fill=0)
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    rng = np.random.default_rng(7)
    arr = np.asarray(img, dtype=np.float64) + rng.normal(0, 20, (320, 520))
    mask = rng.random((320, 520))
    arr[mask < 0.02] = 0
    arr[mask > 0.98] = 255
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


def png_bytes(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def add_native_page(doc: pymupdf.Document, lines: list[str] | None = None, rotation: int = 0) -> pymupdf.Page:
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_font(fontname="libsans", fontfile=str(FONT_PATH))
    for i, line in enumerate(lines or NATIVE_LINES):
        page.insert_text((72, 100 + 22 * i), line, fontsize=12, fontname="libsans")
    if rotation:
        page.set_rotation(rotation)
    return page


def add_image_page(doc: pymupdf.Document, img: Image.Image | None = None, rotation: int = 0) -> pymupdf.Page:
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=png_bytes(img or a4_text_image(150)))
    if rotation:
        page.set_rotation(rotation)
    return page


def add_embedded_ocr_page(doc: pymupdf.Document, hidden_lines: list[str]) -> pymupdf.Page:
    page = add_image_page(doc)
    page.insert_font(fontname="libsans", fontfile=str(FONT_PATH))
    for i, line in enumerate(hidden_lines):
        page.insert_text((72, 100 + 22 * i), line, fontsize=12, fontname="libsans", render_mode=3)
    return page


def save(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(path)
    doc.close()
    return path


def fake_page_text(
    page_number: int,
    width: float,
    height: float,
    text: str,
    *,
    quality: PageQuality = PageQuality.GOOD,
    confidence: float = 0.95,
    method: ExtractionMethod = ExtractionMethod.TESSERACT,
) -> PageText:
    words = [
        Word(
            text=token,
            bbox={"x0": 10.0 + 40 * i, "y0": 10.0, "x1": 45.0 + 40 * i, "y1": 22.0},
            confidence=confidence,
            block_no=0,
            line_no=0,
        )
        for i, token in enumerate(text.split())
    ]
    return PageText(
        page_number=page_number,
        width=width,
        height=height,
        method=method,
        origin=TextLayerOrigin.OCR,
        words=words,
        text=words_to_text(words),
        mean_confidence=confidence,
        quality=quality,
        engine_version="fake 1.0",
    )


class FakeBackend:
    """Test-Backend: liefert festen Text und merkt sich die Aufrufe."""

    method = ExtractionMethod.TESSERACT

    def __init__(
        self,
        text: str = "MUSTER Fake Erkennung Versicherungsnummer 4711-0815 Vertragsguthaben 12.345,67 EUR Ende",
        *,
        name: str = "fake",
        available: bool = True,
        quality: PageQuality = PageQuality.GOOD,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.text = text
        self.available = available
        self.quality = quality
        self.error = error
        self.calls: list[dict[str, object]] = []

    def is_available(self) -> tuple[bool, str]:
        return (True, "bereit") if self.available else (False, "Fake-Backend ist nicht installiert.")

    def version(self) -> str | None:
        return "1.0"

    def recognize(self, image, page_number, page_width, page_height, dpi) -> PageText:
        self.calls.append(
            {
                "page": page_number,
                "size": image.size,
                "page_width": page_width,
                "page_height": page_height,
                "dpi": dpi,
            }
        )
        if self.error is not None:
            raise self.error
        return fake_page_text(page_number, page_width, page_height, self.text, quality=self.quality)
