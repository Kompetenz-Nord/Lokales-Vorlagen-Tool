"""Bausteine der Seite „Kundenmappen“ (Stapelverarbeitung mehrerer Verträge einer Kundin/eines Kunden).

* ``common``     – Texte, Status-Symbole der Warteschlange, Titel-Abfrage
* ``choice``     – Rückfrage „ein Vertrag oder mehrere Verträge?“ (per ``monkeypatch`` ersetzbar)
* ``importing``  – Warteschlange: Dateien nacheinander lesen und je Vertrag zuordnen (Hintergrund)
* ``tables``     – Tabellen „Warteschlange“, „Verträge in dieser Mappe“ und „Ergebnis der Erzeugung“
"""
