"""Bewertung der Textqualität einer Seite (GOOD / LOW / EMPTY).

Regeln
------
* **EMPTY**: weniger als ``settings.min_text_chars_per_page`` Buchstaben/Ziffern.
* **LOW**, wenn mindestens eines gilt:
  - Anteil "Störzeichen" (Ersatzzeichen U+FFFD, Steuer-/Privatzeichen,
    typische OCR-Artefakte wie ``| ~ ^ ¦ { } \\``) über 12 % der
    sichtbaren Zeichen,
  - bei mindestens 8 Wörtern: weniger als 55 % "wortartige" Tokens
    (Wörter mit ≥ 2 Buchstaben und überwiegend Buchstaben, Zahlen/Datum/
    Betrag, übliche Einzelzeichen),
  - mittlere OCR-Konfidenz unter ``settings.ocr_low_confidence``.
* sonst **GOOD**.

Die Schwellen sind Erfahrungswerte, nicht kalibriert.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..core.models import PageQuality, TextLayerOrigin, Word
from ..core.settings import Settings

LOW_QUALITY_WARNING = "Niedrige Erkennungsqualität – bitte Werte manuell prüfen."
EMPTY_OCR_WARNING = "Auf dieser Seite wurde kein brauchbarer Text erkannt – Seite bitte manuell prüfen."

#: Anteil Störzeichen, ab dem die Seite als LOW gilt.
MAX_GARBAGE_RATIO = 0.12
#: Mindestanteil wortartiger Tokens.
MIN_PLAUSIBLE_SHARE = 0.55
#: Erst ab so vielen Tokens wird der Wortanteil bewertet.
MIN_TOKENS_FOR_SHARE = 8

_GARBAGE_SYMBOLS = frozenset("|~^¦{}\\`¬¤¨")
_SINGLE_OK = frozenset("-–—&§/+%€$£@#*()[].,:;!?\"'„“”‚‘’=<>°")
_NUMERIC_TOKEN = re.compile(r"^[(\[]?[+\-−]?[\d.,:/\-–%€$£]*\d[\d.,:/\-–%€$£]*[)\]]?[.,;:]?$")
_VOWELS = frozenset("aeiouyäöüàáâéèêíìîóòôúùû")


@dataclass(frozen=True)
class TextStats:
    """Kennzahlen einer Seite (für Qualität und Vergleich zweier Textquellen)."""

    meaningful_chars: int
    visible_chars: int
    garbage_ratio: float
    token_count: int
    plausible_share: float

    @property
    def score(self) -> float:
        """Vergleichswert 0..1 (höher = besser), unabhängig von Konfidenzen."""
        if self.meaningful_chars == 0:
            return 0.0
        return max(0.0, self.plausible_share * (1.0 - min(1.0, self.garbage_ratio)))


def _is_garbage_char(c: str) -> bool:
    if c in _GARBAGE_SYMBOLS or c == "�":
        return True
    category = unicodedata.category(c)
    return category in ("Co", "Cn", "Cs") or (category == "Cc" and c not in "\n\r\t")


def _is_plausible_token(token: str) -> bool:
    if len(token) == 1:
        return token.isalnum() or token in _SINGLE_OK
    if _NUMERIC_TOKEN.match(token):
        return True
    core = token.strip(".,:;!?\"'„“”‚‘’()[]")
    if not core:
        return False
    letters = sum(1 for c in core if c.isalpha())
    if letters < 2:
        # z. B. "Nr.1", "§5" – zählt, wenn überwiegend Ziffern/Buchstaben
        return sum(1 for c in core if c.isalnum()) >= max(2, len(core) - 1)
    if letters / len(core) < 0.6:
        return False
    if len(core) > 4 and not any(c in _VOWELS for c in core.casefold()):
        return False
    return not any(_is_garbage_char(c) for c in core)


def text_statistics(text: str, words: list[Word] | None = None) -> TextStats:
    """Kennzahlen aus Text (und – falls vorhanden – Wortliste) berechnen."""
    visible = [c for c in text if not c.isspace()]
    meaningful = sum(1 for c in visible if c.isalnum())
    garbage = sum(1 for c in visible if _is_garbage_char(c))
    tokens = [w.text for w in words if w.text.strip()] if words else text.split()
    plausible = sum(1 for t in tokens if _is_plausible_token(t))
    return TextStats(
        meaningful_chars=meaningful,
        visible_chars=len(visible),
        garbage_ratio=garbage / len(visible) if visible else 0.0,
        token_count=len(tokens),
        plausible_share=plausible / len(tokens) if tokens else 0.0,
    )


def assess_text_quality(
    text: str,
    words: list[Word],
    origin: TextLayerOrigin,
    mean_confidence: float | None,
    settings: Settings,
) -> tuple[PageQuality, list[str]]:
    """Qualität einer Seite bestimmen; liefert (Qualität, Warnungen auf Deutsch)."""
    stats = text_statistics(text, words)
    if stats.meaningful_chars < settings.min_text_chars_per_page:
        warnings = [EMPTY_OCR_WARNING] if origin == TextLayerOrigin.OCR else []
        return PageQuality.EMPTY, warnings
    low = stats.garbage_ratio > MAX_GARBAGE_RATIO
    if stats.token_count >= MIN_TOKENS_FOR_SHARE and stats.plausible_share < MIN_PLAUSIBLE_SHARE:
        low = True
    if mean_confidence is not None and mean_confidence < settings.ocr_low_confidence:
        low = True
    if low:
        return PageQuality.LOW, [LOW_QUALITY_WARNING]
    return PageQuality.GOOD, []
