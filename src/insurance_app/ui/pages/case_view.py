"""Vorgangsansicht: Kopf, 9-Schritte-Anzeige und Reiter Dokumente / Datenprüfung / Vorschau & Export / Verlauf.

Konstruktor laut Hauptfenster: ``CaseView(service, show_catalog)`` mit ``open_case(case_id)``,
``current_case_id()``, ``apply_template(catalog_id)`` und ``refresh()``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.case import PipelineStage
from ...core.service import AppService
from .. import dialogs, theme
from ..case.common import error_text
from ..case.controller import CaseController
from ..case.documents_panel import DocumentsPanel
from ..case.history_panel import HistoryPanel
from ..case.importing import LOCKED_SKIPPED_NOTE, ensure_passwords
from ..case.preview_panel import PreviewPanel
from ..case.review_panel import ReviewPanel
from ..case.stepper import StageStepper
from ..widgets.common import badge, muted, page_title

log = logging.getLogger(__name__)

TAB_DOCUMENTS, TAB_REVIEW, TAB_PREVIEW, TAB_HISTORY = range(4)
TAB_TITLES = ("Dokumente", "Datenprüfung", "Vorschau && Export", "Verlauf")  # && = wörtliches &

# Stufe -> Reiter (1–3 Dokumente, 4–6 Datenprüfung, 7–9 Vorschau & Export)
STAGE_TAB = {
    **{s: TAB_DOCUMENTS for s in (1, 2, 3)},
    **{s: TAB_REVIEW for s in (4, 5, 6)},
    **{s: TAB_PREVIEW for s in (7, 8, 9)},
}

# Vorgänge, die direkt nach dem Öffnen ausgewertet werden sollen (z. B. nach dem Ablegen auf dem Dashboard)
_PENDING_PROCESSING: set[str] = set()


def request_processing(case_id: str) -> None:
    """Beim nächsten Öffnen dieses Vorgangs automatisch „Texterkennung & Auswertung“ starten."""
    _PENDING_PROCESSING.add(case_id)


def consume_processing_request(case_id: str) -> bool:
    if case_id in _PENDING_PROCESSING:
        _PENDING_PROCESSING.discard(case_id)
        return True
    return False


class CaseView(QWidget):
    case_changed = Signal(str)

    def __init__(self, service: AppService, show_catalog: Callable[[], None]) -> None:
        super().__init__()
        self.service = service
        self._show_catalog = show_catalog
        self.controller = CaseController(service, self)
        self._rendered_case_id: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 8)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        # --- leerer Zustand ---------------------------------------------
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addWidget(page_title("Vorgang"))
        ev.addWidget(
            muted(
                "Kein Vorgang geöffnet. Bitte auf dem Dashboard einen Vorgang öffnen, Dateien ablegen "
                "oder den Musterfall laden."
            )
        )
        ev.addStretch(1)
        self.stack.addWidget(empty)

        # --- geöffneter Vorgang -----------------------------------------
        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)
        head = QHBoxLayout()
        self.title_label = page_title("Vorgang")
        head.addWidget(self.title_label, 1)
        self.local_badge = badge(
            theme.LOCAL_ONLY_TEXT,
            theme.OK,
            "Alle Dokumente und Ergebnisse dieses Vorgangs bleiben auf diesem Gerät.",
        )
        self.local_badge.setAccessibleName("Lokale Verarbeitung – keine Cloud-Übertragung")
        head.addWidget(self.local_badge)
        cv.addLayout(head)
        self.meta_label = muted("")
        self.meta_label.setToolTip("Die Vorgangs-ID ist eine kurze Kennung ohne persönliche Daten.")
        cv.addWidget(self.meta_label)

        self.stepper = StageStepper()
        self.stepper.step_clicked.connect(self._step_clicked)
        cv.addWidget(self.stepper)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Bereiche des Vorgangs")
        self.documents = DocumentsPanel(
            service,
            self.controller,
            start_processing=self.start_processing,
            goto_review=lambda: self.show_tab(TAB_REVIEW),
            task_parent=self,
        )
        self.review = ReviewPanel(service, self.controller, show_catalog=self._show_catalog, task_parent=self)
        self.preview = PreviewPanel(
            service, self.controller, goto_review=lambda: self.show_tab(TAB_REVIEW), task_parent=self
        )
        self.history = HistoryPanel(service, self.controller)
        for panel, title in zip(
            (self.documents, self.review, self.preview, self.history), TAB_TITLES, strict=True
        ):
            self.tabs.addTab(panel, title)
        self.tabs.setTabToolTip(TAB_DOCUMENTS, "Schritte 1–3: Ablage, Dokumentcheck, Texterkennung")
        self.tabs.setTabToolTip(TAB_REVIEW, "Schritte 4–6: erkannte Werte prüfen und Auftragsdaten wählen")
        self.tabs.setTabToolTip(TAB_PREVIEW, "Schritte 7–9: Vorschau, Qualitätskontrolle und PDF erzeugen")
        self.tabs.setTabToolTip(TAB_HISTORY, "Wer hat wann was geändert (nur lokal gespeichert)")
        self.tabs.currentChanged.connect(self._tab_changed)
        cv.addWidget(self.tabs, 1)
        self.stack.addWidget(content)

        self.controller.case_loaded.connect(self._on_case_loaded)
        self.controller.data_changed.connect(self._on_data_changed)
        self.controller.busy_changed.connect(self._on_busy_changed)
        self.stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # Öffentliche Schnittstelle (Hauptfenster)
    # ------------------------------------------------------------------
    def open_case(self, case_id: str) -> None:
        if self.controller.busy:
            if case_id != self.controller.case_id:
                dialogs.show_info(
                    self, "Bitte warten", "Ein Arbeitsschritt läuft noch. Bitte warten, bis er beendet ist."
                )
            return
        same = case_id == self.controller.case_id
        try:
            self.controller.open(case_id)
        except Exception as exc:  # noqa: BLE001 – z. B. gelöscht oder beschädigt
            dialogs.show_error(self, "Vorgang nicht geöffnet", error_text(exc))
            return
        if not same:
            self.show_tab(self._default_tab())
            self.tabs.tabBar().setFocus()
        if consume_processing_request(case_id):
            self.show_tab(TAB_DOCUMENTS)
            self.start_processing()

    def current_case_id(self) -> str | None:
        return self.controller.case_id

    def apply_template(self, catalog_id: str) -> None:
        if self.controller.busy:
            dialogs.show_info(self, "Bitte warten", "Ein Arbeitsschritt läuft noch.")
            return
        if self.controller.case is not None:
            self.controller.reload()  # inzwischen gelöscht? Dann nicht wieder anlegen
        if self.controller.case is None:
            try:
                case = self.service.create_case()
                self.controller.open(case.id)
            except Exception as exc:  # noqa: BLE001
                dialogs.show_error(self, "Vorgang nicht angelegt", error_text(exc))
                return
        if self.review.select_template(catalog_id):
            self.show_tab(TAB_REVIEW)
        self.refresh()

    def refresh(self) -> None:
        if self.controller.busy:
            return
        if self.controller.case is not None:
            self.review.refresh_choices()
            self.documents.update_local_ai_state()  # Einstellungen können sich geändert haben
            self.controller.reload()
        else:
            self._render_all()

    # ------------------------------------------------------------------
    def show_tab(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)

    def _default_tab(self) -> int:
        case = self.controller.case
        if case is None or not case.contract_fields and not case.template_id:
            return TAB_DOCUMENTS
        if case.exports:
            return TAB_PREVIEW
        return TAB_REVIEW

    def start_processing(self) -> None:
        case = self.controller.case
        if case is None or self.controller.busy:
            return
        if not any(d.check and d.check.ok for d in case.active_documents()):
            dialogs.show_info(
                self,
                "Nichts auszuwerten",
                "Es gibt keine lesbaren Dateien in diesem Vorgang. Bitte zuerst Dateien hinzufügen.",
            )
            return

        # Passwörter werden nie gespeichert: nach einem Neustart vor dem Lesen erneut abfragen
        skipped = ensure_passwords(self, self.service, case, only_unread=True)
        missing = [LOCKED_SKIPPED_NOTE.format(name=name) for name in skipped]

        def done(result: object) -> None:
            notes, summary = result if isinstance(result, tuple) else ([], None)
            self.documents.set_result(missing + list(notes), summary)

        def failed(exc: BaseException, cancelled: bool) -> None:
            if cancelled:
                dialogs.show_info(
                    self,
                    "Abgebrochen",
                    "Die Texterkennung wurde abgebrochen. Bereits gelesene Dokumente bleiben erhalten. "
                    "Sie können die Auswertung jederzeit erneut starten.",
                )
            else:
                dialogs.show_error(self, "Auswertung nicht abgeschlossen", error_text(exc))

        self.controller.run(
            self,
            "Texterkennung und Auswertung laufen (lokal) …",
            lambda c, progress, cancel: self.service.process_documents(c, progress=progress, cancel=cancel),
            done,
            failed,
        )

    # ------------------------------------------------------------------
    def _on_case_loaded(self) -> None:
        case = self.controller.case
        new_id = case.id if case is not None else None
        if new_id != self._rendered_case_id:
            self.documents.clear_result()
            self.preview.reset()
            self.review.reset_view()
            self._rendered_case_id = new_id
            if case is not None:
                self.documents.update_local_ai_state()
        else:
            self.preview.invalidate()
        self._render_all()
        if new_id:
            self.case_changed.emit(new_id)

    def _on_data_changed(self) -> None:
        self.preview.invalidate()
        self._render_all()

    def _render_all(self) -> None:
        case = self.controller.case
        self.stack.setCurrentIndex(1 if case is not None else 0)
        self.documents.render()
        self.review.render()
        self.preview.render()
        self._render_header()
        self.history.dirty = True
        if self.tabs.currentIndex() == TAB_HISTORY:
            self.history.render()

    def _render_header(self) -> None:
        case = self.controller.case
        if case is None:
            return
        self.title_label.setText(case.display_title())
        self.title_label.setAccessibleName(case.display_title())
        tpl = ""
        if case.template_id:
            ch = self.review.choice_for(case.template_id)
            tpl = f" · Vorlage: {ch.title if ch else case.template_id}"
        docs = len(case.documents)
        self.meta_label.setText(f"Vorgangs-ID: {case.id} · {docs} Dokument(e){tpl}")
        finished = bool(case.exports) and case.stage == PipelineStage.EXPORT
        self.stepper.set_stage(case.stage, finished=finished)

    def _on_busy_changed(self, busy: bool) -> None:
        # Nur Inhalte sperren – der Fortschrittsdialog (Kind dieser Seite) bleibt bedienbar.
        self.tabs.setEnabled(not busy)
        self.stepper.setEnabled(not busy)

    def _step_clicked(self, stage: int) -> None:
        self.show_tab(STAGE_TAB.get(stage, TAB_DOCUMENTS))

    def _tab_changed(self, index: int) -> None:
        if index == TAB_HISTORY and self.history.dirty:
            self.history.render()
