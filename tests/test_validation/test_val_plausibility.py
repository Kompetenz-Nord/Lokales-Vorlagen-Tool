import json
from datetime import date

import pytest

from insurance_app.core.models import Candidate, Evidence, ExtractionMethod, FieldStatus, FieldValue, Severity
from insurance_app.core.paths import resource_root
from insurance_app.validation.plausibility import RULES, check_plausibility

TODAY = date(2026, 11, 13)


def fv(key: str, norm: str, status: FieldStatus = FieldStatus.EXTRACTED) -> FieldValue:
    return FieldValue(key=key, value=norm, normalized_value=norm, status=status)


def run(code: str, contract=None, order=None):
    return check_plausibility([code], contract or {}, order or {}, TODAY)


def test_registry_covers_all_template_rule_codes():
    schemas = resource_root() / "config" / "field_schemas"
    used: set[str] = set()
    for path in schemas.glob("*.json"):
        used |= set(json.loads(path.read_text(encoding="utf-8")).get("plausibility_rules", []))
    assert used, "keine Plausibilitätsregeln in den Feldzuordnungen gefunden"
    assert used <= set(RULES)
    assert set(RULES) == {
        "bav_product_expected",
        "employment_period_order",
        "birth_date_plausible",
        "iban_country_hint",
    }


def test_unknown_code_raises():
    with pytest.raises(ValueError, match="gibt_es_nicht"):
        check_plausibility(["gibt_es_nicht"], {}, {}, TODAY)


@pytest.mark.parametrize(
    ("product", "contract_type"),
    [
        ("Basisrente Invest (MUSTER)", "Eigenvertrag"),
        ("Basisrente Invest", None),
        (None, "Eigenvertrag"),
        ("Rürup-Rente Klassik", None),
        ("Riester-Rente", None),
        ("Privatrente Flex", None),
    ],
)
def test_bav_product_warning(product, contract_type):
    contract = {}
    if product:
        contract["contract.product"] = fv("contract.product", product)
    if contract_type:
        contract["contract.contract_type"] = fv("contract.contract_type", contract_type)
    msgs = run("bav_product_expected", contract)
    assert len(msgs) == 1
    assert msgs[0].severity == Severity.WARNING
    assert msgs[0].code == "BAV_PRODUCT_UNEXPECTED"
    assert msgs[0].message == (
        "Produkt/Vertragstyp deutet nicht auf eine Direktversicherung (bAV) hin. "
        "Bitte prüfen, ob diese Vorlage passt."
    )


def test_bav_product_ok_and_unknown():
    ok = {"contract.product": fv("contract.product", "Direktversicherung gemäß § 3 Nr. 63 EStG")}
    assert run("bav_product_expected", ok) == []
    mixed = {
        "contract.product": fv("contract.product", "Basisrente"),
        "contract.contract_type": fv("contract.contract_type", "Direktversicherung"),
    }
    assert run("bav_product_expected", mixed) == []
    unknown = run("bav_product_expected", {})
    assert [m.severity for m in unknown] == [Severity.INFO]
    other = run("bav_product_expected", {"contract.product": fv("contract.product", "Rentenversicherung XY")})
    assert [m.code for m in other] == ["BAV_PRODUCT_UNKNOWN"]


def test_bav_product_checks_conflict_candidates():
    ev = Evidence(document_id="D", file_name="p.png", method=ExtractionMethod.TESSERACT)
    conflict = FieldValue(
        key="contract.product",
        status=FieldStatus.CONFLICT,
        candidates=[
            Candidate(
                field_key="contract.product",
                value="Basisrente Invest",
                normalized="Basisrente Invest",
                evidence=ev,
            ),
            Candidate(field_key="contract.product", value="Produkt X", normalized="Produkt X", evidence=ev),
        ],
    )
    msgs = run("bav_product_expected", {"contract.product": conflict})
    assert [m.code for m in msgs] == ["BAV_PRODUCT_UNEXPECTED"]


def test_missing_and_not_applicable_values_are_ignored():
    na = FieldValue(key="contract.product", value="Basisrente", status=FieldStatus.NOT_APPLICABLE)
    assert [m.severity for m in run("bav_product_expected", {"contract.product": na})] == [Severity.INFO]


def test_employment_period_order():
    bad = {
        "employer.employment_start": fv("employer.employment_start", "2024-06-30"),
        "employer.employment_end": fv("employer.employment_end", "2015-04-01"),
    }
    assert [m.code for m in run("employment_period_order", bad)] == ["EMPLOYMENT_PERIOD_ORDER"]
    good = {
        "employer.employment_start": fv("employer.employment_start", "2015-04-01"),
        "employer.employment_end": fv("employer.employment_end", "2024-06-30"),
        "contract.start_date": fv("contract.start_date", "2019-01-01"),
        "insured.birth_date": fv("insured.birth_date", "1988-08-12"),
    }
    assert run("employment_period_order", good) == []
    early_end = {**good, "employer.employment_end": fv("employer.employment_end", "2018-12-31")}
    assert [m.code for m in run("employment_period_order", early_end)] == ["EMPLOYMENT_END_BEFORE_CONTRACT"]
    child = {**good, "insured.birth_date": fv("insured.birth_date", "2005-01-01")}
    assert "EMPLOYMENT_START_TOO_YOUNG" in [m.code for m in run("employment_period_order", child)]


def test_birth_date_plausible():
    assert run("birth_date_plausible", {"insured.birth_date": fv("insured.birth_date", "1988-08-12")}) == []
    future = run("birth_date_plausible", {"insured.birth_date": fv("insured.birth_date", "2030-01-01")})
    assert [m.code for m in future] == ["BIRTH_DATE_IMPLAUSIBLE"]
    after_start = run(
        "birth_date_plausible",
        {
            "insured.birth_date": fv("insured.birth_date", "1988-08-12"),
            "contract.start_date": fv("contract.start_date", "1985-01-01"),
        },
    )
    assert [m.code for m in after_start] == ["BIRTH_AFTER_CONTRACT_START"]
    assert run("birth_date_plausible", {}) == []


def test_iban_country_hint():
    at = {"order.payment_iban": fv("order.payment_iban", "AT611904300234573201", FieldStatus.MANUAL)}
    msgs = run("iban_country_hint", order=at)
    assert [(m.code, m.severity) for m in msgs] == [("IBAN_FOREIGN", Severity.INFO)]
    assert "AT61" not in msgs[0].message
    de = {"order.payment_iban": fv("order.payment_iban", "DE89370400440532013000", FieldStatus.MANUAL)}
    assert run("iban_country_hint", order=de) == []
    assert run("iban_country_hint") == []


def test_duplicate_codes_run_once():
    msgs = check_plausibility(["bav_product_expected", "bav_product_expected"], {}, {}, TODAY)
    assert len(msgs) == 1
