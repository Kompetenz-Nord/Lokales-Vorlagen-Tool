"""Speicherbereinigung (Garbage Collection) nur im Fenster-Thread.

Hintergrund: Lange Schritte (Texterkennung, Vorschau, Export) laufen in Worker-Threads.
Pythons automatische Speicherbereinigung startet in dem Thread, der gerade viele Objekte
anlegt – also oft im Worker. Räumt sie dort Fenster-Objekte (Qt) aus Referenzkreisen ab,
werden diese im falschen Thread zerstört. Qt erlaubt das nicht; die Folge kann ein Absturz
der ganzen App sein (in Tests als „Segmentation fault“ beobachtet).

Lösung: Die automatische Bereinigung wird abgeschaltet und stattdessen regelmäßig von
einem Zeitgeber im Fenster-Thread ausgeführt. Objekte ohne Referenzkreis gibt Python wie
gewohnt sofort frei; nur Kreise warten bis zum nächsten Takt.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer

__all__ = ["DEFAULT_INTERVAL_MS", "MainThreadGc", "install_main_thread_gc", "uninstall_main_thread_gc"]

#: Abstand der Bereinigung in Millisekunden.
DEFAULT_INTERVAL_MS = 500
#: Jede wievielte Bereinigung auch ältere Objekte prüft (mittlere / alle Generationen).
_MIDDLE_EVERY = 10
_FULL_EVERY = 60
_PROPERTY = "_lvt_main_thread_gc"


class MainThreadGc(QObject):
    """Führt ``gc.collect`` taktweise im Thread der Anwendung aus."""

    def __init__(self, app: QCoreApplication, interval_ms: int = DEFAULT_INTERVAL_MS) -> None:
        super().__init__(app)
        self.ticks = 0
        self._was_enabled = gc.isenabled()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.setInterval(max(50, int(interval_ms)))
        self._timer.timeout.connect(self.collect)
        self._timer.start()

    def collect(self) -> int:
        """Ein Takt: meist nur junge Objekte, regelmäßig auch ältere. Gibt die Generation zurück."""
        if QThread.currentThread() is not self.thread():  # pragma: no cover - reine Absicherung
            return -1
        self.ticks += 1
        generation = 2 if self.ticks % _FULL_EVERY == 0 else 1 if self.ticks % _MIDDLE_EVERY == 0 else 0
        gc.collect(generation)
        return generation

    def stop(self) -> None:
        """Zeitgeber anhalten und die automatische Bereinigung wiederherstellen."""
        self._timer.stop()
        if self._was_enabled:
            gc.enable()


def install_main_thread_gc(
    app: QCoreApplication | None = None, interval_ms: int = DEFAULT_INTERVAL_MS
) -> MainThreadGc | None:
    """Bereinigung auf den Fenster-Thread legen (mehrfacher Aufruf ist unschädlich)."""
    app = app or QCoreApplication.instance()
    if app is None:
        return None
    existing = app.property(_PROPERTY)
    if isinstance(existing, MainThreadGc):
        return existing
    guard = MainThreadGc(app, interval_ms)
    app.setProperty(_PROPERTY, guard)
    return guard


def uninstall_main_thread_gc(app: QCoreApplication | None = None) -> None:
    app = app or QCoreApplication.instance()
    if app is None:
        return
    guard = app.property(_PROPERTY)
    if isinstance(guard, MainThreadGc):
        guard.stop()
        guard.deleteLater()
        app.setProperty(_PROPERTY, None)
