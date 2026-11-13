"""Gemeinsame Einstellungen der Oberflächentests."""

from __future__ import annotations

import pytest

from insurance_app.ui.gc_guard import install_main_thread_gc, uninstall_main_thread_gc


@pytest.fixture(scope="session", autouse=True)
def _main_thread_gc(qapp):
    """Wie in der App: Speicherbereinigung nur im Fenster-Thread (sonst sporadische Abstürze)."""
    install_main_thread_gc(qapp)
    yield
    uninstall_main_thread_gc(qapp)
