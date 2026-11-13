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


# EXT-12: zweistellige Jahre – optionale, feldabhängige Jahrhundertwahl (Standard unverändert)
def test_ext_12_default_pivot_unchanged():
    assert parse_german_date("03.04.56") == date(2056, 4, 3)
    assert parse_german_date("01.08.72") == date(1972, 8, 1)


def test_ext_12_past_only_for_birth_dates():
    today = date(2026, 11, 13)
    assert parse_german_date("03.04.56", past_only=True, today=today) == date(1956, 4, 3)
    assert parse_german_date("13.11.26", past_only=True, today=today) == date(2026, 11, 13)
    assert parse_german_date("14.11.26", past_only=True, today=today) == date(1926, 11, 14)
    assert parse_german_date("03.04.1956", past_only=True, today=today) == date(1956, 4, 3)


def test_ext_12_future_window_for_other_dates():
    today = date(2026, 11, 13)
    assert parse_german_date("01.08.72", max_future_years=50, today=today) == date(2072, 8, 1)
    assert parse_german_date("01.01.76", max_future_years=50, today=today) == date(2076, 1, 1)
    assert parse_german_date("01.01.77", max_future_years=50, today=today) == date(1977, 1, 1)
    found = find_dates("vom 01.01.80 bis 01.01.45", max_future_years=50, today=today)
    assert [d for d, _ in found] == [date(1980, 1, 1), date(2045, 1, 1)]
