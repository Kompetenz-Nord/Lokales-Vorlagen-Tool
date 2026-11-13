"""Einfacher Brief: automatische Vorbefüllung aus dem Dokument, Prüfung, PDF-Erzeugung.

Alle Daten synthetisch („MUSTER – KEINE ECHTDATEN“).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pymupdf
import pytest

from insurance_app.core.address_book import AddressBook
from insurance_app.core.paths import AppPaths
from insurance_app.core.service import AppService, ExportBlockedError
from insurance_app.extraction.normalize import normalize_license_plate
from insurance_app.letters.simple import (
    SIMPLE_LETTER_TYPES,
    Reference,
    SimpleLetter,
    find_insurer_address,
    prefill_letter,
    to_plan,
    validate_letter,
)
from insurance_app.samples.kfz import KFZ_TRUTH, generate_kfz_sample

TODAY = date(2026, 11, 13)


@pytest.fixture()
def svc(tmp_path: Path):
    service = AppService(
        AppPaths.for_testing(tmp_path), guard_network=True, configure_logs=False, today=lambda: TODAY
    )
    yield service
    service.close()
    from insurance_app.security.network_guard import uninstall_network_guard

    uninstall_network_guard()


def complete_letter(**changes) -> SimpleLetter:
    base = SimpleLetter(
        letter_type="kuendigung_kfz",
        sender_name="Erika Musterfrau",
        sender_street="Beispielweg 7",
        sender_postal_city="12345 Musterstadt",
        recipient="Muster Kfz-Versicherung AG\n54321 Beispielstadt",
        references=[
            Reference(label="Kennzeichen", value="MU-ST 1234"),
            Reference(label="Versicherungsnummer", value="KFZ 0001 2345 67"),
        ],
        subject="Kündigung",
        date="13.11.2026",
        body=SIMPLE_LETTER_TYPES["kuendigung_kfz"].body(),
        signer="Erika Musterfrau",
    )
    return base.model_copy(update=changes)


# ---------------------------------------------------------------------------
# Automatische Vorbefüllung
# ---------------------------------------------------------------------------


def test_document_fills_all_letter_fields_automatically(svc: AppService, tmp_path: Path):
    """Dokument rein -> Absender, Empfänger, Kennzeichen und Versicherungsnummer stehen im Brief."""
    sample = generate_kfz_sample(tmp_path / "muster")
    case, prefill, notes = svc.letter_from_documents([sample], "kuendigung_kfz")
    letter = prefill.letter
    assert not notes
    assert letter.sender_name == KFZ_TRUTH["sender_name"]
    assert letter.sender_street == KFZ_TRUTH["sender_street"]
    assert letter.sender_postal_city == KFZ_TRUTH["sender_postal_city"]
    assert letter.recipient_lines() == KFZ_TRUTH["recipient"]
    refs = {r.label: r.value for r in letter.references}
    assert refs == {
        "Kennzeichen": KFZ_TRUTH["Kennzeichen"],
        "Versicherungsnummer": KFZ_TRUTH["Versicherungsnummer"],
    }
    assert letter.subject == "Kündigung" and letter.date == "13.11.2026"
    assert letter.signer == KFZ_TRUTH["sender_name"]
    assert "fristgerecht, ersatzweise zum nächstmöglichen Zeitpunkt" in letter.body
    assert set(prefill.auto_fields) >= {
        "sender_name",
        "sender_street",
        "sender_postal_city",
        "recipient",
        "reference:Kennzeichen",
        "reference:Versicherungsnummer",
    }
    # Ohne weitere Eingabe ist der Brief vollständig -> ein Klick genügt
    assert validate_letter(letter) == []
    _case, outcome = svc.generate_simple_letter(case, letter)
    assert outcome.path.is_file() and outcome.path.parent == svc.export_dir
    assert "Kuendigung-Kfz" in outcome.path.name
    text = pymupdf.open(outcome.path)[0].get_text()
    for expected in (
        "Erika Musterfrau",
        "Muster Kfz-Versicherung AG",
        "MU-ST 1234",
        "KFZ 0001 2345 67",
        "Kündigung",
    ):
        assert expected in text, expected
    assert "VORSCHAU" not in text
    assert svc.load_case(case.id).exports[-1].kind == "letter"
    assert svc.stored_simple_letter(svc.load_case(case.id)) == letter


@pytest.mark.slow
def test_scanned_document_fills_the_letter_too(svc: AppService, tmp_path: Path):
    """Auch ein Foto/Scan (Texterkennung) reicht für die automatische Befüllung."""
    from insurance_app.ocr.tesseract_backend import TesseractBackend

    if not TesseractBackend().is_available()[0]:
        pytest.skip("Tesseract ist nicht installiert")
    sample = generate_kfz_sample(tmp_path / "muster")
    scan = tmp_path / "MUSTER_scan.png"
    pymupdf.open(sample)[0].get_pixmap(dpi=200).save(scan)
    _case, prefill, notes = svc.letter_from_documents([scan], "kuendigung_kfz")
    letter = prefill.letter
    assert not notes
    assert (letter.sender_name, letter.sender_street, letter.sender_postal_city) == (
        KFZ_TRUTH["sender_name"],
        KFZ_TRUTH["sender_street"],
        KFZ_TRUTH["sender_postal_city"],
    )
    assert letter.recipient_lines() == KFZ_TRUTH["recipient"]
    assert {r.label: r.value for r in letter.references} == {
        "Kennzeichen": KFZ_TRUTH["Kennzeichen"],
        "Versicherungsnummer": KFZ_TRUTH["Versicherungsnummer"],
    }
    assert validate_letter(letter) == []


def test_nothing_is_invented_when_the_document_has_no_data(svc: AppService):
    prefill = svc.prefill_simple_letter(svc.create_case(), "kuendigung_kfz")
    letter = prefill.letter
    assert (letter.sender_name, letter.sender_street, letter.recipient, letter.signer) == ("", "", "", "")
    assert [r.value for r in letter.references] == ["", ""]
    assert prefill.auto_fields == []
    assert len(validate_letter(letter)) >= 5


def test_conflicting_values_are_left_empty_with_a_note(svc: AppService):
    """Widersprüchliche Angaben werden nicht geraten, sondern als Hinweis genannt."""
    case = svc.create_sample_case()  # Musterfall: Anschrift der versicherten Person im Konflikt
    conflict_keys = [k for k, fv in case.contract_fields.items() if fv.status.value == "CONFLICT"]
    assert conflict_keys
    prefill = prefill_letter(case, "kuendigung", TODAY)
    if any(k.startswith("policyholder.") for k in conflict_keys):
        assert any("unterschiedliche Werte" in n for n in prefill.notes)
    refs = {r.label: r.value for r in prefill.letter.references}
    assert refs["Versicherungsnummer"]  # eindeutig erkannt -> übernommen


def test_insurer_address_only_from_a_matching_sender_line():
    name = "Muster Kfz-Versicherung AG"
    text = "Seite 1\nMuster Kfz-Versicherung AG · Musterallee 1 · 54321 Beispielstadt\nFrau Erika Musterfrau"
    assert find_insurer_address([text], name) == [
        "Muster Kfz-Versicherung AG",
        "Musterallee 1",
        "54321 Beispielstadt",
    ]
    assert find_insurer_address(["Muster Kfz-Versicherung AG, 54321 Beispielstadt"], name) == [
        "Muster Kfz-Versicherung AG",
        "54321 Beispielstadt",
    ]
    # fremde Absenderzeile (z. B. Vermittler) oder Zeile ohne PLZ: nichts übernehmen
    assert find_insurer_address(["Beispiel Makler GmbH · Hauptweg 2 · 11111 Anderswo"], name) == []
    assert find_insurer_address(["Muster Kfz-Versicherung AG · Musterallee 1"], name) == []


def test_tightly_printed_sender_line_gets_spaces_back_and_a_note():
    """Sehr kleine Schrift wird manchmal ohne Wortabstände gelesen – nur Leerzeichen werden ergänzt."""
    from insurance_app.core.models import BoundingBox, ExtractionMethod, PageText, Word
    from insurance_app.letters.simple import _unglue, read_address_window

    assert _unglue("Musterallee1") == "Musterallee 1"
    assert _unglue("54321Beispielstadt") == "54321 Beispielstadt"
    assert _unglue("MusterKfz-VersicherungAG") == "Muster Kfz-Versicherung AG"
    assert _unglue("Muster Kfz-Versicherung AG") == "Muster Kfz-Versicherung AG"  # schon lesbar: unverändert

    def word(text: str, x: float, y: float) -> Word:
        return Word(text=text, bbox=BoundingBox(x0=x, y0=y, x1=x + 6 * len(text), y1=y + 10), confidence=0.9)

    rows = [
        (130, ["MusterKfz-VersicherungAG·Musterallee1·54321Beispielstadt"]),
        (148, ["Frau"]),
        (161, ["Erika", "Musterfrau"]),
        (175, ["Beispielweg", "7"]),
        (189, ["12345", "Musterstadt"]),
    ]
    words = []
    for y, texts in rows:
        x = 70.0
        for text in texts:
            words.append(word(text, x, y))
            x += 6 * len(text) + 5
    page = PageText(page_number=1, width=595, height=842, method=ExtractionMethod.TESSERACT, words=words)
    window = read_address_window(page)
    assert window is not None and (window.name, window.street) == ("Erika Musterfrau", "Beispielweg 7")
    assert window.sender_parts == ["Muster Kfz-Versicherung AG", "Musterallee 1", "54321 Beispielstadt"]
    assert window.sender_reconstructed


@pytest.mark.parametrize(
    ("raw", "plate"),
    [
        ("MU-ST 1234", "MU-ST 1234"),
        ("m ab 12", "M-AB 12"),
        ("B-MW 471E", "B-MW 471E"),
        ("Tarif Komfort", None),
        ("12345", None),
    ],
)
def test_license_plate_normalization(raw, plate):
    assert normalize_license_plate(raw) == plate


# ---------------------------------------------------------------------------
# Prüfung vor der Erzeugung
# ---------------------------------------------------------------------------


def test_complete_letter_has_no_open_points():
    assert validate_letter(complete_letter()) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"sender_name": " "}, "Namen"),
        ({"sender_postal_city": "Musterstadt"}, "Postleitzahl"),
        ({"recipient": "Muster Kfz-Versicherung AG"}, "Empfänger"),
        ({"date": "31.02.2026"}, "Datum"),
        ({"subject": ""}, "Betreff"),
        ({"signer": ""}, "Namen unter dem Brief"),
        ({"references": [Reference(label="Kennzeichen", value="")]}, "Bezugsangabe"),
        ({"body": "Sehr geehrte Damen und Herren,\n\nMit freundlichen Grüßen"}, "Brieftext"),
        (
            {"body": "Sehr geehrte Damen und Herren,\n\nGrund: [bitte eintragen]\n\nMit freundlichen Grüßen"},
            "Platzhalter",
        ),
    ],
)
def test_open_points_block_in_plain_language(svc: AppService, changes, expected):
    letter = complete_letter(**changes)
    problems = validate_letter(letter)
    assert any(expected in p for p in problems), problems
    with pytest.raises(ExportBlockedError):
        svc.generate_simple_letter(None, letter)
    assert not list(svc.export_dir.glob("*.pdf"))


def test_special_termination_needs_the_reason_filled_in():
    ltype = SIMPLE_LETTER_TYPES["sonderkuendigung"]
    letter = complete_letter(letter_type=ltype.key, subject=ltype.subject, body=ltype.body())
    assert any("Platzhalter" in p for p in validate_letter(letter))
    fixed = letter.model_copy(
        update={
            "body": letter.body.replace(
                letter.body[letter.body.index("[") : letter.body.index("]") + 1],
                "Beitragserhöhung laut Ihrem Schreiben vom 01.11.2026",
            )
        }
    )
    assert validate_letter(fixed) == []


def test_plan_matches_the_form_and_keeps_signature_empty():
    plan = to_plan(complete_letter(), mode="final")
    assert plan.sender_lines == ["Erika Musterfrau", "Beispielweg 7", "12345 Musterstadt"]
    assert plan.recipient_lines == ["Muster Kfz-Versicherung AG", "54321 Beispielstadt"]
    assert [(i.label, i.value) for i in plan.info] == [
        ("Kennzeichen:", "MU-ST 1234"),
        ("Versicherungsnummer:", "KFZ 0001 2345 67"),
        ("Datum:", "13.11.2026"),
    ]
    assert plan.salutation == "Sehr geehrte Damen und Herren," and plan.closing == "Mit freundlichen Grüßen"
    assert len(plan.blocks) == 2 and plan.signer == "Erika Musterfrau"
    assert not plan.blocking


def test_no_deadline_is_calculated_in_any_text():
    """Kein Brieftext nennt ein von der App berechnetes Datum."""
    import re

    for ltype in SIMPLE_LETTER_TYPES.values():
        assert not re.search(r"\d{1,2}\.\d{1,2}\.\d{2,4}", ltype.text), ltype.key


def test_preview_has_watermark_and_lists_open_points(svc: AppService):
    preview = svc.preview_simple_letter(complete_letter(sender_street=""))
    assert preview.page_images and all(p.is_file() for p in preview.page_images)
    assert any("Straße" in p for p in preview.problems)
    assert "VORSCHAU" in pymupdf.open(preview.pdf_path)[0].get_text().upper()


# ---------------------------------------------------------------------------
# Adressbuch
# ---------------------------------------------------------------------------


def test_address_book_remembers_only_what_the_user_used(svc: AppService, tmp_path: Path):
    assert svc.recipient_book() == []
    svc.generate_simple_letter(
        None, complete_letter(recipient="Muster Kfz-Versicherung AG\nPostfach 12\n54321 Beispielstadt")
    )
    assert svc.recipient_book() == [["Muster Kfz-Versicherung AG", "Postfach 12", "54321 Beispielstadt"]]
    book = AddressBook(svc.paths.data / "empfaenger.json")  # neu geladen: dauerhaft gespeichert
    assert book.lookup("Muster Kfz-Versicherung") == [
        "Muster Kfz-Versicherung AG",
        "Postfach 12",
        "54321 Beispielstadt",
    ]
    assert book.lookup("Andere Versicherung") is None
    assert not book.remember(["Nur ein Name"])  # ohne Anschrift wird nichts gemerkt
    assert book.forget("Muster Kfz-Versicherung AG") and book.entries() == []
