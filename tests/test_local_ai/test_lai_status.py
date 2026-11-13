"""Statuserkennung der lokalen KI und Hardware-Hinweise."""

from __future__ import annotations

from pathlib import Path

import pytest
from lai_fake_ollama import free_port, running_fake

from insurance_app.core.settings import Settings
from insurance_app.local_ai import status as status_mod
from insurance_app.local_ai.ollama_client import OllamaClient
from insurance_app.local_ai.status import (
    MODEL_CATALOG,
    UNVERIFIED_NOTE,
    HardwareInfo,
    LocalAiState,
    check_model_requirements,
    detect_status,
    find_ollama_binary,
    hardware_info,
)


@pytest.fixture()
def no_binary(monkeypatch):
    monkeypatch.setattr(status_mod, "find_ollama_binary", lambda: None)


@pytest.fixture()
def with_binary(monkeypatch, tmp_path):
    exe = tmp_path / "ollama"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(status_mod, "find_ollama_binary", lambda: exe)


def test_state_labels_are_german():
    assert LocalAiState.NOT_INSTALLED == "Lokale KI nicht installiert"
    assert LocalAiState.SETTING_UP == "Modell wird eingerichtet"
    assert LocalAiState.READY == "Bereit"
    assert LocalAiState.ERROR == "Fehler"
    assert LocalAiState.DISABLED == "Deaktiviert"


def test_disabled_by_default(no_binary):
    settings = Settings(ollama_url=f"http://127.0.0.1:{free_port()}")
    st = detect_status(settings)
    assert settings.ai_enabled is False
    assert st.state == LocalAiState.DISABLED
    assert "ohne sie" in st.message


def test_not_installed_when_no_binary_and_server_down(no_binary):
    settings = Settings(ai_enabled=True, ollama_url=f"http://127.0.0.1:{free_port()}")
    st = detect_status(settings)
    assert st.state == LocalAiState.NOT_INSTALLED
    assert not st.ollama_found and not st.ollama_running
    assert st.ollama_version is None


def test_error_when_installed_but_not_running(with_binary):
    settings = Settings(ai_enabled=True, ollama_url=f"http://127.0.0.1:{free_port()}")
    st = detect_status(settings)
    assert st.state == LocalAiState.ERROR
    assert st.ollama_found and not st.ollama_running
    assert "starten" in st.message


def test_ready_when_model_present(no_binary):
    with running_fake() as fake:
        settings = Settings(ai_enabled=True, ollama_url=fake.url, ai_model="qwen3-vl:4b")
        st = detect_status(settings, client=OllamaClient(fake.url))
    assert st.state == LocalAiState.READY
    assert st.ollama_found  # Dienst läuft -> gilt als installiert
    assert st.ollama_running and st.model_present
    assert st.ollama_version == "0.12.0"
    assert st.model_name == "qwen3-vl:4b"


def test_model_missing_and_pulling(no_binary):
    with running_fake() as fake:
        settings = Settings(ai_enabled=True, ollama_url=fake.url, ai_model="qwen3-vl:8b")
        missing = detect_status(settings)
        pulling = detect_status(settings, pulling=True)
    assert missing.state == LocalAiState.NOT_INSTALLED
    assert missing.ollama_running and not missing.model_present
    assert "Zustimmung" in missing.message
    assert pulling.state == LocalAiState.SETTING_UP


def test_invalid_model_name_in_settings(no_binary):
    with running_fake() as fake:
        settings = Settings(ai_enabled=True, ollama_url=fake.url, ai_model="qwen3-vl:235b-cloud")
        st = detect_status(settings)
    assert st.state == LocalAiState.ERROR
    assert "Cloud" in st.message


def test_non_loopback_url_in_settings(no_binary):
    st = detect_status(Settings(ai_enabled=True, ollama_url="http://192.168.1.10:11434"))
    assert st.state == LocalAiState.ERROR
    assert "nicht auf diesen Rechner" in st.message
    st_off = detect_status(Settings(ai_enabled=False, ollama_url="http://example.com:11434"))
    assert st_off.state == LocalAiState.DISABLED


def test_find_ollama_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(status_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(status_mod, "_candidate_paths", lambda: [tmp_path / "fehlt" / "ollama"])
    assert find_ollama_binary() is None
    exe = tmp_path / "Ollama.app" / "Contents" / "Resources" / "ollama"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(status_mod, "_candidate_paths", lambda: [tmp_path / "fehlt", exe])
    assert find_ollama_binary() == exe
    monkeypatch.setattr(status_mod.shutil, "which", lambda _name: "/opt/x/ollama")
    assert find_ollama_binary() == Path("/opt/x/ollama")


def test_candidate_paths_per_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(status_mod.sys, "platform", "darwin")
    assert Path("/Applications/Ollama.app/Contents/Resources/ollama") in status_mod._candidate_paths()
    monkeypatch.setattr(status_mod.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tmp_path / "Programs" / "Ollama" / "ollama.exe" in status_mod._candidate_paths()


def test_model_catalog_verification_flags():
    assert set(MODEL_CATALOG) >= {"qwen3-vl:4b", "qwen3-vl:8b", "glm-ocr", "gemma4:e4b"}
    for entry in MODEL_CATALOG.values():
        assert entry.approx_download_gb > 0 and entry.min_ram_gb > 0
        if entry.verified:  # Größe aus dem Ollama-Registry-Manifest gelesen
            assert "Registry" in entry.note
        else:
            assert entry.note == UNVERIFIED_NOTE and "ungeprüft" in entry.note
    assert MODEL_CATALOG["qwen3-vl:8b"].verified is False  # nicht aus der Registry geprüft


def test_hardware_info(tmp_path, monkeypatch):
    hw = hardware_info(tmp_path / "models")
    assert hw.models_dir == str(tmp_path / "models")
    assert hw.free_disk_gb is not None and hw.free_disk_gb >= 0
    assert hw.cpu_count and hw.cpu_count >= 1
    assert hw.total_ram_gb is None or hw.total_ram_gb > 0
    assert hw.platform
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "env-models"))
    assert hardware_info().models_dir == str(tmp_path / "env-models")
    monkeypatch.delenv("OLLAMA_MODELS")
    assert hardware_info().models_dir.endswith(str(Path(".ollama") / "models"))


def test_check_model_requirements():
    weak = HardwareInfo(
        total_ram_gb=4, available_ram_gb=2, free_disk_gb=1, cpu_count=2, platform="x", models_dir="m"
    )
    hints = check_model_requirements("qwen3-vl:8b", weak)
    assert any("Arbeitsspeicher knapp" in h for h in hints)
    assert any("Speicherplatz knapp" in h for h in hints)
    assert UNVERIFIED_NOTE in hints
    strong = HardwareInfo(total_ram_gb=64, free_disk_gb=500, cpu_count=8, platform="x", models_dir="m")
    assert check_model_requirements("qwen3-vl:4b", strong) == []  # Größe verifiziert -> kein Hinweis
    assert "keine Größenangaben" in check_model_requirements("llava", strong)[0]
