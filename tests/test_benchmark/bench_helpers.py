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


# ---------------------------------------------------------------------------
# Minimaler Ollama-Ersatz (nur 127.0.0.1, synthetische Antworten)
# ---------------------------------------------------------------------------


class FakeOllama:
    """Beantwortet /api/version, /api/tags und /api/chat mit festen MUSTER-Daten."""

    def __init__(self, fields: list[dict], model: str = "qwen3-vl:4b") -> None:
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.chat_calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def _send(self, body: dict) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/api/version":
                    self._send({"version": "0.0-muster"})
                else:
                    self._send({"models": [{"name": model, "model": model, "digest": "a" * 64}]})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                outer.chat_calls += 1
                content = json.dumps({"fields": fields}, ensure_ascii=False)
                self._send(
                    {"model": model, "message": {"role": "assistant", "content": content}, "done": True}
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> FakeOllama:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
