"""Kleine wiederverwendbare Bausteine für alle Seiten."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ...core.models import FieldStatus, Severity
from ..theme import SEVERITY_STYLE, STATUS_STYLE


def page_title(text: str) -> QLabel:
    lbl = QLabel(text, objectName="PageTitle")
    lbl.setAccessibleName(text)
    return lbl


def section_title(text: str) -> QLabel:
    return QLabel(text, objectName="SectionTitle")


def muted(text: str, wrap: bool = True) -> QLabel:
    lbl = QLabel(text, objectName="Muted")
    lbl.setWordWrap(wrap)
    return lbl


def primary_button(text: str, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty("primary", True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def danger_button(text: str, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty("danger", True)
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def badge(text: str, color: str, tooltip: str = "") -> QLabel:
    """Status-Etikett: Farbe UND Text (nie nur Farbe)."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {color}; border: 1px solid {color}; border-radius: 10px; padding: 2px 10px; font-weight: 700;"
    )
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl


def status_badge(status: FieldStatus) -> QLabel:
    sym, txt, color = STATUS_STYLE[status]
    return badge(f"{sym} {txt}", color)


def severity_label(severity: Severity, text: str) -> QLabel:
    sym, color = SEVERITY_STYLE[severity]
    lbl = QLabel(f"{sym}  {text}")
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {color};")
    return lbl


def card(parent: QWidget | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent, objectName="Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    return frame, layout
