"""Hilfen für die UI-Tests von Katalog, Vorlagen-Studio und Einstellungen (nur synthetische Daten)."""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pymupdf
import pytest

from insurance_app.core.paths import AppPaths
from insurance_app.core.service import AppService

TODAY = date(2026, 11, 13)
SCREENSHOT_ENV = "LVT_SCREENSHOTS"
_FAKE_OLLAMA = Path(__file__).resolve().parents[1] / "test_local_ai" / "lai_fake_ollama.py"


def make_service(tmp_path: Path, **settings: Any) -> AppService:
    svc = AppService(
        AppPaths.for_testing(tmp_path),
        guard_network=False,
        configure_logs=False,
        today=lambda: TODAY,
    )
    if settings:
        svc.update_settings(**settings)
    return svc


@pytest.fixture(autouse=True)
def _uib_wait_tasks():
    """Nach jedem Test auf Hintergrundaufgaben warten (QThreadPool)."""
    yield
    from insurance_app.ui.workers import wait_for_all

    wait_for_all(30_000)


# ---------------------------------------------------------------------------
# Dialoge
# ---------------------------------------------------------------------------


@dataclass
class DialogRecorder:
    infos: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    confirms: list[tuple[str, str]] = field(default_factory=list)
    confirm_answer: bool = True
    open_file: Path | None = None
    save_file: Path | None = None
    directory: Path | None = None
    opened: list[Path] = field(default_factory=list)

    def info_text(self) -> str:
        return "\n".join(f"{t}: {x}" for t, x in self.infos)

    def error_text(self) -> str:
        return "\n".join(f"{t}: {x}" for t, x in self.errors)


def patch_dialogs(monkeypatch: pytest.MonkeyPatch) -> DialogRecorder:
    from insurance_app.ui import dialogs

    rec = DialogRecorder()

    def ask_confirm(parent, title, text, **_kw):
        rec.confirms.append((title, text))
        return rec.confirm_answer

    monkeypatch.setattr(dialogs, "show_info", lambda parent, title, text: rec.infos.append((title, text)))
    monkeypatch.setattr(dialogs, "show_error", lambda parent, title, text: rec.errors.append((title, text)))
    monkeypatch.setattr(dialogs, "ask_confirm", ask_confirm)
    monkeypatch.setattr(dialogs, "pick_open_file", lambda parent, title, flt: rec.open_file)
    monkeypatch.setattr(dialogs, "pick_save_file", lambda parent, title, suggested, flt: rec.save_file)
    monkeypatch.setattr(dialogs, "pick_directory", lambda parent, title, start=None: rec.directory)
    monkeypatch.setattr(dialogs, "open_path", lambda path: rec.opened.append(Path(path)))
    return rec


# ---------------------------------------------------------------------------
# Synthetische PDFs
# ---------------------------------------------------------------------------


def _text_field(page: pymupdf.Page, name: str, rect: tuple[float, float, float, float]) -> None:
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = pymupdf.Rect(rect)
    w.text_font = "Helv"
    w.text_fontsize = 10
    page.add_widget(w)


def make_acroform_pdf(path: Path) -> Path:
    """Formular (MUSTER) mit Textfeldern und einem Ankreuzfeld."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 60), "MUSTER - Haftungsfreistellung (KEINE ECHTDATEN)", fontsize=12)
    page.insert_text((50, 112), "Vertragsnummer", fontsize=9)
    _text_field(page, "Vertragsnummer", (160, 100, 380, 116))
    page.insert_text((50, 142), "Name", fontsize=9)
    _text_field(page, "Name", (160, 130, 420, 146))
    page.insert_text((50, 172), "Kundennummer", fontsize=9)
    _text_field(page, "Kundennummer", (160, 160, 380, 176))
    cb = pymupdf.Widget()
    cb.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    cb.field_name = "Einverstanden"
    cb.rect = pymupdf.Rect(50, 200, 62, 212)
    page.add_widget(cb)
    page.insert_text((70, 210), "Einverstanden (MUSTER)", fontsize=9)
    doc.save(path)
    doc.close()
    return Path(path)


def make_plain_pdf(path: Path, pages: int = 2) -> Path:
    """PDF ohne Formularfelder (für den Koordinateneditor)."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 60), f"MUSTER - Formular ohne Felder, Seite {n} (KEINE ECHTDATEN)", fontsize=12)
        page.insert_text((50, 100), "Vertragsnummer: ____________________", fontsize=10)
    doc.save(path)
    doc.close()
    return Path(path)


# ---------------------------------------------------------------------------
# Simulierter Ollama-Dienst (aus tests/test_local_ai, nur 127.0.0.1)
# ---------------------------------------------------------------------------


def fake_ollama() -> ModuleType:
    name = "lai_fake_ollama"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FAKE_OLLAMA)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def free_port_url() -> str:
    return f"http://127.0.0.1:{fake_ollama().free_port()}"


# ---------------------------------------------------------------------------
# Bildschirmfotos
# ---------------------------------------------------------------------------


def screenshot_dir() -> Path | None:
    value = os.environ.get(SCREENSHOT_ENV)
    if not value:
        return None
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_host(service: AppService, section: str, page_factory):
    """Hauptfenster (falls alle Seiten vorhanden sind), sonst die Seite allein – jeweils 1360×860.

    -> (Fenster/Widget zum Fotografieren, Seite)
    """
    try:
        from insurance_app.ui.main_window import MainWindow

        win = MainWindow(service)
        win.resize(1360, 860)
        win.show_section(section)
        return win, win.pages[section]
    except Exception:  # noqa: BLE001 – andere Seiten evtl. noch nicht vorhanden
        page = page_factory()
        page.resize(1360, 860)
        return page, page


def apply_app_style() -> None:
    """Stil der echten App anwenden (nur einmal – erneutes Setzen würde alle Fenster neu gestalten)."""
    from PySide6.QtWidgets import QApplication

    from insurance_app.ui.app import create_application
    from insurance_app.ui.theme import STYLESHEET

    app = QApplication.instance()
    if app is None or app.styleSheet() != STYLESHEET:
        create_application([])


def dispose(widget) -> None:
    """Fenster sofort schließen und löschen.

    Erst ausstehende Null-Timer ausführen (solange alles lebt), dann synchron löschen –
    so laufen verzögerte Aufrufe anderer Seiten nicht in einen späteren Test hinein.
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    if widget is None or not isValid(widget):
        return
    QCoreApplication.processEvents()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
