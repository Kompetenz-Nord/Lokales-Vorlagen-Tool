from datetime import date

import pytest

from insurance_app.core.textutil import find_dates, fold, parse_german_date, parse_month, truncate


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01.02.2022", date(2022, 2, 1)),
        ("1.2.22", date(2022, 2, 1)),
        ("01/02/2022", date(2022, 2, 1)),
        ("2022-02-01", date(2022, 2, 1)),
        ("13. März 2025", date(2025, 3, 13)),
        ("13. Maerz 2025", date(2025, 3, 13)),
        ("13. Mrz. 2025", date(2025, 3, 13)),
        ("gültig ab 1. Juli 2024", date(2024, 7, 1)),
        ("31.02.2024", None),
        ("keine Angabe", None),
        ("Tel. 089 38109-1000", None),
    ],
)
def test_parse_german_date(raw, expected):
    assert parse_german_date(raw) == expected


def test_find_dates_range():
    found = find_dates("Laufzeit 01.02.2022 bis 01.08.2065")
    assert [d for d, _ in found] == [date(2022, 2, 1), date(2065, 8, 1)]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("03.2025", (2025, 3)), ("3/2025", (2025, 3)), ("März 2025", (2025, 3)), ("13.2025", None)],
)
def test_parse_month(raw, expected):
    assert parse_month(raw) == expected


def test_fold_and_truncate():
    assert fold("  Straße  Übernahme ") == "strasse uebernahme"
    assert truncate("x" * 200, 10).endswith("…") and len(truncate("x" * 200, 10)) == 10
