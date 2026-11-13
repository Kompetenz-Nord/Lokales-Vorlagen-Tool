"""TemplateFiller an synthetischen PDFs: Schutzregeln, Fehlerfälle, Fixieren."""

from __future__ import annotations

import pymupdf
import pytest
from pdf_test_helpers import (
    FONTS_DIR,
    form_template,
    form_values,
    make_form_pdf,
    sha256,
    split,
)

from insurance_app.pdf.filler import (
    FLATTEN_NOT_PERMITTED_MESSAGE,
    FlattenNotPermittedError,
    FormPermissionError,
    MappingError,
    TemplateFiller,
    TemplateIntegrityError,
)
from insurance_app.pdf.inspector import inspect_pdf_form
from insurance_app.pdf.qa import run_quality_checks
from insurance_app.templates.resolver import FillPlan, FillValue, resolve_fill_values
from insurance_app.templates.schema import MappingKind


def _plan(pdf, template, consent="true"):
    contract, order = split(form_values(consent))
    return resolve_fill_values(template, contract, order, inspect_pdf_form(pdf), mode="final")


@pytest.fixture()
def filler() -> TemplateFiller:
    return TemplateFiller(FONTS_DIR)


def test_fill_unencrypted_form_incremental(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    original_sha = sha256(pdf)
    t = form_template(pdf)
    plan = _plan(pdf, t)
    assert not plan.blocking, plan.checklist
    out = tmp_path / "out" / "befuellt.pdf"
    res = filler.fill(t, pdf, plan, out)
    assert res.output_path == out and out.is_file()
    assert res.incremental and res.encryption_kept and not res.used_overlay and not res.flattened
    assert res.filled_fields == ["Versicherungsnummer", "Name", "Zustimmung"]
    assert "Unterschrift" in res.skipped_fields
    assert res.sha256 == sha256(out)
    assert sha256(pdf) == original_sha
    assert out.read_bytes().startswith(pdf.read_bytes())
    assert not list(out.parent.glob("*.part"))
    ins = inspect_pdf_form(out)
    assert ins.field("Versicherungsnummer").value == "MUSTER-4711"
    assert ins.field("Name").value == "Musterfrau, Erika"
    assert ins.field("Zustimmung").value == "Yes"
    assert ins.field("Zustimmung").widgets[0].current_state == "Yes"
    assert not ins.field("Unterschrift").has_value


def test_fill_checkbox_off(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    res = filler.fill(t, pdf, _plan(pdf, t, consent="false"), tmp_path / "out.pdf")
    ins = inspect_pdf_form(res.output_path)
    assert ins.field("Zustimmung").widgets[0].current_state == "Off"
    assert ins.field("Zustimmung").value in (None, "Off")


def test_integrity_error_on_modified_template(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    plan = _plan(pdf, t)
    pdf.write_bytes(pdf.read_bytes() + b"\n%MUSTER\n")
    out = tmp_path / "out.pdf"
    with pytest.raises(TemplateIntegrityError) as exc:
        filler.fill(t, pdf, plan, out)
    assert "Prüfsumme" in exc.value.user_message
    assert not out.exists()
    with pytest.raises(TemplateIntegrityError):
        filler.fill(t, tmp_path / "fehlt.pdf", plan, out)


def test_flatten_refused_without_modify_permission(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf", encryption="no_modify")
    t = form_template(pdf)
    out = tmp_path / "out.pdf"
    with pytest.raises(FlattenNotPermittedError) as exc:
        filler.fill(t, pdf, _plan(pdf, t), out, flatten=True)
    assert exc.value.user_message == FLATTEN_NOT_PERMITTED_MESSAGE
    assert not out.exists()
    # ohne Fixieren klappt es und die Rechte bleiben erhalten
    res = filler.fill(t, pdf, _plan(pdf, t), out)
    assert res.encryption_kept and res.incremental
    perms = inspect_pdf_form(out).permissions
    assert perms.encrypted and not perms.can_modify and perms.can_fill_forms


@pytest.mark.slow
def test_flatten_allowed_for_unencrypted_pdf(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    plan = _plan(pdf, t)
    out = tmp_path / "flach.pdf"
    res = filler.fill(t, pdf, plan, out, flatten=True)
    assert res.flattened and not res.incremental
    doc = pymupdf.open(out)
    try:
        assert list(doc[0].widgets()) == []
        text = doc[0].get_text()
        assert "MUSTER-4711" in text and "Musterfrau, Erika" in text
    finally:
        doc.close()
    qa = run_quality_checks(t, pdf, out, plan, renderers=("mupdf", "pdfium"), dpi=60, flattened=True)
    assert qa.passed, [c for c in qa.checks if not c.passed]
    assert qa.flattened


@pytest.mark.slow
def test_flatten_allowed_when_encrypted_with_modify_permission(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf", encryption="modify")
    t = form_template(pdf)
    res = filler.fill(t, pdf, _plan(pdf, t), tmp_path / "flach.pdf", flatten=True)
    assert res.flattened and res.encryption_kept
    assert inspect_pdf_form(res.output_path).permissions.encrypted


def test_refuses_already_signed_template(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf", signed=True)
    t = form_template(pdf)
    with pytest.raises(FormPermissionError) as exc:
        filler.fill(t, pdf, _plan(pdf, t), tmp_path / "out.pdf")
    assert "Unterschrift" in exc.value.user_message
    assert not (tmp_path / "out.pdf").exists()


def test_refuses_password_protected_template(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf", encryption="password")
    t = form_template(pdf).model_copy(update={"sha256": sha256(pdf)})
    plan = FillPlan(template_id=t.template_id, template_version=t.version, values=[])
    with pytest.raises(FormPermissionError):
        filler.fill(t, pdf, plan, tmp_path / "out.pdf")


def test_refuses_without_fill_permission(tmp_path, filler):
    doc = pymupdf.open(make_form_pdf(tmp_path / "basis.pdf"))
    pdf = tmp_path / "gesperrt.pdf"
    doc.save(
        pdf,
        encryption=pymupdf.PDF_ENCRYPT_AES_128,
        owner_pw="owner-muster",
        user_pw="",
        permissions=pymupdf.PDF_PERM_PRINT,
    )
    doc.close()
    t = form_template(pdf)
    with pytest.raises(FormPermissionError):
        filler.fill(t, pdf, _plan(pdf, t), tmp_path / "out.pdf")


def test_signature_values_are_never_written(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    plan = _plan(pdf, t)
    bad = plan.model_copy(
        update={
            "values": [*plan.values, FillValue(pdf_field="Unterschrift", kind=MappingKind.TEXT, text="X")]
        }
    )
    with pytest.raises(MappingError):
        filler.fill(t, pdf, bad, tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()


def test_mapping_errors(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    plan = _plan(pdf, t)
    # Plan einer anderen Vorlage
    with pytest.raises(MappingError):
        filler.fill(t, pdf, plan.model_copy(update={"template_id": "andere"}), tmp_path / "a.pdf")
    # Ein-Zustand existiert nicht im PDF
    values = [v if v.pdf_field != "Zustimmung" else v.model_copy(update={"state": "Ja"}) for v in plan.values]
    with pytest.raises(MappingError) as exc:
        filler.fill(t, pdf, plan.model_copy(update={"values": values}), tmp_path / "b.pdf")
    assert "Ein-Zustand" in exc.value.user_message
    assert not (tmp_path / "b.pdf").exists() and not list(tmp_path.glob("*.part"))
    # Textwert auf ein Ankreuzfeld
    values = [
        v
        if v.pdf_field != "Zustimmung"
        else FillValue(pdf_field="Zustimmung", kind=MappingKind.TEXT, text="x")
        for v in plan.values
    ]
    with pytest.raises(MappingError):
        filler.fill(t, pdf, plan.model_copy(update={"values": values}), tmp_path / "c.pdf")
    # Feld, das in der Zuordnung nicht vorkommt
    extra = [*plan.values, FillValue(pdf_field="Unbekannt", kind=MappingKind.TEXT, text="x")]
    with pytest.raises(MappingError):
        filler.fill(t, pdf, plan.model_copy(update={"values": extra}), tmp_path / "d.pdf")
    # Ausgabe auf das Original
    with pytest.raises(MappingError):
        filler.fill(t, pdf, plan, pdf)
    assert sha256(pdf) == t.sha256


def test_mapping_error_when_field_missing_in_pdf(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    fields = [*t.fields]
    fields[0] = fields[0].model_copy(update={"pdf_field": "Fehlt im PDF"})
    t2 = t.model_copy(update={"fields": fields})
    plan = FillPlan(
        template_id=t2.template_id,
        template_version=t2.version,
        values=[FillValue(pdf_field="Fehlt im PDF", kind=MappingKind.TEXT, text="MUSTER")],
    )
    with pytest.raises(MappingError) as exc:
        filler.fill(t2, pdf, plan, tmp_path / "out.pdf")
    assert "fehlt" in exc.value.user_message


def test_font_size_from_plan_is_applied(tmp_path, filler):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    t = form_template(pdf)
    plan = _plan(pdf, t)
    values = [v.model_copy(update={"font_size": 7.5}) if v.pdf_field == "Name" else v for v in plan.values]
    res = filler.fill(t, pdf, plan.model_copy(update={"values": values}), tmp_path / "out.pdf")
    assert inspect_pdf_form(res.output_path).field("Name").font_size == 7.5
