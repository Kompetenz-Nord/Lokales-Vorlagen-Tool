"""Regressionsschutz für die Texterkennung: gedrehte Seite, starkes Rauschen, Tabellen, Lesefehler.

Misst mit dem Benchmark-Skript auf dem festen synthetischen Testsatz (MUSTER – KEINE ECHTDATEN).
Die Schwellen liegen knapp unter den am 13.11.2026 gemessenen Werten; vor den Korrekturen
lagen ``drehung_90`` und ``rauschen_hoch`` bei 0 von 26 richtigen Feldern.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from bench_helpers import FIXTURE_SET, bench
from PIL import Image

from insurance_app.ocr.preprocess import denoise_median
from insurance_app.ocr.tesseract_backend import DEFAULT_OMP_THREAD_LIMIT, TesseractBackend, subprocess_env

pytestmark = pytest.mark.skipif(
    not TesseractBackend().is_available()[0], reason="Tesseract ist nicht installiert"
)

#: Varianten-ID -> (mindestens richtig, höchstens „sicher falsch“)
EXPECTED = {
    "drehung_90": (25, 0),
    "rauschen_hoch": (24, 1),
    "digital_tabelle": (11, 0),
    "tabelle_scan": (11, 0),
    "verwechslung_scan": (22, 0),
}


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("bench") / "bericht.json"
    rc = bench().main(
        [
            "--set-dir",
            str(FIXTURE_SET),
            "--only",
            ",".join(EXPECTED),
            "--json",
            str(out),
            "--no-memory",
            "--quiet",
        ]
    )
    assert rc == 0
    return {d["item_id"]: d for d in json.loads(out.read_text(encoding="utf-8"))["documents"]}


@pytest.mark.parametrize("item_id", list(EXPECTED))
def test_difficult_scans_are_read(report: dict, item_id: str):
    min_correct, max_confident_wrong = EXPECTED[item_id]
    doc = report[item_id]
    assert doc["error"] is None
    assert doc["correct"] >= min_correct, doc
    assert doc["high_conf_wrong"] <= max_confident_wrong, doc


def test_misread_iban_is_corrected_but_stays_unclear(report: dict):
    """O statt 0 gelesen: Wert wird nur bei gültiger Prüfziffer korrigiert und bleibt „Unklar“."""
    field = report["verwechslung_scan"]["fields"]["bank.iban"]
    assert field == {"outcome": "correct", "status": "UNCLEAR"}


def test_possible_letter_confusion_is_never_confident(report: dict):
    for key in ("insured.street", "policyholder.street"):
        field = report["verwechslung_scan"]["fields"][key]
        assert field["status"] != "EXTRACTED", (key, field)


def test_tesseract_runs_single_threaded_unless_configured(monkeypatch):
    monkeypatch.delenv("OMP_THREAD_LIMIT", raising=False)
    env = subprocess_env()
    assert env["OMP_THREAD_LIMIT"] == DEFAULT_OMP_THREAD_LIMIT == "1"
    assert "OMP_THREAD_LIMIT" not in os.environ  # die App-Umgebung bleibt unverändert
    monkeypatch.setenv("OMP_THREAD_LIMIT", "3")
    assert subprocess_env()["OMP_THREAD_LIMIT"] == "3"  # bewusste Einstellung hat Vorrang


def test_median_denoise_removes_specks_and_keeps_size(tmp_path: Path):
    img = Image.new("L", (40, 40), 255)
    for x, y in ((5, 5), (20, 11), (33, 30)):
        img.putpixel((x, y), 0)  # einzelne Störpunkte
    cleaned = denoise_median(img)
    assert cleaned.size == img.size and cleaned.getextrema() == (255, 255)
    assert img.getpixel((5, 5)) == 0  # Original unverändert
    with pytest.raises(ValueError):
        denoise_median(img, size=4)
