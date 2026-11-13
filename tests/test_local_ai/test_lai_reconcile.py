"""Abgleich von KI-Vorschlägen mit vorhandenen Feldwerten – alle Zweige."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from insurance_app.core.models import (
    Candidate,
    Evidence,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
)
from insurance_app.local_ai.reconcile import (
    NOTE_AI_CONFLICT,
    NOTE_AI_DIFFERS,
    NOTE_AI_ONLY,
    reconcile,
    same_value,
)


def ai(key: str, value: str, normalized: str | None, page: int = 1, quote: str | None = None) -> Candidate:
    return Candidate(
        field_key=key,
        value=value,
        normalized=normalized,
        confidence=0.7,
        evidence=Evidence(
            document_id="D-0000MUST",
            file_name="MUSTER_Police.pdf",
            page=page,
            quote=quote or f"Wert {value}",
            method=ExtractionMethod.LOCAL_VLM,
            model_version="qwen3-vl:4b (a1b2c3d4e5f6)",
            rule_id="local_vlm",
        ),
    )


def ocr(key: str, value: str, normalized: str | None) -> Candidate:
    return Candidate(
        field_key=key,
        value=value,
        normalized=normalized,
        confidence=0.6,
        evidence=Evidence(
            document_id="D-0000MUST",
            file_name="MUSTER_Police.pdf",
            page=1,
            quote=f"Wert {value}",
            method=ExtractionMethod.TESSERACT,
            rule_id="label_right",
        ),
    )


def extracted(
    key: str, value: str, normalized: str | None, status: FieldStatus = FieldStatus.EXTRACTED
) -> FieldValue:
    c = ocr(key, value, normalized)
    return FieldValue(
        key=key,
        value=value,
        normalized_value=normalized,
        status=status,
        confidence=0.6,
        source_document_id="D-0000MUST",
        source_page=1,
        source_quote=c.evidence.quote,
        extraction_method=ExtractionMethod.TESSERACT,
        candidates=[c],
    )


KEY = "contract.start_date"


@pytest.mark.parametrize("status", [FieldStatus.CONFIRMED, FieldStatus.MANUAL])
def test_released_values_are_never_changed_silently(status):
    fv = extracted(KEY, "01.01.2019", "2019-01-01", status)
    fv.user_confirmed_at = datetime(2026, 11, 13, tzinfo=UTC)
    fields = {KEY: fv}
    out, history = reconcile(fields, [ai(KEY, "01.02.2019", "2019-02-01")])
    new = out[KEY]
    assert new.status == status
    assert new.normalized_value == "2019-01-01"
    assert new.user_confirmed_at == fv.user_confirmed_at
    assert new.note == NOTE_AI_DIFFERS
    assert len(new.candidates) == 2 and new.candidates[-1].evidence.method == ExtractionMethod.LOCAL_VLM
    assert history[0].action == "ai_suggestion_differs"
    assert history[0].old_status == history[0].new_status == status
    assert "2019-02-01" in history[0].detail
    # Eingabe unverändert
    assert len(fields[KEY].candidates) == 1 and fields[KEY].note is None


def test_confirmed_with_matching_ai_value_gets_no_warning():
    fv = extracted(KEY, "01.01.2019", "2019-01-01", FieldStatus.CONFIRMED)
    fv.note = "vom Nutzer geprüft"
    out, history = reconcile({KEY: fv}, [ai(KEY, "1.1.2019", "2019-01-01")])
    assert out[KEY].status == FieldStatus.CONFIRMED
    assert out[KEY].note == "vom Nutzer geprüft"
    assert len(out[KEY].candidates) == 2
    assert history[0].action == "ai_suggestion_matches"


def test_not_applicable_stays_but_shows_hint():
    fv = FieldValue(key=KEY, status=FieldStatus.NOT_APPLICABLE, note="nicht relevant")
    out, history = reconcile({KEY: fv}, [ai(KEY, "01.01.2019", "2019-01-01")])
    assert out[KEY].status == FieldStatus.NOT_APPLICABLE
    assert out[KEY].value is None
    assert out[KEY].note == f"nicht relevant | {NOTE_AI_DIFFERS}"
    assert len(history) == 1


def test_extracted_same_value_is_kept():
    fv = extracted("contract.number", "8815201-7", "8815201-7")
    out, history = reconcile({"contract.number": fv}, [ai("contract.number", "8815201 7", "8815201 7")])
    new = out["contract.number"]
    assert new.status == FieldStatus.EXTRACTED
    assert new.normalized_value == "8815201-7"
    assert len(new.candidates) == 2
    assert history[0].action == "ai_suggestion_matches"


def test_extracted_different_value_becomes_conflict():
    fv = extracted(KEY, "01.01.2019", "2019-01-01")
    out, history = reconcile({KEY: fv}, [ai(KEY, "01.07.2019", "2019-07-01")])
    new = out[KEY]
    assert new.status == FieldStatus.CONFLICT
    assert new.value is None and new.normalized_value is None
    assert new.source_document_id is None and new.extraction_method is None
    assert {c.normalized for c in new.candidates} == {"2019-01-01", "2019-07-01"}
    assert new.note == NOTE_AI_CONFLICT
    assert new.suggestion_index is None
    assert history[0].action == "ai_conflict"
    assert history[0].old_value == "2019-01-01" and history[0].new_value == "2019-07-01"


def test_extracted_without_candidate_list_keeps_previous_value_as_alternative():
    fv = FieldValue(
        key=KEY,
        value="01.01.2019",
        normalized_value="2019-01-01",
        status=FieldStatus.EXTRACTED,
        source_document_id="D-0000MUST",
        source_page=2,
        extraction_method=ExtractionMethod.PDF_TEXT,
    )
    out, _ = reconcile({KEY: fv}, [ai(KEY, "01.07.2019", "2019-07-01")])
    new = out[KEY]
    assert new.status == FieldStatus.CONFLICT
    previous = [c for c in new.candidates if c.normalized == "2019-01-01"]
    assert previous and previous[0].evidence.method == ExtractionMethod.PDF_TEXT
    assert previous[0].evidence.page == 2


def test_missing_becomes_extracted_with_ai_evidence():
    out, history = reconcile({}, [ai(KEY, "01.01.2019", "2019-01-01", page=2)])
    new = out[KEY]
    assert new.status == FieldStatus.EXTRACTED
    assert new.value == "01.01.2019" and new.normalized_value == "2019-01-01"
    assert new.extraction_method == ExtractionMethod.LOCAL_VLM
    assert new.source_page == 2 and new.source_document_id == "D-0000MUST"
    assert new.model_version == "qwen3-vl:4b (a1b2c3d4e5f6)"
    assert new.note == NOTE_AI_ONLY
    assert history[0].action == "ai_extracted"
    assert history[0].old_status == FieldStatus.MISSING and history[0].new_status == FieldStatus.EXTRACTED
    assert "LOCAL_VLM" in history[0].source


def test_missing_with_unparsable_ai_value_becomes_unclear():
    explicit_missing = FieldValue(key=KEY, status=FieldStatus.MISSING)
    out, _ = reconcile({KEY: explicit_missing}, [ai(KEY, "irgendwann", None)])
    assert out[KEY].status == FieldStatus.UNCLEAR
    assert out[KEY].normalized_value is None
    assert "Nur durch lokale KI" in out[KEY].note


def test_unclear_with_unparsable_ocr_becomes_extracted_if_ai_parsable():
    fv = extracted(KEY, "0l.0l.20l9", None, FieldStatus.UNCLEAR)
    fv.value = None
    out, history = reconcile({KEY: fv}, [ai(KEY, "01.01.2019", "2019-01-01")])
    assert out[KEY].status == FieldStatus.EXTRACTED
    assert out[KEY].normalized_value == "2019-01-01"
    assert out[KEY].note == NOTE_AI_ONLY
    assert len(out[KEY].candidates) == 2
    assert history[0].new_status == FieldStatus.EXTRACTED


def test_unclear_stays_unclear_if_ai_also_unparsable():
    fv = extracted(KEY, "0l.0l.20l9", None, FieldStatus.UNCLEAR)
    out, history = reconcile({KEY: fv}, [ai(KEY, "01.0?.2019", None)])
    assert out[KEY].status == FieldStatus.UNCLEAR
    assert len(out[KEY].candidates) == 2
    assert history[0].action == "ai_suggestion_added"


def test_unclear_with_parsable_candidates_or_none_stays_unclear():
    ambiguous = FieldValue(
        key=KEY, status=FieldStatus.UNCLEAR, candidates=[ocr(KEY, "01.01.2019", "2019-01-01")]
    )
    out, _ = reconcile({KEY: ambiguous}, [ai(KEY, "01.01.2019", "2019-01-01")])
    assert out[KEY].status == FieldStatus.UNCLEAR
    user_marked = FieldValue(key=KEY, status=FieldStatus.UNCLEAR)
    out, _ = reconcile({KEY: user_marked}, [ai(KEY, "01.01.2019", "2019-01-01")])
    assert out[KEY].status == FieldStatus.UNCLEAR


def test_conflict_stays_conflict():
    fv = FieldValue(
        key=KEY,
        status=FieldStatus.CONFLICT,
        candidates=[ocr(KEY, "01.01.2019", "2019-01-01"), ocr(KEY, "01.02.2019", "2019-02-01")],
    )
    out, history = reconcile({KEY: fv}, [ai(KEY, "01.01.2019", "2019-01-01")])
    assert out[KEY].status == FieldStatus.CONFLICT
    assert out[KEY].value is None
    assert len(out[KEY].candidates) == 3
    assert history[0].old_status == history[0].new_status == FieldStatus.CONFLICT


def test_two_differing_ai_values_on_missing_field_conflict():
    out, history = reconcile(
        {}, [ai(KEY, "01.01.2019", "2019-01-01", 1), ai(KEY, "01.02.2019", "2019-02-01", 2)]
    )
    assert out[KEY].status == FieldStatus.CONFLICT
    assert [h.action for h in history] == ["ai_extracted", "ai_conflict"]


def test_reconcile_is_idempotent():
    cand = ai(KEY, "01.01.2019", "2019-01-01")
    out1, h1 = reconcile({}, [cand])
    out2, h2 = reconcile(out1, [cand])
    assert out2[KEY] == out1[KEY]
    assert len(h1) == 1 and h2 == []


@pytest.mark.parametrize("key", ["order.payment_iban", "order.severance_consent", "order.continuation_mode"])
def test_order_keys_rejected(key):
    fields = {"contract.number": extracted("contract.number", "8815201-7", "8815201-7")}
    with pytest.raises(ValueError):
        reconcile(fields, [ai("contract.number", "1", "1"), ai(key, "x", "x")])


def test_unknown_key_rejected():
    with pytest.raises(ValueError):
        reconcile({}, [ai("gibt.es_nicht", "x", "x")])


def test_same_value_rules():
    assert same_value("insured.last_name", "Müller", None, "MUELLER", None)
    assert same_value("contract.number", "8815201-7", None, "88152017", None)
    assert not same_value(KEY, "2019-01-01", None, "2019-01-02", None)
    assert same_value(KEY, None, "0l.0l.2019", None, "0L.0L.2019")
    assert not same_value(KEY, None, None, "2019-01-01", "01.01.2019")
    assert same_value("premium.amount", "150.00", "150,00", "150.00", "150.00 EUR")
