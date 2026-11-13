"""Gemeinsame Schnittstelle aller OCR-Backends.

Ein Backend erkennt Text auf **einem Seitenbild** und liefert ein ``PageText``
in Seitenkoordinaten (Ursprung oben links, PDF-Punkte). Umrechnung:
``x_pt = x_px * page_width / image.width`` und
``y_pt = y_px * page_height / image.height`` (bei gleichen dpi in beiden
Richtungen ist das der Faktor ``72 / dpi``).

Dreht ein Backend das Bild, weil der Text auf dem Kopf oder quer stand
(Lageerkennung), beziehen sich ``PageText.width/height`` und alle Wortboxen
auf die **aufgerichtete** Seite; ``PageText.rotation`` enthält die Drehung im
Uhrzeigersinn relativ zum übergebenen Bild.

Eine Zeile ist durch das Paar ``(Word.block_no, Word.line_no)`` eindeutig
bestimmt (wie bei PyMuPDF).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from PIL import Image

from ..core.models import BoundingBox, ExtractionMethod, PageText, Word


class OcrUnavailableError(RuntimeError):
    """Das Backend ist nicht installiert oder nicht einsatzbereit (Meldung auf Deutsch)."""


class OcrError(RuntimeError):
    """Texterkennung ist für eine Seite fehlgeschlagen (Meldung auf Deutsch, ohne Inhalte)."""


@runtime_checkable
class OcrBackend(Protocol):
    """Protokoll für seitenweise Texterkennung."""

    name: str
    method: ExtractionMethod

    def is_available(self) -> tuple[bool, str]:
        """(bereit?, verständliche deutsche Begründung)."""
        ...

    def version(self) -> str | None:
        """Versionskennung der Engine oder ``None``."""
        ...

    def recognize(
        self,
        image: Image.Image,
        page_number: int,
        page_width: float,
        page_height: float,
        dpi: float,
    ) -> PageText:
        """Text auf einem Seitenbild erkennen; Wortboxen in Seitenkoordinaten."""
        ...


def scale_bbox(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    scale_x: float,
    scale_y: float,
    page_width: float,
    page_height: float,
) -> BoundingBox:
    """Pixel-Box in Seitenkoordinaten umrechnen und auf die Seite begrenzen."""
    left, right = sorted((x0 * scale_x, x1 * scale_x))
    top, bottom = sorted((y0 * scale_y, y1 * scale_y))
    return BoundingBox(
        x0=min(max(left, 0.0), page_width),
        y0=min(max(top, 0.0), page_height),
        x1=min(max(right, 0.0), page_width),
        y1=min(max(bottom, 0.0), page_height),
    )


def words_to_text(words: Iterable[Word]) -> str:
    """Text Zeile für Zeile aus Wörtern aufbauen (Reihenfolge wie übergeben).

    Wörter mit gleichem ``(block_no, line_no)`` bilden eine Zeile; zwischen
    Blöcken steht eine Leerzeile.
    """
    lines: list[str] = []
    current_key: tuple[int | None, int | None] | None = None
    current_block: int | None = None
    current: list[str] = []
    for word in words:
        key = (word.block_no, word.line_no)
        if key != current_key:
            if current:
                lines.append(" ".join(current))
            if current_key is not None and word.block_no != current_block:
                lines.append("")
            current = []
            current_key = key
            current_block = word.block_no
        current.append(word.text)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines).strip("\n")


def weighted_mean_confidence(words: Iterable[Word]) -> float | None:
    """Nach Wortlänge gewichtete mittlere Konfidenz (0..1) oder ``None`` ohne Wörter."""
    total = 0.0
    weight = 0
    for word in words:
        if word.confidence is None:
            continue
        n = max(len(word.text), 1)
        total += word.confidence * n
        weight += n
    return round(total / weight, 4) if weight else None
