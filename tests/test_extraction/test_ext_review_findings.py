"""Regressionstests zu geprüften Befunden (Extraktion, Normalisierung, Zusammenführung).

Alle Daten sind synthetisch (MUSTER). Testnamen enthalten die Befund-ID.
"""

import unicodedata
from datetime import date

import pytest
from extraction_helpers import page_from_words, words_for_line

from insurance_app.core.fields import FIELD_REGISTRY
from insurance_app.core.models import (
    Candidate,
    DocumentText,
    DocumentType,
    Evidence,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
    Word,
)
from insurance_app.extraction.extractor import ExtractionResult, SourceDocInfo, extract_candidates
from insurance_app.extraction.layout import find_label_values
from insurance_app.extraction.merge import (
    CONFIRMED_LABEL,
    NOTE_REFERENCE,
    contract_number_key,
    group_documents,
    merge_candidates,
)
from insurance_app.extraction.normalize import (
    NOTE_AMOUNT_STRICT,
    NOTE_MONTH_DAY,
    NOTE_NAME_UNCLEAR,
    NOTE_TWO_DIGIT_YEAR,
    is_placeholder,
    is_reference,
    normalize_amount,
    normalize_month,
    normalize_value,
    parse_amount,
    split_person_name,
)
from insurance_app.extraction.rules import (
    KNOWN_INSURERS,
    LABELS,
    RULE_CONFIDENCE,
    SECTION_LABELS,
    STOP_LABELS,
    find_insurer_names,
)

INFO = SourceDocInfo(document_id="D-F", file_name="muster.pdf", doc_type=DocumentType.UNKNOWN)
TODAY = date(2026, 11, 13)


def _rows(*rows: tuple[str, float, float]) -> list[Word]:
    words: list[Word] = []
    for text, x, y in rows:
        words += words_for_line(text, x, y)
    return words


def _extract(*rows: tuple[str, float, float], **kw) -> ExtractionResult:
    page = page_from_words(_rows(*rows), **kw)
    return extract_candidates(DocumentText(document_id="D-F", pages=[page]), INFO)


def _norm(res: ExtractionResult, key: str) -> list[str | None]:
    return [c.normalized for c in res.candidates.get(key, [])]


def _f(key: str):
    return FIELD_REGISTRY[key]


# ---------------------------------------------------------------------------
# EXT-01: bestätigter Wert geht bei erneuter Zusammenführung nicht verloren
# ---------------------------------------------------------------------------


def _doc(doc_id: str, **values: str) -> tuple[SourceDocInfo, ExtractionResult]:
    info = SourceDocInfo(document_id=doc_id, file_name=f"{doc_id}.pdf", doc_type=DocumentType.POLICY)
    cands = {
        key.replace("__", "."): [
            Candidate(
                field_key=key.replace("__", "."),
                value=val,
                normalized=val,
                confidence=0.85,
                evidence=Evidence(
                    document_id=doc_id, file_name=f"{doc_id}.pdf", page=1, method=ExtractionMethod.PDF_TEXT
                ),
            )
        ]
        for key, val in values.items()
    }
    return info, ExtractionResult(candidates=cands)


def test_ext_01_manual_value_survives_every_remerge_while_in_conflict():
    key = "insured.birth_date"
    manual = FieldValue(
        key=key,
        value="01.02.1965",
        normalized_value="1965-02-01",
        status=FieldStatus.MANUAL,
        extraction_method=ExtractionMethod.MANUAL,
    )
    docs = [_doc("D-A", insured__birth_date="1966-02-01"), _doc("D-B", insured__birth_date="1966-02-01")]
    first = merge_candidates(docs, existing={key: manual}).fields[key]
    assert first.status == FieldStatus.CONFLICT
    assert first.candidates[0].evidence.file_name == CONFIRMED_LABEL

    # erneute Extraktion (z. B. nach reset_field eines anderen Feldes) – mehrfach
    fields = {key: first}
    for _ in range(3):
        outcome = merge_candidates(docs, existing=fields)
        fields = outcome.fields
        fv = fields[key]
        assert fv.status == FieldStatus.CONFLICT
        pseudo = fv.candidates[0]
        assert pseudo.evidence.file_name == CONFIRMED_LABEL
        assert pseudo.evidence.method == ExtractionMethod.MANUAL
        assert pseudo.normalized == "1965-02-01"
        assert sum(c.evidence.file_name == CONFIRMED_LABEL for c in fv.candidates) == 1
        assert {c.normalized for c in fv.candidates[1:]} == {"1966-02-01"}

    # auch ohne Dokumente bleibt der bestätigte Wert wählbar
    alone = merge_candidates([], existing=fields).fields[key]
    assert alone.status == FieldStatus.CONFLICT
    assert [c.normalized for c in alone.candidates] == ["1965-02-01"]


# ---------------------------------------------------------------------------
# EXT-02: Namensaufteilung mit Geburtsdatum/Anschrift im selben Wert
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "last", "first", "birth"),
    [
        ("Max Mustermann, geb. 12.03.1980", "Mustermann", "Max", "1980-03-12"),
        ("Max Mustermann, geboren am 12.03.1980", "Mustermann", "Max", "1980-03-12"),
        ("Herr Max Mustermann (* 12.03.1980)", "Mustermann", "Max", "1980-03-12"),
        ("Max Mustermann, Musterweg 5, 12345 Musterstadt", "Mustermann", "Max", None),
        ("Mustermann, Max, Musterweg 5, 12345 Musterstadt", "Mustermann", "Max", None),
        ("Erika Musterfrau geb. Beispiel", "Musterfrau", "Erika", None),
    ],
)
def test_ext_02_split_person_name_cuts_birth_and_address_tail(raw, last, first, birth):
    split = split_person_name(raw)
    assert (split.last, split.first, split.birth_date) == (last, first, birth)
    assert "geb" not in (split.first or "")


def test_ext_02_comma_rule_only_for_name_like_parts():
    assert split_person_name("Musterfrau, Erika").confidence == 0.9
    odd = split_person_name("Mustermann, Max, Dr. med. dent. Zusatz")
    assert odd.confidence < 0.45
    assert odd.note == NOTE_NAME_UNCLEAR


def test_ext_02_company_with_address_keeps_name_and_legal_form_clean():
    split = split_person_name("Muster GmbH, Industriestraße 5, 12345 Stadt")
    assert split.is_company
    assert split.last == "Muster GmbH"
    assert split.legal_form == "GmbH"
    co = split_person_name("Muster Bau GmbH & Co. KG, Industriestraße 5")
    assert (co.last, co.legal_form) == ("Muster Bau GmbH & Co. KG", "GmbH & Co. KG")


def test_ext_02_pipeline_insured_with_birth_date_and_policyholder_with_address():
    res = _extract(("Versicherte Person: Max Mustermann, geb. 12.03.1980", 56, 200))
    assert _norm(res, "insured.last_name") == ["Mustermann"]
    assert _norm(res, "insured.first_name") == ["Max"]
    assert _norm(res, "insured.birth_date") == ["1980-03-12"]
    res = _extract(("Versicherungsnehmer: Muster GmbH, Industriestraße 5, 12345 Stadt", 56, 200))
    assert _norm(res, "policyholder.name") == ["Muster GmbH"]
    assert _norm(res, "policyholder.legal_form") == ["GmbH"]


# ---------------------------------------------------------------------------
# EXT-03: kurzes Label + unbekannter Zusatz
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Ablauf der Aufschubzeit: 01.08.2065",
        "Ablauf der Beitragszahlungsdauer: 01.07.2050",
        "Laufzeit der Rentengarantiezeit: 01.08.2065 bis 31.07.2075",
    ],
)
def test_ext_03_qualified_label_tail_is_not_assigned(line):
    res = _extract((line, 56, 200))
    assert "contract.end_date" not in res.candidates
    assert "contract.start_date" not in res.candidates


def test_ext_03_bank_label_with_qualifier_is_not_assigned():
    res = _extract(("Bankverbindung des Zahlungsempfängers: IBAN DE89 3704 0044 0532 0130 00", 56, 400))
    assert "bank.iban" not in res.candidates


def test_ext_03_legitimate_tight_values_still_work():
    assert _norm(_extract(("Beitrag ab 01.07.2025: 125,00 EUR", 56, 200)), "premium.amount") == ["125.00"]
    assert _norm(_extract(("Ablauf zum 01.01.2050", 56, 200)), "contract.end_date") == ["2050-01-01"]


# ---------------------------------------------------------------------------
# EXT-04: Betrag nicht aus Datum/Jahr/Anzahl davor lesen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ab 07/2025: 125,00 EUR", "125.00"),
        ("ab 1. Juli 2025: 125,00 EUR", "125.00"),
        ("2025: 87,50 EUR", "87.50"),
        ("ab Juli 2025: 125,00", "125.00"),
        ("ab 01.07.: 125,00 EUR", "125.00"),
        ("1.234,56 EUR", "1234.56"),
        ("150,-", "150.00"),
        ("1,234", None),  # mehrdeutig (Tausenderkomma?) -> nicht raten
        ("125,00 EUR (bisher 100,00 EUR)", None),  # zwei Beträge -> nicht raten
        ("ab 07/2025", None),
        ("2O0,00", None),  # Texterkennung: O statt 0
    ],
)
def test_ext_04_amount_ignores_dates_years_and_ambiguity(raw, expected):
    assert normalize_amount(raw)[0] == expected


def test_ext_04_pipeline_premium_after_month_year():
    res = _extract(("Beitrag ab 07/2025: 125,00 EUR", 56, 200))
    assert _norm(res, "premium.amount") == ["125.00"]
    res = _extract(("Monatsbeitrag 2025: 87,50 EUR", 56, 200))
    assert _norm(res, "premium.amount") == ["87.50"]


# ---------------------------------------------------------------------------
# EXT-05: IBAN des Versicherers nicht als Kunden-IBAN
# ---------------------------------------------------------------------------

INSURER_IBAN = "DE89 3704 0044 0532 0130 00"


def test_ext_05_unsere_bankverbindung_mid_page_is_ignored():
    res = _extract((f"Unsere Bankverbindung: IBAN {INSURER_IBAN} BIC COBADEFFXXX", 56, 400))
    assert "bank.iban" not in res.candidates
    assert "DE89370400440532013000" in res.insurer_ibans


@pytest.mark.parametrize("top", [40.0, 200.0])
def test_ext_05_letterhead_info_column_is_ignored(top):
    res = _extract(
        ("Bankverbindung:", 380, top),
        (f"IBAN {INSURER_IBAN}", 380, top + 18),
        ("BIC COBADEFFXXX", 380, top + 36),
    )
    assert "bank.iban" not in res.candidates


def test_ext_05_customer_bank_details_are_still_extracted():
    res = _extract(("Ihre Bankverbindung", 56, 400), (f"IBAN: {INSURER_IBAN}", 56, 418))
    assert _norm(res, "bank.iban") == ["DE89370400440532013000"]


def test_ext_05_merge_drops_insurer_iban_known_from_another_document():
    letter = _extract((f"Unsere Bankverbindung: IBAN {INSURER_IBAN}", 56, 400))
    portal = _extract(("Ihre Bankverbindung", 56, 400), (f"IBAN: {INSURER_IBAN}", 56, 418))
    info_b = SourceDocInfo(document_id="D-G", file_name="b.pdf", doc_type=DocumentType.UNKNOWN)
    out = merge_candidates([(INFO, letter), (info_b, portal)])
    assert out.fields["bank.iban"].status == FieldStatus.MISSING
    assert any(w.code == "INSURER_IBAN_IGNORED" for w in out.warnings)


# ---------------------------------------------------------------------------
# EXT-06: Personenabschnitte enden (Dritte, Entfernung, Fußzeile)
# ---------------------------------------------------------------------------


def test_ext_06_beneficiary_block_does_not_fill_insured():
    res = _extract(
        ("Versicherte Person", 56, 200),
        ("Name: Max Mustermann", 56, 218),
        ("Bezugsberechtigt im Todesfall", 56, 260),
        ("Name: Erika Beispiel", 56, 278),
        ("Anschrift: Nebenweg 7, 54321 Andersstadt", 56, 296),
    )
    assert _norm(res, "insured.last_name") == ["Mustermann"]
    for key in ("insured.street", "insured.postal_code", "insured.city"):
        assert key not in res.candidates, key


@pytest.mark.parametrize("heading", ["Ihr Ansprechpartner", "Arbeitgeber"])
def test_ext_06_third_party_block_does_not_fill_person(heading):
    res = _extract(
        ("Versicherungsnehmer", 56, 150),
        ("Name: Max Mustermann", 56, 168),
        (heading, 56, 210),
        ("Name: Hans Makler", 56, 228),
        ("Anschrift: Maklerweg 1, 11111 Maklerstadt", 56, 246),
    )
    assert _norm(res, "policyholder.name") == ["Mustermann"]
    assert "policyholder.postal_code" not in res.candidates
    assert "policyholder.city" not in res.candidates


def test_ext_06_footer_far_below_section_is_not_policyholder():
    res = _extract(
        ("Versicherungsnehmer", 56, 150),
        ("Name: Max Mustermann", 56, 168),
        ("Anschrift: Hauptweg 1, 12345 Musterstadt", 56, 186),
        ("Anschrift: Beispiel Leben AG, Musterallee 1, 85748 Musterhausen", 56, 780),
    )
    assert _norm(res, "policyholder.postal_code") == ["12345"]
    assert _norm(res, "policyholder.city") == ["Musterstadt"]


# ---------------------------------------------------------------------------
# EXT-07: zweispaltige Zeilen
# ---------------------------------------------------------------------------


def _find(*rows):
    page = page_from_words(_rows(*rows))
    out = {}
    for m in find_label_values(page, LABELS, SECTION_LABELS, stop_labels=STOP_LABELS):
        out.setdefault(m.field_key, m)
    return out


def test_ext_07_empty_value_does_not_take_next_columns_label_and_value():
    res = _extract(
        ("Versicherte Person", 56, 180),
        ("Vorname", 56, 200),
        ("Geburtsdatum 12.03.1980", 260, 200),
    )
    assert "insured.first_name" not in res.candidates
    assert _norm(res, "insured.birth_date") == ["1980-03-12"]


def test_ext_07_value_stops_at_known_label_after_column_gap():
    found = _find(("Tarif", 56, 200), ("DV-Klassik", 150, 200), ("Status", 245, 200), ("aktiv", 300, 200))
    assert found["contract.tariff"].value_text == "DV-Klassik"
    assert found["contract.status"].value_text == "aktiv"
    found = _find(
        ("Produkt", 56, 200),
        ("Basisrente Invest", 150, 200),
        ("Vermittler", 290, 200),
        ("Hans Makler", 360, 200),
    )
    assert found["contract.product"].value_text == "Basisrente Invest"
    assert found["contract.intermediary"].value_text == "Hans Makler"


def test_ext_07_value_beginning_with_label_word_after_colon_is_kept():
    found = _find(("Art der Änderung: Beitrag 100,00 EUR", 56, 200))
    assert found["contract.changes"].value_text == "Beitrag 100,00 EUR"


# ---------------------------------------------------------------------------
# EXT-08: „Bisheriger Beitrag:“ & Co.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("Bisheriger Beitrag: 100,00 EUR", "premium.amount"),
        ("Ursprüngliches Ablaufdatum: 01.01.2050", "contract.end_date"),
        ("Früherer Vermittler: Hans Altmakler", "contract.intermediary"),
        ("Alter Beitrag: 100,00 EUR", "premium.amount"),
    ],
)
def test_ext_08_label_with_old_value_qualifier_is_skipped(line, key):
    assert key not in _extract((line, 56, 200)).candidates


def test_ext_08_neuer_beitrag_still_extracted():
    assert _norm(_extract(("Neuer Beitrag: 90,00 EUR", 56, 200)), "premium.amount") == ["90.00"]


# ---------------------------------------------------------------------------
# EXT-09: „Unternehmen“ im Abschnitt Versicherungsnehmer
# ---------------------------------------------------------------------------


def test_ext_09_unternehmen_in_policyholder_section_is_policyholder():
    res = _extract(("Versicherungsnehmer", 56, 180), ("Unternehmen: Muster Bau GmbH", 56, 200))
    assert "insurer.name" not in res.candidates
    assert _norm(res, "policyholder.name") == ["Muster Bau GmbH"]


def test_ext_09_unternehmen_outside_person_sections_is_insurer():
    res = _extract(("Unternehmen", 56, 200), ("Beispiel Lebensversicherung AG", 200, 200))
    assert _norm(res, "insurer.name") == ["Beispiel Lebensversicherung AG"]
    res = _extract(("Vertragsdaten", 56, 180), ("Unternehmen: Beispiel Lebensversicherung AG", 56, 200))
    assert _norm(res, "insurer.name") == ["Beispiel Lebensversicherung AG"]


# ---------------------------------------------------------------------------
# EXT-10: Versicherername aus Straßennamen / Vertrieb
# ---------------------------------------------------------------------------


def test_ext_10_street_name_in_letterhead_is_not_an_insurer():
    page = page_from_words(
        words_for_line("Beispiel Leben AG · Stuttgarter Straße 5 · 70173 Stuttgart", 56, 60)
    )
    assert find_insurer_names(page, page_lines(page)) == []
    page = page_from_words(words_for_line("Nürnberger Str. 12", 56, 60))
    assert find_insurer_names(page, page_lines(page)) == []


def test_ext_10_sales_firm_is_not_a_known_insurer_and_known_name_is_unclear():
    assert all(pattern != "tecis" for pattern, _ in KNOWN_INSURERS)
    assert RULE_CONFIDENCE["insurer_known_name"] < 0.45
    res = _extract(("Swiss Life · Kundenservice", 56, 40))
    out = merge_candidates([(INFO, res)])
    assert out.fields["insurer.name"].status == FieldStatus.UNCLEAR


def page_lines(page):
    from insurance_app.extraction.layout import group_lines

    return group_lines(page.words)


# ---------------------------------------------------------------------------
# EXT-11: Verweise und „nicht angegeben“
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["nicht angegeben", "n.v.", "N.N.", "-/-", "keine", "keiner", "ohne", "?", "xxx"]
)
def test_ext_11_more_placeholders(raw):
    assert is_placeholder(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("wie Versicherungsnehmer", True),
        ("siehe oben", True),
        ("s. o.", True),
        ("identisch mit VN", True),
        ("= VN", True),
        ("Versicherungsnehmer", True),
        ("Wiesbaden", False),
        ("Max Mustermann", False),
        ("gleichbleibend", False),
    ],
)
def test_ext_11_is_reference(raw, expected):
    assert is_reference(raw) is expected


@pytest.mark.parametrize("value", ["wie Versicherungsnehmer", "siehe oben", "nicht angegeben"])
def test_ext_11_reference_or_missing_value_never_becomes_a_name(value):
    res = _extract((f"Versicherte Person: {value}", 56, 200))
    assert "insured.last_name" not in res.candidates
    assert "insured.first_name" not in res.candidates
    out = merge_candidates([(INFO, res)])
    assert out.fields["insured.last_name"].status == FieldStatus.MISSING


def test_ext_11_reference_gets_explaining_note():
    res = _extract(("Versicherte Person: wie Versicherungsnehmer", 56, 200))
    out = merge_candidates([(INFO, res)])
    assert out.fields["insured.last_name"].note == NOTE_REFERENCE


def test_ext_11_intermediary_nv_is_placeholder():
    res = _extract(("Vermittler: n.v.", 56, 200))
    assert "contract.intermediary" not in res.candidates
    assert "contract.intermediary" in res.placeholders


# ---------------------------------------------------------------------------
# EXT-12: zweistellige Jahre feldabhängig
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("insured.birth_date", "03.04.56", "1956-04-03"),
        ("insured.birth_date", "01.01.10", "2010-01-01"),
        ("insured.birth_date", "14.11.26", "1926-11-14"),  # morgen -> nicht in der Zukunft
        ("contract.end_date", "01.08.72", "2072-08-01"),
        ("contract.end_date", "1.1.69", "2069-01-01"),
        ("contract.start_date", "01.01.85", "1985-01-01"),
        ("contract.end_date", "01.08.2072", "2072-08-01"),
    ],
)
def test_ext_12_two_digit_year_is_field_aware(key, raw, expected):
    normalized, note = normalize_value(_f(key), raw, today=TODAY)
    assert normalized == expected
    assert (note == NOTE_TWO_DIGIT_YEAR) is (len(raw.rsplit(".", 1)[-1]) == 2)


def test_ext_12_pipeline_birth_date_two_digit_year():
    res = _extract(("Geburtsdatum: 03.04.56", 56, 200))
    assert _norm(res, "insured.birth_date") == ["1956-04-03"]
    assert res.candidates["insured.birth_date"][0].note == NOTE_TWO_DIGIT_YEAR


# ---------------------------------------------------------------------------
# EXT-13: Reihenfolge laut Beschriftung
# ---------------------------------------------------------------------------


def test_ext_13_label_order_name_vorname_without_comma():
    res = _extract(("Versicherte Person", 56, 180), ("Name, Vorname: Mustermann Max", 56, 200))
    assert _norm(res, "insured.last_name") == ["Mustermann"]
    assert _norm(res, "insured.first_name") == ["Max"]


def test_ext_13_label_order_vorname_name_with_comma():
    res = _extract(("Versicherte Person", 56, 180), ("Vorname, Name: Max, Mustermann", 56, 200))
    assert _norm(res, "insured.last_name") == ["Mustermann"]
    assert _norm(res, "insured.first_name") == ["Max"]


def test_ext_13_split_person_name_order_parameter():
    assert split_person_name("Mustermann Max", order="last_first").last == "Mustermann"
    assert split_person_name("Max, Mustermann", order="first_last").last == "Mustermann"
    assert split_person_name("Max Mustermann").last == "Mustermann"  # ohne Angabe unverändert


# ---------------------------------------------------------------------------
# EXT-15: Formatunterschiede ergeben keinen Konflikt
# ---------------------------------------------------------------------------


def test_ext_15_formatting_differences_do_not_conflict():
    a = _doc(
        "D-P",
        policyholder__street="Musterstraße",
        insurer__name="Swiss Life AG",
        contract__number="LV1234567",
    )
    b = _doc("D-Q", policyholder__street="Musterstr.", insurer__name="Swiss Life", contract__number="1234567")
    out = merge_candidates([a, b])
    for key in ("policyholder.street", "insurer.name", "contract.number"):
        assert out.fields[key].status == FieldStatus.EXTRACTED, key
    assert not any(w.code == "DOCS_DIFFERENT_CONTRACTS" for w in out.warnings)
    assert group_documents([a, b]) == [["D-P", "D-Q"]]
    assert contract_number_key("LV 1234567") == contract_number_key("1234567")
    assert contract_number_key("8815201-7") == "88152017"


def test_ext_15_real_differences_still_conflict():
    a = _doc("D-P", policyholder__street="Musterstraße", insurer__name="Swiss Life AG")
    b = _doc("D-Q", policyholder__street="Strandweg", insurer__name="Beispiel Leben AG")
    out = merge_candidates([a, b])
    assert out.fields["policyholder.street"].status == FieldStatus.CONFLICT
    assert out.fields["insurer.name"].status == FieldStatus.CONFLICT


# ---------------------------------------------------------------------------
# EXT-17: Land nicht im Ort
# ---------------------------------------------------------------------------


def test_ext_17_country_continuation_goes_to_country_not_city():
    res = _extract(
        ("Versicherungsnehmer", 56, 180),
        ("Anschrift", 56, 200),
        ("Musterstraße 12, 12345 Musterstadt", 200, 200),
        ("Deutschland", 200, 218),
    )
    assert _norm(res, "policyholder.city") == ["Musterstadt"]
    assert _norm(res, "policyholder.country") == ["DE"]


def test_ext_17_country_in_same_line_is_split_off():
    res = _extract(
        ("Versicherte Person", 56, 180), ("Anschrift: Beispielweg 7, 12345 Musterstadt, Deutschland", 56, 200)
    )
    assert _norm(res, "insured.city") == ["Musterstadt"]
    assert _norm(res, "insured.country") == ["DE"]


def test_ext_17_two_line_address_still_joined():
    res = _extract(
        ("Versicherte Person", 56, 180),
        ("Anschrift", 56, 200),
        ("Beispielweg 7", 200, 200),
        ("12345 Musterstadt", 200, 218),
    )
    assert _norm(res, "insured.street") == ["Beispielweg"]
    assert _norm(res, "insured.city") == ["Musterstadt"]


# ---------------------------------------------------------------------------
# PDF-3 / LEGAL-3: Monatsfelder lehnen ein Tagesdatum ab
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["31.12.2026", "15.02.2027", "15.03.2027", "2027-03-15"])
def test_pdf_3_legal_3_month_rejects_full_date_with_other_day(raw):
    for key in ("order.takeover_effective_month", "order.premium_start_month"):
        for strict in (False, True):
            assert normalize_value(_f(key), raw, strict=strict) == (None, NOTE_MONTH_DAY)
    assert normalize_month(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("01.12.2026", "2026-12"), ("12.2026", "2026-12"), ("März 2027", "2027-03"), ("2027-03", "2027-03")],
)
def test_pdf_3_legal_3_month_accepts_month_or_first_of_month(raw, expected):
    assert normalize_value(_f("order.takeover_effective_month"), raw, strict=True) == (expected, None)


# ---------------------------------------------------------------------------
# PDF-6: strenge Betragseingabe, kaufmännische Rundung
# ---------------------------------------------------------------------------

AMOUNT_KEY = "order.reduced_premium_amount"


@pytest.mark.parametrize(
    "raw",
    ["12,345", "1234,565", "1.234,567", "1e3", "100 bis 200", "2O0,00", "5O,00", "1.5OO,00", "12,50 CHF"],
)
def test_pdf_6_strict_amount_rejects_extra_decimals_and_trailing_text(raw):
    assert normalize_value(_f(AMOUNT_KEY), raw, strict=True) == (None, NOTE_AMOUNT_STRICT)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,35", "12.35"),
        ("1.234,56 €", "1234.56"),
        ("EUR 1.234,56", "1234.56"),
        ("150,-", "150.00"),
        ("1234.5", "1234.50"),
    ],
)
def test_pdf_6_strict_amount_accepts_plain_amounts(raw, expected):
    assert normalize_value(_f(AMOUNT_KEY), raw, strict=True) == (expected, None)


def test_pdf_6_rounding_is_half_up():
    assert normalize_amount("1.234,565 EUR")[0] == "1234.57"
    assert normalize_amount("0,125 EUR")[0] is None  # einzelnes Komma + 3 Stellen: mehrdeutig
    assert normalize_amount("1.000,125")[0] == "1000.13"


def test_pdf_6_parse_amount_is_not_first_token():
    assert parse_amount("100 bis 200") is None


# ---------------------------------------------------------------------------
# PDF-7 / SVC-12 / SVC-18: NFC und strenger Modus
# ---------------------------------------------------------------------------


def test_pdf_7_values_are_nfc_normalized():
    nfd = unicodedata.normalize("NFD", "Müller")
    assert nfd != "Müller"
    for strict in (False, True):
        assert normalize_value(_f("insured.last_name"), nfd, strict=strict)[0] == "Müller"
    assert split_person_name(unicodedata.normalize("NFD", "Müller, Jürgen")).last == "Müller"


def test_pdf_7_extracted_candidate_value_is_nfc():
    res = _extract((unicodedata.normalize("NFD", "Versicherte Person: Jürgen Müller"), 56, 200))
    assert [c.value for c in res.candidates["insured.last_name"]] == ["Müller"]


def test_strict_mode_keeps_user_words_that_look_like_placeholders():
    assert normalize_value(_f("insured.last_name"), "Na", strict=True) == ("Na", None)
    assert normalize_value(_f("insured.last_name"), "Na") == (None, None)
    assert normalize_value(_f("contract.changes"), "k.A.", strict=True) == ("k.A.", None)
    assert normalize_value(_f("insured.last_name"), "   ", strict=True) == (None, None)


def test_strict_mode_text_keeps_line_breaks():
    raw = "Erste  Zeile\r\n  zweite   Zeile \n"
    assert normalize_value(_f("contract.changes"), raw, strict=True) == ("Erste Zeile\nzweite Zeile", None)
    assert normalize_value(_f("contract.changes"), raw) == ("Erste Zeile zweite Zeile", None)
