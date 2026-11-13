"""Standardisierte Dateinamen für Exporte – ohne personenbezogene Daten.

Muster: ``JJJJ-MM-TT_<VERSICHERER>_<FORMULAR>_<Kurztitel>_<Vorgangs-ID>.pdf``,
z. B. ``2026-11-13_SWISSLIFE_SL13065_Freigabe_V20261113-AB12CD.pdf``.
Erlaubt sind nur ``A–Z a–z 0–9 _ -``; Umlaute werden umschrieben (ä -> ae).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

__all__ = ["build_export_filename", "sanitize_part", "unique_path"]

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
_MAX_PART = 40
_MAX_UNIQUE = 10_000


def sanitize_part(text: str, fallback: str = "X", max_len: int = _MAX_PART) -> str:
    """Einen Namensteil auf ``[A-Za-z0-9-]`` reduzieren (Leerzeichen -> ``-``)."""
    t = (text or "").translate(_UMLAUTS)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[\s_]+", "-", t.strip())
    t = re.sub(r"[^A-Za-z0-9-]", "", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    t = t[:max_len].strip("-")
    return t or fallback


def build_export_filename(
    created: date,
    insurer_code: str,
    form_number: str,
    short_title: str,
    case_id: str,
) -> str:
    """Exportdateiname nach dem Standardmuster (siehe Modul-Dokumentation)."""
    parts = [
        created.isoformat(),
        sanitize_part(insurer_code, "VERSICHERER").upper(),
        sanitize_part(form_number, "FORMULAR").upper(),
        sanitize_part(short_title, "Vorlage"),
        sanitize_part(case_id, "VORGANG"),
    ]
    return "_".join(parts) + ".pdf"


def unique_path(directory: Path, name: str) -> Path:
    """Freien Pfad liefern: ``name``, sonst ``name_2``, ``name_3`` … (vor der Endung)."""
    directory = Path(directory)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    for counter in range(2, _MAX_UNIQUE):
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError("Kein freier Dateiname im Exportordner gefunden.")
