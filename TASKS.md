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
- [x] PySide6-Oberfläche (Dashboard, Vorgang mit 9 Stufen, Katalog, Vorlagen-Studio, Einstellungen)
- [x] README, Datenschutz-, Feldzuordnungs-, Brief-, KI- und Bedienungsdoku
- [x] Benchmark OCR-only (synthetisch, 20 Dateien) + Messskript OCR+KI
- [x] Adversariale Prüfrunde (54 bestätigte Befunde, alle behoben)

## Welle 3 – Ergänzungen des Anwenders
- [x] Kundenmappen: mehrere Dokumente verschiedener Gesellschaften auf einmal hochladen, Warteschlange, Gruppierung je Vertrag, eine Ausgabe je Anwenderwunsch
- [x] Lokale KI mit Rollen: aktives Modell (`glm-ocr`), Ausweichmodell (`qwen3-vl:4b-instruct`), optionale Gegenprüfung (`gemma4:e4b`); der Anwender entscheidet
- [x] Hardware-Profile kompakt/standard/maximal mit Profilvorschlag; Hinweise für Mac und Windows
- [x] Installationsskripte für die lokale KI (macOS, Windows) – nur mit Rückfrage, nur lokal
- [x] Echter KI-Lauf auf CPU gemessen (siehe `docs/BENCHMARK.md`, Abschnitt 6)

## Welle 4 – Robustheit und Feinschliff
- [x] Texterkennung: gedrehte Seiten, starkes Rauschen, Tabellen-Kopfzeilen, Wasserzeichen-Reste, ein Rechenkern für Tesseract
- [x] Lesefehler: IBAN (O/0, I/1) nur bei gültiger Prüfziffer und immer „Unklar“; Paragrafenzeichen; I/l-Verdacht nie „sicher“
- [x] Bedienung: Originalvorlage aus dem Katalog importieren, importierte Version entfernen, Passwort-Nachfrage nach Neustart, Warnung bei Cloud-Sync-Ordnern, Pflicht/optional sichtbar
- [x] Benchmark neu gemessen: 97,6 % richtig (vorher 80,9 %), 2 sicher falsche Werte (vorher 16)

## Abschluss
- [x] Review-Welle und Fehlerbehebung
- [x] Testbericht (`docs/TESTBERICHT.md`), Beispielexporte (`docs/beispiel/`), Statusübersicht

## Offen (ehrlich)
- [ ] Build und Start auf einem echten Windows-PC und einem echten Mac (hier nur unter Linux gebaut und getestet)
- [ ] Installationsskripte der lokalen KI auf echtem Mac/Windows ausführen (Windows-Skript wurde nie ausgeführt)
- [ ] Laufzeiten der lokalen KI mit Grafikkarte bzw. Apple Silicon messen; `gemma4:e4b` wurde nicht real geladen
- [ ] Verlorene Umlaute bei der Texterkennung („Backerei“) werden nicht erkannt – Namen immer mit der Quelle vergleichen
- [ ] Code-Signatur und Notarisierung der Installationspakete (Zertifikate des Betreibers nötig)
- [!] Originalvorlage „Haftungsfreistellung Arbeitskraftabsicherung – tecis“ fehlt (siehe unten)

## Fehlende Originalvorlagen
Nicht nutzbar (deaktiviert): **Haftungsfreistellung Arbeitskraftabsicherung – tecis** (Original erforderlich).

Als DIN-5008-Brief nutzbar; eine Versicherer-Originalvorlage kann optional importiert werden:
1. Ordentliche Kündigung – Standardanschreiben
2. Außerordentliche Kündigung / Sonderkündigung
3. Befristete Beitragsfreistellung / Beitragsreduzierung – HDI
4. Beitragserhöhung / Vertragsanpassung – Continentale
5. Bestandsübertrag / Betreuerwechsel an tecis AG
6. Widerruf – Antrag / Vertrag / Kündigung
