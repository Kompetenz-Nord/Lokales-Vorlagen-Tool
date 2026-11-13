"""Koordinaten-Overlay für Vorlagen OHNE Formularfelder.

Der Text wird mit der eingebetteten Schrift Liberation Sans (OFL, mit Umlauten)
in ein Rechteck auf die Seite geschrieben. Das verändert den Seiteninhalt und ist
deshalb nur erlaubt, wenn die PDF-Rechte Änderungen zulassen (oder die PDF nicht
verschlüsselt ist).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pymupdf

from insurance_app.pdf.inspector import read_permissions

__all__ = ["OVERLAY_FONT_NAME", "OverlayNotPermittedError", "apply_overlay"]

OVERLAY_FONT_NAME = "LibSans"

_ALIGN = {
    "left": pymupdf.TEXT_ALIGN_LEFT,
    "center": pymupdf.TEXT_ALIGN_CENTER,
    "right": pymupdf.TEXT_ALIGN_RIGHT,
}


class OverlayNotPermittedError(PermissionError):
    """Die PDF-Rechte erlauben keine Änderung des Seiteninhalts."""

    def __init__(self, message: str | None = None) -> None:
        msg = message or (
            "Diese Vorlage erlaubt laut PDF-Berechtigungen keine Änderungen am Seiteninhalt. "
            "Text kann nicht per Koordinaten eingesetzt werden."
        )
        super().__init__(msg)
        self.user_message = msg


def apply_overlay(
    doc: pymupdf.Document,
    page_number: int,
    rect: Sequence[float] | pymupdf.Rect,
    text: str,
    font_file: Path,
    base_size: float = 10.0,
    align: str | None = "left",
    min_size: float = 6.0,
    color: tuple[float, float, float] = (0, 0, 0),
) -> bool:
    """Text in ``rect`` (Seitenkoordinaten oben links, PDF-Punkte) einsetzen.

    Die Schrift wird schrittweise verkleinert, bis der Text vollständig passt.
    Gibt ``False`` zurück (und schreibt nichts), wenn der Text selbst bei
    ``min_size`` nicht passt. ``page_number`` ist 1-basiert.
    """
    perms = read_permissions(doc)
    if perms.encrypted and not perms.can_modify:
        raise OverlayNotPermittedError()
    if not 1 <= page_number <= doc.page_count:
        raise ValueError("Seitenzahl außerhalb der Vorlage")
    font_path = Path(font_file)
    if not font_path.is_file():
        raise FileNotFoundError("Schriftdatei für das Overlay fehlt")
    r = pymupdf.Rect(rect)
    if r.is_empty or r.is_infinite:
        raise ValueError("Ungültiges Rechteck für das Overlay")
    if not text.strip():
        return True
    page = doc[page_number - 1]
    rotate = page.rotation
    if rotate:
        # Rechteck aus der sichtbaren (gedrehten) Ansicht in Seitenkoordinaten umrechnen
        r = r * page.derotation_matrix
        r.normalize()
    align_code = _ALIGN.get((align or "left").lower(), pymupdf.TEXT_ALIGN_LEFT)
    size = _fitting_size(page, r, text, font_path, float(base_size), min_size, align_code, rotate)
    if size is None:
        return False
    rc = page.insert_textbox(
        r,
        text,
        fontsize=size,
        fontname=OVERLAY_FONT_NAME,
        fontfile=str(font_path),
        align=align_code,
        color=color,
        rotate=rotate,
    )
    return rc >= 0


def _fitting_size(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    text: str,
    font_path: Path,
    base_size: float,
    min_size: float,
    align_code: int,
    rotate: int,
) -> float | None:
    """Passende Schriftgröße auf einer Probeseite ermitteln (die Vorlage bleibt unberührt)."""
    scratch = pymupdf.open()
    try:
        probe = scratch.new_page(width=page.mediabox.width, height=page.mediabox.height)
        size = base_size
        while size >= min_size - 1e-9:
            rc = probe.insert_textbox(
                rect,
                text,
                fontsize=size,
                fontname=OVERLAY_FONT_NAME,
                fontfile=str(font_path),
                align=align_code,
                rotate=rotate,
            )
            if rc >= 0:
                return size
            size = round(size - 0.5, 2)
        return None
    finally:
        scratch.close()
