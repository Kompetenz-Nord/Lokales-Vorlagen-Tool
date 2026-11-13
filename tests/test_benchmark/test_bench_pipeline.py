"""Benchmark-Pipeline: Kennzahlen je Datei, „sicher falsch“, Zusammenfassung je Kategorie."""

from __future__ import annotations

import shutil
from datetime import date

import pytest
from bench_helpers import FIXTURE_SET, bench

from insurance_app.core.models import (
    Candidate,
    DocumentType,
    Evidence,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
)
from insurance_app.core.settings import Settings
from insurance_app.ocr.service import TextRecognitionService
from insurance_app.samples.variants import CONFUSABLE_IBAN, BenchmarkItem, load_benchmark_set

DIGITAL_SUBSET = ("digital_nachtrag", "digital_verwechslung", "digital_tabelle")


@pytest.fixture(scope="module")
def items() -> dict[str, BenchmarkItem]:
    return {i.id: i for i in load_benchmark_set(FIXTURE_SET)}


def _cand(key: str, value: str, normalized: str | None) -> Candidate:
    return Candidate(
        field_key=key,
        value=value,
        normalized=normalized,
        confidence=0.6,
        evidence=Evidence(
            document_id="D1", file_name="muster.pdf", page=1, method=ExtractionMethod.TESSERACT
        ),
        document_type=DocumentType.POLICY,
        document_date=date(2011, 10, 1),
    )


def _fv(key: str, status: FieldStatus, value: str | None = None, normalized: str | None = None, cands=()):
    return FieldValue(
        key=key,
        status=status,
        value=value,
        normalized_value=normalized,
        confidence=0.8 if value else None,
        candidates=list(cands),
    )


# ---------------------------------------------------------------------------
# Bewertung an einem konstruierten Beispiel
# ---------------------------------------------------------------------------


def test_high_confidence_wrong_counted_only_for_extracted_status():
    b = bench()
    truth = {
        "contract.number": "1011010-1",
        "premium.amount": "101.10",
        "insured.last_name": "Illner",
        "bank.iban": CONFUSABLE_IBAN,
        "contract.start_date": "2011-10-01",
        "insured.first_name": "Olli",
    }
    fields = {
        # O/I statt 0/1, aber als „Erkannt“ angezeigt -> gefährlichster Fehler
        "contract.number": _fv("contract.number", FieldStatus.EXTRACTED, "IOIIOIO-I", "IOIIOIO-I"),
        "premium.amount": _fv("premium.amount", FieldStatus.EXTRACTED, "101,10 EUR", "101.10"),
        # falsch, aber als unklar markiert -> kein „sicher falsch“
        "insured.last_name": _fv("insured.last_name", FieldStatus.UNCLEAR, "liner", "liner"),
        # Konflikt: kein einzelner Wert, der richtige steht aber zur Auswahl
        "bank.iban": _fv(
            "bank.iban",
            FieldStatus.CONFLICT,
            cands=[
                _cand("bank.iban", "DE02 1001 0010 0006 8201 01", CONFUSABLE_IBAN),
                _cand("bank.iban", "DE02 1001 0010 0006 8201 07", "DE02100100100006820107"),
            ],
        ),
        "contract.start_date": _fv("contract.start_date", FieldStatus.MISSING),
        "insured.first_name": _fv("insured.first_name", FieldStatus.CONFIRMED, "Olli", "Olli"),
        # Wert für ein Feld ohne Sollwert -> nur Hinweis
        "policyholder.city": _fv("policyholder.city", FieldStatus.EXTRACTED, "Musterstadt", "Musterstadt"),
    }
    m = b.score_fields(truth, fields, include_values=True)
    assert (m.total, m.correct, m.wrong, m.missing) == (6, 2, 2, 2)
    assert m.high_conf_wrong == 1
    assert m.flagged_wrong == 1
    assert m.unclear == 1
    assert (m.conflict, m.conflict_with_truth) == (1, 1)
    assert m.unexpected == 1
    assert m.details["contract.number"] == {
        "outcome": "wrong",
        "status": "EXTRACTED",
        "expected": "1011010-1",
        "actual": "IOIIOIO-I",
        "confidence": 0.8,
    }
    assert m.details["bank.iban"]["outcome"] == "missing"
    # ohne include_values keine Werte im Ergebnis
    plain = b.score_fields(truth, fields)
    assert set(plain.details["contract.number"]) == {"outcome", "status"}


def test_delivered_values_follow_status():
    b = bench()
    fields = {
        "a": _fv("insured.last_name", FieldStatus.UNCLEAR, "Mus7erfrau", None),
        "b": _fv("insured.first_name", FieldStatus.CONFLICT),
        "c": _fv("premium.amount", FieldStatus.EXTRACTED, "150,00 EUR", "150.00"),
        "d": _fv("contract.number", FieldStatus.MISSING),
    }
    assert b.delivered_values(fields) == {"a": "Mus7erfrau", "b": None, "c": "150.00", "d": None}


def test_aggregate_counts_each_file_once_overall_and_in_every_category():
    b = bench()
    r1 = b.DocResult("x1", "digital", ["digital", "tabelle"], "tabelle", "x1.pdf", b.METHOD_OCR)
    r1.total, r1.correct, r1.missing, r1.runtime_s, r1.peak_rss_mb = 10, 7, 3, 0.1, 90.0
    r2 = b.DocResult("x2", "tabelle", ["tabelle"], "tabelle", "x2.pdf", b.METHOD_OCR)
    r2.total, r2.correct, r2.wrong, r2.high_conf_wrong, r2.runtime_s, r2.peak_rss_mb = 10, 5, 5, 2, 2.9, 300.0
    other = b.DocResult("x2", "tabelle", ["tabelle"], "tabelle", "x2.pdf", b.METHOD_VLM)
    other.total, other.correct = 10, 10
    per_cat, overall = b.aggregate([r1, r2, other], b.METHOD_OCR)
    assert list(per_cat) == ["digital", "tabelle"]  # Reihenfolge wie CATEGORY_LABELS
    assert per_cat["digital"].files == 1 and per_cat["tabelle"].files == 2
    assert (overall.files, overall.total, overall.correct, overall.wrong) == (2, 20, 12, 5)
    assert overall.high_conf_wrong == 2
    assert overall.accuracy == pytest.approx(0.6)
    assert overall.error_rate == pytest.approx(0.25)
    assert overall.missing_rate == pytest.approx(0.15)
    assert overall.runtime_mean_s == pytest.approx(1.5)
    assert overall.peak_rss_mb == 300.0


def test_repeats_use_median_runtime_and_flag_deviations():
    b = bench()

    def res(runtime: float, correct: int):
        r = b.DocResult("x", "digital", ["digital"], "nachtrag", "x.pdf", b.METHOD_OCR)
        r.total, r.correct, r.missing, r.runtime_s, r.peak_rss_mb = 5, correct, 5 - correct, runtime, runtime
        return [r]

    combined = b._combine_repeats([res(1.0, 5), res(9.0, 5), res(2.0, 5)])[0]
    assert combined.runtime_s == 2.0 and combined.runtime_runs == [1.0, 9.0, 2.0]
    assert combined.peak_rss_mb == 9.0 and not combined.notes
    flagged = b._combine_repeats([res(1.0, 5), res(1.0, 4)])[0]
    assert flagged.correct == 5 and flagged.notes


def test_peak_memory_sampler_measures_or_stays_off():
    b = bench()
    with b.PeakMemorySampler(enabled=False) as off:
        pass
    assert off.peak_mb is None and off.mode == "aus"
    with b.PeakMemorySampler(interval=0.005) as sampler:
        block = bytearray(32 * 1024 * 1024)
        block[::4096] = b"x" * len(block[::4096])
    if sampler.mode != "aus":
        assert sampler.peak_mb is not None and sampler.peak_mb > 32


# ---------------------------------------------------------------------------
# Ende-zu-Ende über die Pipeline-Funktion
# ---------------------------------------------------------------------------


def test_digital_subset_end_to_end_without_tesseract(items):
    b = bench()
    ocr = TextRecognitionService(Settings(), backends={})  # kein OCR-Backend: Textebene muss reichen
    results = []
    for item_id in DIGITAL_SUBSET:
        item = items[item_id]
        assert not item.needs_ocr
        (r,) = b.evaluate_item(item, ocr)
        assert r.error is None, r.error
        assert r.method == b.METHOD_OCR
        assert r.ocr_pages == 0
        assert r.total == len(item.ground_truth)
        assert r.correct + r.wrong + r.missing == r.total
        assert set(r.fields) == set(item.ground_truth)
        assert r.doc_type_ok, (item_id, r.doc_type_detected)
        assert r.runtime_s > 0
        results.append(r)
    by_id = {r.item_id: r for r in results}
    for item_id in ("digital_nachtrag", "digital_verwechslung"):
        r = by_id[item_id]
        assert r.correct / r.total >= 0.8, item_id
        assert r.high_conf_wrong == 0, item_id
    per_cat, overall = b.aggregate(results, b.METHOD_OCR)
    assert {"digital", "tabelle", "verwechslung"} <= set(per_cat)
    assert overall.files == 3 and overall.total == sum(r.total for r in results)
    report = {
        "set": {"files": 3, "pages": 3, "fields": overall.total},
        "environment": {"cpu_count": 1, "platform": "test", "ocr_engines": []},
        "methods": [b.METHOD_OCR],
        "vlm": {"status": "not_requested"},
        "documents": [r.to_dict() for r in results],
    }
    md = b.render_markdown(report)
    assert "| digital |" in md and "**Gesamt**" in md and "digital_tabelle" in md


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract nicht installiert")
def test_scan_end_to_end_with_tesseract(items):
    b = bench()
    item = items["rauschen_ohne"]
    (r,) = b.evaluate_item(item, TextRecognitionService(Settings()))
    assert r.error is None, r.error
    assert r.ocr_pages == 1
    assert any("tesseract" in e.lower() for e in r.engines)
    assert r.total == len(item.ground_truth)
    assert r.correct + r.wrong + r.missing == r.total
    assert r.correct >= r.total // 2
    assert r.peak_rss_mb is None or r.peak_rss_mb > 0
