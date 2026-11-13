# Aufgaben

Legende: `[x]` erledigt und getestet · `[~]` in Arbeit · `[ ]` offen · `[!]` blockiert (Grund angegeben)

## Fundament
- [x] Repository und Umgebung untersucht (Python 3.13, Tesseract 5.3.4 deu/eng, kein Ollama-Netzzugang)
- [x] Originalvorlagen SL13065/SL18477 übernommen, SHA-256 dokumentiert, Felder inspiziert
- [x] Technischer Durchstich: native Befüllung + inkrementelles Speichern + 2 Renderer (SL18477)
- [x] Zentrale Modelle (`core/models.py`, `core/fields.py`, `core/case.py`), Pfade, Einstellungen
- [x] Vorlagenschema + Katalog (9 Einträge) + Feldzuordnungen beider Swiss-Life-Vorlagen (vollständig gegen PDF validiert)
- [x] ARCHITECTURE.md, IMPLEMENTATION_PLAN.md, TASKS.md

## Welle 1 (abgeschlossen, 960 Tests grün)
- [x] A – Dokumentcheck, Klassifikation, Textebene, Tesseract-OCR (ohne Temp-Dateien), Seitenqualität
- [x] B – Normalisierung, Positions-Extraktion, Regeln, Zusammenführung/Konflikte, Validierung, Musterfall-Generator
- [x] C – AcroForm-Inspektion, Werteauflösung, TemplateFiller, QA (MuPDF + PDFium), Registry, Export
- [x] D – SQLite-Repository, Verlauf, Log-Schwärzung, Netzwerk-Wächter, Temp-Ordner, Ollama-Client, VLM-Extraktion

## Zusatz auf Anwenderwunsch
- [x] DIN-5008-Briefvorlagen für Einträge 3–8 (Kündigung, Sonderkündigung, Beitragsänderung, Erhöhung, Betreuerwechsel, Widerruf)
- [x] Prüfung der Beispielvorlagen des Anwenders (Fehler dokumentiert, in neuen Vorlagen behoben)
- [x] Eingangsarten Portal-Screenshot und gescannte Standmitteilung (Positions-Extraktion, Fremd-OCR-Erkennung)

## Integration / Welle 2
- [x] AppService (Orchestrierung der 9 Stufen) + CLI-Selbstprüfung
- [x] Integrationstests End-to-End (Freigabe, Übernahme, Brief, Passwort-PDF, Logs, Netzwerk, Portal-OCR)
- [x] Build-Skripte Windows/macOS + PyInstaller-Spezifikation
- [~] PySide6-Oberfläche (Dashboard/Vorgang · Katalog/Studio/Einstellungen)
- [~] README, Datenschutz-, Feldzuordnungs-, Brief-, KI- und Bedienungsdoku
- [~] Benchmark OCR-only (synthetisch, erweitertes Testset) + Messskript OCR+VLM
- [~] Adversariale Prüfrunde Backend (5 Dimensionen)

## Abschluss
- [ ] Review-Welle und Fehlerbehebung
- [ ] Testbericht, Beispielexport (synthetisch), Statusübersicht

## Fehlende Originalvorlagen
Nicht nutzbar (deaktiviert): **Haftungsfreistellung Arbeitskraftabsicherung – tecis** (Original erforderlich).

Als DIN-5008-Brief nutzbar; eine Versicherer-Originalvorlage kann optional importiert werden:
1. Ordentliche Kündigung – Standardanschreiben
2. Außerordentliche Kündigung / Sonderkündigung
3. Befristete Beitragsfreistellung / Beitragsreduzierung – HDI
4. Beitragserhöhung / Vertragsanpassung – Continentale
5. Bestandsübertrag / Betreuerwechsel an tecis AG
6. Widerruf – Antrag / Vertrag / Kündigung
