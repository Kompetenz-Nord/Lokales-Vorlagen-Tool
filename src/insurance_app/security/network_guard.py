"""Netzwerk-Wächter: LOCAL ONLY auf Ebene des Python-Socket-Moduls.

Nach :func:`install_network_guard` sind nur noch Verbindungen erlaubt zu
* Unix-Domain-Sockets (``AF_UNIX``),
* IPv4-Loopback ``127.0.0.0/8``,
* IPv6-Loopback ``::1`` (auch ``::ffff:127.x.x.x``),
* dem Hostnamen ``localhost``.

Alles andere löst sofort – vor jedem Paket und ohne DNS-Abfrage – einen
:class:`NetworkBlockedError` aus. Geprüft werden ``socket.socket.connect``,
``connect_ex``, ``sendto``/``sendmsg`` (UDP), ``socket.create_connection``, die
Namensauflösung (``getaddrinfo``, ``gethostbyname`` …) und die Rückwärtsauflösung
(``gethostbyaddr``, ``getnameinfo`` und damit ``getfqdn``). Rückwärtsauflösungen sind
nur für Loopback-Adressen erlaubt – auch eine numerische Adresse würde sonst eine
DNS-Anfrage (PTR) auslösen. ``getnameinfo`` mit ``NI_NUMERICHOST`` (reine
Formatierung ohne Namensauflösung) bleibt erlaubt.

Grenzen: Externe Programme (Subprozesse) und native Bibliotheken mit eigenem
Netzwerkcode werden nicht erfasst. Die App startet keine solchen Netzwerkzugriffe.

Für ausdrücklich bestätigte Aktionen gibt es :func:`allow_network`.
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import socket
import threading
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "NetworkBlockedError",
    "allow_network",
    "assert_loopback_url",
    "install_network_guard",
    "is_allowed_address",
    "is_installed",
    "is_loopback_host",
    "is_loopback_url",
    "network_allowed",
    "uninstall_network_guard",
]

log = logging.getLogger(__name__)

BLOCKED_MESSAGE = (
    "Netzwerkzugriff blockiert (LOCAL ONLY): Diese App verbindet sich nur mit dem eigenen Rechner."
)
URL_MESSAGE = (
    "Netzwerkzugriff blockiert (LOCAL ONLY): Die Adresse muss auf den eigenen Rechner zeigen "
    "(127.0.0.1, ::1 oder localhost, nur http/https, ohne Benutzername)."
)


class NetworkBlockedError(PermissionError):
    """Ein Netzwerkzugriff außerhalb des eigenen Rechners wurde verhindert."""

    def __init__(self, message: str = BLOCKED_MESSAGE) -> None:
        super().__init__(message)

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else BLOCKED_MESSAGE


# ---------------------------------------------------------------------------
# Adressprüfung
# ---------------------------------------------------------------------------

_LOCALHOST_NAMES = frozenset({"localhost", "localhost."})


def is_loopback_host(host: object) -> bool:
    """``True`` für 127.x.x.x, ::1 (inkl. IPv4-gemappt) und ``localhost``."""
    if isinstance(host, bytes | bytearray):
        try:
            host = bytes(host).decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str) or not host:
        return False
    name = host.strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if name.casefold() in _LOCALHOST_NAMES:
        return True
    name = name.split("%", 1)[0]  # IPv6-Zonenangabe
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _is_numeric_host(host: object) -> bool:
    if isinstance(host, bytes | bytearray):
        try:
            host = bytes(host).decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def is_allowed_address(family: int, address: Any) -> bool:
    """Darf eine Verbindung zu ``address`` (Socket-Adresse) aufgebaut werden?"""
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is not None and family == af_unix:
        return True
    if family in (socket.AF_INET, socket.AF_INET6):
        if isinstance(address, tuple) and address:
            return is_loopback_host(address[0])
        return False
    return False


# ---------------------------------------------------------------------------
# Freigabe für ausdrücklich bestätigte Aktionen
# ---------------------------------------------------------------------------

_allow_lock = threading.Lock()
_allow_count = 0


def network_allowed() -> bool:
    """Ist gerade eine ausdrückliche Netzwerkfreigabe aktiv?"""
    with _allow_lock:
        return _allow_count > 0


@contextlib.contextmanager
def allow_network(reason: str) -> Iterator[None]:
    """Netzwerk vorübergehend freigeben (nur nach ausdrücklicher Zustimmung des Nutzers).

    Die Freigabe gilt prozessweit, solange mindestens ein ``allow_network``-Block
    aktiv ist (thread-sicherer Zähler). Protokolliert wird nur der Grund.
    """
    global _allow_count
    if not reason or not reason.strip():
        raise ValueError("Für eine Netzwerkfreigabe muss ein Grund angegeben werden.")
    with _allow_lock:
        _allow_count += 1
    log.info("Netzwerkfreigabe aktiv: %s", reason.strip()[:120])
    try:
        yield
    finally:
        with _allow_lock:
            _allow_count -= 1
        log.info("Netzwerkfreigabe beendet: %s", reason.strip()[:120])


# ---------------------------------------------------------------------------
# Monkeypatch
# ---------------------------------------------------------------------------

_install_lock = threading.RLock()
_originals: dict[str, Any] = {}
_own_attrs: dict[str, bool] = {}


def _family_name(family: int) -> str:
    try:
        return socket.AddressFamily(family).name
    except ValueError:
        return str(family)


def _block(what: str, family: int | None = None) -> NetworkBlockedError:
    if family is None:
        log.warning("Netzwerkzugriff blockiert (%s)", what)
    else:
        log.warning("Netzwerkzugriff blockiert (%s, %s)", what, _family_name(family))
    return NetworkBlockedError()


def _check_socket(sock: socket.socket, address: Any, what: str) -> None:
    if network_allowed():
        return
    family = sock.family
    if not is_allowed_address(family, address):
        raise _block(what, family)


def _guarded_connect(self: socket.socket, address: Any) -> None:
    _check_socket(self, address, "connect")
    return _originals["connect"](self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    _check_socket(self, address, "connect_ex")
    return _originals["connect_ex"](self, address)


def _guarded_sendto(self: socket.socket, data: Any, *args: Any) -> int:
    # sendto(data, address) oder sendto(data, flags, address)
    if args:
        _check_socket(self, args[-1], "sendto")
    return _originals["sendto"](self, data, *args)


def _guarded_sendmsg(self: socket.socket, buffers: Any, *args: Any) -> int:
    # sendmsg(buffers[, ancdata[, flags[, address]]])
    if len(args) >= 3 and args[2] is not None:
        _check_socket(self, args[2], "sendmsg")
    return _originals["sendmsg"](self, buffers, *args)


def _host_allowed_for_lookup(host: object) -> bool:
    """Namensauflösung nur für Loopback, numerische Adressen (kein DNS) und ``None``."""
    if host is None:
        return True
    if isinstance(host, str | bytes | bytearray) and len(host) == 0:
        return True
    return is_loopback_host(host) or _is_numeric_host(host)


def _host_allowed_for_reverse_lookup(host: object) -> bool:
    """Rückwärtsauflösung (Adresse -> Name) nur für Loopback: jede andere Adresse fragt DNS (PTR)."""
    return is_loopback_host(host)


def _guarded_gethostbyaddr(host: Any, *args: Any, **kwargs: Any) -> Any:
    if not network_allowed() and not _host_allowed_for_reverse_lookup(host):
        raise _block("gethostbyaddr")
    return _originals["gethostbyaddr"](host, *args, **kwargs)


def _guarded_getnameinfo(sockaddr: Any, flags: Any, *args: Any, **kwargs: Any) -> Any:
    if not network_allowed():
        numeric = isinstance(flags, int) and bool(flags & socket.NI_NUMERICHOST)
        host = sockaddr[0] if isinstance(sockaddr, tuple) and sockaddr else None
        if not numeric and not _host_allowed_for_reverse_lookup(host):
            raise _block("getnameinfo")
    return _originals["getnameinfo"](sockaddr, flags, *args, **kwargs)


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
    if not network_allowed():
        host = address[0] if isinstance(address, tuple) and address else None
        if not is_loopback_host(host):
            raise _block("create_connection")
        source = kwargs.get("source_address") or (args[1] if len(args) > 1 else None)
        if source and isinstance(source, tuple) and source[0] and not is_loopback_host(source[0]):
            raise _block("create_connection")
    return _originals["create_connection"](address, *args, **kwargs)


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    if not network_allowed() and not _host_allowed_for_lookup(host):
        raise _block("getaddrinfo")
    return _originals["getaddrinfo"](host, *args, **kwargs)


def _lookup_guard(name: str) -> Callable[..., Any]:
    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not network_allowed() and not _host_allowed_for_lookup(host):
            raise _block(name)
        return _originals[name](host, *args, **kwargs)

    guarded.__name__ = name
    return guarded


_SOCKET_METHODS: dict[str, Callable[..., Any]] = {
    "connect": _guarded_connect,
    "connect_ex": _guarded_connect_ex,
    "sendto": _guarded_sendto,
    "sendmsg": _guarded_sendmsg,
}

_MODULE_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "create_connection": _guarded_create_connection,
    "getaddrinfo": _guarded_getaddrinfo,
    "gethostbyname": _lookup_guard("gethostbyname"),
    "gethostbyname_ex": _lookup_guard("gethostbyname_ex"),
    "gethostbyaddr": _guarded_gethostbyaddr,
    "getnameinfo": _guarded_getnameinfo,
}


def install_network_guard() -> None:
    """Wächter aktivieren (idempotent)."""
    with _install_lock:
        if _originals:
            return
        for name, func in _SOCKET_METHODS.items():
            original = getattr(socket.socket, name, None)
            if original is None:  # z. B. sendmsg fehlt unter Windows
                continue
            _originals[name] = original
            _own_attrs[name] = name in socket.socket.__dict__
            setattr(socket.socket, name, func)
        for name, func in _MODULE_FUNCTIONS.items():
            original = getattr(socket, name, None)
            if original is None:
                continue
            _originals[name] = original
            setattr(socket, name, func)
        log.info("Netzwerk-Wächter aktiv (LOCAL ONLY)")


def uninstall_network_guard() -> None:
    """Wächter deaktivieren und Originalfunktionen wiederherstellen (für Tests)."""
    with _install_lock:
        if not _originals:
            return
        for name in _SOCKET_METHODS:
            if name not in _originals:
                continue
            if _own_attrs.get(name):
                setattr(socket.socket, name, _originals[name])
            elif name in socket.socket.__dict__:
                delattr(socket.socket, name)  # geerbte C-Methode greift wieder
        for name in _MODULE_FUNCTIONS:
            if name in _originals:
                setattr(socket, name, _originals[name])
        _originals.clear()
        _own_attrs.clear()
        log.info("Netzwerk-Wächter deaktiviert")


def is_installed() -> bool:
    with _install_lock:
        return bool(_originals)


# ---------------------------------------------------------------------------
# URL-Prüfung (z. B. für den Ollama-Client)
# ---------------------------------------------------------------------------


def is_loopback_url(url: str) -> bool:
    """``True`` nur für ``http``/``https``-URLs auf 127.x, ::1 oder localhost ohne Benutzerangaben."""
    if not isinstance(url, str) or not url or any(ch in url for ch in "\r\n\t\x00 \\"):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port  # löst ValueError bei ungültigem Port aus
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return False
    if not host:
        return False
    return is_loopback_host(host)


def assert_loopback_url(url: str) -> None:
    """Wie :func:`is_loopback_url`, löst aber :class:`NetworkBlockedError` aus."""
    if not is_loopback_url(url):
        raise NetworkBlockedError(URL_MESSAGE)
