import math

import pytest
from extraction_helpers import make_word, page_from_words, words_for_line

from insurance_app.core.models import BoundingBox, Word
from insurance_app.extraction.layout import estimate_skew, find_label_values, group_lines
from insurance_app.extraction.rules import LABELS, SECTION_LABELS, STOP_LABELS


def _find(page):
    return find_label_values(page, LABELS, SECTION_LABELS, stop_labels=STOP_LABELS)


def _by_key(matches):
    out = {}
    for m in matches:
        out.setdefault(m.field_key, m)
    return out


def test_group_lines_tolerates_baseline_offset():
    words = words_for_line("Versicherungsbeginn", 71, 200) + words_for_line("01.01.2019", 312, 198)
    words += words_for_line("Geplanter Rentenbeginn", 71, 218) + words_for_line("01.01.2053", 312, 216)
    lines = group_lines(words)
    assert [ln.text for ln in lines] == [
        "Versicherungsbeginn 01.01.2019",
        "Geplanter Rentenbeginn 01.01.2053",
    ]


def test_group_lines_ignores_tall_rotated_words():
    words = words_for_line("Beitrag 150,00 EUR", 56, 300)
    words.append(Word(text="MUSTER", bbox=BoundingBox(x0=60, y0=250, x1=260, y1=420)))
    lines = group_lines(words)
    assert any(ln.text == "Beitrag 150,00 EUR" for ln in lines)


def test_two_column_row_splits_at_next_label():
    words = (
        words_for_line("Produktbezeichnung", 20, 60)
        + words_for_line("Basisrente Invest (MUSTER)", 170, 60)
        + words_for_line("Unternehmen", 470, 60)
        + words_for_line("Beispiel Lebensversicherung AG", 620, 60)
    )
    page = page_from_words(words, width=900, height=350)
    found = _by_key(_find(page))
    assert found["contract.product"].value_text == "Basisrente Invest (MUSTER)"
    assert found["insurer.name"].value_text == "Beispiel Lebensversicherung AG"
    assert found["contract.product"].rule_id == "layout_right"


def test_value_stops_at_known_label_even_with_small_column_gap():
    # Spaltenabstand nur ~4 % der Breite: das nächste Label beendet den Wert
    words = (
        words_for_line("Status", 20, 60)
        + words_for_line("Ruhend", 120, 60)
        + words_for_line("Vertragsnummer", 190, 60)
        + words_for_line("5550123-4", 290, 60)
    )
    found = _by_key(_find(page_from_words(words, width=900, height=350)))
    assert found["contract.status"].value_text == "Ruhend"
    assert found["contract.number"].value_text == "5550123-4"


def test_value_stops_at_large_gap():
    words = words_for_line("Tarif", 56, 100) + words_for_line("DV-Klassik", 230, 100)
    words += words_for_line("Seite 1", 520, 100)
    found = _by_key(_find(page_from_words(words)))
    assert found["contract.tariff"].value_text == "DV-Klassik"


def test_labels_first_then_values_with_baseline_offset():
    labels = [
        "Versicherungsnehmer",
        "Versicherungsbeginn",
        "Letzte Beitragsfälligkeit",
        "Geplanter Rentenbeginn",
    ]
    values = ["Muster Bäckerei GmbH", "01.01.2019", "01.12.2052", "01.01.2053"]
    words: list[Word] = []
    for i, label in enumerate(labels):  # Textreihenfolge: erst alle Labels …
        words += words_for_line(label, 71, 160 + 18 * i)
    for i, value in enumerate(values):  # … dann alle Werte, 2 pt höher
        words += words_for_line(value, 312, 158 + 18 * i)
    found = _by_key(_find(page_from_words(words)))
    assert found["policyholder"].value_text == "Muster Bäckerei GmbH"
    assert found["contract.start_date"].value_text == "01.01.2019"
    assert found["contract.premium_end_date"].value_text == "01.12.2052"
    assert found["contract.pension_start_date"].value_text == "01.01.2053"
    assert "contract.end_date" not in found


def test_label_above_value_with_letter_window_left():
    words = words_for_line("Beispiel Finanz AG", 56, 160) + words_for_line("Versicherung Nr.", 360, 160)
    words += words_for_line("Maklerweg 9", 56, 172) + words_for_line("9630030-1", 360, 172)
    found = _by_key(_find(page_from_words(words)))
    m = found["contract.number"]
    assert m.value_text == "9630030-1"
    assert m.rule_id == "layout_below"
    assert m.confidence < 0.85


def test_punctuation_noise_in_labels():
    for label in ["Versicherung Nr", "Versicherung Nr.:", "Vers. -Nr.", "Versicherungs-Nr:"]:
        words = words_for_line(label, 56, 100) + words_for_line("8815201-7", 230, 100)
        found = _by_key(_find(page_from_words(words)))
        assert found["contract.number"].value_text == "8815201-7", label


def test_ocr_letter_error_in_long_label_is_tolerated():
    words = words_for_line("Versicherungsbeginm", 56, 100) + words_for_line("01.01.2019", 230, 100)
    found = _by_key(_find(page_from_words(words)))
    assert found["contract.start_date"].value_text == "01.01.2019"
    assert found["contract.start_date"].confidence < 0.85


def test_placeholder_unbekannt():
    words = words_for_line("Vertragsstatus", 20, 60) + words_for_line("Unbekannt", 200, 60)
    m = _by_key(_find(page_from_words(words)))["contract.status"]
    assert m.placeholder
    assert m.value_text == ""
    assert m.placeholder_text == "Unbekannt"


def test_empty_value_before_next_label_is_placeholder():
    words = words_for_line("Beitrag", 20, 160) + words_for_line("Abschlussvermittler", 470, 160)
    words += words_for_line("Beispiel Finanz AG", 620, 160)
    found = _by_key(_find(page_from_words(words, width=900, height=350)))
    assert found["premium.amount"].placeholder
    assert found["premium.amount"].placeholder_text == ""
    assert found["contract.intermediary"].value_text == "Beispiel Finanz AG"


def test_stop_label_bounds_value():
    words = words_for_line("Status", 20, 60) + words_for_line("Ruhend", 200, 60)
    words += words_for_line("Rückkaufswert vom", 470, 60) + words_for_line("Unbekannt", 620, 60)
    found = _by_key(_find(page_from_words(words, width=900, height=350)))
    assert found["contract.status"].value_text == "Ruhend"
    assert set(found) == {"contract.status"}


def test_section_heading_maps_generic_name_to_insured():
    words = words_for_line("Versicherte Person", 24, 300, h=13)
    words += words_for_line("Name", 24, 330) + words_for_line("Max Beispiel", 170, 330)
    words += words_for_line("Rente (monatlich)", 470, 330) + words_for_line("Unbekannt", 620, 330)
    found = _by_key(_find(page_from_words(words, width=900, height=350)))
    assert found["insured"].value_text == "Max Beispiel"
    assert found["insured"].section == "Versicherte Person"
    assert "[Versicherte Person]" in found["insured"].rule_id


def test_generic_name_outside_section_is_ignored():
    words = words_for_line("Name", 24, 330) + words_for_line("Max Beispiel", 170, 330)
    assert _find(page_from_words(words)) == []


def test_heading_term_with_value_is_a_label():
    words = words_for_line("Versicherte Person", 71, 160) + words_for_line("Erika Musterfrau", 312, 160)
    words += words_for_line("Name", 71, 178) + words_for_line("Irgendwer", 312, 178)
    found = _by_key(_find(page_from_words(words)))
    assert found["insured"].value_text == "Erika Musterfrau"
    assert found["insured"].section is None
    # kein Abschnitt geöffnet -> "Name" bleibt unzugeordnet
    assert len([m for m in _find(page_from_words(words)) if m.field_key == "insured"]) == 1


def test_generic_label_suppressed_in_foreign_section():
    words = words_for_line("Versicherungsnehmer", 56, 300)
    words += words_for_line("Name", 56, 318) + words_for_line("Muster GmbH", 230, 318)
    words += words_for_line("Geburtsdatum", 56, 336) + words_for_line("01.01.1970", 230, 336)
    found = _by_key(_find(page_from_words(words)))
    assert found["policyholder"].value_text == "Muster GmbH"
    assert "insured.birth_date" not in found


def test_address_continuation_line():
    words = words_for_line("Versicherte Person", 56, 300)
    words += words_for_line("Anschrift", 56, 318) + words_for_line("Beispielweg 7", 230, 318)
    words += words_for_line("12345 Musterstadt", 230, 335)
    words += words_for_line("Geburtsdatum", 56, 352) + words_for_line("12.08.1988", 230, 352)
    found = _by_key(_find(page_from_words(words)))
    assert found["insured.address"].value_text == "Beispielweg 7"
    assert found["insured.address"].continuation_text == "12345 Musterstadt"
    assert found["insured.birth_date"].continuation_text is None


def test_running_text_label_is_not_matched_mid_sentence():
    words = words_for_line("Ihr Beitrag beträgt monatlich 150,00 EUR", 56, 400)
    assert _find(page_from_words(words)) == []


def test_running_text_label_at_line_start_is_marked_tight():
    words = words_for_line("Versicherungsnehmer kann die Versicherung kündigen", 56, 400)
    matches = _find(page_from_words(words))
    assert matches and matches[0].tight and matches[0].rule_id == "layout_inline"


def test_colon_label_mid_line():
    words = words_for_line("Name: Max Beispiel Geburtsdatum: 01.01.1980", 56, 400)
    found = _by_key(_find(page_from_words(words)))
    assert found["insured.birth_date"].value_text == "01.01.1980"


@pytest.mark.parametrize("angle", [-1.5, 1.5])
def test_skewed_page_rows_are_matched(angle: float):
    slope = math.tan(math.radians(angle))
    rows = [
        ("Versicherungsnummer", "8815201-7"),
        ("Versicherungsbeginn", "01.01.2019"),
        ("Ablauf der Versicherung", "01.01.2053"),
        ("Beitrag", "150,00 EUR"),
        ("Zahlweise", "monatlich"),
        ("Tarif", "DV-Klassik"),
    ]
    words: list[Word] = []
    for i, (label, value) in enumerate(rows):
        y = 160 + 17 * i
        for text, x in ((label, 56.0), (value, 230.0)):
            for w in words_for_line(text, x, y):
                dy = slope * w.bbox.x0
                words.append(
                    make_word(w.text, w.bbox.x0, w.bbox.y0 + dy).model_copy(
                        update={
                            "bbox": BoundingBox(
                                x0=w.bbox.x0, y0=w.bbox.y0 + dy, x1=w.bbox.x1, y1=w.bbox.y1 + dy
                            )
                        }
                    )
                )
    # zusätzlicher Fließtext liefert genug Wortpaare für die Schätzung
    for i in range(6):
        for w in words_for_line("Dies ist ein Absatz mit mehreren Wörtern zum Test", 56, 400 + 15 * i):
            dy = slope * w.bbox.x0
            words.append(
                w.model_copy(
                    update={
                        "bbox": BoundingBox(x0=w.bbox.x0, y0=w.bbox.y0 + dy, x1=w.bbox.x1, y1=w.bbox.y1 + dy)
                    }
                )
            )
    assert estimate_skew(words) == pytest.approx(slope, abs=0.004)
    found = _by_key(_find(page_from_words(words)))
    for label, value in rows:
        key = {
            "Versicherungsnummer": "contract.number",
            "Versicherungsbeginn": "contract.start_date",
            "Ablauf der Versicherung": "contract.end_date",
            "Beitrag": "premium.amount",
            "Zahlweise": "premium.frequency",
            "Tarif": "contract.tariff",
        }[label]
        assert found[key].value_text == value, label


def test_straight_page_has_no_skew():
    words = []
    for i in range(10):
        words += words_for_line("Ein gerader Absatz mit Wörtern", 56, 100 + 15 * i)
    assert estimate_skew(words) == 0.0
