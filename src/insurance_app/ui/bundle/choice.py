"""Rückfrage beim Ablegen mehrerer Dateien: ein Vertrag (ein Vorgang) oder mehrere Verträge (Kundenmappe)?

Aufruf immer über das Modul (``choice.ask_single_or_bundle(...)``), damit Tests die Funktion
per ``monkeypatch`` ersetzen können.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

CHOICE_SINGLE = "single"
CHOICE_BUNDLE = "bundle"

QUESTION = (
    "Gehören die Dateien zu EINEM Vertrag (z. B. Police + Nachtrag) oder zu MEHREREN "
    "Verträgen/Gesellschaften?"
)
SINGLE_TEXT = "Ein Vertrag – ein Vorgang"
BUNDLE_TEXT = "Mehrere Verträge – Kundenmappe"
DETAIL_TEXT = (
    "„Ein Vertrag“: Alle Dateien kommen in einen Vorgang (bisheriges Verhalten).\n"
    "„Mehrere Verträge“: Die App legt eine Kundenmappe an, liest die Dateien nacheinander und "
    "bildet je Vertragsnummer einen eigenen Vorgang."
)


def ask_single_or_bundle(parent: QWidget | None, file_count: int) -> str | None:
    """``CHOICE_SINGLE``, ``CHOICE_BUNDLE`` oder ``None`` (abgebrochen)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(f"{file_count} Dateien übernehmen")
    box.setText(QUESTION)
    box.setInformativeText(DETAIL_TEXT)
    single = box.addButton(SINGLE_TEXT, QMessageBox.ButtonRole.AcceptRole)
    bundle = box.addButton(BUNDLE_TEXT, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(single)
    box.exec()
    clicked = box.clickedButton()
    if clicked is single:
        return CHOICE_SINGLE
    if clicked is bundle:
        return CHOICE_BUNDLE
    return None
