"""Einstellungen – Bereich „Lokale KI (optional)“.

Status aus ``ai_models_status()``, Hardware + Empfehlung, Profile, eigene Modellwahl
(aktiv / Ausweichmodell / Gegenprüfung), Modelltabelle mit Download (nur nach Zustimmung)
und Entfernen (nur nach Rückfrage). Ollama wird ausschließlich simuliert (127.0.0.1,
ephemerer Port) – ein echter Ollama-Dienst auf diesem Rechner wird nie angesprochen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel
from uic_helpers import (
    DialogRecorder,
    apply_app_style,
    dispose,
    fake_ollama,
    fixed_hardware,
    flush_tasks,
    free_port_url,
    make_service,
    save_grab,
)

from insurance_app.core.service import SettingsError
from insurance_app.core.settings import Settings
from insurance_app.local_ai.profiles import PROFILES
from insurance_app.local_ai.status import MODEL_CATALOG
from insurance_app.ui.pages.settings import SettingsPage
from insurance_app.ui.studio import consent_dialog
from insurance_app.ui.studio.model_panel import (
    CANCELLED_TEXT,
    INSTALLED_TEXT,
    MISSING_TEXT,
    MODEL_COLUMNS,
    NO_FALLBACK_TEXT,
    LocalAiPanel,
)

pytestmark = pytest.mark.ui


@pytest.fixture(autouse=True)
def _wait_tasks():
    yield
    flush_tasks()


@pytest.fixture()
def dlg(monkeypatch: pytest.MonkeyPatch) -> DialogRecorder:
    return DialogRecorder(monkeypatch)


def make_svc(tmp_path: Path, monkeypatch, hw=None, **settings):
    svc = make_service(tmp_path, **settings)
    monkeypatch.setattr(svc, "hardware_info", lambda: hw or fixed_hardware())
    return svc


def make_page(qtbot, svc) -> SettingsPage:
    page = SettingsPage(svc)
    qtbot.addWidget(page)
    page.resize(1360, 860)
    return page


def wait_status(qtbot, panel: LocalAiPanel) -> None:
    panel.check_status()
    qtbot.waitUntil(lambda: not panel.checking and panel.status is not None, timeout=15_000)


def card_text(panel: LocalAiPanel, key: str) -> str:
    frame = panel.profile_buttons[key].parentWidget()
    return "\n".join(lbl.text() for lbl in frame.findChildren(QLabel))


def installed_column(panel: LocalAiPanel) -> dict[str, str]:
    table = panel.model_table
    return {name: table.item(row, 5).text() for row, name in enumerate(MODEL_CATALOG)}


# ---------------------------------------------------------------------------


def test_hardware_box_shows_recommendation(qtbot, tmp_path, monkeypatch, dlg):
    svc = make_svc(tmp_path, monkeypatch, ollama_url=free_port_url())
    panel = make_page(qtbot, svc).ai_panel
    assert panel.status is None  # ohne Anzeigen keine Statusprüfung
    assert panel.hw_labels["ram"].text() == "16,0 GB gesamt, 8,0 GB frei"
    assert panel.hw_labels["disk"].text() == "120,0 GB frei (Modellordner)"
    assert panel.hw_labels["cpu"].text() == "8 Kerne · MUSTER-OS x86_64"
    text = panel.recommendation_label.text()
    assert text.startswith("Empfehlung für dieses Gerät: Standard – GLM-OCR mit Qwen3-VL")
    assert "16 GB Arbeitsspeicher erkannt" in text
    rec_badges = {k: rec.isVisibleTo(panel) for k, (rec, _cur) in panel.profile_badges.items()}
    assert rec_badges == {"kompakt": False, "standard": True, "maximal": False}

    monkeypatch.setattr(svc, "hardware_info", lambda: fixed_hardware(ram=8.0))
    panel.update_hardware()
    assert panel.recommendation_label.text().startswith("Empfehlung für dieses Gerät: Kompakt")
    monkeypatch.setattr(svc, "hardware_info", lambda: fixed_hardware(ram=4.0))
    panel.update_hardware()
    assert "ohne lokale KI" in panel.recommendation_label.text()
    assert not any(rec.isVisibleTo(panel) for rec, _cur in panel.profile_badges.values())


def test_profile_cards_show_models_download_and_requirements(qtbot, tmp_path, monkeypatch, dlg):
    svc = make_svc(tmp_path, monkeypatch, ollama_url=free_port_url())
    panel = make_page(qtbot, svc).ai_panel
    assert set(panel.profile_buttons) == {"kompakt", "standard", "maximal"}
    for key, prof in PROFILES.items():
        text = card_text(panel, key)
        assert prof.label in text and prof.description in text
        for model in prof.models:
            assert model in text
        size = f"{prof.approx_download_gb:.1f}".replace(".", ",")
        assert f"Download gesamt: etwa {size} GB" in text
        assert f"macOS: {prof.requirement.mac}" in text
        assert f"Windows: {prof.requirement.windows}" in text
    assert "Download gesamt: etwa 6,0 GB" in card_text(panel, "standard")
    _rec, current = panel.profile_badges["standard"]
    assert current.isVisibleTo(panel)  # Standardwerte entsprechen dem Profil „Standard“


def test_profile_button_changes_settings(qtbot, tmp_path, monkeypatch, dlg):
    svc = make_svc(tmp_path, monkeypatch, ollama_url=free_port_url())
    panel = make_page(qtbot, svc).ai_panel
    panel.profile_buttons["maximal"].click()
    s = Settings.load(svc.paths.settings_file)
    assert (s.ai_profile, s.ai_model, s.ai_fallback_model, s.ai_crosscheck_model) == (
        "maximal",
        "glm-ocr",
        "qwen3-vl:4b-instruct",
        "gemma4:e4b",
    )
    assert panel.crosscheck_combo.currentData() == "gemma4:e4b"
    assert "Profil „Maximal“ übernommen" in panel.models_feedback.text()
    assert panel.profile_badges["maximal"][1].isVisibleTo(panel)
    assert not panel.profile_badges["standard"][1].isVisibleTo(panel)
    qtbot.waitUntil(lambda: not panel.checking, timeout=15_000)

    panel.profile_buttons["kompakt"].click()
    assert svc.settings.ai_fallback_model is None and svc.settings.ai_crosscheck_model is None
    assert panel.fallback_combo.currentText() == NO_FALLBACK_TEXT
    assert not dlg.errors, dlg.error_text()


def test_model_choice_persists_via_set_ai_models(qtbot, tmp_path, monkeypatch, dlg):
    svc = make_svc(tmp_path, monkeypatch, ollama_url=free_port_url())
    calls: list[tuple] = []
    original = svc.set_ai_models

    def spy(active, fallback=None, crosscheck=None):
        calls.append((active, fallback, crosscheck))
        return original(active, fallback, crosscheck)

    monkeypatch.setattr(svc, "set_ai_models", spy)
    panel = make_page(qtbot, svc).ai_panel
    # Vorgabe: glm-ocr aktiv, qwen3-vl:4b als Ausweichmodell, keine Gegenprüfung
    assert panel.active_combo.currentData() == "glm-ocr"
    assert panel.fallback_combo.currentData() == "qwen3-vl:4b-instruct"
    assert panel.crosscheck_combo.currentData() is None
    assert [panel.active_combo.itemData(i) for i in range(panel.active_combo.count())] == list(MODEL_CATALOG)

    # Der Anwender entscheidet: qwen3-vl:4b-instruct aktiv -> Ausweichmodell darf nicht dasselbe sein
    panel.active_combo.setCurrentIndex(panel.active_combo.findData("qwen3-vl:4b-instruct"))
    assert calls[-1] == ("qwen3-vl:4b-instruct", "qwen3-vl:4b-instruct", None)
    assert svc.settings.ai_model == "qwen3-vl:4b-instruct" and svc.settings.ai_fallback_model is None
    assert panel.fallback_combo.currentText() == NO_FALLBACK_TEXT
    assert "keines" in panel.models_feedback.text()
    qtbot.waitUntil(lambda: not panel.checking, timeout=15_000)

    panel.fallback_combo.setCurrentIndex(panel.fallback_combo.findData("glm-ocr"))
    panel.crosscheck_combo.setCurrentIndex(panel.crosscheck_combo.findData("gemma4:e4b"))
    assert calls[-1] == ("qwen3-vl:4b-instruct", "glm-ocr", "gemma4:e4b")
    s = Settings.load(svc.paths.settings_file)
    assert (s.ai_model, s.ai_fallback_model, s.ai_crosscheck_model) == (
        "qwen3-vl:4b-instruct",
        "glm-ocr",
        "gemma4:e4b",
    )
    assert panel.models_feedback.text().startswith("✔ Gespeichert")
    qtbot.waitUntil(lambda: not panel.checking, timeout=15_000)

    # Neue Seite zeigt die gespeicherte Wahl
    panel2 = make_page(qtbot, svc).ai_panel
    assert panel2.active_combo.currentData() == "qwen3-vl:4b-instruct"
    assert panel2.fallback_combo.currentData() == "glm-ocr"
    assert panel2.crosscheck_combo.currentData() == "gemma4:e4b"
    assert not dlg.errors, dlg.error_text()


def test_status_message_and_installed_column_with_fake_ollama(qtbot, tmp_path, monkeypatch, dlg):
    lai = fake_ollama()
    with lai.running_fake() as fake:
        svc = make_svc(tmp_path, monkeypatch, ollama_url=fake.url, ai_enabled=True)
        svc.set_ai_models("glm-ocr", "qwen3-vl:4b")  # Anwender wählt das im Fake vorhandene Modell
        page = make_page(qtbot, svc)
        panel = page.ai_panel
        page.refresh()
        qtbot.waitUntil(lambda: not panel.checking and panel.status is not None, timeout=15_000)
        expected = svc.ai_models_status()["message"]
        assert expected == "glm-ocr fehlt – das Ausweichmodell qwen3-vl:4b wird verwendet."
        assert panel.state_label.text() == f"◐ {expected}"  # Symbol + Text
        detail = panel.detail_label.text()
        assert "Ollama: ✔ läuft" in detail and "glm-ocr (○ fehlt)" in detail
        assert "qwen3-vl:4b (✔ vorhanden)" in detail
        installed = installed_column(panel)
        assert installed["glm-ocr"] == MISSING_TEXT and installed["qwen3-vl:4b"] == INSTALLED_TEXT
        assert installed["gemma4:e4b"] == MISSING_TEXT
        assert panel.pull_buttons["glm-ocr"].isEnabled() and panel.pull_buttons["glm-ocr"].isVisibleTo(panel)
        assert panel.remove_buttons["qwen3-vl:4b"].isVisibleTo(panel)
        assert not panel.pull_buttons["qwen3-vl:4b"].isVisibleTo(panel)
        headers = [panel.model_table.horizontalHeaderItem(c).text() for c in range(len(MODEL_COLUMNS))]
        assert headers == list(MODEL_COLUMNS)
        row = list(MODEL_CATALOG).index("glm-ocr")
        assert panel.model_table.item(row, 0).text() == "glm-ocr\n(aktiv)"
        assert "laut Ollama-Registry" in panel.model_table.item(row, 3).text()
        assert MODEL_CATALOG["glm-ocr"].role == panel.model_table.item(row, 1).text()
        assert MODEL_CATALOG["glm-ocr"].advantages == panel.model_table.item(row, 2).text()
        unverified = list(MODEL_CATALOG).index("qwen3-vl:8b")
        assert "ungeprüft" in panel.model_table.item(unverified, 3).text()
        # nur Loopback-Anfragen an den simulierten Dienst, kein Download ohne Klick
        assert "/api/pull" not in fake.state.paths()
        assert "/api/tags" in fake.state.paths()

        # Aktives Modell auf das vorhandene umstellen -> „Bereit“
        panel.active_combo.setCurrentIndex(panel.active_combo.findData("qwen3-vl:4b"))
        qtbot.waitUntil(lambda: not panel.checking, timeout=15_000)
        assert panel.state_label.text() == "✔ Bereit: qwen3-vl:4b ist aktiv."
    assert not dlg.errors, dlg.error_text()


def test_status_without_running_ollama(qtbot, tmp_path, monkeypatch, dlg):
    svc = make_svc(tmp_path, monkeypatch, ollama_url=free_port_url(), ai_enabled=True)
    panel = make_page(qtbot, svc).ai_panel
    wait_status(qtbot, panel)
    assert panel.state_label.text() == "✖ Der lokale Ollama-Dienst läuft nicht."
    assert set(installed_column(panel).values()) == {"? Ollama läuft nicht"}
    assert not any(b.isEnabled() for b in panel.pull_buttons.values())
    assert "nicht erreichbar" in panel.detail_label.text()


def test_download_requires_consent(qtbot, tmp_path, monkeypatch, dlg):
    lai = fake_ollama()
    with lai.running_fake() as fake:
        svc = make_svc(tmp_path, monkeypatch, ollama_url=fake.url, ai_enabled=True)
        panel = make_page(qtbot, svc).ai_panel
        wait_status(qtbot, panel)
        asked: list[consent_dialog.DownloadInfo] = []
        answer = [False]

        def fake_consent(parent, info):
            asked.append(info)
            return answer[0]

        monkeypatch.setattr(consent_dialog, "ask_download_consent", fake_consent)
        panel.pull_buttons["glm-ocr"].click()
        assert len(asked) == 1
        info = asked[0]
        assert info.model == "glm-ocr" and info.approx_gb == pytest.approx(2.66)
        assert info.free_disk_gb == pytest.approx(120.0) and info.verified
        assert not panel.busy
        assert "/api/pull" not in fake.state.paths()  # ohne Zustimmung kein Download

        answer[0] = True
        panel.pull_buttons["glm-ocr"].click()
        assert panel.busy and "eingerichtet" in panel.state_label.text()
        assert not any(b.isEnabled() for b in panel.profile_buttons.values())
        qtbot.waitUntil(lambda: not panel.busy, timeout=15_000)
        pull_body = next(b for _m, p, b in fake.state.requests if p == "/api/pull")
        assert pull_body["model"] == "glm-ocr"
        assert "abgeschlossen" in dlg.info_text()
        qtbot.waitUntil(lambda: not panel.checking, timeout=15_000)
    assert not dlg.errors, dlg.error_text()


def test_download_blocked_without_disk_space(qtbot, tmp_path, monkeypatch, dlg):
    lai = fake_ollama()
    with lai.running_fake() as fake:
        svc = make_svc(
            tmp_path, monkeypatch, hw=fixed_hardware(disk=1.0), ollama_url=fake.url, ai_enabled=True
        )
        panel = make_page(qtbot, svc).ai_panel
        wait_status(qtbot, panel)
        asked: list = []
        monkeypatch.setattr(consent_dialog, "ask_download_consent", lambda p, i: asked.append(i) or True)
        panel.pull_buttons["gemma4:e4b"].click()
        assert asked == []
        assert dlg.errors and dlg.errors[0][0] == "Zu wenig Speicherplatz"
        assert "/api/pull" not in fake.state.paths()


def test_download_can_be_cancelled(qtbot, tmp_path, monkeypatch, dlg):
    lai = fake_ollama()
    state = lai.FakeState(pull_line_delay=0.3)
    with lai.running_fake(state) as fake:
        svc = make_svc(tmp_path, monkeypatch, ollama_url=fake.url, ai_enabled=True)
        panel = make_page(qtbot, svc).ai_panel
        wait_status(qtbot, panel)
        monkeypatch.setattr(consent_dialog, "ask_download_consent", lambda parent, info: True)
        panel.pull_buttons["gemma4:e4b"].click()
        assert panel.busy
        assert not any(b.isEnabled() for b in panel.pull_buttons.values())
        qtbot.waitUntil(lambda: "/api/pull" in fake.state.paths(), timeout=10_000)
        panel.task.cancel.set()
        qtbot.waitUntil(lambda: not panel.busy, timeout=15_000)
        assert CANCELLED_TEXT in dlg.info_text()
        qtbot.waitUntil(lambda: not panel.checking, timeout=15_000)


def test_remove_requires_confirmation(qtbot, tmp_path, monkeypatch, dlg):
    lai = fake_ollama()
    with lai.running_fake() as fake:
        svc = make_svc(tmp_path, monkeypatch, ollama_url=fake.url, ai_enabled=True)
        panel = make_page(qtbot, svc).ai_panel
        wait_status(qtbot, panel)
        dlg.confirm_answers = [False]
        panel.remove_buttons["qwen3-vl:4b"].click()
        assert dlg.confirms and dlg.confirms[-1]["danger"] is True
        assert "/api/delete" not in fake.state.paths()
        dlg.confirm_answers = [True]
        panel.remove_buttons["qwen3-vl:4b"].click()
        qtbot.waitUntil(lambda: not panel.busy, timeout=15_000)
        assert "/api/delete" in fake.state.paths()
        assert "entfernt" in dlg.info_text()
        qtbot.waitUntil(lambda: not panel.checking, timeout=15_000)
        assert installed_column(panel)["qwen3-vl:4b"] == MISSING_TEXT
        assert panel.state_label.text().startswith("⚠")  # weder aktives noch Ausweichmodell vorhanden


def test_local_only_hint_and_no_cloud_models(qtbot, tmp_path, monkeypatch, dlg):
    svc = make_svc(tmp_path, monkeypatch, ollama_url=free_port_url())
    panel = make_page(qtbot, svc).ai_panel
    hint = panel.cloud_hint.text()
    assert ":cloud" in hint and "nicht unterstützt" in hint
    assert "OLLAMA_NO_CLOUD=1" in hint and '{"disable_ollama_cloud": true}' in hint
    assert "~/.ollama/server.json" in hint
    for combo in (panel.active_combo, panel.fallback_combo, panel.crosscheck_combo):
        names = [combo.itemData(i) for i in range(combo.count())]
        assert not any(n and "cloud" in n for n in names)
    # Eine Cloud-Angabe wird abgelehnt und nicht gespeichert
    with pytest.raises(SettingsError, match="Cloud"):
        svc.set_ai_models("gemma4:cloud")
    assert svc.settings.ai_model == "glm-ocr"


def test_settings_ai_screenshot(qtbot, tmp_path, monkeypatch, dlg):
    """Bildschirmfoto ``ui_settings_ai.png`` (nur wenn ``LVT_SCREENSHOTS`` gesetzt ist)."""
    from insurance_app.ui.main_window import MainWindow

    apply_app_style()
    lai = fake_ollama()
    with lai.running_fake() as fake:
        svc = make_svc(tmp_path, monkeypatch, ollama_url=fake.url, ai_enabled=True)
        svc.set_ai_models("glm-ocr", "qwen3-vl:4b")
        win = MainWindow(svc)
        win.resize(1360, 860)
        try:
            win.show()
            qtbot.waitExposed(win)
            win.show_section("settings")
            page = win.pages["settings"]
            qtbot.waitUntil(
                lambda: not page.ai_panel.checking and page.ai_panel.status is not None, timeout=15_000
            )
            qtbot.wait(50)
            page.show_ai_section()
            qtbot.wait(50)
            assert page.ai_panel.state_label.text().startswith("◐")
            save_grab(win, "ui_settings_ai.png")
        finally:
            dispose(win)
