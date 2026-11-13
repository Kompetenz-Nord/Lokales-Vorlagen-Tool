"""Hilfen für die UI-Tests von Dashboard und Vorgangsansicht (nur synthetische Daten)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from insurance_app.core.paths import AppPaths
from insurance_app.core.service import AppService
from insurance_app.ui import dialogs, workers
from insurance_app.ui.theme import STYLESHEET

TODAY = date(2026, 11, 13)
SYN = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
POLICY_PDF = SYN / "MUSTER_Police_Direktversicherung.pdf"
AMENDMENT_PDF = SYN / "MUSTER_Nachtrag_Beitrag.pdf"
SL13065 = "swisslife_sl13065_freigabe"
WINDOW_SIZE = (1360, 860)

FREIGABE_CONFIRM_KEYS = (
    "contract.number",
    "insured.first_name",
    "insured.last_name",
    "policyholder.name",
    "insured.birth_date",
)
FREIGABE_CONFLICT_KEYS = ("insured.street", "insured.house_number", "insured.postal_code", "insured.city")


def make_service(tmp_path: Path) -> AppService:
    return AppService(
        AppPaths.for_testing(tmp_path), guard_network=False, configure_logs=False, today=lambda: TODAY
    )


def flush_tasks() -> None:
    """Laufende Hintergrundaufgaben abwarten und ihre Rückmeldungen verarbeiten."""
    workers.wait_for_all(30_000)
    app = QApplication.instance()
    if app is not None:
        for _ in range(5):
            app.processEvents()


class DialogRecorder:
    """Ersetzt alle modalen Dialoge (``insurance_app.ui.dialogs``) und zeichnet Aufrufe auf."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.infos: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []
        self.confirms: list[dict] = []
        self.passwords: list[tuple[str, bool]] = []
        self.texts: list[tuple[str, str]] = []
        self.opened: list[Path] = []
        self.confirm_answers: list[bool] = []
        self.password_answers: list[str | None] = []
        self.text_answers: list[str | None] = []
        self.files: list[Path] = []
        monkeypatch.setattr(dialogs, "show_info", self._info)
        monkeypatch.setattr(dialogs, "show_error", self._error)
        monkeypatch.setattr(dialogs, "ask_confirm", self._confirm)
        monkeypatch.setattr(dialogs, "ask_password", self._password)
        monkeypatch.setattr(dialogs, "ask_text", self._text)
        monkeypatch.setattr(dialogs, "pick_open_files", lambda *a, **k: list(self.files))
        monkeypatch.setattr(dialogs, "open_path", lambda p: self.opened.append(Path(p)))

    def _info(self, parent, title, text):
        self.infos.append((title, text))

    def _error(self, parent, title, text):
        self.errors.append((title, text))

    def _confirm(self, parent, title, text, *, ok_text="Ja", cancel_text="Abbrechen", danger=False):
        self.confirms.append({"title": title, "text": text, "ok_text": ok_text, "danger": danger})
        return self.confirm_answers.pop(0) if self.confirm_answers else False

    def _password(self, parent, file_name, wrong=False):
        self.passwords.append((file_name, wrong))
        return self.password_answers.pop(0) if self.password_answers else None

    def _text(self, parent, title, label, value=""):
        self.texts.append((title, value))
        return self.text_answers.pop(0) if self.text_answers else None


def make_encrypted_copy(src: Path, dest: Path, user_pw: str) -> Path:
    """Synthetische PDF mit Öffnungspasswort (AES-256) erzeugen."""
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


def style_window(widget: QWidget) -> QWidget:
    """Einheitliches Aussehen für Bildschirmfotos (ohne globales Stylesheet zu ändern)."""
    widget.setStyleSheet(STYLESHEET)
    widget.resize(*WINDOW_SIZE)
    return widget


def screenshot_dir() -> Path | None:
    target = os.environ.get("LVT_SCREENSHOTS")
    if not target:
        return None
    path = Path(target)
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


def release_freigabe_contract_data(svc: AppService, case) -> None:
    """Vertragsdaten für SL13065 wie ein Mensch freigeben (Bestätigen, Konflikte entscheiden, Eintragen)."""
    for key in FREIGABE_CONFIRM_KEYS:
        svc.confirm_field(case, key)
    for key in FREIGABE_CONFLICT_KEYS:
        fv = case.field(key)
        svc.choose_candidate(case, key, fv.suggestion_index)
    svc.edit_field(case, "employer.employment_start", "01.01.2019")
    svc.edit_field(case, "employer.employment_end", "30.09.2026")
