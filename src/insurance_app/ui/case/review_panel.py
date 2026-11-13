"""Reiter „Datenprüfung“ (Stufen 4–6): Vorlage wählen, Werte prüfen, Auftragsdaten, Checkliste."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Case
from ...core.fields import FIELD_REGISTRY
from ...core.models import FieldStatus
from ...core.service import AppService, ReviewStatus, TemplateChoice
from .. import theme
from ..widgets.common import muted
from . import candidate_dialog, field_edit_dialog
from .checklist_panel import ChecklistPanel
from .common import error_text, message_html
from .controller import CaseController
from .evidence_view import EvidenceView
from .order_form import OrderForm, optional_keys
from .review_table import (
    ACTION_COMPARE,
    ACTION_CONFIRM,
    ACTION_EDIT,
    ACTION_NOT_APPLICABLE,
    ACTION_RESET,
    ACTION_UNCLEAR,
    SECTION_ALL,
    SECTION_REQUIRED,
    ReviewTable,
)

log = logging.getLogger(__name__)

NO_TEMPLATE_HINT = "Bitte Vorlage wählen, um nur die benötigten Felder zu sehen."


def template_label(choice: TemplateChoice) -> str:
    if choice.kind == "letter":
        kind = "Brief nach DIN 5008"
    else:
        kind = f"Formular {choice.insurer} {choice.form_number}" if choice.form_number else "Formular"
    return f"{choice.number}. {choice.title} ({kind})"


def status_summary(case: Case, keys: list[str]) -> str:
    counts: dict[FieldStatus, int] = {}
    for k in keys:
        fv = case.get(k)
        st = fv.status if fv is not None else FieldStatus.MISSING
        counts[st] = counts.get(st, 0) + 1
    parts = []
    for st in (
        FieldStatus.CONFIRMED,
        FieldStatus.MANUAL,
        FieldStatus.EXTRACTED,
        FieldStatus.CONFLICT,
        FieldStatus.UNCLEAR,
        FieldStatus.MISSING,
        FieldStatus.NOT_APPLICABLE,
    ):
        n = counts.get(st, 0)
        if n:
            sym, txt, _ = theme.STATUS_STYLE[st]
            parts.append(f"{sym} {n} × {txt}")
    return " · ".join(parts)


class ReviewPanel(QWidget):
    def __init__(
        self,
        service: AppService,
        controller: CaseController,
        *,
        show_catalog: Callable[[], None],
        task_parent: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.controller = controller
        self._show_catalog = show_catalog
        self._task_parent = task_parent
        self.status: ReviewStatus | None = None
        self._choices: list[TemplateChoice] = []
        self._optional_cache: dict[str, set[str]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(6)

        top = QHBoxLayout()
        lbl = QLabel("Vorlage:")
        lbl.setStyleSheet("font-weight: 700;")
        top.addWidget(lbl)
        self.template_combo = QComboBox()
        self.template_combo.setAccessibleName("Vorlage wählen")
        self.template_combo.setToolTip(
            "Welche Vorlage soll befüllt werden? Danach zeigt die Tabelle nur die dafür benötigten Felder."
        )
        self.template_combo.setMinimumWidth(420)
        self.template_combo.activated.connect(self._template_activated)
        lbl.setBuddy(self.template_combo)
        top.addWidget(self.template_combo, 1)
        self.catalog_btn = QPushButton("Vorlagenkatalog öffnen")
        self.catalog_btn.setToolTip("Alle Vorlagen mit Status und Beschreibung ansehen")
        self.catalog_btn.clicked.connect(self._show_catalog)
        top.addWidget(self.catalog_btn)
        layout.addLayout(top)
        self.template_hint = muted("")
        layout.addWidget(self.template_hint)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAccessibleName("Zusammenfassung der Prüfstatus")
        layout.addWidget(self.summary_label)
        self.messages_label = QLabel("")
        self.messages_label.setWordWrap(True)
        self.messages_label.setTextFormat(Qt.TextFormat.RichText)
        self.messages_label.setAccessibleName("Hinweise zum Vorgang")
        layout.addWidget(self.messages_label)

        self.table = ReviewTable()
        self.table.action_requested.connect(self.handle_action)
        self.table.current_key_changed.connect(self._show_evidence)
        self.table.confirm_selected.connect(self.confirm_keys)
        self.evidence = EvidenceView(service)

        self.order_form = OrderForm(service, controller)
        self.checklist = ChecklistPanel("Checkliste")
        self.checklist.field_activated.connect(self.focus_field)
        checklist_scroll = QScrollArea()
        checklist_scroll.setWidgetResizable(True)
        checklist_scroll.setWidget(self.checklist)

        # Links: Prüftabelle über Auftragsdaten; rechts: Beleg über Checkliste (unabhängig verstellbar)
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.addWidget(self.table)
        self.vsplit.addWidget(self.order_form)
        self.vsplit.setStretchFactor(0, 1)
        self.vsplit.setStretchFactor(1, 1)
        self.vsplit.setSizes([340, 290])
        self.vsplit.setChildrenCollapsible(False)
        self.side_split = QSplitter(Qt.Orientation.Vertical)
        self.side_split.addWidget(self.evidence)
        self.side_split.addWidget(checklist_scroll)
        self.side_split.setStretchFactor(0, 3)
        self.side_split.setStretchFactor(1, 2)
        self.side_split.setSizes([420, 210])
        self.side_split.setChildrenCollapsible(False)
        self.hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.hsplit.addWidget(self.vsplit)
        self.hsplit.addWidget(self.side_split)
        self.hsplit.setStretchFactor(0, 3)
        self.hsplit.setStretchFactor(1, 1)
        self.hsplit.setSizes([775, 265])
        self.hsplit.setChildrenCollapsible(False)
        layout.addWidget(self.hsplit, 1)

        controller.busy_changed.connect(self._busy_changed)

    # ------------------------------------------------------------------
    def render(self) -> None:
        case = self.controller.case
        self._fill_templates(case)
        if case is None:
            self.status = None
            self.table.clear()
            self.evidence.clear()
            self.order_form.build(None, [], None, set())
            self.checklist.set_items([], ready=None, empty_text="Kein Vorgang geöffnet.")
            self.summary_label.setText("")
            self.messages_label.setText("")
            return
        try:
            self.status = self.service.review_status(case)
        except Exception as exc:  # noqa: BLE001 – z. B. Vorlage nicht mehr verfügbar
            log.info("Prüfstatus nicht ermittelbar: %s", type(exc).__name__)
            self.status = ReviewStatus(messages=[])
            self.template_hint.setText(f"✖ {error_text(exc)}")
            self.template_hint.show()
        status = self.status
        relevant = status.relevant_contract_keys if status.template_id else None
        self.table.set_rows(case, relevant)
        keys = relevant if relevant is not None else self.table.keys_in_section(SECTION_ALL)
        summary = status_summary(case, keys)
        prefix = "Benötigte Angaben" if relevant is not None else "Erkannte Angaben"
        if relevant is None and case.contract_fields:
            self.summary_label.setText(f"ℹ {NO_TEMPLATE_HINT}\n{prefix}: {summary}")
        elif relevant is None:
            self.summary_label.setText(
                "Noch keine erkannten Angaben. Bitte im Reiter „Dokumente“ die Texterkennung starten "
                "oder oben eine Vorlage wählen und Werte selbst eintragen."
            )
        else:
            self.summary_label.setText(f"{prefix}: {summary}" if summary else "")
        self.messages_label.setText(message_html(status.messages, limit=6))
        self.messages_label.setVisible(bool(status.messages))

        tpl_kind = None
        legal_hint = None
        optional: set[str] = set()
        if status.template_id:
            choice = next((c for c in self._choices if c.catalog_id == status.template_id), None)
            tpl_kind = choice.kind if choice else "form"
            legal_hint = choice.legal_hint if choice else None
            optional = self._optional(status.template_id, status.relevant_order_keys)
        self.order_form.build(case, status.relevant_order_keys, tpl_kind, optional, legal_hint=legal_hint)
        blocking = {i.field_key for i in status.blocking_items if i.field_key}
        self.order_form.sync(case, status.hints, blocking)
        if status.template_id:
            self.checklist.set_items(status.checklist, ready=status.ready)
        else:
            self.checklist.set_items([], ready=None, empty_text="Bitte zuerst eine Vorlage wählen.")
        key = self.table.current_key()
        if key:
            self._show_evidence(key)
        else:
            self.evidence.clear()

    def _optional(self, catalog_id: str, keys: list[str]) -> set[str]:
        if catalog_id not in self._optional_cache:
            try:
                tpl = self.service.active_template(catalog_id)
            except Exception:  # noqa: BLE001 – ohne Vorlage keine optionalen Felder
                return set()
            self._optional_cache[catalog_id] = optional_keys(tpl, list(FIELD_REGISTRY))
        return {k for k in keys if k in self._optional_cache[catalog_id]}

    def _fill_templates(self, case: Case | None) -> None:
        if not self._choices:
            try:
                self._choices = self.service.template_choices()
            except Exception as exc:  # noqa: BLE001
                log.info("Vorlagen nicht lesbar: %s", type(exc).__name__)
                self._choices = []
        combo = self.template_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("– bitte Vorlage wählen –", None)
        current = case.template_id if case else None
        for ch in self._choices:
            if ch.available:
                combo.addItem(template_label(ch), ch.catalog_id)
                combo.setItemData(combo.count() - 1, ch.description, Qt.ItemDataRole.ToolTipRole)
        if current and combo.findData(current) < 0:
            ch = next((c for c in self._choices if c.catalog_id == current), None)
            title = ch.title if ch else current
            combo.addItem(f"{title} (nicht verfügbar)", current)
        combo.setCurrentIndex(max(0, combo.findData(current)) if current else 0)
        combo.setEnabled(case is not None and not self.controller.busy)
        combo.blockSignals(False)
        ch = next((c for c in self._choices if c.catalog_id == current), None) if current else None
        self.template_hint.setText("")  # nur für Fehlermeldungen; Hinweise stehen in der Zusammenfassung
        if ch is None:
            self.template_hint.setToolTip("")
            combo.setToolTip(
                "Welche Vorlage soll befüllt werden? Danach zeigt die Tabelle nur die dafür benötigten Felder. "
                "Die Vorlage kann jederzeit gewechselt werden."
            )
        else:
            desc = ch.status_reason if ch.kind == "letter" else ch.description
            self.template_hint.setToolTip(desc)  # rechtlicher Hinweis steht bei den Auftragsdaten
            combo.setToolTip(desc)
        self.template_hint.setVisible(bool(self.template_hint.text()))

    def choice_for(self, catalog_id: str) -> TemplateChoice | None:
        return next((c for c in self._choices if c.catalog_id == catalog_id), None)

    def refresh_choices(self) -> None:
        self._choices = []
        self._optional_cache.clear()

    def select_template(self, catalog_id: str) -> bool:
        result = self.controller.act(
            self,
            lambda c: self.service.select_template(c, catalog_id),
            error_title="Vorlage nicht verfügbar",
        )
        return result is not None

    def _template_activated(self, index: int) -> None:
        catalog_id = self.template_combo.itemData(index)
        case = self.controller.case
        if case is None or catalog_id is None:
            if case is not None and case.template_id and catalog_id is None:
                self.controller.act(self, lambda c: self.service.clear_template(c))
            return
        if catalog_id == case.template_id:
            return
        self.select_template(str(catalog_id))

    # ------------------------------------------------------------------
    def handle_action(self, action: str, key: str) -> None:
        case = self.controller.case
        if case is None or self.controller.busy:
            return
        svc = self.service
        if action == ACTION_CONFIRM:
            self.controller.act(self, lambda c: svc.confirm_field(c, key), error_title="Nicht bestätigt")
        elif action == ACTION_UNCLEAR:
            self.controller.act(self, lambda c: svc.mark_unclear(c, key))
        elif action == ACTION_NOT_APPLICABLE:
            self.controller.act(self, lambda c: svc.mark_not_applicable(c, key))
        elif action == ACTION_RESET:
            self.controller.act(self, lambda c: svc.reset_field(c, key))
        elif action in (ACTION_EDIT, ACTION_COMPARE):
            run_dialog = (
                field_edit_dialog.edit_field_value
                if action == ACTION_EDIT
                else candidate_dialog.compare_candidates
            )
            if run_dialog(self, svc, case, key):
                self.controller.data_changed.emit()
        self.table.select_key(key)

    def confirm_keys(self, keys: list[str]) -> None:
        skipped = self.controller.act(
            self, lambda c: self.service.confirm_fields(c, keys), error_title="Nicht bestätigt"
        )
        if skipped is None:
            return
        done = len(keys) - len(skipped)
        if skipped:
            labels = ", ".join(FIELD_REGISTRY[k].label for k in skipped[:6])
            more = f" und {len(skipped) - 6} weitere" if len(skipped) > 6 else ""
            self.table.bulk_info.setText(
                f"✔ {done} bestätigt. ⚠ Nicht bestätigt (Konflikt oder kein eindeutiger Wert – bitte einzeln "
                f"prüfen): {labels}{more}"
            )
            self.table.bulk_info.setStyleSheet(f"color: {theme.WARN};")
        else:
            self.table.bulk_info.setText(f"✔ {done} Werte bestätigt.")
            self.table.bulk_info.setStyleSheet(f"color: {theme.OK};")

    def focus_field(self, key: str) -> None:
        if self.table.select_key(key):
            self.table.tree.setFocus()
            return
        row = self.order_form.rows.get(key)
        if row is not None:
            self.order_form.ensureWidgetVisible(row.frame)
            target = row.edit or next(iter(row.radios.values()), None)
            if target is not None:
                target.setFocus()

    def _show_evidence(self, key: str) -> None:
        case = self.controller.case
        if case is None or not key:
            self.evidence.clear()
            return
        self.evidence.show_field(case, key)

    def _busy_changed(self, busy: bool) -> None:
        self.table.set_actions_enabled(not busy)
        self.template_combo.setEnabled(not busy and self.controller.case is not None)
        self.catalog_btn.setEnabled(not busy)

    def reset_view(self) -> None:
        """Nach dem Öffnen eines anderen Vorgangs: Auswahl/Bildspeicher zurücksetzen."""
        self.evidence.invalidate_cache()
        self.evidence.clear()
        self.table.clear()

    def required_keys_shown(self) -> list[str]:
        return self.table.keys_in_section(SECTION_REQUIRED)
