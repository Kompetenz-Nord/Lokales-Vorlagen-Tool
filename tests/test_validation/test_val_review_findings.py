"""Regressionstests zu geprüften Befunden (Validierung). Synthetische Daten."""

from datetime import date

from insurance_app.core.fields import FIELD_REGISTRY
from insurance_app.core.models import FieldStatus, FieldValue, Severity
from insurance_app.validation.validators import PREMIUM_END_CODE, validate_case, validate_field

TODAY = date(2026, 11, 13)


def _fv(key: str, norm: str, status: FieldStatus = FieldStatus.EXTRACTED, value: str | None = None):
    return FieldValue(key=key, normalized_value=norm, value=value or norm, status=status)


def test_ext_16_premium_end_after_end_uses_date_order_code():
    contract = {
        "contract.premium_end_date": _fv("contract.premium_end_date", "2050-12-01"),
        "contract.end_date": _fv("contract.end_date", "2040-01-01"),
    }
    msgs = [m for m in validate_case(contract, {}, TODAY) if m.code == PREMIUM_END_CODE]
    assert len(msgs) == 1
    # Der Vorgang zeigt feldübergreifende Meldungen der Familie DATE_ORDER_* an (AppService.case_messages)
    assert "DATE_ORDER" in msgs[0].code
    assert msgs[0].severity == Severity.WARNING
    assert msgs[0].field_key == "contract.premium_end_date"
    # Feldprüfung allein erzeugt die Meldung nicht (sie ist feldübergreifend)
    fdef = FIELD_REGISTRY["contract.premium_end_date"]
    assert not validate_field(fdef, contract["contract.premium_end_date"], TODAY)


def test_ext_16_no_message_when_order_is_fine():
    contract = {
        "contract.premium_end_date": _fv("contract.premium_end_date", "2039-12-01"),
        "contract.end_date": _fv("contract.end_date", "2040-01-01"),
    }
    assert not [m for m in validate_case(contract, {}, TODAY) if m.code == PREMIUM_END_CODE]


def test_legal_3_stored_month_with_other_day_is_warned():
    # Altbestand: früher wurde „15.03.2027“ still als 2027-03 gespeichert
    key = "order.takeover_effective_month"
    fv = _fv(key, "2027-03", FieldStatus.MANUAL, value="15.03.2027")
    msgs = validate_field(FIELD_REGISTRY[key], fv, TODAY)
    assert [m.code for m in msgs] == ["MONTH_DAY_NOT_FIRST"]
    assert "01." in msgs[0].message
    ok = _fv(key, "2027-03", FieldStatus.MANUAL, value="03.2027")
    assert validate_field(FIELD_REGISTRY[key], ok, TODAY) == []
    first = _fv(key, "2027-03", FieldStatus.MANUAL, value="01.03.2027")
    assert validate_field(FIELD_REGISTRY[key], first, TODAY) == []
