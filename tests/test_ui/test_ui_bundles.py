"""UI-Tests „Kundenmappen“: Mappen verwalten, Warteschlange, Anwenderwunsch, „Alle fertigen erzeugen“.

Alle Daten sind synthetisch („MUSTER – KEINE ECHTDATEN“). Modale Dialoge werden ersetzt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from uic_helpers import (
    AMENDMENT_PDF,
    KRANKEN_INSURER,
    POLICY_PDF,
    SACH_INSURER,
    DialogRecorder,
    apply_app_style,
    dispose,
    flush_tasks,
    make_encrypted_copy,
    make_kranken_pdf,
    make_policy_pdf,
    make_service,
    save_grab,
)

from insurance_app.core.service import BatchItemStatus
from insurance_app.ui.bundle.common import BUNDLE_EXPLANATION, GENERATE_CONFIRM_TEXT, TITLE_LABEL
from insurance_app.ui.bundle.importing import SKIPPED_MESSAGE
from insurance_app.ui.bundle.tables import (
    CONTRACT_COLUMNS,
    NO_TEMPLATE_STATUS,
    NO_TEMPLATE_TEXT,
    QUEUE_COLUMNS,
    READY_TEXT,
)
from insurance_app.ui.pages.bundles import BundlesPage

pytestmark = pytest.mark.ui

BUNDLE_TITLE = "Kundin Erika Musterfrau – Optimierung (MUSTER)"


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
def third_pdf(tmp_path: Path) -> Path:
    return make_policy_pdf(tmp_path / "MUSTER_Police_Sachversicherung.pdf")


def make_page(qtbot, svc, opened: list[str] | None = None) -> BundlesPage:
    page = BundlesPage(svc, open_case=(opened.append if opened is not None else (lambda _cid: None)))
    qtbot.addWidget(page)
    page.resize(1360, 860)
    return page


def wait_idle(qtbot, page: BundlesPage) -> None:
    qtbot.waitUntil(lambda: not page.busy, timeout=60_000)
    flush_tasks()


def hints(page: BundlesPage) -> dict[str, str]:
    return {r.contract_hint: r.case_id for r in page.overview}


def fill_widerruf(svc, case) -> None:
    """Pflichtangaben eines Widerrufs wie ein Mensch prüfen bzw. eintragen (synthetisch)."""
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
    svc.set_choice(case, "order.deadline_checked", True)
    svc.set_choice(case, "order.revocation_subject", "termination")
    svc.edit_field(case, "order.termination_letter_date", "01.11.2026")


# ---------------------------------------------------------------------------


def test_create_rename_dissolve_and_delete_bundles(qtbot, svc, dlg):
    page = make_page(qtbot, svc)
    assert page.detail_stack.currentIndex() == 0 and "Noch keine Kundenmappe" in page.empty_label.text()
    assert not page.lower.isVisibleTo(page)
    assert not page.rename_btn.isEnabled() and not page.delete_btn.isEnabled()

    # Neue Mappe (Abbrechen legt nichts an)
    dlg.text_answers = [None]
    page.new_btn.click()
    assert svc.list_bundles() == []
    dlg.text_answers = ["Kundin Müller – Optimierung (MUSTER)"]
    page.new_btn.click()
    assert dlg.texts[-1][:2] == ("Neue Kundenmappe", TITLE_LABEL)
    bundles = svc.list_bundles()
    assert [b.title for b in bundles] == ["Kundin Müller – Optimierung (MUSTER)"]
    bid = bundles[0].id
    assert page.selected_bundle_id() == bid
    assert page.detail_stack.currentIndex() == 1 and page.lower.isVisibleTo(page)
    assert page.explanation.text() == BUNDLE_EXPLANATION
    item_text = page.bundle_list.item(0).text()
    assert "Kundin Müller – Optimierung (MUSTER)" in item_text and "0 Verträge" in item_text
    assert item_text.split("\n")[1].count(".") == 2  # Datum TT.MM.JJJJ
    assert not page.generate_btn.isEnabled()  # noch keine Verträge

    # Umbenennen (bisheriger Name wird vorgeschlagen)
    dlg.text_answers = ["Kundin Müller – neu (MUSTER)"]
    page.rename_btn.click()
    assert dlg.texts[-1][2] == "Kundin Müller – Optimierung (MUSTER)"
    assert svc.get_bundle(bid).title == "Kundin Müller – neu (MUSTER)"
    assert page.title_label.text() == "Kundin Müller – neu (MUSTER)"

    # Auflösen: Vorgänge bleiben erhalten
    case = svc.create_case("Vorgang in Mappe (MUSTER)")
    case.bundle_id = bid
    svc.save_case(case)
    page.refresh()
    assert "1 Vertrag" in page.bundle_list.item(0).text()
    dlg.confirm_answers = [False]
    page.dissolve_btn.click()
    assert dlg.confirms[-1]["danger"] is False
    assert "bleiben vollständig erhalten" in dlg.confirms[-1]["text"]
    assert [b.id for b in svc.list_bundles()] == [bid]
    dlg.confirm_answers = [True]
    page.dissolve_btn.click()
    assert svc.list_bundles() == []
    assert svc.load_case(case.id).bundle_id is None
    assert page.detail_stack.currentIndex() == 0

    # Mappe und Vorgänge löschen: Warnung (unwiderruflich), Abbrechen ist Standard
    dlg.text_answers = ["Kunde Beispiel (MUSTER)"]
    page.new_btn.click()
    bid2 = page.selected_bundle_id()
    case2 = svc.create_case("Zweiter Vorgang (MUSTER)")
    case2.bundle_id = bid2
    svc.save_case(case2)
    page.refresh()
    dlg.confirm_answers = [False]
    page.delete_btn.click()
    confirm = dlg.confirms[-1]
    assert confirm["danger"] is True
    assert "unwiderruflich" in confirm["text"] and "nicht rückgängig" in confirm["text"]
    assert "Exportordner bleiben erhalten" in confirm["text"]
    assert svc.get_bundle(bid2).case_count == 1
    dlg.confirm_answers = [True]
    page.delete_btn.click()
    assert svc.list_bundles() == []
    assert case2.id not in {s.id for s in svc.list_cases(include_archived=True)}
    assert case.id in {s.id for s in svc.list_cases(include_archived=True)}
    assert not dlg.errors, dlg.error_text()


def test_import_queue_groups_files_by_contract(qtbot, svc, dlg, third_pdf):
    bundle = svc.create_bundle(BUNDLE_TITLE)
    page = make_page(qtbot, svc)
    assert page.select_bundle(bundle.id)
    headers = [page.queue_table.horizontalHeaderItem(c).text() for c in range(page.queue_table.columnCount())]
    assert headers == list(QUEUE_COLUMNS)

    dlg.files = [POLICY_PDF, AMENDMENT_PDF, third_pdf]
    page.choose_btn.click()
    # Sofort sichtbar: alle Dateien warten in der Warteschlange
    assert page.busy
    assert page.queue_table.rowCount() == 3
    assert page.queue_table.row_texts(1) == ["○ Wartet"] * 3
    assert not page.choose_btn.isEnabled() and not page.bundle_list.isEnabled()
    wait_idle(qtbot, page)

    assert page.queue_table.row_texts(1) == [f"✔ {BatchItemStatus.DONE.value}"] * 3
    assert page.queue_table.row_texts(0) == [POLICY_PDF.name, AMENDMENT_PDF.name, third_pdf.name]
    types = page.queue_table.row_texts(2)
    assert types[1] == "Nachtrag" and "Police" in types[0]
    assert page.queue_table.row_texts(3)[2] == SACH_INSURER
    assigned = page.queue_table.row_texts(4)
    assert assigned[0] == assigned[1] != assigned[2]  # Police + Nachtrag -> derselbe Vorgang
    assert "…2017" in assigned[0] and "…0012" in assigned[2]
    assert not assigned[0].startswith(BUNDLE_TITLE)  # Mappenname wird in der Mappe nicht wiederholt
    assert page.queue_summary.text().startswith("3 von 3 Dateien fertig")

    # Verträge der Mappe: 2 Vorgänge
    table = page.contracts_table
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == list(CONTRACT_COLUMNS)
    assert table.rowCount() == 2
    rows = {r.contract_hint: r for r in page.overview}
    assert set(rows) == {"…2017", "…0012"}
    assert rows["…2017"].document_count == 2 and rows["…0012"].document_count == 1
    assert set(table.status_texts().values()) == {NO_TEMPLATE_STATUS}
    assert "2 Verträge" in page.bundle_list.currentItem().text()
    assert page.generate_btn.isEnabled() and page.choose_btn.isEnabled()

    # Später nachgereichte Datei desselben Vertrags landet im vorhandenen Vorgang
    dlg.files = [AMENDMENT_PDF]
    page.choose_btn.click()
    wait_idle(qtbot, page)
    assert page.queue_table.rowCount() == 3  # gleiche Datei -> Zeile wiederverwendet
    assert len(page.overview) == 2
    assert not dlg.errors, dlg.error_text()


def test_busy_queue_keeps_bundle_and_rejects_new_files(qtbot, svc, dlg, third_pdf):
    first = svc.create_bundle(BUNDLE_TITLE)
    other = svc.create_bundle("Kunde Beispiel (MUSTER)")
    page = make_page(qtbot, svc)
    page.select_bundle(first.id)
    assert page.start_import([POLICY_PDF, third_pdf])
    assert page.busy
    assert not page.select_bundle(other.id)  # laufende Mappe bleibt sichtbar
    assert page.selected_bundle_id() == first.id
    assert not page.start_import([AMENDMENT_PDF])
    assert dlg.infos and dlg.infos[-1][0] == "Bitte warten"
    wait_idle(qtbot, page)
    assert page.select_bundle(other.id) and page.queue_items() == []
    assert page.select_bundle(first.id) and len(page.queue_items()) == 2  # Warteschlange je Mappe


def test_password_protected_file_is_asked_and_retried(qtbot, svc, dlg, tmp_path):
    locked = make_encrypted_copy(
        make_policy_pdf(tmp_path / "sach.pdf"), tmp_path / "MUSTER_geschuetzt.pdf", "MUSTER-1234"
    )
    bundle = svc.create_bundle(BUNDLE_TITLE)
    page = make_page(qtbot, svc)
    page.select_bundle(bundle.id)
    dlg.password_answers = ["falsch", "MUSTER-1234"]
    assert page.start_import([POLICY_PDF, locked])
    wait_idle(qtbot, page)
    assert dlg.passwords == [("MUSTER_geschuetzt.pdf", False), ("MUSTER_geschuetzt.pdf", True)]
    assert page.queue_table.row_texts(1) == ["✔ Fertig", "✔ Fertig"]
    assert len(page.overview) == 2
    assert not dlg.errors, dlg.error_text()


def test_password_cancel_skips_file(qtbot, svc, dlg, tmp_path):
    locked = make_encrypted_copy(POLICY_PDF, tmp_path / "MUSTER_geschuetzt.pdf", "MUSTER-1234")
    bundle = svc.create_bundle(BUNDLE_TITLE)
    page = make_page(qtbot, svc)
    page.select_bundle(bundle.id)
    dlg.password_answers = [None]
    page.start_import([locked])
    wait_idle(qtbot, page)
    item = page.queue_items()[0]
    assert item.status == BatchItemStatus.NEEDS_PASSWORD and item.message == SKIPPED_MESSAGE
    assert page.queue_table.row_texts(1) == ["⚠ Passwort erforderlich"]
    assert page.overview == []


def test_template_choice_persists_and_open_case(qtbot, svc, dlg, third_pdf):
    bundle = svc.create_bundle(BUNDLE_TITLE)
    svc.import_into_bundle(bundle.id, [POLICY_PDF, third_pdf])
    opened: list[str] = []
    page = make_page(qtbot, svc, opened)
    page.select_bundle(bundle.id)
    cid = hints(page)["…2017"]
    combo = page.contracts_table.combos[cid]
    assert combo.itemText(0) == NO_TEMPLATE_TEXT
    available = {c.catalog_id for c in svc.template_choices() if c.available}
    assert {combo.itemData(i) for i in range(1, combo.count())} == available
    idx = combo.findData("widerruf")
    combo.setCurrentIndex(idx)
    combo.activated.emit(idx)
    assert svc.load_case(cid).template_id == "widerruf"
    status = page.contracts_table.status_texts()[cid]
    assert status.startswith("✖") and "offene" in status  # Prüfung bleibt Pflicht

    page.refresh()  # neue Kombinationsfelder zeigen die gespeicherte Wahl
    combo = page.contracts_table.combos[cid]
    assert combo.currentData() == "widerruf"
    other = hints(page)["…0012"]
    assert page.contracts_table.combos[other].currentData() is None

    combo.setCurrentIndex(0)  # „– bitte wählen –“ -> Wunsch zurücknehmen
    combo.activated.emit(0)
    assert svc.load_case(cid).template_id is None
    assert page.contracts_table.status_texts()[cid] == NO_TEMPLATE_STATUS

    page.contracts_table.open_buttons[other].click()
    assert opened == [other]
    assert not dlg.errors, dlg.error_text()


def test_generate_all_reports_files_and_skipped_reasons(qtbot, svc, dlg, third_pdf):
    bundle = svc.create_bundle(BUNDLE_TITLE)
    svc.import_into_bundle(bundle.id, [POLICY_PDF, AMENDMENT_PDF, third_pdf])
    page = make_page(qtbot, svc)
    page.select_bundle(bundle.id)
    ready_id, open_id = hints(page)["…2017"], hints(page)["…0012"]
    ready = svc.load_case(ready_id)
    svc.select_template(ready, "widerruf")
    fill_widerruf(svc, ready)
    assert svc.review_status(ready).ready, [i.message for i in svc.review_status(ready).blocking_items]
    page.refresh()
    assert page.contracts_table.status_texts() == {ready_id: READY_TEXT, open_id: NO_TEMPLATE_STATUS}
    assert "1 von 2 Vorgängen bereit" in page.ready_label.text()

    dlg.confirm_answers = [False]  # Abbrechen erzeugt nichts
    page.generate_btn.click()
    assert dlg.confirms[-1]["text"] == GENERATE_CONFIRM_TEXT
    assert not page.busy and not page.result_frame.isVisibleTo(page)

    dlg.confirm_answers = [True]
    page.generate_btn.click()
    wait_idle(qtbot, page)
    assert page.result_frame.isVisibleTo(page)
    case_ids = [
        page.result_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
        for r in range(page.result_table.rowCount())
    ]
    results = dict(zip(case_ids, page.result_table.result_texts(), strict=True))
    assert results[ready_id].startswith("✔ ") and results[ready_id].endswith(".pdf")
    assert results[open_id] == "✖ Keine Vorlage (Anwenderwunsch) gewählt."
    assert "1 Dokument erzeugt, 1 übersprungen" in page.result_summary.text()
    exported = svc.load_case(ready_id).exports[-1]
    assert (svc.export_dir / exported.file_name).is_file()
    assert (
        page.contracts_table.item([r.case_id for r in page.overview].index(ready_id), 6).text()
        == f"✔ {exported.file_name}"
    )

    page.open_export_btn.click()
    assert dlg.opened == [svc.export_dir]
    assert not dlg.errors, dlg.error_text()


def test_generate_all_skips_unreviewed_case_with_reason(qtbot, svc, dlg, third_pdf):
    bundle = svc.create_bundle(BUNDLE_TITLE)
    svc.import_into_bundle(bundle.id, [POLICY_PDF, third_pdf])
    page = make_page(qtbot, svc)
    page.select_bundle(bundle.id)
    cid = hints(page)["…2017"]
    svc.select_template(svc.load_case(cid), "widerruf")  # gewählt, aber nicht geprüft
    page.refresh()
    dlg.confirm_answers = [True]
    page.generate_all()
    wait_idle(qtbot, page)
    texts = page.result_table.result_texts()
    assert all(t.startswith("✖ ") for t in texts)
    assert any("offene Pflichtpunkte" in t for t in texts)
    assert any("Keine Vorlage" in t for t in texts)
    assert "0 Dokumente erzeugt, 2 übersprungen" in page.result_summary.text()
    assert not list(svc.export_dir.glob("*.pdf"))


def test_bundles_screenshot(qtbot, svc, dlg, third_pdf, tmp_path):
    """Bildschirmfoto ``ui_bundles.png``: Mappe mit 3 synthetischen Verträgen (nur mit ``LVT_SCREENSHOTS``)."""
    from insurance_app.ui.main_window import MainWindow

    apply_app_style()
    kranken = make_kranken_pdf(tmp_path / "MUSTER_Police_Krankenversicherung.pdf")
    bundle = svc.create_bundle(BUNDLE_TITLE)
    win = MainWindow(svc)
    win.resize(1360, 860)
    try:
        win.show()
        qtbot.waitExposed(win)
        page = win.pages["bundles"]
        win.open_bundle(bundle.id, [POLICY_PDF, AMENDMENT_PDF, third_pdf, kranken])
        wait_idle(qtbot, page)
        assert page.queue_table.row_texts(1) == ["✔ Fertig"] * 4
        assert len(page.overview) == 3
        assert {r.insurer for r in page.overview} >= {SACH_INSURER, KRANKEN_INSURER}
        cid = hints(page)["…2017"]
        combo = page.contracts_table.combos[cid]
        idx = combo.findData("widerruf")
        combo.setCurrentIndex(idx)
        combo.activated.emit(idx)
        qtbot.wait(50)
        page.scroll_to_queue()  # Warteschlange und Verträge im Bild
        qtbot.wait(50)
        save_grab(win, "ui_bundles.png")
    finally:
        dispose(win)
