# Benchmark: Texterkennung und Datenextraktion

**Kurz gesagt:**

- Testsatz: **20 synthetische Dateien** (25 Seiten, 456 Sollwerte), alle mit „MUSTER – KEINE ECHTDATEN“ gekennzeichnet. Die Zahlen gelten **nur für diesen Testsatz**, nicht für echte Dokumente.
- **Nur OCR** (PDF-Textebene + Tesseract + Regeln), Stand 13.11.2026 nach den Korrekturen: **97,6 % richtig, 1,5 % falsch, 0,9 % fehlend.**

  | Kennzahl | vorher | jetzt |
  |---|---:|---:|
  | Richtig | 80,9 % | **97,6 %** |
  | Falsch | 4,8 % | **1,5 %** |
  | Fehlend | 14,3 % | **0,9 %** |
  | Sicher falsch (falsch und ohne Warnung) | 16 | **2** |
  | Unklar (App warnt selbst) | 11 | 15 |
  | Konflikt (Mensch entscheidet) | 0 | 2 |

- **Behoben:** um 90° gedrehter Scan (0 → 26 von 26), stark verrauschter Scan (0 → 26 von 26), Tabellen mit Spaltenköpfen (7 → 11 von 12), „§“ als „8“ gelesen, Wasserzeichen-Reste im Wert, IBAN mit „O“ statt „0“.
- **Verbleibend:** Der Umlaut in „Bäckerei“ wird in 2 Dateien als „Backerei“ gelesen und erscheint als „Erkannt“. Das lässt sich aus dem Bild allein nicht sicher erkennen.
- **Lokale KI auf CPU** (gesondert gemessen, [Abschnitt 6](#6-lokale-ki-echte-messung-auf-cpu)): `glm-ocr` 38–43 s je Seite, 26 von 26 Feldern; `qwen3-vl:4b-instruct` ca. 250–256 s je Seite; die Denkvariante `qwen3-vl:4b` ist ohne Grafikkarte nicht nutzbar.
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
| Rechner | Linux-Container (x86_64), 4 CPU-Kerne, 15 GB RAM, **keine GPU** |
| Software | Python 3.13.18, Tesseract 5.3.4 (`deu+eng`, `osd`), PyMuPDF 2.1 (MuPDF 1.28.6) |
| App-Einstellungen | Standard (`Settings()`): OCR-Backend `tesseract`, 300 dpi, Zeitgrenze 240 s je Seite |
| Code-Stand | Commit `ae2c686` |
| Systemlast | 0,7 bis 0,9 (1-Minuten-Mittel) – der Rechner war sonst unbelastet |

Messung:

```
OMP_THREAD_LIMIT=1 python3 scripts/benchmark_extraction.py --json ergebnis.json
```

Die App lässt Tesseract inzwischen selbst mit einem Rechenkern laufen (`OMP_THREAD_LIMIT=1`), solange die Umgebung nichts anderes vorgibt. Der Grund steht in Abschnitt 5. Die Feldergebnisse waren in zwei vollständigen Läufen gleich. Die Messung „vorher“ stammt vom selben Tag und Rechner (Commit `0327d2d`, dort unter Fremdlast und mit `--repeat 3`).

## 4. Ergebnisse (nur OCR)

### 4.1 Je Kategorie

| Kategorie | Dateien | Seiten (davon OCR) | Sollwerte | Richtig | Falsch | Fehlend | Sicher falsch | Unklar | Konflikt | Ø Zeit/Datei | Spitzen-RAM | Richtig vorher |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| digital | 4 | 6 (0) | 81 | 98,8 % | 0,0 % | 1,2 % | 0 | 0 | 1 | 0,04 s | 106 MB | 93,8 % |
| rauschen | 3 | 3 (3) | 78 | 100,0 % | 0,0 % | 0,0 % | 0 | 0 | 0 | 3,82 s | 340 MB | 64,1 % |
| schraeglage | 4 | 4 (4) | 104 | 100,0 % | 0,0 % | 0,0 % | 0 | 0 | 0 | 2,80 s | 349 MB | 96,2 % |
| aufloesung | 4 | 4 (4) | 104 | 97,1 % | 1,9 % | 1,0 % | 0 | 5 | 0 | 2,45 s | 349 MB | 91,3 % |
| jpeg | 2 | 2 (2) | 39 | 97,4 % | 2,6 % | 0,0 % | 1 | 1 | 0 | 1,85 s | 304 MB | 97,4 % |
| drehung | 1 | 1 (1) | 26 | 100,0 % | 0,0 % | 0,0 % | 0 | 0 | 0 | 6,06 s | 349 MB | 0,0 % |
| tabelle | 2 | 2 (1) | 24 | 91,7 % | 0,0 % | 8,3 % | 0 | 2 | 2 | 1,26 s | 347 MB | 58,3 % |
| verwechslung | 2 | 2 (1) | 52 | 94,2 % | 5,8 % | 0,0 % | 0 | 4 | 0 | 1,21 s | 353 MB | 92,3 % |
| mehrseitig | 3 | 8 (5) | 67 | 97,0 % | 1,5 % | 1,5 % | 1 | 3 | 0 | 3,78 s | 366 MB | 89,6 % |
| **Gesamt** | **20** | **25 (19)** | **456** | **97,6 %** | **1,5 %** | **0,9 %** | **2** | **15** | **2** | **2,71 s** | **366 MB** | **80,9 %** |

Den Dokumenttyp erkannte die App bei allen 20 Dateien richtig (vorher 19).

### 4.2 Je Datei

| Datei-ID | Richtig | Falsch | Fehlend | Sicher falsch | Unklar | Konflikt | Zeit | Spitzen-RAM |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| digital_police_3seiten | 29/29 | 0 | 0 | 0 | 0 | 0 | 0,08 s | 104 MB |
| digital_nachtrag | 14/14 | 0 | 0 | 0 | 0 | 0 | 0,03 s | 106 MB |
| digital_tabelle | 11/12 | 0 | 1 | 0 | 0 | 1 | 0,03 s | 106 MB |
| digital_verwechslung | 26/26 | 0 | 0 | 0 | 0 | 0 | 0,04 s | 106 MB |
| rauschen_ohne | 26/26 | 0 | 0 | 0 | 0 | 0 | 2,39 s | 290 MB |
| rauschen_niedrig | 26/26 | 0 | 0 | 0 | 0 | 0 | 2,15 s | 299 MB |
| rauschen_hoch | 26/26 | 0 | 0 | 0 | 0 | 0 | 6,92 s | 340 MB |
| schraeglage_1_5 | 26/26 | 0 | 0 | 0 | 0 | 0 | 2,41 s | 316 MB |
| schraeglage_3_0 | 26/26 | 0 | 0 | 0 | 0 | 0 | 3,52 s | 325 MB |
| schraeglage_4_5 | 26/26 | 0 | 0 | 0 | 0 | 0 | 3,12 s | 349 MB |
| aufloesung_200 | 25/26 | 1 | 0 | 0 | 3 | 0 | 2,78 s | 349 MB |
| aufloesung_150 | 24/26 | 1 | 1 | 0 | 2 | 0 | 2,60 s | 345 MB |
| aufloesung_100 | 26/26 | 0 | 0 | 0 | 0 | 0 | 2,27 s | 283 MB |
| jpeg_q20_police | 25/26 | 1 | 0 | 1 | 1 | 0 | 2,32 s | 304 MB |
| jpeg_q25_portal | 13/13 | 0 | 0 | 0 | 0 | 0 | 1,39 s | 279 MB |
| drehung_90 | 26/26 | 0 | 0 | 0 | 0 | 0 | 6,06 s | 349 MB |
| tabelle_scan | 11/12 | 0 | 1 | 0 | 2 | 1 | 2,50 s | 347 MB |
| verwechslung_scan | 23/26 | 3 | 0 | 0 | 4 | 0 | 2,37 s | 353 MB |
| mehrseitig_police_scan | 28/29 | 0 | 1 | 0 | 3 | 0 | 6,71 s | 356 MB |
| mehrseitig_standmitteilung_scan | 8/9 | 1 | 0 | 1 | 0 | 0 | 4,57 s | 366 MB |

### 4.3 Alle verbleibenden Abweichungen

| Datei-ID | Feld | Soll | Ist | Status in der App |
|---|---|---|---|---|
| jpeg_q20_police | Name Versicherungsnehmer | Muster Bäckerei GmbH | Muster Backerei GmbH | **Erkannt** (sicher falsch) |
| mehrseitig_standmitteilung_scan | Name Versicherungsnehmer | Muster Bäckerei GmbH | Muster Backerei GmbH | **Erkannt** (sicher falsch) |
| aufloesung_200 | Tarif | DV-Klassik (MUSTER) | -DV-Klassik | Unklar |
| aufloesung_150 | Beitrag | 150,00 | nicht lesbar | Unklar |
| verwechslung_scan | Kontoinhaber | Olli Illner | Olli Iliner | Unklar |
| verwechslung_scan | Straße (versicherte Person, Versicherungsnehmer) | Ilmweg | IImweg | Unklar |
| digital_tabelle, tabelle_scan | Beitrag | 140,00 | mehrere Beträge im Beitragsverlauf | Konflikt (Mensch entscheidet) |
| aufloesung_150, mehrseitig_police_scan | Währung | EUR | – | Fehlt |

Bei 9 von 11 Abweichungen warnt die App selbst (Unklar, Konflikt oder Fehlt). Ohne Warnung bleiben nur die beiden Umlaut-Fälle.

## 5. Einordnung

**Digitale PDFs sind zuverlässig.** 80 von 81 Werten richtig, kein falscher Wert. Der eine offene Wert ist der Beitrag in der Tabelle mit Beitragsverlauf: Dort stehen drei verschiedene Beträge, die App meldet einen Konflikt und rät nicht.

**Was die Korrekturen bewirkt haben**

| Problem vorher | Maßnahme | Ergebnis |
|---|---|---|
| 90° gedrehter Scan: 0 von 26, dazu 2 sicher falsche Werte | Lageerkennung (Tesseract OSD) auch dann, wenn die Wörter hochkant stehen. Die Fundstelle wird in die angezeigte Seite zurückgerechnet. | 26 von 26 |
| Stark verrauschter Scan: kein einziges Wort | Bleibt das Ergebnis schwach, folgt ein zweiter Versuch mit einem Medianfilter (einfacher Rauschfilter). Das bessere Ergebnis zählt. | 26 von 26 |
| Tabellen: Werte unter Spaltenköpfen fehlten (5 von 12) | Steht rechts neben einer Beschriftung die nächste Beschriftung, sucht die App den Wert darunter in derselben Spalte. | 11 von 12 |
| „§ 3“ als „8 3“ oder „3 3“ gelesen (vorher der häufigste sicher falsche Wert) | In Gesetzesverweisen („gemäß … Nr. … EStG“) wird das Paragrafenzeichen wiederhergestellt. | 0 Fälle |
| Wortfetzen vom Wasserzeichen im Wert („eats Backstraße“) | Allein stehende, sehr unsichere Wortfetzen werden nicht als Wert gelesen. | 0 Fälle |
| IBAN „DEO2 …“ blieb ungelesen | „O“→„0“ und „I“/„l“→„1“ nur an Ziffernstellen und **nur**, wenn danach die Prüfziffer stimmt. Der Wert bleibt „Unklar“ mit Hinweis. | richtig, Status Unklar |
| „IImweg“ statt „Ilmweg“ als „Erkannt“ | Die App korrigiert nicht, markiert solche Werte aber immer als „Unklar“. | falsch, aber mit Warnung |
| Tesseract lief unter CPU-Last bis in die Zeitgrenze (240 s, leere Seite) | Tesseract läuft mit einem Rechenkern. | siehe unten |

**Was bleibt:** Verlorene Umlaut-Punkte („Bäckerei“ → „Backerei“) erkennt die App nicht. Tesseract ist sich dabei sicher, und „Backerei“ ist kein offensichtlicher Lesefehler. Namen müssen deshalb immer mit der Quelle verglichen werden.

**Schräglage bis 4,5°, Auflösung 100–300 dpi, starke JPEG-Kompression:** jeweils 24 bis 26 von 26 Werten richtig.

**Laufzeit und Arbeitsspeicher**

- Eine digitale PDF braucht etwa 0,04 s, eine Scanseite 2–3,5 s. Gedrehte und stark verrauschte Seiten brauchen wegen des zweiten Versuchs 6–7 s.
- Spitzen-RAM inklusive Tesseract: rund 106 MB bei digitalen PDFs, bis 366 MB bei Scans.
- **CPU-Last** (gemessen vor der Korrektur): Ohne Begrenzung nutzte Tesseract alle Kerne. Unter Fremdlast liefen zwei Seiten in die Zeitgrenze von 240 s und blieben leer. Gesonderter Versuch mit einer Seite und Tesseract direkt:

  | Bedingung | alle Kerne | ein Kern (`OMP_THREAD_LIMIT=1`) |
  |---|---:|---:|
  | Rechner ohne Last | 1,47 s | 1,57 s |
  | 4 voll ausgelastete Hintergrundprozesse | 38,5 s / 248,0 s (zwei Läufe) | 2,47 s |

  Ohne Last ist ein Kern kaum langsamer, unter Last aber um ein Vielfaches stabiler. Deshalb ist ein Kern jetzt die Voreinstellung.

## 6. Lokale KI: echte Messung auf CPU

**Gemessen am 13.11.2026** im selben Container (4 CPU-Kerne, 15 GB RAM, **keine GPU**). Ollama lief lokal, nur über `127.0.0.1`, mit `OLLAMA_NO_CLOUD=1`. Testdokument: der synthetische Scan `MUSTER_Police_Scan.pdf` (1 Seite, 26 Sollfelder).

| Modell | Rolle in der App | Zeit je Seite | Ergebnis |
|---|---|---:|---|
| `glm-ocr` | aktives Modell (Voreinstellung): liest den Text, die App-Regeln ordnen ihn den Feldern zu | 38–43 s | **26 von 26** Feldern richtig |
| `qwen3-vl:4b-instruct` | Ausweichmodell (Voreinstellung): liest Felder direkt aus dem Seitenbild | ca. 250–256 s | 6 von 8 abgefragten Feldern richtig |
| `qwen3-vl:4b` (Denkvariante) | wählbar, nicht Voreinstellung | 345 s und 370 s | Antwort beide Male am Token-Limit abgeschnitten, **0 Felder nutzbar** |

- **`glm-ocr` im ganzen App-Ablauf:** Auf dem Scan ergänzte die KI 9 zuvor fehlende Felder und erzeugte 2 Konflikte. Über Konflikte entscheidet der Mensch, die KI überschreibt nichts still. Die echte Modellausgabe liegt als Testdatei bei (`tests/fixtures/synthetic/glm_ocr_ausgabe_police_scan_s1.txt`), der Test `test_real_glm_ocr_output_is_fully_extracted` sichert die 26 von 26 ab.
- **Denkvariante:** Ohne Grafikkarte nicht nutzbar. Die App kennzeichnet sie als „nur mit Grafikkarte empfohlen“.
- **Arbeitsspeicher:** Zwei Bildmodelle gleichzeitig mit 16.384 Token Kontext führten zum Speicherüberlauf. Die App fragt deshalb mit 8.192 Token Kontext an, lässt immer nur ein Modell über alle Dokumente laufen und entlädt es danach. Auf Rechnern mit 16 GB empfiehlt sich zusätzlich `OLLAMA_MAX_LOADED_MODELS=1`.

**Nicht gemessen:**

- Zeiten mit Grafikkarte oder auf Apple-Chips (M-Serie). Dort ist deutlich mehr Tempo zu erwarten, belegt ist das hier nicht.
- `gemma4:e4b` (Gegenprüfung im Profil „Maximal“).
- Die KI über den ganzen Testsatz mit 20 Dateien. Auf CPU dauert das bei rund 4 Minuten je Seite entsprechend lange. Selbst messen: `python3 scripts/benchmark_extraction.py --with-vlm --model qwen3-vl:4b-instruct --vlm-timeout 600`.

Mit `--with-vlm` verfährt das Skript wie die App: Die KI prüft nur Felder, die fehlen, unklar sind oder einen Konflikt haben. Sicher falsche OCR-Werte wie „Backerei“ bleiben daher auch mit KI bestehen. Das Skript spricht nur Loopback-Adressen an.

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

Weitere Schalter: `--repeat N` (Median der Laufzeit), `--omp-thread-limit 1`, `--no-memory`, `--seed N`, `--vlm-timeout S`. Tests: `python3 -m pytest tests/test_benchmark` (der Tesseract-Test ist als `slow` markiert).

## 9. Stand der gefundenen Schwachstellen

| Nr. | Schwachstelle | Stand |
|---|---|---|
| 1 | Lageerkennung bei 90° gedrehten Seiten | behoben (`ocr/tesseract_backend.py`) |
| 2 | Kein Rauschfilter | behoben (`ocr/preprocess.py`, `denoise_median`) |
| 3 | Tesseract ohne Thread-Begrenzung | behoben (`subprocess_env`) |
| 4 | Tabellen mit Spaltenköpfen | behoben (`extraction/layout.py`); Beitragsverlauf mit mehreren Beträgen bleibt bewusst ein Konflikt |
| 5 | Unsichere Wortfetzen im Wert | behoben (`drop_noise_fragments`) |
| 6 | IBAN mit O statt 0 | behoben (`repair_iban_ocr`), Wert bleibt „Unklar“ |
| 7 | „§“ als Ziffer gelesen | behoben (`fix_paragraph_sign`) |
| 8 | I/l-Verwechslung („IImweg“) | entschärft: immer „Unklar“, keine automatische Korrektur |
| 9 | Verlorene Umlaute („Backerei“) | **offen** – nicht sicher erkennbar |

Abgesichert durch `tests/test_benchmark/test_bench_ocr_regression.py` und `tests/test_extraction/test_ext_ocr_confusions.py`.
