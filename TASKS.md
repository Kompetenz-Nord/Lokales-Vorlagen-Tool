# Aufgaben

Legende: `[x]` erledigt und getestet · `[~]` in Arbeit · `[ ]` offen · `[!]` blockiert (Grund angegeben)

## Fundament
- [x] Repository und Umgebung untersucht (Python 3.13, Tesseract 5.3.4 deu/eng, kein Ollama-Netzzugang)
- [x] Originalvorlagen SL13065/SL18477 übernommen, SHA-256 dokumentiert, Felder inspiziert
- [x] Technischer Durchstich: native Befüllung + inkrementelles Speichern + 2 Renderer (SL18477)
- [x] Zentrale Modelle (`core/models.py`, `core/fields.py`, `core/case.py`), Pfade, Einstellungen
- [x] Vorlagenschema + Katalog (9 Einträge) + Feldzuordnungen beider Swiss-Life-Vorlagen (vollständig gegen PDF validiert)
- [x] ARCHITECTURE.md, IMPLEMENTATION_PLAN.md, TASKS.md

## Welle 1
- [~] A – Dokumentcheck, Klassifikation, Textebene, Tesseract-OCR, Seitenqualität
- [~] B – Normalisierung, Positions-Extraktion, Regeln, Zusammenführung/Konflikte, Validierung, Musterfall-Generator
- [~] C – AcroForm-Inspektion, Werteauflösung, TemplateFiller, QA (MuPDF + PDFium), Registry, Export
- [~] D – SQLite-Repository, Verlauf, Log-Schwärzung, Netzwerk-Wächter, Temp-Ordner, Ollama-Client, VLM-Extraktion

## Integration / Welle 2
- [ ] AppService (Orchestrierung der 9 Stufen)
- [ ] PySide6-Oberfläche
- [ ] README, Datenschutz-Doku, Feldzuordnungs-Doku, Build-Skripte Windows/macOS
- [ ] Benchmark OCR-only (synthetisch) + Messskript OCR+VLM
- [ ] Integrationstests End-to-End

## Abschluss
- [ ] Review-Welle und Fehlerbehebung
- [ ] Testbericht, Beispielexport (synthetisch), Statusübersicht

## Fehlende Originalvorlagen (Katalog sichtbar, deaktiviert)
1. Ordentliche Kündigung – Standardanschreiben
2. Außerordentliche Kündigung / Sonderkündigung
3. Befristete Beitragsfreistellung / Beitragsreduzierung – HDI
4. Beitragserhöhung / Vertragsanpassung – Continentale
5. Bestandsübertrag / Betreuerwechsel an tecis AG
6. Widerruf – Antrag / Vertrag / Kündigung
7. Haftungsfreistellung Arbeitskraftabsicherung – tecis
