"""Seite „Vorlagen-Studio“: Originalvorlagen importieren, Felder zuordnen, prüfen, bestätigen.

* Originalvorlagen werden als schreibgeschützte Kopie übernommen; die gewählte
  Datei wird nie verändert. Jede Übernahme ist eine eigene Version.
* Mitgelieferte Zuordnungen (Swiss Life) sind nur lesbar; „Bearbeiten“ legt eine eigene Kopie an.
* Testbefüllung mit Musterdaten zeigt die Zuordnung auf der Seite – ohne etwas zu speichern.
* Koordinateneditor für PDFs ohne Formularfelder (Release 2, Grundfunktion).
* Vorlagenpakete (ZIP: Original + Zuordnung + Prüfsumme) exportieren/importieren;
  importierte Pakete müssen erneut bestätigt werden.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ...core.service import AppService, TemplateChoice
from ...pdf.inspector import FormInspection, PdfOpenError, inspect_pdf_form
from ...templates.registry import (
    CatalogItem,
    TemplateCheckReport,
    TemplateRegistryError,
    TemplateVersionInfo,
)
from ...templates.schema import MappingKind, TemplateDefinition, TemplateStatus
from ...templates.studio import TemplateDraft, auto_mapping
from .. import dialogs
from ..studio.common import (
    TOOLTIP_ACROFORM,
    TOOLTIP_OVERLAY,
    TOOLTIP_SHA,
    format_dt,
    template_status_color,
    template_status_text,
)
from ..studio.coordinate_editor import CoordinateEditorDialog
from ..studio.mapping_editor import MappingEditor
from ..studio.sample_fill import PagePreviewDialog, SampleFillResult, run_sample_fill
from ..theme import INFO, TEXT_MUTED
from ..widgets.common import badge, danger_button, muted, page_title, primary_button
from ..workers import Task, run_task, user_message

log = logging.getLogger(__name__)

TABLE_COLUMNS = (
    "Nr.",
    "Name",
    "Versicherer",
    "Formularnummer",
    "Version/Stand",
    "Status",
    "Quelle",
    "Änderungsdatum",
)
COL_STATUS = 5
COL_SOURCE = 6
VERSION_COLUMNS = ("Version", "Quelle", "Übernommen am", "Bestätigt", "Prüfsumme (SHA-256)", "Verwendung")
PDF_FILTER = "PDF (*.pdf)"
PACKAGE_FILTER = "Vorlagenpaket (*.zip)"

IMPORT_EXPLANATION = (
    "Was beim Import passiert:\n"
    "• Ihre Original-PDF bleibt unverändert. Die App legt nur eine schreibgeschützte Kopie an.\n"
    "• Die App sucht die ausfüllbaren Formularfelder der PDF und schlägt eine Zuordnung vor.\n"
    "• Diese Zuordnung müssen Sie Zeile für Zeile prüfen und bestätigen. Erst danach ist die "
    "Vorlage „Aktiv“ und kann befüllt werden.\n"
    "• Nichts verlässt dieses Gerät: kein Upload, keine Cloud, keine Übermittlung an Dritte.\n"
    "• Die Berechtigungen der PDF werden beachtet. Erlaubt die PDF das Ausfüllen nicht oder ist sie "
    "passwortgeschützt, wird sie nicht befüllt – ein Schutz wird nie umgangen."
)
IMPORT_LETTER_NOTE = (
    "Hinweis zu diesem Eintrag: Bisher erzeugt die App hierfür ein Kundenschreiben nach DIN 5008. "
    "Nach dem Import einer eigenen Originalvorlage ist der Eintrag erst wieder wählbar, wenn Sie "
    "die Feldzuordnung geprüft und bestätigt haben."
)
REMOVE_BUTTON_TEXT = "Importierte Version entfernen"


class StudioPage(QWidget):
    def __init__(self, service: AppService) -> None:
        super().__init__()
        self.service = service
        self.items: list[tuple[CatalogItem, TemplateChoice | None]] = []
        self.catalog_id: str | None = None
        self.report: TemplateCheckReport | None = None
        self.versions: list[TemplateVersionInfo] = []
        self.current_version: TemplateVersionInfo | None = None
        self.current_definition: TemplateDefinition | None = None
        self.current_inspection: FormInspection | None = None
        self.preview_dialog: PagePreviewDialog | None = None
        self.coord_dialog: CoordinateEditorDialog | None = None
        self.sample_task: Task | None = None
        self._selecting = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.setSpacing(8)
        outer.addWidget(page_title("Vorlagen-Studio"))
        outer.addWidget(
            muted(
                "Hier richten Sie Vorlagen ein: Originalvorlage (PDF) importieren, Formularfelder den Daten "
                "zuordnen, mit Musterdaten testen und bestätigen. Die Originaldatei wird dabei nie verändert."
            )
        )

        actions = QHBoxLayout()
        self.import_button = primary_button(
            "Originalvorlage importieren …",
            "Original-PDF des Versicherers übernehmen. Die App speichert eine schreibgeschützte Kopie.",
        )
        self.import_button.clicked.connect(self.import_original)
        actions.addWidget(self.import_button)
        self.sample_button = QPushButton("Testbefüllung mit Musterdaten")
        self.sample_button.setToolTip(
            "Füllt die Vorlage mit erkennbar erfundenen Werten und zeigt die Seiten. Es wird nichts gespeichert."
        )
        self.sample_button.clicked.connect(self.run_sample_fill)
        actions.addWidget(self.sample_button)
        self.coord_button = QPushButton("Koordinateneditor")
        self.coord_button.setToolTip(TOOLTIP_OVERLAY)
        self.coord_button.clicked.connect(self.open_coordinate_editor)
        actions.addWidget(self.coord_button)
        actions.addStretch(1)
        self.export_pkg_button = QPushButton("Paket exportieren …")
        self.export_pkg_button.setToolTip(
            "Vorlagenpaket (ZIP) mit Originalvorlage, Feldzuordnung und Prüfsumme speichern – ohne Vorgangsdaten."
        )
        self.export_pkg_button.clicked.connect(self.export_package)
        actions.addWidget(self.export_pkg_button)
        self.import_pkg_button = QPushButton("Paket importieren …")
        self.import_pkg_button.setToolTip(
            "Vorlagenpaket (ZIP) übernehmen. Importierte Pakete müssen Sie anschließend prüfen und bestätigen."
        )
        self.import_pkg_button.clicked.connect(self.import_package)
        actions.addWidget(self.import_pkg_button)
        outer.addLayout(actions)
        self.import_hint = QLabel("")
        self.import_hint.setWordWrap(True)
        self.import_hint.setStyleSheet(f"color: {INFO}; font-weight: 600;")
        self.import_hint.setVisible(False)
        outer.addWidget(self.import_hint)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(TABLE_COLUMNS))
        self.table.setAccessibleName("Vorlagen im Katalog")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection)
        splitter.addWidget(self.table)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 6, 0, 0)
        dl.setSpacing(6)
        head = QHBoxLayout()
        self.detail_title = QLabel("Bitte eine Vorlage auswählen")
        self.detail_title.setStyleSheet("font-weight: 700; font-size: 12pt;")
        head.addWidget(self.detail_title, 1)
        self.detail_badge = badge("", TEXT_MUTED)
        self.detail_badge.setVisible(False)
        head.addWidget(self.detail_badge)
        dl.addLayout(head)
        self.detail_reasons = muted("")
        dl.addWidget(self.detail_reasons)

        self.tabs = QTabWidget()
        self.editor = MappingEditor(service)
        self.editor.saved.connect(self._after_save)
        self.tabs.addTab(self.editor, "Feldzuordnung")
        versions_box = QWidget()
        vl = QVBoxLayout(versions_box)
        vl.addWidget(
            muted(
                "Jede übernommene Originalvorlage ist eine eigene Version. Vorgänge merken sich die verwendete "
                "Version; ältere Versionen bleiben erhalten, damit nachvollziehbar bleibt, womit ein Vorgang "
                "erstellt wurde. Auswahl einer Zeile zeigt deren Feldzuordnung."
            )
        )
        self.versions_table = QTableWidget(0, len(VERSION_COLUMNS))
        self.versions_table.setHorizontalHeaderLabels(list(VERSION_COLUMNS))
        self.versions_table.setAccessibleName("Versionen der Vorlage")
        self.versions_table.verticalHeader().setVisible(False)
        self.versions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.versions_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.versions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        vh = self.versions_table.horizontalHeader()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        vh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        sha_item = self.versions_table.horizontalHeaderItem(4)
        if sha_item is not None:
            sha_item.setToolTip(TOOLTIP_SHA)
        self.versions_table.itemSelectionChanged.connect(self._on_version_selection)
        vl.addWidget(self.versions_table, 1)
        remove_row = QHBoxLayout()
        self.remove_version_button = danger_button(REMOVE_BUTTON_TEXT)
        self.remove_version_button.setAccessibleName(REMOVE_BUTTON_TEXT)
        self.remove_version_button.clicked.connect(self.remove_version)
        remove_row.addWidget(self.remove_version_button)
        self.remove_hint = muted("")
        remove_row.addWidget(self.remove_hint, 1)
        vl.addLayout(remove_row)
        self.tabs.addTab(versions_box, "Versionen")
        dl.addWidget(self.tabs, 1)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([240, 560])
        outer.addWidget(splitter, 1)

        self.refresh()

    # ------------------------------------------------------------------
    # Tabelle
    # ------------------------------------------------------------------
    def refresh(self, recheck: bool = False) -> None:
        try:
            items = self.service.registry.list_items(refresh=recheck)
            choices = {c.catalog_id: c for c in self.service.template_choices()}
        except Exception as exc:  # noqa: BLE001
            log.warning("Vorlagenkatalog nicht lesbar (%s)", type(exc).__name__)
            dialogs.show_error(self, "Vorlagen-Studio", user_message(exc))
            items, choices = [], {}
        self.items = [(it, choices.get(it.entry.id)) for it in items]
        selected = self.catalog_id
        self._selecting = True
        try:
            self.table.setRowCount(len(self.items))
            for row, (item, choice) in enumerate(self.items):
                self._fill_row(row, item, choice)
        finally:
            self._selecting = False
        if selected is not None:
            cur = self.current_version
            self.select_entry(
                selected,
                version=cur.version if cur is not None else None,
                source=cur.source if cur is not None else None,
                reload=not self.editor.dirty,
            )
        else:
            self._update_buttons()

    def _fill_row(self, row: int, item: CatalogItem, choice: TemplateChoice | None) -> None:
        e = item.entry
        is_letter = choice is not None and choice.kind == "letter"
        status = choice.status if choice is not None else item.status
        reason = choice.status_reason if choice is not None else item.status_reason
        version = item.version or (choice.version if choice and choice.version else None)
        if item.source:
            source = item.source
        elif is_letter:
            source = "Brief"
        else:
            source = "–"
        values = (
            str(e.number),
            e.title,
            e.insurer,
            e.form_number or ("Brief (DIN 5008)" if is_letter else "–"),
            version or "–",
            template_status_text(status),
            source,
            format_dt(item.updated_at),
        )
        for col, text in enumerate(values):
            cell = QTableWidgetItem(text)
            cell.setData(Qt.ItemDataRole.UserRole, e.id)
            if col == COL_STATUS:
                cell.setToolTip(reason)
                cell.setForeground(QBrush(QColor(template_status_color(status))))
            elif col == COL_SOURCE and is_letter and not item.source:
                cell.setToolTip("Für diesen Eintrag erzeugt die App ein Kundenschreiben nach DIN 5008.")
            self.table.setItem(row, col, cell)

    def status_text(self, catalog_id: str) -> str:
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, COL_STATUS)
            if cell is not None and cell.data(Qt.ItemDataRole.UserRole) == catalog_id:
                return cell.text()
        return ""

    def select_entry(
        self, catalog_id: str, version: str | None = None, reload: bool = True, source: str | None = None
    ) -> None:
        """Eintrag auswählen (z. B. aus dem Katalog „Im Studio öffnen“)."""
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell is not None and cell.data(Qt.ItemDataRole.UserRole) == catalog_id:
                self._selecting = True
                try:
                    self.table.selectRow(row)
                finally:
                    self._selecting = False
                if reload or catalog_id != self.catalog_id:
                    self._load_entry(catalog_id, version, source)
                else:
                    self._update_buttons()
                return

    def _on_selection(self) -> None:
        if self._selecting:
            return
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        cell = self.table.item(rows[0].row(), 0)
        if cell is None:
            return
        catalog_id = str(cell.data(Qt.ItemDataRole.UserRole))
        if catalog_id == self.catalog_id:
            return
        if self.editor.dirty and not dialogs.ask_confirm(
            self,
            "Ungespeicherte Änderungen",
            "Die Feldzuordnung wurde geändert, aber noch nicht gespeichert. Änderungen verwerfen?",
            ok_text="Verwerfen",
            danger=True,
        ):
            self.select_entry(self.catalog_id or catalog_id, reload=False)
            return
        self._load_entry(catalog_id)

    # ------------------------------------------------------------------
    # Details
    # ------------------------------------------------------------------
    def _choice(self, catalog_id: str) -> TemplateChoice | None:
        return next((c for it, c in self.items if it.entry.id == catalog_id), None)

    def _load_entry(self, catalog_id: str, version: str | None = None, source: str | None = None) -> None:
        self.catalog_id = catalog_id
        registry = self.service.registry
        entry = registry.entry(catalog_id)
        choice = self._choice(catalog_id)
        try:
            self.report = registry.verify(catalog_id)
            self.versions = registry.list_versions(catalog_id)
        except TemplateRegistryError as exc:
            self.report = None
            self.versions = []
            dialogs.show_error(self, "Vorlagen-Studio", exc.user_message)
        self.detail_title.setText(f"Nr. {entry.number} – {entry.title} ({entry.insurer})")
        if self.report is not None:
            status = self.report.status
            self.detail_badge.setText(template_status_text(status))
            self.detail_badge.setStyleSheet(
                f"color: {template_status_color(status)}; border: 1px solid {template_status_color(status)}; "
                "border-radius: 10px; padding: 2px 10px; font-weight: 600;"
            )
            self.detail_badge.setAccessibleName(f"Status der Originalvorlage: {status.value}")
            self.detail_badge.setVisible(True)
            lines = list(self.report.reasons) + list(self.report.notes)
            if choice is not None and choice.kind == "letter" and status == TemplateStatus.MISSING_ORIGINAL:
                lines = [
                    "Für diesen Eintrag erzeugt die App ein Kundenschreiben nach DIN 5008. "
                    "Eine eigene Originalvorlage ist optional."
                ]
            self.detail_reasons.setText(
                "\n".join(dict.fromkeys(lines)) if lines else "✔ Geprüft und bestätigt."
            )
        else:
            self.detail_badge.setVisible(False)
            self.detail_reasons.setText("")
        self._fill_versions()
        target = None
        if version is not None:
            wanted = source or "importiert"  # gleiche Version: eigene Kopie vor mitgelieferter
            target = next((v for v in self.versions if v.version == version and v.source == wanted), None)
            target = target or next((v for v in self.versions if v.version == version), None)
        if target is None and self.versions:
            target = self._default_version()
        self._load_version(target)

    def _default_version(self) -> TemplateVersionInfo | None:
        if not self.versions:
            return None
        # Neueste unbestätigte Version zuerst (sie braucht Aufmerksamkeit), sonst die aktive
        newest = self.versions[0]
        if newest.source == "importiert" and not newest.confirmed:
            return newest
        if self.report is not None and self.report.version:
            for v in self.versions:
                if v.version == self.report.version and v.source == self.report.source:
                    return v
        return newest

    def _fill_versions(self) -> None:
        self._selecting = True
        try:
            self.versions_table.setRowCount(len(self.versions))
            for row, v in enumerate(self.versions):
                active = (
                    self.report is not None
                    and self.report.version == v.version
                    and self.report.source == v.source
                    and self.report.pdf_path is not None
                )
                values = (
                    v.version,
                    v.source,
                    format_dt(v.imported_at) if v.imported_at else "mit dem Programm geliefert",
                    "✔ ja" if v.confirmed else "◐ nein – bitte prüfen",
                    (v.sha256[:16] + "…") if v.sha256 else "–",
                    "★ wird verwendet" if active else "–",
                )
                for col, text in enumerate(values):
                    cell = QTableWidgetItem(text)
                    if col == 4:
                        cell.setToolTip(v.sha256 or TOOLTIP_SHA)
                    self.versions_table.setItem(row, col, cell)
        finally:
            self._selecting = False

    def _on_version_selection(self) -> None:
        if self._selecting:
            return
        rows = self.versions_table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self.versions):
            v = self.versions[idx]
            if self.current_version is not None and (v.version, v.source) == (
                self.current_version.version,
                self.current_version.source,
            ):
                return
            self._load_version(v)
            self.tabs.setCurrentIndex(0)

    def _load_version(self, version: TemplateVersionInfo | None, draft: TemplateDraft | None = None) -> None:
        self.current_version = version
        self.current_definition = None
        self.current_inspection = None
        if version is None:
            choice = self._choice(self.catalog_id or "")
            msg = "Noch keine Originalvorlage vorhanden. Mit „Originalvorlage importieren …“ übernehmen Sie eine PDF."
            if choice is not None and choice.kind == "letter":
                msg += "\nFür diesen Eintrag erzeugt die App bereits ein Kundenschreiben nach DIN 5008."
            self.editor.clear(msg)
            self._update_buttons()
            return
        try:
            self.current_definition = TemplateDefinition.model_validate_json(
                Path(version.mapping_path).read_text("utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            log.warning("Feldzuordnung nicht lesbar (%s)", type(exc).__name__)
            self.editor.clear(
                "Die Feldzuordnung dieser Version ist fehlerhaft und kann nicht angezeigt werden."
            )
            self._update_buttons()
            return
        pdf = Path(version.pdf_path)
        if pdf.is_file():
            try:
                self.current_inspection = inspect_pdf_form(pdf)
            except PdfOpenError as exc:
                log.warning("Originalvorlage nicht lesbar (%s)", type(exc).__name__)
        if (
            draft is None
            and version.source == "importiert"
            and not version.confirmed
            and self.current_inspection
        ):
            try:
                draft = auto_mapping(
                    self.service.registry.entry(version.catalog_id),
                    pdf,
                    self.current_inspection,
                    version.version,
                )
            except Exception as exc:  # noqa: BLE001 – Vorschläge sind nur eine Hilfe
                log.info("Vorschläge nicht verfügbar (%s)", type(exc).__name__)
                draft = None
        is_active = (
            self.report is not None
            and self.report.version == version.version
            and self.report.source == version.source
        )
        status = self.report.status if is_active and self.report is not None else None
        self.editor.load(
            version.catalog_id,
            version,
            self.current_definition,
            self.current_inspection,
            draft,
            status=status,
        )
        for row, v in enumerate(self.versions):
            if (v.version, v.source) == (version.version, version.source):
                self._selecting = True
                try:
                    self.versions_table.selectRow(row)
                finally:
                    self._selecting = False
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_entry = self.catalog_id is not None
        v = self.current_version
        has_pdf = v is not None and Path(v.pdf_path).is_file()
        fillable = bool(
            self.current_definition
            and any(
                m.kind not in (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE)
                for m in self.current_definition.fields
            )
        )
        self.import_button.setEnabled(has_entry)
        self.import_button.setToolTip(
            "Original-PDF des Versicherers übernehmen. Die App speichert eine schreibgeschützte Kopie."
            if has_entry
            else "Bitte zuerst eine Vorlage in der Liste auswählen."
        )
        busy = self.sample_task is not None
        self.sample_button.setEnabled(has_pdf and fillable and not busy)
        if not has_pdf:
            self.sample_button.setToolTip("Keine Originalvorlage vorhanden.")
        elif not fillable:
            self.sample_button.setToolTip("Die Zuordnung enthält noch keine befüllbaren Felder.")
        else:
            self.sample_button.setToolTip(
                "Füllt die Vorlage mit erkennbar erfundenen Werten und zeigt die Seiten. Es wird nichts gespeichert."
            )
        no_fields = has_pdf and self.current_inspection is not None and not self.current_inspection.fields
        self.coord_button.setEnabled(bool(no_fields))
        self.coord_button.setToolTip(
            TOOLTIP_OVERLAY
            if no_fields
            else "Nur für PDFs ohne Formularfelder. Diese Vorlage hat ausfüllbare Felder. " + TOOLTIP_ACROFORM
        )
        self.export_pkg_button.setEnabled(has_pdf)
        removable = v is not None and v.source == "importiert"
        self.remove_version_button.setEnabled(removable)
        if v is None:
            reason = "Bitte zuerst eine Version in der Tabelle auswählen."
        elif removable:
            reason = (
                f"Löscht die von Ihnen importierte Version {v.version} (Kopie der PDF und Feldzuordnung). "
                "Bereits erzeugte PDFs bleiben erhalten."
            )
        else:
            reason = "ℹ Mitgelieferte Originalvorlagen gehören zum Programm und können nicht entfernt werden."
        self.remove_version_button.setToolTip(reason)
        self.remove_hint.setText("" if removable or v is None else reason)

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------
    def import_original(self) -> None:
        if self.catalog_id is None:
            return
        choice = self._choice(self.catalog_id)
        entry = self.service.registry.entry(self.catalog_id)
        is_letter = choice is not None and choice.kind == "letter"
        text = f"Originalvorlage für „{entry.title}“ ({entry.insurer}) importieren.\n\n{IMPORT_EXPLANATION}"
        if is_letter:
            text += f"\n\n{IMPORT_LETTER_NOTE}"
        text += "\n\nIm nächsten Schritt wählen Sie die PDF-Datei aus. Fortfahren?"
        if not dialogs.ask_confirm(
            self,
            "Eigene Originalvorlage importieren?" if is_letter else "Originalvorlage importieren",
            text,
            ok_text="PDF auswählen …",
        ):
            return
        self.import_hint.setVisible(False)
        path = dialogs.pick_open_file(self, "Originalvorlage (PDF) auswählen", PDF_FILTER)
        if path is None:
            return
        catalog_id = self.catalog_id
        try:
            draft = self.service.registry.import_original(catalog_id, Path(path))
        except TemplateRegistryError as exc:
            dialogs.show_error(self, "Import nicht möglich", exc.user_message)
            return
        definition = draft.definition
        version = definition.version if definition is not None else None
        assigned = len(definition.fields) if definition is not None else 0
        dialogs.show_info(
            self,
            "Originalvorlage übernommen",
            f"Die PDF wurde als schreibgeschützte Kopie übernommen (Version {version}). "
            "Ihre Originaldatei bleibt unverändert und wird nie bearbeitet.\n\n"
            f"Automatisch zugeordnet: {assigned} Feld(er) · ohne Zuordnung: {len(draft.unassigned_fields)}.\n"
            "Bitte prüfen Sie jetzt jede Zeile der Feldzuordnung und bestätigen Sie sie anschließend.",
        )
        self.editor.mark_clean()  # neue Version: vorherige Ansicht verwerfen
        self.refresh()
        self.select_entry(catalog_id, version=version)
        if self.current_version is not None and self.current_version.version == version:
            self._load_version(self.current_version, draft)
        self.tabs.setCurrentIndex(0)

    def prepare_import(self, catalog_id: str) -> None:
        """Eintrag für den Import vorwählen (aus dem Katalog „Originalvorlage importieren …“)."""
        self.select_entry(catalog_id)
        if self.catalog_id != catalog_id:
            return
        entry = self.service.registry.entry(catalog_id)
        self.import_hint.setText(
            f"➜ „{entry.title}“ ist für den Import ausgewählt. Klicken Sie jetzt auf "
            "„Originalvorlage importieren …“ und wählen Sie die Original-PDF des Versicherers."
        )
        self.import_hint.setVisible(True)
        self.import_button.setFocus()

    def remove_version(self) -> None:
        """Ausgewählte, selbst importierte Version nach Rückfrage löschen."""
        v = self.current_version
        catalog_id = self.catalog_id
        if v is None or catalog_id is None or v.source != "importiert":
            return
        try:
            affected = self.service.cases_using_template_version(catalog_id, v.version)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Version nicht entfernt", user_message(exc))
            return
        entry = self.service.registry.entry(catalog_id)
        text = (
            f"Die importierte Version {v.version} von „{entry.title}“ wirklich entfernen?\n\n"
            "• Gelöscht werden die Kopie der Original-PDF und die Feldzuordnung dieser Version. "
            "Das lässt sich nicht rückgängig machen.\n"
            "• Bereits erzeugte PDFs und das Exportprotokoll bleiben unverändert.\n"
            "• Mitgelieferte Originalvorlagen des Programms bleiben erhalten."
        )
        if affected:
            text += (
                f"\n\n⚠ {len(affected)} Vorgang/Vorgänge verwenden diese Version. Ihre Daten bleiben "
                "erhalten, aber dort muss die Vorlage neu ausgewählt werden, bevor eine PDF erzeugt "
                "werden kann."
            )
        if self.editor.dirty:
            text += "\n\n⚠ Ungespeicherte Änderungen an der Feldzuordnung gehen dabei verloren."
        if not dialogs.ask_confirm(
            self, "Importierte Version entfernen?", text, ok_text="Endgültig entfernen", danger=True
        ):
            return
        try:
            affected = self.service.remove_imported_template_version(catalog_id, v.version)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Version nicht entfernt", user_message(exc))
            return
        self.editor.mark_clean()
        self.current_version = None
        self.refresh()
        self.select_entry(catalog_id)
        info = f"Die importierte Version {v.version} wurde entfernt."
        if affected:
            info += (
                f"\n\n{len(affected)} Vorgang/Vorgänge müssen die Vorlage neu auswählen "
                "(Hinweis in der Checkliste des Vorgangs)."
            )
        dialogs.show_info(self, "Version entfernt", info)

    def _after_save(self, catalog_id: str, _confirmed: bool) -> None:
        version = self.current_version.version if self.current_version else None
        self.refresh()
        self.select_entry(catalog_id, version=version)

    def run_sample_fill(self) -> None:
        v = self.current_version
        definition = self.current_definition
        if v is None or definition is None or self.sample_task is not None:
            return
        pdf = Path(v.pdf_path)
        fonts = self.service.paths.fonts_dir
        temp = self.service.paths.temp_dir

        def work(progress, cancel) -> SampleFillResult:
            return run_sample_fill(definition, pdf, fonts, temp, progress=progress, cancel=cancel)

        def done(result: SampleFillResult) -> None:
            if not isValid(self):
                return
            self.sample_task = None
            self._update_buttons()
            dlg = PagePreviewDialog(
                f"Testbefüllung – {definition.title} (Version {definition.version})", result, self
            )
            self.preview_dialog = dlg
            dlg.show()

        def failed(exc: BaseException) -> None:
            if not isValid(self):
                return
            self.sample_task = None
            self._update_buttons()
            dialogs.show_error(self, "Testbefüllung nicht möglich", user_message(exc))

        self.sample_task = run_task(self, "Testbefüllung mit Musterdaten …", work, done, failed)
        self._update_buttons()

    def open_coordinate_editor(self) -> CoordinateEditorDialog | None:
        v = self.current_version
        if v is None or self.current_definition is None or self.catalog_id is None:
            return None
        pages = self.current_inspection.page_count if self.current_inspection else 1
        dlg = CoordinateEditorDialog(
            self.service, self.catalog_id, self.current_definition, Path(v.pdf_path), pages, self
        )
        dlg.saved.connect(self._after_save)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self.coord_dialog = dlg
        dlg.show()
        return dlg

    def export_package(self) -> None:
        v = self.current_version
        if v is None or self.catalog_id is None:
            return
        suggested = f"Vorlagenpaket_{self.catalog_id}_{v.version}.zip"
        dest = dialogs.pick_save_file(self, "Vorlagenpaket speichern", suggested, PACKAGE_FILTER)
        if dest is None:
            return
        dest = Path(dest)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_name(dest.name + ".zip")
        try:
            out = self.service.registry.export_package(self.catalog_id, dest, version=v.version)
        except TemplateRegistryError as exc:
            dialogs.show_error(self, "Paket nicht gespeichert", exc.user_message)
            return
        dialogs.show_info(
            self,
            "Paket gespeichert",
            f"Das Vorlagenpaket „{out.name}“ wurde gespeichert.\n\n"
            "Es enthält die Originalvorlage, die Feldzuordnung und eine Prüfsumme – keine Vorgangs- oder "
            "Personendaten.",
        )

    def import_package(self) -> None:
        path = dialogs.pick_open_file(self, "Vorlagenpaket öffnen", PACKAGE_FILTER)
        if path is None:
            return
        try:
            info = self.service.registry.import_package(Path(path))
        except TemplateRegistryError as exc:
            dialogs.show_error(self, "Paket nicht übernommen", exc.user_message)
            return
        entry = self.service.registry.entry(info.catalog_id)
        dialogs.show_info(
            self,
            "Paket übernommen",
            f"Vorlage „{entry.title}“, Version {info.version} wurde übernommen.\n\n"
            "Wichtig: Importierte Pakete sind noch nicht freigegeben. Bitte prüfen Sie die Feldzuordnung "
            "und bestätigen Sie sie – erst dann ist die Vorlage „Aktiv“.",
        )
        self.editor.mark_clean()
        self.refresh()
        self.select_entry(info.catalog_id, version=info.version)
        self.tabs.setCurrentIndex(0)
