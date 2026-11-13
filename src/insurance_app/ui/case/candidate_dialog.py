"""Dialog „Werte vergleichen“ für Felder mit Konflikt.

Zeigt alle Fundstellen nebeneinander. Der fachlich vermutlich maßgebliche Wert ist nur
markiert („bitte selbst entscheiden“) – übernommen wird erst nach Klick auf
„Diesen Wert übernehmen“ (``service.choose_candidate``).
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Case
from ...core.fields import FIELD_REGISTRY
from ...core.service import AppService
from .. import theme
from ..widgets.common import muted, primary_button
from .common import error_text
from .formatting import (
    CONFIDENCE_TOOLTIP,
    candidate_display,
    confidence_text,
    currency_of,
    method_text,
    page_origin,
)

COLUMNS = ("Wert", "Datei", "Seite", "Textausschnitt", "Methode", "Sicherheit", "Hinweis")
SUGGESTION_HINT = "Hinweis: vermutlich maßgeblich (bitte selbst entscheiden)"


class CandidateDialog(QDialog):
    def __init__(self, service: AppService, case: Case, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.case = case
        self.key = key
        self.chosen_index: int | None = None
        fdef = FIELD_REGISTRY[key]
        fv = case.field(key)
        self.setWindowTitle(f"Werte vergleichen – {fdef.label}")
        self.setMinimumSize(900, 360)
        self.setModal(True)

        layout = QVBoxLayout(self)
        head = QLabel(f"Für „{fdef.label}“ wurden unterschiedliche Werte gefunden.")
        head.setStyleSheet("font-size: 12pt; font-weight: 700;")
        head.setWordWrap(True)
        layout.addWidget(head)
        layout.addWidget(
            muted(
                "Bitte prüfen Sie die Fundstellen und wählen Sie den richtigen Wert. "
                "Die App entscheidet nicht selbst – der Hinweis ist nur eine Orientierung."
            )
        )
        if fv.note:
            note = QLabel(f"ℹ {fv.note}")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {theme.INFO};")
            layout.addWidget(note)

        self.table = QTableWidget(len(fv.candidates), len(COLUMNS))
        self.table.setAccessibleName("Gefundene Werte im Vergleich")
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.horizontalHeaderItem(5).setToolTip(CONFIDENCE_TOOLTIP)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        currency = currency_of(case)
        for row, c in enumerate(fv.candidates):
            ev = c.evidence
            origin = page_origin(case, ev.document_id, ev.page)
            note_parts = []
            if fv.suggestion_index == row:
                note_parts.append(SUGGESTION_HINT)
            if c.note:
                note_parts.append(c.note)
            if c.effective_date:
                note_parts.append(f"gültig ab {c.effective_date:%d.%m.%Y}")
            elif c.document_date:
                note_parts.append(f"Dokument vom {c.document_date:%d.%m.%Y}")
            if c.normalized is None:
                note_parts.append("nicht eindeutig lesbar – bitte manuell eintragen")
            cells = [
                candidate_display(key, c, currency),
                ev.file_name,
                str(ev.page) if ev.page else "–",
                ev.quote or "",
                method_text(ev.method, origin),
                confidence_text(c.confidence).removeprefix("Sicherheit: "),
                "\n".join(note_parts),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 5:
                    item.setToolTip(CONFIDENCE_TOOLTIP)
                if fv.suggestion_index == row:
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.resizeRowsToContents()
        self.table.itemSelectionChanged.connect(self._update)
        self.table.cellDoubleClicked.connect(lambda *_: self.choose())
        layout.addWidget(self.table, 1)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.ERROR};")
        self.error.hide()
        layout.addWidget(self.error)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.clicked.connect(self.reject)
        self.choose_btn = primary_button("Diesen Wert übernehmen")
        self.choose_btn.setAccessibleName("Diesen Wert übernehmen")
        self.choose_btn.clicked.connect(self.choose)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.choose_btn)
        layout.addLayout(bar)
        self._update()

    def selected_row(self) -> int | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return min(rows) if rows else None

    def select_row(self, row: int) -> None:
        self.table.selectRow(row)

    def _update(self) -> None:
        self.choose_btn.setEnabled(self.selected_row() is not None)

    def choose(self) -> None:
        row = self.selected_row()
        if row is None:
            return
        try:
            self.service.choose_candidate(self.case, self.key, row)
        except Exception as exc:  # noqa: BLE001 – im Dialog verständlich anzeigen
            self.error.setText(f"✖ {error_text(exc)}")
            self.error.show()
            return
        self.chosen_index = row
        self.accept()


def compare_candidates(parent: QWidget | None, service: AppService, case: Case, key: str) -> bool:
    """Dialog modal anzeigen. ``True``, wenn ein Wert übernommen wurde."""
    dlg = CandidateDialog(service, case, key, parent)
    try:
        return dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen_index is not None
    finally:
        dlg.deleteLater()
