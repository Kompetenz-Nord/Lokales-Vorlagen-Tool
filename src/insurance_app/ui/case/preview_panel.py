"""Reiter „Vorschau & Export“ (Stufen 7–9).

Ablauf: „Vorschau erzeugen“ (Probe-PDF + Checkliste + technische Qualitätskontrolle) →
„PDF jetzt erzeugen“ (nur ohne offene Pflichtpunkte und mit bestandener Technik-Prüfung,
nach ausdrücklicher Bestätigung) → Erfolgsanzeige mit „PDF öffnen“ / „Ordner öffnen“.
"""

from __future__ import annotations

import hashlib
import html
import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Case
from ...core.models import Severity
from ...core.service import AppService, ExportOutcome, PreviewResult
from .. import dialogs, theme
from ..widgets.common import muted, primary_button, section_title
from .checklist_panel import ChecklistPanel
from .common import clear_layout, error_text
from .controller import CaseController
from .formatting import format_local_time, severity_color, severity_symbol

log = logging.getLogger(__name__)

CONFIRM_TEXT = (
    "Die App erzeugt einen Entwurf zur Prüfung. Es wird nichts versendet, keine Unterschrift erzeugt "
    "und nichts gekündigt. PDF jetzt erzeugen?"
)
ZOOM_LEVELS = (75, 100, 150)
PREVIEW_DPI = 110
SCREEN_DPI = 96  # 100 % = Originalgröße auf einem üblichen Bildschirm


def data_fingerprint(case: Case | None) -> str | None:
    """Prüfsumme der für die PDF maßgeblichen Daten (ohne Zeitstempel, Verlauf, Stufe)."""
    if case is None:
        return None
    parts = [case.id, case.template_id or "", case.template_version or ""]
    for key, fv in sorted(case.all_fields().items()):
        parts.append(f"{key}={fv.status.value}|{fv.normalized_value}|{fv.value}")
    parts += [f"{d.id}:{int(d.excluded)}" for d in case.documents]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def qa_summary(preview: PreviewResult) -> str:
    if preview.qa is None:
        return "Keine Qualitätskontrolle vorhanden."
    total = len(preview.qa.checks)
    passed = sum(1 for c in preview.qa.checks if c.passed)
    return f"{passed} von {total} Prüfungen bestanden"


def export_blocker(preview: PreviewResult | None, stale: bool) -> str:
    """Grund, warum „PDF jetzt erzeugen“ gesperrt ist ("" = erlaubt)."""
    if preview is None:
        return "Bitte zuerst „Vorschau erzeugen“."
    if stale:
        return "Die Daten wurden nach der Vorschau geändert. Bitte die Vorschau neu erzeugen."
    if preview.blocking:
        return "Es gibt noch offene Pflichtpunkte (siehe Checkliste)."
    if preview.qa is None or not preview.qa.technical_passed:
        return "Die technische Qualitätskontrolle ist nicht bestanden (siehe Prüfergebnisse)."
    return ""


class PreviewPanel(QWidget):
    def __init__(
        self,
        service: AppService,
        controller: CaseController,
        *,
        goto_review: Callable[[], None],
        task_parent: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.controller = controller
        self._goto_review = goto_review
        self._task_parent = task_parent
        self.preview: PreviewResult | None = None
        self.stale = False
        self._fingerprint: str | None = None
        self.last_export: ExportOutcome | None = None
        self._pixmaps: list[QPixmap] = []
        self._zoom = 75
        self._titles: dict[str, str] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.preview_btn = primary_button(
            "Vorschau erzeugen",
            "Probe-PDF mit den freigegebenen Werten erzeugen und technisch prüfen (lokal, nichts wird gespeichert).",
        )
        self.preview_btn.setAccessibleName("Vorschau erzeugen")
        self.preview_btn.clicked.connect(self.build_preview)
        top.addWidget(self.preview_btn)
        self.preview_state = QLabel("")
        self.preview_state.setWordWrap(True)
        top.addWidget(self.preview_state, 1)
        layout.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        # --- Seitenansicht -----------------------------------------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        zoom_bar = QHBoxLayout()
        zoom_bar.addWidget(QLabel("Zoom:"))
        self.zoom_group = QButtonGroup(self)
        self.zoom_buttons: dict[int, QPushButton] = {}
        for z in ZOOM_LEVELS:
            b = QPushButton(f"{z} %")
            b.setCheckable(True)
            b.setAccessibleName(f"Zoom {z} Prozent")
            b.setStyleSheet(
                f"QPushButton:checked {{ background: #DCE8F7; border: 2px solid {theme.PRIMARY}; font-weight: 700; }}"
            )
            b.setChecked(z == self._zoom)
            b.clicked.connect(lambda _=False, zz=z: self.set_zoom(zz))
            self.zoom_group.addButton(b)
            self.zoom_buttons[z] = b
            zoom_bar.addWidget(b)
        zoom_bar.addStretch(1)
        lv.addLayout(zoom_bar)
        self.pages_scroll = QScrollArea()
        self.pages_scroll.setWidgetResizable(True)
        self.pages_scroll.setAccessibleName("Seiten der Vorschau")
        self.pages_widget = QWidget()
        self.pages_layout = QVBoxLayout(self.pages_widget)
        self.pages_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.pages_layout.setSpacing(12)
        self.pages_scroll.setWidget(self.pages_widget)
        lv.addWidget(self.pages_scroll, 1)
        split.addWidget(left)

        # --- Prüfergebnisse und Export ------------------------------------
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 8, 0)
        rv.setSpacing(8)

        # 1) Aktion zuerst: PDF erzeugen (mit Begründung, falls gesperrt)
        rv.addWidget(section_title("PDF erzeugen"))
        btns = QHBoxLayout()
        self.back_btn = QPushButton("Zurück zur Datenprüfung")
        self.back_btn.clicked.connect(self._goto_review)
        self.generate_btn = primary_button("PDF jetzt erzeugen")
        self.generate_btn.setAccessibleName("PDF jetzt erzeugen")
        self.generate_btn.clicked.connect(self.generate_pdf)
        btns.addWidget(self.back_btn)
        btns.addWidget(self.generate_btn)
        btns.addStretch(1)
        rv.addLayout(btns)
        self.blocker_label = QLabel("")
        self.blocker_label.setWordWrap(True)
        self.blocker_label.setStyleSheet(f"color: {theme.WARN}; font-weight: 600;")
        rv.addWidget(self.blocker_label)
        self.flatten_cb = QCheckBox("Formularfelder fixieren (flach)")
        self.flatten_cb.setAccessibleName("Formularfelder fixieren")
        rv.addWidget(self.flatten_cb)
        self.flatten_reason = muted("")
        rv.addWidget(self.flatten_reason)
        rv.addWidget(muted(CONFIRM_TEXT.replace(" PDF jetzt erzeugen?", "")))

        self.success = QFrame(objectName="Card")
        self.success.setStyleSheet("QFrame#Card { background: #E6F4EA; border: 1px solid #B7DFC2; }")
        sv = QVBoxLayout(self.success)
        self.success_title = QLabel("")
        self.success_title.setStyleSheet(f"color: {theme.OK}; font-weight: 700; font-size: 11.5pt;")
        self.success_title.setWordWrap(True)
        sv.addWidget(self.success_title)
        self.success_path = QLabel("")
        self.success_path.setWordWrap(True)
        self.success_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sv.addWidget(self.success_path)
        sbtns = QHBoxLayout()
        self.open_pdf_btn = QPushButton("PDF öffnen")
        self.open_pdf_btn.clicked.connect(self._open_pdf)
        self.open_dir_btn = QPushButton("Ordner öffnen")
        self.open_dir_btn.clicked.connect(self._open_dir)
        sbtns.addWidget(self.open_pdf_btn)
        sbtns.addWidget(self.open_dir_btn)
        sbtns.addStretch(1)
        sv.addLayout(sbtns)
        sv.addWidget(
            muted(
                "Bitte die PDF prüfen, ausdrucken und handschriftlich unterschreiben. Versand erfolgt durch Sie."
            )
        )
        self.success.hide()
        rv.addWidget(self.success)

        # 2) Checkliste und Prüfergebnisse
        self.checklist = ChecklistPanel("Checkliste")
        self.checklist.setMinimumHeight(170)
        rv.addWidget(self.checklist)

        rv.addWidget(section_title("Technische Qualitätskontrolle"))
        self.qa_summary = QLabel("")
        self.qa_summary.setStyleSheet("font-weight: 600;")
        rv.addWidget(self.qa_summary)
        self.qa_list = QListWidget()
        self.qa_list.setWordWrap(True)
        self.qa_list.setMinimumHeight(170)
        self.qa_list.setAccessibleName("Ergebnisse der Qualitätskontrolle")
        self.qa_list.setToolTip(
            "Die App öffnet die erzeugte Datei erneut, liest die Felder zurück und stellt jede Seite "
            "mit zwei Programmen (MuPDF und PDFium) dar."
        )
        rv.addWidget(self.qa_list)

        self.hints_title = section_title("Prüfbedürftige Hinweise zum Brief")
        rv.addWidget(self.hints_title)
        self.hints_label = QLabel("")
        self.hints_label.setTextFormat(Qt.TextFormat.RichText)
        self.hints_label.setWordWrap(True)
        rv.addWidget(self.hints_label)

        rv.addWidget(section_title("Bisher erzeugte PDFs dieses Vorgangs"))
        self.exports_table = QTableWidget(0, 4)
        self.exports_table.setAccessibleName("Erzeugte PDFs dieses Vorgangs")
        self.exports_table.setHorizontalHeaderLabels(["Datei", "Datum", "Vorlage", "QA-Zusammenfassung"])
        self.exports_table.verticalHeader().setVisible(False)
        self.exports_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.exports_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.exports_table.setMinimumHeight(120)
        self.exports_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.exports_table.horizontalHeader().setStretchLastSection(True)
        self.exports_table.cellDoubleClicked.connect(self._open_export_row)
        self.exports_table.setToolTip("Doppelklick öffnet die PDF")
        rv.addWidget(self.exports_table)
        rv.addStretch(1)
        right_scroll.setWidget(right)
        split.addWidget(right_scroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([600, 460])
        layout.addWidget(split, 1)

        controller.busy_changed.connect(lambda _b: self._update_controls())
        self.render()

    # ------------------------------------------------------------------
    def invalidate(self) -> None:
        """Nach Änderungen prüfen, ob die Vorschau noch zu den Daten passt (sonst „veraltet“)."""
        if self.preview is not None and data_fingerprint(self.controller.case) != self._fingerprint:
            self.stale = True
        self.render()

    def reset(self) -> None:
        self.preview = None
        self.stale = False
        self.last_export = None
        self._pixmaps = []
        self.success.hide()
        self.render()

    def render(self) -> None:
        case = self.controller.case
        preview = self.preview
        if case is None:
            self.preview_state.setText("Kein Vorgang geöffnet.")
        elif not case.template_id:
            self.preview_state.setText("✖ Bitte zuerst im Reiter „Datenprüfung“ eine Vorlage wählen.")
        elif preview is None:
            self.preview_state.setText(
                "Noch keine Vorschau. Die Vorschau zeigt die PDF mit den freigegebenen Werten."
            )
        elif self.stale:
            self.preview_state.setText("⚠ Vorschau veraltet – die Daten wurden geändert. Bitte neu erzeugen.")
        else:
            kind = "Formular (Originalvorlage)" if preview.kind == "form" else "Brief nach DIN 5008"
            self.preview_state.setText(f"✔ Vorschau aktuell: {kind}, {len(preview.page_images)} Seite(n).")
        self._render_pages()
        if preview is not None:
            self.checklist.set_items(preview.checklist, ready=not preview.blocking)
            self.qa_summary.setText(qa_summary(preview))
            self.qa_list.clear()
            checks = list(preview.qa.checks) if preview.qa else []
            checks.sort(key=lambda c: c.passed)  # nicht bestandene zuerst
            for c in checks:
                if c.passed:
                    sym, color = "✔", theme.OK
                elif c.severity == Severity.ERROR:
                    sym, color = "✖", theme.ERROR
                else:
                    sym, color = severity_symbol(c.severity), severity_color(c.severity)
                page = f" (Seite {c.page})" if c.page else ""
                item = QListWidgetItem(f"{sym} {c.message}{page}")
                item.setForeground(QColor(color))
                if c.code == "PLAN_COMPLETE" and not c.passed:
                    item.setToolTip("Keine technische Störung: Es fehlen noch Angaben (siehe Checkliste).")
                self.qa_list.addItem(item)
            hints = preview.hints
        else:
            self.checklist.set_items(
                [], ready=None, empty_text="Die Checkliste erscheint nach „Vorschau erzeugen“."
            )
            self.qa_summary.setText("")
            self.qa_list.clear()
            hints = []
        self.hints_title.setVisible(bool(hints))
        self.hints_label.setVisible(bool(hints))
        self.hints_label.setText(
            "".join(
                f'<div style="color:{severity_color(h.severity)}">{severity_symbol(h.severity)}&nbsp;'
                f"<b>prüfbedürftiger Hinweis:</b> {html.escape(h.text)}"
                + (f"<br><i>Quelle: {html.escape(h.source)}</i>" if h.source else "")
                + "</div>"
                for h in hints
            )
        )
        self._render_flatten()
        self._render_exports()
        self._update_controls()

    def _render_flatten(self) -> None:
        preview = self.preview
        self.flatten_cb.blockSignals(True)
        if preview is None:
            self.flatten_cb.setEnabled(False)
            self.flatten_cb.setChecked(False)
            self.flatten_cb.setToolTip("Erst nach der Vorschau verfügbar.")
            self.flatten_reason.setText(
                "„Fixieren“ macht die Formularfelder unveränderlich. Ob das erlaubt ist, zeigt die Vorschau."
            )
        elif preview.flatten_allowed:
            if not self.flatten_cb.isEnabled():
                self.flatten_cb.setChecked(bool(self.service.settings.flatten_default))
            self.flatten_cb.setEnabled(True)
            self.flatten_cb.setToolTip(
                "Fixieren: Die Formularfelder werden in feste Seiteninhalte umgewandelt und sind danach "
                "nicht mehr änderbar. Standard: ausfüllbar lassen."
            )
            self.flatten_reason.setText(preview.flatten_reason)
        else:
            self.flatten_cb.setChecked(False)
            self.flatten_cb.setEnabled(False)
            self.flatten_cb.setToolTip(preview.flatten_reason)
            self.flatten_reason.setText(f"ℹ {preview.flatten_reason}")
        self.flatten_cb.blockSignals(False)

    def _render_pages(self) -> None:
        clear_layout(self.pages_layout)
        if not self._pixmaps:
            lbl = muted("Hier erscheinen die Seiten der Vorschau.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.pages_layout.addWidget(lbl)
            return
        factor = (self._zoom / 100.0) * SCREEN_DPI / PREVIEW_DPI
        for n, pm in enumerate(self._pixmaps, start=1):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w = max(1, int(pm.width() * factor))
            lbl.setPixmap(
                pm.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation) if w != pm.width() else pm
            )
            lbl.setAccessibleName(f"Vorschau Seite {n}")
            lbl.setToolTip(f"Seite {n} von {len(self._pixmaps)}")
            lbl.setStyleSheet(f"border: 1px solid {theme.BORDER}; background: white;")
            self.pages_layout.addWidget(lbl)
            cap = muted(f"Seite {n} von {len(self._pixmaps)}")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.pages_layout.addWidget(cap)

    def _render_exports(self) -> None:
        case = self.controller.case
        exports = list(reversed(case.exports)) if case is not None else []
        self.exports_table.setRowCount(len(exports))
        titles = self._template_titles() if exports else {}
        for row, rec in enumerate(exports):
            name = QTableWidgetItem(rec.file_name or Path(rec.file_path).name)
            name.setData(Qt.ItemDataRole.UserRole, rec.file_path)
            name.setToolTip(rec.file_path)
            self.exports_table.setItem(row, 0, name)
            self.exports_table.setItem(row, 1, QTableWidgetItem(format_local_time(rec.timestamp)))
            self.exports_table.setItem(
                row,
                2,
                QTableWidgetItem(titles.get(rec.template_id, rec.template_id) + f" ({rec.template_version})"),
            )
            qa = ("✔ " if rec.qa_passed else "✖ ") + (rec.qa_summary or "")
            if rec.flattened:
                qa += " · fixiert"
            self.exports_table.setItem(row, 3, QTableWidgetItem(qa))

    def _template_titles(self) -> dict[str, str]:
        if self._titles is None:
            titles: dict[str, str] = {}
            try:
                for ch in self.service.template_choices():
                    if ch.available:
                        try:
                            titles[self.service.active_template(ch.catalog_id).template_id] = ch.title
                        except Exception:  # noqa: BLE001, S112 – Titel ist nur Komfort
                            continue
            except Exception:  # noqa: BLE001
                titles = {}
            self._titles = titles
        return self._titles

    def _update_controls(self) -> None:
        case = self.controller.case
        busy = self.controller.busy
        has_template = bool(case and case.template_id)
        self.preview_btn.setEnabled(not busy and has_template)
        blocker = (
            export_blocker(self.preview, self.stale) if has_template else "Bitte zuerst eine Vorlage wählen."
        )
        self.generate_btn.setEnabled(not busy and not blocker)
        self.generate_btn.setToolTip(blocker or "Die PDF wird erzeugt und im Exportordner abgelegt.")
        self.blocker_label.setText(f"⚠ {blocker}" if blocker else "")
        self.blocker_label.setVisible(bool(blocker))
        self.back_btn.setEnabled(not busy)
        for b in self.zoom_buttons.values():
            b.setEnabled(bool(self._pixmaps))

    def set_zoom(self, zoom: int) -> None:
        self._zoom = zoom
        for z, b in self.zoom_buttons.items():
            b.setChecked(z == zoom)
        self._render_pages()

    # ------------------------------------------------------------------
    def build_preview(self) -> None:
        def done(result: object) -> None:
            if not isinstance(result, PreviewResult):
                return
            self.preview = result
            self.stale = False
            self._fingerprint = data_fingerprint(self.controller.case)
            self._pixmaps = [QPixmap(str(p)) for p in result.page_images if Path(p).exists()]
            self.render()

        self.controller.run(
            self._task_parent or self,
            "Vorschau wird erzeugt und geprüft …",
            lambda case, progress, cancel: self.service.build_preview(case),
            done,
            cancellable=False,
            error_title="Vorschau nicht möglich",
        )

    def generate_pdf(self) -> None:
        if export_blocker(self.preview, self.stale) or self.controller.case is None:
            return
        if not dialogs.ask_confirm(self, "PDF jetzt erzeugen", CONFIRM_TEXT, ok_text="PDF jetzt erzeugen"):
            return
        flatten = self.flatten_cb.isEnabled() and self.flatten_cb.isChecked()

        def done(result: object) -> None:
            if not isinstance(result, ExportOutcome):
                return
            self.last_export = result
            self.success_title.setText(f"✔ PDF erzeugt: {result.path.name}")
            self.success_path.setText(f"Gespeichert in: {result.path.parent}")
            self.success.show()
            self.render()

        def failed(exc: BaseException, cancelled: bool) -> None:
            if cancelled:
                dialogs.show_info(self, "Abgebrochen", "Die PDF wurde nicht erzeugt.")
                return
            dialogs.show_error(self, "PDF nicht erzeugt", error_text(exc))

        self.controller.run(
            self._task_parent or self,
            "PDF wird erzeugt und geprüft …",
            lambda case, progress, cancel: self.service.generate_pdf(case, flatten=flatten),
            done,
            failed,
            cancellable=False,
        )

    def _open_pdf(self) -> None:
        if self.last_export is not None:
            dialogs.open_path(self.last_export.path)

    def _open_dir(self) -> None:
        if self.last_export is not None:
            dialogs.open_path(self.last_export.path.parent)

    def _open_export_row(self, row: int, _col: int) -> None:
        item = self.exports_table.item(row, 0)
        path = Path(str(item.data(Qt.ItemDataRole.UserRole))) if item else None
        if path is None:
            return
        if path.exists():
            dialogs.open_path(path)
        else:
            dialogs.show_info(
                self,
                "Datei nicht gefunden",
                "Die PDF liegt nicht mehr am gespeicherten Ort (verschoben oder gelöscht).",
            )
