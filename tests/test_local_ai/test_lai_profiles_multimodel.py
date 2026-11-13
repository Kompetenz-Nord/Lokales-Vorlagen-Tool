"""Hardwareprofile und Mehrmodell-Ablauf (glm-ocr + qwen3-vl + gemma4) gegen simuliertes Ollama.

Es wird kein echtes Modell ausgeführt – der simulierte Dienst antwortet je Modell deterministisch.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from lai_fake_ollama import MUSTER_DIGEST, FakeState, running_fake  # noqa: E402

from insurance_app.core.models import ExtractionMethod, FieldStatus  # noqa: E402
from insurance_app.core.paths import AppPaths  # noqa: E402
from insurance_app.core.service import AppService, ServiceError  # noqa: E402
from insurance_app.local_ai.ollama_ocr import markdown_to_page_text  # noqa: E402
from insurance_app.local_ai.profiles import PROFILES, recommend_profile, settings_for_profile  # noqa: E402
from insurance_app.local_ai.status import MODEL_CATALOG, HardwareInfo  # noqa: E402

SYN = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"


def hw(ram: float | None, disk: float | None = 100.0) -> HardwareInfo:
    return HardwareInfo(
        total_ram_gb=ram,
        available_ram_gb=ram,
        free_disk_gb=disk,
        cpu_count=8,
        platform="test",
        models_dir="/tmp/m",
    )


def test_catalog_has_three_requested_models_with_roles():
    for name in ("glm-ocr", "qwen3-vl:4b", "gemma4:e4b"):
        entry = MODEL_CATALOG[name]
        assert entry.role and entry.advantages and entry.verified  # Größe aus Ollama-Registry-Manifest
        assert entry.num_ctx > 4096  # Bildanfragen brauchen mehr als den Ollama-Standard


@pytest.mark.parametrize(
    ("ram", "disk", "expected"),
    [
        (4, 100, None),
        (8, 100, "kompakt"),
        (16, 100, "standard"),
        (24, 100, "standard"),
        (32, 100, "maximal"),
        (64, 10, "standard"),
        (32, 2, None),
    ],
)
def test_recommend_profile(ram, disk, expected):
    key, reason = recommend_profile(hw(ram, disk))
    assert key == expected and reason


def test_profiles_models():
    assert PROFILES["kompakt"].models == ["glm-ocr"]
    assert PROFILES["standard"].models == ["glm-ocr", "qwen3-vl:4b"]
    assert PROFILES["maximal"].models == ["glm-ocr", "qwen3-vl:4b", "gemma4:e4b"]
    cfg = settings_for_profile("maximal")
    assert cfg["ai_model"] == "glm-ocr" and cfg["ai_fallback_model"] == "qwen3-vl:4b"
    assert cfg["ai_crosscheck_model"] == "gemma4:e4b"
    assert PROFILES["standard"].approx_download_gb == 6.0


def test_markdown_table_to_layout_extracts_label_values():
    md = "# Versicherungsschein (MUSTER)\n| Versicherungsnummer | 8815201-7 |\n|---|---|\n| Versicherungsbeginn | 01.01.2019 |\n**Versicherte Person** Erika Musterfrau"
    page = markdown_to_page_text(md, 1, 595, 842, engine="glm-ocr (test)")
    assert page.method == ExtractionMethod.LOCAL_VLM and page.words
    from insurance_app.core.models import DocumentText, DocumentType
    from insurance_app.extraction.extractor import SourceDocInfo, extract_candidates

    res = extract_candidates(
        DocumentText(document_id="D-T", pages=[page]), SourceDocInfo("D-T", "x.pdf", DocumentType.POLICY)
    )
    assert res.best("contract.number").normalized == "8815201-7"
    assert res.best("contract.start_date").normalized == "2019-01-01"


def _models(*names: str) -> list[dict]:
    return [{"name": n, "model": n, "size": 1, "digest": MUSTER_DIGEST, "details": {}} for n in names]


def _chat(payload: dict):
    model = payload["model"]
    if model == "glm-ocr":
        return 200, {
            "message": {
                "content": "| Versicherungsnummer | 8815201-7 |\n| Versicherungsbeginn | 01.01.2019 |"
            },
            "done_reason": "stop",
        }
    value = "2053-01-01" if model == "qwen3-vl:4b" else "2054-01-01"  # gemma widerspricht absichtlich
    body = {
        "fields": [
            {
                "field_key": "contract.end_date",
                "value": value,
                "quote": "Ablauf 01.01.2053",
                "page": 1,
                "region": None,
                "reason": "sichtbar",
                "uncertainty": "low",
            }
        ]
    }
    return 200, {"message": {"content": json.dumps(body)}, "done_reason": "stop"}


@pytest.fixture()
def svc(tmp_path: Path):
    service = AppService(
        AppPaths.for_testing(tmp_path),
        guard_network=False,
        configure_logs=False,
        today=lambda: date(2026, 11, 13),
    )
    yield service
    service.close()


def test_active_missing_uses_fallback_and_none_present_fails(svc: AppService):
    state = FakeState(models=_models("qwen3-vl:4b"), chat=_chat)
    with running_fake(state) as fake:
        svc.update_settings(ai_enabled=True, ollama_url=fake.url)
        svc.set_ai_models("glm-ocr", "qwen3-vl:4b")
        status = svc.ai_models_status()
        assert status["will_run"] == "qwen3-vl:4b" and status["ready"]
        assert "Ausweichmodell" in status["message"]
        svc.set_ai_models("glm-ocr", None)
        assert not svc.ai_models_status()["ready"]
        with pytest.raises(ServiceError):
            svc.run_local_ai(svc.create_case())


def test_active_model_failure_triggers_fallback(svc: AppService):
    def chat(payload):
        if payload["model"] == "glm-ocr":
            return 500, {"error": "simulierter Fehler"}
        return _chat(payload)

    state = FakeState(models=_models("glm-ocr", "qwen3-vl:4b"), chat=chat)
    with running_fake(state) as fake:
        svc.update_settings(ai_enabled=True, ollama_url=fake.url)
        svc.apply_ai_profile("standard")
        case = svc.create_case()
        svc.add_document(case, SYN / "MUSTER_Police_Direktversicherung.pdf")
        svc.process_documents(case)
        result = svc.run_local_ai(case)
        assert result["models_used"] == ["glm-ocr", "qwen3-vl:4b"]
        assert (
            case.field("contract.end_date").normalized_value == "2053-01-01"
        )  # Qwen bestätigt die Regel-Extraktion


def test_maximal_profile_crosscheck_creates_conflict(svc: AppService):
    state = FakeState(models=_models("glm-ocr", "qwen3-vl:4b", "gemma4:e4b"), chat=_chat)
    with running_fake(state) as fake:
        svc.update_settings(ai_enabled=True, ollama_url=fake.url)
        svc.apply_ai_profile("maximal")
        case = svc.create_case()
        svc.add_document(case, SYN / "MUSTER_Police_Direktversicherung.pdf")
        svc.process_documents(case)
        result = svc.run_local_ai(case)
        assert set(result["models_used"]) == {
            "glm-ocr",
            "gemma4:e4b",
        }  # glm-ocr erfolgreich -> kein Ausweichmodell
        # Ablaufdatum: Regel-Extraktion 2053, Gemma 2054 -> Konflikt, Mensch entscheidet
        assert case.field("contract.end_date").status == FieldStatus.CONFLICT
        assert all(
            fv.status not in (FieldStatus.EXTRACTED, FieldStatus.CONFLICT)
            for fv in case.order_fields.values()
        )
        assert "gemma4:e4b" in case.metadata.get("lokale_ki_modelle", "")


def test_real_glm_ocr_output_is_fully_extracted():
    """Echte GLM-OCR-Ausgabe (Ollama, glm-ocr, CPU) des synthetischen Police-Scans, Seite 1.

    GLM-OCR fasst Label und Wert zu einer Zeile zusammen; die Layout-Rekonstruktion muss
    trotzdem alle Sollwerte liefern (gemessen: 26/26 nach der Korrektur, vorher 1/26).
    """
    from insurance_app.core.models import DocumentText, DocumentType
    from insurance_app.extraction.extractor import SourceDocInfo, extract_candidates

    text = (SYN / "glm_ocr_ausgabe_police_scan_s1.txt").read_text("utf-8")
    page = markdown_to_page_text(text, 1, 595.28, 841.89, engine="glm-ocr")
    res = extract_candidates(
        DocumentText(document_id="D-G", pages=[page]), SourceDocInfo("D-G", "scan.pdf", DocumentType.POLICY)
    )
    truth = json.loads((SYN / "MUSTER_Police_Scan.pdf.truth.json").read_text("utf-8"))
    got = {k: (res.best(k).normalized if res.best(k) else None) for k in truth}
    assert got == truth
