"""Kundenmappe / Stapelverarbeitung: 1 Kundin, 3 Verträge, 3 Dokumente(-gruppen) -> 3 Ausgaben.

Alle Daten synthetisch („MUSTER – KEINE ECHTDATEN“).
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from insurance_app.core.paths import AppPaths
from insurance_app.core.service import AppService, BatchItemStatus

TODAY = date(2026, 11, 13)
SYN = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "LiberationSans-Regular.ttf"


def third_policy(path: Path) -> Path:
    """Synthetische Police einer anderen Gesellschaft (anderer Vertrag)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="lib", fontfile=str(FONT))
    lines = [
        ("Muster Sachversicherung AG (MUSTER)", 60),
        ("MUSTER – KEINE ECHTDATEN", 80),
        ("Versicherungsschein", 130),
        ("Versicherungsnummer:      7770001-2", 170),
        ("Versicherungsnehmer:      Musterfrau, Erika", 190),
        ("Versicherungsbeginn:      01.03.2021", 210),
        ("Beitrag:                  25,00 EUR monatlich", 230),
    ]
    for text, y in lines:
        page.insert_text((60, y), text, fontname="lib", fontsize=11)
    doc.save(path)
    return path


@pytest.fixture()
def svc(tmp_path: Path):
    service = AppService(
        AppPaths.for_testing(tmp_path), guard_network=False, configure_logs=False, today=lambda: TODAY
    )
    yield service
    service.close()


def fill_letter_basics(svc: AppService, case) -> None:
    for key in ("policyholder.name", "contract.number"):
        fv = case.field(key)
        if fv.status.value == "EXTRACTED":
            svc.confirm_field(case, key)
        elif fv.status.value == "CONFLICT":
            svc.choose_candidate(case, key, fv.suggestion_index or 0)
    for key, value in (
        ("policyholder.name", "Musterfrau"),
        ("policyholder.first_name", "Erika"),
        ("policyholder.street", "Beispielweg"),
        ("policyholder.house_number", "7"),
        ("policyholder.postal_code", "12345"),
        ("policyholder.city", "Musterstadt"),
        ("order.recipient_company", "Beispiel Versicherung (MUSTER)"),
        ("order.recipient_street", "Musterallee"),
        ("order.recipient_house_number", "1"),
        ("order.recipient_postal_code", "54321"),
        ("order.recipient_city", "Beispielstadt"),
    ):
        if not case.field(key).status.is_released:
            svc.edit_field(case, key, value)


@pytest.mark.slow
def test_customer_with_three_contracts_three_outputs(svc: AppService, tmp_path: Path):
    files = [
        SYN / "MUSTER_Police_Direktversicherung.pdf",  # Vertrag A
        SYN / "MUSTER_Vertragsuebersicht_Portal.png",  # Vertrag B (Bild -> OCR)
        third_policy(tmp_path / "police_sach.pdf"),  # Vertrag C, andere Gesellschaft
        SYN / "MUSTER_Nachtrag_Beitrag.pdf",  # gehört zu Vertrag A
    ]
    bundle = svc.create_bundle("Kundin A – Optimierung (MUSTER)")
    seen: list[tuple[int, int]] = []
    result = svc.import_into_bundle(bundle.id, files, progress=lambda c, t, m: seen.append((c, t)))
    assert all(i.status == BatchItemStatus.DONE for i in result.items), [
        (i.file_name, i.message) for i in result.items
    ]
    assert seen and seen[-1][0] == seen[-1][1]  # Fortschritt läuft bis zum Ende
    rows = {r.contract_hint: r for r in svc.bundle_overview(bundle.id)}
    assert len(rows) == 3, rows
    assert rows["…2017"].document_count == 2  # Police + Nachtrag automatisch zusammengeführt
    assert {r.document_count for r in rows.values()} == {2, 1}
    assert svc.get_bundle(bundle.id).case_count == 3

    # Je Vertrag ein anderer Anwenderwunsch
    wishes = {"…2017": "widerruf", "…1234": "kuendigung_ordentlich", "…0012": "hdi_beitragsfreistellung"}
    for hint, catalog_id in wishes.items():
        case = svc.load_case(rows[hint].case_id)
        svc.select_template(case, catalog_id)
        fill_letter_basics(svc, case)
        svc.set_choice(case, "order.deadline_checked", True)
        if catalog_id == "widerruf":
            svc.set_choice(case, "order.revocation_subject", "termination")
            svc.edit_field(case, "order.termination_letter_date", "01.11.2026")
        elif catalog_id == "kuendigung_ordentlich":
            svc.set_choice(case, "order.termination_timing", "next_possible")
            svc.set_choice(case, "order.revoke_sepa", False)
            svc.set_choice(case, "order.payout_to_account", False)
        else:
            svc.set_choice(case, "order.premium_change_type", "exemption")
            svc.edit_field(case, "order.change_start_date", "01.12.2026")
            svc.mark_not_applicable(case, "order.change_end_date")
            svc.set_choice(case, "order.sepa_mandate_continues", True)
        status = svc.review_status(case)
        assert status.ready, (catalog_id, [i.message for i in status.blocking_items])

    exports = svc.generate_bundle(bundle.id)
    assert len(exports) == 3 and all(e.ok for e in exports), [(e.title, e.message) for e in exports]
    names = sorted(e.path.name for e in exports)
    assert any("_Widerruf_" in n for n in names)
    assert any("_Kuendigung_" in n for n in names)
    assert any("_Beitragsaenderung_" in n for n in names)
    assert all(e.path.parent == svc.export_dir for e in exports)
    overview = svc.bundle_overview(bundle.id)
    assert all(r.exported_file for r in overview)


def test_batch_skips_unready_cases_with_reason_and_supports_cancel(svc: AppService, tmp_path: Path):
    bundle = svc.create_bundle("Mappe B (MUSTER)")
    result = svc.import_into_bundle(
        bundle.id, [SYN / "MUSTER_Police_Direktversicherung.pdf", third_policy(tmp_path / "c.pdf")]
    )
    assert len(result.case_ids) == 2
    exports = svc.generate_bundle(bundle.id)
    assert not any(e.ok for e in exports)
    assert all("Keine Vorlage" in e.message for e in exports)
    case = svc.load_case(result.case_ids[0])
    svc.select_template(case, "widerruf")
    exports = svc.generate_bundle(bundle.id)
    msgs = {e.case_id: e.message for e in exports}
    assert "offene Pflichtpunkte" in msgs[case.id]
    # Abbruch: nichts wird mehr übernommen
    cancel = threading.Event()
    cancel.set()
    result = svc.import_into_bundle(bundle.id, [SYN / "MUSTER_Nachtrag_Beitrag.pdf"], cancel=cancel)
    assert result.items[0].status == BatchItemStatus.CANCELLED and not result.case_ids


def test_batch_adds_late_amendment_to_existing_contract_and_handles_bad_files(
    svc: AppService, tmp_path: Path
):
    bundle = svc.create_bundle("Mappe C (MUSTER)")
    first = svc.import_into_bundle(bundle.id, [SYN / "MUSTER_Police_Direktversicherung.pdf"])
    junk = tmp_path / "notiz.txt"
    junk.write_text("kein Dokument")
    locked = tmp_path / "geschuetzt.pdf"
    d = pymupdf.open(SYN / "MUSTER_Nachtrag_Beitrag.pdf")
    d.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="Muster-1", owner_pw="Muster-2")
    second = svc.import_into_bundle(bundle.id, [junk, locked])
    status = {i.file_name: i.status for i in second.items}
    assert status["notiz.txt"] == BatchItemStatus.REJECTED
    assert status["geschuetzt.pdf"] == BatchItemStatus.NEEDS_PASSWORD
    third = svc.import_into_bundle(bundle.id, [locked], passwords={str(locked): "Muster-1"})
    assert third.items[0].status == BatchItemStatus.DONE
    assert third.case_ids == first.case_ids  # Nachtrag dem vorhandenen Vertrag zugeordnet
    case = svc.load_case(first.case_ids[0])
    assert len(case.active_documents()) == 2
    assert case.field("premium.amount").status.value == "CONFLICT"  # Police 150 vs. Nachtrag 100
