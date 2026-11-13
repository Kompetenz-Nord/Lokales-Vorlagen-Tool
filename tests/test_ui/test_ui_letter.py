"""Seite „Brief schreiben“: Dokument rein, Brief automatisch befüllt, ein Klick zum PDF.

Alle Daten synthetisch („MUSTER – KEINE ECHTDATEN“).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from uic_helpers import DialogRecorder, apply_app_style, dispose, make_service, save_grab

from insurance_app.samples.kfz import KFZ_TRUTH, generate_kfz_sample
from insurance_app.ui.pages.letter import AUTO_TOOLTIP, READY_TEXT, SIGNATURE_HINT, LetterPage


@pytest.fixture()
def dlg(monkeypatch: pytest.MonkeyPatch) -> DialogRecorder:
    return DialogRecorder(monkeypatch)


@pytest.fixture()
def page(qtbot, tmp_path: Path, dlg):
    apply_app_style()
    svc = make_service(tmp_path)
    widget = LetterPage(svc)
    qtbot.addWidget(widget)
    widget.resize(1100, 800)
    widget.show()
    yield widget
    svc.close()


def load_sample(qtbot, page: LetterPage) -> None:
    page.load_sample()
    qtbot.waitUntil(lambda: not page.busy, timeout=30_000)


def refs(page: LetterPage) -> dict[str, str]:
    return {r.label_edit.text(): r.value_edit.text() for r in page.rows}


def test_empty_form_shows_only_the_letter_fields(page: LetterPage):
    """Wie im Briefkopf: Absender, Empfänger, Bezug, Betreff, Datum, Text, Name – sonst nichts."""
    assert page.type_combo.currentText() == "Kündigung Kfz-Versicherung"
    assert list(refs(page)) == ["Kennzeichen", "Versicherungsnummer"]
    assert page.subject.text() == "Kündigung" and page.date.text() == "13.11.2026"
    assert "fristgerecht, ersatzweise zum nächstmöglichen Zeitpunkt" in page.body.toPlainText()
    assert page.sender_name.text() == "" and page.recipient.toPlainText() == ""
    assert page.problem_label.text().startswith("Noch offen:")
    assert page.signature_hint.text() == SIGNATURE_HINT
    assert not page.status_label.isVisible()


def test_document_fills_the_form_and_one_click_creates_the_pdf(qtbot, page: LetterPage, dlg):
    load_sample(qtbot, page)
    assert page.sender_name.text() == KFZ_TRUTH["sender_name"]
    assert page.sender_street.text() == KFZ_TRUTH["sender_street"]
    assert page.sender_postal_city.text() == KFZ_TRUTH["sender_postal_city"]
    assert page.recipient.toPlainText().splitlines() == KFZ_TRUTH["recipient"]
    assert refs(page) == {
        "Kennzeichen": KFZ_TRUTH["Kennzeichen"],
        "Versicherungsnummer": KFZ_TRUTH["Versicherungsnummer"],
    }
    assert page.signer.text() == KFZ_TRUTH["sender_name"]
    # Kennzeichnung: Text + Hinterlegung, nicht nur Farbe
    status = page.status_label.text()
    assert "Automatisch ausgefüllt (6)" in status
    for name in ("Ihr Name", "Straße", "PLZ und Ort", "Empfänger", "Kennzeichen", "Versicherungsnummer"):
        assert name in status
    assert page.sender_name.property("autoFilled") is True and page.sender_name.toolTip() == AUTO_TOOLTIP
    assert page.subject.property("autoFilled") in (False, None)
    assert page.problem_label.text() == READY_TEXT
    assert not page.preview.pixmap().isNull()

    page.generate_btn.click()
    assert not dlg.errors, dlg.error_text()
    assert page.last_export is not None and page.last_export.is_file()
    assert "PDF erzeugt" in page.result_label.text() and "von Hand unterschreiben" in page.result_label.text()
    text = pymupdf.open(page.last_export)[0].get_text()
    assert KFZ_TRUTH["Kennzeichen"] in text and KFZ_TRUTH["Versicherungsnummer"] in text
    assert page.open_btn.isEnabled()
    page.open_btn.click()
    assert dlg.opened == [page.last_export.parent]


def test_editing_removes_the_automatic_mark(qtbot, page: LetterPage):
    load_sample(qtbot, page)
    qtbot.keyClicks(page.sender_street, "a")
    assert page.sender_street.property("autoFilled") is False and page.sender_street.toolTip() == ""
    assert page.sender_name.property("autoFilled") is True  # andere Felder bleiben gekennzeichnet


def test_incomplete_letter_is_not_created_and_says_why(qtbot, page: LetterPage, dlg):
    page.sender_name.setText("Erika Musterfrau")
    page.generate_btn.click()
    assert dlg.errors and "noch nicht vollständig" in dlg.errors[-1][0]
    assert "Straße und Hausnummer" in dlg.errors[-1][1]
    assert page.last_export is None
    assert not list(page.service.export_dir.glob("*.pdf"))


def test_reference_rows_can_be_added_and_removed(qtbot, page: LetterPage):
    page.add_ref_btn.click()
    assert len(page.rows) == 3
    page.rows[-1].label_edit.setText("Kundennummer")
    page.rows[-1].value_edit.setText("K-0001")
    assert page.letter().filled_references()[-1].label == "Kundennummer"
    page.rows[0].remove_btn.click()
    assert list(refs(page)) == ["Versicherungsnummer", "Kundennummer"]


def test_letter_type_change_keeps_data_and_asks_before_replacing_own_text(qtbot, page: LetterPage, dlg):
    load_sample(qtbot, page)
    page.type_combo.setCurrentIndex(page.type_combo.findData("widerruf"))
    assert page.subject.text() == "Widerruf" and "widerrufe" in page.body.toPlainText()
    assert page.sender_name.text() == KFZ_TRUTH["sender_name"]  # Daten bleiben erhalten
    assert not dlg.confirms  # Standardtext war unverändert -> keine Rückfrage
    page.body.setPlainText("Sehr geehrte Damen und Herren,\n\neigener Text.\n\nMit freundlichen Grüßen")
    dlg.confirm_answers = [False]
    page.type_combo.setCurrentIndex(page.type_combo.findData("kuendigung"))
    assert dlg.confirms and "eigener Text." in page.body.toPlainText()
    assert page.type_combo.currentData() == "widerruf"  # Abbruch: Briefart bleibt


def test_special_termination_blocks_until_reason_is_entered(qtbot, page: LetterPage, dlg):
    load_sample(qtbot, page)
    page.type_combo.setCurrentIndex(page.type_combo.findData("sonderkuendigung"))
    assert "Platzhalter" in page.problem_label.text()
    assert "Sonderkündigungsrecht" in page.hint_label.text()
    body = page.body.toPlainText()
    page.body.setPlainText(body[: body.index("[")] + "Beitragserhöhung." + body[body.index("]") + 1 :])
    page.refresh_preview()
    assert page.problem_label.text() == READY_TEXT


def test_used_recipient_is_offered_again(qtbot, page: LetterPage, dlg):
    assert not page.book_combo.isVisible()
    load_sample(qtbot, page)
    page.generate_btn.click()
    page.new_letter()
    assert page.recipient.toPlainText() == "" and page.book_combo.isVisible()
    page.book_combo.activated.emit(1)
    assert page.recipient.toPlainText().splitlines() == KFZ_TRUTH["recipient"]


def test_main_window_has_letter_page_and_reopens_letter_cases(qtbot, tmp_path: Path, dlg):
    from insurance_app.ui.main_window import MainWindow

    apply_app_style()
    svc = make_service(tmp_path)
    win = MainWindow(svc)
    try:
        win.show()
        qtbot.waitExposed(win)
        assert [k for k, _ in win.NAV][:2] == ["dashboard", "letter"]
        win.pages["dashboard"].letter_btn.click()
        page = win.pages["letter"]
        assert win.stack.currentWidget() is page
        load_sample(qtbot, page)
        qtbot.wait(50)
        # nichts überlappt, nichts ist abgeschnitten
        assert page.preview_scroll.horizontalScrollBar().maximum() == 0
        assert (
            page.book_combo.isHidden()
            or page.book_combo.geometry().top() >= page.recipient.geometry().bottom()
        )
        label = page.rows[1].label_edit
        assert label.fontMetrics().horizontalAdvance(label.text()) < label.width() - 12
        for row in page.rows:  # auch die Werte sind vollständig sichtbar
            value = row.value_edit
            assert value.fontMetrics().horizontalAdvance(value.text()) < value.width() - 12
        save_grab(win, "ui_letter.png")
        page.generate_btn.click()
        case_id = page.case.id
        page.new_letter()
        win.show_section("dashboard")
        win.open_case(case_id)  # aus der Vorgangsliste: öffnet wieder das Briefformular
        assert win.stack.currentWidget() is page
        assert page.sender_name.text() == KFZ_TRUTH["sender_name"]
    finally:
        dispose(win)
        svc.close()


def test_sample_document_is_marked_as_synthetic(tmp_path: Path):
    text = pymupdf.open(generate_kfz_sample(tmp_path))[0].get_text()
    assert "MUSTER – KEINE ECHTDATEN" in text
