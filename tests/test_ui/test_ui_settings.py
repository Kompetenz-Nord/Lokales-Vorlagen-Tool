"""Einstellungen & Datenschutz: LOCAL ONLY, Ablage, OCR, Export, Exportprotokoll, Zustimmungsdialog.

Die ausführlichen Tests des Bereichs „Lokale KI“ stehen in ``test_ui_settings_ai.py``.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from uib_helpers import (  # noqa: F401 – Fixture wird durch Import aktiv
    _uib_wait_tasks,
    apply_app_style,
    build_host,
    dispose,
    free_port_url,
    make_service,
    patch_dialogs,
    screenshot_dir,
)

from insurance_app.core.settings import Settings
from insurance_app.export.exporter import ExportLogEntry
from insurance_app.ui.pages.settings import SettingsPage
from insurance_app.ui.studio.consent_dialog import CONSENT_TEXT, ConsentDialog, DownloadInfo
from insurance_app.ui.theme import LOCAL_ONLY_TEXT

pytestmark = pytest.mark.ui


def _page(qtbot, svc) -> SettingsPage:
    page = SettingsPage(svc)
    qtbot.addWidget(page)
    return page


def _all_text(page) -> str:
    return "\n".join(lbl.text() for lbl in page.findChildren(QLabel))


def test_settings_shows_local_only_and_ai_disabled(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path, ollama_url=free_port_url())
    page = _page(qtbot, svc)
    # ohne refresh() keine Statusprüfung (auch nicht gegen 127.0.0.1)
    assert page.ai_panel.status is None
    page.refresh()
    qtbot.waitUntil(lambda: not page.ai_panel.checking and page.ai_panel.status is not None, timeout=15_000)
    text = _all_text(page)
    assert LOCAL_ONLY_TEXT in text
    assert "Keine Cloud, keine Telemetrie" in text
    assert "liegt beim Betreiber" in text
    assert "DSGVO-konform" not in text
    assert "ausgeschaltet" in page.ai_panel.state_label.text()
    assert page.ai_panel.state_label.text().startswith("○")  # Symbol + Text
    assert not page.ai_panel.enable_check.isChecked()
    assert not any(b.isEnabled() for b in page.ai_panel.pull_buttons.values())  # Ollama läuft nicht
    assert "Vorgänge" in page.storage_labels["counts"].text()
    assert page.storage_labels["data_dir"].text() == str(svc.paths.data)
    qtbot.mouseClick(page.open_data_button, Qt.MouseButton.LeftButton)
    assert rec.opened == [svc.paths.data]
    assert not rec.errors


def test_toggling_ai_updates_settings(qtbot, tmp_path, monkeypatch):
    patch_dialogs(monkeypatch)
    svc = make_service(tmp_path, ollama_url=free_port_url())
    page = _page(qtbot, svc)
    page.ai_panel.enable_check.setChecked(True)
    assert svc.settings.ai_enabled is True
    assert Settings.load(svc.paths.settings_file).ai_enabled is True
    qtbot.waitUntil(lambda: not page.ai_panel.checking, timeout=15_000)
    assert page.ai_panel.state_label.text() == "✖ Der lokale Ollama-Dienst läuft nicht."
    combo = page.ai_panel.active_combo
    combo.setCurrentIndex(combo.findData("qwen3-vl:8b"))
    assert svc.settings.ai_model == "qwen3-vl:8b"
    assert Settings.load(svc.paths.settings_file).ai_model == "qwen3-vl:8b"
    qtbot.waitUntil(lambda: not page.ai_panel.checking, timeout=15_000)
    page.ai_panel.enable_check.setChecked(False)
    assert Settings.load(svc.paths.settings_file).ai_enabled is False
    qtbot.waitUntil(lambda: not page.ai_panel.checking, timeout=15_000)


def test_export_dir_change_persists(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _page(qtbot, svc)
    target = tmp_path / "neuer_exportordner"
    rec.directory = target
    qtbot.mouseClick(page.export_dir_button, Qt.MouseButton.LeftButton)
    assert not rec.errors, rec.error_text()
    assert svc.settings.export_dir == str(target)
    assert Settings.load(svc.paths.settings_file).export_dir == str(target)
    assert svc.export_dir == target
    assert page.storage_labels["export_dir"].text() == str(target)
    assert page.export_dir_reset.isEnabled()
    qtbot.mouseClick(page.export_dir_reset, Qt.MouseButton.LeftButton)
    assert svc.settings.export_dir is None
    assert Settings.load(svc.paths.settings_file).export_dir is None


def test_ocr_and_export_options_are_saved(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _page(qtbot, svc)
    status = svc.ocr_status()
    model = page.ocr_backend_combo.model()
    for i in range(page.ocr_backend_combo.count()):
        name = page.ocr_backend_combo.itemData(i)
        expected = status[name][0] or name == svc.settings.ocr_backend
        assert model.item(i).isEnabled() == expected
    for name, label in page.ocr_status_labels.items():
        assert label.text().startswith("✔" if status[name][0] else "○")
    page.ocr_dpi_spin.setValue(400)
    qtbot.mouseClick(page.ocr_apply, Qt.MouseButton.LeftButton)
    assert svc.settings.ocr_dpi == 400
    assert "Gespeichert" in page.ocr_feedback.text()
    assert page.ocr_dpi_spin.minimum() == 150 and page.ocr_dpi_spin.maximum() == 600

    fake_tesseract = tmp_path / "tesseract-muster"
    fake_tesseract.write_text("#!/bin/sh\n", encoding="utf-8")
    rec.open_file = fake_tesseract
    qtbot.mouseClick(page.tesseract_pick, Qt.MouseButton.LeftButton)
    assert page.tesseract_label.text() == str(fake_tesseract)
    assert svc.settings.tesseract_cmd is None  # erst nach „Übernehmen“
    qtbot.mouseClick(page.ocr_apply, Qt.MouseButton.LeftButton)
    assert Settings.load(svc.paths.settings_file).tesseract_cmd == str(fake_tesseract)
    qtbot.mouseClick(page.tesseract_auto, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(page.ocr_apply, Qt.MouseButton.LeftButton)
    assert svc.settings.tesseract_cmd is None
    assert page.tesseract_label.text().startswith("Automatisch")

    page.flatten_check.setChecked(True)
    assert svc.settings.flatten_default is True
    page.letter_form_combo.setCurrentIndex(page.letter_form_combo.findData("A"))
    assert Settings.load(svc.paths.settings_file).letter_form == "A"


def test_export_log_table_without_personal_data(qtbot, tmp_path, monkeypatch):
    patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    entry = ExportLogEntry(
        case_id="VG-MUSTER-0001",
        template_id="swisslife_sl13065_2024-09",
        template_version="2024-09",
        file_name="2026-11-13_SWISSLIFE_SL13065_Freigabe_VG-MUSTER-0001.pdf",
        flattened=False,
        qa_passed=True,
        sha256="0" * 64,
    )
    svc.paths.export_log_file.parent.mkdir(parents=True, exist_ok=True)
    svc.paths.export_log_file.write_text(entry.model_dump_json() + "\n", encoding="utf-8")
    page = _page(qtbot, svc)
    table = page.export_table
    assert table.rowCount() == 1
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["Zeit", "Vorgang", "Vorlage", "Version", "Datei", "Fixiert", "QA"]
    assert table.item(0, 1).text() == "VG-MUSTER-0001"
    assert table.item(0, 6).text() == "✔ bestanden"
    assert not page.export_empty.isVisible()


def test_consent_dialog_requires_checkbox(qtbot):
    info = DownloadInfo(model="qwen3-vl:4b", approx_gb=3.3, free_disk_gb=50.0, license_hint="bitte prüfen")
    dlg = ConsentDialog(info)
    qtbot.addWidget(dlg)
    text = "\n".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    assert "Ollama" in text and "keine Dokumente" in text
    assert "3,3 GB" in text and "50,0 GB" in text
    assert dlg.consent.text() == CONSENT_TEXT
    assert not dlg.ok_button.isEnabled()
    dlg._accept()
    assert not dlg.consented
    dlg.consent.setChecked(True)
    assert dlg.ok_button.isEnabled()
    dlg._accept()
    assert dlg.consented


def test_settings_screenshot(qtbot, tmp_path, monkeypatch):
    patch_dialogs(monkeypatch)
    apply_app_style()
    svc = make_service(tmp_path, ollama_url=free_port_url())
    svc.create_sample_case()
    host, page = build_host(svc, "settings", lambda: SettingsPage(svc))
    try:
        host.show()
        qtbot.waitExposed(host)
        page.refresh()
        qtbot.waitUntil(lambda: not page.ai_panel.checking, timeout=15_000)
        qtbot.wait(100)
        image = host.grab()
        assert not image.isNull()
        target = screenshot_dir()
        if target is not None:
            assert image.save(str(target / "ui_settings.png"))
    finally:
        dispose(host)
