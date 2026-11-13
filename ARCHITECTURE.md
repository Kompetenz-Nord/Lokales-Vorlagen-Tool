# Architektur – Lokales Vorlagen-Tool

**Kurz gesagt:** Eine Python-Desktop-App (PySide6). Sie liest Versicherungsdokumente **ausschließlich lokal**, lässt jeden erkannten Wert vom Menschen prüfen und befüllt danach die **unveränderte Originalvorlage**. Die lokale KI ist ein optionales Zusatzmodul. Ohne sie funktioniert alles.

## 1. Leitprinzipien

| Prinzip | Umsetzung |
|---|---|
| LOCAL ONLY | Kein Cloud-Dienst, keine Telemetrie. Ein Netzwerk-Wächter (`security/network_guard.py`) blockiert ausgehende Verbindungen außer Loopback (`127.0.0.1`/`::1`). Ausnahme: ausdrücklich bestätigte Modell-Downloads. |
| Beleg statt Vermutung | Jeder Wert trägt Quelle, Seite, Textausschnitt, Methode, Konfidenz und Status (`FieldValue`). Fehlt der Beleg, bleibt der Wert leer (`MISSING`). |
| Mensch entscheidet | Nur `CONFIRMED`/`MANUAL` gelangt ins PDF. Auftragsdaten (`order.*`) sind technisch gegen automatische Befüllung gesperrt (`Case.set_field`). |
| Original bleibt Original | Hochgeladene Dokumente: schreibgeschützte Kopie im Vorgangsordner. Vorlagen: SHA-256-geprüft. Befüllung per **inkrementellem Speichern**: Die Ausgabe beginnt byte-identisch mit dem Original. |
| PDF-Rechte respektieren | Die Swiss-Life-Vorlagen sind mit einem Rechte-Passwort geschützt („Ausfüllen erlaubt, Ändern nicht“). Die App füllt nur aus, behält Verschlüsselung und Rechte bei und umgeht nichts. |

## 2. Schichten und Module

```
src/insurance_app/
  core/         Datenmodelle (models, fields, case), Pfade, Einstellungen, AppService (Orchestrierung)
  documents/    Eingangsprüfung (Typ, Größe, Seiten, Passwort, Defekt, Drehung), Klassifikation, Seitenbilder
  ocr/          Native Textebene (PyMuPDF), OCR-Backends (Tesseract; optional RapidOCR/Docling), Seitenqualität
  extraction/   Normalisierung, Label-Wert-Zuordnung über Wortpositionen, Regeln je Feld, Zusammenführung/Konflikte
  validation/   Feldprüfungen (IBAN-Prüfziffer, PLZ, Datum, Betrag, Vertragsnummer), Plausibilitätsregeln je Vorlage
  templates/    Vorlagenkatalog (9 Einträge), Schema, Registry (Status/Version/Import/Paket), Werteauflösung (FillPlan)
  pdf/          AcroForm-Inspektion, TemplateFiller (nativ + Overlay-Fallback), Rendering (MuPDF + PDFium), Qualitätskontrolle
  export/       Dateinamen, Export nach bestandener QA, Exportprotokoll ohne Personendaten
  storage/      SQLite (Vorgänge, Kundenmappen, Dokumente, Verlauf, Exportprotokoll, Vorlagenversionen), Schema-Version 3
  security/     Log-Schwärzung, Netzwerk-Wächter, temporäre Arbeitsordner, Sperre gegen einen zweiten App-Start
  local_ai/     Optional: Ollama-Client (nur Loopback), Modellkatalog und Hardware-Profile, GLM-OCR-Text, VLM-Extraktion mit JSON-Schema, Abgleich -> CONFLICT
  letters/      Generierte Kundenschreiben nach DIN 5008 (Textbausteine, Plausibilität, Renderer, Brief-QA)
  samples/      Musterfall-Generator (synthetisch, „MUSTER – KEINE ECHTDATEN“) und Benchmark-Varianten
  ui/           PySide6-Oberfläche (Dashboard, Vorgang mit 9 Stufen, Kundenmappen, Vorlagen, Vorlagen-Studio, Einstellungen)
```

Abhängigkeitsrichtung: `ui → core.service → (documents, ocr, extraction, validation, templates, pdf, export, storage, local_ai) → core.models`. Fachmodule importieren **nie** `ui`.

## 3. Datenfluss (9-Stufen-Pipeline)

```
1 Ablage ─► 2 Dokumentcheck ─► 3 Texterkennung ─► 4 Datenextraktion ─► 5 Konflikt-/Plausiprüfung
   │            check_document()    native Textebene      Label↔Wert über      merge_candidates():
   │            (Kopie, SHA-256)    → OCR nur für Seiten  Wortpositionen,      CONFLICT/UNCLEAR/MISSING,
   │                                  ohne brauchbaren     Regeln je Feld       validate_*()
   │                                  Text (Tesseract)
   ▼
6 Datenfreigabe ─► 7 Vorlage wählen ─► 8 Vorschau + QA ─────────────► 9 Export
  Nutzer bestätigt/     nur "Aktiv"       resolve_fill_values() → FillPlan    erst nach Klick
  korrigiert; Auftrags- wählbar           TemplateFiller (temp) → QA (2 Renderer) "PDF jetzt erzeugen"
  daten aktiv wählen                      Checkliste offener Punkte          → exports/
```

Optional zwischen 3 und 5: **lokale Vision-KI** nur für Seiten mit schlechter OCR-Qualität oder unklarer Zuordnung. Ihre Vorschläge sind `Candidate`s mit Methode `LOCAL_VLM`. Weicht ein Vorschlag von einem OCR-Wert ab, entsteht `CONFLICT`. Die KI löst nie selbst auf.

## 4. Zentrales Datenmodell

* `FieldValue` (je Feld): `value`, `normalized_value` (kanonisch: Datum `YYYY-MM-DD`, Monat `YYYY-MM`, Betrag `1234.56`, IBAN kompakt), `status`, `confidence`, `source_document_id`, `source_page`, `source_quote`, `extraction_method`, `model_version`, `user_confirmed_at`, `candidates[]`, `note`, `validation[]`.
* Feldregister `core/fields.py`: Schlüssel, Label, Gruppe, Datentyp, Kategorie (`CONTRACT`/`ORDER`), `sensitive`, `legal_relevant`.
* `Case`: Dokumente (mit Prüf-, Klassifikations- und Textergebnis), `contract_fields`, `order_fields`, gewählte Vorlage + Version, Verlauf (`HistoryEntry`), Exporte.

## 5. Vorlagen

* Katalog `config/template_catalog.json` mit 9 Einträgen in 4 Gruppen. Status je Eintrag: `Aktiv`, `Originalvorlage fehlt`, `Zu prüfen`, `Fehler`.
  - `Aktiv` setzt voraus: Original vorhanden, SHA-256 passt, alle zugeordneten Felder existieren im PDF, Ausfüllrecht vorhanden, Zuordnung bestätigt.
* Feldzuordnung `config/field_schemas/*.json` (`TemplateDefinition`/`FieldMapping`): `kind` (text, date_part, amount, checkbox, radio, signature, signature_date …), `source` oder `compose`, `conditions` (UND), `required`, `legal_relevant`, `fill_policy` (`never` für Unterschriften).
* Vom Anwender importierte Vorlagen werden versioniert unter `user_data/templates/<katalog-id>/<version>/` abgelegt (Original + Mapping). Paketformat: ZIP mit `original.pdf` + `mapping.json` + `manifest.json`.

### Zwei Vorlagenarten
| Art | Quelle | Befüllung | Status im Katalog |
|---|---|---|---|
| **Originalvorlage** (AcroForm-PDF) | Versicherer-Formular, unverändert (`assets/templates/originals` oder importiert) | native Formularfelder, inkrementell | `Aktiv` nach SHA-/Feld-/Rechteprüfung und bestätigter Zuordnung |
| **Brief nach DIN 5008** (generiert) | Textbausteine `config/letter_templates/*.json` | ReportLab, eingebettete Schrift, Form A/B | `Aktiv`, solange keine eigene Originalvorlage importiert wurde |

Briefe sind Kundenschreiben an den Versicherer – keine nachgebauten Versicherer-Formulare. Die tecis-Haftungsfreistellung (Eintrag 9) wird bewusst nicht generiert (haftungsrelevanter Wortlaut eines Dritten).

## 6. Befüllung und Qualitätskontrolle

1. `resolve_fill_values()` erzeugt einen `FillPlan`. Er enthält nur freigegebene Werte und eine Checkliste: fehlende Pflichtfelder, unbestätigte Werte, offene rechtliche Auswahlen, Bestätigungen, Plausibilitätswarnungen.
2. `TemplateFiller` arbeitet auf einer Kopie der Vorlage:
   - Textfelder setzt er nativ (Darstellung mit DA-Schrift).
   - Bei Checkboxen und Optionsgruppen setzt er `/V` und `/AS` auf den **im PDF gemessenen** Ein-Zustand. Die vorhandenen Darstellungen bleiben erhalten.
   - Betragsfelder: Wert `1234.56`, Darstellung `1.234,56`, passend zu Acrobat `AFNumber_Format(2,2,…)`.
   - Gespeichert wird inkrementell mit unveränderter Verschlüsselung.
3. Qualitätskontrolle:
   - Die Datei wird erneut geöffnet. Geprüft werden Seitenzahl, zurückgelesene Feldwerte, leere Signaturfelder, Checkbox-Zustände, byte-identischer Original-Präfix und Glyphenabdeckung (WinAnsi).
   - Der Text muss in die Feldbreite passen.
   - Jede Seite wird mit **MuPDF und PDFium** gerendert. Zu erhaltende Seiten müssen pixelgleich mit dem Original sein.
4. Erst danach verschiebt die App die Datei in den Exportordner. Name nach dem Muster `JJJJ-MM-TT_SWISSLIFE_SL13065_Freigabe_<Vorgangs-ID>.pdf`.

„Formularfelder fixieren“ (flach) wird nur angeboten, wenn die PDF-Rechte Änderungen erlauben. Bei den Swiss-Life-Vorlagen ist das nicht der Fall. Sie bleiben daher ausfüllbar, und die Oberfläche begründet das.

Technische Details:

- PyMuPDF setzt beim Aktualisieren eines Textfelds die Schrift auf Helvetica (`/Helv`). Helvetica hat praktisch dieselben Zeichenbreiten wie das im Formular hinterlegte Arial.
- Die Formular-Ressourcen (`/DR`), die Verschlüsselung und die PDF-Rechte bleiben unverändert.
- Der Wert eines Betragsfelds (`/V`) wird roh gespeichert (`1234.56`). Angezeigt wird `1.234,56`, passend zum Acrobat-Zahlformat.

**Briefe:** Die Brief-QA (`letters/qa.py`) prüft A4-Format, vorhandene Texte, das Fehlen von Platzhaltern und Vorschau-Wasserzeichen sowie eingebettete Schriften. Außerdem prüft sie, dass die Anschrift im Sichtfenster liegt, genug Platz für die Unterschrift bleibt und die Metadaten keine Personendaten enthalten. Beide Renderer müssen die Datei darstellen können.

## 7. Datenschutz-Architektur

* Laufzeitdaten liegen nur lokal: `user_data/` (SQLite, Vorgangsordner, Logs, tmp) und `exports/`.
* Logs enthalten nur technische Angaben: Job-ID, Zeit, Status, Fehlerklasse. Ein `RedactingFilter` schwärzt zusätzlich IBAN-, Datums-, Nummern- und E-Mail-Muster.
* Temporäre Dateien liegen in einem Arbeitsordner je Job. Sie werden nach Abschluss oder Abbruch gelöscht; beim Start räumt die App Reste auf.
* Passwörter für PDFs fragt nur ein Dialog ab (`QLineEdit.Password`). Sie werden weder gespeichert noch protokolliert.

## 8. Lokale KI (optional)

* `local_ai/ollama_client.py`: HTTP nur an Loopback (geprüft vor jedem Aufruf). Modell-Download nur mit `user_consented=True`. Modellnamen mit `:cloud` werden abgelehnt. `unload_model` entlädt ein Modell nach Gebrauch (`keep_alive: 0`).
* `local_ai/status.py`: Modellkatalog (`MODEL_CATALOG`) mit Rolle, Größe, Kontextlänge und Kennzeichnung von Denkvarianten. `local_ai/profiles.py`: Profile `kompakt`, `standard`, `maximal` und Profilvorschlag aus Arbeitsspeicher und freiem Speicherplatz.
* **Rollen** (der Anwender wählt sie in den Einstellungen):

  | Rolle | Voreinstellung | Arbeitsweise |
  |---|---|---|
  | Aktives Modell | `glm-ocr` | liest die Seite als Text (`local_ai/ollama_ocr.py`); danach ordnen die normalen Regeln der App die Felder zu |
  | Ausweichmodell | `qwen3-vl:4b-instruct` | liest Felder direkt aus dem Seitenbild als JSON mit wörtlichem Zitat (`local_ai/vlm_extractor.py`); nur für Dokumente, bei denen das aktive Modell nichts liefert |
  | Gegenprüfung | keine (`gemma4:e4b` im Profil „maximal“) | liest alle Dokumente unabhängig ein zweites Mal |

* **Ablauf** (`AppService.run_local_ai`): Ein Modell bearbeitet alle Dokumente und wird dann entladen, erst danach folgt das nächste. Je Bildanfrage 8.192 Token Kontext, Temperatur 0, festes JSON-Schema, Zeitlimit `ai_timeout_s` (600 s).
* **Abgleich** (`local_ai/reconcile.py`): Die KI überschreibt nie. Ein abweichender Wert erzeugt einen `CONFLICT`, auch wenn der bisherige Wert nur „Unklar“ war. Auftragsdaten und rechtlich relevante Felder liest die KI nicht.

## 9. Zentrale Steuerung (`core/service.py`)

`AppService` ist die einzige Schnittstelle der Oberfläche. Zuständigkeiten:

- **Start:** Netzwerk-Wächter, Logging, Aufräumen alter Temp-Ordner, Datenbank, Registry und OCR-Backends. Tesseract wird auch ohne PATH gefunden (macOS-Homebrew, Windows Program Files).
- **Stufen:** `add_document` → `recognize_text` / `extract_data` (bzw. `process_documents`) → Nutzeraktionen (`confirm_field`, `edit_field`, `set_choice`, `choose_candidate`, `mark_unclear`, `mark_not_applicable`) → `select_template` → `review_status` → `build_preview` → `generate_pdf`.
- **Vorlagenwahl:** jederzeit möglich. `review_status` liefert die für die Vorlage benötigten Felder. Nicht benötigte Angaben, z. B. der Abschlussvermittler, müssen nicht bestätigt werden.
- **Lange Schritte:** laufen in Worker-Threads der Oberfläche und sind über `threading.Event` abbrechbar.
- **Kundenmappen:** `create_bundle` → `import_into_bundle` (Warteschlange: jede Datei wird nacheinander geprüft, gelesen und über die Vertragsnummer einem Vorgang zugeordnet; ein Nachtrag landet beim passenden Vertrag) → je Vorgang Vorlage wählen und prüfen → `generate_bundle` erzeugt je Vorgang eine Ausgabe. Nicht fertige Vorgänge werden mit Begründung übersprungen, nichts wird halb erzeugt.
- **Gleichzeitige Änderungen:** Jeder Vorgang trägt eine Revisionsnummer (`Case.revision`). Speichert ein veralteter Stand, meldet das Repository `StaleCaseError`, statt neuere Daten zu überschreiben.
- **Ein Start je Datenordner:** Eine Sperrdatei verhindert, dass zwei App-Fenster gleichzeitig dieselbe Datenbank ändern.

## 10. Erweiterbarkeit

* **Neuer Versicherer/Vorlage:** Original-PDF im Vorlagen-Studio importieren, automatisch erkannte Felder zuordnen, Zuordnung bestätigen. Danach ist die Vorlage `Aktiv`. Kein Code nötig.
* **Neues Feld:** Eintrag in `core/fields.py` plus Label-Synonyme in `extraction/rules.py`.
* **Neues OCR-Backend:** Klasse mit dem Protokoll `ocr.base.OcrBackend`.
