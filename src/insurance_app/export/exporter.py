"""Export: geprüfte PDF in den Exportordner übernehmen und protokollieren.

* Export nur nach bestandener Qualitätskontrolle (``QaReport.passed``) und nur,
  wenn die Datei seit der Prüfung unverändert ist (SHA-256).
* Die Datei wird atomar unter einem standardisierten Namen abgelegt.
* Das Exportprotokoll (JSON Lines) enthält keine personenbezogenen Daten:
  Zeitpunkt, Vorgangs-ID, Vorlage, Dateiname, Fixierung, QA-Ergebnis, Prüfsumme.
* Exportierte PDFs enthalten Personen- und Bankdaten: Auf POSIX-Systemen sind sie
  nur für den eigenen Benutzer lesbar (``0600``). Der Standard-Exportordner der App
  wird mit ``0700`` angelegt; einen selbst gewählten Ordner ändert die App nicht.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from insurance_app.core.models import utcnow
from insurance_app.export.naming import build_export_filename, sanitize_part
from insurance_app.pdf.qa import QaReport
from insurance_app.templates.schema import TemplateDefinition

__all__ = ["ExportBlockedError", "ExportLogEntry", "ExportResult", "Exporter"]

log = logging.getLogger(__name__)

_MAX_UNIQUE = 10_000
_FILE_MODE = 0o600
_DIR_MODE = 0o700


def _make_private(path: Path, mode: int = _FILE_MODE) -> None:
    """Rechte auf ``mode`` setzen (nur POSIX; Fehler werden ignoriert)."""
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        os.chmod(path, mode)


class ExportBlockedError(Exception):
    """Export nicht erlaubt (z. B. Qualitätskontrolle nicht bestanden)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message = message


class ExportLogEntry(BaseModel):
    """Protokollzeile – bewusst ohne personenbezogene Daten."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=utcnow)
    case_id: str
    template_id: str
    template_version: str
    file_name: str
    flattened: bool = False
    qa_passed: bool
    sha256: str


class ExportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    log_entry: ExportLogEntry


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Exporter:
    def __init__(self, export_dir: Path, log_file: Path, *, private_dir: bool = False) -> None:
        """``private_dir=True``: Exportordner gehört der App (Standardordner) und wird auf
        ``0700`` gesetzt. Ein vom Anwender gewählter Ordner (Standard ``False``) bleibt unverändert.
        """
        self.export_dir = Path(export_dir)
        self.log_file = Path(log_file)
        self.private_dir = private_dir

    def finalize(
        self,
        case_id: str,
        template: TemplateDefinition,
        filled_tmp: Path,
        qa: QaReport,
        flattened: bool,
        created: date | None = None,
    ) -> ExportResult:
        """Geprüfte Datei exportieren. Wirft ``ExportBlockedError``, wenn das nicht erlaubt ist."""
        filled_tmp = Path(filled_tmp)
        if not qa.passed:
            raise ExportBlockedError(
                "Die Qualitätskontrolle ist nicht bestanden. Die PDF wurde nicht exportiert."
            )
        if qa.template_id and qa.template_id != template.template_id:
            raise ExportBlockedError("Die Qualitätskontrolle gehört zu einer anderen Vorlage.")
        if qa.flattened != flattened:
            raise ExportBlockedError("Die Qualitätskontrolle passt nicht zur gewählten Ausgabeart.")
        if not filled_tmp.is_file():
            raise ExportBlockedError(
                "Die erzeugte PDF wurde nicht gefunden. Bitte die Vorschau neu erzeugen."
            )
        sha = _sha256_file(filled_tmp)
        if qa.filled_sha256 and qa.filled_sha256 != sha:
            raise ExportBlockedError(
                "Die PDF wurde nach der Qualitätskontrolle verändert. Bitte die Vorschau neu erzeugen."
            )
        safe_case = sanitize_part(case_id, "VORGANG")
        name = build_export_filename(
            created or date.today(),
            template.insurer_code,
            template.form_number,
            template.short_title,
            safe_case,
        )
        if self.private_dir:
            self.export_dir.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
            _make_private(self.export_dir, _DIR_MODE)
        else:
            self.export_dir.mkdir(parents=True, exist_ok=True)
        dest = self._reserve(name)
        try:
            _make_private(filled_tmp)  # Rechte vor dem Umbenennen setzen: kein lesbares Zeitfenster
            self._move(filled_tmp, dest)
            _make_private(dest)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        if _sha256_file(dest) != sha:  # pragma: no cover - Absicherung gegen Kopierfehler
            dest.unlink(missing_ok=True)
            raise ExportBlockedError("Die exportierte Datei ist fehlerhaft. Bitte erneut versuchen.")
        entry = ExportLogEntry(
            case_id=safe_case,
            template_id=template.template_id,
            template_version=template.version,
            file_name=dest.name,
            flattened=flattened,
            qa_passed=qa.passed,
            sha256=sha,
        )
        self._append_log(entry)
        log.info("Export abgeschlossen: vorgang=%s vorlage=%s", safe_case, template.template_id)
        return ExportResult(path=dest, log_entry=entry)

    # ------------------------------------------------------------------
    def _reserve(self, name: str) -> Path:
        """Freien Dateinamen exklusiv anlegen (verhindert Überschreiben bei parallelen Exporten)."""
        stem, suffix = Path(name).stem, Path(name).suffix
        for counter in range(1, _MAX_UNIQUE):
            candidate = self.export_dir / (name if counter == 1 else f"{stem}_{counter}{suffix}")
            try:
                fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE)
            except FileExistsError:
                continue
            os.close(fd)
            return candidate
        raise ExportBlockedError("Im Exportordner ist kein freier Dateiname verfügbar.")

    @staticmethod
    def _move(source: Path, dest: Path) -> None:
        """Datei atomar an ``dest`` übernehmen (gleiches Laufwerk: umbenennen, sonst kopieren)."""
        try:
            os.replace(source, dest)
            return
        except OSError:
            pass
        part = dest.with_name(dest.name + ".part")
        try:
            part.unlink(missing_ok=True)
            fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), _FILE_MODE)
            with os.fdopen(fd, "wb") as out, source.open("rb") as src:
                shutil.copyfileobj(src, out, 1 << 20)
                out.flush()
                os.fsync(out.fileno())
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        try:
            source.unlink()
        except OSError:  # pragma: no cover - Quelle gesperrt
            log.warning("Temporäre Datei konnte nach dem Export nicht gelöscht werden")

    def _append_log(self, entry: ExportLogEntry) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        line = entry.model_dump_json() + "\n"
        with self.log_file.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def read_log(self) -> list[ExportLogEntry]:
        """Protokoll lesen (beschädigte Zeilen werden übersprungen)."""
        if not self.log_file.is_file():
            return []
        entries: list[ExportLogEntry] = []
        for line in self.log_file.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(ExportLogEntry.model_validate(json.loads(line)))
            except (ValueError, ValidationError):
                log.warning("Beschädigte Zeile im Exportprotokoll übersprungen")
        return entries
