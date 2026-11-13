"""Warteschlange einer Kundenmappe: Dateien nacheinander lesen und je Vertrag zuordnen.

* Die Zeilen erscheinen sofort als „Wartet“; während des Laufs wird die gerade gelesene Datei
  als „Wird ausgewertet“ markiert, danach steht das Ergebnis je Datei in der Zeile.
* Passwortgeschützte PDFs: Nach dem Lauf fragt ``dialogs.ask_password`` je Datei nach dem Passwort;
  diese Dateien werden danach erneut eingelesen. Passwörter bleiben nur im Arbeitsspeicher
  (nie gespeichert, nie protokolliert) und werden am Ende verworfen.
* Abbruch über den Fortschrittsdialog: noch nicht gelesene Dateien bleiben „Abgebrochen“.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QWidget

from ...core.service import AppService, BatchItem, BatchItemStatus, BatchResult
from .. import dialogs, workers
from ..case.common import error_text

log = logging.getLogger(__name__)

RETRY_MESSAGE = "Passwort eingegeben – die Datei wird erneut gelesen."
SKIPPED_MESSAGE = "Übersprungen – es wurde kein Passwort eingegeben."


class _Progress(QObject):
    """Brücke vom Arbeits-Thread in den GUI-Thread (Signal wird dort zugestellt)."""

    file_started = Signal(int)


class BundleImport(QObject):
    """Ein Import-Lauf (inkl. Passwort-Nachfragen) für eine Kundenmappe."""

    changed = Signal()  # Zeilen der Warteschlange haben sich geändert
    finished = Signal(object)  # list[BatchItem] – die ganze Warteschlange

    def __init__(
        self,
        parent: QWidget,
        service: AppService,
        bundle_id: str,
        paths: list[Path],
        queue: list[BatchItem] | None = None,
    ) -> None:
        super().__init__(parent)
        self._parent = parent
        self.service = service
        self.bundle_id = bundle_id
        self.queue: list[BatchItem] = queue if queue is not None else []
        self.case_ids: list[str] = []
        self.task: workers.Task | None = None
        self.running = False
        self.cancelled = False
        self._passwords: dict[str, str] = {}  # nur im Arbeitsspeicher, nie gespeichert
        self._skipped: set[str] = set()
        self._current: list[str] = []
        self._paths = [str(p) for p in paths]
        index = {item.path: item for item in self.queue}
        for p in self._paths:
            item = index.get(p)
            if item is None:
                item = BatchItem(file_name=Path(p).name, path=p)
                self.queue.append(item)
                index[p] = item
            else:  # dieselbe Datei erneut abgelegt -> Zeile wiederverwenden
                self._reset(item)

    # ------------------------------------------------------------------
    @staticmethod
    def _reset(item: BatchItem, message: str = "") -> None:
        item.status = BatchItemStatus.WAITING
        item.message = message
        item.case_id = None

    def _item(self, path: str) -> BatchItem | None:
        return next((i for i in self.queue if i.path == path), None)

    def start(self) -> None:
        self._run(self._paths)

    def _run(self, paths: list[str]) -> None:
        self.running = True
        self._current = list(paths)
        bridge = _Progress(self)
        bridge.file_started.connect(self._on_file_started)
        passwords = {p: self._passwords[p] for p in paths if p in self._passwords} or None
        service, bundle_id = self.service, self.bundle_id
        count = len(paths)

        def work(progress: Callable[[int, int, str], None], cancel: threading.Event) -> BatchResult:
            def report(current: int, total: int, text: str) -> None:
                progress(current, total, text)
                if 0 <= current < count:  # erste Phase: Datei Nr. ``current`` wird gelesen
                    bridge.file_started.emit(current)

            return service.import_into_bundle(
                bundle_id, [Path(p) for p in paths], passwords=passwords, progress=report, cancel=cancel
            )

        self.changed.emit()
        self.task = workers.run_task(
            self._parent,
            "Dokumente werden nacheinander gelesen und den Verträgen zugeordnet (lokal) …",
            work,
            self._done,
            self._failed,
        )

    @Slot(int)
    def _on_file_started(self, index: int) -> None:
        if 0 <= index < len(self._current):
            item = self._item(self._current[index])
            if item is not None and item.status == BatchItemStatus.WAITING:
                item.status = BatchItemStatus.RUNNING
                item.message = ""
                self.changed.emit()

    # ------------------------------------------------------------------
    def _done(self, result: BatchResult) -> None:
        self.task = None
        for res in result.items:
            item = self._item(res.path)
            if item is None:
                continue
            item.status = res.status
            item.message = res.message
            item.case_id = res.case_id
            item.doc_type = res.doc_type
            item.insurer = res.insurer
        for cid in result.case_ids:
            if cid not in self.case_ids:
                self.case_ids.append(cid)
        self.cancelled = self.cancelled or any(i.status == BatchItemStatus.CANCELLED for i in result.items)
        self.changed.emit()
        if self.cancelled:
            self._finish()
            return
        retry: list[str] = []
        for res in result.items:
            if res.status != BatchItemStatus.NEEDS_PASSWORD or res.path in self._skipped:
                continue
            item = self._item(res.path)
            password = dialogs.ask_password(self._parent, res.file_name, wrong=res.path in self._passwords)
            if password:
                self._passwords[res.path] = password
                retry.append(res.path)
                if item is not None:
                    self._reset(item, RETRY_MESSAGE)
            else:
                self._skipped.add(res.path)
                if item is not None:
                    item.message = SKIPPED_MESSAGE
        if retry:
            self._run(retry)
            return
        self._finish()

    def _failed(self, exc: BaseException) -> None:
        self.task = None
        log.info("Stapel nicht abgeschlossen: %s", type(exc).__name__)
        text = error_text(exc)
        for p in self._current:
            item = self._item(p)
            if item is not None and item.status in (BatchItemStatus.WAITING, BatchItemStatus.RUNNING):
                item.status = BatchItemStatus.REJECTED
                item.message = f"Nicht verarbeitet: {text}"
        self.changed.emit()
        dialogs.show_error(self._parent, "Dokumente nicht vollständig übernommen", text)
        self._finish()

    def _finish(self) -> None:
        self._passwords.clear()
        self.running = False
        self.finished.emit(self.queue)

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel.set()


def summary_text(items: list[BatchItem]) -> str:
    """Kurzfassung der Warteschlange, z. B. „3 von 4 Dateien fertig · 1 Passwort erforderlich“."""
    total = len(items)
    if not total:
        return "Noch keine Dateien in der Warteschlange."
    counts: dict[BatchItemStatus, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    parts = [f"{counts.get(BatchItemStatus.DONE, 0)} von {total} Dateien fertig"]
    for status, label in _SUMMARY_LABELS:
        if counts.get(status):
            parts.append(f"{counts[status]} {label}")
    return " · ".join(parts)


_SUMMARY_LABELS = (
    (BatchItemStatus.RUNNING, "werden ausgewertet"),
    (BatchItemStatus.WAITING, "warten"),
    (BatchItemStatus.NEEDS_PASSWORD, "mit Passwort"),
    (BatchItemStatus.REJECTED, "nicht verarbeitbar"),
    (BatchItemStatus.CANCELLED, "abgebrochen"),
)
