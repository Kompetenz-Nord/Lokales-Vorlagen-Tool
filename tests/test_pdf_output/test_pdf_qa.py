"""Qualitätskontrolle: Fehler müssen erkannt werden (manipulierte Ausgaben)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from pdf_test_helpers import (
    FONTS_DIR,
    form_template,
    form_values,
    load_template,
    make_form_pdf,
    requires_originals,
    sl13065_values,
    sl18477_private_values,
    split,
)

from conftest import SL13065_PDF, SL18477_PDF
from insurance_app.pdf.filler import TemplateFiller
from insurance_app.pdf.inspector import inspect_pdf_form
from insurance_app.pdf.qa import QaReport, run_quality_checks
from insurance_app.templates.resolver import FillValue, resolve_fill_values
from insurance_app.templates.schema import MappingKind

FAST = {"renderers": ("mupdf",), "dpi": 50}


def _failed(report: QaReport) -> set[str]:
    return {c.code for c in report.checks if not c.passed}


@pytest.fixture()
def synthetic(tmp_path):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    contract, order = split(form_values())
    plan = resolve_fill_values(t, contract, order, inspect_pdf_form(pdf), mode="final")
    out = tmp_path / "out.pdf"
    TemplateFiller(FONTS_DIR).fill(t, pdf, plan, out)
    return pdf, t, plan, out


def _edit(path: Path, func, incremental: bool = True, **save_kwargs) -> None:
    doc = pymupdf.open(path)
    func(doc)
    if incremental:
        doc.save(path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        doc.close()
    else:
        tmp = path.with_suffix(".tmp.pdf")
        doc.save(tmp, **save_kwargs)
        doc.close()
        tmp.replace(path)


def test_synthetic_baseline_passes(synthetic):
    pdf, t, plan, out = synthetic
    report = run_quality_checks(t, pdf, out, plan, **FAST)
    assert report.passed, [c for c in report.checks if not c.passed]
    assert report.template_id == t.template_id and report.plan_mode == "final"
    assert report.rendered_pages == {}


def test_detects_unreadable_file(synthetic, tmp_path):
    pdf, t, plan, _ = synthetic
    bad = tmp_path / "kaputt.pdf"
    bad.write_bytes(b"%PDF-1.7\nkaputt")
    report = run_quality_checks(t, pdf, bad, plan, **FAST)
    assert not report.passed and "FILE_OPENS" in _failed(report)
    missing = run_quality_checks(t, pdf, tmp_path / "fehlt.pdf", plan, **FAST)
    assert not missing.passed and missing.filled_sha256 is None


def test_detects_stray_value_in_unplanned_field(synthetic):
    pdf, t, plan, out = synthetic
    plan_without_name = plan.model_copy(update={"values": [v for v in plan.values if v.pdf_field != "Name"]})
    report = run_quality_checks(t, pdf, out, plan_without_name, **FAST)
    assert {"FIELD_VALUES", "VALUES_VISIBLE"} <= _failed(report)


def test_detects_wrong_value(synthetic):
    pdf, t, plan, out = synthetic
    values = [v.model_copy(update={"text": "ANDERS"}) if v.pdf_field == "Name" else v for v in plan.values]
    report = run_quality_checks(t, pdf, out, plan.model_copy(update={"values": values}), **FAST)
    assert "FIELD_VALUES" in _failed(report)


def test_detects_filled_signature(synthetic):
    pdf, t, plan, out = synthetic

    def sign(doc):
        for w in doc[0].widgets():
            if w.field_name == "Unterschrift":
                kind, parent = doc.xref_get_key(w.xref, "Parent")
                doc.xref_set_key(int(parent.split()[0]), "V", "<</Type/Sig/Contents <00>>>")

    _edit(out, sign)
    report = run_quality_checks(t, pdf, out, plan, **FAST)
    assert {"SIGNATURES_EMPTY", "FIELD_VALUES"} <= _failed(report)


def test_detects_page_content_change(synthetic):
    pdf, t, plan, out = synthetic
    _edit(out, lambda doc: doc[0].insert_text((300, 400), "HINZUGEFÜGT", fontsize=20))
    report = run_quality_checks(t, pdf, out, plan, **FAST)
    assert "NO_PAGE_CHANGES" in _failed(report)


def test_detects_added_annotation(synthetic):
    pdf, t, plan, out = synthetic
    _edit(out, lambda doc: doc[0].add_text_annot((400, 500), "Notiz"))
    assert "NO_PAGE_CHANGES" in _failed(run_quality_checks(t, pdf, out, plan, **FAST))


def test_detects_full_rewrite_and_lost_prefix(synthetic):
    pdf, t, plan, out = synthetic
    _edit(out, lambda doc: None, incremental=False, garbage=3)
    report = run_quality_checks(t, pdf, out, plan, **FAST)
    assert "ORIGINAL_PREFIX" in _failed(report)
    flattened_mode = run_quality_checks(t, pdf, out, plan, flattened=True, **FAST)
    assert next(c for c in flattened_mode.checks if c.code == "ORIGINAL_PREFIX").passed


def test_detects_text_too_long(synthetic, tmp_path):
    pdf, t, plan, _ = synthetic
    long_text = "Musterfrau-Langername-Doppelname, Erika Maria Theresia Muster"
    values = [v.model_copy(update={"text": long_text}) if v.pdf_field == "Name" else v for v in plan.values]
    bad_plan = plan.model_copy(update={"values": values})
    out = tmp_path / "lang.pdf"
    TemplateFiller(FONTS_DIR).fill(t, pdf, bad_plan, out)
    assert "TEXT_FITS" in _failed(run_quality_checks(t, pdf, out, bad_plan, **FAST))


def test_detects_unsupported_glyphs_in_plan(synthetic):
    pdf, t, plan, out = synthetic
    values = [*plan.values, FillValue(pdf_field="Extra", kind=MappingKind.TEXT, text="Łódź")]
    report = run_quality_checks(t, pdf, out, plan.model_copy(update={"values": values}), **FAST)
    assert "GLYPHS" in _failed(report)


def test_unknown_renderer_is_an_error(synthetic):
    pdf, t, plan, out = synthetic
    report = run_quality_checks(t, pdf, out, plan, renderers=("unbekannt",), dpi=50)
    assert not report.passed and "RENDER_OK" in _failed(report)


def test_plan_for_other_template(synthetic):
    pdf, t, plan, out = synthetic
    report = run_quality_checks(t, pdf, out, plan.model_copy(update={"template_id": "anders"}), **FAST)
    assert "PLAN_COMPLETE" in _failed(report)


@requires_originals
def test_detects_removed_encryption(tmp_path):
    t = load_template("swisslife_sl13065.json")
    contract, order = split(sl13065_values())
    plan = resolve_fill_values(t, contract, order, inspect_pdf_form(SL13065_PDF))
    out = tmp_path / "out.pdf"
    TemplateFiller(FONTS_DIR).fill(t, SL13065_PDF, plan, out)
    _edit(out, lambda doc: None, incremental=False, encryption=pymupdf.PDF_ENCRYPT_NONE)
    report = run_quality_checks(t, SL13065_PDF, out, plan, **FAST)
    assert {"ENCRYPTION_KEPT", "ORIGINAL_PREFIX"} <= _failed(report)


@requires_originals
@pytest.mark.slow
def test_detects_change_on_preserved_page(tmp_path):
    t = load_template("swisslife_sl18477.json")
    contract, order = split(sl18477_private_values())
    plan = resolve_fill_values(t, contract, order, inspect_pdf_form(SL18477_PDF))
    out = tmp_path / "out.pdf"
    TemplateFiller(FONTS_DIR).fill(t, SL18477_PDF, plan, out)
    _edit(out, lambda doc: doc[4].insert_text((100, 100), "MUSTER", fontsize=14))
    report = run_quality_checks(t, SL18477_PDF, out, plan, **FAST)
    assert "PRESERVED_PAGES" in _failed(report)
    page_check = next(c for c in report.checks if c.code == "PRESERVED_PAGES")
    assert page_check.page == 5
