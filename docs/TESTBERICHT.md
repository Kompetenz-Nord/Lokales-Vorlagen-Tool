# Testbericht

**Kurz gesagt:**

- **1.428 automatische Tests bestanden, 0 fehlgeschlagen, 1 übersprungen** (Lauf am 13.11.2026 unter Linux). Der übersprungene Test prüft nur die Meldung „RapidOCR ist nicht installiert“ – RapidOCR ist hier installiert.
- Die gebaute App (PyInstaller, Linux) startet und besteht ihre Selbstprüfung.
- Texterkennung auf dem synthetischen Testsatz: **97,6 % richtig**, 2 Werte falsch ohne Warnung (Einzelheiten: [BENCHMARK.md](BENCHMARK.md)).
- **Nicht getestet:** Windows und macOS (kein solcher Rechner verfügbar), lokale KI mit Grafikkarte, echte Kundendokumente.
- Alle Tests arbeiten nur mit synthetischen Daten („MUSTER – KEINE ECHTDATEN“).

---

## 1. Umgebung

| | |
|---|---|
| Datum | 13.11.2026 |
| Rechner | Linux-Container (x86_64), 4 CPU-Kerne, 15 GB RAM, keine Grafikkarte |
| Software | Python 3.13.18, PySide6 6.12, Tesseract 5.3.4 (`deu+eng`, `osd`), PyMuPDF 2.1, PyInstaller 6.22.3 |
| Anzeige | ohne Bildschirm (`QT_QPA_PLATFORM=offscreen`) |

## 2. Ausgeführte Befehle und Ergebnisse

| Nr. | Befehl | Ergebnis |
|---|---|---|
| 1 | `ruff check src tests scripts` | „All checks passed!“ |
| 2 | `ruff format --check src tests scripts` | 213 Dateien, alle korrekt formatiert |
| 3 | `QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests` | **1428 passed, 1 skipped** in 173 s |
| 4 | `QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests -m "not slow"` | 1402 passed, 1 skipped, 26 deselected in 142 s |
| 5 | `QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests -m slow` | 26 passed |
| 6 | `python3 -m PyInstaller --noconfirm --clean packaging/lokales_vorlagen_tool.spec` | Build erfolgreich, Ordner 434 MB (inklusive der Hausschriften DM Sans und Tinos) |
| 7 | gebaute App: `LokalesVorlagenTool --selftest` | 14 Prüfpunkte OK, „Selbstprüfung abgeschlossen“ |
| 8 | gebaute App: Start der Oberfläche (ohne Bildschirm, 12 s) | läuft ohne Absturz; Logdatei nur für den Nutzer lesbar (0600) |
| 9 | `python3 scripts/make_examples.py` | 3 Beispiel-PDFs in `docs/beispiel/` erzeugt |
| 10 | `OMP_THREAD_LIMIT=1 python3 scripts/benchmark_extraction.py` | 97,6 % richtig, siehe [BENCHMARK.md](BENCHMARK.md) |
| 11 | `bash -n scripts/install_local_ai_macos.sh` | Syntax in Ordnung |

Die „langsamen“ Tests (`slow`) sind Tests mit echter Texterkennung und mit den Swiss-Life-Originalvorlagen.

## 3. Tests je Bereich

| Bereich | Tests | Was geprüft wird |
|---|---:|---|
| `test_core` | 25 | Datums- und Texthilfen |
| `test_documents` | 109 | Eingangsprüfung: Dateityp, Größe, Passwort, defekte Dateien, Seitenbilder |
| `test_ocr` | 78 | Textebene, Tesseract, Seitenqualität, Drehung, Rauschfilter |
| `test_extraction` | 334 | Normalisierung, Zuordnung Beschriftung → Wert, Konflikte, Lesefehler (IBAN, „§“, I/l) |
| `test_validation` | 53 | IBAN-Prüfziffer, PLZ, Datum, Betrag, Plausibilität |
| `test_template_mapping` | 114 | Vorlagenkatalog, Feldzuordnung, Versionen, Import, Entfernen |
| `test_pdf_output` | 91 | Befüllung der Originalvorlagen, zwei Renderer, Qualitätskontrolle, Export |
| `test_letters` | 51 | DIN-5008-Briefe: Maße, Textbausteine, Plausibilität; einfacher Brief: automatische Befüllung, Prüfung, Erzeugung, Adressbuch |
| `test_storage` | 40 | Datenbank, Migrationen, gleichzeitige Änderungen |
| `test_security` | 91 | Log-Schwärzung, Netzwerk-Wächter, Temp-Ordner, Dateirechte |
| `test_local_ai` | 216 | Ollama-Anbindung (simuliert), Modellrollen, Profile, Abgleich |
| `test_benchmark` | 33 | Messskript und Regressionsschutz für schwierige Scans |
| `test_integration` | 32 | Kompletter Ablauf: Freigabe, Übernahme, Brief, Kundenmappe mit 3 Verträgen |
| `test_ui` | 162 | Oberfläche: Dashboard, Brief schreiben, Vorgang, Kundenmappen, Katalog, Studio, Einstellungen, Gestaltung nach tecis Styleguide |
| **Summe** | **1429** | davon 1 übersprungen |

## 4. Geprüfte Schutzregeln

| Regel | Nachweis |
|---|---|
| Keine Datenübertragung nach außen | Netzwerk-Wächter blockiert alles außer Loopback (`test_sec_network_guard.py`); Integrationstests laufen mit aktivem Wächter |
| Keine Cloud-KI | Modellnamen mit `:cloud` werden abgelehnt; Ollama nur über `127.0.0.1` (`test_lai_ollama_client.py`, `test_ui_settings_ai.py`) |
| Modell-Download nur nach Zustimmung | Ohne Bestätigung kein Aufruf von `/api/pull` (`test_ui_settings_ai.py`) |
| Keine erfundenen Daten | Unlesbare Werte bleiben „Unklar“ oder „Fehlt“; korrigierte IBAN nur bei gültiger Prüfziffer und nie „sicher“ (`test_ext_ocr_confusions.py`) |
| Auftragsdaten nie automatisch | Extraktion und KI lehnen `order.*`-Felder ab (`test_ext_*`, `test_lai_reconcile.py`) |
| Unterschriften bleiben leer | Unterschriftsfelder werden nie befüllt (`test_pdf_filler.py`) |
| Original bleibt unverändert | SHA-256 der Vorlage vor und nach dem Befüllen gleich; leerer Füllplan ergibt bytegenaue Kopie (`test_pdf_end_to_end.py`) |
| PDF-Berechtigungen | Kein „Flachrechnen“, wenn die Vorlage Änderungen verbietet (`test_pdf_filler.py`) |
| Keine Personendaten im Protokoll | Schwärzung von IBAN, Datum, Nummern, E-Mail (`test_sec_logging.py`, `test_sec_redaction.py`) |
| PDF erst nach Freigabe | Offene Pflichtpunkte blockieren die Erzeugung (`test_end_to_end.py`, `test_bundle_batch.py`) |

## 5. In dieser Runde gefundene und behobene Fehler

| Fehler | Auswirkung | Behebung | Nachweis |
|---|---|---|---|
| Sporadischer Absturz der Oberfläche („Segmentation fault“) | Die Speicherbereinigung von Python lief in einem Hintergrund-Thread und gab dort Fenster-Objekte frei. Vorher: 2 Abstürze in 6 Läufen der Oberflächentests. | Bereinigung läuft nur noch im Fenster-Thread (`ui/gc_guard.py`) | 18 von 18 Läufen der Oberflächentests ohne Absturz; `test_ui_gc_guard.py` |
| Katalog schnitt die rechte Spalte ab | „Aktiv“-Marke und Text nicht sichtbar, waagerechter Rollbalken | Import-Schaltfläche in eigener Zeile | `test_catalog_screenshot` prüft die Breite |
| Fehlermeldung ohne Formathinweis | Bei falschem Datum stand nur „Wert nicht eindeutig lesbar“ | Meldung nennt wieder „TT.MM.JJJJ“ | `test_ui_case.py` |
| Gedrehte und stark verrauschte Scans wurden nicht gelesen | 0 von 26 Feldern | Lageerkennung und Rauschfilter | `test_bench_ocr_regression.py` |
| Tabellenwerte unter Spaltenköpfen fehlten | 5 von 12 Feldern fehlten | Suche unter der Beschriftung | `test_bench_ocr_regression.py` |
| „§“ als „8“ gelesen, als „Erkannt“ angezeigt | falscher Produktname ohne Warnung | Wiederherstellung in Gesetzesverweisen | `test_ext_ocr_confusions.py` |
| Rotierte Logdateien mit Standardrechten | für andere Nutzer des Rechners lesbar | Rechte 0600 auch nach dem Rotieren | `test_sec_logging.py` |
| Zwei veraltete Tests | erwarteten altes Verhalten | an dokumentiertes Verhalten angepasst | SL18477 Zwischenzeitraum, 180°-Fundstelle |

## 5a. Gestaltung nach dem tecis Styleguide

Die Oberfläche folgt dem tecis Corporate Design Manual 1.0 (Farben, DM Sans, Tinos). `tests/test_ui/test_ui_brand.py` (8 Tests) prüft Farbwerte, Aufrasterung, Farbkombinationen, Schriften und dass außerhalb von `theme.py` keine Farbe fest eingetragen ist. Einzelheiten und die eine bewusste Ergänzung (Signalfarben für Prüfstatus): [GESTALTUNG.md](GESTALTUNG.md).

## 5b. Einfacher Brief („Brief schreiben“)

| Geprüft | Ergebnis | Nachweis |
|---|---|---|
| Digitales Musterdokument (Kfz-Versicherungsschein) → Brief | Absender, Empfänger, Kennzeichen und Versicherungsnummer automatisch gefüllt; ohne weitere Eingabe vollständig; PDF erzeugt | `test_document_fills_all_letter_fields_automatically` |
| Dasselbe Dokument als Scan (Texterkennung mit Tesseract) | alle Angaben gefüllt | `test_scanned_document_fills_the_letter_too` |
| Dasselbe als Scan mit RapidOCR (von Hand geprüft, offline mit Netzwerk-Wächter) | alle Angaben gefüllt, 3,4 s statt 1,5 s; Hinweis „Schreibweise bitte prüfen“ beim Empfänger, weil RapidOCR die sehr kleine Absenderzeile ohne Wortabstände liest | Lauf am 13.11.2026 |
| Leeres oder widersprüchliches Dokument | Felder bleiben leer, Hinweis nennt den Grund – nichts wird erfunden | `test_nothing_is_invented_…`, `test_conflicting_values_…` |
| Unvollständiger Brief, Platzhalter im Text | kein PDF, verständliche Liste der offenen Punkte | `test_open_points_block_in_plain_language` |
| Keine berechneten Fristen in den Brieftexten | bestätigt | `test_no_deadline_is_calculated_in_any_text` |
| Oberfläche: nichts überlappt oder ist abgeschnitten, ein Klick erzeugt das PDF | bestätigt | `tests/test_ui/test_ui_letter.py` (10 Tests) |

**Grenze:** Getestet wurde mit einem synthetischen Dokument im üblichen Briefaufbau (Absenderzeile über dem Adressfeld). Bei echten Dokumenten mit anderem Aufbau können einzelne Felder leer bleiben; sie lassen sich dann von Hand eintragen.

## 6. Lokale KI

- **Automatische Tests** laufen gegen einen simulierten Ollama-Dienst. Sie prüfen Protokoll, Fehlerbehandlung und Schutzregeln, **nicht** die Erkennungsqualität.
- **Echter Lauf auf CPU** (ohne Grafikkarte, synthetischer Scan mit 26 Feldern):

  | Modell | Zeit je Seite | Ergebnis |
  |---|---:|---|
  | `glm-ocr` | 38–43 s | 26 von 26 Feldern |
  | `qwen3-vl:4b-instruct` | ca. 250–256 s | 6 von 8 abgefragten Feldern |
  | `qwen3-vl:4b` (Denkvariante) | 345–370 s | Antwort abgeschnitten, nicht nutzbar |

- `gemma4:e4b` wurde nicht real geladen. Zeiten mit Grafikkarte oder Apple Silicon wurden nicht gemessen.

## 7. Was nicht getestet wurde

| Punkt | Grund | Empfehlung |
|---|---|---|
| Windows 10/11 und macOS | kein solcher Rechner in der Build-Umgebung | `scripts/setup_*.{ps1,sh}` und `scripts/build_*.{ps1,sh}` auf dem Zielrechner ausführen, danach `--selftest` |
| Installationsskripte der lokalen KI | macOS-Skript nur unter Linux durchgespielt, Windows-Skript nie ausgeführt | zuerst mit `--dry-run` bzw. `-DryRun` starten |
| Lokale KI mit Grafikkarte | keine Grafikkarte vorhanden | auf Zielhardware mit synthetischen Dokumenten messen |
| Echte Dokumente | bewusst nicht: nur synthetische Daten im Projekt | erste echte Vorgänge besonders gründlich gegen die Quelle prüfen |
| Druckbild auf Papier | kein Drucker | je Vorlage einen Probedruck prüfen (Falzmarken, Adressfeld im Fensterumschlag) |
| Bedienung durch Menschen | nur automatische Oberflächentests | kurzer Probelauf mit dem Musterfall |
| Code-Signatur der Installationspakete | Zertifikate des Betreibers nötig | vor der Verteilung signieren (Windows) bzw. notarisieren (macOS) |

## 8. Bekannte Einschränkungen

- Verlorene Umlaut-Punkte in Scans („Bäckerei“ → „Backerei“) erkennt die App nicht. Der Wert erscheint als „Erkannt“. Namen deshalb immer mit der Quelle vergleichen.
- „Erkannt“ heißt nie „richtig“: Jeder Wert muss vor der PDF-Erzeugung von einem Menschen bestätigt werden. Die App erzwingt das.
- Die Originalvorlage „Haftungsfreistellung Arbeitskraftabsicherung – tecis“ fehlt. Der Eintrag ist deaktiviert (siehe [FEHLENDE_VORLAGEN.md](FEHLENDE_VORLAGEN.md)).
