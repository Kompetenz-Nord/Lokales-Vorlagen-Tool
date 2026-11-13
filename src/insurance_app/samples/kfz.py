"""Synthetischer Kfz-Versicherungsschein für den einfachen Brief („MUSTER – KEINE ECHTDATEN“)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from ..core.paths import resource_root

__all__ = ["KFZ_SAMPLE_NAME", "KFZ_TRUTH", "generate_kfz_sample"]

KFZ_SAMPLE_NAME = "MUSTER_Kfz_Versicherungsschein.pdf"
#: Erwartete Angaben im Brief (für Tests und zum Nachvollziehen).
KFZ_TRUTH = {
    "sender_name": "Erika Musterfrau",
    "sender_street": "Beispielweg 7",
    "sender_postal_city": "12345 Musterstadt",
    "recipient": ["Muster Kfz-Versicherung AG", "Musterallee 1", "54321 Beispielstadt"],
    "Versicherungsnummer": "KFZ 0001 2345 67",
    "Kennzeichen": "MU-ST 1234",
}


def generate_kfz_sample(target_dir: Path) -> Path:
    """Einseitigen, digitalen Kfz-Versicherungsschein mit frei erfundenen Daten erzeugen."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    fonts = resource_root() / "assets" / "fonts"
    doc = pymupdf.open()
    page = doc.new_page()  # A4
    page.insert_font(fontname="lib", fontfile=str(fonts / "LiberationSans-Regular.ttf"))
    page.insert_font(fontname="libb", fontfile=str(fonts / "LiberationSans-Bold.ttf"))

    def put(x: float, y: float, text: str, size: float = 10.5, bold: bool = False) -> None:
        page.insert_text((x, y), text, fontname="libb" if bold else "lib", fontsize=size)

    put(57, 50, "Muster Kfz-Versicherung AG (MUSTER)", 14, bold=True)
    put(400, 50, "MUSTER – KEINE ECHTDATEN", 9)
    put(71, 135, "Muster Kfz-Versicherung AG · Musterallee 1 · 54321 Beispielstadt", 7)
    for i, line in enumerate(("Frau", "Erika Musterfrau", "Beispielweg 7", "12345 Musterstadt")):
        put(71, 155 + i * 14, line)
    put(57, 290, "Versicherungsschein zur Kraftfahrtversicherung", 13, bold=True)
    rows = [
        ("Versicherungsnummer:", "KFZ 0001 2345 67"),
        ("Versicherungsnehmer:", "Musterfrau, Erika"),
        ("Anschrift:", "Beispielweg 7, 12345 Musterstadt"),
        ("Amtliches Kennzeichen:", "MU-ST 1234"),
        ("Fahrzeug:", "Musterwagen Kombi (MUSTER)"),
        ("Versicherungsbeginn:", "01.01.2025"),
        ("Beitrag:", "48,90 EUR monatlich"),
    ]
    for i, (label, value) in enumerate(rows):
        y = 330 + i * 20
        put(57, y, label)
        put(220, y, value)
    put(57, 520, "Dieses Dokument ist ein frei erfundenes Muster und kein Versicherungsschein.", 9)
    doc.set_metadata({"title": "MUSTER – KEINE ECHTDATEN", "subject": "synthetisches Testdokument"})
    out = target_dir / KFZ_SAMPLE_NAME
    doc.save(out)
    doc.close()
    return out
