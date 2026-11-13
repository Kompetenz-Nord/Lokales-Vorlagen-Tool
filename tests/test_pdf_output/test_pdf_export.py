"""Export: Dateinamen, Freigabe nur nach bestandener QA, Protokoll ohne Personendaten."""

from __future__ import annotations

from datetime import date

import pytest
from pdf_test_helpers import (
    FONTS_DIR,
    PERSONAL_VALUES,
    form_template,
    form_values,
    make_form_pdf,
    sha256,
    split,
)

from insurance_app.export.exporter import ExportBlockedError, Exporter, ExportLogEntry
from insurance_app.export.naming import build_export_filename, sanitize_part, unique_path
from insurance_app.pdf.filler import TemplateFiller
from insurance_app.pdf.inspector import inspect_pdf_form
from insurance_app.pdf.qa import QaCheck, QaReport, run_quality_checks
from insurance_app.templates.resolver import resolve_fill_values

CASE_ID = "V20261113-AB12CD"


def test_build_export_filename_pattern():
    name = build_export_filename(date(2026, 11, 13), "SWISSLIFE", "SL13065", "Freigabe", CASE_ID)
    assert name == "2026-11-13_SWISSLIFE_SL13065_Freigabe_V20261113-AB12CD.pdf"


def test_build_export_filename_sanitizes():
    name = build_export_filename(date(2026, 1, 2), "tecis AG", "", "Bestandsübertrag / Wechsel", "V1../../x")
    assert name == "2026-01-02_TECIS-AG_FORMULAR_Bestandsuebertrag-Wechsel_V1x.pdf"
    assert all(c.isalnum() or c in "_-." for c in name)
    assert sanitize_part("Größe ß Ä", "X") == "Groesse-ss-Ae"
    assert sanitize_part("///", "LEER") == "LEER"
    assert len(sanitize_part("a" * 200)) == 40


def test_unique_path(tmp_path):
    first = unique_path(tmp_path, "datei.pdf")
    assert first == tmp_path / "datei.pdf"
    first.write_bytes(b"1")
    second = unique_path(tmp_path, "datei.pdf")
    assert second.name == "datei_2.pdf"
    second.write_bytes(b"2")
    assert unique_path(tmp_path, "datei.pdf").name == "datei_3.pdf"


def _qa_failed(template_id: str) -> QaReport:
    return QaReport(
        passed=False,
        checks=[QaCheck(code="FIELD_VALUES", passed=False, message="abweichend")],
        template_id=template_id,
    )


@pytest.fixture()
def filled(tmp_path):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    contract, order = split(form_values())
    plan = resolve_fill_values(t, contract, order, inspect_pdf_form(pdf), mode="final")
    work = tmp_path / "tmp" / "vorschau.pdf"
    TemplateFiller(FONTS_DIR).fill(t, pdf, plan, work)
    qa = run_quality_checks(t, pdf, work, plan, renderers=("mupdf",), dpi=50)
    assert qa.passed
    return t, work, qa


def test_finalize_moves_file_and_logs(tmp_path, filled):
    t, work, qa = filled
    exporter = Exporter(tmp_path / "exports", tmp_path / "logs" / "export.jsonl")
    digest = sha256(work)
    result = exporter.finalize(CASE_ID, t, work, qa, flattened=False, created=date(2026, 11, 13))
    assert result.path.name == "2026-11-13_TECIS_MUSTER1_Muster_V20261113-AB12CD.pdf"
    assert result.path.parent == tmp_path / "exports"
    assert not work.exists()
    assert sha256(result.path) == digest == result.log_entry.sha256
    assert not list((tmp_path / "exports").glob("*.part"))
    log = exporter.read_log()
    assert len(log) == 1 and isinstance(log[0], ExportLogEntry)
    entry = log[0]
    assert entry.case_id == CASE_ID and entry.template_id == t.template_id and entry.qa_passed
    assert entry.file_name == result.path.name and not entry.flattened
    raw = (tmp_path / "logs" / "export.jsonl").read_text("utf-8")
    for value in PERSONAL_VALUES:
        assert value not in raw
        assert value not in result.path.name


def test_finalize_second_export_gets_unique_name(tmp_path, filled):
    t, work, qa = filled
    exporter = Exporter(tmp_path / "exports", tmp_path / "export.jsonl")
    copy = work.with_name("kopie.pdf")
    copy.write_bytes(work.read_bytes())
    a = exporter.finalize(CASE_ID, t, work, qa, flattened=False, created=date(2026, 11, 13))
    b = exporter.finalize(CASE_ID, t, copy, qa, flattened=False, created=date(2026, 11, 13))
    assert b.path.name == a.path.name.replace(".pdf", "_2.pdf")
    assert len(exporter.read_log()) == 2


def test_finalize_refuses_failed_qa(tmp_path, filled):
    t, work, _ = filled
    exporter = Exporter(tmp_path / "exports", tmp_path / "export.jsonl")
    with pytest.raises(ExportBlockedError) as exc:
        exporter.finalize(CASE_ID, t, work, _qa_failed(t.template_id), flattened=False)
    assert "Qualitätskontrolle" in exc.value.user_message
    assert work.exists()
    assert not (tmp_path / "exports").exists() or not list((tmp_path / "exports").glob("*.pdf"))
    assert exporter.read_log() == []


def test_finalize_refuses_file_changed_after_qa(tmp_path, filled):
    t, work, qa = filled
    work.write_bytes(work.read_bytes() + b"\n%geaendert\n")
    exporter = Exporter(tmp_path / "exports", tmp_path / "export.jsonl")
    with pytest.raises(ExportBlockedError) as exc:
        exporter.finalize(CASE_ID, t, work, qa, flattened=False)
    assert "verändert" in exc.value.user_message


def test_finalize_refuses_mismatches(tmp_path, filled):
    t, work, qa = filled
    exporter = Exporter(tmp_path / "exports", tmp_path / "export.jsonl")
    with pytest.raises(ExportBlockedError):
        exporter.finalize(CASE_ID, t, work, qa, flattened=True)
    with pytest.raises(ExportBlockedError):
        exporter.finalize(CASE_ID, t, work, qa.model_copy(update={"template_id": "anders"}), flattened=False)
    with pytest.raises(ExportBlockedError):
        exporter.finalize(CASE_ID, t, tmp_path / "fehlt.pdf", qa, flattened=False)


def test_read_log_skips_corrupt_lines(tmp_path):
    log = tmp_path / "export.jsonl"
    log.write_text(
        '{"kaputt": true}\nkein json\n'
        + ExportLogEntry(
            case_id=CASE_ID,
            template_id="t",
            template_version="1",
            file_name="a.pdf",
            qa_passed=True,
            sha256="0" * 64,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    entries = Exporter(tmp_path, log).read_log()
    assert [e.file_name for e in entries] == ["a.pdf"]
    assert Exporter(tmp_path, tmp_path / "fehlt.jsonl").read_log() == []


def test_logs_contain_no_personal_values(tmp_path, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    contract, order = split(form_values())
    plan = resolve_fill_values(t, contract, order, inspect_pdf_form(pdf), mode="final")
    work = tmp_path / "vorschau.pdf"
    TemplateFiller(FONTS_DIR).fill(t, pdf, plan, work)
    qa = run_quality_checks(t, pdf, work, plan, renderers=("mupdf",), dpi=40)
    Exporter(tmp_path / "exports", tmp_path / "export.jsonl").finalize(CASE_ID, t, work, qa, flattened=False)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Export abgeschlossen" in text and "Vorlage befüllt" in text
    for value in PERSONAL_VALUES:
        assert value not in text
