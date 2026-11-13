"""End-to-End: Musterfall (Police + Nachtrag + Standmitteilung) -> Prüfung -> Vorlage -> PDF-Export.

Alle Daten sind synthetisch („MUSTER – KEINE ECHTDATEN“).
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
from datetime import date
from pathlib import Path

import pymupdf
import pypdf
import pytest

from conftest import SL13065_PDF, SL18477_PDF
from insurance_app.core.models import FieldStatus
from insurance_app.core.paths import AppPaths
from insurance_app.core.service import AppService, FieldInputError, ServiceError
from insurance_app.export.exporter import ExportBlockedError

TODAY = date(2026, 11, 13)
SYN = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def svc(tmp_path: Path):
    service = AppService(
        AppPaths.for_testing(tmp_path), guard_network=False, configure_logs=False, today=lambda: TODAY
    )
    yield service
    service.close()


@pytest.fixture()
def sample_case(svc: AppService):
    return svc.create_sample_case()


def pdf_fields(path: Path) -> dict[str, str]:
    reader = pypdf.PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    return {k: (v.get("/V") if isinstance(v, dict) else v) for k, v in (reader.get_fields() or {}).items()}


def release_freigabe_data(svc: AppService, case) -> None:
    for key in (
        "contract.number",
        "insured.first_name",
        "insured.last_name",
        "policyholder.name",
        "insured.birth_date",
    ):
        svc.confirm_field(case, key)
    for key in ("insured.street", "insured.house_number", "insured.postal_code", "insured.city"):
        fv = case.field(key)
        assert fv.status == FieldStatus.CONFLICT and fv.suggestion_index is not None
        svc.choose_candidate(case, key, fv.suggestion_index)  # bewusste Nutzerentscheidung (Nachtrag)
    svc.edit_field(case, "employer.employment_start", "01.01.2019")
    svc.edit_field(case, "employer.employment_end", "30.09.2026")


# ---------------------------------------------------------------------------


def test_sample_case_extraction_and_conflicts(svc: AppService, sample_case):
    case = sample_case
    assert len(case.documents) == 3
    for d in case.documents:
        stored = Path(d.stored_path)
        assert stored.exists() and not os.access(stored, os.W_OK) or os.geteuid() == 0
        assert d.text is not None and d.classification is not None
    types = {d.original_name: d.classification.doc_type.value for d in case.documents}
    assert types["MUSTER_Police_Direktversicherung.pdf"] == "POLICY"
    assert types["MUSTER_Nachtrag_Beitrag.pdf"] == "AMENDMENT"
    assert types["MUSTER_Standmitteilung_Scan.pdf"] == "STATUS_REPORT"
    f = case.contract_fields
    assert (
        f["contract.number"].status == FieldStatus.EXTRACTED
        and f["contract.number"].normalized_value == "8815201-7"
    )
    assert f["contract.number"].source_page is not None and f["contract.number"].source_quote
    prem = f["premium.amount"]
    assert prem.status == FieldStatus.CONFLICT and prem.normalized_value is None
    assert prem.candidates[prem.suggestion_index].document_type.value == "AMENDMENT"
    # Fußzeilen-IBAN des Versicherers wird nie als Kundendaten übernommen
    assert all(c.normalized != "DE02120300000000202051" for c in f["bank.iban"].candidates)
    # Auftragsdaten werden nie automatisch befüllt
    assert case.order_fields == {} or all(
        fv.status not in (FieldStatus.EXTRACTED, FieldStatus.CONFLICT) for fv in case.order_fields.values()
    )


def test_catalog_choices(svc: AppService):
    choices = {c.catalog_id: c for c in svc.template_choices()}
    assert len(choices) == 9
    assert (
        choices["swisslife_sl13065_freigabe"].kind == "form"
        and choices["swisslife_sl13065_freigabe"].available
    )
    assert choices["swisslife_sl18477_uebernahme"].kind == "form"
    assert sum(1 for c in choices.values() if c.kind == "letter" and c.available) == 6
    missing = choices["tecis_haftungsfreistellung_ak"]
    assert not missing.available and missing.status.value == "Originalvorlage fehlt"
    with pytest.raises(ServiceError):
        svc.active_template("tecis_haftungsfreistellung_ak")


def test_sl13065_full_flow_to_export(svc: AppService, sample_case):
    case = sample_case
    original_sha = sha(SL13065_PDF)
    svc.select_template(case, "swisslife_sl13065_freigabe")
    status = svc.review_status(case)
    assert not status.ready
    assert {"order.severance_consent", "order.waive_refund"} == set(status.relevant_order_keys)
    assert "contract.intermediary" not in status.relevant_contract_keys  # Abschlussvermittler irrelevant
    with pytest.raises(ExportBlockedError):
        svc.generate_pdf(case)

    release_freigabe_data(svc, case)
    svc.set_choice(case, "order.severance_consent", False)
    svc.set_choice(case, "order.waive_refund", False)
    status = svc.review_status(case)
    assert status.ready, [i.message for i in status.blocking_items]

    preview = svc.build_preview(case)
    assert preview.kind == "form" and preview.qa.passed and preview.page_images
    assert not preview.flatten_allowed and "Fixieren" in preview.flatten_reason

    out = svc.generate_pdf(case)
    assert out.path.parent == svc.export_dir and out.path.exists()
    assert re.fullmatch(r"2026-11-13_SWISSLIFE_SL13065_Freigabe_V\d{8}-[0-9A-F]{6}\.pdf", out.path.name)
    assert out.qa.passed
    fields = pdf_fields(out.path)
    assert fields["Versicherung Nummer"] == "8815201-7"
    assert fields["Name Vorname"] == "Musterfrau, Erika"
    assert fields["Datum 1.32"] == "1988" and fields["Datum 3.32"] == "2026"
    assert fields.get("Abfindung") in (None, "/Off", "Off")
    assert fields.get("Unterschrift") in (None, "")
    assert sha(SL13065_PDF) == original_sha  # Originalvorlage unverändert
    assert out.path.read_bytes().startswith(SL13065_PDF.read_bytes())  # inkrementell befüllt
    # Protokoll ohne personenbezogene Daten
    log_text = svc.paths.export_log_file.read_text("utf-8")
    assert "Musterfrau" not in log_text and "8815201" not in log_text
    # Wiederaufnahme nach Neustart
    reloaded = svc.load_case(case.id)
    assert reloaded.exports and reloaded.exports[-1].file_name == out.path.name
    assert reloaded.stage.name == "EXPORT"
    assert any(h.action == "export" for h in svc.case_history(case.id))
    assert not (svc.paths.preview_dir / case.id).exists()  # Vorschau-Dateien gelöscht


def test_sl18477_private_variant(svc: AppService, sample_case):
    case = sample_case
    original_sha = sha(SL18477_PDF)
    svc.select_template(case, "swisslife_sl18477_uebernahme")
    release_freigabe_data(svc, case)
    svc.confirm_field(case, "insured.nationality")
    svc.edit_field(case, "insured.birth_place", "Musterstadt")
    svc.set_choice(case, "order.continuation_mode", "private")
    status = svc.review_status(case)
    assert not status.ready
    codes = {i.code for i in status.blocking_items}
    assert "LEGAL_DECISION_MISSING" in codes or "CONDITION_UNDECIDED" in codes
    svc.set_choice(case, "order.private_contribution_option", "reduced")
    svc.edit_field(case, "order.reduced_premium_amount", "1.234,56")
    svc.set_choice(case, "order.pep_status", "no")
    svc.set_choice(case, "order.payment_method", "sepa")
    svc.edit_field(case, "order.payment_iban", "DE89 3704 0044 0532 0130 00")
    svc.edit_field(case, "order.payment_account_holder", "Erika Musterfrau")
    svc.edit_field(case, "order.payment_institution", "Beispielbank (MUSTER)")
    status = svc.review_status(case)
    assert any(i.code == "ACKNOWLEDGMENT_MISSING" for i in status.blocking_items)
    svc.set_choice(case, "order.advice_waiver_acknowledged", True)
    status = svc.review_status(case)
    assert status.ready, [i.message for i in status.blocking_items]
    out = svc.generate_pdf(case)
    doc = pymupdf.open(out.path)
    assert doc.page_count == 6
    fields = pdf_fields(out.path)
    assert fields["Fortführung der Versicherung"] == "/1"
    assert fields["reduzierter Beitrag"] == "1234.56"
    assert fields["IBAN"] == "DE89 3704 0044 0532 0130 00"
    assert fields["Firma"] in (None, "")  # Arbeitgeber-Abschnitt bleibt leer
    assert fields["Versicherung durch die versicherte Person"] == "/Ja"
    assert sha(SL18477_PDF) == original_sha
    assert {c.code for c in out.qa.checks if c.code == "PRESERVED_PAGES" and c.passed}


def test_letter_flow_kuendigung(svc: AppService, sample_case):
    case = sample_case
    svc.select_template(case, "kuendigung_ordentlich")
    assert case.get("order.letter_date").normalized_value == "2026-11-13"
    for key in (
        "contract.number",
        "policyholder.name",
        "policyholder.street",
        "policyholder.house_number",
        "policyholder.postal_code",
        "policyholder.city",
        "insurer.name",
        "insured.first_name",
        "insured.last_name",
    ):
        svc.confirm_field(case, key)
    svc.copy_insurer_to_recipient(case)
    svc.edit_field(case, "order.recipient_street", "Musterallee")
    svc.edit_field(case, "order.recipient_house_number", "1")
    svc.edit_field(case, "order.recipient_postal_code", "12345")
    svc.edit_field(case, "order.recipient_city", "Musterstadt")
    svc.set_choice(case, "order.termination_timing", "next_possible")
    svc.set_choice(case, "order.revoke_sepa", False)
    svc.set_choice(case, "order.payout_to_account", False)
    status = svc.review_status(case)
    assert any(i.code == "ACK_REQUIRED" for i in status.blocking_items)
    assert any(i.code == "BAV_TERMINATION" for i in status.checklist)  # Direktversicherung -> Warnung
    svc.set_choice(case, "order.deadline_checked", True)
    status = svc.review_status(case)
    assert status.ready, [i.message for i in status.blocking_items]
    assert status.hints
    preview = svc.build_preview(case)
    assert preview.kind == "letter" and preview.page_images
    out = svc.generate_pdf(case)
    assert re.fullmatch(r"2026-11-13_BRIEF_DIN5008_Kuendigung_V\d{8}-[0-9A-F]{6}\.pdf", out.path.name)
    text = " ".join(pymupdf.open(out.path)[0].get_text().split())
    assert "Kündigung des Versicherungsvertrags" in text and "VORSCHAU" not in text
    assert "zum nächstmöglichen Zeitpunkt" in text
    assert out.qa.passed


def test_field_input_validation(svc: AppService, sample_case):
    with pytest.raises(FieldInputError):
        svc.edit_field(sample_case, "employer.employment_start", "31.02.2020")
    fv = svc.edit_field(sample_case, "order.payment_iban", "DE00 3704 0044 0532 0130 00")  # Prüfziffer falsch
    assert any(m.code == "IBAN_CHECKSUM" for m in fv.validation)
    with pytest.raises(ServiceError):
        svc.confirm_field(sample_case, "premium.amount")  # Konflikt kann nicht pauschal bestätigt werden
    with pytest.raises(ServiceError):
        svc.mark_unclear(sample_case, "order.severance_consent")


def test_user_marked_unclear_survives_reextraction(svc: AppService, sample_case):
    svc.mark_unclear(sample_case, "insured.birth_date", "Geburtsdatum unleserlich")
    svc.extract_data(sample_case)
    assert sample_case.field("insured.birth_date").status == FieldStatus.UNCLEAR


def test_uploaded_original_untouched_and_duplicate(svc: AppService, tmp_path: Path):
    src = tmp_path / "eingang.pdf"
    src.write_bytes((SYN / "MUSTER_Police_Direktversicherung.pdf").read_bytes())
    before = (sha(src), src.stat().st_mtime_ns)
    case = svc.create_case()
    r1 = svc.add_document(case, src)
    r2 = svc.add_document(case, src)
    assert r1.accepted and r2.duplicate and not r2.accepted
    assert (sha(src), src.stat().st_mtime_ns) == before
    stored = Path(r1.document.stored_path)
    assert "eingang" not in stored.name  # keine Originalnamen im Ablageordner


def test_password_protected_pdf_never_persisted(svc: AppService, tmp_path: Path):
    src = tmp_path / "geschuetzt.pdf"
    doc = pymupdf.open(SYN / "MUSTER_Police_Direktversicherung.pdf")
    secret = "Geheim-4711!"
    doc.save(src, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=secret, owner_pw=secret + "x")
    case = svc.create_case()
    r = svc.add_document(case, src)
    assert r.needs_password and not r.accepted
    r = svc.add_document(case, src, password="falsch")
    assert r.needs_password and r.wrong_password
    r = svc.add_document(case, src, password=secret)
    assert r.accepted
    svc.process_documents(case)
    assert case.contract_fields["contract.number"].normalized_value == "8815201-7"
    for f in svc.paths.data.rglob("*"):
        if f.is_file():
            assert secret.encode() not in f.read_bytes(), f


def test_logs_contain_no_personal_data(tmp_path: Path):
    service = AppService(
        AppPaths.for_testing(tmp_path), guard_network=False, configure_logs=True, today=lambda: TODAY
    )
    try:
        case = service.create_sample_case()
        service.select_template(case, "swisslife_sl13065_freigabe")
        service.review_status(case)
    finally:
        service.close()
        from insurance_app.security.logging_setup import shutdown_logging

        shutdown_logging()
    text = "".join(p.read_text("utf-8", errors="ignore") for p in service.paths.logs_dir.glob("*.log*"))
    assert text, "Log sollte technische Einträge enthalten"
    for needle in ("Musterfrau", "Erika", "8815201", "DE89", "Bäckerei", "Beispielweg"):
        assert needle not in text


def test_network_guard_blocks_external(tmp_path: Path):
    from insurance_app.security.network_guard import NetworkBlockedError, uninstall_network_guard

    service = AppService(AppPaths.for_testing(tmp_path), guard_network=True, configure_logs=False)
    try:
        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("203.0.113.1", 80), timeout=1)
    finally:
        service.close()
        uninstall_network_guard()


@pytest.mark.slow
def test_portal_screenshot_ocr_and_bav_plausibility(svc: AppService):
    if not svc.ocr_status().get("tesseract", (False, ""))[0]:
        pytest.skip("Tesseract nicht installiert")
    case = svc.create_case()
    svc.add_document(case, SYN / "MUSTER_Vertragsuebersicht_Portal.png")
    notes, _ = svc.process_documents(case)
    doc = case.documents[0]
    assert doc.text.pages[0].method.value == "TESSERACT"
    assert doc.classification.doc_type.value == "PORTAL_OVERVIEW"
    f = case.contract_fields
    assert f["contract.number"].normalized_value == "5550123-4"
    assert f["contract.start_date"].normalized_value == "2022-02-01"
    assert f["contract.end_date"].normalized_value == "2065-08-01"
    assert f["contract.intermediary"].status in (FieldStatus.EXTRACTED, FieldStatus.UNCLEAR)
    svc.select_template(case, "swisslife_sl13065_freigabe")
    msgs = svc.case_messages(case)
    assert any(
        m.code == "BAV_PRODUCT_UNEXPECTED" for m in msgs
    )  # Basisrente/Eigenvertrag ≠ Direktversicherung
