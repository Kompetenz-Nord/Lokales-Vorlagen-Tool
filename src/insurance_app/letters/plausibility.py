"""Plausibilitätsregeln für Briefvorlagen.

Regeln erzeugen nur Warnungen, wenn sie keine gesicherte Aussage erlauben;
``ERROR`` nur bei eindeutigen Widersprüchen (z. B. Ende vor Beginn).
Meldungen enthalten keine personenbezogenen Werte.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from ..core.models import FieldStatus, FieldValue, Severity, ValidationMessage
from ..core.textutil import fold

Fields = dict[str, FieldValue]
Rule = Callable[[Fields, Fields, date], list[ValidationMessage]]


def _released(store: Fields, key: str) -> FieldValue | None:
    fv = store.get(key)
    return fv if fv is not None and fv.status in (FieldStatus.CONFIRMED, FieldStatus.MANUAL) else None


def _date(store: Fields, key: str) -> date | None:
    fv = _released(store, key)
    return fv.as_date() if fv else None


def _amount(store: Fields, key: str) -> Decimal | None:
    fv = _released(store, key)
    return fv.as_decimal() if fv else None


def _msg(sev: Severity, code: str, text: str, key: str | None = None) -> ValidationMessage:
    return ValidationMessage(severity=sev, code=code, message=text, field_key=key)


def bav_termination_warning(contract: Fields, order: Fields, today: date) -> list[ValidationMessage]:
    texts = []
    for key in ("contract.contract_type", "contract.product"):
        fv = contract.get(key)
        if fv is not None and fv.value and fv.status != FieldStatus.NOT_APPLICABLE:
            texts.append(fold(fv.value))
    joined = " ".join(texts)
    if "direktversicherung" in joined or "betriebliche altersversorgung" in joined or "bav" in joined.split():
        return [
            _msg(
                Severity.WARNING,
                "BAV_TERMINATION",
                "Direktversicherung (bAV): Versicherungsnehmer ist in der Regel der Arbeitgeber. Eine Kündigung durch "
                "die versicherte Person ist meist nicht möglich, und es gelten Verfügungsbeschränkungen "
                "(§ 2 Abs. 2 BetrAVG). Bitte vor dem Versand prüfen.",
                "contract.contract_type",
            )
        ]
    return []


def letter_date_plausible(contract: Fields, order: Fields, today: date) -> list[ValidationMessage]:
    d = _date(order, "order.letter_date")
    if d is None:
        return []
    if d > today + timedelta(days=14):
        return [
            _msg(
                Severity.WARNING,
                "LETTER_DATE_FUTURE",
                "Das Briefdatum liegt weit in der Zukunft.",
                "order.letter_date",
            )
        ]
    if d < today - timedelta(days=30):
        return [
            _msg(
                Severity.WARNING,
                "LETTER_DATE_PAST",
                "Das Briefdatum liegt mehr als 30 Tage zurück.",
                "order.letter_date",
            )
        ]
    return []


def desired_date_future(contract: Fields, order: Fields, today: date) -> list[ValidationMessage]:
    d = _date(order, "order.desired_date")
    if d is not None and d < today:
        return [
            _msg(
                Severity.WARNING,
                "DESIRED_DATE_PAST",
                "Der gewünschte Beendigungszeitpunkt liegt in der Vergangenheit – bitte prüfen.",
                "order.desired_date",
            )
        ]
    return []


def notice_date_recent(contract: Fields, order: Fields, today: date) -> list[ValidationMessage]:
    d = _date(order, "order.insurer_notice_date")
    if d is None:
        return []
    if d > today:
        return [
            _msg(
                Severity.ERROR,
                "NOTICE_DATE_FUTURE",
                "Das Datum der Mitteilung liegt in der Zukunft.",
                "order.insurer_notice_date",
            )
        ]
    if d < today - timedelta(days=31):
        return [
            _msg(
                Severity.WARNING,
                "NOTICE_DATE_OLD",
                "Die Mitteilung bzw. der Schadenfall liegt länger als einen Monat zurück. Kurze Kündigungsfristen "
                "könnten abgelaufen sein – bitte prüfen.",
                "order.insurer_notice_date",
            )
        ]
    return []


def change_period_order(contract: Fields, order: Fields, today: date) -> list[ValidationMessage]:
    start = _date(order, "order.change_start_date")
    end = _date(order, "order.change_end_date")
    if start and end and end <= start:
        return [
            _msg(
                Severity.ERROR,
                "CHANGE_PERIOD_ORDER",
                "Das Ende der Befristung liegt vor oder auf dem Beginn – bitte Zeitraum korrigieren.",
                "order.change_end_date",
            )
        ]
    return []


def change_start_future(contract: Fields, order: Fields, today: date) -> list[ValidationMessage]:
    start = _date(order, "order.change_start_date")
    if start and start < today:
        return [
            _msg(
                Severity.WARNING,
                "CHANGE_START_PAST",
                "Der Beginn der Änderung liegt in der Vergangenheit. Rückwirkende Änderungen sind oft nicht möglich.",
                "order.change_start_date",
            )
        ]
    return []


def reduction_below_current(contract: Fields, order: Fields, today: date) -> list[ValidationMessage]:
    kind = _released(order, "order.premium_change_type")
    if kind is None or kind.normalized_value != "reduction":
        return []
    new = _amount(order, "order.new_premium_amount")
    current = _amount(contract, "premium.amount")
    if new is not None and current is not None and new >= current:
        return [
            _msg(
                Severity.WARNING,
                "REDUCTION_NOT_LOWER",
                "Der neue Beitrag ist nicht niedriger als der bestätigte bisherige Beitrag (Zahlweise prüfen).",
                "order.new_premium_amount",
            )
        ]
    return []


def increase_total_matches(contract: Fields, order: Fields, today: date) -> list[ValidationMessage]:
    inc = _amount(order, "order.increase_amount")
    total = _amount(order, "order.new_premium_amount")
    current = _amount(contract, "premium.amount")
    out: list[ValidationMessage] = []
    if inc is not None and inc <= 0:
        out.append(
            _msg(
                Severity.ERROR,
                "INCREASE_NOT_POSITIVE",
                "Die Erhöhung muss größer als 0 sein.",
                "order.increase_amount",
            )
        )
    if inc is not None and total is not None and current is not None and current + inc != total:
        out.append(
            _msg(
                Severity.WARNING,
                "INCREASE_TOTAL_MISMATCH",
                "Bisheriger Beitrag plus Erhöhung ergibt nicht den angegebenen Gesamtbeitrag (Zahlweise prüfen).",
                "order.new_premium_amount",
            )
        )
    return out


RULES: dict[str, Rule] = {
    "bav_termination_warning": bav_termination_warning,
    "letter_date_plausible": letter_date_plausible,
    "desired_date_future": desired_date_future,
    "notice_date_recent": notice_date_recent,
    "change_period_order": change_period_order,
    "change_start_future": change_start_future,
    "reduction_below_current": reduction_below_current,
    "increase_total_matches": increase_total_matches,
}


def check_letter_plausibility(
    rule_codes: list[str], contract: Fields, order: Fields, today: date | None = None
) -> list[ValidationMessage]:
    today = today or date.today()
    out: list[ValidationMessage] = []
    for code in rule_codes:
        rule = RULES.get(code)
        if rule is None:
            raise ValueError(f"Unbekannte Plausibilitätsregel: {code}")
        out.extend(rule(contract, order, today))
    return out
