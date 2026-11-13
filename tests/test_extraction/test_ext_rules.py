from extraction_helpers import page_from_words, words_for_line

from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory
from insurance_app.core.models import DocumentText, DocumentType, Word
from insurance_app.core.textutil import fold
from insurance_app.extraction.extractor import SourceDocInfo, extract_candidates
from insurance_app.extraction.layout import group_lines
from insurance_app.extraction.rules import (
    COMPOSITE_KEYS,
    LABELS,
    SECTION_LABELS,
    find_bav_keyword,
    find_inline_contract_numbers,
    find_insurer_names,
    find_laufzeit_ranges,
)

INFO = SourceDocInfo(document_id="D-R", file_name="test.pdf", doc_type=DocumentType.UNKNOWN)


def _extract(words: list[Word], **kw):
    return extract_candidates(DocumentText(document_id="D-R", pages=[page_from_words(words, **kw)]), INFO)


def _norm(result, key):
    return [c.normalized for c in result.candidates.get(key, [])]


def test_labels_contain_no_order_fields():
    keys = set(LABELS)
    for mapping in SECTION_LABELS.values():
        keys |= set(mapping)
    for key in keys:
        if key.startswith("_"):
            continue
        for member in COMPOSITE_KEYS.get(key, (key,)):
            assert FIELD_REGISTRY[member].category == FieldCategory.CONTRACT, member
            assert not member.startswith("order.")


def test_end_date_never_uses_rentenbeginn_labels():
    assert not any("rentenbeginn" in fold(p) for p in LABELS["contract.end_date"])


def test_rentenbeginn_is_never_end_date():
    words = words_for_line("Rentenbeginn", 56, 200) + words_for_line("01.08.2065", 230, 200)
    words += words_for_line("Spätester Rentenbeginn", 56, 218) + words_for_line("01.08.2070", 230, 218)
    res = _extract(words)
    assert "contract.end_date" not in res.candidates
    assert _norm(res, "contract.pension_start_date") == ["2065-08-01"]


def test_footer_iban_of_insurer_is_ignored():
    footer = "Bankverbindung: Beispielbank · IBAN DE02 1203 0000 0000 2020 51 · BIC BYLADEM1001"
    words = words_for_line(footer, 56, 790, h=8)
    words += words_for_line("Gläubiger-ID DE98ZZZ09999999999", 56, 800, h=8)
    words += words_for_line("Versicherungsnummer", 56, 200) + words_for_line("8815201-7", 230, 200)
    res = _extract(words)
    assert not any(k.startswith("bank.") for k in res.candidates)
    assert any("Fußzeile" in w for w in res.warnings)
    assert "DE02" not in " ".join(res.warnings)


def test_customer_bank_section_is_extracted_but_creditor_line_is_not():
    words = words_for_line("Bankverbindung", 56, 300, h=12)
    words += words_for_line("Kontoinhaber", 56, 318) + words_for_line("Muster Bäckerei GmbH", 230, 318)
    words += words_for_line("IBAN", 56, 336) + words_for_line("DE89 3704 0044 0532 0130 00", 230, 336)
    words += words_for_line(
        "Ihre Bankverbindung: IBAN DE02 1203 0000 0000 2020 51 Gläubiger-ID DE98ZZZ09999999999", 56, 500
    )
    res = _extract(words)
    assert _norm(res, "bank.iban") == ["DE89370400440532013000"]
    assert _norm(res, "bank.account_holder") == ["Muster Bäckerei GmbH"]


def test_iban_without_customer_context_is_ignored():
    words = words_for_line("IBAN", 56, 336) + words_for_line("DE89 3704 0044 0532 0130 00", 230, 336)
    res = _extract(words)
    assert "bank.iban" not in res.candidates


def test_iban_equal_to_footer_iban_is_dropped_everywhere():
    words = words_for_line("Bankverbindung", 56, 300, h=12)
    words += words_for_line("IBAN", 56, 318) + words_for_line("DE02 1203 0000 0000 2020 51", 230, 318)
    words += words_for_line("Bankverbindung: Beispielbank IBAN DE02 1203 0000 0000 2020 51", 56, 790, h=8)
    res = _extract(words)
    assert "bank.iban" not in res.candidates


def test_letter_window_address_is_not_used():
    words = []
    for i, line in enumerate(
        ["Beispiel Finanz AG", "Herrn Max Beispiel", "Musterstraße 1", "12345 Musterstadt"]
    ):
        words += words_for_line(line, 56, 160 + 12 * i)
    words += words_for_line("Versicherung Nr.", 360, 160) + words_for_line("9630030-1", 360, 172)
    res = _extract(words)
    assert _norm(res, "contract.number") == ["9630030-1"]
    address_keys = [
        k for k in res.candidates if k.split(".")[-1] in {"street", "postal_code", "city", "house_number"}
    ]
    assert address_keys == []
    assert not any(k.startswith(("policyholder.", "insured.")) for k in res.candidates)


def test_inline_contract_number_regex():
    words = words_for_line("Jährliche Information zur Entwicklung Ihrer Versicherung Nr. 9630030-1", 56, 300)
    page = page_from_words(words)
    hits = find_inline_contract_numbers(page, group_lines(page.words))
    assert [h.value_text for h in hits] == ["9630030-1"]
    assert hits[0].rule_id == "inline_contract_number"
    assert [w.text for w in hits[0].value_words] == ["9630030-1"]


def test_inline_contract_number_requires_digits():
    words = words_for_line("Bitte geben Sie Ihre Versicherungsnummer immer an.", 56, 300)
    page = page_from_words(words)
    assert find_inline_contract_numbers(page, group_lines(page.words)) == []


def test_laufzeit_range_inline():
    words = words_for_line("Die Laufzeit vom 01.02.2022 bis 01.08.2065 ist vereinbart.", 56, 300)
    page = page_from_words(words)
    hits = find_laufzeit_ranges(page, group_lines(page.words))
    assert hits and hits[0].field_key == "contract.term"
    res = _extract(words)
    assert _norm(res, "contract.start_date") == ["2022-02-01"]
    assert _norm(res, "contract.end_date") == ["2065-08-01"]
    assert res.candidates["contract.end_date"][0].evidence.rule_id == "laufzeit_range"


def test_bav_keyword_near_estg():
    for text in ("Direktversicherung nach § 3 Nr. 63 EStG", "Direktversicherung nach 8 3 Nr. 63 EStG"):
        page = page_from_words(words_for_line(text, 56, 130))
        hits = find_bav_keyword(page, group_lines(page.words))
        assert [h.value_text for h in hits] == ["Direktversicherung"], text
        assert hits[0].confidence <= 0.5
    page = page_from_words(words_for_line("Direktversicherung ohne Rechtsgrundlage", 56, 130))
    assert find_bav_keyword(page, group_lines(page.words)) == []


def test_insurer_name_from_header_only():
    header = words_for_line("Muster Lebensversicherung AG (MUSTER)", 56, 40, h=12)
    page = page_from_words(header)
    assert [h.value_text for h in find_insurer_names(page, group_lines(page.words))] == [
        "Muster Lebensversicherung AG"
    ]
    middle = words_for_line("Muster Lebensversicherung AG", 56, 400)
    page = page_from_words(middle)
    assert find_insurer_names(page, group_lines(page.words)) == []


def test_insurer_known_name_fallback():
    page = page_from_words(words_for_line("Swiss Life · Kundenservice", 56, 40))
    hits = find_insurer_names(page, group_lines(page.words))
    assert [h.value_text for h in hits] == ["Swiss Life"]
    assert hits[0].rule_id == "insurer_known_name"


def test_generic_versicherung_ag_is_not_an_insurer_name():
    page = page_from_words(words_for_line("Ihre Versicherung AG", 56, 40))
    assert find_insurer_names(page, group_lines(page.words)) == []


def test_premium_frequency_inferred_from_label():
    words = words_for_line("Monatsbeitrag", 56, 200) + words_for_line("99,90 €", 230, 200)
    res = _extract(words)
    assert _norm(res, "premium.amount") == ["99.90"]
    assert _norm(res, "premium.currency") == ["EUR"]
    assert _norm(res, "premium.frequency") == ["monthly"]
    assert res.candidates["premium.frequency"][0].evidence.rule_id == "premium_frequency_from_label"


def test_employment_dates_from_labels():
    words = words_for_line("Beginn der Betriebszugehörigkeit", 56, 200) + words_for_line(
        "01.04.2015", 260, 200
    )
    words += words_for_line("Austrittsdatum", 56, 218) + words_for_line("30.06.2024", 260, 218)
    res = _extract(words)
    assert _norm(res, "employer.employment_start") == ["2015-04-01"]
    assert _norm(res, "employer.employment_end") == ["2024-06-30"]
