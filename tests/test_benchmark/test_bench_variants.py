"""Benchmark-Varianten: deterministisch, mit Musterlösung, synthetisch gekennzeichnet."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from bench_helpers import FIXTURE_SET
from PIL import Image

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory, FieldType
from insurance_app.samples.generator import CUSTOMER_IBAN, MUSTER_MARK
from insurance_app.samples.variants import (
    CATEGORY_LABELS,
    CONFUSABLE_IBAN,
    MANIFEST_NAME,
    describe_variants,
    generate_benchmark_set,
    load_benchmark_set,
    variant_ids,
)
from insurance_app.validation.validators import iban_is_valid

MAX_FIXTURE_BYTES = 15 * 1024 * 1024
FILE_STATS = ("sha256", "size_bytes")


def _without_stats(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k not in FILE_STATS}


# ---------------------------------------------------------------------------
# Umfang der Varianten
# ---------------------------------------------------------------------------


def test_variant_ids_unique_and_categories_known():
    ids = variant_ids()
    assert 18 <= len(ids) <= 24
    assert len(set(ids)) == len(ids)
    for item in describe_variants():
        for cat in item.categories:
            assert cat in CATEGORY_LABELS, cat


def test_variants_cover_requested_degradations():
    metas = [i.meta for i in describe_variants()]
    scans = [m for m in metas if m["needs_ocr"]]
    assert {m["noise"] for m in scans if m["category"] == "rauschen"} == {"ohne", "niedrig", "hoch"}
    skew_cat = [m for m in metas if "schraeglage" in (m["category"], *m["also_in"])]
    assert {m["skew_deg"] for m in skew_cat} == {0.0, 1.5, 3.0, 4.5}
    res_cat = [m for m in metas if "aufloesung" in (m["category"], *m["also_in"])]
    assert {m["dpi"] for m in res_cat} == {300, 200, 150, 100}
    assert any(m["jpeg_quality"] is not None and m["jpeg_quality"] <= 25 for m in metas)
    assert any(m["rotation_deg"] == 90 for m in metas)
    assert {m["format"] for m in metas} == {"pdf_text", "pdf_scan", "png", "jpeg"}
    layouts = {m["layout"]: m for m in metas}
    assert {"tabelle", "verwechslung", "nachtrag", "police_3seiten", "standmitteilung", "portal"} <= set(
        layouts
    )
    assert layouts["police_3seiten"]["page_count"] == 3
    assert sum(1 for m in metas if not m["needs_ocr"]) >= 3  # schnelle digitale Dateien


def test_confusable_values_present():
    truth = {i.id: i.ground_truth for i in describe_variants()}["digital_verwechslung"]
    assert truth["contract.number"] == "1011010-1"
    assert (truth["insured.first_name"], truth["insured.last_name"]) == ("Olli", "Illner")
    assert truth["bank.iban"] == CONFUSABLE_IBAN
    assert sum(ch in "01" for ch in CONFUSABLE_IBAN[4:]) >= 12


# ---------------------------------------------------------------------------
# Musterlösung
# ---------------------------------------------------------------------------

_PATTERNS = {
    FieldType.AMOUNT: re.compile(r"^\d+\.\d{2}$"),
    FieldType.POSTAL_CODE: re.compile(r"^\d{5}$"),
    FieldType.CURRENCY: re.compile(r"^[A-Z]{3}$"),
}


def test_ground_truth_uses_known_fields_and_canonical_values():
    for item in describe_variants():
        assert item.ground_truth, item.id
        for key, value in item.ground_truth.items():
            fdef = FIELD_REGISTRY.get(key)
            assert fdef is not None, key
            assert fdef.category == FieldCategory.CONTRACT and fdef.extractable, key
            assert value == value.strip() and value, (item.id, key)
            if fdef.dtype == FieldType.DATE:
                date.fromisoformat(value)
            elif fdef.dtype == FieldType.IBAN:
                assert iban_is_valid(value), key
                assert value in (CUSTOMER_IBAN, CONFUSABLE_IBAN)  # nur veröffentlichte Beispiel-IBANs
            elif fdef.dtype == FieldType.CHOICE:
                assert value in (fdef.choices or {}), (key, value)
            elif fdef.dtype in _PATTERNS:
                assert _PATTERNS[fdef.dtype].match(value), (key, value)


# ---------------------------------------------------------------------------
# Erzeugung
# ---------------------------------------------------------------------------

SUBSET = ["digital_nachtrag", "rauschen_niedrig", "aufloesung_100", "jpeg_q25_portal", "drehung_90"]


def test_generation_is_deterministic(tmp_path: Path):
    first = generate_benchmark_set(tmp_path / "a", only=SUBSET)
    second = generate_benchmark_set(tmp_path / "b", only=SUBSET)
    assert [i.id for i in first] == [i.id for i in second] == [i for i in variant_ids() if i in SUBSET]
    for a, b in zip(first, second, strict=True):
        assert a.file.read_bytes() == b.file.read_bytes(), a.id
        assert a.meta == b.meta
        assert a.ground_truth == b.ground_truth
        truth_file = a.file.parent / f"{a.file.name}.truth.json"
        assert json.loads(truth_file.read_text(encoding="utf-8")) == a.ground_truth
    manifest = json.loads((tmp_path / "a" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True and manifest["mark"] == MUSTER_MARK
    assert [e["file"] for e in manifest["items"]] == [i.file.name for i in first]


def test_seed_changes_noise_but_not_ground_truth(tmp_path: Path):
    a = generate_benchmark_set(tmp_path / "s0", seed=0, only=["aufloesung_100"])[0]
    b = generate_benchmark_set(tmp_path / "s1", seed=1, only=["aufloesung_100"])[0]
    assert a.file.read_bytes() != b.file.read_bytes()
    assert a.ground_truth == b.ground_truth


def test_unknown_variant_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="Unbekannte Varianten"):
        generate_benchmark_set(tmp_path, only=["gibt_es_nicht"])


def test_load_rejects_missing_or_foreign_set(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_benchmark_set(tmp_path)
    (tmp_path / MANIFEST_NAME).write_text(json.dumps({"synthetic": False, "items": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_benchmark_set(tmp_path)


# ---------------------------------------------------------------------------
# Mitgelieferter Testsatz (tests/fixtures/benchmark)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_items():
    return load_benchmark_set(FIXTURE_SET)


def test_fixture_set_matches_generator_description(fixture_items):
    described = describe_variants()
    assert [i.id for i in fixture_items] == [i.id for i in described]
    for fixed, desc in zip(fixture_items, described, strict=True):
        assert fixed.file.name == desc.file.name
        assert _without_stats(fixed.meta) == desc.meta, fixed.id
        assert fixed.ground_truth == desc.ground_truth, fixed.id


def test_fixture_files_intact_and_small(fixture_items):
    total = 0
    for item in fixture_items:
        data = item.file.read_bytes()
        assert hashlib.sha256(data).hexdigest() == item.meta["sha256"], item.id
        assert len(data) == item.meta["size_bytes"]
        total += len(data)
    total += sum(p.stat().st_size for p in FIXTURE_SET.glob("*.json"))
    assert total < MAX_FIXTURE_BYTES


def test_fixture_files_are_marked_synthetic(fixture_items):
    for item in fixture_items:
        if item.file.suffix == ".pdf":
            with pymupdf.open(str(item.file)) as doc:
                assert doc.metadata.get("subject") == MUSTER_MARK, item.id
                assert doc.page_count == item.meta["page_count"]
                if not item.needs_ocr:
                    for page in doc:
                        assert MUSTER_MARK in page.get_text(), (item.id, page.number)
        else:
            with Image.open(item.file) as img:
                info_mark = img.info.get("Comment") or img.info.get("comment")
                if isinstance(info_mark, bytes):
                    info_mark = info_mark.decode("utf-8")
                assert info_mark == MUSTER_MARK, item.id
