"""Simulierter Ollama-Dienst für Tests (nur 127.0.0.1, ephemerer Port, synthetische Daten).

Bildet die genutzten REST-Endpunkte nach: /api/version, /api/tags, /api/show,
/api/pull (Stream aus JSON-Zeilen), /api/delete, /api/chat.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MUSTER_DIGEST = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"


def default_models() -> list[dict[str, Any]]:
    return [
        {
            "name": "qwen3-vl:4b",
            "model": "qwen3-vl:4b",
            "modified_at": "2026-11-01T10:00:00.123456789+01:00",
            "size": 3_300_000_000,
            "digest": MUSTER_DIGEST,
            "details": {
                "format": "gguf",
                "family": "qwen3vl",
                "parameter_size": "4.4B",
                "quantization_level": "Q4_K_M",
            },
        },
        {
            "name": "llava:latest",
            "model": "llava:latest",
            "modified_at": "2026-10-01T10:00:00Z",
            "size": 4_700_000_000,
            "digest": "f" * 64,
            "details": {"family": "llama", "parameter_size": "7B", "quantization_level": "Q4_0"},
        },
    ]


ChatHandler = Callable[[dict[str, Any]], tuple[int, Any]]


@dataclass
class FakeState:
    version: str = "0.12.0"
    models: list[dict[str, Any]] = field(default_factory=default_models)
    # Antwort für /api/chat: Funktion(payload) -> (HTTP-Status, JSON-Body oder bytes)
    chat: ChatHandler | None = None
    chat_delay: float = 0.0
    pull_lines: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"status": "pulling manifest"},
            {"status": "pulling a1b2c3", "digest": "sha256:a1b2c3", "total": 1000, "completed": 0},
            {"status": "pulling a1b2c3", "digest": "sha256:a1b2c3", "total": 1000, "completed": 500},
            {"status": "pulling a1b2c3", "digest": "sha256:a1b2c3", "total": 1000, "completed": 1000},
            {"status": "verifying sha256 digest"},
            {"status": "writing manifest"},
            {"status": "success"},
        ]
    )
    pull_line_delay: float = 0.0
    redirect_paths: set[str] = field(default_factory=set)
    requests: list[tuple[str, str, Any]] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)
    pull_disconnected: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, method: str, path: str, body: Any) -> None:
        with self.lock:
            self.requests.append((method, path, body))

    def paths(self) -> list[str]:
        with self.lock:
            return [p for _, p, _ in self.requests]


def _model_matches(entry: dict[str, Any], name: str) -> bool:
    wanted = name if ":" in name else f"{name}:latest"
    return entry["name"] == wanted


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeOllama/1.0"
    protocol_version = "HTTP/1.0"

    @property
    def state(self) -> FakeState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - Signatur der Basisklasse
        return

    # -- Hilfen ---------------------------------------------------------
    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def _send(self, status: int, body: Any) -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self) -> bool:
        if self.path in self.state.redirect_paths:
            self.send_response(302)
            self.send_header("Location", "http://203.0.113.7:11434" + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        return False

    # -- Routen ----------------------------------------------------------
    def do_GET(self) -> None:
        self.state.record("GET", self.path, None)
        if self._redirect():
            return
        if self.path == "/api/version":
            self._send(200, {"version": self.state.version})
        elif self.path == "/api/tags":
            self._send(200, {"models": self.state.models})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self) -> None:
        body = self._read_json()
        self.state.record("DELETE", self.path, body)
        if self.path != "/api/delete":
            self._send(404, {"error": "not found"})
            return
        name = (body or {}).get("model", "")
        before = len(self.state.models)
        self.state.models = [m for m in self.state.models if not _model_matches(m, name)]
        if len(self.state.models) == before:
            self._send(404, {"error": f"model '{name}' not found"})
        else:
            self._send(200, b"")

    def do_POST(self) -> None:
        body = self._read_json()
        self.state.record("POST", self.path, body)
        if self._redirect():
            return
        if self.path == "/api/show":
            name = (body or {}).get("model", "")
            match = next((m for m in self.state.models if _model_matches(m, name)), None)
            if match is None:
                self._send(404, {"error": f"model '{name}' not found"})
            else:
                self._send(
                    200, {"details": match["details"], "modelfile": "# MUSTER", "capabilities": ["vision"]}
                )
        elif self.path == "/api/pull":
            self._pull()
        elif self.path == "/api/chat":
            if self.state.chat_delay and self.state.stop.wait(self.state.chat_delay):
                return
            handler = self.state.chat
            if handler is None:
                self._send(500, {"error": "kein Chat konfiguriert"})
                return
            status, payload = handler(body or {})
            try:
                self._send(status, payload)
            except OSError:
                return
        else:
            self._send(404, {"error": "not found"})

    def _pull(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        try:
            for line in self.state.pull_lines:
                self.wfile.write(json.dumps(line).encode("utf-8") + b"\n")
                self.wfile.flush()
                if self.state.pull_line_delay and self.state.stop.wait(self.state.pull_line_delay):
                    return
        except OSError:
            self.state.pull_disconnected.set()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        return  # Verbindungsabbrüche durch den Client sind in Tests gewollt


class FakeOllama:
    def __init__(self, state: FakeState | None = None) -> None:
        self.state = state or FakeState()
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05})
        self._thread.daemon = True

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeOllama:
        self._thread.start()
        return self

    def stop(self) -> None:
        self.state.stop.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@contextmanager
def running_fake(state: FakeState | None = None) -> Iterator[FakeOllama]:
    fake = FakeOllama(state).start()
    try:
        yield fake
    finally:
        fake.stop()


def free_port() -> int:
    """Port, auf dem (sehr wahrscheinlich) niemand lauscht."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def chat_reply(content: str | dict[str, Any] | list[Any], done_reason: str = "stop") -> ChatHandler:
    """Chat-Antwort im Ollama-Format (``message.content`` als JSON-Text)."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

    def handler(_payload: dict[str, Any]) -> tuple[int, Any]:
        return 200, {
            "model": "qwen3-vl:4b",
            "created_at": "2026-11-13T10:00:00Z",
            "message": {"role": "assistant", "content": text},
            "done": True,
            "done_reason": done_reason,
        }

    return handler
