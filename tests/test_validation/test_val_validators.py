from datetime import date

import pytest

from insurance_app.core.fields import FIELD_REGISTRY
from insurance_app.core.models import FieldStatus, FieldValue, Severity
from insurance_app.validation.validators import (
    iban_is_valid,
    postal_code_de_is_valid,
    validate_case,
    validate_field,
)

TODAY = date(2026, 11, 13)


def fv(key: str, norm: str | None, status: FieldStatus = FieldStatus.EXTRACTED, value: str | None = None):
    return FieldValue(
        key=key, normalized_value=norm, value=value if value is not None else norm, status=status
    )


def check(key: str, norm: str | None, **kw):
    return validate_field(FIELD_REGISTRY[key], fv(key, norm, **kw), TODAY)


def codes(msgs):
    return [m.code for m in msgs]


@pytest.mark.parametrize(
    "iban",
    [
        "DE89370400440532013000",
        "DE89 3704 0044 0532 0130 00",
        "de89370400440532013000",
        "GB82WEST12345698765432",
        "AT611904300234573201",
        "CH9300762011623852957",
        "NL91ABNA0417164300",
        "FR1420041010050500013M02606",
        "IT60X0542811101000000123456",
        "ES9121000418450200051332",
        "BE68539007547034",
        "LU280019400644750000",
        "PL61109010140000071219812874",
        "DE02120300000000202051",
    ],
)
def test_valid_ibans(iban):
    assert iban_is_valid(iban)


@pytest.mark.parametrize(
    "iban",
    [
        "DE89370400440532013001",  # Prüfziffer
        "DE8937040044053201300",  # Länge
        "AT61190430023457320",  # Länge AT
        "XX89370400440532013000",
        "",
        None,
        "DE89-3704",
    ],
)
def test_invalid_ibans(iban):
    assert not iban_is_valid(iban)


def test_postal_code_de():
    assert postal_code_de_is_valid("12345")
    assert postal_code_de_is_valid("01067")
    for bad in ("00999", "1234", "123456", "1234a", None):
        assert not postal_code_de_is_valid(bad)


def test_dates():
    assert check("contract.start_date", "2019-01-01") == []
    assert codes(check("contract.start_date", "2024-02-30")) == ["DATE_INVALID"]
    assert codes(check("contract.start_date", "1850-01-01")) == ["DATE_RANGE"]
    assert codes(check("contract.start_date", "1930-01-01")) == ["DATE_UNUSUAL"]


def test_birth_date_rules():
    assert check("insured.birth_date", "1988-08-12") == []
    future = check("insured.birth_date", "2030-01-01")
    assert codes(future) == ["BIRTH_DATE_FUTURE"] and future[0].severity == Severity.WARNING
    assert codes(check("insured.birth_date", "2016-01-01")) == ["BIRTH_DATE_AGE"]
    assert codes(check("insured.birth_date", "1920-01-01")) == ["BIRTH_DATE_AGE"]


def test_amounts():
    assert check("premium.amount", "150.00") == []
    neg = check("premium.amount", "-1.00")
    assert codes(neg) == ["AMOUNT_NEGATIVE"] and neg[0].severity == Severity.ERROR
    large = check("premium.amount", "250000.00")
    assert codes(large) == ["AMOUNT_LARGE"] and large[0].severity == Severity.WARNING
    assert codes(check("premium.amount", "abc")) == ["AMOUNT_INVALID"]


def test_iban_field_error_without_echo():
    msgs = check("bank.iban", "DE89370400440532013001")
    assert codes(msgs) == ["IBAN_CHECKSUM"]
    assert msgs[0].severity == Severity.ERROR
    assert "DE89" not in msgs[0].message
    assert check("bank.iban", "DE89370400440532013000") == []


def test_postal_code_field():
    assert check("insured.postal_code", "12345") == []
    assert codes(check("insured.postal_code", "1234")) == ["POSTAL_CODE_DE"]


def test_contract_number_rules():
    assert check("contract.number", "8815201-7") == []
    ocr = check("contract.number", "88152O1-7")
    assert codes(ocr) == ["CONTRACT_NUMBER_OCR_CONFUSION"]
    assert ocr[0].message == "Mögliche Verwechslung von O/0 oder I/1 durch die Texterkennung – bitte prüfen."
    assert "CONTRACT_NUMBER_OCR_CONFUSION" in codes(check("contract.number", "12I45678"))
    assert "CONTRACT_NUMBER_LENGTH" in codes(check("contract.number", "12"))
    assert "CONTRACT_NUMBER_LENGTH" in codes(check("contract.number", "1" * 31))
    assert "CONTRACT_NUMBER_CHARS" in codes(check("contract.number", "ABC#1234"))
    assert "CONTRACT_NUMBER_NO_DIGITS" in codes(check("contract.number", "ABCDEF"))


def test_name_bool_choice_month_integer_currency():
    assert codes(check("insured.last_name", "Muster3")) == ["NAME_DIGITS"]
    assert check("insured.last_name", "Musterfrau") == []
    assert codes(check("order.severance_consent", "vielleicht", status=FieldStatus.MANUAL)) == [
        "BOOL_INVALID"
    ]
    assert check("order.severance_consent", "true", status=FieldStatus.MANUAL) == []
    assert codes(check("premium.frequency", "weekly")) == ["CHOICE_INVALID"]
    assert check("premium.frequency", "monthly") == []
    assert codes(check("order.takeover_effective_month", "2025-13", status=FieldStatus.MANUAL)) == [
        "MONTH_INVALID"
    ]
    assert check("order.takeover_effective_month", "2025-03", status=FieldStatus.MANUAL) == []
    assert codes(check("contract.end_age", "6x7")) == ["INTEGER_INVALID"]
    assert codes(check("contract.end_age", "150")) == ["END_AGE_UNUSUAL"]
    assert codes(check("premium.currency", "Euro!")) == ["CURRENCY_INVALID"]


def test_missing_and_not_applicable_are_not_checked():
    assert check("bank.iban", None, status=FieldStatus.MISSING) == []
    assert check("bank.iban", "kaputt", status=FieldStatus.NOT_APPLICABLE) == []


def test_unnormalized_raw_value():
    f = FIELD_REGISTRY["contract.start_date"]
    released = FieldValue(key=f.key, value="demnächst", status=FieldStatus.MANUAL)
    msgs = validate_field(f, released, TODAY)
    assert codes(msgs) == ["FORMAT_INVALID"] and msgs[0].severity == Severity.ERROR
    assert "TT.MM.JJJJ" in msgs[0].message
    extracted = FieldValue(key=f.key, value="demnächst", status=FieldStatus.EXTRACTED)
    assert validate_field(f, extracted, TODAY)[0].severity == Severity.WARNING
    # Rohwert ohne Normalform, aber lesbar -> wird nachträglich normalisiert und geprüft
    raw_ok = FieldValue(key=f.key, value="01.01.2019", status=FieldStatus.MANUAL)
    assert validate_field(f, raw_ok, TODAY) == []


def test_validate_case_cross_field_rules():
    contract = {
        "contract.start_date": fv("contract.start_date", "2053-01-01", FieldStatus.CONFIRMED),
        "contract.end_date": fv("contract.end_date", "2019-01-01", FieldStatus.CONFIRMED),
        "employer.employment_start": fv("employer.employment_start", "2024-06-30"),
        "employer.employment_end": fv("employer.employment_end", "2015-04-01"),
        "insured.birth_date": fv("insured.birth_date", "1988-08-12"),
        "contract.premium_end_date": fv("contract.premium_end_date", "2060-12-01"),
        "contract.pension_start_date": fv("contract.pension_start_date", "2053-01-01"),
    }
    msgs = validate_case(contract, {}, TODAY)
    by_code = {m.code: m for m in msgs}
    assert by_code["DATE_ORDER_CONTRACT"].severity == Severity.ERROR  # beide bestätigt
    assert by_code["DATE_ORDER_EMPLOYMENT"].severity == Severity.WARNING
    assert "PREMIUM_END_AFTER_END" in by_code
    assert "DATE_ORDER_BIRTH_START" not in by_code
    contract["contract.start_date"] = fv("contract.start_date", "1980-01-01")
    contract["contract.end_date"] = fv("contract.end_date", "2053-01-01")
    by_code = {m.code: m for m in validate_case(contract, {}, TODAY)}
    assert by_code["DATE_ORDER_BIRTH_START"].severity == Severity.WARNING
    assert "DATE_ORDER_CONTRACT" not in by_code


def test_validate_case_includes_order_fields_and_skips_conflicts():
    order = {"order.payment_iban": fv("order.payment_iban", "DE89370400440532013001", FieldStatus.MANUAL)}
    contract = {
        "contract.start_date": FieldValue(key="contract.start_date", status=FieldStatus.CONFLICT),
        "contract.end_date": fv("contract.end_date", "2019-01-01"),
    }
    msgs = validate_case(contract, order, TODAY)
    assert codes(msgs) == ["IBAN_CHECKSUM"]
    assert msgs[0].field_key == "order.payment_iban"


def test_foreign_postal_code_not_flagged():
    contract = {
        "insured.postal_code": fv("insured.postal_code", "1010"),
        "insured.country": fv("insured.country", "AT"),
    }
    assert validate_case(contract, {}, TODAY) == []
    contract["insured.country"] = fv("insured.country", "DE")
    assert codes(validate_case(contract, {}, TODAY)) == ["POSTAL_CODE_DE"]


def test_messages_never_echo_personal_values():
    values = {
        "insured.last_name": "Muster3frau",
        "bank.iban": "DE89370400440532013001",
        "insured.birth_date": "2030-01-01",
        "contract.number": "88152O1-7",
        "insured.postal_code": "9876",
    }
    contract = {k: fv(k, v) for k, v in values.items()}
    msgs = validate_case(contract, {}, TODAY)
    assert len(msgs) >= 5
    for m in msgs:
        for v in values.values():
            assert v not in m.message
