"""Ollama-Client gegen einen simulierten lokalen Dienst (keine echten Modelle, keine Netzwerkzugriffe)."""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

import pytest
from lai_fake_ollama import chat_reply, free_port, running_fake

from insurance_app.local_ai.ollama_client import (
    ConfirmationRequiredError,
    ConsentRequiredError,
    InvalidModelNameError,
    InvalidModelResponseError,
    ModelNotFoundError,
    OllamaClient,
    OllamaRequestError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    PullCancelledError,
    validate_model_name,
)
from insurance_app.security.network_guard import (
    NetworkBlockedError,
    install_network_guard,
    uninstall_network_guard,
)


@pytest.fixture()
def fake():
    with running_fake() as server:
        yield server


# ---------------------------------------------------------------------------
# Status und Modelle
# ---------------------------------------------------------------------------


def test_version_tags_show(fake):
    client = OllamaClient(fake.url, timeout=5)
    assert client.is_running() is True
    assert client.version() == "0.12.0"
    models = client.list_models()
    assert [m.name for m in models] == ["qwen3-vl:4b", "llava:latest"]
    qwen = models[0]
    assert qwen.size_bytes == 3_300_000_000
    assert qwen.digest and qwen.digest.startswith("a1b2c3")
    assert qwen.family == "qwen3vl"
    assert qwen.parameter_size == "4.4B"
    assert qwen.quantization == "Q4_K_M"
    assert qwen.modified_at.startswith("2026-11-01")
    details = client.show_model("qwen3-vl:4b")
    assert details["capabilities"] == ["vision"]
    assert ("POST", "/api/show", {"model": "qwen3-vl:4b"}) in fake.state.requests


def test_has_model_uses_latest_as_default_tag(fake):
    client = OllamaClient(fake.url)
    assert client.has_model("qwen3-vl:4b")
    assert client.has_model("llava")
    assert not client.has_model("qwen3-vl:8b")


def test_show_missing_model_raises_model_not_found(fake):
    client = OllamaClient(fake.url)
    with pytest.raises(ModelNotFoundError) as info:
        client.show_model("qwen3-vl:8b")
    assert "nicht vorhanden" in str(info.value)


def test_server_down_raises_unavailable():
    client = OllamaClient(f"http://127.0.0.1:{free_port()}", timeout=2)
    assert client.is_running() is False
    assert client.version() is None
    with pytest.raises(OllamaUnavailableError) as info:
        client.list_models()
    assert "nicht erreichbar" in str(info.value)


def test_redirects_are_refused(fake):
    fake.state.redirect_paths = {"/api/version", "/api/show"}
    client = OllamaClient(fake.url)
    assert client.version() is None
    with pytest.raises(OllamaRequestError) as info:
        client.show_model("qwen3-vl:4b")
    assert "weitergeleitet" in str(info.value)
    # Kein Folgeaufruf an das Weiterleitungsziel
    assert all(not p.startswith("http") for p in fake.state.paths())


# ---------------------------------------------------------------------------
# Loopback-Pflicht und Proxys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:11434",
        "http://192.168.1.10:11434",
        "http://user@127.0.0.1:11434",
        "http://user:pass@localhost:11434",
        "ftp://127.0.0.1:11434",
        "http://127.0.0.1.example.com:11434",
        "http://0.0.0.0:11434",
        "http://[::2]:11434",
        "file:///etc/passwd",
        "127.0.0.1:11434",
        "",
    ],
)
def test_non_loopback_urls_rejected(url):
    with pytest.raises(NetworkBlockedError):
        OllamaClient(url)


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://[::1]:11434", "http://127.0.0.5:11434/"])
def test_loopback_urls_accepted(url):
    client = OllamaClient(url)
    assert not client.base_url.endswith("/")


def test_url_is_rechecked_before_every_request(fake):
    client = OllamaClient(fake.url)
    client.base_url = "http://203.0.113.5:11434"  # nachträglich manipuliert
    with pytest.raises(NetworkBlockedError):
        client.list_models()


def test_environment_proxies_are_ignored(fake, monkeypatch):
    bogus = "http://203.0.113.9:3128"
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(var, bogus)
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "")
    install_network_guard()
    try:
        # Ein Standard-Opener würde den (gesperrten) Proxy verwenden …
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(fake.url + "/api/version", timeout=2)  # noqa: S310
        # … der Client geht direkt an den lokalen Dienst.
        client = OllamaClient(fake.url, timeout=5)
        assert client.version() == "0.12.0"
        assert client.has_model("qwen3-vl:4b")
    finally:
        uninstall_network_guard()


# ---------------------------------------------------------------------------
# Modellnamen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "qwen3-vl:235b-cloud",
        "gpt-oss:120b-cloud",
        "model:cloud",
        "cloud",
        "deepseek-v3.1:671b-cloud",
        "registry.ollama.ai/library/qwen3-vl:4b",
        "hf.co/someone/model",
        "someone/model",
        "Qwen3-VL:4b",
        "qwen3 vl",
        "",
        "../model",
        "qwen3-vl:4b;rm",
        "qwen3-vl:4b:extra",
        "-qwen",
        "a" * 200,
    ],
)
def test_invalid_model_names_rejected(name):
    with pytest.raises(InvalidModelNameError):
        validate_model_name(name)


@pytest.mark.parametrize(
    "name", ["qwen3-vl:4b", "qwen3-vl:8b", "llava", "llama3.2-vision:11b", "mycloudy:1b"]
)
def test_valid_model_names(name):
    assert validate_model_name(f"  {name} ") == name


def test_cloud_model_rejected_before_any_request(fake):
    client = OllamaClient(fake.url)
    with pytest.raises(InvalidModelNameError):
        client.pull_model("qwen3-vl:235b-cloud", user_consented=True)
    with pytest.raises(InvalidModelNameError):
        client.chat_structured("model:cloud", "s", "u", [], {"type": "object"})
    assert fake.state.requests == []


# ---------------------------------------------------------------------------
# Download (pull)
# ---------------------------------------------------------------------------


def test_pull_requires_consent(fake):
    client = OllamaClient(fake.url)
    for consent in (False, None, "ja", 1):
        with pytest.raises(ConsentRequiredError):
            client.pull_model("qwen3-vl:8b", user_consented=consent)  # type: ignore[arg-type]
    assert fake.state.requests == []


def test_pull_streams_progress(fake):
    client = OllamaClient(fake.url)
    events: list[tuple[str, int | None, int | None]] = []
    client.pull_model("qwen3-vl:8b", user_consented=True, progress=lambda s, c, t: events.append((s, c, t)))
    assert events[0] == ("pulling manifest", None, None)
    assert ("pulling a1b2c3", 500, 1000) in events
    assert events[-1][0] == "success"
    method, path, body = fake.state.requests[-1]
    assert (method, path) == ("POST", "/api/pull")
    assert body == {"model": "qwen3-vl:8b", "stream": True}


def test_pull_progress_callback_errors_do_not_abort(fake):
    client = OllamaClient(fake.url)

    def broken(_s, _c, _t):
        raise RuntimeError("UI-Fehler")

    client.pull_model("qwen3-vl:8b", user_consented=True, progress=broken)


def test_pull_error_line_is_mapped(fake):
    fake.state.pull_lines = [
        {"status": "pulling manifest"},
        {"error": "pull model manifest: file does not exist"},
    ]
    client = OllamaClient(fake.url)
    with pytest.raises(ModelNotFoundError):
        client.pull_model("gibtsnicht:1b", user_consented=True)
    fake.state.pull_lines = [{"error": "disk full"}]
    with pytest.raises(OllamaRequestError) as info:
        client.pull_model("qwen3-vl:8b", user_consented=True)
    assert info.value.detail == "disk full"


def test_pull_without_success_is_incomplete(fake):
    fake.state.pull_lines = [{"status": "pulling manifest"}]
    client = OllamaClient(fake.url)
    with pytest.raises(OllamaRequestError):
        client.pull_model("qwen3-vl:8b", user_consented=True)


def test_pull_cancel_from_progress_callback(fake):
    fake.state.pull_line_delay = 0.2
    client = OllamaClient(fake.url)
    cancel = threading.Event()
    seen: list[str] = []

    def progress(status, _c, _t):
        seen.append(status)
        cancel.set()

    with pytest.raises(PullCancelledError):
        client.pull_model("qwen3-vl:8b", user_consented=True, progress=progress, cancel=cancel)
    assert seen == ["pulling manifest"]


def test_pull_cancel_unblocks_waiting_read(fake):
    fake.state.pull_line_delay = 10.0  # Dienst schweigt nach der ersten Zeile
    client = OllamaClient(fake.url)
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(PullCancelledError):
            client.pull_model("qwen3-vl:8b", user_consented=True, cancel=cancel)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3.0


def test_pull_already_cancelled_sends_nothing(fake):
    client = OllamaClient(fake.url)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(PullCancelledError):
        client.pull_model("qwen3-vl:8b", user_consented=True, cancel=cancel)
    assert fake.state.requests == []


# ---------------------------------------------------------------------------
# Löschen
# ---------------------------------------------------------------------------


def test_delete_requires_confirmation(fake):
    client = OllamaClient(fake.url)
    with pytest.raises(ConfirmationRequiredError):
        client.delete_model("llava", confirmed=False)
    assert fake.state.requests == []
    client.delete_model("llava", confirmed=True)
    assert ("DELETE", "/api/delete", {"model": "llava"}) in fake.state.requests
    assert not client.has_model("llava")
    with pytest.raises(ModelNotFoundError):
        client.delete_model("llava", confirmed=True)


# ---------------------------------------------------------------------------
# Strukturierter Chat
# ---------------------------------------------------------------------------

SCHEMA = {"type": "object", "properties": {"fields": {"type": "array"}}, "required": ["fields"]}


def test_chat_structured_sends_schema_and_limits(fake):
    fake.state.chat = chat_reply({"fields": []})
    client = OllamaClient(fake.url)
    result = client.chat_structured(
        "qwen3-vl:4b", "SYSTEM", "USER", ["aW1n"], SCHEMA, max_tokens=256, timeout=5
    )
    assert result == {"fields": []}
    _, path, body = fake.state.requests[-1]
    assert path == "/api/chat"
    assert body["format"] == SCHEMA
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0, "num_predict": 256}
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert body["messages"][1] == {"role": "user", "content": "USER", "images": ["aW1n"]}


def test_chat_strips_code_fences(fake):
    fake.state.chat = chat_reply('```json\n{"fields": []}\n```')
    assert OllamaClient(fake.url).chat_structured("qwen3-vl:4b", "s", "u", [], SCHEMA) == {"fields": []}


@pytest.mark.parametrize(
    "content,done_reason",
    [("kein json", "stop"), ("[1, 2]", "stop"), ('"text"', "stop"), ('{"fields": [', "length"), ("", "stop")],
)
def test_chat_invalid_responses(fake, content, done_reason):
    fake.state.chat = chat_reply(content, done_reason=done_reason)
    with pytest.raises(InvalidModelResponseError):
        OllamaClient(fake.url).chat_structured("qwen3-vl:4b", "s", "u", [], SCHEMA)


def test_chat_unknown_model_maps_to_model_not_found(fake):
    fake.state.chat = lambda _p: (404, {"error": "model 'x' not found"})
    with pytest.raises(ModelNotFoundError):
        OllamaClient(fake.url).chat_structured("qwen3-vl:8b", "s", "u", [], SCHEMA)


def test_chat_server_error(fake):
    fake.state.chat = lambda _p: (500, {"error": "out of memory"})
    with pytest.raises(OllamaRequestError) as info:
        OllamaClient(fake.url).chat_structured("qwen3-vl:4b", "s", "u", [], SCHEMA)
    assert "HTTP 500" in str(info.value)
    assert info.value.detail == "out of memory"


def test_chat_timeout(fake):
    fake.state.chat = chat_reply({"fields": []})
    fake.state.chat_delay = 3.0
    started = time.monotonic()
    with pytest.raises(OllamaTimeoutError):
        OllamaClient(fake.url).chat_structured("qwen3-vl:4b", "s", "u", [], SCHEMA, timeout=0.3)
    assert time.monotonic() - started < 2.5


def test_chat_requires_schema(fake):
    with pytest.raises(ValueError):
        OllamaClient(fake.url).chat_structured("qwen3-vl:4b", "s", "u", [], {})
