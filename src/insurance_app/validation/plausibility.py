"""Plausibilitätsregeln je Vorlage (Codes in ``TemplateDefinition.plausibility_rules``).

Plausibilitätsprüfungen sind Hinweise für den Menschen – sie ändern keine Werte.
Meldungen nennen keine vollständigen Personen- oder Bankdaten.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from insurance_app.core.models import FieldStatus, FieldValue, Severity, ValidationMessage
from insurance_app.core.textutil import fold

__all__ = ["RULES", "PlausibilityRule", "check_plausibility"]

CheckFn = Callable[[dict[str, FieldValue], dict[str, FieldValue], date], list[ValidationMessage]]


@dataclass(frozen=True)
class PlausibilityRule:
    code: str
    description: str
    check: CheckFn


_IGNORED = (FieldStatus.MISSING, FieldStatus.NOT_APPLICABLE)


def _texts(fv: FieldValue | None) -> list[str]:
    """Alle Textwerte eines Feldes (bei Konflikt auch die Kandidaten)."""
    if fv is None or fv.status in _IGNORED:
        return []
    out: list[str] = []
    if fv.effective_value:
        out.append(fv.effective_value)
    if fv.status == FieldStatus.CONFLICT:
        out.extend(c.normalized or c.value for c in fv.candidates if (c.normalized or c.value))
    return out


def _date(fields: dict[str, FieldValue], key: str) -> date | None:
    fv = fields.get(key)
    if fv is None or fv.status in (*_IGNORED, FieldStatus.CONFLICT):
        return None
    return fv.as_date()


def _age_on(birth: date, ref: date) -> int:
    return ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))


_NON_BAV = re.compile(r"basisrente|ruerup|rurup|riester|eigenvertrag|privatrente|private rentenversicherung")
_BAV = re.compile(r"direktversicherung|betriebliche altersversorgung|\bbav\b|§\s*3\s*nr\.?\s*63|§\s*40\s*b")


def _bav_product_expected(
    contract: dict[str, FieldValue], order: dict[str, FieldValue], today: date
) -> list[ValidationMessage]:
    texts = [
        fold(t) for key in ("contract.product", "contract.contract_type") for t in _texts(contract.get(key))
    ]
    if any(_BAV.search(t) for t in texts):
        return []
    if any(_NON_BAV.search(t) for t in texts):
        return [
            ValidationMessage(
                severity=Severity.WARNING,
                code="BAV_PRODUCT_UNEXPECTED",
                message=(
                    "Produkt/Vertragstyp deutet nicht auf eine Direktversicherung (bAV) hin. "
                    "Bitte prüfen, ob diese Vorlage passt."
                ),
                field_key="contract.contract_type",
            )
        ]
    return [
        ValidationMessage(
            severity=Severity.INFO,
            code="BAV_PRODUCT_UNKNOWN",
            message=(
                "Aus Produkt/Vertragstyp ist nicht erkennbar, ob es eine Direktversicherung (bAV) ist. "
                "Bitte prüfen, ob diese Vorlage passt."
            ),
            field_key="contract.contract_type",
        )
    ]


def _employment_period_order(
    contract: dict[str, FieldValue], order: dict[str, FieldValue], today: date
) -> list[ValidationMessage]:
    start = _date(contract, "employer.employment_start")
    end = _date(contract, "employer.employment_end")
    out: list[ValidationMessage] = []
    if start and end and start >= end:
        out.append(
            ValidationMessage(
                severity=Severity.WARNING,
                code="EMPLOYMENT_PERIOD_ORDER",
                message="Der Beginn der Betriebszugehörigkeit liegt nicht vor ihrem Ende. Bitte prüfen.",
                field_key="employer.employment_end",
            )
        )
    contract_start = _date(contract, "contract.start_date")
    if end and contract_start and end < contract_start:
        out.append(
            ValidationMessage(
                severity=Severity.WARNING,
                code="EMPLOYMENT_END_BEFORE_CONTRACT",
                message="Das Ende der Betriebszugehörigkeit liegt vor dem Versicherungsbeginn. Bitte prüfen.",
                field_key="employer.employment_end",
            )
        )
    birth = _date(contract, "insured.birth_date")
    if start and birth and _age_on(birth, start) < 14:
        out.append(
            ValidationMessage(
                severity=Severity.WARNING,
                code="EMPLOYMENT_START_TOO_YOUNG",
                message="Zum Beginn der Betriebszugehörigkeit wäre die versicherte Person jünger als 14 Jahre.",
                field_key="employer.employment_start",
            )
        )
    return out


def _birth_date_plausible(
    contract: dict[str, FieldValue], order: dict[str, FieldValue], today: date
) -> list[ValidationMessage]:
    birth = _date(contract, "insured.birth_date")
    if birth is None:
        return []
    out: list[ValidationMessage] = []
    if birth >= today or not 14 <= _age_on(birth, today) <= 100:
        out.append(
            ValidationMessage(
                severity=Severity.WARNING,
                code="BIRTH_DATE_IMPLAUSIBLE",
                message="Das Geburtsdatum ist unplausibel (Alter nicht zwischen 14 und 100 Jahren). Bitte prüfen.",
                field_key="insured.birth_date",
            )
        )
    start = _date(contract, "contract.start_date")
    if start is not None:
        if birth >= start:
            out.append(
                ValidationMessage(
                    severity=Severity.WARNING,
                    code="BIRTH_AFTER_CONTRACT_START",
                    message="Das Geburtsdatum liegt nicht vor dem Versicherungsbeginn. Bitte prüfen.",
                    field_key="insured.birth_date",
                )
            )
        elif not 14 <= _age_on(birth, start) <= 75:
            out.append(
                ValidationMessage(
                    severity=Severity.INFO,
                    code="AGE_AT_START_UNUSUAL",
                    message="Das Eintrittsalter bei Versicherungsbeginn ist ungewöhnlich. Bitte prüfen.",
                    field_key="insured.birth_date",
                )
            )
    return out


def _iban_country_hint(
    contract: dict[str, FieldValue], order: dict[str, FieldValue], today: date
) -> list[ValidationMessage]:
    fv = order.get("order.payment_iban")
    if fv is None or fv.status in _IGNORED:
        return []
    iban = (fv.effective_value or "").replace(" ", "").upper()
    if len(iban) >= 2 and iban[:2].isalpha() and iban[:2] != "DE":
        return [
            ValidationMessage(
                severity=Severity.INFO,
                code="IBAN_FOREIGN",
                message=(
                    "Die IBAN gehört zu einem Konto außerhalb Deutschlands. "
                    "Bitte prüfen, ob die Beitragszahlung so möglich ist."
                ),
                field_key="order.payment_iban",
            )
        ]
    return []


RULES: dict[str, PlausibilityRule] = {
    rule.code: rule
    for rule in (
        PlausibilityRule(
            "bav_product_expected",
            "Produkt/Vertragstyp passt zu einer Direktversicherung (bAV)",
            _bav_product_expected,
        ),
        PlausibilityRule(
            "employment_period_order",
            "Betriebszugehörigkeit: Beginn vor Ende, Ende nicht vor Versicherungsbeginn",
            _employment_period_order,
        ),
        PlausibilityRule(
            "birth_date_plausible",
            "Geburtsdatum plausibel (Alter 14–100, vor Versicherungsbeginn)",
            _birth_date_plausible,
        ),
        PlausibilityRule(
            "iban_country_hint",
            "Hinweis bei ausländischer IBAN für die Beitragszahlung",
            _iban_country_hint,
        ),
    )
}


def check_plausibility(
    rule_codes: list[str],
    contract: dict[str, FieldValue],
    order: dict[str, FieldValue],
    today: date | None = None,
) -> list[ValidationMessage]:
    """Die genannten Plausibilitätsregeln ausführen. Unbekannter Code -> ``ValueError``."""
    unknown = [c for c in rule_codes if c not in RULES]
    if unknown:
        raise ValueError(f"Unbekannte Plausibilitätsregel(n): {', '.join(unknown)}")
    today = today or date.today()
    out: list[ValidationMessage] = []
    for code in dict.fromkeys(rule_codes):
        out.extend(RULES[code].check(contract, order, today))
    return out
