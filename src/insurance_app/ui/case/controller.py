"""Gemeinsamer Zustand des geöffneten Vorgangs.

* Kurze Aktionen (bestätigen, bearbeiten, auswählen …) laufen im GUI-Thread über :meth:`act`.
  Die Service-Methoden verändern das übergebene ``Case``-Objekt und speichern es.
* Lange Schritte (Texterkennung, lokale KI, Vorschau, PDF-Erzeugung) laufen über :meth:`run`
  im Hintergrund. Sie arbeiten auf einer frisch geladenen Kopie; danach wird der Vorgang
  im GUI-Thread neu geladen. Während der Aufgabe ist ``busy`` gesetzt (Bedienelemente gesperrt).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from ...core.case import Case
from ...core.service import AppService
from .. import dialogs, workers
from .common import error_text

log = logging.getLogger(__name__)

T = TypeVar("T")
ProgressFn = Callable[[int, int, str], None]
WorkFn = Callable[[Case, ProgressFn, threading.Event], Any]

# Fehlerarten, die bei kurzen Aktionen als verständliche Meldung erscheinen
_ACTION_ERRORS: tuple[type[BaseException], ...] = (Exception,)


class CaseController(QObject):
    case_loaded = Signal()  # anderer oder neu geladener Vorgang (auch: keiner)
    data_changed = Signal()  # Werte im geöffneten Vorgang wurden geändert
    busy_changed = Signal(bool)

    def __init__(self, service: AppService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.case: Case | None = None
        self.busy = False
        self._task: workers.Task | None = None

    # ------------------------------------------------------------------
    @property
    def case_id(self) -> str | None:
        return self.case.id if self.case is not None else None

    def open(self, case_id: str) -> None:
        self.case = self.service.load_case(case_id)
        self.case_loaded.emit()

    def reload(self) -> bool:
        """Vorgang neu laden. ``False``, wenn er nicht mehr existiert (z. B. gelöscht)."""
        if self.case is None:
            return False
        try:
            self.case = self.service.load_case(self.case.id)
        except LookupError:
            self.case = None
            self.case_loaded.emit()
            return False
        self.case_loaded.emit()
        return True

    def close_case(self) -> None:
        self.case = None
        self.case_loaded.emit()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.busy_changed.emit(busy)

    # ------------------------------------------------------------------
    def act(
        self,
        parent: QWidget | None,
        fn: Callable[[Case], T],
        *,
        error_title: str = "Aktion nicht möglich",
        notify: bool = True,
    ) -> T | None:
        """Kurze Aktion auf dem geöffneten Vorgang (GUI-Thread). Fehler -> verständliche Meldung."""
        if self.case is None or self.busy:
            return None
        try:
            result = fn(self.case)
        except _ACTION_ERRORS as exc:
            log.info("Aktion abgelehnt: %s", type(exc).__name__)
            dialogs.show_error(parent, error_title, error_text(exc))
            if notify:
                self.data_changed.emit()
            return None
        if notify:
            self.data_changed.emit()
        return result

    def run(
        self,
        parent: QWidget | None,
        title: str,
        fn: WorkFn,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException, bool], None] | None = None,
        *,
        cancellable: bool = True,
        error_title: str = "Schritt nicht abgeschlossen",
    ) -> workers.Task | None:
        """Lange Aufgabe im Hintergrund auf einer frisch geladenen Kopie des Vorgangs."""
        if self.case is None or self.busy:
            return None
        try:
            work = self.service.load_case(self.case.id)
        except LookupError as exc:
            dialogs.show_error(parent, error_title, error_text(exc))
            self.reload()
            return None
        self._set_busy(True)
        state = {"cancelled": False}

        def _fn(progress: ProgressFn, cancel: threading.Event) -> Any:
            try:
                return fn(work, progress, cancel)
            except BaseException:
                # Abbruch nur hier zuverlässig erkennbar: Das Schließen des Fortschrittsdialogs
                # setzt das Abbruch-Signal später ebenfalls.
                state["cancelled"] = cancel.is_set()
                raise

        def _done(result: Any) -> None:
            self._task = None
            self._set_busy(False)
            self.reload()
            if on_done is not None:
                on_done(result)

        def _failed(exc: BaseException) -> None:
            self._task = None
            cancelled = bool(state["cancelled"])
            self._set_busy(False)
            self.reload()
            if on_error is not None:
                on_error(exc, cancelled)
            elif cancelled:
                dialogs.show_info(parent, "Abgebrochen", "Der Schritt wurde abgebrochen.")
            else:
                dialogs.show_error(parent, error_title, error_text(exc))

        task = workers.run_task(parent, title, _fn, _done, _failed, cancellable=cancellable)
        self._task = task
        return task
