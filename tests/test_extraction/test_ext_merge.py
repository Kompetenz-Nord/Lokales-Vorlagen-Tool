from datetime import UTC, date, datetime

import pytest
from extraction_helpers import FIXTURES, document_text_from_pdf, info_for

from insurance_app.core.fields import FieldCategory, contract_fields
from insurance_app.core.models import (
    Candidate,
    DocumentText,
    DocumentType,
    Evidence,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
    TextLayerOrigin,
)
from insurance_app.extraction.extractor import ExtractionResult, SourceDocInfo, extract_candidates
from insurance_app.extraction.merge import group_documents, merge_candidates
from insurance_app.samples.generator import (
    FILE_AMENDMENT,
    FILE_POLICY,
    FILE_PORTAL,
    FILE_STATUS_REPORT,
    portal_page_text,
)


@pytest.fixture(scope="module")
def results() -> dict[str, tuple[SourceDocInfo, ExtractionResult]]:
    out = {}
    for name, doc_id in ((FILE_POLICY, "D-POL"), (FILE_AMENDMENT, "D-NT")):
        info = info_for(None, name, doc_id)
        out[name] = (info, extract_candidates(document_text_from_pdf(FIXTURES / name, doc_id), info))
    info = info_for(None, FILE_STATUS_REPORT, "D-SM")
    dt = document_text_from_pdf(FIXTURES / FILE_STATUS_REPORT, "D-SM", origin=TextLayerOrigin.EMBEDDED_OCR)
    out[FILE_STATUS_REPORT] = (info, extract_candidates(dt, info))
    info = info_for(None, FILE_PORTAL, "D-PORT")
    out[FILE_PORTAL] = (
        info,
        extract_candidates(DocumentText(document_id="D-PORT", pages=[portal_page_text()]), info),
    )
    return out


def _ev(doc: str = "D-X", page: int = 1) -> Evidence:
    return Evidence(document_id=doc, file_name=f"{doc}.pdf", page=page, method=ExtractionMethod.PDF_TEXT)


def _cand(key: str, norm: str | None, conf: float = 0.8, doc: str = "D-X", **kw) -> Candidate:
    return Candidate(
        field_key=key, value=norm or "???", normalized=norm, confidence=conf, evidence=_ev(doc), **kw
    )


def _res(**cands: list[Candidate]) -> ExtractionResult:
    return ExtractionResult(candidates={k.replace("__", "."): v for k, v in cands.items()})


def test_policy_and_amendment_conflicts_with_hint(results):
    out = merge_candidates([results[FILE_POLICY], results[FILE_AMENDMENT]])
    premium = out.fields["premium.amount"]
    assert premium.status == FieldStatus.CONFLICT
    assert premium.value is None and premium.normalized_value is None
    assert {c.normalized for c in premium.candidates} == {"150.00", "100.00"}
    hinted = premium.candidates[premium.suggestion_index]
    assert hinted.document_type == DocumentType.AMENDMENT
    assert hinted.normalized == "100.00"
    assert premium.note == (
        "Hinweis: Der Nachtrag (gültig ab 01.07.2024) ist fachlich vermutlich maßgeblich – bitte selbst entscheiden."
    )
    for key in ("insured.street", "insured.house_number", "insured.postal_code", "insured.city"):
        fv = out.fields[key]
        assert fv.status == FieldStatus.CONFLICT, key
        assert fv.candidates[fv.suggestion_index].evidence.document_id == "D-NT"
    for key in ("contract.number", "premium.frequency", "policyholder.name", "insured.last_name"):
        assert out.fields[key].status == FieldStatus.EXTRACTED, key
    number = out.fields["contract.number"]
    assert number.normalized_value == "8815201-7"
    assert len(number.candidates) == 2
    assert number.source_document_id in {"D-POL", "D-NT"}
    assert out.warnings == []


def test_outcome_contains_only_contract_fields(results):
    out = merge_candidates([results[FILE_POLICY]])
    assert set(out.fields) == {f.key for f in contract_fields()}
    assert all(f.category == FieldCategory.CONTRACT for f in contract_fields())
    assert out.fields["contract.status"].status == FieldStatus.MISSING


def test_history_entries_for_extraction_and_conflict(results):
    out = merge_candidates([results[FILE_POLICY], results[FILE_AMENDMENT]])
    actions = {(h.field_key, h.action) for h in out.history}
    assert ("premium.amount", "conflict") in actions
    assert ("contract.number", "extracted") in actions
    assert all(h.actor == "system" for h in out.history)


def test_full_musterfall_agrees_across_three_documents(results):
    out = merge_candidates([results[FILE_POLICY], results[FILE_AMENDMENT], results[FILE_STATUS_REPORT]])
    assert out.fields["contract.start_date"].status == FieldStatus.EXTRACTED
    assert len(out.fields["contract.start_date"].candidates) == 2
    assert out.fields["insured.first_name"].status == FieldStatus.EXTRACTED
    assert out.fields["contract.pension_start_date"].status == FieldStatus.EXTRACTED
    assert out.warnings == []


def test_different_contracts_warning_without_numbers(results):
    out = merge_candidates([results[FILE_POLICY], results[FILE_PORTAL]])
    codes = [w.code for w in out.warnings]
    assert codes == ["DOCS_DIFFERENT_CONTRACTS"]
    assert "8815201" not in out.warnings[0].message and "5550123" not in out.warnings[0].message
    assert out.fields["contract.number"].status == FieldStatus.CONFLICT
    assert out.fields["contract.number"].suggestion_index is None


def test_group_documents(results):
    ordered = [
        results[FILE_POLICY],
        results[FILE_PORTAL],
        results[FILE_AMENDMENT],
        results[FILE_STATUS_REPORT],
    ]
    assert group_documents(ordered) == [["D-POL", "D-NT", "D-SM"], ["D-PORT"]]
    no_number = (SourceDocInfo("D-EMPTY", "leer.pdf", DocumentType.UNKNOWN), ExtractionResult())
    assert group_documents([*ordered, no_number])[-1] == ["D-EMPTY"]


def test_placeholder_note_for_missing_field(results):
    out = merge_candidates([results[FILE_PORTAL]])
    fv = out.fields["premium.amount"]
    assert fv.status == FieldStatus.MISSING
    assert fv.note == "In der Quelle ist für dieses Feld kein Wert eingetragen."
    res = ExtractionResult(
        placeholders={"contract.status": [_ev().model_copy(update={"quote": "Vertragsstatus Unbekannt"})]}
    )
    out = merge_candidates([(SourceDocInfo("D-X", "x.pdf", DocumentType.UNKNOWN), res)])
    assert out.fields["contract.status"].note == "Quelle nennt den Wert als 'Unbekannt'."


def test_confirmed_value_preserved_when_sources_agree():
    confirmed = FieldValue(
        key="premium.amount",
        value="100,00 EUR",
        normalized_value="100.00",
        status=FieldStatus.CONFIRMED,
        user_confirmed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    info = SourceDocInfo("D-NT", "nt.pdf", DocumentType.AMENDMENT, effective_date=date(2024, 7, 1))
    res = _res(premium__amount=[_cand("premium.amount", "100.00", doc="D-NT")])
    out = merge_candidates([(info, res)], existing={"premium.amount": confirmed})
    fv = out.fields["premium.amount"]
    assert fv.status == FieldStatus.CONFIRMED
    assert fv.normalized_value == "100.00"
    assert fv.user_confirmed_at == confirmed.user_confirmed_at
    assert len(fv.candidates) == 1
    assert [h.action for h in out.history if h.field_key == "premium.amount"] == ["sources_added"]


def test_confirmed_value_conflicts_with_differing_new_source():
    confirmed = FieldValue(
        key="premium.amount", value="150,00", normalized_value="150.00", status=FieldStatus.MANUAL
    )
    info = SourceDocInfo("D-NT", "nt.pdf", DocumentType.AMENDMENT, effective_date=date(2024, 7, 1))
    res = _res(premium__amount=[_cand("premium.amount", "100.00", doc="D-NT")])
    out = merge_candidates([(info, res)], existing={"premium.amount": confirmed})
    fv = out.fields["premium.amount"]
    assert fv.status == FieldStatus.CONFLICT
    assert fv.value is None
    pseudo = fv.candidates[0]
    assert pseudo.evidence.file_name == "bisher bestätigter Wert"
    assert pseudo.evidence.method == ExtractionMethod.MANUAL
    assert pseudo.normalized == "150.00"
    assert fv.note == "Neue Quelle weicht vom bestätigten Wert ab."
    assert fv.candidates[1].normalized == "100.00"


def test_resolved_conflict_is_not_raised_again_on_rerun(results):
    first = merge_candidates([results[FILE_POLICY], results[FILE_AMENDMENT]])
    conflict = first.fields["premium.amount"]
    chosen = conflict.candidates[conflict.suggestion_index]
    confirmed = conflict.model_copy(
        update={
            "status": FieldStatus.CONFIRMED,
            "value": chosen.value,
            "normalized_value": chosen.normalized,
            "suggestion_index": None,
        }
    )
    second = merge_candidates(
        [results[FILE_POLICY], results[FILE_AMENDMENT]], existing={"premium.amount": confirmed}
    )
    assert second.fields["premium.amount"].status == FieldStatus.CONFIRMED
    assert second.fields["premium.amount"].normalized_value == "100.00"


def test_not_applicable_and_manual_stay():
    na = FieldValue(key="contract.tariff", status=FieldStatus.NOT_APPLICABLE)
    manual = FieldValue(
        key="contract.status", value="aktiv", normalized_value="aktiv", status=FieldStatus.MANUAL
    )
    res = _res(contract__tariff=[_cand("contract.tariff", "XY")])
    out = merge_candidates(
        [(SourceDocInfo("D-X", "x.pdf", DocumentType.POLICY), res)],
        existing={"contract.tariff": na, "contract.status": manual},
    )
    assert out.fields["contract.tariff"] == na
    assert out.fields["contract.status"] == manual


def test_order_fields_never_touched():
    existing_order = FieldValue(
        key="order.payment_iban", normalized_value="DE89370400440532013000", status=FieldStatus.MANUAL
    )
    res = ExtractionResult(
        candidates={"order.payment_iban": [_cand("order.payment_iban", "DE89370400440532013000")]}
    )
    out = merge_candidates(
        [(SourceDocInfo("D-X", "x.pdf", DocumentType.POLICY), res)],
        existing={"order.payment_iban": existing_order},
    )
    assert not any(k.startswith("order.") for k in out.fields)
    assert not any((h.field_key or "").startswith("order.") for h in out.history)


def test_suggestion_requires_shared_contract_number_and_effective_date():
    pol = SourceDocInfo("D-P", "p.pdf", DocumentType.POLICY)
    nt = SourceDocInfo("D-N", "n.pdf", DocumentType.AMENDMENT, effective_date=date(2024, 7, 1))
    nt_no_date = SourceDocInfo("D-N", "n.pdf", DocumentType.AMENDMENT)

    def pair(nt_info, nt_number):
        rp = _res(
            premium__amount=[_cand("premium.amount", "150.00", doc="D-P")],
            contract__number=[_cand("contract.number", "111-1", doc="D-P")],
        )
        rn = _res(
            premium__amount=[
                _cand(
                    "premium.amount",
                    "100.00",
                    doc="D-N",
                    document_type=DocumentType.AMENDMENT,
                    effective_date=nt_info.effective_date,
                )
            ],
            contract__number=[_cand("contract.number", nt_number, doc="D-N")],
        )
        return [(pol, rp), (nt_info, rn)]

    ok = merge_candidates(pair(nt, "111-1")).fields["premium.amount"]
    assert ok.suggestion_index == 1
    other_contract = merge_candidates(pair(nt, "222-2")).fields["premium.amount"]
    assert other_contract.suggestion_index is None
    assert (
        other_contract.note
        == "Die Quellen nennen unterschiedliche Werte – bitte den richtigen Wert auswählen."
    )
    no_date = merge_candidates(pair(nt_no_date, "111-1")).fields["premium.amount"]
    assert no_date.suggestion_index is None


def test_low_confidence_and_unreadable_become_unclear():
    info = SourceDocInfo("D-X", "x.pdf", DocumentType.UNKNOWN)
    out = merge_candidates(
        [
            (
                info,
                _res(
                    contract__tariff=[_cand("contract.tariff", "XY", conf=0.3)],
                    contract__start_date=[_cand("contract.start_date", None, conf=0.4)],
                ),
            )
        ]
    )
    assert out.fields["contract.tariff"].status == FieldStatus.UNCLEAR
    assert out.fields["contract.tariff"].normalized_value == "XY"
    start = out.fields["contract.start_date"]
    assert start.status == FieldStatus.UNCLEAR
    assert start.normalized_value is None and start.value == "???"


def test_text_variants_are_not_a_conflict():
    info = SourceDocInfo("D-X", "x.pdf", DocumentType.UNKNOWN)
    res = _res(
        contract__status=[_cand("contract.status", "Ruhend", 0.9), _cand("contract.status", "ruhend", 0.5)],
        contract__number=[_cand("contract.number", "8815201-7"), _cand("contract.number", "88152017", 0.4)],
    )
    out = merge_candidates([(info, res)])
    assert out.fields["contract.status"].status == FieldStatus.EXTRACTED
    assert out.fields["contract.status"].normalized_value == "Ruhend"
    assert out.fields["contract.number"].status == FieldStatus.EXTRACTED


def test_previously_extracted_value_without_source_becomes_missing():
    old = FieldValue(key="contract.tariff", value="XY", normalized_value="XY", status=FieldStatus.EXTRACTED)
    out = merge_candidates([], existing={"contract.tariff": old})
    assert out.fields["contract.tariff"].status == FieldStatus.MISSING
    assert [(h.field_key, h.action) for h in out.history] == [("contract.tariff", "cleared")]
