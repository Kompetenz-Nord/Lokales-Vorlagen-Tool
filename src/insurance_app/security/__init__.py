"""Sicherheit und Datenschutz: Log-Schwärzung, Netzwerk-Wächter, temporäre Arbeitsordner."""

from __future__ import annotations

from .logging_setup import JobLoggerAdapter, configure_logging, job_logger, new_job_id, shutdown_logging
from .network_guard import (
    NetworkBlockedError,
    allow_network,
    assert_loopback_url,
    install_network_guard,
    is_installed,
    is_loopback_url,
    uninstall_network_guard,
)
from .redaction import RedactingFilter, redact
from .tempfiles import TempWorkspace, cleanup_stale, force_rmtree

__all__ = [
    "JobLoggerAdapter",
    "NetworkBlockedError",
    "RedactingFilter",
    "TempWorkspace",
    "allow_network",
    "assert_loopback_url",
    "cleanup_stale",
    "configure_logging",
    "force_rmtree",
    "install_network_guard",
    "is_installed",
    "is_loopback_url",
    "job_logger",
    "new_job_id",
    "redact",
    "shutdown_logging",
    "uninstall_network_guard",
]
