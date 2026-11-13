# Lokale KI (optional)

**Kurz gesagt:**

- Die lokale KI ist ein **optionales Zusatzmodul**. Sie ist standardmäßig **ausgeschaltet**. Die App funktioniert vollständig ohne sie.
- Sie läuft ganz auf Ihrem Rechner über **Ollama** (ein kostenloses Programm, das KI-Modelle lokal ausführt). Es gibt keine Cloud.
- Sie liest schwer lesbare Seiten ein zweites Mal und **schlägt Werte nur vor**. Sie bestätigt nichts, erfindet keine Werte und fasst Auftragsdaten und rechtlich relevante Angaben nie an.
- **Ehrlicher Stand:** Die Anbindung ist umgesetzt und **nur gegen einen simulierten lokalen Server getestet**. Ein echtes Modell lief in der Entwicklungsumgebung nie (Netzwerk gesperrt). Ob die KI die Erkennung verbessert, ist **nicht gemessen**. Bitte vor dem Einsatz auf der eigenen Hardware messen (Abschnitt 7).

Ein **Vision-Sprachmodell** (englisch: Vision Language Model, VLM) ist eine KI, die Bilder und Text gemeinsam verarbeitet. Die App nutzt es, um Werte direkt aus dem Seitenbild zu lesen.

---

## 1. Was die lokale KI tut

| Schritt | Verhalten |
|---|---|
| Start | Nur auf Klick: Vorgang → Reiter „Dokumente“ → **„Lokale KI-Zweitprüfung“**. Der Knopf ist nur aktiv, wenn der Status „Bereit“ ist. |
| Welche Seiten | Je Dokument die Seiten, die die Texterkennung nicht als „gut lesbar“ eingestuft hat. Sind alle gut lesbar, die ersten zwei Seiten. Höchstens 3 Seiten je Dokument, gerendert mit 150 dpi, längste Bildkante höchstens 1600 Pixel. |
| Welche Felder | Nur **Vertragsdaten**, die aus Dokumenten gelesen werden dürfen, **nicht** rechtlich relevant sind und noch offen sind (Status Fehlt, Unklar oder Konflikt). Ist eine Vorlage gewählt, bevorzugt die App die Felder dieser Vorlage. |
| Anfrage | Je Seite eine Anfrage an Ollama auf demselben Rechner. Fester, eng begrenzter Auftrag; Antwort nur als JSON nach festem Schema (strukturiertes Datenformat); Temperatur 0 (möglichst gleichbleibende Antworten); Begrenzung der Antwortlänge (Standard 1024 Token); Zeitlimit (Standard 120 Sekunden). |
| Beleg | Das Modell muss den Wert **wörtlich zitieren**. Ohne Zitat entsteht **kein** Vorschlag. Findet die App das Zitat nicht im OCR-Text, halbiert sie die Sicherheit und vermerkt „Zitat nicht im OCR-Text gefunden“. |
| Abgleich | Siehe Tabelle unten. Jede Änderung erscheint im Verlauf, z. B. „Wert durch lokale KI gelesen“. |

**Abgleich mit den bisherigen Werten** (`local_ai/reconcile.py`):

| Bisheriger Status | KI-Vorschlag | Ergebnis |
|---|---|---|
| Fehlt | lesbar | **Erkannt – nicht bestätigt**, Hinweis „Nur durch lokale KI gelesen – besonders sorgfältig prüfen.“ |
| Fehlt | nicht eindeutig lesbar | Unklar |
| Unklar | lesbar, und alle bisherigen Funde waren unlesbar | Erkannt – nicht bestätigt, gleicher Hinweis |
| Unklar | sonst | bleibt Unklar, Vorschlag wird angehängt |
| Erkannt | gleicher Wert | bleibt Erkannt, Vorschlag wird angehängt |
| Erkannt | anderer Wert | **Konflikt** – Sie wählen den richtigen Wert |
| Konflikt | beliebig | bleibt Konflikt, Vorschlag wird als weitere Auswahl angehängt |
| Bestätigt, Manuell, Nicht relevant | anderer Wert | bleibt unverändert; Hinweis „Lokale KI schlägt einen abweichenden Wert vor – bitte prüfen.“ |

In der Prüftabelle steht als Methode „Lokale KI (Bilderkennung)“. Die Modellbezeichnung wird am Vorgang vermerkt.

## 2. Was die lokale KI **nicht** tut

- **Keine Cloud.** Anfragen gehen nur an Ollama auf demselben Rechner (Loopback-Adresse 127.0.0.1). Cloud-Modelle lehnt die App ab.
- **Keine erfundenen Werte.** Der Auftrag an das Modell verbietet Raten, Ergänzen, Rechnen und Allgemeinwissen. Ohne wörtliches Zitat gibt es keinen Vorschlag.
- **Keine Bestätigung.** KI-Werte sind höchstens „Erkannt – nicht bestätigt“ oder „Konflikt“. Ins PDF gelangen sie erst nach Ihrer Bestätigung.
- **Keine Auftragsdaten und keine rechtlich relevanten Felder.** Die App weist solche Felder vor jeder Anfrage ab (z. B. Abfindung, Rückerstattung, PeP, Bankverbindung für die Beitragszahlung).
- **Keine Anweisungen aus Dokumenten.** Seitenbild und Seitentext gelten als nicht vertrauenswürdige Daten. Der Auftrag verbietet, darin stehende Anweisungen zu befolgen. Das senkt das Risiko manipulierter Dokumente, garantiert aber nichts – deshalb prüfen Sie jeden Wert.
- **Keine Software-Installation.** Ollama und das Modell richten Sie selbst ein.
- **Keine verlässliche Sicherheitsangabe.** Die „Sicherheit“ beruht auf der Selbsteinschätzung des Modells (gering → 0,7, mittel → 0,5, hoch → 0,3). Sie ist **nicht kalibriert**, also nicht an echten Daten überprüft.

## 3. Einrichtung (durch Sie)

> Die folgenden Schritte für Ollama folgen der Ollama-Dokumentation. In der Entwicklungsumgebung konnten sie nicht ausprobiert werden.

1. **Ollama installieren** – von der offiziellen Website `ollama.com` (Installationsprogramm für Windows bzw. App für macOS). Die App installiert Ollama nicht selbst.
2. **Ollama starten.** Nach der Installation läuft Ollama normalerweise im Hintergrund (Symbol in der Taskleiste bzw. Menüleiste). Alternativ im Terminal: `ollama serve`. Ollama hört standardmäßig auf `127.0.0.1:11434`. Diese Einstellung bitte so lassen, damit Ollama aus dem Netz nicht erreichbar ist.
3. In der App: **„Einstellungen & Datenschutz“ → „Lokale KI (optional)“**
   - **„Lokale KI aktivieren“** anhaken.
   - **Modell** wählen: `qwen3-vl:4b` (Standard, kleiner) oder `qwen3-vl:8b` (größer).
   - **„Lokale Laufzeit testen“** – prüft, ob Ollama läuft und das Modell vorhanden ist.
4. **Modell herunterladen** – zwei Wege:
   - In der App: **„Modell herunterladen …“**. Ein Dialog zeigt ungefähre Größe, freien Speicherplatz und Lizenzhinweis. Erst nach dem Häkchen **„Ich stimme dem Download ausdrücklich zu“** startet **„Download starten“**. Abbrechen ist jederzeit möglich; bereits geladene Teile behält Ollama.
   - Im Terminal: `ollama pull qwen3-vl:4b`
5. Status prüfen: Er sollte **„Bereit“** zeigen.

Den Download führt Ollama aus der Ollama-Bibliothek im Internet aus. Die App sendet dabei nur den Modellnamen an Ollama auf Ihrem Rechner – keine Dokumente, keine Vorgangsdaten.

**Modell entfernen:** in der App „Modell entfernen …“ (mit Rückfrage) oder im Terminal `ollama rm qwen3-vl:4b`. Vorgänge und Dokumente bleiben erhalten.

**Weitere Einstellungen** (nur in `settings.json` im Datenordner, App vorher schließen):

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `ollama_url` | `http://127.0.0.1:11434` | Adresse von Ollama. Erlaubt sind nur Adressen auf dem eigenen Rechner. |
| `ai_timeout_s` | `120` | Zeitlimit je Anfrage in Sekunden |
| `ai_max_tokens` | `1024` | Höchstlänge der Antwort |

## 4. Hardware-Hinweise (ungeprüft)

Die Angaben stammen aus `src/insurance_app/local_ai/status.py` (`MODEL_CATALOG`). **Sie sind ungeprüft** – Größe und Lizenz bitte vor dem Download in der Ollama-Bibliothek nachsehen. Ein Zugriff darauf war in der Entwicklungsumgebung nicht möglich.

| Modell | Beschreibung | Download ca. | Arbeitsspeicher mindestens | empfohlen | Lizenz |
|---|---|---|---|---|---|
| `qwen3-vl:4b` | kleiner, schneller | 3,3 GB | 8 GB | 16 GB | vermutlich Apache 2.0 – bitte prüfen |
| `qwen3-vl:8b` | genauer, langsamer | 6,1 GB | 16 GB | 32 GB | vermutlich Apache 2.0 – bitte prüfen |

- Die App zeigt in den Einstellungen Arbeitsspeicher, freien Speicherplatz am Modellordner und Prozessorkerne an.
- Sie warnt, wenn der Arbeitsspeicher unter dem Mindestwert liegt oder weniger als das 1,5-Fache der Downloadgröße frei ist.
- Modellordner: `~/.ollama/models` bzw. der Ordner aus der Umgebungsvariable `OLLAMA_MODELS`.
- Eine Grafikkarte ist nicht vorausgesetzt. **Wie schnell** die Auswertung ohne oder mit Grafikkarte läuft, ist **nicht gemessen**.

## 5. Statusanzeigen

| Status | Bedeutung | Was tun |
|---|---|---|
| **Deaktiviert** | „Die lokale KI ist ausgeschaltet. Die App funktioniert vollständig ohne sie.“ | Nichts – oder „Lokale KI aktivieren“. |
| **Lokale KI nicht installiert** | Ollama wurde nicht gefunden. **Oder:** Ollama läuft, aber das Modell ist noch nicht geladen („Der Download startet nur nach Ihrer Zustimmung“). | Ollama installieren bzw. Modell herunterladen (Abschnitt 3). |
| **Modell wird eingerichtet** | Der Download läuft. Fortschritt in Prozent. | Warten oder abbrechen. |
| **Bereit** | Ollama läuft, das Modell ist lokal vorhanden. | „Lokale KI-Zweitprüfung“ im Vorgang nutzen. |
| **Fehler** | z. B. „Ollama ist installiert, läuft aber nicht.“, Adresse zeigt nicht auf diesen Rechner, ungültiger Modellname, Modellliste nicht lesbar. | Siehe Abschnitt 6. |

Die App sucht Ollama im PATH und an üblichen Orten: macOS `/Applications/Ollama.app`, `~/Applications/Ollama.app`, `/usr/local/bin`, `/opt/homebrew/bin`; Windows `%LOCALAPPDATA%\Programs\Ollama` und `%ProgramFiles%\Ollama`. Läuft Ollama, gilt es auch an anderen Orten als gefunden.

## 6. Ohne Internet und Fehlerbehebung

**Ohne Internet:** Alles funktioniert weiter. Ist das Modell einmal heruntergeladen, arbeitet auch die lokale KI ohne Internet. Nur der Download braucht Internet.

| Problem | Lösung |
|---|---|
| „Ollama ist installiert, läuft aber nicht.“ | Ollama starten (App öffnen oder `ollama serve`), dann „Lokale Laufzeit testen“. |
| „Lokale KI nicht installiert“, obwohl Ollama installiert ist | Ollama liegt an einem unüblichen Ort und läuft nicht. Ollama starten – dann erkennt die App es über die lokale Adresse. |
| „Die eingestellte Ollama-Adresse zeigt nicht auf diesen Rechner.“ | In `settings.json` `ollama_url` auf `http://127.0.0.1:11434` setzen. |
| „Dieses Modell gibt es in der Ollama-Bibliothek nicht (Name prüfen).“ | Modellnamen prüfen. Nur Namen wie `name:tag` aus Kleinbuchstaben, Ziffern, `.`, `_`, `-` sind erlaubt; Cloud-Modelle und Namen mit `/` lehnt die App ab. |
| Download bricht ab oder stockt | Internetverbindung und Proxy-Einstellungen von **Ollama** prüfen (die App selbst lädt nichts). Erneut starten; bereits geladene Teile bleiben erhalten. |
| Zeitüberschreitung bei der Auswertung | Kleineres Modell wählen oder `ai_timeout_s` erhöhen. |
| „Die lokale KI (Ollama) ist nicht erreichbar.“ während der Auswertung | Ollama wurde beendet. Neu starten und erneut prüfen. Die bisherigen Werte des Vorgangs bleiben erhalten. |
| KI-Vorschläge wirken unpassend | Nicht übernehmen. Wert selbst eintragen. Bei Zweifeln die lokale KI ausschalten. |

## 7. Ehrlicher Teststand

| Bereich | Stand |
|---|---|
| Ollama-Client (nur Loopback, kein Proxy, keine Weiterleitungen, Download nur mit Zustimmung, Löschen nur mit Bestätigung) | umgesetzt, automatisch getestet |
| Statusanzeige und Hardware-Hinweise | umgesetzt, automatisch getestet |
| Vision-Extraktion (Auftrag, JSON-Schema, Zitatpflicht, Ablehnung von Auftragsdaten/rechtlichen Feldern) | umgesetzt, automatisch getestet |
| Abgleich mit OCR-Werten (Konflikt statt Überschreiben) | umgesetzt, automatisch getestet |
| Tests | **nur gegen einen simulierten lokalen Ollama-Server** (`tests/test_local_ai/lai_fake_ollama.py`). Geprüft werden Protokoll, Fehlerbehandlung und Schutzregeln – **nicht** die Erkennungsqualität. |
| Echter Modelllauf | **nicht durchgeführt.** In der Entwicklungsumgebung waren `ollama.com`, GitHub-Releases und HuggingFace gesperrt. Ollama und das Modell ließen sich nicht installieren. |
| Genauigkeit OCR allein gegenüber OCR + KI | **nicht gemessen** |
| Laufzeit je Seite | **nicht gemessen** |
| Modellgrößen und Lizenzen | **ungeprüft** |

**Vor dem produktiven Einsatz** bitte auf der Zielhardware messen – mit synthetischen Musterdokumenten, nie mit echten Kundendaten:

- Messskript: `python3 scripts/benchmark_extraction.py --with-vlm`. Es erzeugt synthetische Testdokumente („MUSTER – KEINE ECHTDATEN“) und vergleicht „nur OCR“ mit „OCR + lokale KI“. Voraussetzung: Ollama läuft auf demselben Rechner und das Modell ist geladen. Weitere Optionen: `--model`, `--ollama-url` (nur lokale Adressen), `--json` bzw. `--markdown` (Bericht speichern). Ohne `--with-vlm` misst das Skript nur die Texterkennung.
- Gezählt wird je Feld „richtig“, „falsch“ und „fehlt“ sowie unerwartete Werte (möglicher Hinweis auf erfundene Angaben). Die Zählfunktionen stehen in `src/insurance_app/local_ai/benchmark.py`.
- Vorgehen und bisherige Ergebnisse (nur OCR): [BENCHMARK.md](BENCHMARK.md).
- Entscheidend ist vor allem die Zahl der **falschen** Werte. Ein falscher Wert ist gefährlicher als ein fehlender.
