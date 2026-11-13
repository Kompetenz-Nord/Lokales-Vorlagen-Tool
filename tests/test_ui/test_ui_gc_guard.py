"""Speicherbereinigung nur im Fenster-Thread (Schutz vor Abstürzen durch Worker-Threads)."""

from __future__ import annotations

import gc
import threading
import weakref

from insurance_app.ui.app import create_application
from insurance_app.ui.gc_guard import MainThreadGc, install_main_thread_gc


class _Node:
    def __init__(self) -> None:
        self.me = self  # Referenzkreis: nur die Speicherbereinigung räumt das Objekt ab
        self.collected_in: list[int] = []


def test_automatic_gc_is_off_and_guard_is_installed_once(qapp):
    guard = install_main_thread_gc(qapp)
    assert isinstance(guard, MainThreadGc)
    assert install_main_thread_gc(qapp) is guard  # mehrfacher Aufruf legt keinen zweiten Zeitgeber an
    assert create_application([]) is qapp and install_main_thread_gc(qapp) is guard
    assert not gc.isenabled()


def test_cycles_are_collected_by_the_timer_in_the_gui_thread(qapp, qtbot):
    install_main_thread_gc(qapp)
    seen: list[int] = []
    node = _Node()
    ref = weakref.ref(node)
    weakref.finalize(node, lambda: seen.append(threading.get_ident()))
    del node

    # Ein Worker legt sehr viele Objekte an – früher löste das die Bereinigung im Worker aus
    def churn() -> None:
        for _ in range(200):
            junk = [[i] for i in range(2000)]
            for item in junk:
                item.append(item)

    worker = threading.Thread(target=churn)
    worker.start()
    worker.join()
    assert ref() is not None and not seen  # im Worker wurde nichts abgeräumt

    qtbot.waitUntil(lambda: bool(seen), timeout=10_000)
    assert seen == [threading.main_thread().ident] and ref() is None


def test_collect_reaches_older_generations_regularly(qapp):
    guard = install_main_thread_gc(qapp)
    start = guard.ticks
    generations = [guard.collect() for _ in range(60)]
    assert guard.ticks == start + 60
    assert 2 in generations and generations.count(1) >= 5 and generations.count(0) >= 50
