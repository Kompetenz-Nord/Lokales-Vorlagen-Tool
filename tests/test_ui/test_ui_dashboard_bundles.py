"""Dashboard: mehrere Dateien -> Rückfrage „ein Vertrag oder mehrere Verträge (Kundenmappe)“.

Alle Daten sind synthetisch. Die Rückfrage (``bundle.choice.ask_single_or_bundle``) und alle
modalen Dialoge werden ersetzt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from uic_helpers import (
    AMENDMENT_PDF,
    POLICY_PDF,
    DialogRecorder,
    flush_tasks,
    make_policy_pdf,
    make_service,
)

from insurance_app.ui.bundle import choice
from insurance_app.ui.bundle.common import TITLE_LABEL
from insurance_app.ui.pages.dashboard import DashboardPage

pytestmark = pytest.mark.ui

TITLE = "Kundin Müller – Optimierung (MUSTER)"


@pytest.fixture()
def svc(tmp_path: Path):
    service = make_service(tmp_path)
    yield service
    flush_tasks()
    service.close()


@pytest.fixture()
def dlg(monkeypatch: pytest.MonkeyPatch) -> DialogRecorder:
    return DialogRecorder(monkeypatch)


class ChoiceStub:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.answer: str | None = choice.CHOICE_SINGLE
        self.calls: list[int] = []
        monkeypatch.setattr(choice, "ask_single_or_bundle", self._ask)

    def _ask(self, parent, file_count):
        self.calls.append(file_count)
        return self.answer


@pytest.fixture()
def ask(monkeypatch: pytest.MonkeyPatch) -> ChoiceStub:
    return ChoiceStub(monkeypatch)


@pytest.fixture()
def third_pdf(tmp_path: Path) -> Path:
    return make_policy_pdf(tmp_path / "MUSTER_Police_Sachversicherung.pdf")


def make_page(qtbot, svc, opened=None, bundles=None) -> DashboardPage:
    page = DashboardPage(
        svc,
        open_case=(opened.append if opened is not None else (lambda _cid: None)),
        open_bundle=(
            (lambda bid, paths: bundles.append((bid, list(paths)))) if bundles is not None else None
        ),
    )
    qtbot.addWidget(page)
    return page


# ---------------------------------------------------------------------------


def test_choice_dialog_texts_and_buttons(qtbot, monkeypatch):
    """Die echte Rückfrage (QMessageBox) mit beiden Antworten – ohne modales Warten."""
    seen: list[QMessageBox] = []

    def fake_exec(box: QMessageBox) -> int:
        seen.append(box)
        target = next(b for b in box.buttons() if b.text() == choice.BUNDLE_TEXT)
        target.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert choice.ask_single_or_bundle(None, 3) == choice.CHOICE_BUNDLE
    box = seen[0]
    assert box.text() == choice.QUESTION
    assert "EINEM Vertrag" in box.text() and "MEHREREN" in box.text()
    assert {b.text() for b in box.buttons()} >= {choice.SINGLE_TEXT, choice.BUNDLE_TEXT, "Abbrechen"}


def test_single_file_does_not_ask(qtbot, svc, dlg, ask):
    opened: list[str] = []
    page = make_page(qtbot, svc, opened, bundles=[])
    case_id = page.import_files([POLICY_PDF])
    assert case_id is not None and opened == [case_id]
    assert ask.calls == []
    qtbot.waitUntil(lambda: not page._busy, timeout=30_000)


def test_multiple_files_one_contract_keeps_single_case(qtbot, svc, dlg, ask):
    opened: list[str] = []
    bundles: list[tuple[str, list[Path]]] = []
    page = make_page(qtbot, svc, opened, bundles)
    ask.answer = choice.CHOICE_SINGLE
    case_id = page.import_files([POLICY_PDF, AMENDMENT_PDF])
    assert ask.calls == [2]
    assert case_id is not None and opened == [case_id]
    assert bundles == [] and svc.list_bundles() == []
    assert len(svc.load_case(case_id).documents) == 2
    qtbot.waitUntil(lambda: not page._busy, timeout=30_000)


def test_multiple_files_several_contracts_creates_bundle_and_navigates(qtbot, svc, dlg, ask, third_pdf):
    opened: list[str] = []
    bundles: list[tuple[str, list[Path]]] = []
    page = make_page(qtbot, svc, opened, bundles)
    ask.answer = choice.CHOICE_BUNDLE
    dlg.text_answers = [TITLE]
    files = [POLICY_PDF, AMENDMENT_PDF, third_pdf]
    assert page.import_files(files) is None
    assert ask.calls == [3]
    assert dlg.texts[0][1] == TITLE_LABEL
    created = svc.list_bundles()
    assert [b.title for b in created] == [TITLE]
    assert bundles == [(created[0].id, files)]  # Mappenseite übernimmt die Warteschlange
    assert opened == [] and svc.list_cases() == []  # kein Einzelvorgang angelegt


def test_choice_or_title_cancel_creates_nothing(qtbot, svc, dlg, ask):
    bundles: list[tuple[str, list[Path]]] = []
    page = make_page(qtbot, svc, bundles=bundles)
    ask.answer = None
    assert page.import_files([POLICY_PDF, AMENDMENT_PDF]) is None
    ask.answer = choice.CHOICE_BUNDLE
    dlg.text_answers = [None]  # Titelabfrage abgebrochen
    assert page.import_files([POLICY_PDF, AMENDMENT_PDF]) is None
    assert svc.list_cases() == [] and svc.list_bundles() == [] and bundles == []


def test_bundle_button_creates_empty_bundle(qtbot, svc, dlg, ask):
    bundles: list[tuple[str, list[Path]]] = []
    page = make_page(qtbot, svc, bundles=bundles)
    assert page.bundle_btn.text() == "Kundenmappe anlegen (mehrere Verträge)"
    assert page.bundle_btn.isVisibleTo(page) and page.bundle_btn.isEnabled()
    dlg.text_answers = [TITLE]
    page.bundle_btn.click()
    created = svc.list_bundles()
    assert len(created) == 1 and bundles == [(created[0].id, [])]
    assert ask.calls == []


def test_standalone_dashboard_reads_bundle_queue_itself(qtbot, svc, dlg, ask, third_pdf):
    page = make_page(qtbot, svc)  # ohne Mappenseite (open_bundle=None)
    ask.answer = choice.CHOICE_BUNDLE
    dlg.text_answers = [TITLE]
    page.import_files([POLICY_PDF, AMENDMENT_PDF, third_pdf])
    qtbot.waitUntil(lambda: not page._busy, timeout=60_000)
    flush_tasks()
    bundle = svc.list_bundles()[0]
    assert bundle.case_count == 2
    assert dlg.infos and dlg.infos[-1][0] == "Kundenmappe eingelesen"
    assert "3 von 3 Dateien fertig" in dlg.infos[-1][1]
    assert page.table.rowCount() == 2  # beide Vorgänge erscheinen in der Vorgangsliste


def test_main_window_multi_file_drop_shows_bundle_queue(qtbot, svc, dlg, ask, third_pdf):
    from insurance_app.ui.main_window import MainWindow

    win = MainWindow(svc)
    qtbot.addWidget(win)
    assert list(win._nav_buttons)[:3] == ["dashboard", "letter", "bundles"]
    assert win._nav_buttons["bundles"].text() == "👥  Kundenmappen"
    assert win._nav_buttons["catalog"].text() == "📄  Vorlagen && Formulare"  # „&“ wörtlich
    dashboard, bundles_page = win.pages["dashboard"], win.pages["bundles"]
    ask.answer = choice.CHOICE_BUNDLE
    dlg.text_answers = [TITLE]
    dashboard.import_files([POLICY_PDF, AMENDMENT_PDF, third_pdf])
    assert win.stack.currentWidget() is bundles_page
    assert win._nav_buttons["bundles"].isChecked()
    assert bundles_page.busy and bundles_page.queue_table.rowCount() == 3
    qtbot.waitUntil(lambda: not bundles_page.busy, timeout=60_000)
    flush_tasks()
    assert bundles_page.selected_bundle().title == TITLE
    assert bundles_page.queue_table.row_texts(1) == ["✔ Fertig"] * 3
    assert bundles_page.contracts_table.rowCount() == 2

    # „Prüfen & bearbeiten“ öffnet den Vorgang in der Vorgangsansicht
    cid = bundles_page.overview[0].case_id
    bundles_page.contracts_table.open_buttons[cid].click()
    assert win.stack.currentWidget() is win.case_view and win.current_case_id() == cid

    # Zurück zur Mappe über das Hauptfenster
    win.open_bundle(bundles_page.selected_bundle_id())
    assert win.stack.currentWidget() is bundles_page
    assert not dlg.errors, dlg.error_text()
