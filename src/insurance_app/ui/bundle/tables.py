"""Tabellen der Seite „Kundenmappen“: Warteschlange, Verträge der Mappe, Ergebnis der Erzeugung."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ...core.service import BatchItem, BundleCaseRow, BundleExportRow, TemplateChoice
from .. import theme
from .common import batch_status_color, batch_status_text, open_points_text

QUEUE_COLUMNS = ("Datei", "Status", "Dokumenttyp", "Versicherer", "Zuordnung", "Hinweis")
CONTRACT_COLUMNS = (
    "Vorgang",
    "Versicherer",
    "Vertrag",
    "Dokumente",
    "Anwenderwunsch",
    "Status",
    "Erzeugt",
    "Aktion",
)
RESULT_COLUMNS = ("Vorgang", "Ergebnis")

NO_TEMPLATE_TEXT = "– bitte wählen –"
READY_TEXT = "✔ bereit"
NO_TEMPLATE_STATUS = "– keine Vorlage"
OPEN_CASE_TEXT = "Prüfen && bearbeiten"  # && = wörtliches &


def _item(text: str, tooltip: str = "", color: str | None = None, data: object = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if tooltip:
        item.setToolTip(tooltip)
    if color:
        item.setForeground(QBrush(QColor(color)))
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    return item


def _setup(table: QTableWidget, columns: tuple[str, ...], name: str) -> None:
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels(list(columns))
    table.setAccessibleName(name)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setWordWrap(True)
    table.setAlternatingRowColors(True)


def fit_height(table: QTableWidget, min_rows: int = 2, max_rows: int = 12) -> None:
    """Tabelle so hoch wie ihr Inhalt (die Seite selbst scrollt)."""
    rows = table.rowCount()
    height = table.horizontalHeader().height() + 2 * table.frameWidth() + 4
    for r in range(min(rows, max_rows)):
        height += table.rowHeight(r)
    height += max(0, min_rows - rows) * table.verticalHeader().defaultSectionSize()
    table.setFixedHeight(height)


# ---------------------------------------------------------------------------
# Warteschlange
# ---------------------------------------------------------------------------


class QueueTable(QTableWidget):
    """Eine Zeile je Datei: Datei | Status | Dokumenttyp | Versicherer | Zuordnung | Hinweis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(QUEUE_COLUMNS), parent)
        _setup(self, QUEUE_COLUMNS, "Warteschlange der Kundenmappe")
        self.verticalHeader().setDefaultSectionSize(32)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for col, width in enumerate((230, 165, 190, 200, 250)):
            self.setColumnWidth(col, width)
        self.horizontalHeaderItem(4).setToolTip("Vorgang (Vertrag), dem die Datei zugeordnet wurde")
        fit_height(self)

    def set_items(self, items: list[BatchItem], titles: dict[str, str]) -> None:
        self.setRowCount(len(items))
        for row, it in enumerate(items):
            status = batch_status_text(it.status)
            assigned = titles.get(it.case_id or "", it.case_id or "–") if it.case_id else "–"
            cells = (
                _item(it.file_name, it.file_name, data=it.path),
                _item(status, it.message or status, batch_status_color(it.status)),
                _item(it.doc_type or "–"),
                _item(it.insurer or "–"),
                _item(assigned, assigned),
                _item(it.message or "", it.message),
            )
            for col, cell in enumerate(cells):
                self.setItem(row, col, cell)
        fit_height(self)

    def row_texts(self, column: int) -> list[str]:
        return [self.item(r, column).text() for r in range(self.rowCount())]


# ---------------------------------------------------------------------------
# Verträge der Mappe
# ---------------------------------------------------------------------------


class ContractsTable(QTableWidget):
    """Ein Vertrag = ein Vorgang = ein Anwenderwunsch (Vorlage)."""

    template_chosen = Signal(str, object)  # (Vorgangs-ID, Katalog-ID oder None)
    open_requested = Signal(str)  # Vorgangs-ID

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(CONTRACT_COLUMNS), parent)
        _setup(self, CONTRACT_COLUMNS, "Verträge in dieser Kundenmappe")
        self.verticalHeader().setDefaultSectionSize(46)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 190), (2, 80), (3, 200), (4, 270), (5, 150), (6, 170), (7, 180)):
            self.setColumnWidth(col, width)
        self.horizontalHeaderItem(2).setToolTip("Nur die letzten Stellen der Vertragsnummer")
        self.horizontalHeaderItem(4).setToolTip(
            "Was die Kundin bzw. der Kunde für diesen Vertrag wünscht (Vorlage bzw. Brief)"
        )
        self.combos: dict[str, QComboBox] = {}
        self.open_buttons: dict[str, QPushButton] = {}
        self.rows: list[BundleCaseRow] = []
        fit_height(self)

    def set_rows(self, rows: list[BundleCaseRow], choices: list[TemplateChoice]) -> None:
        self.rows = list(rows)
        self.combos = {}
        self.open_buttons = {}
        self.setRowCount(0)
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.setItem(
                r,
                0,
                _item(
                    row.title,
                    f"{row.title}\nVorgangs-ID: {row.case_id}\nStand: {row.stage_label}",
                    data=row.case_id,
                ),
            )
            self.setItem(r, 1, _item(row.insurer or "–", row.insurer or "Versicherer nicht erkannt"))
            self.setItem(r, 2, _item(row.contract_hint or "–", "Letzte Stellen der Vertragsnummer"))
            docs = ", ".join(row.doc_types) or "–"
            self.setItem(r, 3, _item(docs, f"{row.document_count} Dokument(e): {docs}"))
            self.setItem(r, 4, QTableWidgetItem(""))
            self.setCellWidget(r, 4, self._template_combo(row, choices))
            text, color, tip = self._status(row)
            self.setItem(r, 5, _item(text, tip, color))
            exported = row.exported_file or "–"
            self.setItem(r, 6, _item(exported, exported))
            self.setItem(r, 7, QTableWidgetItem(""))
            self.setCellWidget(r, 7, self._open_button(row))
        fit_height(self, max_rows=20)

    @staticmethod
    def _status(row: BundleCaseRow) -> tuple[str, str, str]:
        if not row.template_id:
            return NO_TEMPLATE_STATUS, theme.TEXT_MUTED, "Bitte zuerst den Anwenderwunsch (Vorlage) wählen."
        if row.ready:
            return READY_TEXT, theme.OK, "Alle Pflichtangaben sind geprüft – kann erzeugt werden."
        return (
            open_points_text(row.blocking_count),
            theme.ERROR,
            "Offene Pflichtpunkte – bitte mit „Prüfen & bearbeiten“ im Vorgang klären.",
        )

    def _template_combo(self, row: BundleCaseRow, choices: list[TemplateChoice]) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 2, 4, 2)
        combo = QComboBox()
        combo.setAccessibleName(f"Anwenderwunsch für {row.title}")
        combo.setToolTip("Gewünschten Vorgang (Vorlage) für diesen Vertrag wählen")
        combo.addItem(NO_TEMPLATE_TEXT, None)
        for c in choices:
            if not c.available:
                continue
            combo.addItem(c.title, c.catalog_id)
            tip = c.description or c.title
            if c.kind == "letter":
                tip += "\n(Kundenschreiben nach DIN 5008)"
            combo.setItemData(combo.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        if row.template_id:
            idx = combo.findData(row.template_id)
            if idx < 0:
                combo.addItem(f"{row.template_title or row.template_id} (nicht verfügbar)", row.template_id)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
        combo.activated.connect(
            lambda _i, cid=row.case_id, c=combo: self.template_chosen.emit(cid, c.currentData())
        )
        lay.addWidget(combo)
        self.combos[row.case_id] = combo
        return box

    def _open_button(self, row: BundleCaseRow) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 2, 4, 2)
        btn = QPushButton(OPEN_CASE_TEXT)
        btn.setAccessibleName(f"Prüfen und bearbeiten: {row.title}")
        btn.setToolTip("Vorgang öffnen: Daten prüfen, Auftragsdaten wählen, Vorschau ansehen")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, cid=row.case_id: self.open_requested.emit(cid))
        lay.addWidget(btn)
        self.open_buttons[row.case_id] = btn
        return box

    def status_texts(self) -> dict[str, str]:
        return {
            self.item(r, 0).data(Qt.ItemDataRole.UserRole): self.item(r, 5).text()
            for r in range(self.rowCount())
        }


# ---------------------------------------------------------------------------
# Ergebnis der Erzeugung
# ---------------------------------------------------------------------------


class ResultTable(QTableWidget):
    """Je Vorgang: ✔ Datei oder ✖ Grund."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(RESULT_COLUMNS), parent)
        _setup(self, RESULT_COLUMNS, "Ergebnis der Erzeugung")
        self.verticalHeader().setDefaultSectionSize(32)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(0, 420)
        fit_height(self, min_rows=1)

    def set_rows(self, rows: list[BundleExportRow]) -> None:
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.setItem(r, 0, _item(row.title, f"{row.title}\nVorgangs-ID: {row.case_id}", data=row.case_id))
            if row.ok and row.path is not None:
                text, color, tip = f"✔ {row.path.name}", theme.OK, str(row.path)
            else:
                text, color, tip = f"✖ {row.message}", theme.ERROR, row.message
            self.setItem(r, 1, _item(text, tip, color))
        fit_height(self, min_rows=1)

    def result_texts(self) -> list[str]:
        return [self.item(r, 1).text() for r in range(self.rowCount())]
