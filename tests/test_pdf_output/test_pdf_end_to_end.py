"""Ende-zu-Ende: Werteauflösung -> Befüllung -> Qualitätskontrolle an den Originalvorlagen.

Alle eingetragenen Werte sind synthetisch (MUSTER).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from pdf_test_helpers import (
    FONTS_DIR,
    PERSONAL_VALUES,
    load_template,
    requires_originals,
    sha256,
    sl13065_values,
    sl18477_employer_values,
    sl18477_private_values,
    split,
)
from pypdf import PdfReader

from conftest import SL13065_PDF, SL18477_PDF
from insurance_app.pdf.filler import FlattenNotPermittedError, TemplateFiller
from insurance_app.pdf.inspector import inspect_pdf_form
from insurance_app.pdf.qa import QaReport, run_quality_checks
from insurance_app.templates.resolver import FillPlan, resolve_fill_values

pytestmark = [requires_originals, pytest.mark.originals]


def _fill(tmp_path: Path, schema: str, pdf: Path, values, name: str):
    template = load_template(schema)
    contract, order = split(values)
    inspection = inspect_pdf_form(pdf)
    plan = resolve_fill_values(template, contract, order, inspection, mode="final")
    assert not plan.blocking, [i.message for i in plan.blocking_items]
    out = tmp_path / f"{name}.pdf"
    before = sha256(pdf)
    result = TemplateFiller(FONTS_DIR).fill(template, pdf, plan, out)
    assert sha256(pdf) == before == template.sha256, "Originalvorlage muss unverändert bleiben"
    return template, plan, result


def _pypdf_values(path: Path) -> dict[str, object]:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        assert reader.decrypt("")
    return {k: v.get("/V") for k, v in (reader.get_fields() or {}).items()}


def _assert_qa(report: QaReport, renderers: tuple[str, ...] = ("mupdf", "pdfium")) -> None:
    failed = [(c.code, c.renderer, c.message) for c in report.checks if not c.passed]
    assert report.passed, failed
    codes = {c.code for c in report.checks}
    for code in (
        "FILE_OPENS",
        "PAGE_COUNT",
        "PAGE_SIZES",
        "ORIGINAL_PREFIX",
        "ENCRYPTION_KEPT",
        "FIELD_VALUES",
        "SIGNATURES_EMPTY",
        "TEXT_FITS",
        "GLYPHS",
        "RENDER_OK",
        "VALUES_VISIBLE",
        "NO_PAGE_CHANGES",
        "PLAN_COMPLETE",
    ):
        assert code in codes, code
    for renderer in renderers:
        assert any(c.code == "RENDER_OK" and c.renderer == renderer and c.passed for c in report.checks)
        assert any(c.code == "VALUES_VISIBLE" and c.renderer == renderer and c.passed for c in report.checks)
    for check in report.checks:
        for value in PERSONAL_VALUES:
            assert value not in check.message


def _assert_encryption_kept(original: Path, filled: Path) -> None:
    a, b = inspect_pdf_form(original).permissions, inspect_pdf_form(filled).permissions
    assert b.encrypted and a.raw == b.raw and a.encryption_method == b.encryption_method
    assert b.can_fill_forms and not b.can_modify and not b.can_assemble


@pytest.mark.slow
def test_sl13065_full(tmp_path):
    template, plan, result = _fill(
        tmp_path, "swisslife_sl13065.json", SL13065_PDF, sl13065_values(), "sl13065"
    )
    out = result.output_path
    assert result.incremental and result.encryption_kept and not result.used_overlay
    assert out.read_bytes().startswith(SL13065_PDF.read_bytes())
    _assert_encryption_kept(SL13065_PDF, out)

    ins = inspect_pdf_form(out)
    expected = {
        "Versicherung Nummer": "MUSTER-4711",
        "Versicherte Person": "Erika Musterfrau",
        "Versicherungsnehmer": "Muster Bäckerei GmbH & Co. KG",
        "Name Vorname": "Musterfrau, Erika",
        "Datum 1.1": "01",
        "Datum 1.21": "02",
        "Datum 1.32": "1980",
        "Straße Hausnummer": "Große Straße 12a",
        "Postleitzahl": "12345",
        "Ort": "Musterstadt",
        "Datum 2.1": "01",
        "Datum 2.21": "04",
        "Datum 2.32": "2010",
        "Datum 3.1": "30",
        "Datum 3.21": "09",
        "Datum 3.32": "2026",
    }
    for name, value in expected.items():
        assert ins.field(name).value == value, name
    assert ins.field("Abfindung").value == "Ja"
    assert ins.field("Abfindung").widgets[0].current_state == "Ja"
    assert ins.field("Rückerstattung").widgets[0].current_state == "Off"  # false -> Rückerstattung erfolgt
    for name in (
        "Unterschrift",
        "Datum Unterschrift 1.1",
        "Datum Unterschrift 1.21",
        "Datum Unterschrift 1.32",
    ):
        assert not ins.field(name).has_value

    pv = _pypdf_values(out)
    assert pv["Versicherung Nummer"] == "MUSTER-4711"
    assert pv["Straße Hausnummer"] == "Große Straße 12a"
    assert str(pv["Abfindung"]) == "/Ja"
    assert pv.get("Unterschrift") is None

    report = run_quality_checks(template, SL13065_PDF, out, plan, render_dir=tmp_path / "qa")
    _assert_qa(report)
    assert set(report.rendered_pages) == {"mupdf", "pdfium"}
    assert all(Path(p).is_file() for paths in report.rendered_pages.values() for p in paths)
    assert report.filled_sha256 == sha256(out)


@pytest.mark.slow
def test_sl13065_refund_waiver_checked(tmp_path):
    template, plan, result = _fill(
        tmp_path, "swisslife_sl13065.json", SL13065_PDF, sl13065_values("false", "true"), "sl13065b"
    )
    ins = inspect_pdf_form(result.output_path)
    assert ins.field("Abfindung").widgets[0].current_state == "Off"
    assert ins.field("Rückerstattung").widgets[0].current_state == "Ja"
    report = run_quality_checks(template, SL13065_PDF, result.output_path, plan, renderers=("pdfium",))
    _assert_qa(report, renderers=("pdfium",))


def test_sl13065_flatten_not_permitted(tmp_path):
    template = load_template("swisslife_sl13065.json")
    contract, order = split(sl13065_values())
    plan = resolve_fill_values(template, contract, order, inspect_pdf_form(SL13065_PDF))
    out = tmp_path / "flach.pdf"
    with pytest.raises(FlattenNotPermittedError) as exc:
        TemplateFiller(FONTS_DIR).fill(template, SL13065_PDF, plan, out, flatten=True)
    assert "kein Fixieren" in exc.value.user_message and "ausfüllbar" in exc.value.user_message
    assert not out.exists()
    assert sha256(SL13065_PDF) == template.sha256


@pytest.mark.slow
def test_sl18477_employer_variant_with_interim_paying(tmp_path):
    template, plan, result = _fill(
        tmp_path, "swisslife_sl18477.json", SL18477_PDF, sl18477_employer_values(), "sl18477_ag"
    )
    out = result.output_path
    _assert_encryption_kept(SL18477_PDF, out)
    ins = inspect_pdf_form(out)
    assert (
        ins.field("Fortführung der Versicherung durch den neuen Arbeitgeber").widgets[0].current_state == "Ja"
    )
    assert ins.field("Versicherung durch die versicherte Person").widgets[0].current_state == "Off"
    assert ins.field("Firma").value == "Muster Bäckerei GmbH & Co. KG"
    assert ins.field("Ansprechpartner").value == "Max Mustermann"
    assert ins.field("Straße Hausnummer").value == "Große Straße 12a"
    assert (ins.field("Datum 1.1").value, ins.field("Datum 1.21").value) == ("01", "2027")
    assert (ins.field("Datum 2.1").value, ins.field("Datum 2.21").value) == ("02", "2027")
    assert ins.field("private Fortführung für den Zwischenzeitraum").value == "Ja"
    assert ins.field("Beitrag für den Zwischenzeitraum").value == "1"
    interim = ins.field("privat gezahlter Beitrag für den Zwischenzeitraum")
    assert interim.value == "89.50"  # Feldwert für Acrobat-Zahlformat
    assert ins.field("politisch exponierte Person").value == "0"
    assert ins.field("Beitragszahlung").value == "0"
    assert ins.field("IBAN").value == "DE89 3704 0044 0532 0130 00"
    # Private Fortführung im Zwischenzeitraum: die Personendaten in Abschnitt C werden gebraucht …
    assert ins.field("Name Vorname").value == "Musterfrau, Erika"
    assert ins.field("Datum 3.1").has_value and ins.field("Geburtsort").has_value
    # … die übrigen Angaben der rein privaten Fortführung bleiben leer
    for name in ("reduzierter Beitrag", "Amt PeP"):
        assert not ins.field(name).has_value, name
    assert ins.field("Fortführung der Versicherung").value is None
    # Darstellung des Betrags im deutschen Format
    doc = pymupdf.open(out)
    try:
        xref = interim.widgets[0].xref
        ap = int(doc.xref_get_key(xref, "AP/N")[1].split()[0])
        assert b"(89,50)" in doc.xref_stream(ap)
    finally:
        doc.close()
    report = run_quality_checks(template, SL18477_PDF, out, plan)
    _assert_qa(report)
    preserved = [c for c in report.checks if c.code == "PRESERVED_PAGES"]
    assert {c.renderer for c in preserved} == {"mupdf", "pdfium"} and all(c.passed for c in preserved)


@pytest.mark.slow
def test_sl18477_private_variant_reduced_pep_related_transfer(tmp_path):
    template, plan, result = _fill(
        tmp_path, "swisslife_sl18477.json", SL18477_PDF, sl18477_private_values(), "sl18477_privat"
    )
    out = result.output_path
    ins = inspect_pdf_form(out)
    assert ins.field("Versicherung durch die versicherte Person").widgets[0].current_state == "Ja"
    assert (
        ins.field("Fortführung der Versicherung durch den neuen Arbeitgeber").widgets[0].current_state
        == "Off"
    )
    radio = ins.field("Fortführung der Versicherung")
    assert radio.value == "1"
    assert [w.current_state for w in radio.widgets] == ["Off", "1", "Off"]
    assert ins.field("reduzierter Beitrag").value == "1234.56"
    pep = ins.field("politisch exponierte Person")
    assert pep.value == "2" and [w.current_state for w in pep.widgets] == ["Off", "Off", "2"]
    assert ins.field("nahestehende Person Amt PeP").value == "Mustermann, Max, Bürgermeister"
    assert not ins.field("Amt PeP").has_value
    assert ins.field("Beitragszahlung").value == "1"
    assert ins.field("Name Vorname").value == "Musterfrau, Erika"
    assert (ins.field("Datum 3.1").value, ins.field("Datum 3.21").value, ins.field("Datum 3.32").value) == (
        "01",
        "02",
        "1980",
    )
    for name in ("Firma", "Datum 1.1", "private Fortführung für den Zwischenzeitraum"):
        assert not ins.field(name).has_value, name
    for f in ins.fields:
        if f.field_type == "signature" or f.name.startswith("Datum Unterschrift"):
            assert not f.has_value, f.name

    pv = _pypdf_values(out)
    assert pv["reduzierter Beitrag"] == "1234.56"
    assert str(pv["politisch exponierte Person"]) == "/2"
    assert pv["nahestehende Person Amt PeP"] == "Mustermann, Max, Bürgermeister"
    report = run_quality_checks(template, SL18477_PDF, out, plan, render_dir=tmp_path / "qa")
    _assert_qa(report)
    assert len(report.rendered_pages["pdfium"]) == 6


def test_preview_plan_with_open_items_fails_plan_complete(tmp_path):
    """Vorschau mit offenen Pflichtpunkten: technisch korrekt, aber nicht exportierbar."""
    template = load_template("swisslife_sl13065.json")
    values = [v for v in sl13065_values() if v.key != "employer.employment_end"]
    contract, order = split(values)
    plan = resolve_fill_values(template, contract, order, inspect_pdf_form(SL13065_PDF))
    assert plan.blocking
    out = tmp_path / "vorschau.pdf"
    TemplateFiller(FONTS_DIR).fill(template, SL13065_PDF, plan, out)
    report = run_quality_checks(template, SL13065_PDF, out, plan, renderers=("mupdf",), dpi=50)
    assert not report.passed
    assert report.technical_passed
    assert [c.code for c in report.errors] == ["PLAN_COMPLETE"]
    assert not inspect_pdf_form(out).field("Datum 3.1").has_value


def test_empty_plan_keeps_form_empty(tmp_path):
    template = load_template("swisslife_sl13065.json")
    plan = FillPlan(template_id=template.template_id, template_version=template.version)
    out = tmp_path / "leer.pdf"
    result = TemplateFiller(FONTS_DIR).fill(template, SL13065_PDF, plan, out)
    assert result.filled_fields == []
    assert all(not f.has_value for f in inspect_pdf_form(out).fields)
    # Nichts einzutragen -> die Ausgabe ist eine bytegenaue Kopie der Originalvorlage
    assert out.read_bytes() == SL13065_PDF.read_bytes()
