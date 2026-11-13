"""Logging-Konfiguration: Logdatei ohne Klartext-Personendaten."""

from __future__ import annotations

import logging
import logging.handlers
import re

import pytest

from insurance_app.security.logging_setup import (
    LOG_FILE_NAME,
    configure_logging,
    job_logger,
    new_job_id,
    shutdown_logging,
)
from insurance_app.security.redaction import RedactingFilter

MUSTER_IBAN = "DE89370400440532013000"


@pytest.fixture()
def restore_root_logging():
    root = logging.getLogger()
    level = root.level
    yield
    shutdown_logging()
    root.setLevel(level)
    logging.captureWarnings(False)


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_log_file_contains_no_personal_patterns(tmp_path, restore_root_logging):
    log_file = configure_logging(tmp_path / "logs")
    assert log_file == tmp_path / "logs" / LOG_FILE_NAME
    log = logging.getLogger("insurance_app.test")
    log.info("IBAN %s gelesen", MUSTER_IBAN)
    log.info("Geburtsdatum 12.08.1988, Beginn 2019-01-01")
    log.warning("Vertragsnummer 9630030-1 und PLZ 12345, Mail erika@example.org")
    log.info("Telefon +49 30 1234567")
    try:
        raise RuntimeError(f"Fehler bei {MUSTER_IBAN} / 9630030-1")
    except RuntimeError:
        log.exception("Ausnahme")
    job_logger("insurance_app.job", "V20261113-ABC123").info("Seite %d fertig (%d Felder)", 2, 7)
    _flush()
    content = log_file.read_text(encoding="utf-8")
    for secret in (
        MUSTER_IBAN,
        "12.08.1988",
        "2019-01-01",
        "9630030",
        "12345",
        "erika@example.org",
        "1234567",
    ):
        assert secret not in content, secret
    assert "[IBAN]" in content and "[DATUM]" in content and "[ZAHL]" in content
    assert "[job V20261113-ABC123] Seite 2 fertig (7 Felder)" in content
    assert "RuntimeError" in content
    line = content.splitlines()[0]
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO insurance_app\.test IBAN \[IBAN\] gelesen$", line
    )


def test_configure_logging_is_idempotent(tmp_path, restore_root_logging):
    configure_logging(tmp_path / "a")
    configure_logging(tmp_path / "b", console=True)
    root = logging.getLogger()
    own = [h for h in root.handlers if getattr(h, "_insurance_app_handler", False)]
    assert len(own) == 2
    file_handlers = [h for h in own if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(file_handlers) == 1
    fh = file_handlers[0]
    assert fh.baseFilename.endswith(str(tmp_path / "b" / LOG_FILE_NAME))
    assert fh.maxBytes == 1_000_000 and fh.backupCount == 3
    assert fh.encoding == "utf-8"
    for h in own:
        assert any(isinstance(f, RedactingFilter) for f in h.filters)
    configure_logging(tmp_path / "b")
    assert len([h for h in root.handlers if getattr(h, "_insurance_app_handler", False)]) == 1


def test_level_is_applied(tmp_path, restore_root_logging):
    log_file = configure_logging(tmp_path, level=logging.WARNING)
    logging.getLogger("insurance_app.test").info("nicht sichtbar")
    logging.getLogger("insurance_app.test").warning("sichtbar")
    _flush()
    content = log_file.read_text(encoding="utf-8")
    assert "sichtbar" in content and "nicht sichtbar" not in content


def test_job_logger_prefix_and_sanitizing():
    adapter = job_logger("insurance_app.x", "J-ABCDEF12")
    msg, kwargs = adapter.process("Start", {})
    assert msg == "[job J-ABCDEF12] Start"
    assert kwargs["extra"]["job_id"] == "J-ABCDEF12"
    evil = job_logger("insurance_app.x", "a\nb c/d" + "x" * 100)
    assert "\n" not in evil.job_id and " " not in evil.job_id and len(evil.job_id) == 64


def test_new_job_id_survives_redaction():
    from insurance_app.security.redaction import redact

    job_id = new_job_id()
    assert re.fullmatch(r"J-[0-9A-F]{8}", job_id)
    assert redact(f"[job {job_id}] ok") == f"[job {job_id}] ok"
