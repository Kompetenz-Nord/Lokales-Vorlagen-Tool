"""Dateitypen anhand des Inhalts (Magic Bytes) erkennen – nie anhand der Endung."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

FileType = Literal["pdf", "png", "jpeg", "tiff", "unknown"]

#: Erlaubte Dateiendungen (Kleinbuchstaben).
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"})
#: Größte zulässige Datei (200 MiB).
MAX_FILE_SIZE_BYTES: int = 200 * 1024 * 1024

#: Passende Endungen je erkanntem Typ.
EXTENSIONS_BY_TYPE: dict[str, frozenset[str]] = {
    "pdf": frozenset({".pdf"}),
    "png": frozenset({".png"}),
    "jpeg": frozenset({".jpg", ".jpeg"}),
    "tiff": frozenset({".tif", ".tiff"}),
}
#: Anzeigenamen für Meldungen.
FILE_TYPE_LABELS: dict[str, str] = {"pdf": "PDF", "png": "PNG", "jpeg": "JPEG", "tiff": "TIFF"}

# Laut PDF-Referenz darf "%PDF-" innerhalb der ersten 1024 Bytes stehen.
_PDF_SEARCH_WINDOW = 1024
_HEAD_BYTES = 2048


def detect_file_type_from_bytes(head: bytes) -> FileType:
    """Typ aus den ersten Bytes einer Datei bestimmen."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "tiff"
    if b"%PDF-" in head[:_PDF_SEARCH_WINDOW]:
        return "pdf"
    return "unknown"


def detect_file_type(path: Path) -> FileType:
    """Typ einer Datei anhand der Magic Bytes; ``"unknown"`` bei unbekanntem Inhalt oder Lesefehler."""
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return "unknown"
    return detect_file_type_from_bytes(head)
