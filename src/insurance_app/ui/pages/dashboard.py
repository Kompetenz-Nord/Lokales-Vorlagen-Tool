"""Dashboard „Vorgänge“: Dateien ablegen, Musterfall laden, letzte Vorgänge suchen und verwalten.

Konstruktor laut Hauptfenster: ``DashboardPage(service, open_case)``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.case import CaseSummary
from ...core.service import AppService
from .. import dialogs, theme, workers
from ..case.common import error_text
from ..case.drop_area import DropArea
from ..case.formatting import format_local_time, stage_text
from ..case.importing import add_files, local_files
from ..widgets.common import danger_button, muted, page_title, primary_button, section_title
from .case_view import consume_processing_request, request_processing

log = logging.getLogger(__name__)

COLUMNS = ("Datum", "Titel", "Stufe", "Vorlage", "Dokumente", "Offene Punkte")

DELETE_WARNING = (
    "Den Vorgang „{title}“ wirklich löschen?\n\n"
    "Dabei werden alle lokal gespeicherten Dokumente, erkannten Daten und der Verlauf dieses Vorgangs "
    "unwiderruflich gelöscht. Das kann nicht rückgängig gemacht werden.\n\n"
    "Bereits erzeugte PDFs im Exportordner bleiben erhalten."
)


class DashboardPage(QWidget):
    def __init__(self, service: AppService, open_case: Callable[[str], None]) -> None:
        super().__init__()
        self.service = service
        self._open_case = open_case
        self._busy = False
        self._summaries: list[CaseSummary] = []
        self._titles: dict[str, str] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(page_title("Vorgänge"), 1)
        local = muted(theme.LOCAL_ONLY_TEXT, wrap=False)
        local.setToolTip(
            "Alle Dateien und Ergebnisse bleiben auf diesem Gerät. Es gibt keine Cloud-KI und keine Telemetrie."
        )
        local.setAccessibleName("Lokale Verarbeitung – keine Cloud-Übertragung")
        head.addWidget(local)
        layout.addLayout(head)
        layout.addWidget(
            muted(
                "Legen Sie Versicherungsunterlagen ab. Die App liest sie lokal, zeigt jeden erkannten Wert mit "
                "Fundstelle und füllt erst nach Ihrer Prüfung die Originalvorlage aus."
            )
        )

        # --- Ablagefläche -------------------------------------------------
        self.drop_area = DropArea()
        self.drop_area.setAccessibleName("Ablagefläche für Dokumente")
        self.drop_area.setMinimumHeight(150)
        self.drop_area.files_dropped.connect(self.import_files)
        dv = QVBoxLayout(self.drop_area)
        dv.setContentsMargins(24, 18, 24, 18)
        dv.setSpacing(8)
        big = QLabel("📥  Police oder Nachtrag hier ablegen")
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big.setStyleSheet("font-size: 14pt; font-weight: 700; background: transparent; border: none;")
        dv.addWidget(big)
        sub = QLabel(
            "PDF, PNG, JPG oder TIFF – auch mehrere Dateien (z. B. Police, Nachtrag, Standmitteilung, "
            "Portal-Screenshot). Die Dateien werden nur als Kopie in der lokalen Ablage gespeichert."
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("background: transparent; border: none;")
        dv.addWidget(sub)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.choose_btn = primary_button("Dateien auswählen …", "Dateien von diesem Computer auswählen")
        self.choose_btn.setAccessibleName("Dateien auswählen")
        self.choose_btn.clicked.connect(self.choose_files)
        btns.addWidget(self.choose_btn)
        self.sample_btn = QPushButton("Musterfall laden (synthetisch)")
        self.sample_btn.setAccessibleName("Musterfall laden")
        self.sample_btn.setToolTip(
            "Legt einen Vorgang mit erfundenen Musterdokumenten an („MUSTER – KEINE ECHTDATEN“). "
            "Gut zum Ausprobieren."
        )
        self.sample_btn.clicked.connect(self.load_sample)
        btns.addWidget(self.sample_btn)
        btns.addStretch(1)
        dv.addLayout(btns)
        layout.addWidget(self.drop_area)

        # --- Liste der Vorgänge -------------------------------------------
        layout.addWidget(section_title("Zuletzt bearbeitet"))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen: Titel, Vorgangs-ID, Vertragsnummer oder Name …")
        self.search.setAccessibleName("Vorgänge durchsuchen")
        self.search.setToolTip("Die Suche läuft nur lokal auf diesem Gerät.")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.refresh())
        filters.addWidget(self.search, 1)
        self.show_archived = QCheckBox("Archivierte anzeigen")
        self.show_archived.toggled.connect(lambda _c: self.refresh())
        filters.addWidget(self.show_archived)
        layout.addLayout(filters)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setAccessibleName("Liste der Vorgänge")
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.horizontalHeaderItem(2).setToolTip(
            "Bis zu welchem der 9 Arbeitsschritte der Vorgang gekommen ist"
        )
        self.table.horizontalHeaderItem(5).setToolTip(
            "Erkannte, noch nicht bestätigte Werte sowie Konflikte und unklare Angaben"
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.open_selected())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 1)
        self.empty_label = muted(
            "Noch keine Vorgänge. Legen Sie oben Dateien ab oder laden Sie den Musterfall."
        )
        layout.addWidget(self.empty_label)

        actions = QHBoxLayout()
        self.open_btn = primary_button("Öffnen", "Ausgewählten Vorgang öffnen (auch per Doppelklick)")
        self.open_btn.clicked.connect(self.open_selected)
        self.rename_btn = QPushButton("Umbenennen …")
        self.rename_btn.setAccessibleName("Umbenennen")
        self.rename_btn.clicked.connect(self.rename_selected)
        self.archive_btn = QPushButton("Archivieren")
        self.archive_btn.setToolTip("Archivierte Vorgänge werden ausgeblendet, bleiben aber gespeichert.")
        self.archive_btn.clicked.connect(self.toggle_archive_selected)
        self.delete_btn = danger_button("Löschen …", "Vorgang mit allen lokalen Dokumenten und Daten löschen")
        self.delete_btn.setAccessibleName("Löschen")
        self.delete_btn.setStyleSheet(
            f"QPushButton:disabled {{ color: #9AA4B1; border-color: {theme.BORDER}; background: #EEF1F5; }}"
        )
        self.delete_btn.clicked.connect(self.delete_selected)
        for b in (self.open_btn, self.rename_btn, self.archive_btn, self.delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        selected = self.selected_case_id()
        self._titles = None  # Vorlagen können im Studio hinzugekommen sein
        try:
            self._summaries = self.service.list_cases(
                search=self.search.text().strip() or None, include_archived=self.show_archived.isChecked()
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Vorgänge nicht lesbar: %s", type(exc).__name__)
            self._summaries = []
            self.empty_label.setText(f"Vorgänge konnten nicht gelesen werden: {error_text(exc)}")
        titles = self._template_titles()
        self.table.setRowCount(len(self._summaries))
        for row, s in enumerate(self._summaries):
            title = s.title or f"Vorgang {s.id}"
            if s.archived:
                title += "  (archiviert)"
            cells = (
                format_local_time(s.updated_at),
                title,
                stage_text(s.stage),
                titles.get(s.template_id, s.template_id) if s.template_id else "–",
                str(s.document_count),
                (f"⚠ {s.open_issues}" if s.open_issues else "✔ 0"),
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, s.id)
                if col == 1:
                    item.setToolTip(f"{title}\nVorgangs-ID: {s.id}")
                if col == 5:
                    item.setToolTip(
                        f"{s.open_issues} offene Punkte (unbestätigt, Konflikt oder unklar)"
                        if s.open_issues
                        else "Keine offenen Punkte"
                    )
                    item.setForeground(QColor(theme.WARN if s.open_issues else theme.OK))
                self.table.setItem(row, col, item)
            if s.id == selected:
                self.table.selectRow(row)
        if self._summaries:
            self.empty_label.hide()
        else:
            self.empty_label.setText(
                "Keine Vorgänge gefunden."
                if self.search.text().strip()
                else "Noch keine Vorgänge. Legen Sie oben Dateien ab oder laden Sie den Musterfall."
            )
            self.empty_label.show()
        self._update_buttons()

    def _template_titles(self) -> dict[str, str]:
        if self._titles is None:
            try:
                self._titles = {c.catalog_id: c.title for c in self.service.template_choices()}
            except Exception:  # noqa: BLE001 – Titel sind nur Komfort
                return {}
        return self._titles

    def row_titles(self) -> list[str]:
        return [self.table.item(r, 1).text() for r in range(self.table.rowCount())]

    def selected_case_id(self) -> str | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            return None
        item = self.table.item(min(rows), 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def select_case(self, case_id: str) -> bool:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == case_id:
                self.table.selectRow(r)
                return True
        return False

    def _selected_summary(self) -> CaseSummary | None:
        cid = self.selected_case_id()
        return next((s for s in self._summaries if s.id == cid), None)

    def _update_buttons(self) -> None:
        s = self._selected_summary()
        idle = not self._busy
        for b in (self.open_btn, self.rename_btn, self.archive_btn, self.delete_btn):
            b.setEnabled(idle and s is not None)
        self.archive_btn.setText("Wiederherstellen" if s is not None and s.archived else "Archivieren")
        self.choose_btn.setEnabled(idle)
        self.sample_btn.setEnabled(idle)
        self.drop_area.setAcceptDrops(idle)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.table.setEnabled(not busy)
        self._update_buttons()

    # ------------------------------------------------------------------
    # Dateien übernehmen
    # ------------------------------------------------------------------
    def choose_files(self) -> None:
        paths = dialogs.pick_open_files(self)
        if paths:
            self.import_files(paths)

    def import_files(self, paths: list[Path]) -> str | None:
        """Neuen Vorgang anlegen, Dateien übernehmen, Vorgang öffnen und Auswertung starten."""
        if self._busy:
            return None
        files = local_files([Path(p) for p in paths])
        if not files:
            dialogs.show_info(
                self, "Keine Dateien", "Es wurden keine Dateien übergeben (Ordner werden nicht übernommen)."
            )
            return None
        try:
            case = self.service.create_case()
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Vorgang nicht angelegt", error_text(exc))
            return None
        report = add_files(self, self.service, case, files)
        if not report.any_added:
            try:
                self.service.delete_case(case.id)  # leeren Vorgang nicht stehen lassen
            except Exception as exc:  # noqa: BLE001
                log.info("Leerer Vorgang nicht gelöscht: %s", type(exc).__name__)
            dialogs.show_error(
                self,
                "Keine Datei übernommen",
                "Keine der Dateien konnte übernommen werden:\n\n" + "\n".join(report.problems),
            )
            self.refresh()
            return None
        if report.problems:
            dialogs.show_info(
                self,
                "Hinweis zu einzelnen Dateien",
                f"{report.accepted} Datei(en) übernommen.\n\nNicht übernommen bzw. Hinweise:\n"
                + "\n".join(report.problems),
            )
        self.refresh()
        if report.accepted:
            request_processing(case.id)
        self._open_case(case.id)
        if consume_processing_request(case.id):
            # Die Vorgangsansicht hat den Auftrag nicht übernommen -> hier auswerten
            self._process_here(case.id)
        return case.id

    def _process_here(self, case_id: str) -> None:
        try:
            work = self.service.load_case(case_id)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Auswertung nicht gestartet", error_text(exc))
            return
        self._set_busy(True)

        def done(_result: object) -> None:
            self._set_busy(False)
            self.refresh()

        def failed(exc: BaseException) -> None:
            self._set_busy(False)
            self.refresh()
            dialogs.show_error(self, "Auswertung nicht abgeschlossen", error_text(exc))

        workers.run_task(
            self,
            "Texterkennung und Auswertung laufen (lokal) …",
            lambda progress, cancel: self.service.process_documents(work, progress=progress, cancel=cancel),
            done,
            failed,
        )

    def load_sample(self) -> None:
        if self._busy:
            return
        self._set_busy(True)

        def done(case: object) -> None:
            self._set_busy(False)
            self.refresh()
            case_id = getattr(case, "id", None)
            if case_id:
                self._open_case(str(case_id))

        def failed(exc: BaseException) -> None:
            self._set_busy(False)
            self.refresh()
            dialogs.show_error(self, "Musterfall nicht geladen", error_text(exc))

        workers.run_task(
            self,
            "Musterfall wird angelegt und ausgewertet (synthetische Daten) …",
            lambda progress, cancel: self.service.create_sample_case(progress=progress, cancel=cancel),
            done,
            failed,
        )

    # ------------------------------------------------------------------
    # Verwaltung
    # ------------------------------------------------------------------
    def open_selected(self) -> None:
        cid = self.selected_case_id()
        if cid and not self._busy:
            self._open_case(cid)

    def rename_selected(self) -> None:
        s = self._selected_summary()
        if s is None or self._busy:
            return
        title = dialogs.ask_text(
            self, "Vorgang umbenennen", "Neuer Titel (bitte ohne sensible Daten):", s.title
        )
        if title is None or not title.strip():
            return
        try:
            case = self.service.load_case(s.id)
            self.service.rename_case(case, title)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Nicht umbenannt", error_text(exc))
        self.refresh()

    def toggle_archive_selected(self) -> None:
        s = self._selected_summary()
        if s is None or self._busy:
            return
        try:
            self.service.archive_case(s.id, archived=not s.archived)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Nicht geändert", error_text(exc))
        self.refresh()

    def delete_selected(self) -> None:
        s = self._selected_summary()
        if s is None or self._busy:
            return
        ok = dialogs.ask_confirm(
            self,
            "Vorgang endgültig löschen",
            DELETE_WARNING.format(title=s.title or f"Vorgang {s.id}"),
            ok_text="Endgültig löschen",
            danger=True,
        )
        if not ok:
            return
        try:
            self.service.delete_case(s.id)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Nicht gelöscht", error_text(exc))
        self.refresh()
