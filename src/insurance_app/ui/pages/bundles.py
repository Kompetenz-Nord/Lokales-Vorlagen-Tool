"""Seite „Kundenmappen“: mehrere Verträge einer Kundin/eines Kunden in einem Durchgang bearbeiten.

Konstruktor laut Hauptfenster: ``BundlesPage(service, open_case)`` mit ``refresh()``,
``select_bundle(bundle_id)`` und ``start_import(paths)``.

Ablauf
1. Mappe anlegen, alle Dokumente ablegen -> Warteschlange liest sie nacheinander (Hintergrund)
   und legt je Vertragsnummer einen Vorgang an (Police + Nachtrag -> ein Vorgang).
2. Je Vertrag den Anwenderwunsch (Vorlage) wählen und den Vorgang prüfen („Prüfen & bearbeiten“).
   Die menschliche Prüfung je Vorgang bleibt Pflicht.
3. „Alle fertigen erzeugen“ erzeugt nacheinander alle bereiten Vorgänge; übersprungene mit Grund.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Bundle
from ...core.service import AppService, BatchItem, BundleCaseRow, BundleExportRow, TemplateChoice
from .. import dialogs, theme, workers
from ..bundle.common import (
    BUNDLE_EXPLANATION,
    GENERATE_CONFIRM_TEXT,
    ask_bundle_title,
    contracts_text,
)
from ..bundle.importing import BundleImport, summary_text
from ..bundle.tables import ContractsTable, QueueTable, ResultTable, short_title
from ..case.common import error_text
from ..case.drop_area import DropArea
from ..case.formatting import format_local_time
from ..case.importing import local_files
from ..widgets.common import card, danger_button, muted, page_title, primary_button, section_title

log = logging.getLogger(__name__)

EMPTY_NO_BUNDLES = (
    "Noch keine Kundenmappe. Legen Sie mit „Neue Kundenmappe“ eine an – oder legen Sie auf dem "
    "Dashboard mehrere Dateien ab und wählen Sie „Mehrere Verträge – Kundenmappe“."
)
EMPTY_NO_SELECTION = "Bitte links eine Kundenmappe wählen."

DISSOLVE_TEXT = (
    "Die Kundenmappe „{title}“ auflösen?\n\n"
    "Die {count} bleiben vollständig erhalten (Dokumente, geprüfte Daten, Verlauf) und stehen weiter "
    "unter „Dashboard / Vorgänge“. Nur die Zusammenfassung als Mappe entfällt."
)
DELETE_ALL_TEXT = (
    "Die Kundenmappe „{title}“ und ALLE enthaltenen Vorgänge ({count}) wirklich löschen?\n\n"
    "Dabei werden alle lokal gespeicherten Dokumente, erkannten Daten und der Verlauf dieser Vorgänge "
    "unwiderruflich gelöscht. Das kann nicht rückgängig gemacht werden.\n\n"
    "Bereits erzeugte PDFs im Exportordner bleiben erhalten."
)


class BundlesPage(QWidget):
    def __init__(self, service: AppService, open_case: Callable[[str], None]) -> None:
        super().__init__()
        self.service = service
        self._open_case = open_case
        self.bundles: list[Bundle] = []
        self.overview: list[BundleCaseRow] = []
        self.importer: BundleImport | None = None
        self.generate_task: workers.Task | None = None
        self._busy = False
        self._queues: dict[str, list[BatchItem]] = {}
        self._results: dict[str, list[BundleExportRow]] = {}
        self._choices: list[TemplateChoice] | None = None
        self._shown_id: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 12)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(page_title("Kundenmappen"), 1)
        local = muted(theme.LOCAL_ONLY_TEXT, wrap=False)
        local.setToolTip("Alle Dokumente und Ergebnisse bleiben auf diesem Gerät.")
        head.addWidget(local)
        outer.addLayout(head)
        outer.addWidget(
            muted(
                "Eine Kundenmappe bündelt mehrere Verträge einer Kundin bzw. eines Kunden – z. B. drei "
                "Verträge bei verschiedenen Gesellschaften mit jeweils eigenem Wunsch."
            )
        )

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(objectName="BundleContent")
        self.scroll.viewport().setObjectName("BundleViewport")
        self.scroll.setStyleSheet("#BundleViewport, #BundleContent { background: transparent; }")
        self.scroll.viewport().setAutoFillBackground(False)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 8, 8)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(self._build_left())
        self.detail_stack = QStackedWidget()
        self.empty_label = muted(EMPTY_NO_BUNDLES)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setContentsMargins(4, 8, 4, 4)
        ev.addWidget(self.empty_label)
        ev.addStretch(1)
        self.detail_stack.addWidget(empty)
        self.detail_stack.addWidget(self._build_head())
        top.addWidget(self.detail_stack, 1)
        lay.addLayout(top)
        self.lower = self._build_lower()
        lay.addWidget(self.lower)
        lay.addStretch(1)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)

        self.refresh()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def _build_left(self) -> QWidget:
        left = QWidget()
        left.setFixedWidth(290)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(section_title("Mappen"))
        self.bundle_list = QListWidget()
        self.bundle_list.setAccessibleName("Liste der Kundenmappen")
        self.bundle_list.setWordWrap(True)
        self.bundle_list.setSpacing(2)
        self.bundle_list.setMinimumHeight(170)
        self.bundle_list.currentItemChanged.connect(lambda *_: self._on_selection())
        lv.addWidget(self.bundle_list, 1)
        self.new_btn = primary_button(
            "Neue Kundenmappe", "Neue Mappe für eine Kundin bzw. einen Kunden anlegen"
        )
        self.new_btn.clicked.connect(self.new_bundle)
        lv.addWidget(self.new_btn)
        return left

    def _build_head(self) -> QWidget:
        """Kopf der gewählten Mappe: Titel, Verwaltung, Erklärung, Ablagefläche."""
        head = QWidget()
        lay = QVBoxLayout(head)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.title_label = QLabel("", objectName="SectionTitle")
        self.title_label.setStyleSheet("font-size: 14pt;")
        self.title_label.setWordWrap(True)
        lay.addWidget(self.title_label)
        self.meta_label = muted("")
        lay.addWidget(self.meta_label)

        manage = QHBoxLayout()
        self.rename_btn = QPushButton("Umbenennen …")
        self.rename_btn.setAccessibleName("Kundenmappe umbenennen")
        self.rename_btn.clicked.connect(self.rename_selected)
        self.dissolve_btn = QPushButton("Mappe auflösen (Vorgänge behalten) …")
        self.dissolve_btn.setAccessibleName("Mappe auflösen, Vorgänge behalten")
        self.dissolve_btn.setToolTip("Die Mappe entfällt, alle Vorgänge bleiben unter „Vorgänge“ erhalten.")
        self.dissolve_btn.clicked.connect(self.dissolve_selected)
        self.delete_btn = danger_button(
            "Mappe und Vorgänge löschen …",
            "Mappe und alle enthaltenen Vorgänge mit Dokumenten unwiderruflich löschen",
        )
        self.delete_btn.setAccessibleName("Mappe und Vorgänge löschen")
        self.delete_btn.setStyleSheet(
            f"QPushButton:disabled {{ color: {theme.DISABLED_TEXT}; border-color: {theme.BORDER}; background: {theme.BG_SUBTLE}; }}"
        )
        self.delete_btn.clicked.connect(self.delete_selected)
        for btn in (self.rename_btn, self.dissolve_btn, self.delete_btn):
            manage.addWidget(btn)
        manage.addStretch(1)
        lay.addLayout(manage)
        self.explanation = muted(BUNDLE_EXPLANATION)
        lay.addWidget(self.explanation)

        self.drop_area = DropArea()
        self.drop_area.setAccessibleName("Ablagefläche für die Dokumente der Kundenmappe")
        self.drop_area.setMinimumHeight(88)
        self.drop_area.files_dropped.connect(self.start_import)
        dv = QHBoxLayout(self.drop_area)
        dv.setContentsMargins(20, 10, 20, 10)
        texts = QVBoxLayout()
        big = QLabel("📥  Dokumente der Kundin bzw. des Kunden hier ablegen")
        big.setStyleSheet("font-size: 12.5pt; font-weight: 700; background: transparent; border: none;")
        texts.addWidget(big)
        sub = QLabel(
            "PDF, PNG, JPG oder TIFF – beliebig viele Dateien, auch verschiedener Gesellschaften. "
            "Die Dateien werden nur als Kopie lokal gespeichert."
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        sub.setStyleSheet("background: transparent; border: none;")
        texts.addWidget(sub)
        dv.addLayout(texts, 1)
        self.choose_btn = primary_button("Dateien auswählen …", "Dateien von diesem Computer auswählen")
        self.choose_btn.setAccessibleName("Dateien auswählen")
        self.choose_btn.clicked.connect(self.choose_files)
        dv.addWidget(self.choose_btn)
        lay.addWidget(self.drop_area)
        lay.addStretch(1)
        return head

    def _build_lower(self) -> QWidget:
        """Warteschlange, Verträge der Mappe, Erzeugen und Ergebnis (volle Breite)."""
        lower = QWidget()
        lay = QVBoxLayout(lower)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)

        lay.addWidget(section_title("Warteschlange"))
        self.queue_summary = muted("Noch keine Dateien in der Warteschlange.")
        lay.addWidget(self.queue_summary)
        self.queue_table = QueueTable()
        lay.addWidget(self.queue_table)

        lay.addWidget(section_title("Verträge in dieser Mappe"))
        self.contracts_hint = muted(
            "Je Vertrag den Anwenderwunsch wählen und mit „Prüfen & bearbeiten“ die Daten prüfen. "
            "Erst danach ist der Vorgang „✔ bereit“."
        )
        lay.addWidget(self.contracts_hint)
        self.contracts_table = ContractsTable()
        self.contracts_table.template_chosen.connect(self._template_chosen)
        self.contracts_table.open_requested.connect(self._open_case_clicked)
        lay.addWidget(self.contracts_table)

        actions = QHBoxLayout()
        self.generate_btn = primary_button(
            "Alle fertigen erzeugen",
            "Alle Vorgänge mit „✔ bereit“ nacheinander erzeugen. Nicht bereite werden mit Grund übersprungen.",
        )
        self.generate_btn.clicked.connect(self.generate_all)
        actions.addWidget(self.generate_btn)
        self.ready_label = muted("", wrap=False)
        actions.addWidget(self.ready_label, 1)
        lay.addLayout(actions)

        self.result_frame, rl = card()
        self.result_frame.setAccessibleName("Ergebnis der Erzeugung")
        rl.addWidget(section_title("Ergebnis der Erzeugung"))
        self.result_summary = muted("")
        rl.addWidget(self.result_summary)
        self.result_table = ResultTable()
        rl.addWidget(self.result_table)
        rrow = QHBoxLayout()
        self.open_export_btn = QPushButton("Exportordner öffnen")
        self.open_export_btn.setToolTip("Den lokalen Ordner mit den erzeugten PDFs öffnen")
        self.open_export_btn.clicked.connect(self.open_export_dir)
        rrow.addWidget(self.open_export_btn)
        rrow.addStretch(1)
        rl.addLayout(rrow)
        self.result_frame.setVisible(False)
        lay.addWidget(self.result_frame)
        return lower

    # ------------------------------------------------------------------
    # Zustand
    # ------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        bid = self.selected_bundle_id()
        idle = not self._busy
        self.bundle_list.setEnabled(idle)
        self.new_btn.setEnabled(idle)
        for btn in (self.rename_btn, self.dissolve_btn, self.delete_btn):
            btn.setEnabled(idle and bid is not None)
        self.choose_btn.setEnabled(idle and bid is not None)
        self.drop_area.setAcceptDrops(idle and bid is not None)
        self.contracts_table.setEnabled(idle)
        self.generate_btn.setEnabled(idle and bool(self.overview))
        self.open_export_btn.setEnabled(idle)

    def selected_bundle_id(self) -> str | None:
        item = self.bundle_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def selected_bundle(self) -> Bundle | None:
        bid = self.selected_bundle_id()
        return next((b for b in self.bundles if b.id == bid), None)

    def queue_items(self, bundle_id: str | None = None) -> list[BatchItem]:
        bid = bundle_id or self.selected_bundle_id()
        return list(self._queues.get(bid or "", []))

    # ------------------------------------------------------------------
    # Liste der Mappen
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._busy:
            return
        self._choices = None  # Vorlagen können sich im Studio geändert haben
        self._reload_list(self.selected_bundle_id())

    def _reload_list(self, select_id: str | None) -> None:
        try:
            self.bundles = self.service.list_bundles()
        except Exception as exc:  # noqa: BLE001
            log.warning("Kundenmappen nicht lesbar: %s", type(exc).__name__)
            self.bundles = []
        self.bundle_list.blockSignals(True)
        try:
            self.bundle_list.clear()
            current = None
            for b in self.bundles:
                item = QListWidgetItem(
                    f"{b.title}\n{format_local_time(b.created_at)} · {contracts_text(b.case_count)}"
                )
                item.setData(Qt.ItemDataRole.UserRole, b.id)
                item.setToolTip(f"{b.title}\nMappen-ID: {b.id}")
                self.bundle_list.addItem(item)
                if b.id == select_id:
                    current = item
            if current is None and self.bundles:
                current = self.bundle_list.item(0)
            if current is not None:
                self.bundle_list.setCurrentItem(current)
        finally:
            self.bundle_list.blockSignals(False)
        self._show_selected()

    def select_bundle(self, bundle_id: str) -> bool:
        if self._busy:  # während einer Warteschlange bleibt die laufende Mappe sichtbar
            return self._shown_id == bundle_id
        if not any(b.id == bundle_id for b in self.bundles):
            self._reload_list(bundle_id)
            return self.selected_bundle_id() == bundle_id
        for row in range(self.bundle_list.count()):
            item = self.bundle_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == bundle_id:
                self.bundle_list.setCurrentItem(item)  # löst _on_selection aus
                if self._shown_id != bundle_id:
                    self._show_selected()
                return True
        return False

    def bundle_titles(self) -> list[str]:
        return [b.title for b in self.bundles]

    def _on_selection(self) -> None:
        if self._busy:
            return
        self._show_selected()

    def _show_selected(self) -> None:
        bundle = self.selected_bundle()
        self._shown_id = bundle.id if bundle else None
        if bundle is None:
            self.overview = []
            self.empty_label.setText(EMPTY_NO_BUNDLES if not self.bundles else EMPTY_NO_SELECTION)
            self.detail_stack.setCurrentIndex(0)
            self.lower.setVisible(False)
            self._update_buttons()
            return
        self.detail_stack.setCurrentIndex(1)
        self.lower.setVisible(True)
        self.title_label.setText(bundle.title)
        self.title_label.setAccessibleName(f"Kundenmappe {bundle.title}")
        self.refresh_overview()
        self._render_results()

    # ------------------------------------------------------------------
    # Verträge der Mappe
    # ------------------------------------------------------------------
    def _template_choices(self) -> list[TemplateChoice]:
        if self._choices is None:
            try:
                self._choices = [c for c in self.service.template_choices() if c.available]
            except Exception as exc:  # noqa: BLE001
                log.warning("Vorlagen nicht lesbar: %s", type(exc).__name__)
                return []
        return self._choices

    def refresh_overview(self) -> None:
        bundle = self.selected_bundle()
        if bundle is None:
            return
        try:
            self.overview = self.service.bundle_overview(bundle.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Mappenübersicht nicht lesbar: %s", type(exc).__name__)
            self.overview = []
            self.contracts_hint.setText(f"Übersicht nicht lesbar: {error_text(exc)}")
        self.contracts_table.set_rows(self.overview, self._template_choices(), bundle.title)
        ready = sum(1 for r in self.overview if r.ready)
        count = len(self.overview)
        self.meta_label.setText(
            f"Angelegt am {format_local_time(bundle.created_at)} · {contracts_text(count)} · "
            f"{ready} bereit zum Erzeugen"
        )
        self.ready_label.setText(
            f"{ready} von {count} Vorgängen bereit. Die Prüfung je Vorgang bleibt Pflicht."
            if count
            else "Noch keine Verträge – bitte zuerst Dokumente ablegen."
        )
        self._render_queue()
        self._update_buttons()

    def _titles(self) -> dict[str, str]:
        bundle = self.selected_bundle()
        return {r.case_id: short_title(r.title, bundle.title if bundle else None) for r in self.overview}

    def _render_queue(self) -> None:
        items = self._queues.get(self._shown_id or "", [])
        self.queue_table.set_items(items, self._titles())
        self.queue_summary.setText(summary_text(items))

    def _template_chosen(self, case_id: str, catalog_id: object) -> None:
        if self._busy:
            return
        try:
            case = self.service.load_case(case_id)
            if catalog_id:
                self.service.select_template(case, str(catalog_id))
            else:
                self.service.clear_template(case)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Anwenderwunsch nicht übernommen", error_text(exc))
        self.refresh_overview()

    def _open_case_clicked(self, case_id: str) -> None:
        if not self._busy:
            self._open_case(case_id)

    # ------------------------------------------------------------------
    # Mappen verwalten
    # ------------------------------------------------------------------
    def new_bundle(self) -> str | None:
        if self._busy:
            return None
        title = ask_bundle_title(self)
        if title is None:
            return None
        try:
            bundle = self.service.create_bundle(title)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Kundenmappe nicht angelegt", error_text(exc))
            return None
        self._reload_list(bundle.id)
        return bundle.id

    def rename_selected(self) -> None:
        bundle = self.selected_bundle()
        if bundle is None or self._busy:
            return
        title = ask_bundle_title(self, "Kundenmappe umbenennen", bundle.title)
        if title is None:
            return
        try:
            self.service.rename_bundle(bundle.id, title)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Nicht umbenannt", error_text(exc))
        self._reload_list(bundle.id)

    def dissolve_selected(self) -> None:
        bundle = self.selected_bundle()
        if bundle is None or self._busy:
            return
        count = "Vorgänge" if bundle.case_count != 1 else "Vorgang"
        count = f"{bundle.case_count} {count}"
        ok = dialogs.ask_confirm(
            self,
            "Kundenmappe auflösen",
            DISSOLVE_TEXT.format(title=bundle.title, count=count),
            ok_text="Mappe auflösen",
        )
        if ok:
            self._delete(bundle, delete_cases=False)

    def delete_selected(self) -> None:
        bundle = self.selected_bundle()
        if bundle is None or self._busy:
            return
        ok = dialogs.ask_confirm(
            self,
            "Mappe und Vorgänge endgültig löschen",
            DELETE_ALL_TEXT.format(title=bundle.title, count=bundle.case_count),
            ok_text="Endgültig löschen",
            danger=True,
        )
        if ok:
            self._delete(bundle, delete_cases=True)

    def _delete(self, bundle: Bundle, *, delete_cases: bool) -> None:
        try:
            self.service.delete_bundle(bundle.id, delete_cases=delete_cases)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Kundenmappe nicht gelöscht", error_text(exc))
        self._queues.pop(bundle.id, None)
        self._results.pop(bundle.id, None)
        self._reload_list(None)

    # ------------------------------------------------------------------
    # Warteschlange (Import)
    # ------------------------------------------------------------------
    def choose_files(self) -> None:
        if self._busy or self.selected_bundle_id() is None:
            return
        paths = dialogs.pick_open_files(self)
        if paths:
            self.start_import(paths)

    def start_import(self, paths: list[Path]) -> bool:
        """Dateien in die Warteschlange der gewählten Mappe übernehmen und nacheinander lesen."""
        bid = self.selected_bundle_id()
        if self._busy:
            dialogs.show_info(
                self,
                "Bitte warten",
                "Es läuft noch eine Warteschlange bzw. Erzeugung. Bitte warten, bis sie beendet ist, "
                "und die Dateien danach erneut ablegen.",
            )
            return False
        if bid is None:
            return False
        files = local_files([Path(p) for p in paths])
        if not files:
            dialogs.show_info(
                self, "Keine Dateien", "Es wurden keine Dateien übergeben (Ordner werden nicht übernommen)."
            )
            return False
        queue = self._queues.setdefault(bid, [])
        self.importer = BundleImport(self, self.service, bid, files, queue)
        self.importer.changed.connect(self._render_queue)
        self.importer.finished.connect(self._import_finished)
        self._set_busy(True)
        self._render_queue()
        self.importer.start()
        return True

    def _import_finished(self, _items: object) -> None:
        importer = self.importer
        self.importer = None
        self._set_busy(False)
        if importer is not None:
            importer.deleteLater()
        self._choices = None
        self._reload_list(self.selected_bundle_id())

    # ------------------------------------------------------------------
    # Alle fertigen erzeugen
    # ------------------------------------------------------------------
    def generate_all(self) -> None:
        bundle = self.selected_bundle()
        if bundle is None or self._busy:
            return
        if not dialogs.ask_confirm(
            self, "Alle fertigen erzeugen", GENERATE_CONFIRM_TEXT, ok_text="Jetzt erzeugen"
        ):
            return
        bid = bundle.id
        self._set_busy(True)

        def done(rows: object) -> None:
            self.generate_task = None
            self._results[bid] = list(rows) if isinstance(rows, list) else []
            self._set_busy(False)
            self._reload_list(bid)
            self.scroll.ensureWidgetVisible(self.result_frame)

        def failed(exc: BaseException) -> None:
            self.generate_task = None
            self._set_busy(False)
            self._reload_list(bid)
            dialogs.show_error(self, "Erzeugung nicht abgeschlossen", error_text(exc))

        self.generate_task = workers.run_task(
            self,
            "Bereite Vorgänge werden nacheinander erzeugt (lokal) …",
            lambda progress, cancel: self.service.generate_bundle(bid, progress=progress, cancel=cancel),
            done,
            failed,
        )

    def _render_results(self) -> None:
        rows = self._results.get(self._shown_id or "")
        self.result_frame.setVisible(rows is not None)
        if rows is None:
            return
        bundle = self.selected_bundle()
        self.result_table.set_rows(rows, bundle.title if bundle else None)
        made = sum(1 for r in rows if r.ok)
        skipped = len(rows) - made
        text = f"{made} {'Dokument' if made == 1 else 'Dokumente'} erzeugt, {skipped} übersprungen."
        if skipped:
            text += " Übersprungene Vorgänge bitte mit „Prüfen & bearbeiten“ öffnen und klären."
        if made:
            text += " Die Dateien sind Entwürfe zur Prüfung – es wurde nichts versendet."
        self.result_summary.setText(text)

    def scroll_to_queue(self) -> None:
        """Zur Warteschlange scrollen (Tabellen „Warteschlange“ und „Verträge“ im Blick)."""
        self.scroll.verticalScrollBar().setValue(max(self.lower.y() - 4, 0))

    def open_export_dir(self) -> None:
        path = self.service.export_dir
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            dialogs.show_error(self, "Exportordner", error_text(exc))
            return
        dialogs.open_path(path)
