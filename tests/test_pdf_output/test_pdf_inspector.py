"""AcroForm-Inspektion an den Originalvorlagen und an synthetischen PDFs."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pdf_test_helpers import make_form_pdf, requires_originals

from conftest import SL13065_PDF, SL18477_PDF
from insurance_app.pdf.inspector import (
    PdfOpenError,
    format_af_number,
    inspect_pdf_form,
    option_label_near,
    parse_number_format,
    pdf_name,
)


@requires_originals
def test_sl13065_fields_and_permissions():
    ins = inspect_pdf_form(SL13065_PDF)
    assert ins.page_count == 1
    assert ins.page_sizes == [(595.276, 841.89)]
    assert ins.has_acroform and not ins.is_xfa
    assert ins.signed_signature_fields == []
    p = ins.permissions
    assert p.encrypted and not p.needs_password
    assert p.can_fill_forms and p.can_annotate and p.can_print and p.can_copy
    assert not p.can_modify and not p.can_assemble
    assert not p.can_flatten
    assert p.encryption_method and "AES" in p.encryption_method
    assert len(ins.fields) == 23
    types = {f.name: f.field_type for f in ins.fields}
    assert types["Versicherung Nummer"] == "text"
    assert types["Abfindung"] == "checkbox"
    assert types["Rückerstattung"] == "checkbox"
    assert types["Unterschrift"] == "signature"
    assert types["Formular zurücksetzen"] == "button"
    abf = ins.field("Abfindung")
    assert abf is not None and abf.on_states == ["Ja"] and abf.widgets[0].current_state == "Off"
    day, month, year = ins.field("Datum 1.1"), ins.field("Datum 1.21"), ins.field("Datum 1.32")
    assert (day.max_len, month.max_len, year.max_len) == (2, 2, 4)
    assert year.number_format == {
        "decimals": 0,
        "sep_style": 1,
        "neg_style": 0,
        "curr_style": 0,
        "currency": "",
        "currency_prepend": True,
    }
    nr = ins.field("Versicherung Nummer")
    assert nr.font == "Arial" and nr.font_size == 10.0 and nr.max_len is None
    assert nr.align == "left" and day.align == "center"
    assert not nr.read_only and not nr.multiline and not nr.comb
    assert all(not f.has_value for f in ins.fields)
    # Koordinaten oben links: Versicherungsnummer liegt im oberen Drittel
    x0, y0, x1, y1 = nr.widgets[0].rect
    assert 250 < y0 < 260 and x1 > x0 and y1 > y0


@requires_originals
def test_sl18477_radio_states_and_number_formats():
    ins = inspect_pdf_form(SL18477_PDF)
    assert ins.page_count == 6
    assert len(ins.fields) == 51  # 51 Feldnamen (Optionsgruppen zählen einmal)
    radios = {f.name: f for f in ins.fields if f.field_type == "radio"}
    assert set(radios) == {
        "Fortführung der Versicherung",
        "Beitrag für den Zwischenzeitraum",
        "politisch exponierte Person",
        "Beitragszahlung",
    }
    assert radios["Fortführung der Versicherung"].on_states == ["0", "1", "2"]
    assert radios["Beitrag für den Zwischenzeitraum"].on_states == ["0", "1"]
    assert radios["politisch exponierte Person"].on_states == ["0", "1", "2"]
    assert radios["Beitragszahlung"].on_states == ["0", "1"]
    assert radios["politisch exponierte Person"].pages == [2]
    for name in ("reduzierter Beitrag", "privat gezahlter Beitrag für den Zwischenzeitraum"):
        nf = ins.field(name).number_format
        assert nf["decimals"] == 2 and nf["sep_style"] == 2
    sigs = [f.name for f in ins.fields if f.field_type == "signature"]
    assert sigs == [f"Unterschriftsfeld {i}" for i in range(1, 5)]
    assert ins.field("Unterschriftsfeld 4").pages == [3]
    # Seiten 4–6 ohne Felder
    assert not any(p in (4, 5, 6) for f in ins.fields for p in f.pages)


@requires_originals
@pytest.mark.parametrize(
    ("field", "state", "lines", "expected"),
    [
        ("Fortführung der Versicherung", "0", 1, "unverändertem Beitrag"),
        ("Fortführung der Versicherung", "1", 1, "reduzierten"),
        ("Fortführung der Versicherung", "2", 1, "beitragsfrei"),
        ("Beitrag für den Zwischenzeitraum", "0", 1, "beitragsfrei"),
        ("Beitrag für den Zwischenzeitraum", "1", 1, "beitragspflichtig"),
        ("politisch exponierte Person", "0", 1, "nein"),
        ("politisch exponierte Person", "1", 2, "übt/übte folgendes Amt"),
        ("politisch exponierte Person", "2", 3, "Familienmitglied"),
        ("politisch exponierte Person", "2", 3, "nahestehende Person"),
        ("Beitragszahlung", "0", 1, "SEPA-Lastschriftmandat"),
        ("Beitragszahlung", "1", 1, "per Rechnung"),
        ("Beitragszahlung", "1", 1, "Überweisung"),
        ("Fortführung der Versicherung durch den neuen Arbeitgeber", "Ja", 1, "neuen Arbeitgeber"),
        ("Versicherung durch die versicherte Person", "Ja", 1, "Private Fortführung"),
    ],
)
def test_sl18477_radio_semantics_match_pdf_text(field, state, lines, expected):
    """Die Zustände in den Zuordnungen müssen zum Text neben dem Widget passen."""
    label = option_label_near(SL18477_PDF, field, state, lines=lines)
    assert expected in label


@requires_originals
def test_sl18477_radio_semantics_are_exclusive():
    assert "reduziert" not in option_label_near(SL18477_PDF, "Fortführung der Versicherung", "0")
    assert "beitragspflichtig" not in option_label_near(SL18477_PDF, "Beitrag für den Zwischenzeitraum", "0")
    one = option_label_near(SL18477_PDF, "politisch exponierte Person", "1", lines=2)
    assert "Familienmitglied" not in one
    assert "SEPA" not in option_label_near(SL18477_PDF, "Beitragszahlung", "1")


@requires_originals
def test_sl18477_mapping_choice_maps_match_labels():
    """Gegenprobe: choice_map der mitgelieferten Zuordnung zeigt auf die richtigen Zustände."""
    from pdf_test_helpers import load_template

    t = load_template("swisslife_sl18477.json")
    expectations = {
        "Fortführung der Versicherung": {
            "unchanged": "unverändert",
            "reduced": "reduziert",
            "exempt": "beitragsfrei",
        },
        "Beitrag für den Zwischenzeitraum": {"exempt": "beitragsfrei", "paying": "beitragspflichtig"},
        "Beitragszahlung": {"sepa": "SEPA", "transfer": "Rechnung"},
    }
    for field, mapping in expectations.items():
        m = t.mapping(field)
        for key, word in mapping.items():
            assert word in option_label_near(SL18477_PDF, field, m.choice_map[key])
    pep = t.mapping("politisch exponierte Person")
    assert option_label_near(SL18477_PDF, pep.pdf_field, pep.choice_map["no"]) == "nein"
    assert "übt/übte" in option_label_near(SL18477_PDF, pep.pdf_field, pep.choice_map["yes_office"], lines=2)
    assert "Familienmitglied" in option_label_near(
        SL18477_PDF, pep.pdf_field, pep.choice_map["yes_related"], lines=3
    )


@requires_originals
def test_sl13065_refund_checkbox_means_no_refund():
    label = option_label_near(SL13065_PDF, "Rückerstattung", "Ja")
    assert "nicht zurückerstattet" in label
    assert "Abfindung" in option_label_near(SL13065_PDF, "Abfindung", "Ja")


def test_option_label_near_unknown_field_returns_empty(tmp_path):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    assert option_label_near(pdf, "gibt es nicht") == ""
    assert option_label_near(pdf, "Zustimmung", "Nein") == ""
    assert "Ich stimme zu" in option_label_near(pdf, "Zustimmung", "Yes")


def test_synthetic_form_unencrypted(tmp_path):
    ins = inspect_pdf_form(make_form_pdf(tmp_path / "muster.pdf"))
    assert not ins.permissions.encrypted
    assert ins.permissions.can_modify and ins.permissions.can_flatten
    assert ins.permissions.raw == -1
    assert {f.name for f in ins.fields} == {"Versicherungsnummer", "Name", "Zustimmung", "Unterschrift"}
    assert ins.field("Zustimmung").on_states == ["Yes"]


def test_synthetic_form_restricted_permissions(tmp_path):
    ins = inspect_pdf_form(make_form_pdf(tmp_path / "muster.pdf", encryption="no_modify"))
    p = ins.permissions
    assert p.encrypted and p.can_fill_forms and not p.can_modify and not p.can_flatten


def test_signed_signature_field_is_reported(tmp_path):
    ins = inspect_pdf_form(make_form_pdf(tmp_path / "muster.pdf", signed=True))
    assert ins.signed_signature_fields == ["Unterschrift"]
    assert ins.field("Unterschrift").signed


def test_password_protected_pdf(tmp_path):
    pdf = make_form_pdf(tmp_path / "muster.pdf", encryption="password")
    ins = inspect_pdf_form(pdf)
    assert ins.permissions.needs_password and ins.fields == [] and ins.page_count == 0
    unlocked = inspect_pdf_form(pdf, password="muster")
    assert not unlocked.permissions.needs_password
    assert unlocked.field("Name") is not None


def test_unreadable_file_raises(tmp_path):
    bad = tmp_path / "kaputt.pdf"
    bad.write_bytes(b"kein pdf")
    with pytest.raises(PdfOpenError):
        inspect_pdf_form(bad)
    with pytest.raises(PdfOpenError):
        inspect_pdf_form(tmp_path / "fehlt.pdf")


def test_parse_number_format_and_formatting():
    assert parse_number_format('AFNumber_Format(2, 2, 0, 0, "", true);')["sep_style"] == 2
    assert parse_number_format('AFNumber_Format(2, 0, 0, 0, "€", false);')["currency"] == "€"
    assert parse_number_format("AFDate_FormatEx('dd.mm.yyyy');") is None
    assert parse_number_format(None) is None
    assert format_af_number("1234.56") == "1.234,56"
    assert format_af_number(Decimal("1234567.5"), 2, 0) == "1,234,567.50"
    assert format_af_number("1234.5", 2, 1) == "1234.50"
    assert format_af_number("1234.5", 2, 3) == "1234,50"
    assert format_af_number("2027", 0, 1) == "2027"
    assert format_af_number("89.5") == "89,50"
    assert format_af_number("0.005") == "0,01"
    with pytest.raises(ValueError):
        format_af_number("abc")


def test_pdf_name_escaping():
    assert pdf_name("Ja") == "/Ja"
    assert pdf_name("0") == "/0"
    assert pdf_name("Auswahl 1") == "/Auswahl#201"
    assert pdf_name("Ä") == "/#C3#84"
    with pytest.raises(ValueError):
        pdf_name("")
