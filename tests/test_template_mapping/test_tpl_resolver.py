"""Werteauflösung (FillPlan): alle Schutz- und Formatregeln."""

from __future__ import annotations

import pytest
from tpl_test_helpers import (
    PERSONAL_VALUES,
    button_info,
    fv,
    inspection,
    load_template,
    manual,
    requires_originals,
    sl13065_values,
    sl18477_employer,
    sl18477_private,
    split,
    template,
    text_info,
)

from conftest import SL13065_PDF, SL18477_PDF
from insurance_app.core.models import FieldStatus, Severity, ValidationMessage
from insurance_app.pdf.inspector import inspect_pdf_form
from insurance_app.templates.resolver import FillPlan, resolve_fill_values
from insurance_app.templates.schema import Acknowledgment, Condition, FieldMapping, FillPolicy, MappingKind


def codes(plan: FillPlan, blocking: bool | None = None) -> list[str]:
    return [i.code for i in plan.checklist if blocking is None or i.blocking == blocking]


def messages(plan: FillPlan) -> list[str]:
    return [i.message for i in plan.checklist]


def resolve(t, values, ins=None, mode="preview", extra=None) -> FillPlan:
    contract, order = split(values)
    return resolve_fill_values(t, contract, order, ins, mode=mode, extra_messages=extra)


NR = FieldMapping(
    pdf_field="Nr", label="Versicherung Nr.", kind=MappingKind.TEXT, source="contract.number", required=True
)


# ---------------------------------------------------------------------------
# Status-Regeln
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [FieldStatus.CONFIRMED, FieldStatus.MANUAL])
def test_released_values_are_filled(status):
    plan = resolve(template(NR), [fv("contract.number", "MUSTER-4711", status=status)])
    assert plan.value_for("Nr").text == "MUSTER-4711"
    assert plan.value_for("Nr").source_keys == ["contract.number"]
    assert not plan.blocking


def test_extracted_required_is_blocking_and_not_filled():
    plan = resolve(template(NR), [fv("contract.number", "MUSTER-4711", status=FieldStatus.EXTRACTED)])
    assert plan.values == []
    item = next(i for i in plan.checklist if i.code == "VALUE_NOT_CONFIRMED")
    assert item.blocking and item.severity == Severity.ERROR
    assert item.message == "Vertrags-/Versicherungsnummer: Wert noch nicht bestätigt"
    assert item.field_key == "contract.number" and item.pdf_field == "Nr"


def test_extracted_optional_is_warning_only():
    m = NR.model_copy(update={"required": False})
    plan = resolve(template(m), [fv("contract.number", "MUSTER-4711", status=FieldStatus.EXTRACTED)])
    assert plan.values == [] and not plan.blocking
    assert codes(plan) == ["VALUE_NOT_CONFIRMED"]
    assert plan.checklist[0].severity == Severity.WARNING
    assert plan.open_items == plan.checklist


def test_dedup_upgrades_to_blocking():
    optional = NR.model_copy(update={"required": False, "pdf_field": "Nr2"})
    plan = resolve(
        template(optional, NR), [fv("contract.number", "MUSTER-4711", status=FieldStatus.EXTRACTED)]
    )
    items = [i for i in plan.checklist if i.code == "VALUE_NOT_CONFIRMED"]
    assert len(items) == 1 and items[0].blocking
    assert items[0].message == "Vertrags-/Versicherungsnummer: Wert noch nicht bestätigt"


def test_not_applicable_satisfies_required():
    plan = resolve(template(NR), [fv("contract.number", None, status=FieldStatus.NOT_APPLICABLE)])
    assert plan.values == [] and plan.checklist == []


def test_conflict_is_always_blocking():
    m = NR.model_copy(update={"required": False})
    plan = resolve(template(m), [fv("contract.number", "MUSTER-4711", status=FieldStatus.CONFLICT)])
    assert plan.values == [] and codes(plan, blocking=True) == ["CONFLICT_UNRESOLVED"]
    assert "Konflikt nicht gelöst" in plan.checklist[0].message


@pytest.mark.parametrize(
    ("status", "code"),
    [(FieldStatus.UNCLEAR, "VALUE_UNCLEAR"), (FieldStatus.MISSING, "REQUIRED_MISSING")],
)
def test_unclear_and_missing_block_when_required(status, code):
    plan = resolve(template(NR), [fv("contract.number", None, status=status)])
    assert codes(plan, blocking=True) == [code]
    optional = resolve(
        template(NR.model_copy(update={"required": False})), [fv("contract.number", None, status=status)]
    )
    assert not optional.blocking


def test_missing_entirely_counts_as_missing():
    plan = resolve(template(NR), [])
    assert codes(plan) == ["REQUIRED_MISSING"]
    assert plan.checklist[0].message == "Vertrags-/Versicherungsnummer: Angabe fehlt"


def test_released_but_empty_counts_as_missing():
    plan = resolve(template(NR), [fv("contract.number", "  ", "")])
    assert codes(plan) == ["REQUIRED_MISSING"]


# ---------------------------------------------------------------------------
# Bedingungen
# ---------------------------------------------------------------------------


def _pep_template():
    cond = [Condition(field="order.pep_status", equals="yes_office")]
    return template(
        FieldMapping(
            pdf_field="Amt",
            label="Amt",
            kind=MappingKind.TEXT,
            source="order.pep_office",
            required=True,
            conditions=cond,
        ),
        FieldMapping(
            pdf_field="Amt2",
            label="Amt (Wiederholung)",
            kind=MappingKind.TEXT,
            source="order.pep_office",
            required=True,
            conditions=cond,
        ),
        FieldMapping(
            pdf_field="Optional",
            label="Optional",
            kind=MappingKind.TEXT,
            # nicht rechtlich relevante Quelle: rechtlich relevante Registerfelder
            # (z. B. order.measure) gelten seit LEGAL-2 auch ohne Zuordnungs-Kennzeichen als Pflicht
            source="contract.tariff",
            conditions=[Condition(field="order.reason")],
        ),
    )


def test_undecided_condition_gives_one_item_per_condition_field():
    plan = resolve(_pep_template(), [manual("order.pep_office", "Bürgermeister")])
    assert plan.values == []
    cond_items = [i for i in plan.checklist if i.code == "CONDITION_UNDECIDED"]
    assert len(cond_items) == 1
    assert cond_items[0].message == "Bitte zuerst eine Auswahl treffen: Politisch exponierte Person (PeP)"
    assert cond_items[0].blocking and cond_items[0].field_key == "order.pep_status"


def test_unmet_condition_leaves_field_empty_and_not_required():
    plan = resolve(_pep_template(), [manual("order.pep_status", "no"), manual("order.reason", "x")])
    assert plan.values == [] and plan.checklist == []


def test_met_condition_requires_value():
    plan = resolve(_pep_template(), [manual("order.pep_status", "yes_office")])
    assert codes(plan, blocking=True) == ["REQUIRED_MISSING"]
    plan = resolve(
        _pep_template(), [manual("order.pep_status", "yes_office"), manual("order.pep_office", "Rat")]
    )
    assert [v.pdf_field for v in plan.values] == ["Amt", "Amt2"] and not plan.blocking


def test_not_applicable_condition_field_is_unmet():
    # Nicht rechtlich relevantes Bedingungsfeld: „nicht relevant“ = Bedingung nicht erfüllt.
    m = FieldMapping(
        pdf_field="Inst",
        label="Geldinstitut",
        kind=MappingKind.TEXT,
        source="order.payment_institution",
        required=True,
        conditions=[Condition(field="contract.tariff")],
    )
    plan = resolve(template(m), [fv("contract.tariff", None, status=FieldStatus.NOT_APPLICABLE)])
    assert plan.checklist == [] and plan.values == []


def test_pdf_1_not_applicable_on_legal_condition_field_is_undecided():
    # Rechtliche Auswahl (PeP) als „nicht relevant“ markiert: keine Entscheidung -> blockiert.
    plan = resolve(_pep_template(), [fv("order.pep_status", None, status=FieldStatus.NOT_APPLICABLE)])
    items = [i for i in plan.checklist if i.blocking]
    assert [(i.code, i.field_key) for i in items] == [("CONDITION_UNDECIDED", "order.pep_status")]


def test_in_condition():
    m = FieldMapping(
        pdf_field="IBAN",
        label="IBAN",
        kind=MappingKind.TEXT,
        source="order.payment_iban",
        required=True,
        conditions=[Condition(field="order.payment_method", **{"in": ["sepa", "transfer"]})],
    )
    plan = resolve(
        template(m),
        [manual("order.payment_method", "transfer"), manual("order.payment_iban", "DE89370400440532013000")],
    )
    assert plan.value_for("IBAN").text == "DE89 3704 0044 0532 0130 00"


def test_sl18477_missing_continuation_mode_reports_decision_once():
    t = load_template("swisslife_sl18477.json")
    values = [v for v in sl18477_private() if v.key not in {"order.continuation_mode"}]
    plan = resolve(t, values)
    items = [i for i in plan.checklist if i.field_key == "order.continuation_mode"]
    assert len(items) == 1
    assert items[0].message == "Bitte ausdrücklich entscheiden: Art der Fortführung" and items[0].blocking
    assert plan.value_for("Name Vorname") is None  # Abschnitt C hängt von der Entscheidung ab


# ---------------------------------------------------------------------------
# Unterschriften, manual_only, rechtliche Entscheidungen
# ---------------------------------------------------------------------------


def test_signature_fields_never_filled_and_counted():
    t = load_template("swisslife_sl18477.json")
    plan = resolve(t, sl18477_private())
    info = next(i for i in plan.checklist if i.code == "SIGNATURE_FIELDS_EMPTY")
    assert (
        info.message
        == "16 Unterschrifts- und Datumsfelder bleiben leer (Unterschrift erfolgt handschriftlich)."
    )
    assert info.severity == Severity.INFO and not info.blocking
    never = {m.pdf_field for m in t.fields if m.fill_policy == FillPolicy.NEVER}
    assert not never & {v.pdf_field for v in plan.values}
    plan13 = resolve(load_template("swisslife_sl13065.json"), sl13065_values())
    assert any(m.startswith("4 Unterschrifts-") for m in messages(plan13))


def test_manual_only_policy():
    m = NR.model_copy(update={"fill_policy": FillPolicy.MANUAL_ONLY})
    confirmed = resolve(template(m), [fv("contract.number", "MUSTER-4711")])
    assert confirmed.values == [] and codes(confirmed, blocking=True) == ["MANUAL_ENTRY_REQUIRED"]
    manual_plan = resolve(template(m), [manual("contract.number", "MUSTER-4711")])
    assert manual_plan.value_for("Nr").text == "MUSTER-4711" and not manual_plan.blocking


def test_legal_checkbox_needs_explicit_decision():
    t = load_template("swisslife_sl13065.json")
    values = [v for v in sl13065_values() if v.key != "order.severance_consent"]
    plan = resolve(t, values)
    item = next(i for i in plan.checklist if i.field_key == "order.severance_consent")
    assert item.code == "LEGAL_DECISION_MISSING" and item.blocking
    assert (
        item.message
        == "Bitte ausdrücklich entscheiden: Zustimmung zur Abfindung gem. § 3 BetrAVG (im Kündigungsfall)"
    )
    assert plan.value_for("Abfindung") is None


def test_legal_checkbox_false_is_valid_decision():
    t = load_template("swisslife_sl13065.json")
    values = [v for v in sl13065_values() if v.key != "order.severance_consent"]
    plan = resolve(t, [*values, manual("order.severance_consent", "false")])
    assert plan.value_for("Abfindung").state == "Off"
    assert plan.value_for("Rückerstattung").state == "Off"
    assert not plan.blocking


def test_legal_checkbox_not_required_still_needs_decision():
    m = FieldMapping(
        pdf_field="Zustimmung",
        label="Zustimmung",
        kind=MappingKind.CHECKBOX,
        source="order.severance_consent",
        legal_relevant=True,
    )
    plan = resolve(template(m), [])
    assert codes(plan, blocking=True) == ["LEGAL_DECISION_MISSING"]


def test_checkbox_invalid_bool_value():
    m = FieldMapping(
        pdf_field="Z", label="Zustimmung", kind=MappingKind.CHECKBOX, source="order.severance_consent"
    )
    plan = resolve(template(m), [manual("order.severance_consent", "vielleicht")])
    assert codes(plan, blocking=True) == ["INVALID_VALUE"]


# ---------------------------------------------------------------------------
# Checkboxen / Optionsgruppen und Ein-Zustände
# ---------------------------------------------------------------------------


CB = FieldMapping(
    pdf_field="Z", label="Zustimmung", kind=MappingKind.CHECKBOX, source="order.severance_consent"
)


def test_checkbox_on_state_from_inspection():
    plan = resolve(
        template(CB), [manual("order.severance_consent", "true")], ins=inspection(button_info("Z", ["Yes"]))
    )
    assert plan.value_for("Z").state == "Yes" and not plan.checklist


def test_checkbox_without_inspection_falls_back_and_flags():
    preview = resolve(template(CB), [manual("order.severance_consent", "true")])
    assert preview.value_for("Z").state == "Ja"
    assert codes(preview, blocking=False) == ["ONSTATE_UNVERIFIED"]
    final = resolve(template(CB), [manual("order.severance_consent", "true")], mode="final")
    assert {"NO_INSPECTION", "ONSTATE_UNVERIFIED"} <= set(codes(final, blocking=True))


def test_checkbox_without_on_state_in_pdf():
    plan = resolve(
        template(CB), [manual("order.severance_consent", "true")], ins=inspection(button_info("Z", []))
    )
    assert codes(plan, blocking=True) == ["ONSTATE_MISSING"] and plan.values == []


def test_checkbox_checked_when_choice():
    t = load_template("swisslife_sl18477.json")
    plan = resolve(t, sl18477_employer())
    assert plan.value_for("Fortführung der Versicherung durch den neuen Arbeitgeber").state == "Ja"
    assert plan.value_for("Versicherung durch die versicherte Person").state == "Off"
    assert plan.value_for("private Fortführung für den Zwischenzeitraum").state == "Off"
    assert plan.value_for("Beitrag für den Zwischenzeitraum") is None  # Bedingung nicht erfüllt


RADIO = FieldMapping(
    pdf_field="Fortführung",
    label="Beitragsvariante",
    kind=MappingKind.RADIO,
    source="order.private_contribution_option",
    choice_map={"unchanged": "0", "reduced": "1", "exempt": "2"},
    required=True,
    legal_relevant=True,
)


def test_radio_choice_map():
    plan = resolve(template(RADIO), [manual("order.private_contribution_option", "reduced")])
    assert plan.value_for("Fortführung").state == "1"
    ins = inspection(button_info("Fortführung", ["0", "1", "2"], "radio"))
    assert (
        resolve(template(RADIO), [manual("order.private_contribution_option", "exempt")], ins=ins)
        .value_for("Fortführung")
        .state
        == "2"
    )


def test_radio_invalid_choice_and_missing_state():
    plan = resolve(template(RADIO), [manual("order.private_contribution_option", "anders")])
    assert codes(plan, blocking=True) == ["INVALID_CHOICE"] and plan.values == []
    ins = inspection(button_info("Fortführung", ["0", "1"], "radio"))
    plan = resolve(template(RADIO), [manual("order.private_contribution_option", "exempt")], ins=ins)
    assert codes(plan, blocking=True) == ["ONSTATE_MISSING"]


def test_radio_missing_decision():
    plan = resolve(template(RADIO), [])
    assert codes(plan, blocking=True) == ["LEGAL_DECISION_MISSING"]
    assert (
        plan.checklist[0].message == "Bitte ausdrücklich entscheiden: Private Fortführung: Beitragsvariante"
    )


# ---------------------------------------------------------------------------
# compose, Datum, Betrag, Formatierung
# ---------------------------------------------------------------------------


def test_compose_optional_part_missing():
    t = load_template("swisslife_sl13065.json")
    plan = resolve(t, sl13065_values())
    assert plan.value_for("Versicherungsnehmer").text == "Muster Bäckerei GmbH & Co. KG"
    assert plan.value_for("Versicherte Person").text == "Erika Musterfrau"
    assert plan.value_for("Name Vorname").text == "Musterfrau, Erika"
    assert plan.value_for("Straße Hausnummer").text == "Große Straße 12a"
    vn = plan.value_for("Versicherungsnehmer")
    assert set(vn.source_keys) == {"policyholder.first_name", "policyholder.name"}


def test_compose_required_part_missing_is_blocking():
    t = load_template("swisslife_sl13065.json")
    values = [v for v in sl13065_values() if v.key != "insured.first_name"]
    plan = resolve(t, values)
    assert plan.value_for("Name Vorname") is None and plan.value_for("Versicherte Person") is None
    items = [i for i in plan.checklist if i.field_key == "insured.first_name"]
    assert len(items) == 1 and items[0].code == "REQUIRED_MISSING" and items[0].blocking


def test_compose_not_applicable_strips_dangling_separator():
    t = load_template("swisslife_sl13065.json")
    values = [v for v in sl13065_values() if v.key != "insured.first_name"]
    plan = resolve(t, [*values, fv("insured.first_name", None, status=FieldStatus.NOT_APPLICABLE)])
    assert plan.value_for("Name Vorname").text == "Musterfrau"
    assert plan.value_for("Versicherte Person").text == "Musterfrau"


def test_compose_optional_part_unconfirmed_is_not_silently_dropped():
    t = load_template("swisslife_sl13065.json")
    values = [v for v in sl13065_values() if v.key != "insured.house_number"]
    plan = resolve(t, [*values, fv("insured.house_number", "12a", status=FieldStatus.EXTRACTED)])
    assert plan.value_for("Straße Hausnummer") is None
    assert "VALUE_NOT_CONFIRMED" in codes(plan, blocking=True)


def test_compose_only_optional_parts_missing_is_blocking_when_required():
    m = FieldMapping(
        pdf_field="Adresse",
        label="Adresse",
        kind=MappingKind.TEXT,
        compose="{insured.street?} {insured.house_number?}",
        required=True,
    )
    plan = resolve(template(m), [])
    assert codes(plan, blocking=True) == ["REQUIRED_MISSING"]
    assert plan.checklist[0].message == "Adresse: Angabe fehlt"
    na = [fv("insured.street", None, status=FieldStatus.NOT_APPLICABLE)]
    assert resolve(template(m), na).checklist == []


def test_mapped_field_missing_in_pdf_is_blocking():
    ins = inspection(text_info("Anderes Feld"))
    plan = resolve(template(NR), [fv("contract.number", "MUSTER-4711")], ins=ins)
    assert codes(plan, blocking=True) == ["FIELD_MISSING_IN_PDF"] and plan.values == []


def test_date_parts_from_date_and_month():
    t13 = load_template("swisslife_sl13065.json")
    plan = resolve(t13, sl13065_values())
    assert [plan.value_for(f"Datum 1.{s}").text for s in ("1", "21", "32")] == ["01", "02", "1980"]
    assert [plan.value_for(f"Datum 3.{s}").text for s in ("1", "21", "32")] == ["30", "09", "2026"]
    t18 = load_template("swisslife_sl18477.json")
    plan = resolve(t18, sl18477_employer())
    assert (plan.value_for("Datum 1.1").text, plan.value_for("Datum 1.21").text) == ("01", "2027")
    assert (plan.value_for("Datum 2.1").text, plan.value_for("Datum 2.21").text) == ("02", "2027")


def test_date_part_from_raw_manual_value_and_invalid_combination():
    day = FieldMapping(
        pdf_field="Tag", label="Tag", kind=MappingKind.DATE_PART, source="insured.birth_date", date_part="day"
    )
    plan = resolve(template(day), [raw_manual("insured.birth_date", "1.2.1980")])
    assert plan.value_for("Tag").text == "01"
    month_day = FieldMapping(
        pdf_field="Tag",
        label="Tag",
        kind=MappingKind.DATE_PART,
        source="order.premium_start_month",
        date_part="day",
    )
    plan = resolve(template(month_day), [manual("order.premium_start_month", "2027-02")])
    assert codes(plan, blocking=True) == ["INVALID_VALUE"]


def raw_manual(key: str, value: str):
    from insurance_app.core.models import FieldValue

    return FieldValue(key=key, value=value, status=FieldStatus.MANUAL)


AMOUNT = FieldMapping(
    pdf_field="Betrag",
    label="Betrag",
    kind=MappingKind.AMOUNT,
    source="order.reduced_premium_amount",
    required=True,
)


def test_amount_text_and_raw_value():
    plan = resolve(template(AMOUNT), [manual("order.reduced_premium_amount", "1234.56")])
    v = plan.value_for("Betrag")
    assert (v.text, v.raw_value, v.stored_value) == ("1.234,56", "1234.56", "1234.56")
    ins = inspection(text_info("Betrag", width=60, number_format={"decimals": 2, "sep_style": 2}))
    v = resolve(template(AMOUNT), [manual("order.reduced_premium_amount", "89.5")], ins=ins).value_for(
        "Betrag"
    )
    assert (v.text, v.raw_value) == ("89,50", "89.50")


def test_amount_without_number_format_stores_display_text():
    ins = inspection(text_info("Betrag", width=80))
    v = resolve(template(AMOUNT), [manual("order.reduced_premium_amount", "1234.5")], ins=ins).value_for(
        "Betrag"
    )
    assert (v.text, v.raw_value, v.stored_value) == ("1.234,50", None, "1.234,50")


@pytest.mark.parametrize("value", ["-5.00", "abc", "NaN"])
def test_amount_invalid(value):
    plan = resolve(template(AMOUNT), [manual("order.reduced_premium_amount", value)])
    assert codes(plan, blocking=True) == ["INVALID_VALUE"] and plan.values == []


def test_text_formatting_by_data_type():
    t = template(
        FieldMapping(pdf_field="Geb", label="Geb", kind=MappingKind.TEXT, source="insured.birth_date"),
        FieldMapping(
            pdf_field="Monat", label="Monat", kind=MappingKind.TEXT, source="order.premium_start_month"
        ),
        FieldMapping(pdf_field="IBAN", label="IBAN", kind=MappingKind.TEXT, source="order.payment_iban"),
        FieldMapping(pdf_field="Zahl", label="Zahlweise", kind=MappingKind.TEXT, source="premium.frequency"),
        FieldMapping(pdf_field="Betrag", label="Beitrag", kind=MappingKind.TEXT, source="premium.amount"),
        FieldMapping(pdf_field="Bool", label="Bool", kind=MappingKind.TEXT, source="order.severance_consent"),
        FieldMapping(pdf_field="Fest", label="Fest", kind=MappingKind.STATIC, static_value="MUSTER  Text"),
    )
    plan = resolve(
        t,
        [
            fv("insured.birth_date", "01.02.1980", "1980-02-01"),
            manual("order.premium_start_month", "2027-02"),
            manual("order.payment_iban", "DE89370400440532013000"),
            fv("premium.frequency", "monatlich", "monthly"),
            fv("premium.amount", "1.234,56 EUR", "1234.56"),
            manual("order.severance_consent", "false"),
        ],
    )
    got = {v.pdf_field: v.text for v in plan.values}
    assert got == {
        "Geb": "01.02.1980",
        "Monat": "02.2027",
        "IBAN": "DE89 3704 0044 0532 0130 00",
        "Zahl": "monatlich",
        "Betrag": "1.234,56",
        "Bool": "nein",
        "Fest": "MUSTER Text",
    }


def test_multiline_keeps_line_breaks():
    m = FieldMapping(pdf_field="Text", label="Text", kind=MappingKind.MULTILINE, source="order.reason")
    plan = resolve(template(m), [manual("order.reason", "Erste  Zeile\r\nZweite Zeile")])
    assert plan.value_for("Text").text == "Erste Zeile\nZweite Zeile"


# ---------------------------------------------------------------------------
# Darstellbarkeit: Breite, Zeichen, Länge
# ---------------------------------------------------------------------------

FIRMA = FieldMapping(
    pdf_field="Firma", label="Firma", kind=MappingKind.TEXT, source="order.new_employer_company"
)


def test_text_fits_without_change():
    ins = inspection(text_info("Firma", width=464, size=9))
    v = resolve(
        template(FIRMA), [manual("order.new_employer_company", "Muster Bäckerei GmbH & Co. KG")], ins=ins
    )
    assert v.value_for("Firma").font_size is None


def test_text_shrinks_to_fit():
    ins = inspection(text_info("Firma", width=110, size=9))
    plan = resolve(
        template(FIRMA), [manual("order.new_employer_company", "Muster Bäckerei GmbH & Co. KG")], ins=ins
    )
    size = plan.value_for("Firma").font_size
    assert size is not None and 6.0 <= size < 9 and not plan.blocking


def test_text_too_long_is_blocking():
    ins = inspection(text_info("Firma", width=60, size=9))
    plan = resolve(
        template(FIRMA),
        [manual("order.new_employer_company", "Muster Bäckerei GmbH & Co. KG Zweig")],
        ins=ins,
    )
    assert codes(plan, blocking=True) == ["TEXT_TOO_LONG"] and plan.values == []
    assert plan.checklist[0].message == "Firma: Text passt nicht ins Feld – bitte kürzen."


def test_auto_font_size_field_gets_explicit_size():
    ins = inspection(text_info("Firma", width=300, size=0))
    v = resolve(template(FIRMA), [manual("order.new_employer_company", "Muster GmbH")], ins=ins).value_for(
        "Firma"
    )
    assert v.font_size == 10.0


def test_multiline_fit():
    m = FieldMapping(pdf_field="Text", label="Text", kind=MappingKind.MULTILINE, source="order.reason")
    ins = inspection(text_info("Text", width=200, size=9, multiline=True, height=40))
    ok = resolve(template(m), [manual("order.reason", "Kurzer Muster-Grund")], ins=ins)
    assert not ok.blocking
    too_long = resolve(template(m), [manual("order.reason", "Sehr langer Muster-Grund " * 30)], ins=ins)
    assert codes(too_long, blocking=True) == ["TEXT_TOO_LONG"]


def test_unsupported_characters_are_blocking():
    plan = resolve(template(FIRMA), [manual("order.new_employer_company", "Łódź Muster Sp. z o.o.")])
    item = plan.checklist[0]
    assert item.code == "GLYPH_UNSUPPORTED" and item.blocking
    assert "„Ł“" in item.message and "kann in dieser Vorlage nicht dargestellt werden" in item.message
    assert plan.values == []


def test_max_len_exceeded_is_blocking():
    m = FieldMapping(
        pdf_field="PLZ", label="PLZ", kind=MappingKind.TEXT, source="insured.postal_code", max_len=5
    )
    plan = resolve(template(m), [fv("insured.postal_code", "123456")])
    assert codes(plan, blocking=True) == ["MAX_LENGTH_EXCEEDED"]
    ins = inspection(text_info("PLZ", max_len=4))
    plan = resolve(
        template(m.model_copy(update={"max_len": None})), [fv("insured.postal_code", "12345")], ins=ins
    )
    assert codes(plan, blocking=True) == ["MAX_LENGTH_EXCEEDED"]
    assert "höchstens 4 Zeichen" in plan.checklist[0].message


# ---------------------------------------------------------------------------
# Bestätigungen und zusätzliche Meldungen
# ---------------------------------------------------------------------------


def test_acknowledgment_required():
    t = load_template("swisslife_sl18477.json")
    values = [v for v in sl18477_private() if v.key != "order.advice_waiver_acknowledged"]
    plan = resolve(t, values)
    item = next(i for i in plan.checklist if i.code == "ACKNOWLEDGMENT_MISSING")
    assert item.blocking and item.message.startswith("Bitte bestätigen: Abschnitt F (Beratungsverzicht)")
    plan = resolve(t, [*values, manual("order.advice_waiver_acknowledged", "false")])
    assert "ACKNOWLEDGMENT_MISSING" in codes(plan, blocking=True)
    plan = resolve(t, [*values, manual("order.advice_waiver_acknowledged", "true")])
    assert "ACKNOWLEDGMENT_MISSING" not in codes(plan)


def test_acknowledgment_with_conditions():
    ack = Acknowledgment(
        field="order.advice_waiver_acknowledged",
        text="Muster-Hinweis",
        conditions=[Condition(field="order.continuation_mode", equals="private")],
    )
    t = template(acks=[ack])
    assert codes(resolve(t, [manual("order.continuation_mode", "employer")])) == []
    assert codes(resolve(t, [manual("order.continuation_mode", "private")]), blocking=True) == [
        "ACKNOWLEDGMENT_MISSING"
    ]
    assert codes(resolve(t, []), blocking=True) == ["CONDITION_UNDECIDED"]


def test_extra_messages():
    extra = [
        ValidationMessage(
            severity=Severity.ERROR,
            code="IBAN_CHECKSUM",
            message="IBAN-Prüfziffer falsch",
            field_key="order.payment_iban",
        ),
        ValidationMessage(severity=Severity.WARNING, code="BAV_PRODUCT", message="Produkt prüfen"),
        ValidationMessage(severity=Severity.INFO, code="HINWEIS", message="Nur ein Hinweis"),
    ]
    plan = resolve(template(NR), [fv("contract.number", "MUSTER-4711")], extra=extra)
    by_code = {i.code: i for i in plan.checklist}
    assert (
        by_code["IBAN_CHECKSUM"].blocking
        and not by_code["BAV_PRODUCT"].blocking
        and not by_code["HINWEIS"].blocking
    )
    assert plan.blocking
    assert [i.code for i in plan.open_items] == ["IBAN_CHECKSUM", "BAV_PRODUCT"]
    assert plan.checklist[0].code == "IBAN_CHECKSUM"  # blockierende Punkte zuerst


def test_field_validation_errors_of_used_fields_block():
    value = fv("contract.number", "MUSTER-4711")
    value = value.model_copy(
        update={
            "validation": [
                ValidationMessage(severity=Severity.ERROR, code="CN_FORMAT", message="Nummer ungültig")
            ]
        }
    )
    plan = resolve(template(NR), [value])
    assert plan.value_for("Nr") is not None
    item = next(i for i in plan.checklist if i.code == "CN_FORMAT")
    assert item.blocking and item.field_key == "contract.number"


def test_invalid_mode():
    with pytest.raises(ValueError):
        resolve(template(NR), [], mode="export")


# ---------------------------------------------------------------------------
# Vollständige Pläne und Datenschutz
# ---------------------------------------------------------------------------


def test_full_plans_have_no_blocking_items():
    assert not resolve(load_template("swisslife_sl13065.json"), sl13065_values()).blocking
    private = resolve(load_template("swisslife_sl18477.json"), sl18477_private())
    assert not private.blocking, private.checklist
    assert (
        private.value_for("Firma") is None and private.value_for("Fortführung der Versicherung").state == "1"
    )
    assert private.value_for("reduzierter Beitrag").text == "1.234,56"
    assert private.value_for("politisch exponierte Person").state == "2"
    assert private.value_for("Beitragszahlung").state == "1"
    employer = resolve(load_template("swisslife_sl18477.json"), sl18477_employer())
    assert not employer.blocking, employer.checklist
    assert employer.value_for("Name Vorname") is None


@requires_originals
def test_plans_with_real_inspection_have_no_blocking_items():
    t13 = load_template("swisslife_sl13065.json")
    plan = resolve(t13, sl13065_values(), ins=inspect_pdf_form(SL13065_PDF), mode="final")
    assert not plan.blocking, plan.checklist
    assert plan.value_for("Abfindung").state == "Ja"
    t18 = load_template("swisslife_sl18477.json")
    plan = resolve(t18, sl18477_private(), ins=inspect_pdf_form(SL18477_PDF), mode="final")
    assert not plan.blocking, plan.checklist
    assert plan.value_for("reduzierter Beitrag").raw_value == "1234.56"


def test_checklist_never_contains_personal_values():
    t = load_template("swisslife_sl18477.json")
    values = [
        fv(v.key, v.value, v.normalized_value, status=FieldStatus.EXTRACTED)
        if v.key.startswith("insured.")
        else v
        for v in sl18477_private()
    ]
    values = [v for v in values if v.key != "order.payment_account_holder"]
    values.append(manual("order.pep_related_person", "Łódź Mustermann"))
    ins = inspection(text_info("IBAN", width=40))
    plan = resolve(t, values, ins=ins, mode="final")
    assert plan.blocking
    for item in plan.checklist:
        for value in PERSONAL_VALUES:
            assert value not in item.message, (value, item.message)


# ---------------------------------------------------------------------------
# Regressionen: „nicht relevant“ bei rechtlichen Auswahlen, Registerkennzeichen, ODER-Bedingungen
# ---------------------------------------------------------------------------


def _na(key: str):
    return fv(key, None, status=FieldStatus.NOT_APPLICABLE)


def test_pdf_1_not_applicable_legal_choices_block_sl18477():
    t = load_template("swisslife_sl18477.json")
    decided = {"order.continuation_mode", "order.pep_status", "order.payment_method"}
    values = [v for v in sl18477_private() if v.key not in decided]
    plan = resolve(t, [*values, *(_na(k) for k in decided)])
    assert plan.blocking
    legal = {i.field_key for i in plan.checklist if i.code == "LEGAL_DECISION_MISSING" and i.blocking}
    assert decided <= legal
    item = next(i for i in plan.checklist if i.field_key == "order.pep_status")
    assert "„nicht relevant“ ist hier keine Entscheidung" in item.message
    assert plan.value_for("politisch exponierte Person") is None
    assert plan.value_for("Beitragszahlung") is None


def test_legal_1_not_applicable_checkboxes_block_sl13065():
    t = load_template("swisslife_sl13065.json")
    keys = {"order.severance_consent", "order.waive_refund"}
    values = [v for v in sl13065_values() if v.key not in keys]
    plan = resolve(t, [*values, *(_na(k) for k in keys)])
    blocking = {(i.code, i.field_key) for i in plan.checklist if i.blocking}
    assert blocking == {("LEGAL_DECISION_MISSING", k) for k in keys}
    assert plan.value_for("Abfindung") is None and plan.value_for("Rückerstattung") is None


def test_legal_1_not_applicable_stays_valid_for_legal_text_and_amount_fields():
    m = FieldMapping(
        pdf_field="Amt",
        label="Amt",
        kind=MappingKind.TEXT,
        source="order.pep_office",
        required=True,
        legal_relevant=True,
    )
    assert resolve(template(m), [_na("order.pep_office")]).checklist == []
    assert resolve(template(AMOUNT), [_na("order.reduced_premium_amount")]).checklist == []


def test_legal_1_not_applicable_in_compose_and_acknowledgment():
    m = FieldMapping(
        pdf_field="Text",
        label="Text",
        kind=MappingKind.TEXT,
        compose="{contract.number} {order.severance_consent?}",
    )
    plan = resolve(template(m), [fv("contract.number", "MUSTER-4711"), _na("order.severance_consent")])
    assert codes(plan, blocking=True) == ["LEGAL_DECISION_MISSING"] and plan.values == []
    ack = Acknowledgment(
        field="order.advice_waiver_acknowledged",
        text="Beratungsverzicht besprochen",
        conditions=[Condition(field="order.continuation_mode", equals="private")],
    )
    plan = resolve(template(acks=[ack]), [_na("order.continuation_mode")])
    assert codes(plan, blocking=True) == ["CONDITION_UNDECIDED"]


def test_legal_2_registry_legal_source_without_mapping_flag_needs_decision():
    # Importierte Zuordnung ohne legal_relevant/required: Register entscheidet trotzdem.
    m = FieldMapping(
        pdf_field="Rückerstattung",
        label="Rückerstattung",
        kind=MappingKind.CHECKBOX,
        source="order.waive_refund",
    )
    plan = resolve(template(m), [])
    assert codes(plan, blocking=True) == ["LEGAL_DECISION_MISSING"]
    assert codes(resolve(template(m), [_na("order.waive_refund")]), blocking=True) == [
        "LEGAL_DECISION_MISSING"
    ]
    ok = resolve(template(m), [manual("order.waive_refund", "false")])
    assert not ok.blocking and ok.value_for("Rückerstattung").state == "Off"


def test_legal_2_registry_legal_manual_only_and_compose_block():
    m = FieldMapping(
        pdf_field="IBAN",
        label="IBAN",
        kind=MappingKind.TEXT,
        source="order.payment_iban",
        fill_policy=FillPolicy.MANUAL_ONLY,
    )
    plan = resolve(template(m), [fv("order.payment_iban", "DE89370400440532013000")])
    assert codes(plan, blocking=True) == ["MANUAL_ENTRY_REQUIRED"]
    composed = FieldMapping(
        pdf_field="Bezug", label="Bezug", kind=MappingKind.TEXT, compose="{order.pep_office}"
    )
    plan = resolve(template(composed), [])
    assert codes(plan, blocking=True) == ["REQUIRED_MISSING"]


def _interim(paying: bool = True) -> list:
    values = [v for v in sl18477_employer() if v.key != "order.interim_private_continuation"]
    values.append(manual("order.interim_private_continuation", "true"))
    values.append(manual("order.interim_contribution_option", "paying" if paying else "exempt"))
    if paying:
        values.append(manual("order.interim_premium_amount", "50.00"))
    return values


C_PERSON_FIELDS = [
    "Name Vorname",
    "Datum 3.1",
    "Datum 3.21",
    "Datum 3.32",
    "Straße Hausnummer 1",
    "Postleitzahl 1",
    "Ort 1",
    "Geburtsort",
    "Stattsangehörigkeit",
]


def test_legal_4_interim_private_continuation_fills_section_c_person_data():
    t = load_template("swisslife_sl18477.json")
    plan = resolve(t, _interim())
    assert not plan.blocking, plan.blocking_items
    assert all(plan.value_for(f) is not None for f in C_PERSON_FIELDS)
    assert plan.value_for("Name Vorname").text == "Musterfrau, Erika"
    assert plan.value_for("Versicherung durch die versicherte Person").state == "Off"
    assert plan.value_for("Fortführung der Versicherung") is None  # C-Beitragsvariante nur bei privat
    assert any("Zwischenzeitraum" in n and "Swiss Life" in n for n in t.notes)


def test_legal_4_interim_person_data_is_required():
    t = load_template("swisslife_sl18477.json")
    values = [v for v in _interim() if v.key != "insured.birth_place"]
    plan = resolve(t, values)
    item = next(i for i in plan.blocking_items if i.field_key == "insured.birth_place")
    assert item.code == "REQUIRED_MISSING" and item.pdf_field == "Geburtsort"


def test_legal_4_no_interim_and_undecided_alternatives():
    t = load_template("swisslife_sl18477.json")
    plan = resolve(t, sl18477_employer())  # interim = false
    assert all(plan.value_for(f) is None for f in C_PERSON_FIELDS) and not plan.blocking
    # Fortführung durch AG, Zwischenzeitraum noch offen -> blockiert (eine Meldung je Feld)
    values = [v for v in sl18477_employer() if v.key != "order.interim_private_continuation"]
    plan = resolve(t, values)
    items = [i for i in plan.blocking_items if i.field_key == "order.interim_private_continuation"]
    assert len(items) == 1 and items[0].code == "LEGAL_DECISION_MISSING"
    assert all(plan.value_for(f) is None for f in C_PERSON_FIELDS)


def test_legal_4_any_conditions_semantics():
    m = FieldMapping(
        pdf_field="Name",
        label="Name",
        kind=MappingKind.TEXT,
        source="insured.last_name",
        required=True,
        conditions=[Condition(field="order.payment_method", equals="sepa")],
        any_conditions=[
            Condition(field="order.continuation_mode", equals="private"),
            Condition(field="order.interim_private_continuation", equals="true"),
        ],
    )
    name = fv("insured.last_name", "Musterfrau")
    sepa = manual("order.payment_method", "sepa")
    # UND-Bedingung nicht erfüllt -> unabhängig von ODER irrelevant
    plan = resolve(template(m), [name, manual("order.payment_method", "transfer")])
    assert plan.values == [] and plan.checklist == []
    # eine Alternative erfüllt, die andere offen -> erfüllt
    plan = resolve(template(m), [name, sepa, manual("order.interim_private_continuation", "true")])
    assert plan.value_for("Name").text == "Musterfrau" and not plan.blocking
    # keine Alternative erfüllt -> nicht relevant
    plan = resolve(
        template(m),
        [
            name,
            sepa,
            manual("order.continuation_mode", "employer"),
            manual("order.interim_private_continuation", "false"),
        ],
    )
    assert plan.values == [] and plan.checklist == []
    # alle offen -> nur die erste offene Alternative wird gemeldet
    plan = resolve(template(m), [name, sepa])
    assert [(i.code, i.field_key) for i in plan.checklist] == [
        ("CONDITION_UNDECIDED", "order.continuation_mode")
    ]
    assert "order.interim_private_continuation" in template(m).source_keys()
