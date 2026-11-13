"""Temporäre Arbeitsordner je Job (werden immer wieder entfernt).

* :class:`TempWorkspace` legt ``<base>/<job_id>-<zufall>`` mit Rechten ``0o700`` an
  und löscht ihn beim Verlassen – auch bei einer Ausnahme und auch dann, wenn
  darin schreibgeschützte Dateien liegen.
* :func:`cleanup_stale` räumt beim Programmstart Reste abgebrochener Jobs auf.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import stat
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import Any

__all__ = ["TempWorkspace", "cleanup_stale", "force_remove", "force_rmtree"]

log = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


def _make_writable(path: str) -> None:
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return
    if stat.S_ISLNK(mode):
        return
    wanted = stat.S_IRWXU if stat.S_ISDIR(mode) else (stat.S_IRUSR | stat.S_IWUSR)
    try:
        os.chmod(path, stat.S_IMODE(mode) | wanted)
    except OSError:
        pass


def _on_rm_error(func: Any, path: str, _exc: Any) -> None:
    """Fehlerbehandlung für ``shutil.rmtree``: Schreibschutz aufheben und erneut versuchen."""
    parent = os.path.dirname(path)
    if parent:
        _make_writable(parent)
    _make_writable(path)
    func(path)


def force_rmtree(path: Path) -> None:
    """Verzeichnis samt Inhalt löschen, auch mit schreibgeschützten Dateien (Windows).

    Symbolische Links werden nicht verfolgt (nur der Link selbst wird entfernt).
    Ein nicht vorhandener Pfad ist kein Fehler.
    """
    path = Path(path)
    if not os.path.lexists(path):
        return
    if path.is_symlink() or not path.is_dir():
        force_remove(path)
        return
    for root, dirs, files in os.walk(path, followlinks=False):
        _make_writable(root)
        for name in dirs:
            _make_writable(os.path.join(root, name))
        for name in files:
            _make_writable(os.path.join(root, name))
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_rm_error)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=_on_rm_error)


def force_remove(path: Path) -> None:
    """Einzelne Datei (oder Link) löschen, Schreibschutz vorher aufheben."""
    path = Path(path)
    if not os.path.lexists(path):
        return
    try:
        path.unlink()
    except PermissionError:
        _make_writable(str(path))
        if path.parent:
            _make_writable(str(path.parent))
        path.unlink()


class TempWorkspace:
    """Kontextmanager für einen privaten, temporären Arbeitsordner.

    Beispiel::

        with TempWorkspace(paths.temp_dir, job_id) as work:
            (work / "seite1.png").write_bytes(...)
        # Ordner ist jetzt entfernt
    """

    def __init__(self, base: Path, job_id: str) -> None:
        self.base = Path(base)
        self.job_id = _SAFE_ID.sub("_", str(job_id))[:48] or "job"
        self._path: Path | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            raise RuntimeError("Arbeitsordner ist nicht aktiv (nur innerhalb von 'with' verfügbar).")
        return self._path

    def __enter__(self) -> Path:
        self.base.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            candidate = self.base / f"{self.job_id}-{secrets.token_hex(6)}"
            try:
                candidate.mkdir(mode=0o700)
            except FileExistsError:
                continue
            break
        else:  # pragma: no cover - praktisch ausgeschlossen
            raise FileExistsError("Temporärer Arbeitsordner konnte nicht angelegt werden.")
        if os.name == "posix":
            os.chmod(candidate, 0o700)  # unabhängig von der umask
        self._path = candidate
        return candidate

    def cleanup(self) -> bool:
        """Ordner entfernen. Gibt ``True`` zurück, wenn er danach nicht mehr existiert."""
        if self._path is None:
            return True
        try:
            force_rmtree(self._path)
        except OSError as exc:
            log.warning("Temporärer Arbeitsordner konnte nicht gelöscht werden (%s)", type(exc).__name__)
            return False
        self._path = None
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.cleanup()
        return False  # Ausnahmen nie verschlucken


def cleanup_stale(base: Path, max_age_hours: float = 24) -> int:
    """Einträge in ``base`` löschen, die älter als ``max_age_hours`` sind.

    Gibt die Anzahl entfernter Einträge zurück. Fehler einzelner Einträge werden
    protokolliert (nur Fehlerklasse) und übersprungen.
    """
    base = Path(base)
    if not base.is_dir():
        return 0
    cutoff = time.time() - max(0.0, float(max_age_hours)) * 3600.0
    removed = 0
    for entry in list(base.iterdir()):
        try:
            mtime = entry.lstat().st_mtime
            if mtime > cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                force_rmtree(entry)
            else:
                force_remove(entry)
            removed += 1
        except OSError as exc:
            log.warning("Alter temporärer Eintrag nicht entfernt (%s)", type(exc).__name__)
    if removed:
        log.info("Temporäre Reste aufgeräumt: %d", removed)
    return removed
