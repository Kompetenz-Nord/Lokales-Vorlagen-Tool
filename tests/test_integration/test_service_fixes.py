"""Regressionstests für Befunde der adversarialen Prüfrunde (AppService). Synthetische Daten."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from insurance_app.core.models import FieldStatus
from insurance_app.core.paths import AppPaths
from insurance_app.core.service import DEFAULT_NOTE, AppService, ServiceError, SettingsError
from insurance_app.export.exporter import ExportBlockedError
from insurance_app.letters import load_letter_template

TODAY = date(2026, 11, 13)
SYN = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
POLICE = SYN / "MUSTER_Police_Direktversicherung.pdf"
NACHTRAG = SYN / "MUSTER_Nachtrag_Beitrag.pdf"


@pytest.fixture()
def svc(tmp_path: Path):
    service = AppService(
        AppPaths.for_testing(tmp_path), guard_network=False, configure_logs=False, today=lambda: TODAY
    )
    yield service
    service.close()


def processed_case(svc: AppService, *files: Path):
    case = svc.create_case()
    for f in files:
        svc.add_document(case, f)
    svc.process_documents(case)
    return case


def test_pdf_2_excluded_document_values_are_withdrawn(svc: AppService):
    case = processed_case(svc, POLICE, NACHTRAG)
    police = next(d for d in case.documents if d.original_name == POLICE.name)
    svc.confirm_field(case, "insured.birth_date")  # nur in der Police
    assert case.field("insured.birth_date").source_document_id == police.id
    svc.set_document_excluded(case, police.id, True)
    fv = case.field("insured.birth_date")
    assert fv.status == FieldStatus.MISSING and "Quelle" in (fv.note or "")
    assert all(c.evidence.document_id != police.id for c in fv.candidates)
    assert any(h.action == "source_removed" for h in case.history)


def test_svc_01_priv_03_removing_last_document_clears_its_data(svc: AppService):
    case = processed_case(svc, POLICE)
    assert case.field("insured.last_name").status == FieldStatus.EXTRACTED
    svc.remove_document(case, case.documents[0].id)
    assert not case.documents
    assert all(
        fv.status in (FieldStatus.MISSING, FieldStatus.NOT_APPLICABLE) for fv in case.contract_fields.values()
    )
    assert svc.list_cases(search="Musterfrau") == []


def test_svc_11_promoted_duplicate_keeps_recognition(svc: AppService, tmp_path: Path):
    copy = tmp_path / "kopie.pdf"
    copy.write_bytes(POLICE.read_bytes())
    case = processed_case(svc, POLICE, copy)
    original = next(d for d in case.documents if d.duplicate_of is None)
    svc.remove_document(case, original.id)
    assert len(case.documents) == 1 and case.documents[0].text is not None
    assert case.field("insured.birth_date").status == FieldStatus.EXTRACTED


def test_svc_02_user_marked_unclear_shows_new_conflict(svc: AppService):
    case = processed_case(svc, POLICE)
    svc.mark_unclear(case, "premium.amount")
    svc.add_document(case, NACHTRAG)
    svc.process_documents(case)
    fv = case.field("premium.amount")
    assert fv.status == FieldStatus.CONFLICT and fv.user_marked_unclear
    assert {c.normalized for c in fv.candidates} >= {"150.00", "100.00"}
    before = len(case.history)
    svc.extract_data(case)  # erneute Auswertung ohne Änderung -> keine falschen Verlaufseinträge
    assert not [h for h in case.history[before:] if h.field_key == "premium.amount"]


def test_legal_1_legal_decisions_cannot_be_not_applicable(svc: AppService):
    case = svc.create_case()
    for key in (
        "order.severance_consent",
        "order.continuation_mode",
        "order.pep_status",
        "order.payment_method",
    ):
        with pytest.raises(ServiceError):
            svc.mark_not_applicable(case, key)
    svc.mark_not_applicable(case, "order.change_end_date")  # Datum: "unbefristet" bleibt erlaubt


def test_legal_6_acknowledgment_reset_on_template_switch(svc: AppService):
    case = processed_case(svc, POLICE)
    svc.select_template(case, "hdi_beitragsfreistellung")
    svc.set_choice(case, "order.deadline_checked", True)
    svc.select_template(case, "kuendigung_ordentlich")
    assert case.field("order.deadline_checked").status == FieldStatus.MISSING
    codes = {i.code for i in svc.review_status(case).blocking_items}
    assert "ACK_REQUIRED" in codes


def test_legal_7_template_defaults_are_marked_as_proposals(svc: AppService):
    case = processed_case(svc, POLICE)
    svc.select_template(case, "tecis_bestandsuebertrag")
    for key in ("order.letter_date", "order.new_advisor_company"):
        fv = case.field(key)
        assert fv.note == DEFAULT_NOTE and fv.user_confirmed_at is None
    hist = [h for h in svc.case_history(case.id) if h.field_key == "order.letter_date"]
    assert hist and hist[-1].actor == "system" and "bitte prüfen" in (hist[-1].detail or "")
    items = svc.review_status(case).checklist
    assert {i.field_key for i in items if i.code == "PROPOSAL_UNREVIEWED"} >= {
        "order.letter_date",
        "order.new_advisor_company",
    }
    svc.edit_field(case, "order.letter_date", "13.11.2026")
    items = svc.review_status(case).checklist
    assert "order.letter_date" not in {i.field_key for i in items if i.code == "PROPOSAL_UNREVIEWED"}


def test_legal_7_stale_proposed_letter_date_is_refreshed(tmp_path: Path):
    day = {"d": date(2026, 11, 13)}
    service = AppService(
        AppPaths.for_testing(tmp_path), guard_network=False, configure_logs=False, today=lambda: day["d"]
    )
    try:
        case = service.create_case()
        service.select_template(case, "widerruf")
        assert case.field("order.letter_date").normalized_value == "2026-11-13"
        day["d"] = date(2026, 12, 3)
        service._propose_letter_date(case)
        assert case.field("order.letter_date").normalized_value == "2026-12-03"
    finally:
        service.close()


def test_svc_08_letter_stays_available_while_import_unconfirmed(svc: AppService, tmp_path: Path):
    pdf = tmp_path / "eigenes_formular.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    w = pymupdf.Widget()
    w.field_name, w.field_type, w.rect = (
        "Versicherungsnummer",
        pymupdf.PDF_WIDGET_TYPE_TEXT,
        pymupdf.Rect(100, 100, 300, 120),
    )
    page.add_widget(w)
    doc.save(pdf)
    svc.registry.import_original("kuendigung_ordentlich", pdf)
    choice = next(c for c in svc.template_choices(refresh=True) if c.catalog_id == "kuendigung_ordentlich")
    assert choice.available and choice.kind == "letter" and "noch nicht aktiv" in choice.status_reason


def test_svc_08_template_version_change_blocks_export(svc: AppService):
    case = svc.create_case()
    svc.select_template(case, "widerruf")
    case.template_version = "1999-01"
    items = svc.review_status(case).blocking_items
    assert any(i.code == "TEMPLATE_VERSION_CHANGED" for i in items)
    with pytest.raises(ExportBlockedError):
        svc.generate_pdf(case)


def test_priv_06_invalid_settings_are_not_saved(svc: AppService):
    before = svc.paths.settings_file.read_text("utf-8") if svc.paths.settings_file.exists() else None
    with pytest.raises(SettingsError):
        svc.update_settings(ocr_languages="deu, eng")
    with pytest.raises(SettingsError):
        svc.update_settings(letter_form="C")
    after = svc.paths.settings_file.read_text("utf-8") if svc.paths.settings_file.exists() else None
    assert before == after
    assert svc.settings.ocr_languages == "deu+eng"


def test_svc_12_manual_names_matching_placeholder_words(svc: AppService):
    case = svc.create_case()
    fv = svc.edit_field(case, "insured.last_name", "Na")
    assert fv.status == FieldStatus.MANUAL and fv.normalized_value == "Na"


def test_svc_18_attachment_lines_are_kept(svc: AppService):
    case = svc.create_case()
    svc.edit_field(case, "order.attachments", "Kopie der Police\nVollmacht\nKündigungsbestätigung")
    from insurance_app.letters.compose import resolve_letter

    t = load_letter_template(svc.paths.letter_templates_dir / "widerruf.json")
    plan = resolve_letter(t, case.contract_fields, case.order_fields)
    assert plan.attachments == ["Kopie der Police", "Vollmacht", "Kündigungsbestätigung"]


def test_svc_13_unreachable_export_folder_gives_user_message(svc: AppService, tmp_path: Path):
    blocker = tmp_path / "keinordner"
    blocker.write_text("x")
    svc.update_settings(export_dir=str(blocker / "unter"))
    case = processed_case(svc, POLICE)
    svc.select_template(case, "widerruf")
    for key in (
        "policyholder.name",
        "policyholder.street",
        "policyholder.house_number",
        "policyholder.postal_code",
        "policyholder.city",
        "contract.number",
    ):
        svc.confirm_field(case, key)
    for key, value in (
        ("order.recipient_company", "Beispiel Lebensversicherung AG"),
        ("order.recipient_street", "Musterallee"),
        ("order.recipient_postal_code", "12345"),
        ("order.recipient_city", "Musterstadt"),
    ):
        svc.edit_field(case, key, value)
    svc.set_choice(case, "order.revocation_subject", "termination")
    svc.edit_field(case, "order.termination_letter_date", "01.11.2026")
    svc.set_choice(case, "order.deadline_checked", True)
    assert svc.review_status(case).ready, [i.message for i in svc.review_status(case).blocking_items]
    with pytest.raises(ExportBlockedError) as exc:
        svc.generate_pdf(case)
    assert "Exportordner" in exc.value.user_message


@pytest.mark.slow
def test_svc_17_selftest_runs_real_ocr(tmp_path: Path, monkeypatch):
    from insurance_app.core.selftest import run_selftest

    monkeypatch.setenv("VORLAGEN_TOOL_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("VORLAGEN_TOOL_EXPORT_DIR", str(tmp_path / "e"))
    out = io.StringIO()
    code = run_selftest(out)
    text = out.getvalue()
    assert code == 0, text
    assert "Texterkennung (OCR): gescannter Musterbeleg korrekt gelesen." in text
    from insurance_app.security.network_guard import uninstall_network_guard

    uninstall_network_guard()
    json.dumps(text)  # nur technische Ausgabe
