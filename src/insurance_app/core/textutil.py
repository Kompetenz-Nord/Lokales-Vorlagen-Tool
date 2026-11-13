"""Gemeinsame Text-Hilfsfunktionen (Normalisierung, deutsche Datumsangaben).

Bewusst ohne Abhängigkeiten zu anderen Fachmodulen, damit OCR, Klassifikation
und Extraktion dieselben Regeln verwenden.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

GERMAN_MONTHS: dict[str, int] = {
    "januar": 1,
    "jan": 1,
    "jänner": 1,
    "februar": 2,
    "feb": 2,
    "märz": 3,
    "maerz": 3,
    "mär": 3,
    "mrz": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}

_UMLAUT_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})

_WS = re.compile(r"\s+")


def normalize_ws(text: str) -> str:
    """Mehrfache Leerzeichen/Zeilenumbrüche zu einem Leerzeichen zusammenfassen."""
    return _WS.sub(" ", text.replace(" ", " ")).strip()


def fold(text: str) -> str:
    """Vergleichsform: Kleinschreibung, Umlaute transliteriert, Akzente entfernt, Whitespace normalisiert."""
    t = normalize_ws(text).translate(_UMLAUT_FOLD).casefold()
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def _year(y: int) -> int:
    """Zweistellige Jahre: 00–69 -> 2000–2069, 70–99 -> 1970–1999."""
    if y < 100:
        return 2000 + y if y < 70 else 1900 + y
    return y


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        if not 1900 <= y <= 2200:
            return None
        return date(y, m, d)
    except ValueError:
        return None


_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})\s?[./]\s?(\d{1,2})\s?[./]\s?(\d{4}|\d{2})(?!\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_LONG_DATE = re.compile(r"(?<!\d)(\d{1,2})\.?\s*([A-Za-zÄÖÜäöüß]{3,10})\.?\s+(\d{4})(?!\d)")


def parse_german_date(text: str) -> date | None:
    """Erstes eindeutiges Datum aus einem kurzen Text lesen.

    Unterstützt ``01.02.2022``, ``1.2.22``, ``01/02/2022``, ``2022-02-01`` und
    ``13. März 2025``. Gibt ``None`` zurück, wenn kein gültiges Datum enthalten ist.
    """
    found = find_dates(text)
    return found[0][0] if found else None


def find_dates(text: str) -> list[tuple[date, tuple[int, int]]]:
    """Alle Datumsangaben mit Fundstelle (Start, Ende), in Textreihenfolge."""
    results: list[tuple[date, tuple[int, int]]] = []
    for m in _NUMERIC_DATE.finditer(text):
        d = _safe_date(_year(int(m.group(3))), int(m.group(2)), int(m.group(1)))
        if d:
            results.append((d, m.span()))
    for m in _ISO_DATE.finditer(text):
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            results.append((d, m.span()))
    for m in _LONG_DATE.finditer(text):
        month = GERMAN_MONTHS.get(m.group(2).casefold().rstrip("."))
        if month:
            d = _safe_date(int(m.group(3)), month, int(m.group(1)))
            if d:
                results.append((d, m.span()))
    # Überlappungen entfernen (z. B. Teilstücke), Reihenfolge nach Position
    results.sort(key=lambda r: r[1][0])
    dedup: list[tuple[date, tuple[int, int]]] = []
    for r in results:
        if dedup and r[1][0] < dedup[-1][1][1]:
            continue
        dedup.append(r)
    return dedup


_MONTH_NUMERIC = re.compile(r"(?<![\d.])(\d{1,2})\s?[./]\s?(\d{4})(?!\d)")
_MONTH_LONG = re.compile(r"([A-Za-zÄÖÜäöüß]{3,10})\.?\s+(\d{4})(?!\d)")


def parse_month(text: str) -> tuple[int, int] | None:
    """Monat/Jahr lesen: ``03.2025``, ``3/2025``, ``März 2025`` -> (2025, 3)."""
    m = _MONTH_NUMERIC.search(text)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1900 <= year <= 2200:
            return year, month
    m = _MONTH_LONG.search(text)
    if m:
        month = GERMAN_MONTHS.get(m.group(1).casefold())
        year = int(m.group(2))
        if month and 1900 <= year <= 2200:
            return year, month
    return None


def format_date_de(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year:04d}"


def truncate(text: str, limit: int = 160) -> str:
    t = normalize_ws(text)
    return t if len(t) <= limit else t[: limit - 1] + "…"
