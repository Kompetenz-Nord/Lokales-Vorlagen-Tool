"""Zentrale Logging-Konfiguration (nur technische Angaben, geschwärzt).

* Rotierende Logdatei ``<log_dir>/app.log`` (1 MB, 3 Sicherungen, UTF-8).
* Jeder Handler erhält einen :class:`RedactingFilter`.
* Mehrfacher Aufruf ist unschädlich (idempotent): eigene Handler werden ersetzt,
  fremde Handler bleiben unberührt.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import re
import secrets
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from .redaction import RedactingFilter

__all__ = [
    "LOG_FILE_NAME",
    "LOG_FORMAT",
    "JobLoggerAdapter",
    "configure_logging",
    "job_logger",
    "new_job_id",
    "shutdown_logging",
]

LOG_FILE_NAME = "app.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3

_MARKER = "_insurance_app_handler"
_SAFE_JOB_ID = re.compile(r"[^A-Za-z0-9_.:-]")


def _own_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if getattr(h, _MARKER, False)]


def _restrict_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    # Fehler (z. B. fremder Besitzer) sind unkritisch: Logs enthalten nur technische Angaben.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def configure_logging(log_dir: Path, level: int = logging.INFO, console: bool = False) -> Path:
    """Logging der App einrichten und den Pfad der Logdatei zurückgeben."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILE_NAME

    root = logging.getLogger()
    for handler in _own_handlers(root):
        root.removeHandler(handler)
        with contextlib.suppress(OSError):
            handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handlers: list[logging.Handler] = [file_handler]
    if console and sys.stderr is not None:  # in gebauten Fenster-Apps gibt es kein stderr
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        handler.setLevel(level)
        setattr(handler, _MARKER, True)
        root.addHandler(handler)
    root.setLevel(level)
    logging.captureWarnings(True)
    _restrict_permissions(log_file)
    logging.getLogger(__name__).debug("Logging eingerichtet (Level %s)", logging.getLevelName(level))
    return log_file


def shutdown_logging() -> None:
    """Eigene Handler schließen und entfernen (z. B. vor dem Löschen des Logordners)."""
    root = logging.getLogger()
    for handler in _own_handlers(root):
        root.removeHandler(handler)
        with contextlib.suppress(OSError):
            handler.close()


def new_job_id() -> str:
    """Nicht sprechende Job-ID (wird in Logs nicht geschwärzt)."""
    return f"J-{secrets.token_hex(4).upper()}"


class JobLoggerAdapter(logging.LoggerAdapter):
    """Setzt jeder Nachricht ``[job <id>]`` voran."""

    def __init__(self, logger: logging.Logger, job_id: str) -> None:
        super().__init__(logger, {"job_id": job_id})
        self.job_id = job_id

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        extra = dict(kwargs.get("extra") or {})
        extra.setdefault("job_id", self.job_id)
        kwargs["extra"] = extra
        return f"[job {self.job_id}] {msg}", kwargs


def job_logger(name: str, job_id: str) -> JobLoggerAdapter:
    """Logger, der jeder Nachricht die Job-ID voranstellt.

    Die Job-ID wird auf unkritische Zeichen beschränkt (max. 64), damit keine
    Zeilenumbrüche o. Ä. ins Log gelangen. Nur IDs aus :func:`new_job_id` oder
    Vorgangs-IDs bleiben in den Logs ungeschwärzt sichtbar.
    """
    safe = _SAFE_JOB_ID.sub("_", str(job_id))[:64] or "unbekannt"
    return JobLoggerAdapter(logging.getLogger(name), safe)
