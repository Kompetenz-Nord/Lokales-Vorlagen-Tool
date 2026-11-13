"""Vorgangsansicht: Schaltfläche „Lokale KI-Zweitprüfung“.

* aktiv genau dann, wenn ``service.ai_models_status()["ready"]``; Tooltip = Statusmeldung
* ausgeschaltete KI: der lokale Ollama-Dienst wird gar nicht erst gefragt
* Lauf im Hintergrund (Fortschritt + Abbruch), danach „Verwendete Modelle: …“, Hinweise und
  neu geladene Datenprüfung

Ollama wird nicht angesprochen (Service-Methoden ersetzt). Alle Daten synthetisch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from uic_helpers import DialogRecorder, flush_tasks, free_port_url, make_service

from insurance_app.core.service import ServiceError
from insurance_app.ui.case.documents_panel import local_ai_result_text
from insurance_app.ui.pages.case_view import TAB_DOCUMENTS, TAB_REVIEW, CaseView

pytestmark = pytest.mark.ui


@pytest.fixture()
def svc(tmp_path: Path):
    service = make_service(tmp_path, ollama_url=free_port_url())
    yield service
    flush_tasks()
    service.close()


@pytest.fixture()
def dlg(monkeypatch: pytest.MonkeyPatch) -> DialogRecorder:
    return DialogRecorder(monkeypatch)


@pytest.fixture()
def sample(svc):
    return svc.create_sample_case()


def status(ready: bool, message: str) -> dict:
    return {
        "roles": {"active": "glm-ocr", "fallback": "qwen3-vl:4b", "crosscheck": None},
        "models": {"glm-ocr": ready, "qwen3-vl:4b": False},
        "running": True,
        "will_run": "glm-ocr" if ready else None,
        "ready": ready,
        "message": message,
    }


def open_view(qtbot, svc, case_id: str) -> CaseView:
    view = CaseView(svc, show_catalog=lambda: None)
    qtbot.addWidget(view)
    view.resize(1360, 860)
    view.show()
    view.open_case(case_id)
    view.show_tab(TAB_DOCUMENTS)
    return view


# ---------------------------------------------------------------------------


def test_disabled_ai_does_not_contact_ollama(qtbot, svc, sample, dlg, monkeypatch):
    def forbidden():
        raise AssertionError("ai_models_status darf bei ausgeschalteter KI nicht aufgerufen werden")

    monkeypatch.setattr(svc, "ai_models_status", forbidden)
    view = open_view(qtbot, svc, sample.id)
    btn = view.documents.btn_ai
    assert not btn.isEnabled() and "ausgeschaltet" in btn.toolTip()


def test_button_follows_models_status(qtbot, svc, sample, dlg, monkeypatch):
    svc.update_settings(ai_enabled=True)
    current = {"value": status(False, "Weder das aktive noch das Ausweichmodell ist heruntergeladen.")}
    monkeypatch.setattr(svc, "ai_models_status", lambda: dict(current["value"]))
    view = open_view(qtbot, svc, sample.id)
    btn = view.documents.btn_ai
    assert not btn.isEnabled()
    assert btn.toolTip() == "Weder das aktive noch das Ausweichmodell ist heruntergeladen."

    current["value"] = status(True, "Bereit: glm-ocr ist aktiv.")
    view.refresh()
    assert btn.isEnabled()
    assert btn.toolTip().startswith("Bereit: glm-ocr ist aktiv.")
    assert "Konflikt" in btn.toolTip()


def test_run_shows_models_errors_and_reloads_review(qtbot, svc, sample, dlg, monkeypatch):
    svc.update_settings(ai_enabled=True)
    monkeypatch.setattr(svc, "ai_models_status", lambda: status(True, "Bereit: glm-ocr ist aktiv."))
    calls: list[tuple[str, bool, bool]] = []

    def fake_run(case, *, progress=None, cancel=None):
        calls.append((case.id, progress is not None, cancel is not None))
        progress(0, 1, "Lokale KI (glm-ocr) prüft Dokument 1/1")
        case.title = "MUSTER – nach KI-Zweitprüfung"  # Änderung wird gespeichert und neu geladen
        svc.save_case(case)
        return {
            "ok": False,
            "errors": ["glm-ocr, Seite 2: Zeitüberschreitung"],
            "candidates": 3,
            "models_used": ["glm-ocr", "qwen3-vl:4b"],
        }

    monkeypatch.setattr(svc, "run_local_ai", fake_run)
    view = open_view(qtbot, svc, sample.id)
    assert view.documents.btn_ai.isEnabled()
    view.documents.btn_ai.click()
    assert view.controller.busy  # läuft im Hintergrund
    qtbot.waitUntil(lambda: not view.controller.busy and bool(dlg.infos), timeout=30_000)
    assert calls == [(sample.id, True, True)]  # Fortschritt und Abbruch werden übergeben
    title, text = dlg.infos[-1]
    assert title == "Lokale KI-Zweitprüfung"
    assert text.startswith("Verwendete Modelle: glm-ocr, qwen3-vl:4b")
    assert "3 Vorschläge" in text and "Konflikt" in text
    assert "• glm-ocr, Seite 2: Zeitüberschreitung" in text
    assert view.tabs.currentIndex() == TAB_REVIEW
    assert view.controller.case.title == "MUSTER – nach KI-Zweitprüfung"  # Vorgang neu geladen
    assert view.title_label.text() == "MUSTER – nach KI-Zweitprüfung"
    assert not dlg.errors, dlg.error_text()


def test_run_error_is_shown(qtbot, svc, sample, dlg, monkeypatch):
    svc.update_settings(ai_enabled=True)
    monkeypatch.setattr(svc, "ai_models_status", lambda: status(True, "Bereit: glm-ocr ist aktiv."))

    def failing(case, *, progress=None, cancel=None):
        raise ServiceError("Der lokale Ollama-Dienst läuft nicht.")

    monkeypatch.setattr(svc, "run_local_ai", failing)
    view = open_view(qtbot, svc, sample.id)
    view.documents.btn_ai.click()
    qtbot.waitUntil(lambda: not view.controller.busy and bool(dlg.errors), timeout=30_000)
    assert dlg.errors[-1] == ("Lokale KI-Zweitprüfung nicht möglich", "Der lokale Ollama-Dienst läuft nicht.")


def test_result_text_without_models_or_errors():
    text = local_ai_result_text({"ok": True, "errors": [], "candidates": 0, "models_used": []})
    assert text.startswith("Verwendete Modelle: –")
    assert "Hinweise" not in text
    many = local_ai_result_text({"errors": [f"Fehler {i}" for i in range(7)], "models_used": ["glm-ocr"]})
    assert "… und 2 weitere" in many
