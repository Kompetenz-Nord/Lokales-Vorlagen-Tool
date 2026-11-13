"""UI-Tests Vorgangsansicht (Dokumente, Datenprüfung, Vorschau & Export, Verlauf).

Alle Daten sind synthetisch („MUSTER – KEINE ECHTDATEN“). Modale Dialoge werden ersetzt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QLabel
from uia_helpers import (
    FREIGABE_CONFIRM_KEYS,
    FREIGABE_CONFLICT_KEYS,
    SL13065,
    DialogRecorder,
    flush_tasks,
    make_service,
)

from insurance_app.core.case import PipelineStage
from insurance_app.core.models import FieldStatus
from insurance_app.export.exporter import ExportBlockedError
from insurance_app.ui.case import candidate_dialog
from insurance_app.ui.case.candidate_dialog import SUGGESTION_HINT, CandidateDialog
from insurance_app.ui.case.field_edit_dialog import FieldEditDialog
from insurance_app.ui.case.order_form import LEGAL_TEXT, WAIVE_REFUND_WARNING
from insurance_app.ui.case.preview_panel import CONFIRM_TEXT
from insurance_app.ui.case.review_panel import NO_TEMPLATE_HINT
from insurance_app.ui.case.review_table import (
    ACTION_COMPARE,
    ACTION_CONFIRM,
    ACTION_EDIT,
    ACTION_NOT_APPLICABLE,
    ACTION_UNCLEAR,
    COL_VALUE,
    SECTION_OTHER,
    SECTION_REQUIRED,
)
from insurance_app.ui.case.stepper import STEP_CURRENT, STEP_DONE, STEP_OPEN
from insurance_app.ui.pages.case_view import TAB_DOCUMENTS, TAB_HISTORY, TAB_PREVIEW, TAB_REVIEW, CaseView

pytestmark = pytest.mark.ui


@pytest.fixture()
def svc(tmp_path: Path):
    service = make_service(tmp_path)
    yield service
    flush_tasks()
    service.close()


@pytest.fixture()
def dlg(monkeypatch: pytest.MonkeyPatch) -> DialogRecorder:
    return DialogRecorder(monkeypatch)


@pytest.fixture()
def sample(svc):
    return svc.create_sample_case()


@pytest.fixture()
def catalog_calls() -> list[int]:
    return []


@pytest.fixture()
def view(qtbot, svc, dlg, catalog_calls) -> CaseView:
    v = CaseView(svc, show_catalog=lambda: catalog_calls.append(1))
    qtbot.addWidget(v)
    v.resize(1360, 860)
    v.show()
    return v


def choose_suggestion_exec(self: CandidateDialog) -> int:
    """Ersatz für den modalen Dialog: den markierten Vorschlag bewusst auswählen."""
    self.select_row(self.case.field(self.key).suggestion_index)
    self.choose()
    return int(self.result())


def select_template(view: CaseView, catalog_id: str) -> None:
    combo = view.review.template_combo
    idx = combo.findData(catalog_id)
    assert idx > 0, f"Vorlage {catalog_id} nicht wählbar"
    combo.setCurrentIndex(idx)
    combo.activated.emit(idx)


# ---------------------------------------------------------------------------


def test_open_case_shows_header_stepper_and_review_tab(view, sample):
    assert view.current_case_id() is None
    view.open_case(sample.id)
    assert view.current_case_id() == sample.id
    assert view.title_label.text() == sample.display_title()
    assert sample.id in view.meta_label.text()
    assert view.tabs.currentIndex() == TAB_REVIEW  # ausgewertet -> direkt zur Datenprüfung
    states = view.stepper.states()
    assert states[1] == STEP_DONE and states[int(PipelineStage.PRUEFUNG)] == STEP_CURRENT
    assert states[9] == STEP_OPEN
    view.stepper.buttons[PipelineStage.TEXTERKENNUNG].click()
    assert view.tabs.currentIndex() == TAB_DOCUMENTS
    view.stepper.buttons[PipelineStage.VORSCHAU].click()
    assert view.tabs.currentIndex() == TAB_PREVIEW
    view.stepper.buttons[PipelineStage.DATENFREIGABE].click()
    assert view.tabs.currentIndex() == TAB_REVIEW


def test_documents_tab_lists_files_ocr_and_exclusion(qtbot, view, sample, dlg):
    view.open_case(sample.id)
    view.show_tab(TAB_DOCUMENTS)
    docs = view.documents
    assert docs.table.rowCount() == 3
    ocr = [docs.table.item(r, 4).text() for r in range(3)]
    assert all("gut lesbar" in t for t in ocr)
    assert any("fremde OCR-Textebene" in t for t in ocr)  # gescannte Standmitteilung
    assert any("Textebene" in t for t in ocr)
    assert "in Ordnung" in docs.table.item(0, 3).text()
    assert not docs.btn_ai.isEnabled() and "ausgeschaltet" in docs.btn_ai.toolTip()

    docs.table.selectRow(2)
    dlg.confirm_answers = [False]
    docs.btn_remove.click()
    assert dlg.confirms[-1]["danger"] is True
    assert len(view.controller.case.documents) == 3  # Abbrechen behält die Datei

    docs.btn_exclude.click()  # Standmitteilung ausschließen -> Auswertung wird aktualisiert
    qtbot.waitUntil(lambda: not view.controller.busy, timeout=30_000)
    case = view.controller.case
    assert case.documents[2].excluded
    assert docs.table.item(2, 5).text().endswith("ausgeschlossen")
    assert docs.btn_exclude.text() == "Einbeziehen"
    assert not dlg.errors


def test_review_table_rows_with_statuses_and_template_sections(qtbot, view, sample, svc):
    view.open_case(sample.id)
    review = view.review
    table = review.table
    assert NO_TEMPLATE_HINT in review.summary_label.text()
    assert "contract.number" in table.field_keys()
    assert "Erkannt – bitte prüfen" in table.status_text_of("contract.number")
    assert "Sicherheit: hoch" in table.status_text_of("contract.number")
    assert "Konflikt" in table.status_text_of("insured.street")
    assert table.action_button("insured.street", ACTION_COMPARE) is not None
    assert table.action_button("contract.number", ACTION_CONFIRM) is not None
    assert "12.08.1988" in table.item_for("insured.birth_date").text(COL_VALUE)
    assert "€" not in table.item_for("insured.birth_date").text(COL_VALUE)

    select_template(view, SL13065)
    assert svc.load_case(sample.id).template_id == SL13065
    required = table.keys_in_section(SECTION_REQUIRED)
    assert "contract.number" in required and "employer.employment_start" in required
    assert "contract.intermediary" not in required  # Abschlussvermittler irrelevant
    other = table.section_item(SECTION_OTHER)
    assert other is not None and not other.isExpanded()  # eingeklappt
    assert "premium.amount" in table.keys_in_section(SECTION_OTHER)
    assert "Fehlt" in table.status_text_of("employer.employment_start")
    assert NO_TEMPLATE_HINT not in review.summary_label.text()
    assert "Konflikt" in review.summary_label.text()
    assert not review.checklist.ready_label.isVisible()
    assert any(t.startswith("✖ offen") for t in review.checklist.texts())

    # Beleg-Ansicht mit markierter Fundstelle
    table.select_key("insured.street")
    ev = review.evidence
    assert ev.canvas.image is not None and len(ev.canvas.rects) == 1
    assert "Seite 1" in ev.caption.text() and "Textebene" in ev.caption.text()
    assert len(ev.items) == 2

    # Einzelaktionen
    table.trigger("contract.number", ACTION_CONFIRM)
    assert view.controller.case.field("contract.number").status == FieldStatus.CONFIRMED
    assert "Bestätigt" in table.status_text_of("contract.number")
    table.trigger("policyholder.name", ACTION_UNCLEAR)
    assert view.controller.case.field("policyholder.name").status == FieldStatus.UNCLEAR
    table.trigger("contract.tariff", ACTION_NOT_APPLICABLE)
    assert view.controller.case.field("contract.tariff").status == FieldStatus.NOT_APPLICABLE
    assert svc.load_case(sample.id).field("contract.number").status == FieldStatus.CONFIRMED  # gespeichert


def test_bulk_confirm_reports_skipped(qtbot, view, sample):
    view.open_case(sample.id)
    select_template(view, SL13065)
    table = view.review.table
    table.select_open_rows()
    keys = set(table.selected_keys())
    assert set(FREIGABE_CONFIRM_KEYS) <= keys
    table.tree.clearSelection()
    for key in (*FREIGABE_CONFIRM_KEYS, "insured.street"):
        table.item_for(key).setSelected(True)
    table.bulk_btn.click()
    case = view.controller.case
    assert all(case.field(k).status == FieldStatus.CONFIRMED for k in FREIGABE_CONFIRM_KEYS)
    assert case.field("insured.street").status == FieldStatus.CONFLICT
    assert "5 bestätigt" in table.bulk_info.text() and "Straße" in table.bulk_info.text()


def test_conflict_dialog_choose_candidate_updates_status(qtbot, view, sample, monkeypatch):
    view.open_case(sample.id)
    case = view.controller.case
    fv = case.field("insured.street")
    dialog = CandidateDialog(view.service, case, "insured.street")
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 2
    assert SUGGESTION_HINT in dialog.table.item(fv.suggestion_index, 6).text()
    assert not dialog.choose_btn.isEnabled()  # keine Vorauswahl
    dialog.select_row(fv.suggestion_index)
    dialog.choose_btn.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    chosen = case.field("insured.street")
    assert chosen.status == FieldStatus.CONFIRMED and chosen.normalized_value == "Neue Straße"

    # Über die Tabelle (modaler Dialog ersetzt)
    monkeypatch.setattr(CandidateDialog, "exec", choose_suggestion_exec)
    view.review.table.trigger("insured.city", ACTION_COMPARE)
    assert "Bestätigt" in view.review.table.status_text_of("insured.city")
    assert view.service.load_case(sample.id).field("insured.city").status == FieldStatus.CONFIRMED


def test_field_edit_dialog_shows_input_error_inline(qtbot, view, sample):
    view.open_case(sample.id)
    case = view.controller.case
    dialog = FieldEditDialog(view.service, case, "employer.employment_start")
    qtbot.addWidget(dialog)
    assert "TT.MM.JJJJ" in dialog.hint.text()
    assert dialog.edit is not None and dialog.edit.placeholderText() == "TT.MM.JJJJ"
    dialog.set_value("31.02.2020")
    dialog.ok_btn.click()
    assert "TT.MM.JJJJ" in dialog.error.text() and dialog.result() != QDialog.DialogCode.Accepted
    dialog.set_value("01.01.2019")
    dialog.ok_btn.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    fv = case.field("employer.employment_start")
    assert fv.status == FieldStatus.MANUAL and fv.normalized_value == "2019-01-01"


def test_order_form_radio_selection_updates_order_value(qtbot, view, sample, svc):
    view.open_case(sample.id)
    select_template(view, SL13065)
    form = view.review.order_form
    assert set(form.rows) == {"order.severance_consent", "order.waive_refund"}
    row = form.rows["order.severance_consent"]
    assert set(row.radios) == {"true", "false"}
    assert not any(rb.isChecked() for rb in row.radios.values())  # keine Vorauswahl
    assert "noch nicht entschieden" in row.status.text()
    labels = [lbl.text() for lbl in form.rows["order.waive_refund"].frame.findChildren(QLabel)]
    assert any(WAIVE_REFUND_WARNING in t for t in labels)
    assert any(LEGAL_TEXT in t for t in labels)

    row.radios["false"].click()
    fv = svc.load_case(sample.id).order_fields["order.severance_consent"]
    assert fv.status == FieldStatus.MANUAL and fv.normalized_value == "false"
    assert row.radios["false"].isChecked() and not row.radios["true"].isChecked()
    assert "übernommen" in row.status.text()
    assert not any("Abfindung" in t for t in view.review.checklist.texts() if t.startswith("✖"))

    row.clear_btn.click()  # Entscheidung zurücknehmen
    assert view.controller.case.get("order.severance_consent").status == FieldStatus.MISSING
    assert not any(rb.isChecked() for rb in row.radios.values())


def test_order_form_text_fields_and_letter_hints(qtbot, view, sample, dlg):
    view.open_case(sample.id)
    select_template(view, "kuendigung_ordentlich")
    form = view.review.order_form
    case = view.controller.case
    assert form.rows["order.letter_date"].edit.text() == "13.11.2026"  # vorgeschlagen, prüfbar
    row = form.rows["order.desired_date"]
    assert row.edit.placeholderText() == "TT.MM.JJJJ"
    row.edit.setText("31.02.2027")
    row.edit.editingFinished.emit()
    assert "TT.MM.JJJJ" in form.error_text_of("order.desired_date")
    assert (
        case.get("order.desired_date") is None or case.get("order.desired_date").status == FieldStatus.MISSING
    )
    row.edit.setText("31.12.2027")
    row.edit.editingFinished.emit()
    assert form.error_text_of("order.desired_date") == ""
    assert case.order_fields["order.desired_date"].normalized_value == "2027-12-31"

    # Optional -> „nicht relevant“, Pflicht-Empfänger -> nicht
    assert form.rows["order.sender_email"].na_btn is not None
    assert form.rows["order.recipient_company"].na_btn is None
    form.rows["order.sender_email"].na_btn.click()
    assert case.order_fields["order.sender_email"].status == FieldStatus.NOT_APPLICABLE

    # Versicherer übernehmen erst nach Bestätigung
    assert form.copy_btn is not None
    form.copy_btn.click()
    assert dlg.errors and "Versicherer" in dlg.errors[-1][1]
    view.review.table.trigger("insurer.name", ACTION_CONFIRM)
    form.copy_btn.click()
    assert case.order_fields["order.recipient_company"].normalized_value == "Beispiel Lebensversicherung AG"
    assert form.rows["order.recipient_company"].edit.text() == "Beispiel Lebensversicherung AG"

    hints = [w.text() for w in form.widget().findChildren(QLabel) if "prüfbedürftiger Hinweis" in w.text()]
    assert hints and any("Quelle: § 130" in h for h in hints)


def test_full_flow_confirm_order_preview_and_export(qtbot, view, sample, svc, dlg, monkeypatch):
    view.open_case(sample.id)
    select_template(view, SL13065)
    table = view.review.table
    # 1) erkannte Werte gemeinsam bestätigen
    table.select_open_rows()
    table.bulk_btn.click()
    # 2) Konflikte bewusst entscheiden (Dialog ersetzt)
    monkeypatch.setattr(CandidateDialog, "exec", choose_suggestion_exec)
    for key in FREIGABE_CONFLICT_KEYS:
        table.trigger(key, ACTION_COMPARE)

    # 3) fehlende Daten eintragen (Bearbeiten-Dialog ersetzt)
    values = {"employer.employment_start": "01.01.2019", "employer.employment_end": "30.09.2026"}

    def edit_exec(self: FieldEditDialog) -> int:
        self.set_value(values[self.key])
        self.apply()
        return int(self.result())

    monkeypatch.setattr(FieldEditDialog, "exec", edit_exec)
    for key in values:
        table.trigger(key, ACTION_EDIT)
    # 4) Auftragsdaten bewusst wählen
    form = view.review.order_form
    form.rows["order.severance_consent"].radios["false"].click()
    form.rows["order.waive_refund"].radios["false"].click()
    assert view.review.status is not None and view.review.status.ready, [
        i.message for i in view.review.status.blocking_items
    ]
    assert not view.review.checklist.ready_label.isHidden()
    assert not dlg.errors

    # 5) Vorschau
    view.show_tab(TAB_PREVIEW)
    preview = view.preview
    assert not preview.generate_btn.isEnabled()
    preview.preview_btn.click()
    qtbot.waitUntil(lambda: preview.preview is not None and not view.controller.busy, timeout=30_000)
    assert preview.preview.page_images and preview.pages_layout.count() >= 1
    assert "Prüfungen bestanden" in preview.qa_summary.text()
    assert not preview.flatten_cb.isEnabled() and "Fixieren" in preview.flatten_cb.toolTip()
    assert preview.generate_btn.isEnabled(), preview.blocker_label.text()
    preview.set_zoom(150)
    assert preview.zoom_buttons[150].isChecked()

    # 6) Export blockiert -> verständliche Meldung
    original = svc.generate_pdf

    def blocked(case, *, flatten=False):
        raise ExportBlockedError("Die PDF kann noch nicht erzeugt werden. Offene Punkte: MUSTER")

    monkeypatch.setattr(svc, "generate_pdf", blocked)
    dlg.confirm_answers = [True]
    preview.generate_btn.click()
    qtbot.waitUntil(lambda: bool(dlg.errors) and not view.controller.busy, timeout=30_000)
    assert "Offene Punkte: MUSTER" in dlg.errors[-1][1]
    monkeypatch.setattr(svc, "generate_pdf", original)

    # 7) „PDF jetzt erzeugen“ nach Bestätigung
    dlg.confirm_answers = [True]
    preview.generate_btn.click()
    assert dlg.confirms[-1]["text"] == CONFIRM_TEXT
    qtbot.waitUntil(lambda: preview.last_export is not None and not view.controller.busy, timeout=30_000)
    out = preview.last_export.path
    assert out.exists() and out.parent == svc.export_dir
    assert out.name.startswith("2026-11-13_SWISSLIFE_SL13065_Freigabe_")
    assert not preview.success.isHidden() and out.name in preview.success_title.text()
    assert preview.exports_table.rowCount() == 1
    preview.open_pdf_btn.click()
    preview.open_dir_btn.click()
    assert dlg.opened == [out, out.parent]
    assert view.stepper.states()[9] == STEP_DONE

    view.show_tab(TAB_HISTORY)
    actions = view.history.actions_shown()
    assert "PDF erzeugt" in actions and "Wert bestätigt" in actions
    assert "Wert aus dem Vergleich übernommen" in actions


def test_generate_cancelled_by_user_creates_nothing(qtbot, view, sample, svc, dlg):
    from uia_helpers import release_freigabe_contract_data

    release_freigabe_contract_data(svc, sample)
    svc.select_template(sample, SL13065)
    svc.set_choice(sample, "order.severance_consent", False)
    svc.set_choice(sample, "order.waive_refund", False)
    view.open_case(sample.id)
    view.show_tab(TAB_PREVIEW)
    view.preview.preview_btn.click()
    qtbot.waitUntil(lambda: view.preview.preview is not None and not view.controller.busy, timeout=30_000)
    dlg.confirm_answers = [False]
    view.preview.generate_btn.click()
    assert dlg.confirms and not list(svc.export_dir.glob("*.pdf"))
    # Datenänderung -> Vorschau veraltet, Export gesperrt
    view.review.table.trigger("contract.number", "unclear")
    assert view.preview.stale and not view.preview.generate_btn.isEnabled()


def test_apply_template_without_open_case_creates_case(view, svc, catalog_calls):
    view.apply_template(SL13065)
    case_id = view.current_case_id()
    assert case_id is not None
    assert svc.load_case(case_id).template_id == SL13065
    assert view.tabs.currentIndex() == TAB_REVIEW
    view.review.catalog_btn.click()
    assert catalog_calls == [1]


def test_history_tab_lists_actions_in_german(view, sample):
    view.open_case(sample.id)
    view.review.table.trigger("contract.number", ACTION_CONFIRM)
    view.show_tab(TAB_HISTORY)
    hist = view.history
    actions = hist.actions_shown()
    assert actions[0] == "Wert bestätigt"  # neueste zuerst
    assert hist.table.item(0, 1).text() == "Nutzer"
    assert hist.table.item(0, 3).text() == "Vertrags-/Versicherungsnummer"
    assert "→" in hist.table.item(0, 4).text() and "Bestätigt" in hist.table.item(0, 4).text()
    assert "Vorgang angelegt" in actions and "Widersprüchliche Werte gefunden" in actions


def test_refresh_after_external_delete_shows_empty_state(view, sample, svc):
    view.open_case(sample.id)
    svc.delete_case(sample.id)
    view.refresh()
    assert view.current_case_id() is None
    assert view.stack.currentIndex() == 0


def test_apply_template_after_external_delete_does_not_resurrect_case(view, sample, svc):
    view.open_case(sample.id)
    svc.delete_case(sample.id)
    view.apply_template(SL13065)  # z. B. aus dem Vorlagenkatalog
    new_id = view.current_case_id()
    assert new_id is not None and new_id != sample.id
    assert [s.id for s in svc.list_cases()] == [new_id]


def test_candidate_dialog_module_function_uses_exec(view, sample, monkeypatch):
    view.open_case(sample.id)
    monkeypatch.setattr(CandidateDialog, "exec", lambda self: int(QDialog.DialogCode.Rejected))
    assert (
        candidate_dialog.compare_candidates(view, view.service, view.controller.case, "insured.street")
        is False
    )
    assert view.controller.case.field("insured.street").status == FieldStatus.CONFLICT
