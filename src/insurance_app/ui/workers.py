"""Hintergrundaufgaben für lange Schritte (OCR, Vorschau, Export, Modell-Download).

Verwendung::

    task = run_task(self, "Texterkennung läuft …", lambda progress, cancel: svc.process_documents(case, progress=progress, cancel=cancel),
                    on_done=self._after, on_error=self._show_error)

* ``fn(progress, cancel)`` läuft in einem Thread des ``QThreadPool``.
* ``progress(current, total, text)`` darf aus dem Thread aufgerufen werden.
* ``cancel`` ist ein ``threading.Event`` (Schaltfläche „Abbrechen“ im Fortschrittsdialog).
* ``on_done(result)`` / ``on_error(exc)`` laufen im GUI-Thread.
Fehlermeldungen: ``user_message`` der Fachausnahmen wird bevorzugt angezeigt; keine Dokumentinhalte loggen.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

log = logging.getLogger(__name__)

TaskFn = Callable[[Callable[[int, int, str], None], threading.Event], Any]


class _Signals(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(object)


class Task(QRunnable):
    def __init__(self, fn: TaskFn) -> None:
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:  # läuft im Worker-Thread
        try:
            result = self.fn(self._progress, self.cancel)
        except BaseException as exc:  # noqa: BLE001 – an GUI weiterreichen
            log.warning("Hintergrundaufgabe fehlgeschlagen: %s", type(exc).__name__)
            self.signals.failed.emit(exc)
        else:
            self.signals.done.emit(result)

    def _progress(self, current: int, total: int, text: str) -> None:
        self.signals.progress.emit(int(current), int(max(total, 1)), str(text))


_running: set[Task] = set()  # Referenzen halten, bis die Aufgabe beendet ist


def user_message(exc: BaseException) -> str:
    msg = getattr(exc, "user_message", None)
    if msg:
        return str(msg)
    if isinstance(exc, (ValueError, PermissionError, FileNotFoundError)) and str(exc):
        return str(exc)
    return f"Unerwarteter Fehler ({type(exc).__name__}). Details stehen im lokalen Protokoll."


def run_task(
    parent: QWidget | None,
    title: str,
    fn: TaskFn,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
    *,
    show_progress: bool = True,
    cancellable: bool = True,
) -> Task:
    task = Task(fn)
    dialog: QProgressDialog | None = None
    if show_progress and parent is not None:
        dialog = QProgressDialog(title, "Abbrechen" if cancellable else "", 0, 0, parent)
        dialog.setWindowTitle("Bitte warten")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(300)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        if not cancellable:
            dialog.setCancelButton(None)
        dialog.canceled.connect(task.cancel.set)

        def _on_progress(cur: int, tot: int, text: str) -> None:
            if dialog is not None:
                dialog.setMaximum(tot)
                dialog.setValue(min(cur, tot))
                dialog.setLabelText(f"{title}\n{text}")

        task.signals.progress.connect(_on_progress)

    def _finish() -> None:
        _running.discard(task)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _done(result: Any) -> None:
        _finish()
        if on_done:
            on_done(result)

    def _failed(exc: BaseException) -> None:
        _finish()
        if on_error:
            on_error(exc)

    task.signals.done.connect(_done)
    task.signals.failed.connect(_failed)
    _running.add(task)
    QThreadPool.globalInstance().start(task)
    return task


def wait_for_all(timeout_ms: int = 60_000) -> bool:
    """Für Tests/Beenden: auf laufende Aufgaben warten."""
    return QThreadPool.globalInstance().waitForDone(timeout_ms)
