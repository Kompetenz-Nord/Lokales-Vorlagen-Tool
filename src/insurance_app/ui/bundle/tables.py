"""Tabellen der Seite „Kundenmappen“: Warteschlange, Verträge der Mappe, Ergebnis der Erzeugung."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
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
from shiboken6 import isValid

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


def short_title(title: str, bundle_title: str | None) -> str:
    """Vorgangstitel ohne vorangestellten Mappennamen („Mappe: Versicherer (Vertrag …1234)“)."""
    if bundle_title and title.startswith(f"{bundle_title}: "):
        return title[len(bundle_title) + 2 :] or title
    return title


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
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


class _FitTable(QTableWidget):
    """Tabelle, die ihre Zeilenhöhen an umbrochenen Text anpasst und so hoch wie ihr Inhalt ist."""

    MIN_ROW = 32
    MIN_ROWS = 2
    MAX_ROWS = 14

    def __init__(self, columns: tuple[str, ...], name: str, parent: QWidget | None = None) -> None:
        super().__init__(0, len(columns), parent)
        _setup(self, columns, name)
        self.verticalHeader().setDefaultSectionSize(self.MIN_ROW)
        self._fit_pending = False
        self.horizontalHeader().sectionResized.connect(lambda *_: self.schedule_fit())

    def refit(self) -> None:
        self._fit_pending = False
        if not isValid(self):
            return
        self.resizeRowsToContents()
        for row in range(self.rowCount()):
            self.setRowHeight(row, max(self.rowHeight(row) + 6, self.MIN_ROW))
        fit_height(self, self.MIN_ROWS, self.MAX_ROWS)

    def schedule_fit(self) -> None:
        if not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self, self.refit)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        super().resizeEvent(event)
        self.schedule_fit()


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


class QueueTable(_FitTable):
    """Eine Zeile je Datei: Datei | Status | Dokumenttyp | Versicherer | Zuordnung | Hinweis."""

    MAX_ROWS = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(QUEUE_COLUMNS, "Warteschlange der Kundenmappe", parent)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for col, width in ((0, 205), (1, 145), (2, 150), (3, 165), (5, 160)):
            self.setColumnWidth(col, width)
        self.horizontalHeaderItem(4).setToolTip("Vorgang (Vertrag), dem die Datei zugeordnet wurde")
        self.refit()

    def set_items(self, items: list[BatchItem], titles: dict[str, str]) -> None:
        self.setRowCount(len(items))
        for row, it in enumerate(items):
            status = batch_status_text(it.status)
            assigned = titles.get(it.case_id, it.case_id) if it.case_id else "–"
            note = it.message or ""
            if it.case_id and note.startswith("Zugeordnet:"):
                note = ""  # steht bereits in „Zuordnung“
            cells = (
                _item(it.file_name, it.file_name, data=it.path),
                _item(status, it.message or status, batch_status_color(it.status)),
                _item(it.doc_type or "–"),
                _item(it.insurer or "–"),
                _item(assigned, assigned),
                _item(note, it.message),
            )
            for col, cell in enumerate(cells):
                self.setItem(row, col, cell)
        self.refit()

    def row_texts(self, column: int) -> list[str]:
        return [self.item(r, column).text() for r in range(self.rowCount())]


# ---------------------------------------------------------------------------
# Verträge der Mappe
# ---------------------------------------------------------------------------


class ContractsTable(_FitTable):
    """Ein Vertrag = ein Vorgang = ein Anwenderwunsch (Vorlage)."""

    template_chosen = Signal(str, object)  # (Vorgangs-ID, Katalog-ID oder None)
    open_requested = Signal(str)  # Vorgangs-ID

    MIN_ROW = 46
    MAX_ROWS = 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(CONTRACT_COLUMNS, "Verträge in dieser Kundenmappe", parent)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col in (0, 1, 3):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for col, width in ((2, 66), (4, 195), (5, 110), (6, 95), (7, 178)):
            self.setColumnWidth(col, width)
        self.horizontalHeaderItem(2).setToolTip("Nur die letzten Stellen der Vertragsnummer")
        self.horizontalHeaderItem(4).setToolTip(
            "Was die Kundin bzw. der Kunde für diesen Vertrag wünscht (Vorlage bzw. Brief)"
        )
        self.combos: dict[str, QComboBox] = {}
        self.open_buttons: dict[str, QPushButton] = {}
        self.rows: list[BundleCaseRow] = []
        self.bundle_title: str | None = None
        self.refit()

    def set_rows(
        self, rows: list[BundleCaseRow], choices: list[TemplateChoice], bundle_title: str | None = None
    ) -> None:
        self.bundle_title = bundle_title
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
                    short_title(row.title, bundle_title),
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
            if row.exported_file:
                self.setItem(r, 6, _item(f"✔ {row.exported_file}", row.exported_file, theme.OK))
            else:
                self.setItem(r, 6, _item("–", "Noch nicht erzeugt"))
            self.setItem(r, 7, QTableWidgetItem(""))
            self.setCellWidget(r, 7, self._open_button(row))
        self.refit()

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


class ResultTable(_FitTable):
    """Je Vorgang: ✔ Datei oder ✖ Grund."""

    MIN_ROWS = 1
    MAX_ROWS = 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(RESULT_COLUMNS, "Ergebnis der Erzeugung", parent)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(0, 380)
        self.refit()

    def set_rows(self, rows: list[BundleExportRow], bundle_title: str | None = None) -> None:
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            title = short_title(row.title, bundle_title)
            self.setItem(r, 0, _item(title, f"{row.title}\nVorgangs-ID: {row.case_id}", data=row.case_id))
            if row.ok and row.path is not None:
                text, color, tip = f"✔ {row.path.name}", theme.OK, str(row.path)
            else:
                text, color, tip = f"✖ {row.message}", theme.ERROR, row.message
            self.setItem(r, 1, _item(text, tip, color))
        self.refit()

    def result_texts(self) -> list[str]:
        return [self.item(r, 1).text() for r in range(self.rowCount())]
