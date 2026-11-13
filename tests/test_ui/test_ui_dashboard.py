"""UI-Tests Dashboard „Vorgänge“ (offscreen, nur synthetische Daten)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication
from uia_helpers import (
    AMENDMENT_PDF,
    POLICY_PDF,
    DialogRecorder,
    flush_tasks,
    make_encrypted_copy,
    make_service,
)

from insurance_app.ui.bundle import choice
from insurance_app.ui.pages.case_view import TAB_DOCUMENTS, CaseView
from insurance_app.ui.pages.dashboard import COLUMNS, DashboardPage

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


@pytest.fixture(autouse=True)
def choice_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Mehrere Dateien: Rückfrage „ein Vertrag / mehrere Verträge“ – hier immer „ein Vertrag“."""
    calls: list[int] = []

    def answer(parent, file_count):
        calls.append(file_count)
        return choice.CHOICE_SINGLE

    monkeypatch.setattr(choice, "ask_single_or_bundle", answer)
    return calls


def make_page(qtbot, svc, open_case=None) -> DashboardPage:
    page = DashboardPage(svc, open_case=open_case or (lambda _cid: None))
    qtbot.addWidget(page)
    return page


# ---------------------------------------------------------------------------


def test_dashboard_lists_cases_and_live_search(qtbot, svc, dlg):
    alpha = svc.create_case("Alpha Musterfall")
    beta = svc.create_case("Beta Vorgang")
    page = make_page(qtbot, svc)
    headers = [page.table.horizontalHeaderItem(i).text() for i in range(page.table.columnCount())]
    assert headers == list(COLUMNS)
    assert page.table.rowCount() == 2
    assert page.table.item(0, 2).text() == "1/9 Ablage"
    assert page.table.item(0, 0).text().count(".") == 2  # TT.MM.JJJJ HH:MM

    page.search.setText("Alpha")  # Live-Suche ohne Enter
    assert page.row_titles() == ["Alpha Musterfall"]
    page.search.setText(beta.id)
    assert page.row_titles() == ["Beta Vorgang"]
    page.search.setText("gibt-es-nicht")
    assert page.table.rowCount() == 0 and not page.empty_label.isHidden()
    page.search.clear()

    svc.archive_case(beta.id)
    page.refresh()
    assert page.row_titles() == ["Alpha Musterfall"]
    page.show_archived.setChecked(True)
    assert "Beta Vorgang  (archiviert)" in page.row_titles()
    assert page.select_case(beta.id)
    assert page.archive_btn.text() == "Wiederherstellen"
    page.archive_btn.click()
    assert not svc.load_case(beta.id).archived

    assert page.select_case(alpha.id)
    dlg.text_answers.append("Alpha umbenannt")
    page.rename_btn.click()
    assert svc.load_case(alpha.id).title == "Alpha umbenannt"
    assert dlg.texts[0][1] == "Alpha Musterfall"


def test_open_selected_case_by_button_and_double_click(qtbot, svc, dlg):
    case = svc.create_case("Erika Musterfrau (MUSTER)")
    opened: list[str] = []
    page = make_page(qtbot, svc, opened.append)
    assert not page.open_btn.isEnabled()
    page.select_case(case.id)
    assert page.open_btn.isEnabled()
    page.open_btn.click()
    page.table.cellDoubleClicked.emit(0, 1)
    assert opened == [case.id, case.id]


def test_choose_files_creates_case_and_processes(qtbot, svc, dlg, choice_calls):
    view = CaseView(svc, show_catalog=lambda: None)
    qtbot.addWidget(view)
    page = make_page(qtbot, svc, view.open_case)
    dlg.files = [POLICY_PDF, AMENDMENT_PDF]
    page.choose_btn.click()
    qtbot.waitUntil(
        lambda: (
            view.current_case_id() is not None
            and not view.controller.busy
            and bool(view.controller.case and view.controller.case.contract_fields)
        ),
        timeout=30_000,
    )
    case = svc.load_case(view.current_case_id())
    assert [d.original_name for d in case.documents] == [POLICY_PDF.name, AMENDMENT_PDF.name]
    assert all(d.text is not None for d in case.documents)
    assert case.contract_fields["contract.number"].normalized_value == "8815201-7"
    assert view.tabs.currentIndex() == TAB_DOCUMENTS
    assert view.documents.summary is not None  # Ergebnis der Auswertung sichtbar
    assert page.table.rowCount() == 1
    assert choice_calls == [2]  # zwei Dateien -> Rückfrage, Antwort „ein Vertrag“
    assert svc.list_bundles() == []
    assert not dlg.errors


@pytest.mark.filterwarnings("ignore::DeprecationWarning")  # QDragEnterEvent(QPoint, …) in Qt 6
def test_drop_files_on_drop_area(qtbot, svc, dlg):
    opened: list[str] = []
    page = make_page(qtbot, svc, opened.append)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(POLICY_PDF))])
    enter = QDragEnterEvent(
        QPoint(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(page.drop_area, enter)
    assert enter.isAccepted()
    assert page.drop_area.property("dragActive") is True
    drop = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(page.drop_area, drop)
    assert page.drop_area.property("dragActive") is False
    assert len(opened) == 1
    # Ohne Vorgangsansicht wertet das Dashboard selbst aus
    qtbot.waitUntil(lambda: not page._busy, timeout=30_000)
    case = svc.load_case(opened[0])
    assert case.documents[0].text is not None and case.contract_fields


def test_password_protected_pdf_asks_password_and_succeeds(qtbot, svc, dlg, tmp_path):
    protected = make_encrypted_copy(POLICY_PDF, tmp_path / "MUSTER_geschuetzt.pdf", "MUSTER-1234")
    opened: list[str] = []
    page = make_page(qtbot, svc, opened.append)
    dlg.password_answers = ["falsch", "MUSTER-1234"]
    case_id = page.import_files([protected])
    assert dlg.passwords == [("MUSTER_geschuetzt.pdf", False), ("MUSTER_geschuetzt.pdf", True)]
    assert case_id is not None and opened == [case_id]
    qtbot.waitUntil(lambda: not page._busy, timeout=30_000)
    case = svc.load_case(case_id)
    assert len(case.documents) == 1 and case.documents[0].text is not None
    assert not dlg.errors


def test_password_cancel_skips_file_and_removes_empty_case(qtbot, svc, dlg, tmp_path):
    protected = make_encrypted_copy(POLICY_PDF, tmp_path / "MUSTER_geschuetzt.pdf", "MUSTER-1234")
    opened: list[str] = []
    page = make_page(qtbot, svc, opened.append)
    dlg.password_answers = [None]
    assert page.import_files([protected]) is None
    assert opened == [] and svc.list_cases() == []
    assert dlg.errors and "kein Passwort" in dlg.errors[0][1]


def test_rejected_files_are_summarised(qtbot, svc, dlg, tmp_path):
    bad = tmp_path / "notiz.txt"
    bad.write_text("keine PDF", encoding="utf-8")
    opened: list[str] = []
    page = make_page(qtbot, svc, opened.append)
    case_id = page.import_files([POLICY_PDF, bad])
    assert case_id is not None
    assert dlg.infos and "notiz.txt" in dlg.infos[0][1]
    qtbot.waitUntil(lambda: not page._busy, timeout=30_000)


def test_sample_case_button_creates_and_opens_case(qtbot, svc, dlg):
    opened: list[str] = []
    page = make_page(qtbot, svc, opened.append)
    page.sample_btn.click()
    qtbot.waitUntil(lambda: bool(opened), timeout=30_000)
    assert page.table.rowCount() == 1
    assert page.row_titles()[0].startswith("MUSTER")
    assert page.table.item(0, 4).text() == "3"
    assert page.sample_btn.isEnabled()


def test_delete_case_asks_confirmation_and_cancel_keeps_it(qtbot, svc, dlg):
    case = svc.create_case("Erika Musterfrau (MUSTER)")
    page = make_page(qtbot, svc)
    page.select_case(case.id)
    dlg.confirm_answers = [False]
    page.delete_btn.click()
    confirm = dlg.confirms[0]
    assert confirm["danger"] is True
    assert "unwiderruflich" in confirm["text"] and "Exportordner bleiben erhalten" in confirm["text"]
    assert [s.id for s in svc.list_cases()] == [case.id]

    page.select_case(case.id)
    dlg.confirm_answers = [True]
    page.delete_btn.click()
    assert svc.list_cases() == []
    assert page.table.rowCount() == 0
