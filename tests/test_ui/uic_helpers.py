"""Hilfen für die UI-Tests von Kundenmappen, Dashboard-Mappenwahl, Einstellungen „Lokale KI“ und
KI-Zweitprüfung im Vorgang (nur synthetische Daten, kein Netzwerk außer simuliertem Ollama auf 127.0.0.1).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from insurance_app.core.paths import AppPaths
from insurance_app.core.service import AppService
from insurance_app.local_ai.status import HardwareInfo
from insurance_app.ui import dialogs, workers

TODAY = date(2026, 11, 13)
ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "tests" / "fixtures" / "synthetic"
POLICY_PDF = SYN / "MUSTER_Police_Direktversicherung.pdf"
AMENDMENT_PDF = SYN / "MUSTER_Nachtrag_Beitrag.pdf"
FONT = ROOT / "assets" / "fonts" / "LiberationSans-Regular.ttf"
FAKE_OLLAMA = ROOT / "tests" / "test_local_ai" / "lai_fake_ollama.py"
WINDOW_SIZE = (1360, 860)

SACH_INSURER = "Muster Sachversicherung AG"
SACH_NUMBER = "7770001-2"
KRANKEN_INSURER = "Muster Krankenversicherung a.G."
KRANKEN_NUMBER = "5550003-4"


def make_service(tmp_path: Path, **settings: Any) -> AppService:
    svc = AppService(
        AppPaths.for_testing(tmp_path), guard_network=False, configure_logs=False, today=lambda: TODAY
    )
    if settings:
        svc.update_settings(**settings)
    return svc


def flush_tasks() -> None:
    """Laufende Hintergrundaufgaben abwarten und ihre Rückmeldungen verarbeiten."""
    workers.wait_for_all(30_000)
    app = QApplication.instance()
    if app is not None:
        for _ in range(5):
            app.processEvents()


# ---------------------------------------------------------------------------
# Synthetische Dokumente (MUSTER – KEINE ECHTDATEN)
# ---------------------------------------------------------------------------


def make_policy_pdf(
    path: Path,
    insurer: str = SACH_INSURER,
    number: str = SACH_NUMBER,
    premium: str = "25,00 EUR monatlich",
) -> Path:
    """Einseitige Police einer erfundenen Gesellschaft mit Textebene (schnell, ohne OCR)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="lib", fontfile=str(FONT))
    lines = [
        (f"{insurer} (MUSTER)", 60),
        ("MUSTER – KEINE ECHTDATEN", 80),
        ("Versicherungsschein", 130),
        (f"Versicherer:               {insurer}", 160),
        (f"Versicherungsnummer:      {number}", 180),
        ("Versicherungsnehmer:      Musterfrau, Erika", 200),
        ("Versicherungsbeginn:      01.03.2021", 220),
        (f"Beitrag:                  {premium}", 240),
    ]
    for text, y in lines:
        page.insert_text((60, y), text, fontname="lib", fontsize=11)
    doc.save(path)
    doc.close()
    return Path(path)


def make_kranken_pdf(path: Path) -> Path:
    return make_policy_pdf(path, KRANKEN_INSURER, KRANKEN_NUMBER, "310,00 EUR monatlich")


def make_encrypted_copy(src: Path, dest: Path, user_pw: str) -> Path:
    doc = pymupdf.open(src)
    doc.save(
        dest,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="MUSTER-owner",
        user_pw=user_pw,
        permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_FORM,
    )
    doc.close()
    return dest


# ---------------------------------------------------------------------------
# Dialoge
# ---------------------------------------------------------------------------


class DialogRecorder:
    """Ersetzt alle modalen Dialoge (``insurance_app.ui.dialogs``) und zeichnet Aufrufe auf."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.infos: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []
        self.confirms: list[dict] = []
        self.passwords: list[tuple[str, bool]] = []
        self.texts: list[tuple[str, str, str]] = []
        self.opened: list[Path] = []
        self.confirm_answers: list[bool] = []
        self.password_answers: list[str | None] = []
        self.text_answers: list[str | None] = []
        self.files: list[Path] = []
        monkeypatch.setattr(dialogs, "show_info", lambda p, t, x: self.infos.append((t, x)))
        monkeypatch.setattr(dialogs, "show_error", lambda p, t, x: self.errors.append((t, x)))
        monkeypatch.setattr(dialogs, "ask_confirm", self._confirm)
        monkeypatch.setattr(dialogs, "ask_password", self._password)
        monkeypatch.setattr(dialogs, "ask_text", self._text)
        monkeypatch.setattr(dialogs, "pick_open_files", lambda *a, **k: list(self.files))
        monkeypatch.setattr(dialogs, "open_path", lambda p: self.opened.append(Path(p)))

    def _confirm(self, parent, title, text, *, ok_text="Ja", cancel_text="Abbrechen", danger=False):
        self.confirms.append({"title": title, "text": text, "ok_text": ok_text, "danger": danger})
        return self.confirm_answers.pop(0) if self.confirm_answers else False

    def _password(self, parent, file_name, wrong=False):
        self.passwords.append((file_name, wrong))
        return self.password_answers.pop(0) if self.password_answers else None

    def _text(self, parent, title, label, value=""):
        self.texts.append((title, label, value))
        return self.text_answers.pop(0) if self.text_answers else None

    def info_text(self) -> str:
        return "\n".join(f"{t}: {x}" for t, x in self.infos)

    def error_text(self) -> str:
        return "\n".join(f"{t}: {x}" for t, x in self.errors)


# ---------------------------------------------------------------------------
# Lokale KI
# ---------------------------------------------------------------------------


def fixed_hardware(ram: float = 16.0, disk: float = 120.0, cpu: int = 8) -> HardwareInfo:
    """Feste Hardwareangaben (Tests sollen nicht vom Testrechner abhängen)."""
    return HardwareInfo(
        total_ram_gb=ram,
        available_ram_gb=round(ram / 2, 1),
        free_disk_gb=disk,
        cpu_count=cpu,
        platform="MUSTER-OS x86_64",
        models_dir="/tmp/muster-ollama-models",
    )


def fake_ollama() -> ModuleType:
    """Simulierten Ollama-Dienst aus tests/test_local_ai laden (nur 127.0.0.1, ephemerer Port)."""
    name = "lai_fake_ollama"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, FAKE_OLLAMA)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def free_port_url() -> str:
    """Loopback-Adresse, auf der niemand lauscht (kein echter Ollama-Dienst wird berührt)."""
    return f"http://127.0.0.1:{fake_ollama().free_port()}"


# ---------------------------------------------------------------------------
# Fenster und Bildschirmfotos
# ---------------------------------------------------------------------------


def apply_app_style() -> None:
    """Stil der echten App anwenden (nur einmal)."""
    from insurance_app.ui.app import create_application
    from insurance_app.ui.theme import STYLESHEET

    app = QApplication.instance()
    if app is None or app.styleSheet() != STYLESHEET:
        create_application([])


def screenshot_dir() -> Path | None:
    value = os.environ.get("LVT_SCREENSHOTS")
    if not value:
        return None
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_grab(widget: QWidget, name: str) -> Path | None:
    """``widget.grab()`` als PNG speichern, wenn ``LVT_SCREENSHOTS`` gesetzt ist; sonst nur rendern."""
    QApplication.processEvents()
    pixmap = widget.grab()
    assert not pixmap.isNull()
    target = screenshot_dir()
    if target is None:
        return None
    out = target / name
    assert pixmap.save(str(out), "PNG")
    return out


def dispose(widget: QWidget | None) -> None:
    """Fenster schließen und sofort löschen (keine verzögerten Aufrufe in spätere Tests)."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    if widget is None or not isValid(widget):
        return
    flush_tasks()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
