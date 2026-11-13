"""Gemeinsame Texte und Hilfen der Kundenmappen (keine personenbezogenen Daten)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ...core.service import BatchItemStatus
from .. import dialogs, theme

BUNDLE_EXPLANATION = (
    "Legen Sie alle Dokumente der Kundin bzw. des Kunden ab – auch von verschiedenen Gesellschaften. "
    "Die App liest sie nacheinander und legt je Vertrag einen Vorgang an. Danach wählen Sie je Vertrag "
    "den gewünschten Vorgang (Anwenderwunsch), prüfen die Daten und erzeugen alle fertigen Dokumente "
    "in einem Schritt."
)

GENERATE_CONFIRM_TEXT = (
    "Die App erzeugt Entwürfe zur Prüfung. Es wird nichts versendet, keine Unterschrift erzeugt und "
    "nichts gekündigt. Alle bereiten Vorgänge jetzt erzeugen?"
)

TITLE_LABEL = "Name der Kundenmappe (z. B. „Kundin Müller – Optimierung“):"
NEW_BUNDLE_TITLE = "Neue Kundenmappe"
DEFAULT_TITLE = "Kundenmappe"

# Status der Warteschlange -> (Symbol, Farbe). Status nie nur über Farbe zeigen.
BATCH_STATUS_STYLE: dict[BatchItemStatus, tuple[str, str]] = {
    BatchItemStatus.WAITING: ("○", theme.TEXT_MUTED),
    BatchItemStatus.RUNNING: ("◐", theme.INFO),
    BatchItemStatus.DONE: ("✔", theme.OK),
    BatchItemStatus.NEEDS_PASSWORD: ("⚠", theme.WARN),
    BatchItemStatus.REJECTED: ("✖", theme.ERROR),
    BatchItemStatus.CANCELLED: ("–", theme.TEXT_MUTED),
}


def batch_status_text(status: BatchItemStatus) -> str:
    """Status als Symbol + Text, z. B. ``✔ Fertig``."""
    sym, _color = BATCH_STATUS_STYLE[status]
    return f"{sym} {status.value}"


def batch_status_color(status: BatchItemStatus) -> str:
    return BATCH_STATUS_STYLE[status][1]


def contracts_text(count: int) -> str:
    return "1 Vertrag" if count == 1 else f"{count} Verträge"


def open_points_text(count: int) -> str:
    return "✖ 1 offener Punkt" if count == 1 else f"✖ {count} offene Punkte"


def ask_bundle_title(parent: QWidget | None, title: str = NEW_BUNDLE_TITLE, value: str = "") -> str | None:
    """Namen einer Kundenmappe erfragen (``None`` = abgebrochen; leer -> Standardname)."""
    text = dialogs.ask_text(parent, title, TITLE_LABEL, value)
    if text is None:
        return None
    return text.strip() or DEFAULT_TITLE
