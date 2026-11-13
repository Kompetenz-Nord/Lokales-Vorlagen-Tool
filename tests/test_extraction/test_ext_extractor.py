import json

import pytest
from extraction_helpers import FIXTURES, document_text_from_pdf, info_for, page_from_words, words_for_line

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory
from insurance_app.core.models import DocumentText, DocumentType, ExtractionMethod, TextLayerOrigin
from insurance_app.extraction.extractor import (
    EMBEDDED_OCR_FACTOR,
    ExtractionResult,
    SourceDocInfo,
    extract_candidates,
)
from insurance_app.samples.generator import (
    FILE_AMENDMENT,
    FILE_POLICY,
    FILE_PORTAL,
    FILE_STATUS_REPORT,
    INSURER_IBAN,
    portal_page_text,
)

PAGE2_FIELDS = {"bank.account_holder", "bank.iban", "bank.institution"}


def _truth(name: str) -> dict[str, str]:
    return json.loads((FIXTURES / f"{name}.truth.json").read_text(encoding="utf-8"))


def _assert_truth(result: ExtractionResult, truth: dict[str, str]) -> None:
    for key, expected in truth.items():
        cands = result.candidates.get(key)
        assert cands, f"{key} fehlt"
        normalized = {c.normalized for c in cands}
        assert normalized == {expected}, f"{key}: {normalized} != {expected}"


def _no_order_keys(result: ExtractionResult) -> None:
    for key in [*result.candidates, *result.placeholders]:
        assert FIELD_REGISTRY[key].category == FieldCategory.CONTRACT, key


@pytest.fixture(scope="module")
def policy_result() -> ExtractionResult:
    dt = document_text_from_pdf(FIXTURES / FILE_POLICY, "D-POL")
    return extract_candidates(dt, info_for(None, FILE_POLICY, "D-POL"))


def test_policy_all_truth_fields(policy_result):
    _assert_truth(policy_result, _truth(FILE_POLICY))
    _no_order_keys(policy_result)


def test_policy_evidence(policy_result):
    for key, cands in policy_result.candidates.items():
        for c in cands:
            ev = c.evidence
            assert ev.document_id == "D-POL"
            assert ev.file_name == FILE_POLICY
            assert ev.method == ExtractionMethod.PDF_TEXT
            assert ev.bbox is not None
            assert ev.rule_id
            assert ev.quote and len(ev.quote) <= 160
            assert ev.page == (2 if key in PAGE2_FIELDS else 1), key
            assert c.document_type == DocumentType.POLICY
            assert c.confidence is not None and 0 < c.confidence <= 1
    number = policy_result.candidates["contract.number"][0]
    assert number.evidence.quote == "Versicherungsnummer 8815201-7"
    assert number.confidence == pytest.approx(0.85)


def test_policy_split_name_notes_and_confidence(policy_result):
    last = policy_result.candidates["insured.last_name"][0]
    assert last.note is None  # "Nachname, Vorname" -> eindeutig
    assert last.confidence == pytest.approx(0.85 * 0.9)


def test_policy_insurer_bank_never_customer_bank(policy_result):
    ibans = [c.normalized for c in policy_result.candidates["bank.iban"]]
    assert INSURER_IBAN not in ibans


def test_amendment_truth():
    dt = document_text_from_pdf(FIXTURES / FILE_AMENDMENT, "D-NT")
    res = extract_candidates(dt, info_for(None, FILE_AMENDMENT, "D-NT"))
    _assert_truth(res, _truth(FILE_AMENDMENT))
    c = res.candidates["premium.amount"][0]
    assert c.document_type == DocumentType.AMENDMENT
    assert c.effective_date is not None and c.effective_date.isoformat() == "2024-07-01"


def test_portal_from_word_boxes():
    dt = DocumentText(document_id="D-PORT", pages=[portal_page_text()])
    res = extract_candidates(dt, info_for(None, FILE_PORTAL, "D-PORT"))
    _assert_truth(res, _truth(FILE_PORTAL))
    _no_order_keys(res)
    assert "premium.amount" in res.placeholders  # "Beitrag" ohne Wert
    assert res.candidates["contract.number"][0].evidence.method == ExtractionMethod.TESSERACT
    first = res.candidates["policyholder.first_name"][0]
    assert first.note == "Vor- und Nachname wurden automatisch getrennt – bitte prüfen."
    # Rückkaufswert/Rente sind keine Felder -> keine Kandidaten
    assert not any("rueckkauf" in k for k in res.candidates)


def test_status_report_embedded_ocr_layer():
    dt = document_text_from_pdf(FIXTURES / FILE_STATUS_REPORT, "D-SM", origin=TextLayerOrigin.EMBEDDED_OCR)
    res = extract_candidates(dt, info_for(None, FILE_STATUS_REPORT, "D-SM"))
    _assert_truth(res, _truth(FILE_STATUS_REPORT))
    assert not any(k.startswith("bank.") for k in res.candidates)
    assert "contract.end_date" not in res.candidates  # Rentenbeginn ist kein Ablauf
    start = res.candidates["contract.start_date"][0]
    assert start.evidence.page == 2
    assert start.confidence == pytest.approx(0.85 * EMBEDDED_OCR_FACTOR)
    assert start.evidence.model_version == "fremde OCR-Textebene"
    # Briefanschrift (Vermittler) wird nicht als Kunde übernommen
    for key, cands in res.candidates.items():
        for c in cands:
            assert "Finanz" not in c.value, key
            assert "Maklerweg" not in c.value, key
    assert any("Fußzeile" in w for w in res.warnings)


def test_ocr_word_confidence_scales_candidate():
    words = words_for_line("Versicherungsbeginn", 56, 200, conf=0.9) + words_for_line(
        "01.01.2019", 230, 200, conf=0.5
    )
    dt = DocumentText(document_id="D", pages=[page_from_words(words, method=ExtractionMethod.TESSERACT)])
    res = extract_candidates(dt, SourceDocInfo("D", "x.png", DocumentType.UNKNOWN))
    assert res.candidates["contract.start_date"][0].confidence == pytest.approx(0.85 * 0.5)


def test_unparsable_value_becomes_unreadable_candidate():
    words = words_for_line("Versicherungsbeginn", 56, 200) + words_for_line("O1.?1.2O19", 230, 200)
    dt = DocumentText(document_id="D", pages=[page_from_words(words)])
    res = extract_candidates(dt, SourceDocInfo("D", "x.pdf", DocumentType.UNKNOWN))
    c = res.candidates["contract.start_date"][0]
    assert c.normalized is None
    assert c.value == "O1.?1.2O19"
    assert c.note == "Wert nicht eindeutig lesbar"
    assert c.confidence < 0.85


def test_identical_values_are_deduplicated_keeping_best():
    words = words_for_line("Versicherungsnummer", 56, 200) + words_for_line("8815201-7", 230, 200)
    words += words_for_line("Ihre Versicherung Nr. 8815201-7 wird geprüft.", 56, 400)
    dt = DocumentText(document_id="D", pages=[page_from_words(words)])
    res = extract_candidates(dt, SourceDocInfo("D", "x.pdf", DocumentType.UNKNOWN))
    cands = res.candidates["contract.number"]
    assert len(cands) == 1
    assert cands[0].evidence.rule_id == "layout_right"


def test_running_text_never_yields_text_fields():
    text = "Der Versicherungsnehmer kann die Versicherung kündigen."
    words = words_for_line("Versicherungsnehmer kann die Versicherung jederzeit kündigen", 56, 300)
    words += words_for_line(text, 56, 320)
    dt = DocumentText(document_id="D", pages=[page_from_words(words)])
    res = extract_candidates(dt, SourceDocInfo("D", "x.pdf", DocumentType.UNKNOWN))
    assert res.candidates == {}


def test_empty_page_gives_warning_only():
    dt = DocumentText(document_id="D", pages=[page_from_words([])])
    res = extract_candidates(dt, SourceDocInfo("D", "x.pdf", DocumentType.UNKNOWN))
    assert res.candidates == {}
    assert res.warnings == ["Seite 1: kein lesbarer Text – keine Werte übernommen."]


def test_best_helper(policy_result):
    assert policy_result.best("contract.number").normalized == "8815201-7"
    assert policy_result.best("order.payment_iban") is None
