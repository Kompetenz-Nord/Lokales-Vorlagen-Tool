"""Reiter „Dokumente“ (Stufen 1–3): Dateien, Dokumentcheck, Typ, Texterkennung je Seite."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.case import SourceDocument
from ...core.models import DocumentType, ExtractionMethod, Severity
from ...core.service import AppService, ExtractionSummary
from .. import dialogs, theme
from ..widgets.common import badge, muted, primary_button, section_title
from .common import error_text
from .controller import CaseController
from .formatting import (
    METHOD_HELP,
    QUALITY_LABELS,
    QUALITY_SYMBOLS,
    method_text,
    severity_symbol,
)
from .importing import add_files

COLUMNS = ("Dateiname", "Typ", "Seiten", "Prüfung", "Texterkennung je Seite", "Auswertung")

STATUS_LABELS = {
    "EXTRACTED": "erkannt – bitte prüfen",
    "CONFIRMED": "bestätigt",
    "MANUAL": "manuell",
    "CONFLICT": "Konflikte",
    "UNCLEAR": "unklar",
    "MISSING": "nicht gefunden",
    "NOT_APPLICABLE": "nicht relevant",
}


def check_text(doc: SourceDocument) -> tuple[str, str]:
    """(Zelltext, Tooltip) für die Spalte „Prüfung“."""
    check = doc.check
    if check is None:
        return "noch nicht geprüft", ""
    if not check.issues:
        return "✔ in Ordnung", "Datei geprüft: lesbar, nicht beschädigt."
    lines = [f"{severity_symbol(i.severity)} {i.message}" for i in check.issues]
    worst = (
        Severity.ERROR
        if any(i.severity == Severity.ERROR for i in check.issues)
        else Severity.WARNING
        if any(i.severity == Severity.WARNING for i in check.issues)
        else Severity.INFO
    )
    head = {Severity.ERROR: "✖ Problem", Severity.WARNING: "⚠ Hinweis", Severity.INFO: "ℹ Info"}[worst]
    short = f"{head}: {check.issues[0].message}"
    if len(check.issues) > 1:
        short += f" (+{len(check.issues) - 1})"
    return short, "\n".join(lines)


def ocr_text(doc: SourceDocument) -> tuple[str, str]:
    """(Zelltext, Tooltip) für die Texterkennung je Seite."""
    if doc.text is None:
        if doc.excluded:
            return "– (nicht ausgewertet)", ""
        return "noch nicht gelesen", "Bitte „Texterkennung & Auswertung starten“."
    lines = []
    for p in doc.text.pages:
        quality = f"{QUALITY_SYMBOLS[p.quality]} {QUALITY_LABELS[p.quality]}"
        line = f"S. {p.page_number}: {method_text(p.method, p.origin)} – {quality}"
        if p.mean_confidence is not None and p.method != ExtractionMethod.PDF_TEXT:
            line += f" (Wortsicherheit {round(p.mean_confidence * 100)} %)"
        lines.append(line)
    tip = "\n".join(lines) + "\n\n" + METHOD_HELP
    return "\n".join(lines), tip


class DocumentsPanel(QWidget):
    def __init__(
        self,
        service: AppService,
        controller: CaseController,
        *,
        start_processing: Callable[[], None],
        goto_review: Callable[[], None],
        task_parent: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.controller = controller
        self._task_parent = task_parent
        self._start_processing = start_processing
        self._goto_review = goto_review
        self.notes: list[str] = []
        self.summary: ExtractionSummary | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(8)

        intro = muted(
            "Hier sehen Sie alle Dateien dieses Vorgangs. Die App liest zuerst die Textebene des PDFs; "
            "nur Seiten ohne brauchbaren Text werden per OCR (automatische Texterkennung aus dem Bild) gelesen. "
            "Die Originaldateien bleiben unverändert."
        )
        layout.addWidget(intro)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("Dateien hinzufügen …")
        self.btn_add.setToolTip("Weitere Police, Nachtrag, Standmitteilung oder Screenshot hinzufügen")
        self.btn_remove = QPushButton("Entfernen …")
        self.btn_remove.setToolTip("Ausgewählte Datei aus dem Vorgang entfernen (die Kopie wird gelöscht)")
        self.btn_exclude = QPushButton("Ausschließen")
        self.btn_exclude.setToolTip(
            "Datei bleibt im Vorgang, wird aber nicht ausgewertet (z. B. veraltetes Dokument)"
        )
        self.btn_process = primary_button(
            "Texterkennung && Auswertung starten",
            "Text aller Dateien lesen und Werte mit Fundstelle erkennen. Läuft lokal und kann abgebrochen werden.",
        )
        self.btn_ai = QPushButton("Lokale KI-Zweitprüfung")
        for btn in (self.btn_add, self.btn_remove, self.btn_exclude, self.btn_process, self.btn_ai):
            btn.setAccessibleName(btn.text().rstrip(" …").replace("&&", "&"))
            bar.addWidget(btn)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setAccessibleName("Dokumente des Vorgangs")
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 235)
        self.table.setColumnWidth(3, 200)
        hdr.sectionResized.connect(lambda *_: self._schedule_row_heights())
        self._rows_pending = False
        self.table.horizontalHeaderItem(4).setToolTip(METHOD_HELP)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 3)

        notes_head = QHBoxLayout()
        notes_head.addWidget(section_title("Ergebnis der Auswertung"))
        notes_head.addStretch(1)
        self.btn_next = primary_button("Weiter zur Datenprüfung →")
        self.btn_next.setAccessibleName("Weiter zur Datenprüfung")
        self.btn_next.clicked.connect(self._goto_review)
        notes_head.addWidget(self.btn_next)
        layout.addLayout(notes_head)
        self.summary_label = muted("")
        layout.addWidget(self.summary_label)
        self.notes_list = QListWidget()
        self.notes_list.setAccessibleName("Hinweise aus Texterkennung und Auswertung")
        self.notes_list.setWordWrap(True)
        layout.addWidget(self.notes_list, 1)

        self.btn_add.clicked.connect(self.add_files_clicked)
        self.btn_remove.clicked.connect(self.remove_clicked)
        self.btn_exclude.clicked.connect(self.toggle_excluded_clicked)
        self.btn_process.clicked.connect(self._start_processing)
        self.btn_ai.clicked.connect(self.local_ai_clicked)
        self._ai_ready = False
        self.render()

    # ------------------------------------------------------------------
    def selected_document(self) -> SourceDocument | None:
        case = self.controller.case
        rows = {i.row() for i in self.table.selectedIndexes()}
        if case is None or not rows:
            return None
        item = self.table.item(min(rows), 0)
        doc_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return case.document(doc_id) if doc_id else None

    def update_local_ai_state(self) -> None:
        """Lokale KI nur anbieten, wenn sie eingerichtet und bereit ist."""
        self._ai_ready = False
        if not self.service.settings.ai_enabled:
            reason = (
                "Die lokale KI ist ausgeschaltet. Sie ist optional und kann unter "
                "„Einstellungen“ eingerichtet werden. Die App funktioniert vollständig ohne sie."
            )
        else:
            try:
                status = self.service.local_ai_status()
            except Exception as exc:  # noqa: BLE001 – Statusabfrage darf nie stören
                reason = f"Status der lokalen KI unbekannt: {error_text(exc)}"
            else:
                self._ai_ready = getattr(status.state, "name", "") == "READY"
                reason = (
                    "Schwierige Seiten zusätzlich mit dem lokalen Bildmodell lesen. Abweichungen erscheinen "
                    "als Konflikt – die KI entscheidet nie selbst."
                    if self._ai_ready
                    else f"Nicht verfügbar: {status.message}"
                )
        self.btn_ai.setToolTip(reason)
        self._update_buttons()

    def set_result(self, notes: list[str], summary: ExtractionSummary | None) -> None:
        self.notes = list(notes)
        self.summary = summary
        self._render_notes()

    def clear_result(self) -> None:
        self.notes = []
        self.summary = None
        self._render_notes()

    # ------------------------------------------------------------------
    def render(self) -> None:
        case = self.controller.case
        selected = self.selected_document()
        selected_id = selected.id if selected else None
        self.table.setRowCount(0)
        if case is not None:
            self.table.setRowCount(len(case.documents))
            for row, doc in enumerate(case.documents):
                self._fill_row(row, doc)
                if doc.id == selected_id:
                    self.table.selectRow(row)
        self._schedule_row_heights()
        self._render_notes()
        self._update_buttons()

    def _fill_row(self, row: int, doc: SourceDocument) -> None:
        name = QTableWidgetItem(doc.original_name)
        name.setData(Qt.ItemDataRole.UserRole, doc.id)
        name.setToolTip(
            f"{doc.original_name}\nDokument-ID: {doc.id}\nSchreibgeschützte Kopie im Vorgangsordner."
        )
        self.table.setItem(row, 0, name)

        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.setCellWidget(row, 1, self._type_widget(doc))

        pages = str(doc.check.page_count) if doc.check else "–"
        self.table.setItem(row, 2, QTableWidgetItem(pages))

        text, tip = check_text(doc)
        item = QTableWidgetItem(text)
        item.setToolTip(tip)
        if doc.check and any(i.severity == Severity.ERROR for i in doc.check.issues):
            item.setForeground(Qt.GlobalColor.darkRed)
        self.table.setItem(row, 3, item)

        text, tip = ocr_text(doc)
        item = QTableWidgetItem(text)
        item.setToolTip(tip)
        self.table.setItem(row, 4, item)

        if doc.duplicate_of:
            state = "⧉ Dublette – nicht ausgewertet"
            tip = f"Gleicher Inhalt wie Dokument {doc.duplicate_of}."
        elif doc.excluded:
            state = "– ausgeschlossen"
            tip = "Wird bei der Auswertung nicht berücksichtigt."
        else:
            state = "✔ einbezogen"
            tip = "Wird bei der Auswertung berücksichtigt."
        item = QTableWidgetItem(state)
        item.setToolTip(tip)
        self.table.setItem(row, 5, item)

    def _type_widget(self, doc: SourceDocument) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(6)
        cls = doc.classification
        if cls is None:
            lbl = QLabel("noch nicht ausgewertet")
            lbl.setObjectName("Muted")
            lay.addWidget(lbl)
            return box
        combo = QComboBox()
        combo.setAccessibleName(f"Dokumenttyp von {doc.original_name}")
        combo.setToolTip(
            "Falls der Typ falsch erkannt wurde, hier korrigieren. Die Auswertung wird danach aktualisiert."
        )
        for dt in DocumentType:
            combo.addItem(dt.label_de, dt.value)
        combo.setCurrentIndex(max(0, combo.findData(cls.doc_type.value)))
        combo.activated.connect(lambda _i, c=combo, d=doc.id: self._type_changed(d, c.currentData()))
        lay.addWidget(combo, 1)
        if cls.uncertain and not doc.user_doc_type_confirmed:
            lay.addWidget(
                badge(
                    "⚠ unsicher",
                    theme.WARN,
                    "Der Dokumenttyp wurde nicht eindeutig erkannt (heuristischer Wert, nicht kalibriert). "
                    "Bitte prüfen und ggf. korrigieren.",
                )
            )
        return box

    def _schedule_row_heights(self) -> None:
        if not self._rows_pending:
            self._rows_pending = True
            QTimer.singleShot(0, self._fit_rows)

    def _fit_rows(self) -> None:
        """Zeilenhöhe an umbrochenen Text anpassen (erst nach dem Layout sinnvoll)."""
        self._rows_pending = False
        self.table.resizeRowsToContents()
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, max(44, self.table.rowHeight(row) + 6))

    def showEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        super().showEvent(event)
        self._schedule_row_heights()

    def _render_notes(self) -> None:
        self.notes_list.clear()
        case = self.controller.case
        summary = self.summary
        if summary is not None:
            parts = [
                f"{n} {STATUS_LABELS.get(status, status)}"
                for status, n in sorted(summary.counts.items())
                if n
            ]
            self.summary_label.setText(
                f"{summary.documents_used} Dokument(e) ausgewertet. Erkannte Angaben: " + ", ".join(parts)
                if parts
                else f"{summary.documents_used} Dokument(e) ausgewertet."
            )
            for w in summary.warnings:
                QListWidgetItem(f"{severity_symbol(w.severity)} {w.message}", self.notes_list)
        elif case is not None and case.contract_fields:
            self.summary_label.setText(
                "Die Dokumente wurden bereits ausgewertet. Prüfen Sie die Werte im Reiter „Datenprüfung“."
            )
        elif case is not None and case.documents:
            self.summary_label.setText("Noch nicht ausgewertet – bitte „Texterkennung & Auswertung starten“.")
        else:
            self.summary_label.setText("Noch keine Dateien im Vorgang.")
        for note in self.notes:
            QListWidgetItem(f"ℹ {note}", self.notes_list)
        if self.notes_list.count() == 0:
            item = QListWidgetItem("Keine Hinweise.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.notes_list.addItem(item)

    def _update_buttons(self) -> None:
        case = self.controller.case
        idle = case is not None and not self.controller.busy
        doc = self.selected_document()
        has_docs = bool(case and case.active_documents())
        self.btn_add.setEnabled(idle)
        self.btn_remove.setEnabled(idle and doc is not None)
        self.btn_exclude.setEnabled(idle and doc is not None)
        self.btn_exclude.setText("Einbeziehen" if doc is not None and doc.excluded else "Ausschließen")
        self.btn_process.setEnabled(idle and has_docs)
        self.btn_ai.setEnabled(idle and has_docs and self._ai_ready)
        self.btn_next.setEnabled(case is not None and bool(case.contract_fields))

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------
    def add_files_clicked(self) -> None:
        paths = dialogs.pick_open_files(self)
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths: list[Path]) -> None:
        case = self.controller.case
        if case is None or self.controller.busy:
            return
        report = add_files(self, self.service, case, paths)
        self.controller.data_changed.emit()
        if report.problems:
            dialogs.show_info(self, "Hinweis zu einzelnen Dateien", "\n".join(report.problems))
        if report.accepted:
            self._start_processing()

    def remove_clicked(self) -> None:
        doc = self.selected_document()
        if doc is None:
            return
        ok = dialogs.ask_confirm(
            self,
            "Datei entfernen",
            f"„{doc.original_name}“ aus diesem Vorgang entfernen?\n\n"
            "Die Kopie im Vorgangsordner wird gelöscht. Ihre Originaldatei bleibt unverändert. "
            "Die Auswertung wird danach aktualisiert.",
            ok_text="Entfernen",
            danger=True,
        )
        if not ok:
            return
        self.controller.act(
            self, lambda c: self.service.remove_document(c, doc.id), error_title="Datei nicht entfernt"
        )

    def toggle_excluded_clicked(self) -> None:
        doc = self.selected_document()
        if doc is None:
            return
        new_state = not doc.excluded
        result = self.controller.act(
            self,
            lambda c: self.service.set_document_excluded(c, doc.id, new_state) or True,
            error_title="Änderung nicht möglich",
        )
        if result:
            self._start_processing()  # Auswertung ohne/mit dieser Datei aktualisieren

    def _type_changed(self, doc_id: str, value: str | None) -> None:
        if value is None:
            return
        result = self.controller.act(
            self,
            lambda c: self.service.set_document_type(c, doc_id, DocumentType(value)) or True,
            error_title="Dokumenttyp nicht geändert",
        )
        if result:
            self._start_processing()  # Zuordnung (z. B. Nachtrag maßgeblich) neu berechnen

    def local_ai_clicked(self) -> None:
        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            msg = data.get("message") or (
                f"Die lokale KI hat {data.get('candidates', 0)} Vorschläge geliefert. "
                "Abweichungen erscheinen in der Datenprüfung als Konflikt."
            )
            errors = data.get("errors") or []
            if errors:
                msg += "\n\nHinweise:\n" + "\n".join(f"• {e}" for e in errors[:5])
            dialogs.show_info(self, "Lokale KI-Zweitprüfung", str(msg))

        self.controller.run(
            self._task_parent or self,
            "Lokale KI prüft schwierige Seiten …",
            lambda case, progress, cancel: self.service.run_local_ai(case, progress=progress, cancel=cancel),
            done,
            error_title="Lokale KI-Zweitprüfung nicht möglich",
        )
