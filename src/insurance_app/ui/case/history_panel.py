"""Reiter „Verlauf“: lokale Änderungshistorie des Vorgangs (wer, was, wann)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.models import HistoryEntry
from ...core.service import AppService
from .. import theme
from ..widgets.common import muted
from .common import error_text
from .controller import CaseController
from .formatting import action_text, actor_text, format_local_time

COLUMNS = ("Zeit", "Wer", "Aktion", "Feld", "Status alt → neu", "Quelle / Detail")


def status_change(entry: HistoryEntry) -> str:
    if entry.new_status is None and entry.old_status is None:
        return ""
    old = theme.status_text(entry.old_status) if entry.old_status else "–"
    new = theme.status_text(entry.new_status) if entry.new_status else "–"
    return f"{old} → {new}"


class HistoryPanel(QWidget):
    def __init__(self, service: AppService, controller: CaseController) -> None:
        super().__init__()
        self.service = service
        self.controller = controller
        self.dirty = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.addWidget(
            muted(
                "Der Verlauf zeigt jede Erkennung und jede Ihrer Entscheidungen. "
                "Er wird nur lokal auf diesem Gerät gespeichert und nie übertragen."
            )
        )
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setAccessibleName("Verlauf des Vorgangs")
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.error = muted("")
        self.error.hide()
        layout.addWidget(self.error)

    def render(self) -> None:
        self.dirty = False
        case = self.controller.case
        if case is None:
            self.table.setRowCount(0)
            return
        try:
            entries = self.service.case_history(case.id)
        except Exception as exc:  # noqa: BLE001
            self.error.setText(f"Verlauf nicht lesbar: {error_text(exc)}")
            self.error.show()
            entries = []
        else:
            self.error.hide()
        entries = list(reversed(entries))  # neueste zuerst
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            field = self.service.field_label(e.field_key) if e.field_key else ""
            detail = " · ".join(x for x in (e.source, e.detail) if x)
            cells = (
                format_local_time(e.timestamp),
                actor_text(e.actor),
                action_text(e.action),
                field,
                status_change(e),
                detail,
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, col, item)

    def actions_shown(self) -> list[str]:
        return [self.table.item(r, 2).text() for r in range(self.table.rowCount())]
