"""Schriftmetriken für die Passprüfung von Feldinhalten.

Die Swiss-Life-Vorlagen verwenden in den Feldern „Arial“ (DA ``/Arial 9 Tf``).
PyMuPDF erzeugt die Darstellung mit der Standardschrift Helvetica, deren
Laufweiten praktisch identisch mit Arial sind. Für die Breitenberechnung
genügen daher die Helvetica-Metriken (Base-14, ohne eingebettete Schrift).

Die Zeichenabdeckung der Standardschriften entspricht WinAnsi (cp1252).
Zeichen außerhalb davon (z. B. „ő“ oder „Ł“) würden falsch oder gar nicht
dargestellt und werden deshalb vorab gemeldet.
"""

from __future__ import annotations

import math
import unicodedata
from pathlib import Path

import pymupdf

__all__ = [
    "DEFAULT_LINE_FACTOR",
    "fit_font_size",
    "fit_font_size_multiline",
    "font_file",
    "is_winansi_encodable",
    "text_width",
    "unsupported_chars",
    "wrap_text",
]

DEFAULT_LINE_FACTOR = 1.15  # Zeilenabstand relativ zur Schriftgröße (mehrzeilige Felder)


def text_width(text: str, font_size: float, fontname: str = "helv") -> float:
    """Breite eines Textes in PDF-Punkten (Base-14-Metriken, Standard Helvetica)."""
    if not text or font_size <= 0:
        return 0.0
    return float(pymupdf.get_text_length(text, fontname=fontname, fontsize=font_size))


def _floor_step(value: float, step: float = 0.1) -> float:
    return math.floor(value / step + 1e-9) * step


def fit_font_size(
    text: str,
    rect_width: float,
    base_size: float,
    min_size: float = 6.0,
    padding: float = 4.0,
) -> float | None:
    """Größte Schriftgröße ≤ ``base_size``, mit der ``text`` einzeilig in das Feld passt.

    ``padding`` ist der horizontale Gesamtrand (links + rechts). Gibt ``None``
    zurück, wenn der Text selbst bei ``min_size`` nicht passt.
    """
    if base_size <= 0:
        raise ValueError("base_size muss größer als 0 sein")
    available = rect_width - padding
    if not text:
        return base_size
    if available <= 0:
        return None
    width = text_width(text, base_size)
    if width <= available:
        return base_size
    size = _floor_step(base_size * available / width)
    # Rundungssicherheit: so lange verkleinern, bis es wirklich passt
    while size >= min_size and text_width(text, size) > available:
        size = round(size - 0.1, 1)
    if size < min_size:
        return None
    return round(size, 1)


def wrap_text(text: str, max_width: float, font_size: float) -> list[str]:
    """Text wortweise auf ``max_width`` umbrechen (harte Zeilenumbrüche bleiben erhalten).

    Ein einzelnes Wort, das breiter als die Zeile ist, steht allein in seiner Zeile
    (der Aufrufer erkennt das über :func:`text_width`).
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if text_width(candidate, font_size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def fit_font_size_multiline(
    text: str,
    rect_width: float,
    rect_height: float,
    base_size: float,
    min_size: float = 6.0,
    padding: float = 4.0,
    line_factor: float = DEFAULT_LINE_FACTOR,
) -> float | None:
    """Wie :func:`fit_font_size`, aber für mehrzeilige Felder mit Wortumbruch."""
    if base_size <= 0:
        raise ValueError("base_size muss größer als 0 sein")
    available_w = rect_width - padding
    available_h = rect_height - padding
    if not text.strip():
        return base_size
    if available_w <= 0 or available_h <= 0:
        return None
    size = base_size
    while size >= min_size:
        lines = wrap_text(text, available_w, size)
        widest = max(text_width(line, size) for line in lines)
        height = len(lines) * size * line_factor
        if widest <= available_w and height <= available_h:
            return round(size, 1)
        size = round(size - 0.25, 2)
    return None


def is_winansi_encodable(text: str) -> bool:
    """Lässt sich der Text mit den Standardschriften (WinAnsi/cp1252) darstellen?"""
    return not unsupported_chars(text)


def unsupported_chars(text: str) -> list[str]:
    """Nicht darstellbare Zeichen (eindeutig, in Reihenfolge des Auftretens).

    Steuerzeichen (außer Zeilenumbruch und Tabulator) gelten ebenfalls als
    nicht darstellbar.
    """
    seen: list[str] = []
    for ch in text:
        if ch in seen or ch in "\n\t":
            continue
        if unicodedata.category(ch) in ("Cc", "Cs", "Co", "Cn"):
            seen.append(ch)
            continue
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            seen.append(ch)
    return seen


def font_file(fonts_dir: Path, bold: bool = False) -> Path:
    """Pfad der mitgelieferten Liberation-Sans-Schrift (für Overlay-Text)."""
    name = "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"
    path = fonts_dir / name
    if not path.is_file():
        raise FileNotFoundError(f"Schriftdatei fehlt: {name}")
    return path
