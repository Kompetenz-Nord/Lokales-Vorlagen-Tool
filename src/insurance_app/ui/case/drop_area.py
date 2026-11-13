"""Ablagefläche für Dateien (Drag & Drop) – Stil über ``#DropArea`` und ``dragActive``."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QWidget

from .common import set_dynamic_property


def _local_paths(event) -> list[Path]:
    mime = event.mimeData()
    if mime is None or not mime.hasUrls():
        return []
    return [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]


class DropArea(QFrame):
    """Rahmen, der lokale Dateien annimmt und ``files_dropped`` meldet."""

    files_dropped = Signal(list)  # list[Path]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropArea")
        self.setAcceptDrops(True)
        self.setProperty("dragActive", False)

    def _set_active(self, active: bool) -> None:
        set_dynamic_property(self, "dragActive", active)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt-API)
        if _local_paths(event):
            event.acceptProposedAction()
            self._set_active(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802 (Qt-API)
        if _local_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802 (Qt-API)
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt-API)
        self._set_active(False)
        paths = _local_paths(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.files_dropped.emit(paths)
