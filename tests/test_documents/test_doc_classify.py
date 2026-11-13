"""Dokumentklassifikation (documents.classify) mit synthetischen Texten."""

from __future__ import annotations

from datetime import date

import pytest

from insurance_app.core.models import DocumentText, DocumentType, ExtractionMethod, PageText
from insurance_app.documents.classify import classify_document

MUSTER = "MUSTER – KEINE ECHTDATEN"


def doc(*pages: str) -> DocumentText:
    return DocumentText(
        document_id="D-MUSTER",
        pages=[
            PageText(page_number=i + 1, width=595, height=842, method=ExtractionMethod.PDF_TEXT, text=t)
            for i, t in enumerate(pages)
        ],
    )


POLICY = f"""{MUSTER}
Muster Lebensversicherung AG
Max Mustermann
Musterweg 1
12345 Musterstadt
Musterstadt, 15.01.2022
Versicherungsschein
Versicherungsnummer: 4711-0815
Versicherungsbeginn: 01.02.2022
Tarif: MUSTER-RENTE
"""

AMENDMENT = f"""{MUSTER}
Muster Lebensversicherung AG
Musterstadt, 13. März 2025
Nachtrag zum Versicherungsschein
Versicherungsschein-Nr. 4711-0815
Die Vertragsänderung ist gültig ab 01.04.2025.
Geboren am 01.01.1970
"""

PREMIUM = f"""{MUSTER}
Datum: 01.12.2024
Beitragsmitteilung
Vertragsnummer 4711-0815
Ihr neuer Beitrag ab dem 01.01.2025 beträgt 123,45 EUR monatlich.
"""

STATUS = f"""{MUSTER}
Jährliche Information zu Ihrer Versicherung
Stand: 31.12.2024
Vers.-Nr. 4711-0815
Vertragsguthaben 12.345,67 EUR
Rückkaufswert 11.000,00 EUR
"""

CANCELLATION = f"""{MUSTER}
Musterstadt, 05.06.2025
Kündigungsbestätigung
hiermit bestätigen wir die Kündigung Ihres Vertrags Vertrag Nr. 4711-0815 zum 30.06.2025.
"""

PORTAL = f"""{MUSTER}
Vertragsdetails
Vertragsinhaber Max Mustermann
Vertragsnummer 4711-0815
Rückkaufswert 9.876,54 EUR
Abschlussvermittler Muster Makler GmbH
Letzte Aktualisierung 02.03.2025
"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (POLICY, DocumentType.POLICY),
        (AMENDMENT, DocumentType.AMENDMENT),
        (PREMIUM, DocumentType.PREMIUM_NOTICE),
        (STATUS, DocumentType.STATUS_REPORT),
        (CANCELLATION, DocumentType.CANCELLATION_CONFIRMATION),
        (PORTAL, DocumentType.PORTAL_OVERVIEW),
    ],
)
def test_each_type(text: str, expected: DocumentType):
    result = classify_document(doc(text))
    assert result.doc_type == expected
    assert not result.uncertain
    assert 0.5 < result.confidence <= 1.0
    assert result.matched_keywords


def test_umlaut_and_case_robust():
    result = classify_document(doc(f"{MUSTER}\nVERTRAGSAENDERUNG\nNACHTRAG\n"))
    assert result.doc_type == DocumentType.AMENDMENT
    assert "Vertragsänderung" in result.matched_keywords


def test_amendment_not_confused_with_policy():
    result = classify_document(doc(AMENDMENT))
    assert "Versicherungsschein" not in result.matched_keywords
    assert "Nachtrag zum Versicherungsschein" in result.matched_keywords


def test_contract_number_label_is_not_policy_cue():
    result = classify_document(doc(f"{MUSTER}\nBeitragsrechnung\nVersicherungsschein-Nr. 4711-0815\n"))
    assert result.doc_type == DocumentType.PREMIUM_NOTICE


def test_ambiguous_is_uncertain():
    result = classify_document(doc(f"{MUSTER}\nNachtrag\nBeitragsmitteilung\n"))
    assert result.uncertain
    assert result.doc_type in (DocumentType.AMENDMENT, DocumentType.PREMIUM_NOTICE)
    assert any("Zweitbeste" in n for n in result.notes)
    assert result.confidence < 0.6


def test_weak_cue_only_is_unknown():
    result = classify_document(doc(f"{MUSTER}\nSehr geehrte Damen und Herren, eine Änderung.\n"))
    assert result.doc_type == DocumentType.UNKNOWN
    assert result.uncertain
    assert result.confidence == 0.0


def test_empty_text():
    result = classify_document(doc(""))
    assert result.doc_type == DocumentType.UNKNOWN
    assert result.uncertain
    assert result.notes


def test_title_area_weighs_more():
    filler = "\n".join(f"Zeile {i} MUSTER Fließtext ohne Signalwort" for i in range(40))
    text = f"Beitragsmitteilung\n{filler}\nIn Ihrer Police finden Sie Details.\n"
    assert classify_document(doc(text)).doc_type == DocumentType.PREMIUM_NOTICE


def test_letter_date_long_format():
    result = classify_document(doc(AMENDMENT))
    assert result.document_date == date(2025, 3, 13)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Datum: 01.03.2025\nVersicherungsbeginn: 01.02.2020", date(2025, 3, 1)),
        ("Hamburg, 01.03.2025\nIhr Schreiben vom 15.02.2025", date(2025, 3, 1)),
        ("Geboren am 01.01.1970\nMusterstadt, den 02.03.2025", date(2025, 3, 2)),
        ("13. März 2025\nNachtrag", date(2025, 3, 13)),
        ("Versicherungsbeginn: 01.02.2020\nAblauf: 01.02.2060", None),
    ],
)
def test_document_date(text: str, expected: date | None):
    assert classify_document(doc(f"{MUSTER}\n{text}")).document_date == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Die Änderung ist gültig ab 01.04.2025.", date(2025, 4, 1)),
        ("Wirksam ab 1. Juli 2025", date(2025, 7, 1)),
        ("mit Wirkung zum 01.10.2025 erhöht sich", date(2025, 10, 1)),
        ("Änderung zum 01.05.2025", date(2025, 5, 1)),
        ("ab dem 01.01.2026 gilt", date(2026, 1, 1)),
        ("gueltig ab dem 15.08.2025", date(2025, 8, 15)),
        ("Kein Wirksamkeitsdatum. Datum: 01.03.2025", None),
    ],
)
def test_effective_date(text: str, expected: date | None):
    assert classify_document(doc(f"{MUSTER}\n{text}")).effective_date == expected


def test_effective_date_from_amendment():
    assert classify_document(doc(AMENDMENT)).effective_date == date(2025, 4, 1)


def test_contract_numbers():
    text = (
        f"{MUSTER}\n"
        "Versicherungsnummer: 4711-0815\n"
        "Vertrags-Nr. AB 123456\n"
        "Vers.-Nr. 99/88-77\n"
        "Versicherungsschein-Nr.: 4711-0815\n"
        "Vertragsnummer\n5555 66 777\n"
        "Versicherung Nr. LV-0042\n"
        "Vertragsnummer bitte stets angeben\n"
        "Vertragsnummer 1234567 vom 01.03.2025\n"
    )
    result = classify_document(doc(text))
    assert result.contract_numbers == [
        "4711-0815",
        "AB 123456",
        "99/88-77",
        "5555 66 777",
        "LV-0042",
        "1234567",
    ]


def test_social_and_health_insurance_numbers_are_not_contract_numbers():
    text = (
        f"{MUSTER}\n"
        "Rentenversicherungsnummer: 12 150170 M 999\n"
        "Krankenversicherungsnummer A123456789\n"
        "Sozialversicherungsnummer 12345678\n"
        "Vertragsnummer: 4711-0815\n"
    )
    assert classify_document(doc(text)).contract_numbers == ["4711-0815"]


def test_contract_number_without_date_tail():
    result = classify_document(doc(f"{MUSTER}\nVersicherungsnummer: 4711-0815 01.03.2025"))
    assert result.contract_numbers == ["4711-0815"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Swiss Life AG, Niederlassung für Deutschland", "Swiss Life"),
        ("SwissLife Kundenservice", "Swiss Life"),
        ("Continentale Lebensversicherung AG", "Continentale"),
        ("R+V Lebensversicherung AG", "R+V"),
        ("Nürnberger Lebensversicherung AG", "Nürnberger"),
        ("Hannoversche Lebensversicherung AG", "Hannoversche"),
        ("MUSTER Lebensversicherung AG", "MUSTER Lebensversicherung AG"),
        ("Musterstadt, Zürich und Dialog mit Ihnen", None),
    ],
)
def test_insurer_hint(text: str, expected: str | None):
    assert classify_document(doc(f"{MUSTER}\n{text}")).insurer_hint == expected


def test_multi_page_later_pages_weigh_less():
    page1 = f"{MUSTER}\nStandmitteilung\nVertragsguthaben 1.000,00 EUR"
    page2 = "Anlage: Auszug aus dem Versicherungsschein"
    result = classify_document(doc(page1, page2))
    assert result.doc_type == DocumentType.STATUS_REPORT


def test_status_report_tolerates_ocr_l_for_i():
    """Fremd-OCR liest „Information“ oft als „lnformation“ (kleines L)."""
    from insurance_app.core.models import DocumentText, ExtractionMethod, PageText

    text = "Jährliche lnformation zur Entwicklung lhrer Versicherung Nr. 8815201-7\nSehr geehrte Damen und Herren,"
    doc = DocumentText(
        document_id="D-OCR",
        pages=[PageText(page_number=1, width=595, height=842, method=ExtractionMethod.PDF_TEXT, text=text)],
    )
    assert classify_document(doc).doc_type.value == "STATUS_REPORT"


# EXT-14: Gültigkeitsdatum eines Nachtrags – ausdrückliche Angaben haben Vorrang
def test_ext_14_explicit_trigger_wins_over_earlier_ab_dem():
    text = (
        "Nachtrag zum Versicherungsschein\n"
        "Die Beiträge sind ab dem 01.02.2022 monatlich fällig.\n"
        "Die Änderung ist gültig ab 01.07.2025.\n"
    )
    assert classify_document(doc(f"{MUSTER}\n{text}")).effective_date == date(2025, 7, 1)


def test_ext_14_explicit_trigger_on_later_page_wins():
    first = f"{MUSTER}\nNachtrag zum Versicherungsschein\nDie Beiträge sind ab dem 01.02.2022 fällig."
    second = f"{MUSTER}\nDie Änderung ist gültig ab 01.07.2025."
    assert classify_document(doc(first, second)).effective_date == date(2025, 7, 1)


def test_ext_14_due_date_sentence_is_no_effective_date():
    text = "Nachtrag\nDie Beiträge sind ab dem 01.02.2022 monatlich fällig."
    assert classify_document(doc(f"{MUSTER}\n{text}")).effective_date is None


def test_ext_14_new_premium_ab_dem_still_counts():
    text = "Ihr neuer Beitrag ab dem 01.01.2025 beträgt 123,45 EUR. Er ist monatlich fällig."
    assert classify_document(doc(f"{MUSTER}\n{text}")).effective_date == date(2025, 1, 1)


def test_ext_14_contradicting_explicit_dates_give_no_hint():
    text = "Nachtrag\nDie Änderung ist gültig ab 01.07.2025.\nwirksam ab 01.01.2026"
    assert classify_document(doc(f"{MUSTER}\n{text}")).effective_date is None
