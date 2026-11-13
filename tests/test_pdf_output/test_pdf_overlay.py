"""Koordinaten-Overlay für Vorlagen ohne Formularfelder (synthetisch)."""

from __future__ import annotations

import pymupdf
import pytest
from pdf_test_helpers import FONTS_DIR, fv, make_plain_pdf, sha256, split

from insurance_app.pdf.filler import FormPermissionError, TemplateFiller
from insurance_app.pdf.fonts import font_file
from insurance_app.pdf.inspector import inspect_pdf_form
from insurance_app.pdf.overlay import OverlayNotPermittedError, apply_overlay
from insurance_app.pdf.qa import run_quality_checks
from insurance_app.templates.resolver import resolve_fill_values
from insurance_app.templates.schema import MappingKind, TemplateDefinition
from insurance_app.templates.studio import coordinate_field


def _template(pdf) -> TemplateDefinition:
    return TemplateDefinition(
        template_id="muster_kuendigung_1",
        catalog_id="kuendigung_ordentlich",
        version="1",
        insurer="versichererunabhängig",
        insurer_code="ALLGEMEIN",
        form_number="VORLAGE3",
        title="Muster-Kündigung",
        short_title="Kuendigung",
        pdf_file=pdf.name,
        sha256=sha256(pdf),
        page_count=1,
        fields=[
            coordinate_field("Vertragsnummer", 1, [150, 98, 400, 114], "Vertragsnummer", "contract.number"),
            coordinate_field(
                "Name",
                1,
                [150, 128, 400, 144],
                "Name",
                "{insured.last_name}, {insured.first_name}",
                required=True,
            ),
        ],
        mapping_confirmed=True,
    )


def _values():
    return split(
        [
            fv("contract.number", "MUSTER-4711"),
            fv("insured.first_name", "Erika"),
            fv("insured.last_name", "Musterfrau-Große"),
        ]
    )


def test_apply_overlay_embeds_font_and_fits(tmp_path):
    pdf = make_plain_pdf(tmp_path / "muster.pdf")
    doc = pymupdf.open(pdf)
    try:
        ok = apply_overlay(
            doc, 1, [150, 98, 400, 114], "Große Straße 12a", font_file(FONTS_DIR), base_size=10
        )
        assert ok
        fonts = [f[3] for f in doc[0].get_fonts()]
        assert any("Liberation" in name for name in fonts)
        assert "Große Straße 12a" in doc[0].get_text()
        # zu lang selbst bei Mindestgröße -> nichts schreiben
        before = doc[0].read_contents()
        assert not apply_overlay(
            doc, 1, [150, 200, 170, 210], "Viel zu langer Mustertext " * 5, font_file(FONTS_DIR)
        )
        assert doc[0].read_contents() == before
        # schrumpft, um zu passen
        assert apply_overlay(
            doc, 1, [150, 300, 260, 312], "Muster Bäckerei GmbH & Co. KG", font_file(FONTS_DIR), 12
        )
    finally:
        doc.close()


def test_apply_overlay_refused_without_modify_permission(tmp_path):
    pdf = make_plain_pdf(tmp_path / "muster.pdf", encrypted_no_modify=True)
    doc = pymupdf.open(pdf)
    try:
        with pytest.raises(OverlayNotPermittedError):
            apply_overlay(doc, 1, [150, 98, 400, 114], "MUSTER", font_file(FONTS_DIR))
    finally:
        doc.close()


def test_apply_overlay_argument_checks(tmp_path):
    doc = pymupdf.open(make_plain_pdf(tmp_path / "muster.pdf"))
    try:
        with pytest.raises(ValueError):
            apply_overlay(doc, 2, [0, 0, 10, 10], "x", font_file(FONTS_DIR))
        with pytest.raises(FileNotFoundError):
            apply_overlay(doc, 1, [0, 0, 10, 10], "x", tmp_path / "fehlt.ttf")
        assert apply_overlay(doc, 1, [0, 0, 10, 10], "   ", font_file(FONTS_DIR))
    finally:
        doc.close()


@pytest.mark.slow
def test_filler_uses_overlay_for_pdf_without_fields(tmp_path):
    pdf = make_plain_pdf(tmp_path / "muster.pdf")
    assert inspect_pdf_form(pdf).fields == []
    t = _template(pdf)
    contract, order = _values()
    plan = resolve_fill_values(t, contract, order, inspect_pdf_form(pdf), mode="final")
    assert not plan.blocking, plan.checklist
    assert {v.pdf_field for v in plan.values} == {"Vertragsnummer", "Name"}
    out = tmp_path / "out.pdf"
    res = TemplateFiller(FONTS_DIR).fill(t, pdf, plan, out)
    assert res.used_overlay and res.incremental
    assert set(res.filled_fields) == {"Vertragsnummer", "Name"}
    doc = pymupdf.open(out)
    try:
        text = doc[0].get_text()
        assert "MUSTER-4711" in text and "Musterfrau-Große, Erika" in text
        assert any("Liberation" in f[3] for f in doc[0].get_fonts())
    finally:
        doc.close()
    qa = run_quality_checks(t, pdf, out, plan, dpi=60)
    assert qa.passed, [c for c in qa.checks if not c.passed]


def test_filler_refuses_overlay_without_modify_permission(tmp_path):
    pdf = make_plain_pdf(tmp_path / "muster.pdf", encrypted_no_modify=True)
    t = _template(pdf)
    contract, order = _values()
    plan = resolve_fill_values(t, contract, order, inspect_pdf_form(pdf))
    with pytest.raises(FormPermissionError):
        TemplateFiller(FONTS_DIR).fill(t, pdf, plan, tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()


def test_coordinate_field_validation():
    m = coordinate_field("Ort", 1, (10, 20, 110, 34), "Ort", "insured.city", font_size=9, align="right")
    assert m.overlay_rect == [10.0, 20.0, 110.0, 34.0] and m.kind == MappingKind.TEXT and m.align == "right"
    assert m.source == "insured.city" and m.compose is None
    c = coordinate_field(
        "Adresse", 1, (10, 20, 110, 34), "Adresse", "{insured.street} {insured.house_number?}"
    )
    assert c.compose and c.source is None
    for bad in (
        dict(rect=(10, 20, 5, 34)),
        dict(page=0),
        dict(source="gibt.es_nicht"),
        dict(source="{gibt.es_nicht}"),
        dict(kind=MappingKind.CHECKBOX),
        dict(align="mitte"),
    ):
        args = dict(pdf_field_name="X", page=1, rect=(10, 20, 110, 34), label="X", source="insured.city")
        args.update(bad)
        with pytest.raises(ValueError):
            coordinate_field(**args)
