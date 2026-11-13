"""Gemeinsame Test-Fixtures. Alle Testdaten sind synthetisch."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from insurance_app.core.paths import AppPaths, resource_root  # noqa: E402

ROOT = resource_root()
ORIGINALS = ROOT / "assets" / "templates" / "originals"
SL13065_PDF = ORIGINALS / "SL13065_Freigabeerklaerung_Direktversicherung_2024-09.pdf"
SL18477_PDF = ORIGINALS / "SL18477_Uebernahmeerklaerung_Direktversicherung_2024-09.pdf"


@pytest.fixture()
def app_paths(tmp_path: Path) -> AppPaths:
    """Isolierte Laufzeitpfade je Test (keine Daten im Projektordner)."""
    return AppPaths.for_testing(tmp_path)


@pytest.fixture(scope="session")
def originals_available() -> bool:
    return SL13065_PDF.exists() and SL18477_PDF.exists()


@pytest.fixture(autouse=True)
def _main_thread_gc_for_ui_tests(request):
    """Oberflächentests wie die App: Speicherbereinigung nur im Fenster-Thread.

    Ohne das gab es sporadische Abstürze, wenn Qt-Objekte in einem Worker-Thread
    freigegeben wurden (siehe ``insurance_app/ui/gc_guard.py``).
    """
    if "test_ui" not in request.node.path.parts:
        yield
        return
    from insurance_app.ui.gc_guard import install_main_thread_gc, uninstall_main_thread_gc

    qapp = request.getfixturevalue("qapp")
    install_main_thread_gc(qapp)
    yield
    uninstall_main_thread_gc(qapp)
