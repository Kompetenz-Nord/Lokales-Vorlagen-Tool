"""Netzwerk-Wächter (LOCAL ONLY). Es werden keine Pakete nach außen gesendet."""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from insurance_app.security import network_guard as ng
from insurance_app.security.network_guard import (
    NetworkBlockedError,
    allow_network,
    assert_loopback_url,
    install_network_guard,
    is_installed,
    is_loopback_host,
    is_loopback_url,
    network_allowed,
    uninstall_network_guard,
)


@pytest.fixture()
def guard():
    install_network_guard()
    try:
        yield
    finally:
        uninstall_network_guard()


@pytest.fixture()
def listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    try:
        yield srv.getsockname()[1]
    finally:
        srv.close()


def test_blocked_connect_fails_fast(guard):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    started = time.monotonic()
    try:
        with pytest.raises(NetworkBlockedError) as info:
            sock.connect(("203.0.113.1", 80))
    finally:
        sock.close()
    assert time.monotonic() - started < 0.5
    assert "LOCAL ONLY" in str(info.value)
    assert "203.0.113.1" not in str(info.value)  # keine Details in der Meldung
    assert isinstance(info.value, PermissionError)


def test_blocked_connect_ex_and_ipv6(guard):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(NetworkBlockedError):
        sock.connect_ex(("203.0.113.1", 443))
    try:
        sock6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return  # IPv6 auf diesem System nicht verfügbar
    with sock6, pytest.raises(NetworkBlockedError):
        sock6.connect(("2001:db8::1", 80, 0, 0))


def test_blocked_udp_sendto(guard):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(NetworkBlockedError):
            sock.sendto(b"x", ("203.0.113.1", 53))
        with pytest.raises(NetworkBlockedError):
            sock.sendto(b"x", 0, ("203.0.113.1", 53))


def test_hostnames_are_blocked_without_dns(guard):
    started = time.monotonic()
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("example.invalid", 80), timeout=5)
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("example.invalid", 80)
    with pytest.raises(NetworkBlockedError):
        socket.gethostbyname("example.invalid")
    assert time.monotonic() - started < 0.5


def test_loopback_connections_are_allowed(guard, listener):
    for host in ("127.0.0.1", "localhost"):
        conn = socket.create_connection((host, listener), timeout=2)
        conn.close()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(("127.0.0.1", listener))
    assert socket.getaddrinfo("localhost", listener)
    assert socket.getaddrinfo("127.0.0.1", listener)


def test_unix_sockets_are_allowed(guard, tmp_path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX nicht verfügbar")
    path = str(tmp_path / "s.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(path)
        srv.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
            cli.connect(path)
    finally:
        srv.close()


def test_urllib_to_remote_host_is_blocked(guard):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    with pytest.raises(urllib.error.URLError) as info:
        opener.open("http://203.0.113.1/", timeout=5)
    assert isinstance(info.value.reason, NetworkBlockedError)
    assert time.monotonic() - started < 0.5


def test_allow_network_delegates_to_original(guard, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setitem(ng._originals, "connect", lambda self, address: calls.append(address))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.connect(("203.0.113.1", 80))
        assert calls == []
        with allow_network("Test: ausdrücklich bestätigter Download"):
            assert network_allowed()
            sock.connect(("203.0.113.1", 80))  # an die (ersetzte) Originalfunktion weitergereicht
        assert calls == [("203.0.113.1", 80)]
        assert not network_allowed()
        with pytest.raises(NetworkBlockedError):
            sock.connect(("203.0.113.1", 80))
    finally:
        sock.close()


def test_allow_network_counter_is_thread_safe():
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(50):
            with allow_network("Test"), allow_network("Test verschachtelt"):
                assert network_allowed()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not network_allowed()


def test_allow_network_requires_reason():
    with pytest.raises(ValueError), allow_network("  "):
        pass


def test_allow_network_logs_only_reason(caplog):
    with (
        caplog.at_level("INFO", logger="insurance_app.security.network_guard"),
        allow_network("Modell-Download"),
    ):
        pass
    assert "Modell-Download" in caplog.text


def test_install_is_idempotent_and_uninstall_restores():
    original_connect = socket.socket.connect
    original_cc = socket.create_connection
    assert not is_installed()
    install_network_guard()
    install_network_guard()
    assert is_installed()
    assert socket.socket.connect is not original_connect
    uninstall_network_guard()
    uninstall_network_guard()
    assert not is_installed()
    assert socket.socket.connect is original_connect
    assert "connect" not in socket.socket.__dict__
    assert socket.create_connection is original_cc


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("127.255.0.9", True),
        ("::1", True),
        ("[::1]", True),
        ("::ffff:127.0.0.1", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("0.0.0.0", False),
        ("", False),
        ("192.168.1.10", False),
        ("10.0.0.1", False),
        ("::2", False),
        ("localhost.example.com", False),
        ("127.0.0.1.nip.io", False),
        (None, False),
        (b"127.0.0.1", True),
    ],
)
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:11434", True),
        ("https://localhost:8443/api", True),
        ("http://[::1]:11434", True),
        ("http://example.com:11434", False),
        ("http://192.168.1.10:11434", False),
        ("http://user@127.0.0.1:11434", False),
        ("http://:pw@127.0.0.1:11434", False),
        ("ftp://127.0.0.1/", False),
        ("file:///etc/hosts", False),
        ("http://127.0.0.1:99999", False),
        ("http://127.0.0.1\n.evil.com", False),
        ("http://127.0.0.1@example.com", False),
        ("//127.0.0.1:11434", False),
        ("", False),
    ],
)
def test_is_loopback_url(url, expected):
    assert is_loopback_url(url) is expected
    if expected:
        assert_loopback_url(url)
    else:
        with pytest.raises(NetworkBlockedError):
            assert_loopback_url(url)


# ---------------------------------------------------------------------------
# SEC-08: Rückwärtsauflösung (PTR) ohne DNS-Anfrage blockieren
# ---------------------------------------------------------------------------


def test_sec_08_reverse_lookups_of_remote_addresses_are_blocked(guard, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setitem(ng._originals, "gethostbyaddr", lambda host, *a: calls.append(("addr", host)))
    monkeypatch.setitem(
        ng._originals,
        "getnameinfo",
        lambda sa, flags, *a: calls.append(("nameinfo", sa, flags)) or ("x", "80"),
    )
    started = time.monotonic()
    with pytest.raises(NetworkBlockedError):
        socket.gethostbyaddr("93.184.216.34")
    with pytest.raises(NetworkBlockedError):
        socket.gethostbyaddr("")
    with pytest.raises(NetworkBlockedError):
        socket.getnameinfo(("93.184.216.34", 80), 0)
    with pytest.raises(NetworkBlockedError):
        socket.getnameinfo(("2001:db8::1", 80, 0, 0), socket.NI_NAMEREQD)
    # getfqdn ruft intern gethostbyaddr auf und gibt bei Fehler den Namen unverändert zurück
    assert socket.getfqdn("93.184.216.34") == "93.184.216.34"
    assert calls == []
    assert time.monotonic() - started < 0.5


def test_sec_08_loopback_numeric_and_allowed_reverse_lookups_pass(guard, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setitem(ng._originals, "gethostbyaddr", lambda host, *a: calls.append(("addr", host)))
    monkeypatch.setitem(
        ng._originals, "getnameinfo", lambda sa, flags, *a: calls.append(("nameinfo", sa[0])) or ("x", "80")
    )
    socket.gethostbyaddr("127.0.0.1")
    socket.gethostbyaddr("::1")
    socket.getnameinfo(("127.0.0.1", 80), 0)
    socket.getnameinfo(("93.184.216.34", 80), socket.NI_NUMERICHOST | socket.NI_NUMERICSERV)
    with allow_network("Test: ausdrücklich freigegeben"):
        socket.gethostbyaddr("93.184.216.34")
    assert calls == [
        ("addr", "127.0.0.1"),
        ("addr", "::1"),
        ("nameinfo", "127.0.0.1"),
        ("nameinfo", "93.184.216.34"),
        ("addr", "93.184.216.34"),
    ]


def test_sec_08_getnameinfo_is_restored_on_uninstall():
    original = socket.getnameinfo
    install_network_guard()
    try:
        assert socket.getnameinfo is not original
    finally:
        uninstall_network_guard()
    assert socket.getnameinfo is original
