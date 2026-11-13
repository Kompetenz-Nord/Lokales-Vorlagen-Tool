"""Kommandozeile des Benchmarks: Ausgaben, --with-vlm ohne Ollama, nur Loopback."""

from __future__ import annotations

import json
from pathlib import Path

from bench_helpers import FIXTURE_SET, bench, free_loopback_port

from insurance_app.security.network_guard import is_installed


def test_with_vlm_is_skipped_gracefully_when_ollama_unavailable(tmp_path: Path, capsys):
    b = bench()
    guard_before = is_installed()
    out_json = tmp_path / "bericht.json"
    rc = b.main(
        [
            "--set-dir",
            str(FIXTURE_SET),
            "--only",
            "digital_nachtrag",
            "--with-vlm",
            "--ollama-url",
            f"http://127.0.0.1:{free_loopback_port()}",
            "--json",
            str(out_json),
            "--no-memory",
            "--quiet",
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert b.VLM_SKIPPED in stdout
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["vlm"]["status"] == "skipped"
    assert report["vlm"]["reason"]
    assert report["methods"] == [b.METHOD_OCR]
    assert [d["item_id"] for d in report["documents"]] == ["digital_nachtrag"]
    assert report["synthetic"] is True
    assert is_installed() == guard_before  # Netzwerk-Wächter wieder im Ausgangszustand


def test_vlm_runner_refuses_non_loopback_address():
    runner = bench().VlmRunner("http://192.0.2.10:11434", "qwen3-vl:4b")
    ok, reason = runner.availability()
    assert not ok
    assert "127.0.0.1" in reason


def test_markdown_and_json_outputs_with_details(tmp_path: Path, capsys):
    b = bench()
    md_path = tmp_path / "bericht.md"
    json_path = tmp_path / "bericht.json"
    rc = b.main(
        [
            "--set-dir",
            str(FIXTURE_SET),
            "--only",
            "digital_tabelle",
            "--details",
            "--repeat",
            "2",
            "--markdown",
            str(md_path),
            "--json",
            str(json_path),
            "--quiet",
        ]
    )
    assert rc == 0
    md = md_path.read_text(encoding="utf-8")
    assert md == capsys.readouterr().out.rstrip("\n") + "\n"
    assert "## Verfahren: nur OCR" in md and "Abweichungen je Feld" in md
    assert "Nicht angefordert" in md
    report = json.loads(json_path.read_text(encoding="utf-8"))
    doc = report["documents"][0]
    assert len(doc["runtime_runs"]) == 2
    assert doc["fields"]["contract.number"]["expected"] == "6602381-9"
    assert report["overall"]["ocr_only"]["files"] == 1
    assert set(report["categories"]["ocr_only"]) == {"digital", "tabelle"}


def test_unknown_variant_and_bad_repeat_fail_cleanly(capsys):
    b = bench()
    assert b.main(["--set-dir", str(FIXTURE_SET), "--only", "gibt_es_nicht", "--quiet"]) == 1
    assert "Unbekannte Varianten" in capsys.readouterr().err
    assert b.main(["--set-dir", str(FIXTURE_SET), "--repeat", "0", "--quiet"]) == 1


def test_set_dir_without_manifest_is_generated(tmp_path: Path, capsys):
    b = bench()
    target = tmp_path / "satz"
    rc = b.main(["--set-dir", str(target), "--only", "digital_nachtrag", "--generate-only", "--quiet"])
    assert rc == 0
    assert (target / "manifest.json").is_file()
    assert (target / "MUSTER_BENCH_digital_nachtrag.pdf").is_file()
