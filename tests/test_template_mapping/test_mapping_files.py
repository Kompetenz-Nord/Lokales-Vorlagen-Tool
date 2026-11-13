"""Prüft die ausgelieferten Feldzuordnungen gegen Schema, Feldregister und Original-PDFs."""

from __future__ import annotations

import hashlib
import json

import pymupdf
import pytest

from insurance_app.core.fields import FIELD_REGISTRY
from insurance_app.core.paths import resource_root
from insurance_app.templates.schema import MappingKind, TemplateCatalog, TemplateDefinition

ROOT = resource_root()
MAPPINGS = ["swisslife_sl13065.json", "swisslife_sl18477.json"]


def _load(name: str) -> TemplateDefinition:
    return TemplateDefinition.model_validate_json(
        (ROOT / "config" / "field_schemas" / name).read_text("utf-8")
    )


def test_catalog_has_nine_entries_in_four_groups():
    cat = TemplateCatalog.model_validate_json((ROOT / "config" / "template_catalog.json").read_text("utf-8"))
    assert len(cat.entries) == 9
    assert sorted(e.number for e in cat.entries) == list(range(1, 10))
    assert len(cat.groups) == 4
    with_original = [e for e in cat.entries if e.original_file]
    assert {e.form_number for e in with_original} == {"SL13065", "SL18477"}


@pytest.mark.parametrize("name", MAPPINGS)
def test_mapping_keys_exist_in_registry(name):
    t = _load(name)
    unknown = [k for k in t.source_keys() if k not in FIELD_REGISTRY]
    assert unknown == []


@pytest.mark.parametrize("name", MAPPINGS)
def test_mapping_matches_original_pdf(name):
    t = _load(name)
    pdf = ROOT / "assets" / "templates" / "originals" / t.pdf_file
    if not pdf.exists():
        pytest.skip("Originalvorlage nicht vorhanden")
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == t.sha256
    doc = pymupdf.open(pdf)
    assert doc.page_count == t.page_count
    pdf_fields: dict[str, set[int]] = {}
    for page in doc:
        for w in page.widgets():
            pdf_fields.setdefault(w.field_name, set()).add(page.number + 1)
    mapped = {m.pdf_field for m in t.fields}
    assert mapped - set(pdf_fields) == set(), "zugeordnete Felder fehlen im PDF"
    assert set(pdf_fields) - mapped - set(t.ignored_fields) == set(), "PDF-Felder ohne Zuordnung"
    for m in t.fields:
        assert m.page in pdf_fields[m.pdf_field]


@pytest.mark.parametrize("name", MAPPINGS)
def test_signature_fields_never_filled(name):
    t = _load(name)
    sig = [m for m in t.fields if m.kind in (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE)]
    assert sig, "Vorlage sollte Unterschriftsfelder enthalten"
    assert all(m.fill_policy == "never" for m in sig)


@pytest.mark.parametrize("name", MAPPINGS)
def test_legal_order_fields_are_marked(name):
    t = _load(name)
    for m in t.fields:
        if m.source and FIELD_REGISTRY[m.source].legal_relevant:
            assert m.legal_relevant or m.kind in (MappingKind.TEXT, MappingKind.AMOUNT), m.pdf_field


def test_json_files_are_utf8_and_pretty():
    for name in MAPPINGS:
        raw = (ROOT / "config" / "field_schemas" / name).read_text("utf-8")
        json.loads(raw)
        assert "Rückerstattung" in raw or "Übernahme" in raw
