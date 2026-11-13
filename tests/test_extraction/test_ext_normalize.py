from decimal import Decimal

import pytest

from insurance_app.core.fields import FIELD_REGISTRY
from insurance_app.extraction.normalize import (
    NOTE_NAME_TWO_TOKENS,
    NOTE_UNREADABLE,
    compact_key,
    frequency_from_text,
    is_placeholder,
    normalize_amount,
    normalize_bool,
    normalize_contract_number,
    normalize_country,
    normalize_currency,
    normalize_date,
    normalize_frequency,
    normalize_iban,
    normalize_integer,
    normalize_month,
    normalize_postal_code,
    normalize_value,
    parse_amount,
    split_person_name,
    split_postal_city,
    split_street_house,
)
from insurance_app.validation.validators import iban_is_valid


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("01.02.2022", "2022-02-01"),
        ("1.2.22", "2022-02-01"),
        ("13. März 2025", "2025-03-13"),
        ("12.08.1988", "1988-08-12"),
        ("2019-01-01", "2019-01-01"),
        ("01.01.2019 (MUSTER)", "2019-01-01"),
        ("31.02.2024", None),
        ("Unbekannt", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("03.2025", "2025-03"),
        ("März 2025", "2025-03"),
        ("2025-03", "2025-03"),
        ("01.07.2024", "2024-07"),
        ("13.2025", None),
        ("k.A.", None),
    ],
)
def test_normalize_month(raw, expected):
    assert normalize_month(raw) == expected


@pytest.mark.parametrize(
    ("raw", "amount", "currency"),
    [
        ("1.234,56 EUR", "1234.56", "EUR"),
        ("1234,56 €", "1234.56", "EUR"),
        ("EUR 1.234,56", "1234.56", "EUR"),
        ("150,00", "150.00", None),
        ("1.234", "1234", None),
        ("1.170,52 EUR", "1170.52", "EUR"),
        ("150,- €", "150.00", "EUR"),
        ("100 Euro", "100", "EUR"),
        ("1,234.56", "1234.56", None),
        ("1234.56", "1234.56", None),
        ("1.234.567,89 EUR", "1234567.89", "EUR"),
        ("ab 01.07.2024 100,00 EUR", "100.00", "EUR"),
        ("-12,50 EUR", "-12.50", "EUR"),
        ("CHF 99,90", "99.90", "CHF"),
        ("1 234,56 €", "1234.56", "EUR"),
        ("Beitrag – 150,00 EUR", "150.00", "EUR"),  # Gedankenstrich ist kein Minus
        ("−12,50", "-12.50", None),
    ],
)
def test_parse_amount(raw, amount, currency):
    parsed = parse_amount(raw)
    assert parsed is not None
    assert parsed[0] == Decimal(amount)
    assert parsed[1] == currency


@pytest.mark.parametrize("raw", ["keine Angabe", "abc", "01.07.2024", "", None, "Unbekannt"])
def test_parse_amount_rejects(raw):
    assert parse_amount(raw) is None


def test_normalize_amount():
    assert normalize_amount("1.234,56 EUR") == ("1234.56", "EUR")
    assert normalize_amount("1.234") == ("1234.00", None)
    assert normalize_amount("150,-") == ("150.00", None)
    assert normalize_amount("nichts") == (None, None)


def test_currency():
    assert normalize_currency("€") == "EUR"
    assert normalize_currency("Euro") == "EUR"
    assert normalize_currency("usd") == "USD"
    assert normalize_currency("Taler") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DE89 3704 0044 0532 0130 00", "DE89370400440532013000"),
        ("IBAN: DE89370400440532013000 BIC COBADEFFXXX", "DE89370400440532013000"),
        ("de89 3704 0044 0532 0130 00", "DE89370400440532013000"),
        ("Beispielbank, IBAN DE89 3704 0044 0532 0130 00", "DE89370400440532013000"),
        ("keine", None),
        ("DE89 3704", None),
    ],
)
def test_normalize_iban(raw, expected):
    assert normalize_iban(raw) == expected


def test_iban_valid_and_invalid():
    assert iban_is_valid("DE89370400440532013000")
    assert not iban_is_valid("DE89370400440532013001")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345", "12345"),
        ("D-12345", "12345"),
        ("12345 Musterstadt", "12345"),
        ("1010", "1010"),
        ("abc", None),
    ],
)
def test_normalize_postal_code(raw, expected):
    assert normalize_postal_code(raw) == expected


@pytest.mark.parametrize(
    ("raw", "street", "house"),
    [
        ("Große Straße 12a", "Große Straße", "12a"),
        ("Musterweg 3-5", "Musterweg", "3-5"),
        ("Musterweg 3 - 5", "Musterweg", "3-5"),
        ("Am Markt 1 b", "Am Markt", "1 b"),
        ("Straße des 17. Juni 5", "Straße des 17. Juni", "5"),
        ("Musterstr.12", "Musterstr.", "12"),
        ("Neue Straße 3", "Neue Straße", "3"),
        ("Postfach", "Postfach", None),
    ],
)
def test_split_street_house(raw, street, house):
    assert split_street_house(raw) == (street, house)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345 Musterstadt", ("12345", "Musterstadt")),
        ("D-12345 Musterstadt", ("12345", "Musterstadt")),
        ("1010 Wien", ("1010", "Wien")),
        ("Musterstadt", (None, "Musterstadt")),
        ("12345", ("12345", None)),
        ("", (None, None)),
    ],
)
def test_split_postal_city(raw, expected):
    assert split_postal_city(raw) == expected


def test_split_name_comma_form():
    ns = split_person_name("Musterfrau, Erika")
    assert (ns.last, ns.first, ns.is_company) == ("Musterfrau", "Erika", False)
    assert ns.confidence == pytest.approx(0.9)
    assert ns.note is None


def test_split_name_two_tokens():
    ns = split_person_name("Erika Musterfrau")
    assert (ns.last, ns.first) == ("Musterfrau", "Erika")
    assert ns.confidence == pytest.approx(0.6)
    assert ns.note == NOTE_NAME_TWO_TOKENS == "Vor- und Nachname wurden automatisch getrennt – bitte prüfen."


def test_split_name_salutation_and_title():
    ns = split_person_name("Herr Dr. Max Beispiel")
    assert ns.salutation == "Herr Dr."
    assert (ns.first, ns.last) == ("Max", "Beispiel")
    assert split_person_name("Frau Erika Musterfrau").salutation == "Frau"


def test_split_name_strips_ocr_trailing_dot():
    ns = split_person_name("Erika Musterfrau.")
    assert (ns.first, ns.last) == ("Erika", "Musterfrau")


def test_split_name_many_tokens():
    ns = split_person_name("Anna Maria Musterfrau")
    assert (ns.first, ns.last) == ("Anna Maria", "Musterfrau")
    assert ns.confidence == pytest.approx(0.4)
    assert ns.note
    ns = split_person_name("Karl Otto von Beispiel")
    assert (ns.first, ns.last) == ("Karl Otto", "von Beispiel")


@pytest.mark.parametrize(
    ("raw", "legal"),
    [
        ("Muster Bäckerei GmbH", "GmbH"),
        ("Beispiel GmbH & Co. KG", "GmbH & Co. KG"),
        ("Muster AG", "AG"),
        ("Beispiel SE", "SE"),
        ("Muster e.K.", "e.K."),
        ("Sportverein Muster e.V.", "e.V."),
        ("Muster OHG", "OHG"),
        ("Muster GbR", "GbR"),
        ("Muster UG (haftungsbeschränkt)", "UG (haftungsbeschränkt)"),
        ("Muster Gesellschaft mbH", "mbH"),
    ],
)
def test_split_name_company(raw, legal):
    ns = split_person_name(raw)
    assert ns.is_company
    assert ns.last == raw
    assert ns.first is None
    assert ns.legal_form == legal


def test_split_name_not_company_for_lookalikes():
    assert not split_person_name("Sebastian Ag").is_company
    assert not split_person_name("AG Muster").is_company  # Rechtsform nie am Anfang


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("monatlich", "monthly"),
        ("mtl.", "monthly"),
        ("jährlich", "annual"),
        ("jährl.", "annual"),
        ("halbjährlich", "semiannual"),
        ("vierteljährlich", "quarterly"),
        ("Einmalbeitrag", "single"),
        ("Zahlweise: monatlich", "monthly"),
        ("annual", "annual"),
        ("irgendwann", None),
    ],
)
def test_normalize_frequency(raw, expected):
    assert normalize_frequency(raw) == expected


def test_frequency_from_label():
    assert frequency_from_text("Monatsbeitrag") == "monthly"
    assert frequency_from_text("Jahresbeitrag") == "annual"
    assert frequency_from_text("Beitrag (monatlich)") == "monthly"
    assert frequency_from_text("Beitrag") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" 963 0030-1 ", "9630030-1"),
        ("9630030-1", "9630030-1"),
        ("lv 123456", "LV123456"),
        ("8815201-7 vom 01.01.2019", "8815201-7"),
        ("Nr. 12/345.6", "12/345.6"),
        ("Unbekannt", None),
        ("abc", None),
    ],
)
def test_normalize_contract_number(raw, expected):
    assert normalize_contract_number(raw) == expected


@pytest.mark.parametrize(
    "raw", ["Unbekannt", "-", "–", "k.A.", "k. A.", "keine Angabe", "n/a", "nicht vorhanden", "", "  "]
)
def test_placeholders(raw):
    assert is_placeholder(raw)


@pytest.mark.parametrize("raw", ["Max", "0", "Ruhend", "150,00 EUR"])
def test_not_placeholders(raw):
    assert not is_placeholder(raw)


def test_misc_normalizers():
    assert normalize_integer("67 Jahre") == "67"
    assert normalize_integer("Jahre") is None
    assert normalize_bool("ja") == "true"
    assert normalize_bool("Nein") == "false"
    assert normalize_bool("x") == "true"
    assert normalize_bool("vielleicht") is None
    assert normalize_country("Deutschland") == "DE"
    assert normalize_country("Österreich") == "AT"
    assert normalize_country("Atlantis") == "Atlantis"
    assert compact_key("Vers.-Nr.:") == "versnr"


def test_normalize_value_dispatch():
    f = FIELD_REGISTRY
    assert normalize_value(f["contract.start_date"], "01.01.2019") == ("2019-01-01", None)
    assert normalize_value(f["premium.amount"], "150,00 EUR") == ("150.00", None)
    assert normalize_value(f["premium.amount"], "CHF 99,90") == ("99.90", "Währung laut Quelle: CHF")
    assert normalize_value(f["bank.iban"], "DE89 3704 0044 0532 0130 00") == ("DE89370400440532013000", None)
    assert normalize_value(f["premium.frequency"], "monatlich") == ("monthly", None)
    assert normalize_value(f["order.continuation_mode"], "private") == ("private", None)
    assert normalize_value(f["order.severance_consent"], "ja") == ("true", None)
    assert normalize_value(f["contract.end_age"], "67 Jahre") == ("67", None)
    assert normalize_value(f["contract.tariff"], "DV-Klassik (MUSTER).") == ("DV-Klassik (MUSTER)", None)
    assert normalize_value(f["contract.number"], " 963 0030-1") == ("9630030-1", None)
    assert normalize_value(f["insured.postal_code"], "D-12345") == ("12345", None)
    assert normalize_value(f["contract.start_date"], "demnächst") == (None, NOTE_UNREADABLE)
    assert normalize_value(f["contract.start_date"], "Unbekannt") == (None, None)
