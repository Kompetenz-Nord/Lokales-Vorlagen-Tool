"""Hilfen für die Benchmark-Tests: Skript als Modul laden, Pfade zum Testsatz."""

from __future__ import annotations

import importlib.util
import socket
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "benchmark_extraction.py"
FIXTURE_SET = ROOT / "tests" / "fixtures" / "benchmark"
MODULE_NAME = "benchmark_extraction_under_test"


@lru_cache(maxsize=1)
def bench() -> ModuleType:
    """``scripts/benchmark_extraction.py`` als Modul (einmal je Testlauf)."""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module  # nötig für dataclasses mit verzögerten Annotationen
    spec.loader.exec_module(module)
    return module


def free_loopback_port() -> int:
    """Ein Port auf 127.0.0.1, an dem (sehr wahrscheinlich) niemand lauscht."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
