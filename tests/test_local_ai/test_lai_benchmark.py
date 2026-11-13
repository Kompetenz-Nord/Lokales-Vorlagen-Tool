"""Bewertung von Extraktionsergebnissen gegen die Musterlösung."""

from __future__ import annotations

import json

import pytest

from insurance_app.core.paths import resource_root
from insurance_app.local_ai.benchmark import FieldScore, compare_methods, score_extraction, values_equal

TRUTH = {
    "contract.number": "8815201-7",
    "contract.start_date": "2019-01-01",
    "insured.last_name": "Musterfrau",
    "premium.amount": "150.00",
    "bank.iban": "DE89370400440532013000",
}


def test_score_extraction_counts():
    extracted = {
        "contract.number": "88152017",  # gleiche Nummer ohne Trennzeichen -> korrekt
        "contract.start_date": "2019-01-02",  # falsch
        "insured.last_name": "MUSTERFRAU",  # korrekt (Groß-/Kleinschreibung egal)
        "premium.amount": None,  # fehlt
        "bank.iban": "DE89 3704 0044 0532 0130 00",
        "insured.city": "Musterstadt",  # ohne Musterlösung
    }
    score = score_extraction(TRUTH, extracted)
    assert isinstance(score, FieldScore)
    assert (score.correct, score.wrong, score.missing, score.total) == (3, 1, 1, 5)
    assert score.unexpected == 1
    assert score.details == {
        "contract.number": "correct",
        "contract.start_date": "wrong",
        "insured.last_name": "correct",
        "premium.amount": "missing",
        "bank.iban": "correct",
    }
    assert score.accuracy == pytest.approx(0.6)
    assert score.error_rate == pytest.approx(0.2)
    assert score.missing_rate == pytest.approx(0.2)


def test_empty_ground_truth():
    score = score_extraction({}, {"x": "y"})
    assert score.total == 0 and score.accuracy == 0.0 and score.error_rate == 0.0


def test_blank_values_count_as_missing():
    score = score_extraction({"contract.number": "1"}, {"contract.number": "  "})
    assert score.missing == 1


def test_values_equal_is_strict_for_amounts_and_dates():
    assert not values_equal("premium.amount", "150.00", "150")
    assert not values_equal("contract.start_date", "2019-01-01", "01.01.2019")
    assert values_equal("premium.amount", "150.00", " 150.00 ")


def test_compare_methods():
    results = {
        "ocr_only": {"contract.number": "8815201-7", "contract.start_date": None},
        "ocr_vlm": {k: v for k, v in TRUTH.items()},
    }
    report = compare_methods(TRUTH, results)
    assert set(report) == {"ocr_only", "ocr_vlm"}
    assert report["ocr_vlm"]["accuracy"] == 1.0
    assert report["ocr_vlm"]["error_rate"] == 0.0
    assert report["ocr_only"]["correct"] == 1
    assert report["ocr_only"]["missing"] == 4
    assert report["ocr_only"]["missing_rate"] == pytest.approx(0.8)
    assert report["ocr_only"]["details"]["contract.start_date"] == "missing"
    json.dumps(report)


def test_custom_comparator():
    report = compare_methods(
        {"a.b": "x"}, {"m": {"a.b": "X"}}, comparator=lambda _k, e, a: e.lower() == a.lower()
    )
    assert report["m"]["correct"] == 1


def test_truth_fixture_scores_perfectly_against_itself():
    path = (
        resource_root()
        / "tests"
        / "fixtures"
        / "synthetic"
        / "MUSTER_Police_Direktversicherung.pdf.truth.json"
    )
    if not path.exists():
        pytest.skip("Synthetische Musterlösung nicht vorhanden")
    truth = json.loads(path.read_text(encoding="utf-8"))
    score = score_extraction(truth, dict(truth))
    assert score.correct == score.total == len(truth)
