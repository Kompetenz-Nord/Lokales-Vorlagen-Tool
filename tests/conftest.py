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
