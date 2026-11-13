"""Minimaler Ollama-Client – ausschließlich für einen lokalen Ollama-Dienst (Loopback).

Sicherheitsregeln
-----------------
* Die Basis-URL muss auf 127.x, ::1 oder ``localhost`` zeigen (geprüft beim
  Anlegen **und** vor jeder Anfrage).
* HTTP nur über die Standardbibliothek mit einem eigenen Opener: Proxy-Variablen
  (``HTTP_PROXY`` usw.) werden ignoriert, Weiterleitungen abgelehnt.
* Modell-Download nur mit ``user_consented=True``, Löschen nur mit ``confirmed=True``.
* Cloud-Modelle (``…:cloud``, ``…-cloud``) und Namen mit Host/Namespace werden abgelehnt.
* Protokolliert werden nur Endpunkt, Status, Dauer – nie Inhalte.

Verwendete REST-Endpunkte (Ollama-Dokumentation): ``/api/version``, ``/api/tags``,
``/api/show``, ``/api/pull`` (Stream aus JSON-Zeilen), ``/api/delete``, ``/api/chat``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.client import HTTPException, HTTPResponse
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

from insurance_app.security.network_guard import assert_loopback_url

__all__ = [
    "DEFAULT_BASE_URL",
    "ConfirmationRequiredError",
    "ConsentRequiredError",
    "InvalidModelNameError",
    "InvalidModelResponseError",
    "ModelInfo",
    "ModelNotFoundError",
    "OllamaClient",
    "OllamaError",
    "OllamaRequestError",
    "OllamaTimeoutError",
    "OllamaUnavailableError",
    "PullCancelledError",
    "ProgressCallback",
    "validate_model_name",
]

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
MAX_RESPONSE_BYTES = 64 * 1024 * 1024  # Schutz gegen unerwartet große Antworten
USER_AGENT = "LokalesVorlagenTool"

ProgressCallback = Callable[[str, int | None, int | None], None]


# ---------------------------------------------------------------------------
# Fehler (deutsche Meldungen in einfacher Sprache)
# ---------------------------------------------------------------------------


class OllamaError(Exception):
    """Basisklasse aller Fehler der lokalen KI."""

    default_message = "Bei der lokalen KI ist ein Fehler aufgetreten."

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.detail = detail  # technische Zusatzinfo (z. B. Fehlertext von Ollama), nie Dokumentinhalt


class OllamaUnavailableError(OllamaError):
    default_message = "Die lokale KI (Ollama) ist nicht erreichbar. Bitte prüfen, ob Ollama gestartet ist."


class OllamaTimeoutError(OllamaError):
    default_message = "Die lokale KI hat nicht rechtzeitig geantwortet (Zeitüberschreitung)."


class ModelNotFoundError(OllamaError):
    default_message = "Das gewählte KI-Modell ist auf diesem Rechner nicht vorhanden."


class InvalidModelResponseError(OllamaError):
    default_message = "Die Antwort der lokalen KI war ungültig und wird nicht verwendet."


class ConsentRequiredError(OllamaError):
    default_message = (
        "Der Modell-Download startet nur nach Ihrer ausdrücklichen Zustimmung "
        "(Größe und Lizenz bitte vorher prüfen)."
    )


class ConfirmationRequiredError(OllamaError):
    default_message = "Das Modell wird nur nach Ihrer ausdrücklichen Bestätigung gelöscht."


class InvalidModelNameError(OllamaError, ValueError):
    default_message = (
        "Ungültiger Modellname. Erlaubt sind nur lokale Modelle wie „qwen3-vl:4b“ "
        "(keine Cloud-Modelle, keine fremden Server)."
    )


class OllamaRequestError(OllamaError):
    default_message = "Die lokale KI hat die Anfrage mit einem Fehler beantwortet."


class PullCancelledError(OllamaError):
    default_message = "Der Modell-Download wurde abgebrochen."


# ---------------------------------------------------------------------------
# Modellnamen
# ---------------------------------------------------------------------------

_MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*(:[a-z0-9._-]+)?$")
_TOKEN_SPLIT = re.compile(r"[:._-]")


def validate_model_name(name: str) -> str:
    """Modellnamen prüfen und unverändert (ohne Rand-Leerzeichen) zurückgeben.

    Erlaubt: ``name`` oder ``name:tag`` aus Kleinbuchstaben, Ziffern, ``.``, ``_``, ``-``.
    Abgelehnt: Cloud-Modelle (Bestandteil ``cloud``), Host-/Namespace-Angaben (``/``),
    Großbuchstaben, Leerzeichen, überlange Namen.
    """
    if not isinstance(name, str):
        raise InvalidModelNameError()
    candidate = name.strip()
    if not candidate or len(candidate) > 128 or not _MODEL_NAME.match(candidate):
        raise InvalidModelNameError()
    if "cloud" in _TOKEN_SPLIT.split(candidate):
        raise InvalidModelNameError(
            "Cloud-Modelle sind nicht erlaubt. Diese App nutzt ausschließlich lokal installierte Modelle."
        )
    base = candidate.split(":", 1)[0]
    if ".." in candidate or base.endswith((".", "-", "_")):
        raise InvalidModelNameError()
    return candidate


def _with_default_tag(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


# ---------------------------------------------------------------------------
# Datenmodelle
# ---------------------------------------------------------------------------


class ModelInfo(BaseModel):
    """Lokal vorhandenes Modell laut ``/api/tags``."""

    model_config = ConfigDict(extra="ignore")

    name: str
    size_bytes: int = 0
    digest: str | None = None
    modified_at: str | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None


# ---------------------------------------------------------------------------
# HTTP-Opener ohne Proxy und ohne Weiterleitungen
# ---------------------------------------------------------------------------


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, "Weiterleitung abgelehnt", headers, fp)


def _build_opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler({}),  # Umgebungs-Proxys NIE verwenden
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        _RefuseRedirects(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def _is_timeout(exc: BaseException | None) -> bool:
    return isinstance(exc, TimeoutError | socket.timeout)


def _error_text(raw: bytes) -> str | None:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        return data["error"][:300]
    return None


def _strip_wrappers(content: str) -> str:
    text = content.strip()
    if text.startswith("<think>"):
        end = text.find("</think>")
        text = text[end + len("</think>") :].strip() if end >= 0 else ""
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class _Watchdog:
    """Schließt eine Streaming-Antwort, sobald ``cancel`` gesetzt wird (entsperrt ``readline``)."""

    def __init__(self, response: HTTPResponse, cancel: threading.Event) -> None:
        self._response = response
        self._cancel = cancel
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ollama-pull-cancel", daemon=True)

    def __enter__(self) -> _Watchdog:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._done.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._done.is_set():
            if self._cancel.wait(0.05):
                _abort_response(self._response)
                return


def _abort_response(response: HTTPResponse) -> None:
    sock = None
    with contextlib.suppress(Exception):
        sock = response.fp.raw._sock  # type: ignore[union-attr]
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(Exception):
        response.close()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OllamaClient:
    """HTTP-Client für den lokalen Ollama-Dienst."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 10.0) -> None:
        assert_loopback_url(base_url)
        parts = urlsplit(base_url)
        if parts.query or parts.fragment:
            raise ValueError("Die Ollama-Adresse darf keine Parameter enthalten.")
        self.base_url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        self.timeout = float(timeout)
        self._opener = _build_opener()

    # -- interne Hilfen ---------------------------------------------------
    def _url(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        assert_loopback_url(url)
        return url

    def _open(self, method: str, path: str, payload: dict[str, Any] | None, timeout: float) -> HTTPResponse:
        url = self._url(path)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method)  # noqa: S310 - nur Loopback (geprüft)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            return self._opener.open(req, timeout=timeout)  # type: ignore[return-value]
        except urllib.error.HTTPError as exc:
            raw = b""
            with contextlib.suppress(Exception):
                raw = exc.read(4096)
            detail = _error_text(raw)
            log.info("Ollama %s %s -> HTTP %d", method, path, exc.code)
            if exc.code == 404:
                raise ModelNotFoundError(detail=detail) from None
            if 300 <= exc.code < 400:
                raise OllamaRequestError(
                    "Die lokale KI hat auf eine andere Adresse weitergeleitet. Das ist nicht erlaubt.",
                    detail=f"HTTP {exc.code}",
                ) from None
            raise OllamaRequestError(
                f"Die lokale KI meldet einen Fehler (HTTP {exc.code}).", detail=detail
            ) from None
        except urllib.error.URLError as exc:
            if _is_timeout(exc.reason):
                raise OllamaTimeoutError() from None
            log.info("Ollama nicht erreichbar (%s)", type(exc.reason).__name__)
            raise OllamaUnavailableError() from None
        except TimeoutError:
            raise OllamaTimeoutError() from None
        except (OSError, HTTPException) as exc:
            log.info("Ollama-Verbindung fehlgeschlagen (%s)", type(exc).__name__)
            raise OllamaUnavailableError() from None

    def _request_json(
        self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        started = time.monotonic()
        response = self._open(method, path, payload, timeout if timeout is not None else self.timeout)
        try:
            with response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except TimeoutError:
            raise OllamaTimeoutError() from None
        except (OSError, HTTPException) as exc:
            log.info("Ollama-Antwort abgebrochen (%s)", type(exc).__name__)
            raise OllamaUnavailableError() from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OllamaRequestError("Die Antwort der lokalen KI ist unerwartet groß.")
        log.debug("Ollama %s %s ok (%.2f s)", method, path, time.monotonic() - started)
        if not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise OllamaRequestError("Die lokale KI hat eine unlesbare Antwort geschickt.") from None

    # -- Status -----------------------------------------------------------
    def is_running(self) -> bool:
        """Antwortet der lokale Ollama-Dienst? (wirft nie)"""
        try:
            data = self._request_json("GET", "/api/version", timeout=min(self.timeout, 3.0))
        except OllamaError:
            return False
        return isinstance(data, dict)

    def version(self) -> str | None:
        """Ollama-Version oder ``None``, wenn nicht erreichbar."""
        try:
            data = self._request_json("GET", "/api/version", timeout=min(self.timeout, 3.0))
        except OllamaError:
            return None
        if isinstance(data, dict) and isinstance(data.get("version"), str):
            return data["version"][:64]
        return None

    # -- Modelle ----------------------------------------------------------
    def list_models(self) -> list[ModelInfo]:
        """Lokal vorhandene Modelle (``/api/tags``)."""
        data = self._request_json("GET", "/api/tags")
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise OllamaRequestError("Die Modellliste der lokalen KI ist unlesbar.")
        out: list[ModelInfo] = []
        for m in models:
            if not isinstance(m, dict):
                continue
            name = m.get("name") or m.get("model")
            if not isinstance(name, str) or not name:
                continue
            details = m.get("details") if isinstance(m.get("details"), dict) else {}
            size = m.get("size")
            out.append(
                ModelInfo(
                    name=name,
                    size_bytes=int(size) if isinstance(size, int | float) and size >= 0 else 0,
                    digest=m.get("digest") if isinstance(m.get("digest"), str) else None,
                    modified_at=m.get("modified_at") if isinstance(m.get("modified_at"), str) else None,
                    family=details.get("family") if isinstance(details.get("family"), str) else None,
                    parameter_size=(
                        details.get("parameter_size")
                        if isinstance(details.get("parameter_size"), str)
                        else None
                    ),
                    quantization=(
                        details.get("quantization_level")
                        if isinstance(details.get("quantization_level"), str)
                        else None
                    ),
                )
            )
        return out

    def find_model(self, name: str) -> ModelInfo | None:
        """Modell in der lokalen Liste suchen (ohne Tag = ``:latest``)."""
        wanted = _with_default_tag(validate_model_name(name))
        for m in self.list_models():
            if _with_default_tag(m.name) == wanted:
                return m
        return None

    def has_model(self, name: str) -> bool:
        """Ist das Modell lokal vorhanden?"""
        return self.find_model(name) is not None

    def show_model(self, name: str) -> dict[str, Any]:
        """Modelldetails (``/api/show``). :class:`ModelNotFoundError`, wenn nicht vorhanden."""
        model = validate_model_name(name)
        data = self._request_json("POST", "/api/show", {"model": model})
        if not isinstance(data, dict):
            raise OllamaRequestError("Die Modelldetails der lokalen KI sind unlesbar.")
        return data

    def pull_model(
        self,
        name: str,
        user_consented: bool,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
        *,
        read_timeout: float | None = None,
    ) -> None:
        """Modell über den lokalen Ollama-Dienst herunterladen (nur mit Zustimmung).

        ``progress(status, completed, total)`` wird für jede Fortschrittszeile aufgerufen.
        Wird ``cancel`` gesetzt, schließt der Client die Verbindung (Ollama bricht den
        Download dann ab) und löst :class:`PullCancelledError` aus.
        """
        model = validate_model_name(name)
        if user_consented is not True:
            raise ConsentRequiredError()
        if cancel is not None and cancel.is_set():
            raise PullCancelledError()
        timeout = read_timeout if read_timeout is not None else max(self.timeout, 300.0)
        log.info("Modell-Download gestartet (Zustimmung liegt vor)")
        started = time.monotonic()
        response = self._open("POST", "/api/pull", {"model": model, "stream": True}, timeout)
        cancel_event = cancel or threading.Event()
        success = False
        lines = 0
        try:
            watchdog = _Watchdog(response, cancel_event) if cancel is not None else contextlib.nullcontext()
            with watchdog:
                while True:
                    if cancel_event.is_set():
                        raise PullCancelledError()
                    try:
                        raw = response.readline(1024 * 1024)
                    except TimeoutError:
                        if cancel_event.is_set():
                            raise PullCancelledError() from None
                        raise OllamaTimeoutError(
                            "Der Modell-Download stockt (keine Rückmeldung von Ollama)."
                        ) from None
                    except (OSError, HTTPException, ValueError, AttributeError):
                        if cancel_event.is_set():
                            raise PullCancelledError() from None
                        raise OllamaUnavailableError(
                            "Die Verbindung zur lokalen KI wurde während des Downloads unterbrochen."
                        ) from None
                    if not raw:
                        if cancel_event.is_set():
                            raise PullCancelledError()
                        break
                    line = raw.strip()
                    if not line:
                        continue
                    lines += 1
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        raise OllamaRequestError("Unlesbare Fortschrittsmeldung der lokalen KI.") from None
                    if not isinstance(event, dict):
                        continue
                    if "error" in event:
                        detail = str(event.get("error"))[:300]
                        lowered = detail.lower()
                        if "not found" in lowered or "does not exist" in lowered or "manifest" in lowered:
                            raise ModelNotFoundError(
                                "Dieses Modell gibt es in der Ollama-Bibliothek nicht (Name prüfen).",
                                detail=detail,
                            )
                        raise OllamaRequestError("Der Modell-Download ist fehlgeschlagen.", detail=detail)
                    status = str(event.get("status") or "")[:200]
                    completed = event.get("completed")
                    total = event.get("total")
                    if progress is not None:
                        try:
                            progress(
                                status,
                                int(completed) if isinstance(completed, int | float) else None,
                                int(total) if isinstance(total, int | float) else None,
                            )
                        except Exception as exc:  # Fehler der Oberfläche dürfen den Download nicht stören
                            log.warning("Fortschrittsanzeige fehlgeschlagen (%s)", type(exc).__name__)
                    if status == "success":
                        success = True
        finally:
            _abort_response(response)
        duration = time.monotonic() - started
        if not success:
            log.info("Modell-Download ohne Abschlussmeldung beendet (%d Meldungen)", lines)
            raise OllamaRequestError(
                "Der Modell-Download wurde unvollständig beendet. Bitte erneut versuchen."
            )
        log.info("Modell-Download abgeschlossen (%.1f s, %d Meldungen)", duration, lines)

    def delete_model(self, name: str, confirmed: bool) -> None:
        """Modell lokal löschen (nur nach ausdrücklicher Bestätigung)."""
        model = validate_model_name(name)
        if confirmed is not True:
            raise ConfirmationRequiredError()
        self._request_json("DELETE", "/api/delete", {"model": model})
        log.info("Modell gelöscht")

    # -- Auswertung -------------------------------------------------------
    def chat_structured(
        self,
        model: str,
        system: str,
        user: str,
        images_b64: list[str],
        schema: dict[str, Any],
        max_tokens: int = 1024,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Eine Chat-Anfrage mit JSON-Schema (``format``), ``temperature=0`` und Token-Limit.

        Gibt das geparste JSON-Objekt zurück. :class:`InvalidModelResponseError` bei
        ungültigem JSON, Nicht-Objekt oder abgeschnittener Antwort.
        """
        model_name = validate_model_name(model)
        if not isinstance(schema, dict) or not schema:
            raise ValueError("Für strukturierte Antworten ist ein JSON-Schema erforderlich.")
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user, "images": list(images_b64)},
            ],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0, "num_predict": int(max_tokens)},
        }
        started = time.monotonic()
        data = self._request_json("POST", "/api/chat", payload, timeout=timeout)
        duration = time.monotonic() - started
        if not isinstance(data, dict):
            raise InvalidModelResponseError()
        if isinstance(data.get("error"), str):
            raise OllamaRequestError("Die lokale KI meldet einen Fehler.", detail=data["error"][:300])
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise InvalidModelResponseError()
        if data.get("done_reason") == "length":
            raise InvalidModelResponseError(
                "Die Antwort der lokalen KI wurde abgeschnitten (Token-Limit erreicht) und wird nicht verwendet."
            )
        try:
            parsed = json.loads(_strip_wrappers(content))
        except ValueError:
            raise InvalidModelResponseError() from None
        if not isinstance(parsed, dict):
            raise InvalidModelResponseError()
        log.info("Strukturierte KI-Antwort erhalten (%.1f s, %d Bilder)", duration, len(images_b64))
        return parsed
