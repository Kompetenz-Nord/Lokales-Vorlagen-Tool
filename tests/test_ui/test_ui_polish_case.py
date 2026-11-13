"""UI-Feinschliff Vorgang: Passwort nach Neustart erneut abfragen, Pflicht-/optionale Auftragsfelder.

Alle Daten sind synthetisch („MUSTER – KEINE ECHTDATEN“). Modale Dialoge werden ersetzt.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import Qt
from uia_helpers import POLICY_PDF, DialogRecorder, flush_tasks, make_encrypted_copy, make_service

from insurance_app.core.models import FieldStatus
from insurance_app.ui import dialogs
from insurance_app.ui.case.evidence_view import LOCKED_PDF_TEXT, EvidenceView
from insurance_app.ui.case.order_form import (
    BLOCKING_TEXT,
    CONDITIONAL_TEXT,
    OPTIONAL_TEXT,
    REQUIRED_TEXT,
)
from insurance_app.ui.pages.case_view import TAB_REVIEW, CaseView

pytestmark = pytest.mark.ui

PASSWORD = "MUSTER-geheim-1"
SL13065 = "swisslife_sl13065_freigabe"
LETTER = "hdi_beitragsfreistellung"


@pytest.fixture()
def dlg(monkeypatch: pytest.MonkeyPatch) -> DialogRecorder:
    return DialogRecorder(monkeypatch)


@pytest.fixture()
def again(monkeypatch: pytest.MonkeyPatch):
    """Ersetzt die erneute Passwortabfrage; ``answers`` wird der Reihe nach verbraucht."""

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []
            self.answers: list[str | None] = []

        def __call__(self, parent, file_name, wrong=False):
            self.calls.append((file_name, wrong))
            return self.answers.pop(0) if self.answers else None

    rec = Recorder()
    monkeypatch.setattr(dialogs, "ask_password_again", rec)
    return rec


def _view(qtbot, svc) -> CaseView:
    view = CaseView(svc, show_catalog=lambda: None)
    qtbot.addWidget(view)
    view.resize(1360, 860)
    view.show()
    return view


# ---------------------------------------------------------------------------
# 4. Passwort nach Neustart
# ---------------------------------------------------------------------------


def test_text_recognition_after_restart_asks_for_password_again(qtbot, tmp_path, dlg, again):
    encrypted = make_encrypted_copy(POLICY_PDF, tmp_path / "MUSTER_Police_geschuetzt.pdf", PASSWORD)
    svc = make_service(tmp_path)
    case = svc.create_case("MUSTER geschützt")
    assert svc.add_document(case, encrypted, password=PASSWORD).accepted  # noch nicht gelesen
    case_id, doc_id = case.id, case.documents[0].id
    svc.close()

    # „Neustart“: neuer Dienst auf denselben Daten – das Passwort ist absichtlich weg
    svc = make_service(tmp_path)
    try:
        for stored in svc.paths.data.rglob("*"):  # nirgends gespeichert (Datenbank, JSON, Protokolle)
            if stored.is_file():
                assert PASSWORD.encode() not in stored.read_bytes(), stored.name
        assert PASSWORD not in json.dumps(svc.settings.model_dump(), default=str)
        case = svc.load_case(case_id)
        assert [d.id for d in svc.documents_needing_password(case)] == [doc_id]

        view = _view(qtbot, svc)
        view.open_case(case_id)

        # 1) Abbrechen: verständlicher Hinweis statt allgemeinem Fehler, Datei bleibt ungelesen
        view.start_processing()
        flush_tasks()
        assert again.calls == [(encrypted.name, False)]
        assert not dlg.errors
        notes = " ".join(view.documents.notes)
        assert "Passwort fehlt" in notes and "nie gespeichert" in notes and encrypted.name in notes
        assert svc.load_case(case_id).documents[0].text is None

        # 2) falsches, dann richtiges Passwort: Datei wird gelesen
        again.calls.clear()
        again.answers = ["MUSTER-falsch", PASSWORD]
        view.start_processing()
        flush_tasks()
        assert again.calls == [(encrypted.name, False), (encrypted.name, True)]
        assert not dlg.errors
        done = svc.load_case(case_id)
        assert done.documents[0].text is not None
        assert done.contract_fields  # Werte wurden erkannt
        assert svc.documents_needing_password(done) == []

        # 3) in derselben Sitzung keine weitere Abfrage
        again.calls.clear()
        view.start_processing()
        flush_tasks()
        assert again.calls == []
    finally:
        flush_tasks()
        svc.close()


def test_evidence_preview_after_restart_asks_for_password_again(qtbot, tmp_path, dlg, again):
    encrypted = make_encrypted_copy(POLICY_PDF, tmp_path / "MUSTER_Police_geschuetzt.pdf", PASSWORD)
    svc = make_service(tmp_path)
    case = svc.create_case("MUSTER geschützt")
    svc.add_document(case, encrypted, password=PASSWORD)
    svc.process_documents(case)
    case_id = case.id
    svc.close()

    svc = make_service(tmp_path)
    try:
        case = svc.load_case(case_id)
        key = next(
            k
            for k, fv in case.contract_fields.items()
            if fv.status == FieldStatus.EXTRACTED and fv.source_page
        )
        view = EvidenceView(svc)
        qtbot.addWidget(view)
        view.resize(600, 700)
        view.show()

        # Abbrechen: klare Meldung + Schaltfläche, kein allgemeiner Fehlertext
        view.show_field(case, key)
        assert again.calls == [(encrypted.name, False)]
        assert LOCKED_PDF_TEXT in view.quote.text()
        assert "kann nicht angezeigt werden" not in view.quote.text()
        assert view.unlock_btn.isVisibleTo(view)
        assert view.canvas.image is None

        # erneute Auswahl fragt nicht jedes Mal nach
        view.show_field(case, key)
        assert len(again.calls) == 1

        # Schaltfläche: falsch, dann richtig -> Seite wird angezeigt
        again.answers = ["MUSTER-falsch", PASSWORD]
        qtbot.mouseClick(view.unlock_btn, Qt.MouseButton.LeftButton)
        assert again.calls[1:] == [(encrypted.name, False), (encrypted.name, True)]
        assert view.canvas.image is not None
        assert not view.unlock_btn.isVisibleTo(view)
        assert LOCKED_PDF_TEXT not in view.quote.text()
        assert not dlg.errors
    finally:
        svc.close()


def test_unlock_document_checks_password_and_keeps_it_in_memory_only(tmp_path):
    encrypted = make_encrypted_copy(POLICY_PDF, tmp_path / "MUSTER_Police_geschuetzt.pdf", PASSWORD)
    svc = make_service(tmp_path)
    try:
        case = svc.create_case()
        svc.add_document(case, encrypted, password=PASSWORD)
        plain = svc.add_document(case, POLICY_PDF).document
        doc = case.documents[0]
        assert not svc.document_needs_password(case, doc.id)
        svc._passwords.clear()  # wie nach einem Neustart
        assert svc.document_needs_password(case, doc.id)
        assert not svc.document_needs_password(case, plain.id)  # ungeschützte Datei
        assert not svc.unlock_document(case, doc.id, "MUSTER-falsch")
        assert not svc.unlock_document(case, doc.id, "")
        assert svc.document_needs_password(case, doc.id)
        assert svc.unlock_document(case, doc.id, PASSWORD)
        assert not svc.document_needs_password(case, doc.id)
        assert svc.document_page_image(case, doc.id, 1).width > 0
    finally:
        svc.close()


def test_password_again_dialog_text_is_clear(monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QLineEdit

    seen: dict = {}

    def fake_get_text(parent, title, prompt, echo, *args):
        seen.update(title=title, prompt=prompt, echo=echo)
        return "", False

    monkeypatch.setattr(QInputDialog, "getText", fake_get_text)
    assert dialogs.ask_password_again(None, "MUSTER.pdf", wrong=True) is None
    assert seen["title"] == "Passwort erneut erforderlich"
    assert seen["echo"] == QLineEdit.EchoMode.Password
    for needle in (
        "MUSTER.pdf",
        "passwortgeschützt",
        "speichert die App Passwörter nie",
        "Neustart",
        "falsch",
    ):
        assert needle in seen["prompt"], needle


# ---------------------------------------------------------------------------
# 7. Pflicht- und optionale Auftragsfelder
# ---------------------------------------------------------------------------


def _blocking_keys(svc, case) -> set[str]:
    return {i.field_key for i in svc.review_status(case).blocking_items if i.field_key}


def test_order_form_marks_required_and_optional_fields_like_the_checklist(qtbot, tmp_path, dlg):
    svc = make_service(tmp_path)
    try:
        case = svc.create_sample_case()
        svc.select_template(case, LETTER)
        view = _view(qtbot, svc)
        view.open_case(case.id)
        view.show_tab(TAB_REVIEW)
        form = view.review.order_form
        blocking = _blocking_keys(svc, view.controller.case)
        assert blocking & set(form.rows), "Brief braucht Auftragsangaben"

        # alles, was die Checkliste blockierend meldet, ist als Pflichtangabe gekennzeichnet
        for key in blocking & set(form.rows):
            assert form.requirement_text(key) == REQUIRED_TEXT, key
            assert form.is_required(key)
        assert form.requirement_text("order.recipient_company") == REQUIRED_TEXT
        assert BLOCKING_TEXT in form.rows["order.recipient_company"].status.text()
        # optionale Angaben: Symbol + Text, „nicht relevant“ wird angeboten, nichts blockiert
        for key in ("order.sender_email", "order.recipient_department", "order.attachments"):
            row = form.rows[key]
            assert form.requirement_text(key) == OPTIONAL_TEXT == "○ optional", key
            assert not form.is_required(key) and key not in blocking
            assert row.na_btn is not None
            assert BLOCKING_TEXT not in row.status.text()
        # laut Vorlage nötig, aber erst nach einer anderen Entscheidung: keine falsche Sperr-Aussage
        conditional = [
            k
            for k, row in form.rows.items()
            if not row.optional and k not in blocking and view.controller.case.get(k) is None
        ]
        for key in conditional:
            assert form.requirement_text(key) == CONDITIONAL_TEXT, key
        # jede Zeile trägt genau eine Kennzeichnung mit Symbol und Text
        for key, row in form.rows.items():
            assert form.requirement_text(key) in (REQUIRED_TEXT, OPTIONAL_TEXT, CONDITIONAL_TEXT), key
            assert row.requirement.accessibleName()

        # Pflichtangabe eintragen -> blockiert nicht mehr, bleibt aber als Pflicht gekennzeichnet
        svc.edit_field(view.controller.case, "order.recipient_company", "Muster Versicherung AG")
        view.controller.data_changed.emit()
        assert "order.recipient_company" not in _blocking_keys(svc, view.controller.case)
        assert form.requirement_text("order.recipient_company") == REQUIRED_TEXT
        assert "✔ übernommen" in form.rows["order.recipient_company"].status.text()
        assert not dlg.errors
    finally:
        flush_tasks()
        svc.close()


def test_order_form_required_legal_decisions_for_form_template(qtbot, tmp_path, dlg):
    svc = make_service(tmp_path)
    try:
        case = svc.create_sample_case()
        svc.select_template(case, SL13065)
        view = _view(qtbot, svc)
        view.open_case(case.id)
        form = view.review.order_form
        blocking = _blocking_keys(svc, view.controller.case)
        for key in ("order.severance_consent", "order.waive_refund"):
            assert key in blocking
            assert form.requirement_text(key) == REQUIRED_TEXT == "✱ Pflichtangabe"
            assert form.rows[key].na_btn is None  # Pflichtangaben lassen sich nicht „nicht relevant“ setzen
    finally:
        flush_tasks()
        svc.close()
