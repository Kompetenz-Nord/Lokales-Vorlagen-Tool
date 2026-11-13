"""Dateien in einen Vorgang übernehmen (mit Passwortabfrage für geschützte PDFs).

Passwörter fragt nur ``dialogs.ask_password`` ab; sie werden weder gespeichert noch protokolliert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtWidgets import QWidget

from ...core.case import Case
from ...core.service import AppService
from .. import dialogs
from .common import error_text

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass
class ImportReport:
    accepted: int = 0
    duplicates: int = 0
    problems: list[str] = field(default_factory=list)  # verständliche Meldungen je Datei

    @property
    def any_added(self) -> bool:
        return self.accepted > 0 or self.duplicates > 0


def local_files(paths: list[Path]) -> list[Path]:
    """Nur vorhandene Dateien (Ordner werden ignoriert), Reihenfolge bleibt erhalten."""
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_file() and p not in out:
            out.append(p)
    return out


def add_files(parent: QWidget | None, service: AppService, case: Case, paths: list[Path]) -> ImportReport:
    """Dateien prüfen und als schreibgeschützte Kopie übernehmen."""
    report = ImportReport()
    for path in local_files(paths):
        name = path.name
        try:
            result = service.add_document(case, path)
            while result.needs_password:
                password = dialogs.ask_password(parent, result.file_name, wrong=result.wrong_password)
                if not password:
                    break
                result = service.add_document(case, path, password=password)
        except Exception as exc:  # noqa: BLE001 – verständlich melden, nächste Datei weiter
            log.info("Datei nicht übernommen: %s", type(exc).__name__)
            report.problems.append(f"„{name}“: {error_text(exc)}")
            continue
        if result.needs_password:
            report.problems.append(f"„{name}“: übersprungen – es wurde kein Passwort eingegeben.")
        elif result.duplicate:
            report.duplicates += 1
            report.problems.append(f"„{name}“: {result.message}")
        elif result.accepted:
            report.accepted += 1
        else:
            report.problems.append(
                f"„{name}“: {result.message or 'Die Datei kann nicht verarbeitet werden.'}"
            )
    return report
