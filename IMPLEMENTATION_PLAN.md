# Umsetzungsplan

**Ziel von Release 1:** ein durchgängig getestetes MVP, lokal startbar. Release 2 ist in der Architektur angelegt, die lokale KI als getestete Schnittstelle.

## Ausgangslage (Untersuchung am 13.11.2026)

| Punkt | Befund |
|---|---|
| Repository | leer (nur `LICENSE`, Unlicense) |
| Umgebung | Linux x86_64 (Ubuntu 24.04), Python 3.13, 4 CPU, 15 GB RAM, keine GPU |
| Bibliotheken | PySide6 6.12, PyMuPDF 2.1, pypdf 6.x, pypdfium2, Pydantic 2, Tesseract 5.3.4 (`deu`, `eng`) installiert |
| Netzwerk | PyPI erreichbar. `ollama.com`, GitHub-Releases und HuggingFace **gesperrt (403)**. Ollama, Qwen3-VL und Docling-Modelle sind hier daher nicht installierbar. |
| Originalvorlagen | SL13065 (1 S., 22 Felder) und SL18477 (6 S., 50 Felder) vom Anwender bereitgestellt. AES-128-verschlüsselt, ohne Öffnungspasswort. Rechte: Ausfüllen ja, Ändern/Seiten umbauen nein. |
| Beispiel-Eingänge | (a) Screenshot einer Portal-Vertragsübersicht (Label/Wert, 2 Spalten), (b) gescannte Standmitteilung mit fremder OCR-Textebene (GdPicture). **Echte Daten – nicht im Repository.** |

## Fachliche Klarstellungen des Anwenders (13.11.2026)
- Befüllungsbasis kann **ein Portal-Screenshot** (Vertragsdetails) **oder ein Versicherer-Schreiben** (z. B. Standmitteilung/Jährliche Information, auch gescannt) sein.
- Der **Abschlussvermittler ist für die Befüllung irrelevant** → Datenprüfung fokussiert auf die Felder, die die gewählte Vorlage benötigt; die Vorlage kann direkt nach dem Upload gewählt werden. Nicht benötigte erkannte Angaben werden eingeklappt und ohne Bestätigungspflicht angezeigt.

## Technische Entscheidungen (mit Begründung)

1. **PDF-Befüllung mit PyMuPDF, inkrementelles Speichern, Verschlüsselung beibehalten.** Im Test mit dem echten SL18477 korrekt in MuPDF und PDFium: Umlaute, ß, Datumsteilfelder, Kreuze, Betrag `1.234,56`. Die Ausgabe beginnt byte-identisch mit dem Original. pypdf dient nur zum Gegenlesen.
2. **Checkboxen/Radios** werden über `/V` und `/AS` gesetzt. Die originalen Darstellungen (Kreuz aus ZapfDingbats) bleiben erhalten; es wird nichts neu gezeichnet.
3. **Zwei Renderer für die QA:** MuPDF und PDFium (pypdfium2, Chrome-Engine).
4. **OCR:** Native Textebene zuerst. Danach Tesseract `deu+eng` mit Wortkonfidenzen. RapidOCR und Docling sind als optionale Backends angelegt (Import-geschützt). Eine Fremd-OCR-Ebene in Scans wird als `EMBEDDED_OCR` erkannt.
5. **Extraktion über Wortpositionen** (Label links, Wert rechts in derselben Zeile, Toleranz für versetzte Grundlinien). Begründung: In der Beispiel-Standmitteilung stehen Labels und Werte in der Textreihenfolge getrennt.
6. **SQLite** über die Standardbibliothek. Vorgänge werden als validiertes JSON (Pydantic) plus Indexspalten gespeichert. Keine ORM-Abhängigkeit.
7. **Keine Flachstellung** bei Vorlagen ohne Änderungsrecht (Rechte respektieren). Stattdessen bleibt das Formular ausfüllbar; das ist auch der gewünschte Standard.

## Releases

### Release 1 – Durchgängiges MVP (dieser Lauf)
- Upload (PDF/PNG/JPG/TIFF, mehrere Dateien je Vorgang), Dokumentcheck, Klassifikation
- Native Textebene + Tesseract-Fallback mit Konfidenz je Seite/Feld
- Regelbasierte Extraktion mit Beleg, Zusammenführung über mehrere Dokumente, Konflikte Police ↔ Nachtrag
- Validierung (IBAN, PLZ, Datum, Betrag, Vertragsnummer, Pflichtfelder) und Plausibilitätswarnungen
- Datenprüfung: Bestätigen, Bearbeiten, Unklar, Nicht relevant, Konfliktauswahl, Verlauf
- Vorlagenkatalog (9), zwei aktive Swiss-Life-Vorlagen, sieben mit Status `Originalvorlage fehlt`
- Vorschau mit Checkliste, „PDF jetzt erzeugen“, QA mit zwei Renderern, Export und Protokoll
- Musterfall per Klick (synthetisch), Suche, Archivieren/Löschen, Wiederaufnahme nach Neustart
- Tests, Build-Skripte Windows/macOS, Dokumentation

### Release 2 – Vorlagen-Studio & lokale KI (Architektur angelegt, Teile umgesetzt)
- Vorlagen-Import mit Feld-Autoerkennung, Versionierung, Paket-Export/-Import (umgesetzt)
- Grafischer Koordinateneditor für nicht ausfüllbare PDFs (Grundfunktion)
- Ollama-Client, Status, Modellverwaltung mit Zustimmung, VLM-Extraktion, Abgleich (getestet gegen simulierten lokalen Server)
- Vergleichstest OCR-only vs. OCR+VLM: Messskript vorhanden; Messung auf Zielhardware ausstehend

### Release 3 – Erweiterte Automatisierung
- Bessere Klassifikation, Versichererregeln, weitere OCR-Backends (PaddleOCR nur nach Messung)

## Vorgehen
1. Fundament (Modelle, Feldregister, Vorlagenschema, Mappings): erledigt und gegen die echten PDFs geprüft.
2. **Welle 1 (parallel, getrennte Verzeichnisse):**
   - A: `documents` + `ocr`
   - B: `extraction` + `validation` + `samples`
   - C: `pdf` + `templates` + `export`
   - D: `storage` + `security` + `local_ai`
3. Integration: `core/service.py` (AppService) und End-to-End-Test.
4. **Welle 2 (parallel):** UI, Dokumentation/Build-Skripte, Benchmark, Integrationstests.
5. **Prüf-Welle:** adversariale Reviews (Korrektheit, Datenschutz, fachliche Schutzregeln, PDF-QA) → Fehlerbehebung.
6. Abschluss: Testbericht, Beispielexport (synthetisch), Statusübersicht.
