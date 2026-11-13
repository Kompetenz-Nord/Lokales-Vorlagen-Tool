"""Temporäre Arbeitsordner je Job (werden immer wieder entfernt).

* :class:`TempWorkspace` legt ``<base>/<job_id>-<zufall>`` mit Rechten ``0o700`` an
  und löscht ihn beim Verlassen – auch bei einer Ausnahme und auch dann, wenn
  darin schreibgeschützte Dateien liegen.
* :func:`cleanup_stale` räumt beim Programmstart Reste abgebrochener Jobs auf.
* :func:`acquire_instance_lock` hält eine Sperrdatei im Temp-Ordner, solange die
  App läuft (Betriebssystem-Sperre, wird beim Beenden – auch bei einem Absturz –
  automatisch frei). :func:`cleanup_temp_dir` entfernt mit gehaltener Sperre alle
  Reste der eigenen Job-Ordner sofort (z. B. befüllte PDFs nach einem Absturz),
  sonst – während eine andere Instanz läuft – nur Einträge älter als 24 Stunden.
"""

from __future__ import annotations

import contextlib
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

__all__ = [
    "APP_JOB_PATTERNS",
    "LOCK_FILE_NAME",
    "InstanceLock",
    "TempWorkspace",
    "acquire_instance_lock",
    "cleanup_stale",
    "cleanup_temp_dir",
    "force_remove",
    "force_rmtree",
]

log = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")

LOCK_FILE_NAME = ".instanz.lock"

# Eigene Job-Ordner der App im Temp-Ordner (werden mit gehaltener Sperre sofort entfernt)
APP_JOB_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^vorschau$"),  # Vorschau-Dateien je Vorgang
    re.compile(r"^musterfall$"),  # Musterfall-Erzeugung
    re.compile(r"^export-[A-Za-z0-9_-]+$"),  # Arbeitsordner beim Export
    re.compile(r"^testbefuellung-[A-Za-z0-9_-]+$"),  # Testbefüllung im Vorlagen-Studio
    re.compile(r"^docling-[0-9a-f]{12}$"),  # temporäre Seitenbilder der Docling-Texterkennung
    re.compile(r"^J-[A-Za-z0-9_.-]+$"),  # Job-Arbeitsordner
    re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*-[0-9a-f]{12}$"),  # TempWorkspace(<job>-<zufall>)
)


def _make_writable(path: str) -> None:
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return
    if stat.S_ISLNK(mode):
        return
    wanted = stat.S_IRWXU if stat.S_ISDIR(mode) else (stat.S_IRUSR | stat.S_IWUSR)
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IMODE(mode) | wanted)


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
        self.job_id = _SAFE_ID.sub("_", str(job_id)).lstrip(".")[:48] or "job"
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
        if entry.name == LOCK_FILE_NAME:
            continue  # Sperrdatei einer laufenden Instanz nie entfernen
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


# ---------------------------------------------------------------------------
# Einzelinstanz-Sperre und Aufräumen beim Start
# ---------------------------------------------------------------------------


class InstanceLock:
    """Sperrdatei im Temp-Ordner. ``acquired`` ist ``False``, wenn eine andere Instanz läuft.

    Die Sperre ist eine Betriebssystem-Sperre (``flock`` bzw. ``msvcrt.locking``):
    Sie wird beim Beenden des Prozesses – auch nach einem Absturz – automatisch frei.
    """

    def __init__(self, path: Path, fd: int | None) -> None:
        self.path = Path(path)
        self._fd = fd

    @property
    def acquired(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        """Sperre freigeben (idempotent). Die Sperrdatei bleibt liegen."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        with contextlib.suppress(OSError):
            _unlock(fd)
        with contextlib.suppress(OSError):
            os.close(fd)

    def __enter__(self) -> InstanceLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.release()
        return False

    def __del__(self) -> None:  # pragma: no cover - Sicherheitsnetz
        with contextlib.suppress(Exception):
            self.release()


def _try_lock(fd: int) -> bool:
    if os.name == "nt":  # pragma: no cover - nur Windows
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        import fcntl
    except ImportError:  # pragma: no cover - ohne Sperrmöglichkeit konservativ
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - nur Windows
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def acquire_instance_lock(temp_dir: Path) -> InstanceLock:
    """Einzelinstanz-Sperre im Temp-Ordner versuchen (blockiert nie).

    Gibt immer ein :class:`InstanceLock` zurück; ``acquired`` sagt, ob diese Instanz
    die einzige ist. Kann die Sperrdatei nicht angelegt werden, gilt die Sperre als
    nicht erhalten (dann wird vorsichtig nur nach Alter aufgeräumt).
    """
    base = Path(temp_dir)
    path = base / LOCK_FILE_NAME
    try:
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    except OSError as exc:
        log.warning("Sperrdatei im Temp-Ordner nicht verfügbar (%s)", type(exc).__name__)
        return InstanceLock(path, None)
    if not _try_lock(fd):
        os.close(fd)
        log.info("Eine weitere Instanz der App läuft – Temp-Reste werden nur nach Alter entfernt")
        return InstanceLock(path, None)
    return InstanceLock(path, fd)


def _is_app_job_entry(name: str) -> bool:
    return any(p.match(name) for p in APP_JOB_PATTERNS)


def cleanup_temp_dir(base: Path, *, exclusive: bool, max_age_hours: float = 24) -> int:
    """Temp-Ordner beim Start aufräumen.

    * ``exclusive=True`` (Einzelinstanz-Sperre gehalten): Reste der eigenen
      Job-Ordner (:data:`APP_JOB_PATTERNS`) werden unabhängig vom Alter entfernt,
      alles andere nach Alter.
    * ``exclusive=False`` (eine andere Instanz läuft): nur Einträge älter als
      ``max_age_hours`` – laufende Jobs der anderen Instanz bleiben unberührt.

    Die Sperrdatei bleibt immer erhalten. Gibt die Anzahl entfernter Einträge zurück.
    """
    base = Path(base)
    removed = 0
    if exclusive and base.is_dir():
        for entry in list(base.iterdir()):
            if entry.name == LOCK_FILE_NAME or not _is_app_job_entry(entry.name):
                continue
            try:
                if entry.is_dir() and not entry.is_symlink():
                    force_rmtree(entry)
                else:
                    force_remove(entry)
                removed += 1
            except OSError as exc:
                log.warning("Temporärer Job-Rest nicht entfernt (%s)", type(exc).__name__)
    return removed + cleanup_stale(base, max_age_hours=max_age_hours)
