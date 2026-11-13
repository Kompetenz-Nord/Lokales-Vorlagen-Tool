"""Checkliste offener Punkte (blockierende zuerst; ✖ offen / ⚠ Hinweis / ℹ Info)."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ...core.models import Severity
from ...templates.resolver import ChecklistItem
from .. import theme
from ..widgets.common import section_title

READY_TEXT = "✔ Alle Pflichtangaben vollständig"


def item_prefix(item: ChecklistItem) -> tuple[str, str]:
    """(Präfix, Farbe) – Status immer als Symbol + Wort."""
    if item.blocking:
        return "✖ offen", theme.ERROR
    if item.severity in (Severity.ERROR, Severity.WARNING):
        return "⚠ Hinweis", theme.WARN
    return "ℹ Info", theme.INFO


def sort_items(items: Iterable[ChecklistItem]) -> list[ChecklistItem]:
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    return sorted(items, key=lambda i: (0 if i.blocking else 1, order.get(i.severity, 3)))


class ChecklistPanel(QWidget):
    """Liste der Checklistenpunkte; Doppelklick meldet den Feldschlüssel (``field_activated``)."""

    field_activated = Signal(str)

    def __init__(self, title: str = "Checkliste", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.title = section_title(title)
        layout.addWidget(self.title)
        self.ready_label = QLabel(READY_TEXT)
        self.ready_label.setStyleSheet(
            f"color: {theme.OK}; background: {theme.HIGHLIGHT_BG}; border: 1px solid {theme.HIGHLIGHT}; border-radius: 8px;"
            " padding: 6px 10px; font-weight: 700;"
        )
        self.ready_label.setAccessibleName("Alle Pflichtangaben vollständig")
        self.ready_label.hide()
        layout.addWidget(self.ready_label)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.list = QListWidget()
        self.list.setAccessibleName(title)
        self.list.setWordWrap(True)
        self.list.itemActivated.connect(self._activated)
        self.list.itemDoubleClicked.connect(self._activated)
        layout.addWidget(self.list, 1)
        self.items: list[ChecklistItem] = []

    def set_items(
        self, items: Iterable[ChecklistItem] | None, *, ready: bool | None, empty_text: str = ""
    ) -> None:
        self.list.clear()
        self.items = sort_items(items or [])
        blocking = sum(1 for i in self.items if i.blocking)
        self.ready_label.setVisible(bool(ready))
        if ready is None:
            self.summary.setText(empty_text)
        elif blocking:
            self.summary.setText(
                f"✖ Noch {blocking} offene Pflichtpunkt(e) – die PDF kann noch nicht erzeugt werden."
            )
            self.summary.setStyleSheet(f"color: {theme.ERROR}; font-weight: 700;")
        else:
            self.summary.setText("Keine offenen Pflichtpunkte.")
            self.summary.setStyleSheet(f"color: {theme.OK};")
        if ready is None:
            self.summary.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        for ci in self.items:
            prefix, color = item_prefix(ci)
            it = QListWidgetItem(f"{prefix}: {ci.message}")
            it.setForeground(QColor(color))
            it.setData(Qt.ItemDataRole.UserRole, ci.field_key or "")
            tip = "Doppelklick: zum Feld springen" if ci.field_key else ""
            it.setToolTip((ci.message + ("\n" + tip if tip else "")).strip())
            self.list.addItem(it)

    def texts(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def _activated(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self.field_activated.emit(str(key))
