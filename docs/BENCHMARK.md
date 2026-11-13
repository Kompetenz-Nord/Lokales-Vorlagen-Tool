# Benchmark: Texterkennung und Datenextraktion

**Kurz gesagt:**

- Gemessen wurde **nur OCR** (vorhandene PDF-Textebene + Tesseract, danach die regelbasierte Extraktion). Die **lokale KI wurde nicht gemessen** (siehe [Abschnitt 6](#6-vergleich-mit-der-lokalen-ki)).
- Testsatz: **20 synthetische Dateien** (25 Seiten, 456 Sollwerte), alle mit „MUSTER – KEINE ECHTDATEN“ gekennzeichnet. Die Zahlen gelten **nur für diesen Testsatz**, nicht für echte Dokumente.
- Ergebnis gesamt: **80,9 % richtig, 4,8 % falsch, 14,3 % fehlend**.
- **Digitale PDFs:** 93,8 % richtig, **kein** falscher Wert. Einzige Lücke: Werte in Tabellen mit Spaltenköpfen.
- **Ordentliche Scans** (100–300 dpi, leichtes Rauschen, bis 4,5° schräg, starke JPEG-Kompression): meist 25 von 26 Werten richtig. Aber fast jede Scanseite hat **einen Wert, der falsch ist und trotzdem als „Erkannt“ erscheint** (z. B. „§“ wird zu „8“, „Bäckerei“ zu „Backerei“).
- **Zwei Totalausfälle und eine Lücke:** stark verrauschter Scan (0 %, obwohl für Menschen lesbar), um 90° gedrehter Scan (0 %, dazu 2 sicher falsche Werte), Tabellen mit Spaltenköpfen (5 von 12 Werten fehlen).
- **Laufzeit:** digitale PDF ca. 0,04 s, Scanseite ca. 2–3 s auf 4 CPU-Kernen. **Unter CPU-Last** lief Tesseract in der Standardeinstellung bis in die Zeitgrenze von 240 s je Seite. Die Seite blieb dann leer.
- **Folge für die Praxis:** Jeden Wert mit der Quelle vergleichen, bei Scans besonders gründlich. Der Status „Erkannt“ heißt **nicht** „richtig“.

---

## 1. Begriffe

| Begriff | Bedeutung |
|---|---|
| **OCR** | Texterkennung aus dem Bild einer Seite (hier: Tesseract) |
| **Textebene** | Text, der schon im PDF gespeichert ist (digitale PDFs). Dafür ist keine OCR nötig. |
| **Sollwert** | Der richtige Wert eines Feldes laut Musterlösung des Generators |
| **Richtig** | Die App schlägt genau den Sollwert vor |
| **Falsch** | Die App schlägt einen anderen Wert vor |
| **Fehlend** | Die App schlägt keinen einzelnen Wert vor (Status „Fehlt“ oder „Konflikt“) |
| **Sicher falsch** | Falscher Wert mit Status „Erkannt“ (`EXTRACTED`), also **ohne** Unsicherheits-Hinweis. Das ist der gefährlichste Fehler, weil nichts warnt. |
| **Unklar** | Wert mit Status „Unklar“ (`UNCLEAR`). Die App warnt hier selbst. |
| **dpi** | Bildpunkte je Zoll. 300 dpi entspricht einem guten Scan, 100 dpi einem groben. |
| **Spitzen-RAM** | Höchster Arbeitsspeicher während einer Datei: Python-Prozess plus Tesseract |

Verglichen wird nach der kanonischen Form: Datum `JJJJ-MM-TT`, Betrag `1234.56`, IBAN ohne Leerzeichen. Bei Texten und Namen zählen Groß-/Kleinschreibung und Satzzeichen nicht (`local_ai.benchmark.values_equal`).

## 2. Testsatz

Erzeugt mit `insurance_app.samples.variants.generate_benchmark_set()`. Die Erzeugung ist deterministisch (Seed 0). Der Satz liegt unter `tests/fixtures/benchmark/` (10,8 MB, mit `manifest.json` und je Datei einer `*.truth.json`).

- **Synthetisch:** frei erfundene Namen („Erika Musterfrau“, „Olli Illner“, „Muster Tischlerei GmbH“), fiktiver Versicherer „Beispiel Lebensversicherung AG (MUSTER)“, nur veröffentlichte Beispiel-IBANs.
- **Gekennzeichnet:** Jede Seite trägt sichtbar „MUSTER – KEINE ECHTDATEN“. Bei PDFs steht der Hinweis zusätzlich in den Metadaten, bei PNG/JPEG im Bildkommentar.
- **Layouts:** Police aus dem Musterfall (Seite 1), neue Police über 3 Seiten, Nachtrag, Beitragsmitteilung mit Tabellen, Police mit verwechselbaren Zeichen, Standmitteilung (2 Seiten), Portal-Screenshot.

| Kategorie | Dateien | Was variiert |
|---|---|---|
| `digital` | 4 | Digitale PDFs mit Textebene: Police 3 Seiten, Nachtrag, Tabellen, verwechselbare Zeichen |
| `rauschen` | 3 | Scan 300 dpi: ohne / leichtes / starkes Rauschen. „Stark“ ist für Menschen noch gut lesbar. |
| `schraeglage` | 4 | 0° (Referenz = leichtes Rauschen), 1,5°, 3°, 4,5° |
| `aufloesung` | 4 | 300 (Referenz), 200, 150 dpi als PDF-Scan; 100 dpi als PNG-Datei |
| `jpeg` | 2 | Police als JPEG mit Qualität 20, Portal-Screenshot als JPEG mit Qualität 25 |
| `drehung` | 1 | Scan um 90° im Uhrzeigersinn gedreht |
| `tabelle` | 2 | Beitragsmitteilung: Spaltenköpfe über den Werten, Beitragsverlauf mit 3 Zeilen (digital + Scan) |
| `verwechslung` | 2 | Vertragsnummer „1011010-1“, Antragsnummer „0110100111“, Name „Olli Illner“, „Ilmweg 11“, IBAN mit vielen 0/1 (digital + Scan) |
| `mehrseitig` | 3 | Police 3 Seiten (digital + Scan 150 dpi), Standmitteilung 2 Seiten als reiner Scan |

Manche Dateien zählen in zwei Kategorien, etwa die Referenz-Scans. In der Zeile „Gesamt“ zählt jede Datei nur einmal.

## 3. Umgebung

| | |
|---|---|
| Datum | 13.11.2026 |
| Rechner | Linux-Container (x86_64), 4 CPU-Kerne, 15,7 GB RAM, **keine GPU** |
| Software | Python 3.13.18, Tesseract 5.3.4 (`deu+eng`, `osd`), PyMuPDF 2.1 (MuPDF 1.28.6) |
| App-Einstellungen | Standard (`Settings()`): OCR-Backend `tesseract`, 300 dpi, Zeitgrenze 240 s je Seite |
| Code-Stand | Commit `0327d2d` plus nicht eingecheckte Änderungen paralleler Arbeiten (Stand der Messung) |
| Störungen | Auf demselben Rechner liefen gleichzeitig andere Testläufe. Die Systemlast lag bei 2,2 bis 4,8 (Hauptmessung) bzw. bis 12 (Vergleichslauf in Abschnitt 5). |

Hauptmessung:

```
python3 scripts/benchmark_extraction.py --set-dir tests/fixtures/benchmark \
    --omp-thread-limit 1 --repeat 3 --details --json ergebnis.json
```

`--omp-thread-limit 1` lässt Tesseract mit nur einem Rechenkern laufen. Der Grund steht in Abschnitt 5. `--repeat 3`: Jede Datei wurde 3-mal verarbeitet. Die Zeit ist der Median, der Speicher der Höchstwert. Die Feldergebnisse waren in allen Wiederholungen gleich, ebenso in einem zweiten vollständigen Lauf.

## 4. Ergebnisse (nur OCR)

### 4.1 Je Kategorie

| Kategorie | Dateien | Seiten (davon OCR) | Sollwerte | Richtig | Falsch | Fehlend | Sicher falsch | Unklar | Konflikt | Ø Zeit/Datei | Spitzen-RAM |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| digital | 4 | 6 (0) | 81 | 93,8 % | 0,0 % | 6,2 % | 0 | 0 | 0 | 0,04 s | 92 MB |
| rauschen | 3 | 3 (3) | 78 | 64,1 % | 2,6 % | 33,3 % | 2 | 0 | 0 | 2,85 s | 542 MB |
| schraeglage | 4 | 4 (4) | 104 | 96,2 % | 3,8 % | 0,0 % | 4 | 0 | 0 | 2,73 s | 445 MB |
| aufloesung | 4 | 4 (4) | 104 | 91,3 % | 5,8 % | 2,9 % | 4 | 3 | 0 | 2,43 s | 462 MB |
| jpeg | 2 | 2 (2) | 39 | 97,4 % | 2,6 % | 0,0 % | 1 | 1 | 0 | 1,63 s | 422 MB |
| drehung | 1 | 1 (1) | 26 | 0,0 % | 11,5 % | 88,5 % | 2 | 1 | 0 | 2,39 s | 481 MB |
| tabelle | 2 | 2 (1) | 24 | 58,3 % | 0,0 % | 41,7 % | 0 | 1 | 0 | 1,29 s | 483 MB |
| verwechslung | 2 | 2 (1) | 52 | 92,3 % | 7,7 % | 0,0 % | 3 | 1 | 0 | 1,19 s | 498 MB |
| mehrseitig | 3 | 8 (5) | 67 | 89,6 % | 6,0 % | 4,5 % | 2 | 4 | 0 | 3,47 s | 542 MB |
| **Gesamt** | **20** | **25 (19)** | **456** | **80,9 %** | **4,8 %** | **14,3 %** | **16** | **11** | **0** | **2,28 s** | **542 MB** |

Ohne die beiden Totalausfälle (`rauschen_hoch`, `drehung_90`) sind es 404 Sollwerte: 91,3 % richtig, 4,7 % falsch, 14 sicher falsch. Den Dokumenttyp erkannte die App bei 19 von 20 Dateien richtig. Die Ausnahme ist der stark verrauschte Scan, bei dem kein Text erkannt wurde.

### 4.2 Je Datei

| Datei-ID | Richtig | Falsch | Fehlend | Sicher falsch | Unklar | Zeit | RAM Spitze (vorher) |
|---|---:|---:|---:|---:|---:|---:|---:|
| digital_police_3seiten | 29/29 | 0 | 0 | 0 | 0 | 0,08 s | 92 (85) MB |
| digital_nachtrag | 14/14 | 0 | 0 | 0 | 0 | 0,03 s | 92 (92) MB |
| digital_tabelle | 7/12 | 0 | 5 | 0 | 0 | 0,03 s | 92 (92) MB |
| digital_verwechslung | 26/26 | 0 | 0 | 0 | 0 | 0,04 s | 92 (92) MB |
| rauschen_ohne | 25/26 | 1 | 0 | 1 | 0 | 2,06 s | 296 (92) MB |
| rauschen_niedrig | 25/26 | 1 | 0 | 1 | 0 | 2,35 s | 438 (136) MB |
| rauschen_hoch | 0/26 | 0 | 26 | 0 | 0 | 4,14 s | 542 (178) MB |
| schraeglage_1_5 | 25/26 | 1 | 0 | 1 | 0 | 2,75 s | 386 (186) MB |
| schraeglage_3_0 | 25/26 | 1 | 0 | 1 | 0 | 2,70 s | 420 (236) MB |
| schraeglage_4_5 | 25/26 | 1 | 0 | 1 | 0 | 3,11 s | 445 (244) MB |
| aufloesung_200 | 25/26 | 1 | 0 | 0 | 1 | 2,71 s | 454 (294) MB |
| aufloesung_150 | 20/26 | 3 | 3 | 2 | 2 | 2,44 s | 462 (302) MB |
| aufloesung_100 | 25/26 | 1 | 0 | 1 | 0 | 2,23 s | 406 (306) MB |
| jpeg_q20_police | 25/26 | 1 | 0 | 1 | 1 | 2,08 s | 422 (314) MB |
| jpeg_q25_portal | 13/13 | 0 | 0 | 0 | 0 | 1,18 s | 397 (329) MB |
| drehung_90 | 0/26 | 3 | 23 | 2 | 1 | 2,39 s | 481 (306) MB |
| tabelle_scan | 7/12 | 0 | 5 | 0 | 1 | 2,55 s | 483 (306) MB |
| verwechslung_scan | 22/26 | 4 | 0 | 3 | 1 | 2,34 s | 498 (335) MB |
| mehrseitig_police_scan | 23/29 | 3 | 3 | 1 | 4 | 6,15 s | 528 (346) MB |
| mehrseitig_standmitteilung_scan | 8/9 | 1 | 0 | 1 | 0 | 4,17 s | 542 (357) MB |

„Vorher“ ist der Arbeitsspeicher des Prozesses, bevor die Datei verarbeitet wurde.

## 5. Einordnung: Wo „nur OCR“ Probleme hat

**Digitale PDFs sind zuverlässig.** Alle Werte in Label-Wert-Zeilen wurden richtig erkannt, auch über 3 Seiten verteilt und mit verwechselbaren Zeichen. Bankdaten aus der Fußzeile des Versicherers wurden korrekt ignoriert.

**Tabellen mit Spaltenköpfen fehlen** (digital und Scan, je 5 von 12 Werten). Steht die Beschriftung *über* dem Wert und rechts daneben schon die nächste Beschriftung („Versicherungsbeginn | Ablauf | Tarif | Zahlweise“), behandelt die App das Feld als leer. Nur die letzte Spalte („Zahlweise“) wird gefunden. Auch der Beitragsverlauf mit drei Zeilen liefert keinen Wert und keinen Konflikt. Die App zeigt dazu den Hinweis „In der Quelle ist für dieses Feld kein Wert eingetragen.“ Das stimmt hier nicht.

**Typische OCR-Verwechslungen erscheinen als „Erkannt“.** Auf fast jeder ordentlichen Scanseite gab es genau einen sicher falschen Wert:

- „§ 3“ wurde zu „8 3“ oder „3 3“ (Produktname). Tesseract war sich dabei sehr sicher (Wortkonfidenz 0,95). Deshalb fällt der Fehler an der Konfidenz nicht auf.
- „Bäckerei“ wurde zu „Backerei“ (3-mal, Name des Versicherungsnehmers).
- Verwechslung l/I: „Ilmweg“ wurde zu „IImweg“ (2-mal), „Illner“ zu „Iliner“.
- Ein Wortfetzen vom diagonalen MUSTER-Wasserzeichen landete vor dem Wert, z. B. „eats Backstraße“ oder „‘SH Musterweg“. Das Fetzen-Wort hatte nur die Konfidenz 0,07–0,10. Weil die App den Mittelwert aller Wörter nimmt, blieb der Wert trotzdem „Erkannt“ (0,55–0,56).

Gut: Vertragsnummern, Daten und Beträge mit vielen 0/1 wurden auch im Scan richtig gelesen. Eine IBAN mit „O“ statt „0“ („DEO2 …“) wurde als „Unklar“ markiert, nicht als „Erkannt“.

**Schräglage bis 4,5° ist kein Problem.** Die Begradigung wirkt: 25 von 26 richtig, genau wie beim geraden Scan.

**Auflösung:** 300, 200 und 100 dpi lagen bei 25 von 26 richtig. 150 dpi lag mit 20 von 26 deutlich darunter. Das ist ein Einzelbefund: Diese Datei ist zusätzlich JPEG-komprimiert, und das Wasserzeichen kreuzt die Anschrift. Daraus folgt nicht, dass 150 dpi grundsätzlich schlechter ist als 100 dpi.

**Starkes Rauschen: Totalausfall.** Der Scan ist für Menschen gut lesbar, Tesseract liefert aber kein einziges Wort. Es entsteht nichts Falsches, aber auch nichts Brauchbares. In einem gesonderten Versuch (Tesseract direkt, ohne App) lieferte dasselbe Bild nach einem 3×3-Medianfilter 142 Wörter mit Konfidenz 0,88. Der Medianfilter ist ein einfacher Rauschfilter. Die App wendet ihn derzeit nicht an.

**90° gedreht: Totalausfall mit Fehlern.** Tesseract liest den gedrehten Text erstaunlich gut (Konfidenz 0,93). Deshalb startet die App die automatische Lageerkennung nicht, denn die greift nur unter 0,5. Die Wortpositionen bleiben gedreht, und Beschriftung und Wert passen nicht mehr zusammen. Ergebnis: 0 von 26 richtig und 2 sicher falsche Werte (Versicherer „handelt MUSTER“, Produkt „Musterallee“).

**Mehrseitige Scans** verhalten sich wie die einzelnen Seiten. Bei der Police mit 150 dpi fehlen PLZ/Ort und Währung, zwei Werte sind „Unklar“. Die Standmitteilung als reiner Scan wurde bis auf „Backerei“ richtig gelesen. Auch die Briefanschrift des Vermittlers wurde dort nicht als Kundenanschrift übernommen.

**Laufzeit und Arbeitsspeicher**

- Eine digitale PDF braucht etwa 0,04 s. Eine Scanseite braucht 2–3 s, der Portal-Screenshot 1,2 s, die 3-seitige Police 6,2 s.
- Spitzen-RAM inklusive Tesseract: 92 MB bei digitalen PDFs, bis 542 MB bei Scans. Über den ganzen Lauf stieg der Grundbedarf des Prozesses von 85 auf 357 MB. Ob das ein Speicherleck ist oder der Prozess nur Speicher behält, wurde nicht untersucht.
- **CPU-Last ist ein echtes Risiko.** In der Standardeinstellung der App nutzt Tesseract alle Kerne (OpenMP). Ein Vergleichslauf ohne `--omp-thread-limit` lief, während andere Prozesse Tesseract nutzten (Systemlast 8–12). Ergebnis:
  - Zwei Seiten liefen in die Zeitgrenze von 240 s und blieben leer (`aufloesung_100`, `jpeg_q20_police`, je 26 fehlende Werte).
  - Der Portal-Screenshot brauchte 143 s statt 1,2 s.
  - Gesamt fielen dadurch nur 70,0 % statt 80,9 % richtig aus.
- Gesonderter Versuch mit einer Seite und Tesseract direkt:

  | Bedingung | Standard (alle Kerne) | Ein Kern (`OMP_THREAD_LIMIT=1`) |
  |---|---:|---:|
  | Rechner ohne Last | 1,47 s | 1,57 s |
  | 4 voll ausgelastete Hintergrundprozesse | 38,5 s / 248,0 s (zwei Läufe) | 2,47 s |

  Ohne Last ist ein Kern also kaum langsamer, unter Last aber um ein Vielfaches stabiler.

## 6. Vergleich mit der lokalen KI

**Nicht gemessen – in der Build-Umgebung war kein Netzwerkzugang zu Ollama/Modellen; Messung auf Zielhardware mit: `python3 scripts/benchmark_extraction.py --with-vlm`**

Optional mit `--ollama-url http://127.0.0.1:11434` und `--model qwen3-vl:4b`. Das Skript spricht nur Loopback-Adressen an, der Netzwerk-Wächter der App ist aktiv. Ist kein Ollama-Dienst mit dem Modell erreichbar, meldet es „übersprungen: lokale KI nicht verfügbar“ und misst nur OCR.

Mit KI verfährt das Skript wie die App: Die KI prüft nur Felder, die fehlen, unklar sind oder einen Konflikt haben (`VlmExtractor` + `reconcile`). Die Tabelle zeigt dann ein zweites Verfahren `ocr_vlm` mit denselben Kennzahlen. Dieses Dokument behauptet **keine** Verbesserung durch die KI, solange diese Messung fehlt.

Auf Zielhardware lohnt sich der Blick auf die Kategorien `rauschen`, `drehung` und `tabelle`, wo „nur OCR“ ausfällt, und auf die Spalte „Sicher falsch“. Die KI ergänzt keine Felder, die schon „Erkannt“ sind. Sicher falsche OCR-Werte bleiben daher auch mit KI bestehen.

## 7. Grenzen dieser Messung

- **Synthetische Daten:** saubere Schrift (Liberation Sans), bekannte Layouts, künstliches Rauschen. Echte Dokumente haben andere Schriften, Stempel, Unterschriften, Knicke und Handschrift. Die Zahlen sind **keine** Aussage über die Genauigkeit bei echten Dokumenten.
- **Kleiner Satz:** 20 Dateien, viele davon auf Basis derselben Policenseite. Einzelne Werte (z. B. 150 dpi) können Zufall sein.
- **Rückwirkung:** Ein Teil der Layouts stammt aus dem Musterfall, an dem die Extraktionsregeln entwickelt wurden. Für neue Layouts (Police 3 Seiten, Tabelle, Verwechslung) sind die Ergebnisse daher aussagekräftiger.
- **Geteilter Rechner:** Laufzeiten sind unter Fremdlast gemessen und eher zu hoch.
- **Je Datei gemessen:** Das Zusammenführen mehrerer Dateien eines Vorgangs, z. B. Police + Nachtrag mit Konflikt beim Beitrag, ist nicht Teil dieses Benchmarks.

## 8. Selbst messen

```
# Testsatz im Temp-Ordner erzeugen und messen (Markdown auf dem Bildschirm)
python3 scripts/benchmark_extraction.py

# Mitgelieferten Satz verwenden, Ergebnis als JSON und Markdown speichern
python3 scripts/benchmark_extraction.py --set-dir tests/fixtures/benchmark --json b.json --markdown b.md

# Nur eine Kategorie oder einzelne Dateien, mit Soll-/Ist-Werten je Feld
python3 scripts/benchmark_extraction.py --category rauschen --details
python3 scripts/benchmark_extraction.py --only drehung_90,tabelle_scan --details

# Testsatz neu erzeugen (deterministisch)
python3 scripts/benchmark_extraction.py --set-dir tests/fixtures/benchmark --regenerate --generate-only
```

Weitere Schalter: `--repeat N` (Median der Laufzeit), `--omp-thread-limit 1`, `--no-memory`, `--seed N`. Tests: `python3 -m pytest tests/test_benchmark` (der Tesseract-Test ist als `slow` markiert).

## 9. Gefundene Schwachstellen (für die Entwicklung)

Die Fehler sind hier nur dokumentiert, nicht behoben. Jeder lässt sich mit `--only <Datei-ID> --details` nachstellen.

1. **Lage-Erkennung bei 90° gedrehten Seiten** (`ocr/tesseract_backend.py`, `TesseractBackend.recognize`): Die Lageerkennung (OSD) läuft nur bei mittlerer Konfidenz < 0,5. Tesseract liest gedrehte Seiten aber mit 0,93. Datei `drehung_90`: Soll = Drehung 90°, aufgerichtete Koordinaten. Ist = Drehung 0, 0/26 richtig, 2 sicher falsch.
2. **Kein Rauschfilter** (`ocr/preprocess.py` / `TesseractBackend.recognize`): Datei `rauschen_hoch` liefert 0 Wörter. Ein 3×3-Medianfilter ergab im Versuch 142 Wörter mit Konfidenz 0,88. Vorschlag: zweiter Versuch mit gefiltertem Bild, wenn die Seite leer oder schwach bleibt.
3. **Tesseract ohne Thread-Begrenzung** (`ocr/tesseract_backend.py`, `TesseractBackend._run`): Unter CPU-Last bis 248 s je Seite, dann Zeitgrenze und leere Seite. Mit `OMP_THREAD_LIMIT=1` waren es 2,5 s. Vorschlag: Variable beim Aufruf von `subprocess.run` setzen, falls nicht vom Nutzer vorgegeben.
4. **Tabellen mit Spaltenköpfen** (`extraction/layout.py`, `_classify`): Steht rechts neben einer Beschriftung die nächste Beschriftung (`right.first_is_label`), wird das Feld als leer markiert, ohne unter der Beschriftung zu suchen. Dateien `digital_tabelle`, `tabelle_scan`: Versicherungsbeginn, Ablauf, Tarif und Beitrag fehlen. Dazu der irreführende Hinweis „In der Quelle ist für dieses Feld kein Wert eingetragen.“ (`extraction/merge.py`, `_placeholder_note`).
5. **Unsichere Wortfetzen im Wert** (`extraction/extractor.py`, `_word_confidence` / `_DocExtractor._convert`): Die Konfidenz ist der Mittelwert der Wörter. Ein vorangestelltes Fetzen-Wort mit 0,07 bleibt im Wert, und der Status bleibt „Erkannt“ („‘SH Musterweg“, „eats Backstraße“). Vorschlag: Fetzen-Wörter am Rand mit sehr geringer Konfidenz abtrennen oder das Minimum statt des Mittelwerts verwenden.
6. **IBAN mit O statt 0** (`extraction/normalize.py`, `normalize_iban`): „DEO2 1001 0010 0006 8201 01“ wird nicht normalisiert und bleibt „Unklar“. Das ist sicher, aber unnötig. Vorschlag: O→0 und I/l→1 an Ziffernpositionen nur dann ersetzen, wenn danach die Prüfziffer stimmt, und den Wert weiter als „Unklar“ mit Hinweis markieren.
