"""Vorlagen-Studio: automatische Erst-Zuordnung (nur offensichtliche Vertragsdaten)."""

from __future__ import annotations

from tpl_test_helpers import make_import_pdf, requires_originals, sha256

from conftest import SL13065_PDF
from insurance_app.pdf.inspector import inspect_pdf_form
from insurance_app.templates.schema import CatalogEntry, FillPolicy, MappingKind
from insurance_app.templates.studio import auto_mapping, derive_insurer_code, derive_short_title

ENTRY = CatalogEntry(
    id="tecis_bestandsuebertrag",
    number=7,
    group="Betreuung & Enthaftung",
    title="Bestandsübertrag / Betreuerwechsel an tecis AG",
    insurer="tecis",
    description="Muster",
)


def test_auto_mapping_synthetic(tmp_path):
    pdf = make_import_pdf(tmp_path / "MUSTER.pdf")
    draft = auto_mapping(ENTRY, pdf, inspect_pdf_form(pdf), "2026-11")
    d = draft.definition
    assert d is not None and not d.mapping_confirmed
    by_field = {m.pdf_field: m for m in d.fields}
    assert by_field["Versicherungsnummer"].source == "contract.number"
    assert by_field["Versicherungsnummer"].required
    assert by_field["Geburtsort"].source == "insured.birth_place"
    assert by_field["Staatsangehörigkeit"].source == "insured.nationality"
    parts = {
        m.pdf_field: (m.kind, m.source, m.date_part) for m in d.fields if m.kind == MappingKind.DATE_PART
    }
    assert parts == {
        "Geburtsdatum Tag": (MappingKind.DATE_PART, "insured.birth_date", "day"),
        "Geburtsdatum Monat": (MappingKind.DATE_PART, "insured.birth_date", "month"),
        "Geburtsdatum Jahr": (MappingKind.DATE_PART, "insured.birth_date", "year"),
    }
    sig_dates = [m for m in d.fields if m.kind == MappingKind.SIGNATURE_DATE]
    assert {m.pdf_field for m in sig_dates} == {
        "Datum Unterschrift.1",
        "Datum Unterschrift.2",
        "Datum Unterschrift.3",
    }
    assert all(m.fill_policy == FillPolicy.NEVER for m in sig_dates)
    assert by_field["Unterschrift"].kind == MappingKind.SIGNATURE
    assert by_field["Unterschrift"].fill_policy == FillPolicy.NEVER
    assert d.ignored_fields == ["Zurücksetzen"]
    # alles andere bleibt offen
    assert set(draft.unassigned_fields) == {"Name", "IBAN", "Bemerkung", "Einverstanden"}
    assert draft.suggestions["Name"] == "insured.last_name"
    assert draft.suggestions["IBAN"] == "order.payment_iban"
    assert "Einverstanden" in draft.hints
    # Rechtlich relevante Auswahlen werden nie automatisch zugeordnet
    assert not any(m.kind in (MappingKind.CHECKBOX, MappingKind.RADIO) for m in d.fields)
    assert not any(m.source and m.source.startswith("order.") for m in d.fields)


@requires_originals
def test_auto_mapping_on_sl13065_original():
    ins = inspect_pdf_form(SL13065_PDF)
    entry = ENTRY.model_copy(
        update={"id": "swisslife_sl13065_freigabe", "form_number": "SL13065", "insurer": "Swiss Life"}
    )
    draft = auto_mapping(entry, SL13065_PDF, ins, "2024-09")
    d = draft.definition
    by_field = {m.pdf_field: m for m in d.fields}
    assert by_field["Versicherung Nummer"].source == "contract.number"
    assert by_field["Unterschrift"].kind == MappingKind.SIGNATURE
    for part in ("1.1", "1.21", "1.32"):
        assert by_field[f"Datum Unterschrift {part}"].kind == MappingKind.SIGNATURE_DATE
    assert d.ignored_fields == ["Formular zurücksetzen"]
    assert "Abfindung" in draft.unassigned_fields and "Rückerstattung" in draft.unassigned_fields
    assert "Datum 1.1" in draft.unassigned_fields and "Datumsteil" in draft.hints["Datum 1.1"]
    assert draft.suggestions["Name Vorname"] == "{insured.last_name}, {insured.first_name}"
    assert d.insurer_code == "SWISS" and d.form_number == "SL13065"
    assert d.sha256 == sha256(SL13065_PDF) and d.page_count == 1


def test_derived_codes():
    assert derive_insurer_code("tecis") == "TECIS"
    assert derive_insurer_code("versichererunabhängig") == "ALLGEMEIN"
    assert derive_insurer_code("HDI Versicherung AG") == "HDI"
    assert derive_short_title("Bestandsübertrag / Betreuerwechsel an tecis AG") == "Bestandsuebertrag"
    assert derive_short_title("Widerruf – Antrag") == "Widerruf"
    assert derive_short_title("") == "Vorlage"


def test_auto_mapping_without_fields(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "MUSTER ohne Felder")
    pdf = tmp_path / "leer.pdf"
    doc.save(pdf)
    doc.close()
    draft = auto_mapping(ENTRY, pdf, inspect_pdf_form(pdf), "1")
    assert draft.definition.fields == [] and draft.unassigned_fields == []
    assert draft.definition.form_number == "VORLAGE7"
