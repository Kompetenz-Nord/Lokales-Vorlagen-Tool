# Lokale KI (optional)

**Kurz gesagt:**

- **Die App funktioniert ohne KI.** Die lokale KI ist ein Zusatz und standardmäßig ausgeschaltet.
- **Mit KI:** GLM-OCR liest schwierige Scans und Tabellen. Qwen3-VL springt ein, wenn GLM-OCR fehlt oder scheitert. Gemma 4 kann optional gegenprüfen.
- **Alles lokal, nichts geht in die Cloud.** Die App spricht nur mit Ollama auf demselben Rechner (Adresse `127.0.0.1`). Cloud-Modelle lehnt sie ab.
- **Sie entscheiden**, welches Modell aktiv läuft. Voreinstellung: `glm-ocr` aktiv, `qwen3-vl:4b-instruct` als Ausweichmodell.
- **KI-Werte werden nie automatisch bestätigt.** Sie erscheinen höchstens als „Erkannt – bitte prüfen“ oder als „Konflikt“. Auftragsdaten und rechtlich relevante Felder fasst die KI nie an.
- **Empfehlung:** Mac mit Apple Silicon und 16 GB oder Windows-PC mit 16 GB und NVIDIA-Grafikkarte → Profil „standard“. Ohne Grafikkarte läuft es auch, aber deutlich langsamer.
- **Ehrlicher Stand:** Automatische Tests laufen gegen einen simulierten Ollama-Server. In der Build-Umgebung lief zusätzlich ein echtes Ollama (nur Prozessor, keine Grafikkarte). Dort gemessen: `glm-ocr` 38–43 s je Seite, `qwen3-vl:4b-instruct` ca. 250–256 s je Seite. Mit Grafikkarte oder Apple Silicon ist deutlich mehr Tempo zu erwarten, gemessen wurde das nicht. Einzelheiten: [BENCHMARK.md](BENCHMARK.md#6-lokale-ki-echte-messung-auf-cpu).

---

## Inhalt

1. [Begriffe](#1-begriffe)
2. [Welches Modell wofür?](#2-welches-modell-wofür)
3. [So arbeiten die Modelle zusammen](#3-so-arbeiten-die-modelle-zusammen)
4. [Hardware: Was brauche ich für flüssigen Betrieb?](#4-hardware-was-brauche-ich-für-flüssigen-betrieb)
5. [Einrichtung](#5-einrichtung)
6. [Nur-lokal-Modus von Ollama](#6-nur-lokal-modus-von-ollama)
7. [Warum keine Cloud-Modelle?](#7-warum-keine-cloud-modelle)
8. [Einstellungen in der App](#8-einstellungen-in-der-app)
9. [Fehlerbehebung](#9-fehlerbehebung)
10. [Ehrlicher Teststand](#10-ehrlicher-teststand)
11. [Quellen](#11-quellen)

---

## 1. Begriffe

| Begriff | Bedeutung |
|---|---|
| **Ollama** | Kostenloses Programm, das KI-Modelle auf dem eigenen Rechner ausführt. Es läuft im Hintergrund und ist unter `http://127.0.0.1:11434` erreichbar (gleichbedeutend: `http://localhost:11434`). |
| **Modell** | Die eigentliche KI. Eine große Datei, die Ollama einmal herunterlädt und dann offline nutzt. |
| **OCR-Modell** | Liest Text aus dem Bild einer Seite (OCR = optische Zeichenerkennung). Hier: GLM-OCR. |
| **Vision-Sprachmodell (VLM)** | KI, die Bild und Text gemeinsam versteht. Sie kann Werte direkt aus dem Seitenbild lesen und Feldern zuordnen. Hier: Qwen3-VL und Gemma 4. |
| **Aktives Modell** | Das Modell, das zuerst läuft. Voreinstellung: `glm-ocr`. |
| **Ausweichmodell** | Springt ein, wenn das aktive Modell fehlt oder bei einem Dokument nichts liefert. Voreinstellung: `qwen3-vl:4b-instruct`. |
| **Gegenprüfung** | Optionales drittes Modell, das unabhängig nachliest (z. B. `gemma4:e4b`). Weicht es ab, entsteht ein Konflikt. |
| **Profil** | Vorschlag, welche Modelle zu Ihrem Rechner passen: „kompakt“, „standard“, „maximal“. |
| **Loopback** | Netzwerkadresse des eigenen Rechners (`127.0.0.1`). Daten verlassen den Rechner dabei nicht. |
| **GPU / VRAM** | Grafikkarte bzw. ihr eigener Speicher (Grafikspeicher). Mit passender Grafikkarte rechnen Modelle viel schneller. |
| **CPU** | Prozessor. Ohne passende Grafikkarte rechnet Ollama auf dem Prozessor. |
| **Strukturierte Ausgabe** | Das Modell antwortet nur als JSON nach festem Schema (fest vorgegebenes Datenformat). Die App verlangt das von Qwen3-VL und Gemma 4. |

---

## 2. Welches Modell wofür?

| Modell | Aufgabe in der App | Vorteil | Download (exakt) | Lizenz | Quelle |
|---|---|---|---|---|---|
| `glm-ocr` | **Aktives Modell** (Voreinstellung). Liest schwierige Seiten als Text, auch Tabellen. Die geprüften Regeln der App ordnen danach die Felder zu. | Klein und auf Dokumente und Tabellen spezialisiert (laut Anbieter Z.ai). Etwa 0,9 Mrd. Parameter (`ollama show`: 891 Mio. plus 434 Mio. für den Bildteil). | **2,66 GB** (2.655.025.740 Byte) | Im Ollama-Manifest **keine** Lizenzangabe – vor dem Einsatz beim Anbieter Z.ai prüfen | [Z.ai](https://docs.z.ai/guides/vlm/glm-ocr), [Hugging Face](https://huggingface.co/docs/transformers/main/en/model_doc/glm_ocr) |
| `qwen3-vl:4b-instruct` | **Ausweichmodell** (Voreinstellung). Liest Felder direkt aus dem Seitenbild, ohne vorgeschaltete Denkphase. Antwort als JSON, jeder Wert mit wörtlichem Zitat. | Versteht Layout und Bild gemeinsam und ordnet Werte direkt den Feldern zu. Antwortet direkt im geforderten Format und ist deshalb ohne Grafikkarte deutlich schneller als die Denkvariante. Etwa 4 Mrd. Parameter. | **3,34 GB**  | Apache 2.0 (laut Ollama-Manifest) | Ollama-Registry |
| `qwen3-vl:4b` | **Denkvariante** desselben Modells. Wählbar, aber keine Voreinstellung. | „Denkt“ vor der Antwort erst nach. Das kostet viel Zeit und Antwortlänge. In der App gekennzeichnet als „nur mit Grafikkarte empfohlen“. | **3,34 GB** (3.336.619.180 Byte) | Apache 2.0 (laut Ollama-Manifest) | Ollama-Registry, [Ollama: Thinking](https://docs.ollama.com/capabilities/thinking.md) |
| `gemma4:e4b` | **Gegenprüfung** (optional, Profil „maximal“). Liest dieselben Felder unabhängig. | Andere Modellfamilie: Fehler eines einzelnen Modells fallen eher auf, weil Abweichungen als Konflikt erscheinen. | **6,58 GB** | Apache 2.0 (laut Ollama-Manifest) | Ollama-Registry |

- Größen laut Ollama-Registry-Manifest, abgerufen am 13.11.2026. 1 GB = 10⁹ Byte. Für `glm-ocr` und `qwen3-vl:4b` stimmt die Bytezahl mit der lokalen Modellliste (`ollama list`) in der Build-Umgebung überein.
- `glm-ocr` und `qwen3-vl:4b` verlangen laut `ollama show` (Feld „requires“) mindestens **Ollama 0.30.0**. Für `gemma4:e4b` wurde das nicht geprüft.
- Download-Größe ist nicht gleich Speicherbedarf beim Rechnen. Beim Ausführen braucht das Modell zusätzlich Arbeitsspeicher für den Kontext (siehe Abschnitt 4).
- Die App kennt außerdem `qwen3-vl:8b` als Alternative. Größe und Lizenz dieses Modells sind **nicht geprüft**.

---

## 3. So arbeiten die Modelle zusammen

### 3.1 Ablauf

| Schritt | Verhalten |
|---|---|
| Start | Nur auf Klick: Vorgang → Reiter „Dokumente“ → **„Lokale KI-Zweitprüfung“**. Der Knopf ist nur aktiv, wenn die lokale KI eingeschaltet und bereit ist. |
| Welche Seiten | Je Dokument die Seiten ohne PDF-Textebene oder mit unsicherer Texterkennung. Sind alle Seiten gut lesbar, die ersten zwei. Höchstens 3 Seiten je Dokument, gerendert mit 150 dpi, längste Bildkante höchstens 1600 Pixel. |
| Welches Modell zuerst | Das **aktive Modell**. Ist es nicht heruntergeladen, übernimmt das **Ausweichmodell** diese Rolle. |
| Ausweichen | Liefert das aktive Modell bei einem Dokument keinen Vorschlag oder scheitert es, liest das **Ausweichmodell** danach nur diese Dokumente. |
| Ein Modell nach dem anderen | Jedes Modell bearbeitet erst alle Dokumente und wird dann aus dem Arbeitsspeicher entladen. So liegen nie zwei Modelle gleichzeitig im Speicher. |
| Gegenprüfung | Ist ein Gegenprüfungsmodell gewählt und vorhanden, liest es jedes Dokument zusätzlich. |
| GLM-OCR (Texterkennung) | Liefert Text. Die App wertet ihn mit denselben Regeln aus wie die normale Texterkennung. Die Wortpositionen sind dabei nur angenähert. Der Beleg zeigt daher Seite und Textausschnitt, aber keine Markierung im Seitenbild. |
| Qwen3-VL / Gemma 4 (Bildmodelle) | Nur für **Vertragsdaten**, die aus Dokumenten gelesen werden dürfen, **nicht** rechtlich relevant und noch nicht von Ihnen bestätigt sind. Ist eine Vorlage gewählt, bevorzugt die App deren Felder. Fester, eng begrenzter Auftrag. Antwort nur als JSON nach festem Schema. Temperatur 0 (möglichst gleichbleibende Antworten). |
| Beleg | Bildmodelle müssen jeden Wert **wörtlich zitieren**. Ohne Zitat entsteht kein Vorschlag. Steht der Wert nicht im Zitat oder das Zitat nicht im Text der Seite, halbiert die App jeweils die Sicherheit und vermerkt das am Wert. |
| Vermerk | Methode in der Prüftabelle: „Lokale KI (Bilderkennung)“. Die verwendeten Modelle stehen am Vorgang und im Verlauf. |

### 3.2 Abgleich mit den bisherigen Werten

Alle KI-Vorschläge laufen durch denselben Abgleich (`local_ai/reconcile.py`). Die KI entscheidet nie selbst.

| Bisheriger Status | KI-Vorschlag | Ergebnis |
|---|---|---|
| Fehlt | lesbar | **Erkannt – bitte prüfen**, Hinweis „Nur durch lokale KI gelesen – besonders sorgfältig prüfen.“ |
| Fehlt | nicht eindeutig lesbar | Unklar |
| Unklar | lesbar, und alle bisherigen Funde waren unlesbar | Erkannt – bitte prüfen, gleicher Hinweis |
| Unklar | lesbar, aber anderer Wert als die unsichere Texterkennung | **Konflikt** – beide Werte stehen zur Auswahl, Sie entscheiden |
| Unklar | sonst | bleibt Unklar, Vorschlag wird angehängt |
| Erkannt | gleicher Wert | bleibt Erkannt, Vorschlag wird angehängt |
| Erkannt | anderer Wert | **Konflikt** – Sie wählen den richtigen Wert |
| Konflikt | beliebig | bleibt Konflikt, Vorschlag wird als weitere Auswahl angehängt |
| Bestätigt, Manuell, Nicht relevant | anderer Wert | bleibt unverändert. Hinweis „Lokale KI schlägt einen abweichenden Wert vor – bitte prüfen.“ |

Folge für die Gegenprüfung: Liest das aktive Modell einen Wert und die Gegenprüfung einen anderen, wird das Feld zum **Konflikt**. Dann entscheiden Sie.

### 3.3 Was die KI nie tut

- **Nichts bestätigen.** Ins PDF gelangen nur Werte, die Sie bestätigt oder selbst eingetragen haben.
- **Keine Auftragsdaten** (z. B. Kündigungstermin, Zustimmungen) und **keine rechtlich relevanten Felder** (z. B. Abfindung, Rückerstattung, PeP, Bankverbindung für die Beitragszahlung). Die App weist solche Felder vor jeder Anfrage ab.
- **Keine erfundenen Werte.** Der Auftrag verbietet Raten, Ergänzen, Rechnen und Allgemeinwissen.
- **Keine Anweisungen aus Dokumenten befolgen.** Seitenbild und Text gelten als nicht vertrauenswürdige Daten. Das senkt das Risiko manipulierter Dokumente, garantiert aber nichts. Deshalb prüfen Sie jeden Wert.
- **Keine verlässliche Sicherheitsangabe.** Bei Bildmodellen beruht die „Sicherheit“ auf der Selbsteinschätzung des Modells (gering → 0,7, mittel → 0,5, hoch → 0,3). Sie ist **nicht kalibriert** (nicht an echten Daten überprüft).

---

## 4. Hardware: Was brauche ich für flüssigen Betrieb?

**Kurz:** Profil „standard“ für einen Mac mit Apple Silicon und 16 GB oder einen Windows-PC mit 16 GB und NVIDIA-Grafikkarte. Ohne passende Grafikkarte rechnet Ollama auf dem Prozessor. Das funktioniert, ist aber deutlich langsamer.

### 4.1 Profile

Richtwerte aus `src/insurance_app/local_ai/profiles.py` – **nicht auf Zielgeräten gemessen**. Ollama lädt die Modelle nacheinander. Mehr Arbeitsspeicher oder eine Grafikkarte machen den Ablauf flüssiger.

| Profil | Modelle | Download gesamt | macOS | Windows | Arbeitsspeicher mind. / empfohlen | Freier Speicherplatz |
|---|---|---|---|---|---|---|
| **kompakt** | `glm-ocr` | 2,66 GB | Apple Silicon (M1 oder neuer) mit mindestens 8 GB gemeinsamem Arbeitsspeicher | 64-Bit-Windows mit mindestens 8 GB RAM; Grafikkarte nicht erforderlich | 8 / 16 GB | 4 GB |
| **standard** (empfohlen) | `glm-ocr` + `qwen3-vl:4b-instruct` | 6,00 GB | Apple Silicon (M1 oder neuer) mit 16 GB gemeinsamem Arbeitsspeicher | 16 GB RAM; empfohlen: NVIDIA-Grafikkarte mit mindestens 6–8 GB Grafikspeicher (ohne GPU deutlich langsamer) | 16 / 16 GB | 8 GB |
| **maximal** | `glm-ocr` + `qwen3-vl:4b-instruct` + `gemma4:e4b` | 12,58 GB | Apple Silicon mit 32 GB gemeinsamem Arbeitsspeicher (z. B. M-Pro/Max); 16 GB möglich, aber langsamer | 32 GB RAM und NVIDIA-Grafikkarte mit mindestens 12 GB Grafikspeicher empfohlen | 16 / 32 GB | 16 GB |

**So schlägt die App ein Profil vor** (`recommend_profile`):

| Arbeitsspeicher | Vorschlag |
|---|---|
| unter 8 GB | keine lokale KI (die App arbeitet ohne) |
| 8 bis unter 16 GB | kompakt |
| 16 bis unter 32 GB | standard |
| ab 32 GB | maximal |

- Reicht der freie Speicherplatz nicht, geht der Vorschlag eine Stufe tiefer. Unter 4 GB frei: keine lokale KI.
- Der Vorschlag ist nur eine Empfehlung. Sie wählen selbst.
- **Achtung bei 16-GB-PCs:** Windows und Linux melden oft etwas weniger als eingebaut ist (z. B. 15,7 GB). Die App vergleicht diesen Wert ungerundet und kann dann „kompakt“ vorschlagen. Die Einrichtungsskripte runden auf ganze GB und schlagen „standard“ vor. Bei 16 GB können Sie „standard“ wählen.

### 4.2 Grafikkarte (laut Ollama-Dokumentation)

| Hersteller | Unterstützung | Hinweis |
|---|---|---|
| **Apple** | Apple Silicon (M-Serie) über Metal. **Intel-Macs nur über den Prozessor.** | Ollama setzt macOS 14 (Sonoma) oder neuer voraus. Alle Profile setzen auf dem Mac Apple Silicon voraus. |
| **NVIDIA** | Ab Compute Capability 5.0 (Rechenfähigkeit der Karte, Liste bei NVIDIA). Treiber 550 oder neuer; unter Windows laut Windows-Seite 551.61 oder neuer. Karten mit Compute Capability 5.0 bis 6.2 (z. B. GTX 9xx, GTX 10xx) brauchen Treiber 570 oder neuer. | [Liste der Karten](https://developer.nvidia.com/cuda-gpus) |
| **AMD** | Über ROCm v7 (AMD-Treiberpaket für Berechnungen). Windows: Radeon RX 7600, 7600 XT, 7700 XT, 7800 XT, 7900 GRE/XT/XTX und Radeon PRO W7500 bis W7900. | Andere AMD-Karten eventuell über Vulkan |
| **Andere** (z. B. Intel) | Über Vulkan (Grafikschnittstelle) unter Windows und Linux | Mit dieser App nicht getestet |
| **Keine passende Grafikkarte** | Ollama rechnet auf dem Prozessor (CPU) | Funktioniert, aber deutlich langsamer |

Weitere Hinweise:

- **Grafikspeicher:** Passt ein Modell nicht ganz in den Grafikspeicher, lagert Ollama Teile auf den Prozessor aus. Das bremst. Prüfen mit `ollama ps` (Spalte `PROCESSOR`, z. B. „100% GPU“).
- **Kontextlänge** (wie viel Text und Bild das Modell auf einmal verarbeitet): Ollama nutzt bei unter 24 GiB Grafikspeicher standardmäßig 4.000 Token. Für Bilder reicht das nicht. Die App fordert deshalb bei jeder Bildanfrage 8.192 Token an. `OLLAMA_CONTEXT_LENGTH` müssen Sie nicht ändern. Mehr Kontext braucht mehr Speicher: Mit 16.384 Token und zwei gleichzeitig geladenen Bildmodellen lief der Arbeitsspeicher im Test über.
- **Nur ein Modell im Speicher:** Die App entlädt jedes Modell nach Gebrauch. Auf Rechnern mit 16 GB empfiehlt sich zusätzlich die Ollama-Einstellung `OLLAMA_MAX_LOADED_MODELS=1` (Umgebungsvariable, vor dem Start von Ollama setzen).
- **Zeitlimit:** Standard 600 Sekunden je Seite (`ai_timeout_s`). Bildmodelle können ohne Grafikkarte sehr lange brauchen.

### 4.3 Ehrlich: Betrieb ohne Grafikkarte

- In der Build-Umgebung lief Ollama 0.40.16 **nur auf dem Prozessor**: 4 virtuelle Kerne (Intel Xeon), 15,7 GiB Arbeitsspeicher, keine Grafikkarte. Geladen waren `glm-ocr`, `qwen3-vl:4b-instruct` und `qwen3-vl:4b`.
- Gemessen an einem synthetischen Scan (1 Seite, 26 Felder):

  | Modell | Zeit je Seite | Ergebnis |
  |---|---:|---|
  | `glm-ocr` | 38–43 s | 26 von 26 Feldern richtig |
  | `qwen3-vl:4b-instruct` | ca. 250–256 s | 6 von 8 abgefragten Feldern richtig |
  | `qwen3-vl:4b` (Denkvariante) | 345–370 s | Antwort abgeschnitten, 0 Felder nutzbar |

- **Was das für Sie heißt:** Ohne Grafikkarte ist `glm-ocr` gut nutzbar (unter einer Minute je Seite). Das Ausweichmodell braucht rund 4 Minuten je Seite. Die Denkvariante ist ohne Grafikkarte nicht nutzbar.
- Einzelheiten und Messbedingungen: [BENCHMARK.md](BENCHMARK.md#6-lokale-ki-echte-messung-auf-cpu).
- Auf Mac mit Apple Silicon und auf Windows mit Grafikkarte wurde **nichts gemessen**. Bitte vor dem produktiven Einsatz auf dem eigenen Rechner messen (Abschnitt 10).

---

## 5. Einrichtung

**Kurz:** (1) Ollama installieren, (2) Modelle laden, (3) in der App aktivieren. Für Schritt 2 gibt es drei Wege. Keine Anmeldung und kein API-Schlüssel nötig.

### 5.1 Ollama installieren

| System | Weg | Hinweis |
|---|---|---|
| macOS | [ollama.com/download](https://ollama.com/download) → Datei öffnen → Ollama in „Programme“ ziehen → einmal starten | Oder per Homebrew: `brew install --cask ollama` |
| Windows | [ollama.com/download](https://ollama.com/download) → `OllamaSetup.exe` ausführen | Keine Administratorrechte nötig. Oder: `winget install --id Ollama.Ollama -e` |
| Linux (nur Entwicklung) | `curl -fsSL https://ollama.com/install.sh \| sh` | Installationsbefehl laut Ollama |

- Fragt Ollama beim ersten Start nach einer Anmeldung: Für lokale Modelle ist sie **nicht nötig**. Bitte überspringen.
- Ollama hört standardmäßig nur auf `127.0.0.1:11434`. Bitte so lassen (die Variable `OLLAMA_HOST` nicht ändern). Dann ist Ollama aus dem Netz nicht erreichbar.
- Die App installiert keine Software. Die Einrichtungsskripte (5.3) installieren Ollama nur mit Schalter **und** nach Rückfrage.

### 5.2 Weg A: In der App (mit Zustimmungsdialog)

1. **„Einstellungen & Datenschutz“** → **„Lokale KI (optional)“**.
2. **„★ Empfehlung für dieses Gerät“** ansehen. Mit **„Profil „Standard“ übernehmen“** (bzw. Kompakt/Maximal) setzen Sie die Modelle des Profils – ohne Download. Oder unter **„Modellauswahl“** **„Aktives Modell“**, **„Ausweichmodell“** und **„Gegenprüfung“** selbst wählen (wird sofort gespeichert).
3. In der Tabelle **„Lokale KI-Modelle“** je Modell **„Herunterladen …“**. Die App prüft zuerst den freien Speicherplatz. Dann zeigt ein Dialog Größe, Speicherplatz und Lizenz. Erst nach dem Häkchen **„Ich stimme dem Download ausdrücklich zu“** startet **„Download starten“**.
4. Abbrechen ist jederzeit möglich. Bereits geladene Teile behält Ollama.
5. **„Lokale KI aktivieren“** anhaken → **„Status prüfen“**. Der Status sollte z. B. „Bereit: glm-ocr ist aktiv.“ zeigen.

Den Download führt Ollama aus der Ollama-Bibliothek im Internet aus. Die App sendet dabei nur den Modellnamen an Ollama auf Ihrem Rechner – keine Dokumente, keine Vorgangsdaten.

### 5.3 Weg B: Einrichtungsskript

Die Skripte prüfen den Rechner, schlagen ein Profil vor und fragen vor **jedem** Schritt nach (Standardantwort „Nein“). Sie sind mehrfach ausführbar: Vorhandenes bleibt unverändert.

| | macOS | Windows |
|---|---|---|
| Nur prüfen (Testlauf) | `./scripts/install_local_ai_macos.sh --dry-run` | `powershell -ExecutionPolicy Bypass -File scripts\install_local_ai_windows.ps1 -DryRun` |
| Einrichten | `./scripts/install_local_ai_macos.sh standard` | `powershell -ExecutionPolicy Bypass -File scripts\install_local_ai_windows.ps1 -Profile standard` |
| Ollama mitinstallieren | `… standard --install-ollama` (per `brew install --cask ollama`, nur wenn Homebrew vorhanden) | `… -Profile standard -InstallOllama` (per `winget install --id Ollama.Ollama -e`) |

Was die Skripte tun:

| Schritt | macOS | Windows |
|---|---|---|
| 1. Rechner prüfen | Arbeitsspeicher (`sysctl hw.memsize`), Prozessor und Apple Silicon (`sysctl`, `uname -m`), macOS-Version, freier Speicherplatz am Modellordner (`df`) | Arbeitsspeicher (`Win32_ComputerSystem`, zusätzlich eingebaute Module), Grafikkarten (`Win32_VideoController`, bei NVIDIA auch `nvidia-smi` und Treiber ab 551.61), Windows-Version, freier Speicherplatz (`Get-PSDrive`) |
| 2. Profil prüfen | Vorschlag wie in der App (RAM auf ganze GB gerundet), Warnungen gegen die Richtwerte. Bei Warnungen Rückfrage. | wie macOS, zusätzlich Grafikspeicher gegen den Richtwert |
| 3. Ollama | sucht `ollama`. Fehlt es: Anleitung, oder mit Schalter `brew install --cask ollama` nach Rückfrage | sucht `ollama.exe`. Fehlt es: Anleitung, oder mit Schalter `winget install --id Ollama.Ollama -e` nach Rückfrage |
| 4. Nur-lokal-Modus | bietet an, `~/.ollama/server.json` mit `{"disable_ollama_cloud": true}` anzulegen. Gibt es die Datei schon mit anderem Inhalt, ändert das Skript sie **nicht** und nennt die nötige Zeile. | ebenso für `%USERPROFILE%\.ollama\server.json` (UTF-8 ohne BOM) |
| 5. Dienst | prüft `http://127.0.0.1:11434/api/version`. Läuft Ollama nicht: `open -a Ollama`, bis zu 30 s warten. Nach neuem Nur-lokal-Modus: Neustart nach Rückfrage. | ebenso; startet die Ollama-App aus `%LOCALAPPDATA%\Programs\Ollama` |
| 6. Modelle | zeigt Modelle mit Größe und Lizenz. Je Modell Rückfrage, dann `ollama pull`. Vorhandene Modelle werden übersprungen. | ebenso |
| 7. Kontrolle | `ollama list` und kurzer Rauchtest `ollama show <Modell>` (ohne Bild) | ebenso |

Rückgabewert: 0 = in Ordnung (auch wenn Sie etwas bewusst übersprungen haben), 1 = Fehler, 2 = falscher Aufruf (nur macOS).

### 5.4 Weg C: Von Hand im Terminal

```bash
ollama pull glm-ocr        # aktives Modell (2,66 GB)
ollama pull qwen3-vl:4b-instruct    # Ausweichmodell (3,34 GB)
ollama pull gemma4:e4b     # nur für die Gegenprüfung (6,58 GB, Profil „maximal“)
ollama list                # was ist vorhanden?
ollama run qwen3-vl:4b-instruct     # kurzer Test im Chat, beenden mit /bye
```

- Lokale Adresse von Ollama: `http://localhost:11434` (die App nutzt `http://127.0.0.1:11434`, das ist derselbe Rechner).
- Modell entfernen: `ollama rm qwen3-vl:4b-instruct` – oder in der App in der Tabelle „Lokale KI-Modelle“ „Entfernen …“ (mit Rückfrage). Vorgänge und Dokumente bleiben erhalten.
- Modellordner: macOS `~/.ollama/models`, Windows `C:\Users\<Name>\.ollama\models`. Anderer Ort über die Umgebungsvariable `OLLAMA_MODELS`.

**Ohne Internet:** Alles funktioniert weiter. Sind die Modelle einmal geladen, arbeitet auch die lokale KI offline. Nur der Download braucht Internet.

---

## 6. Nur-lokal-Modus von Ollama

**Kurz:** Ein Schalter in Ollama, der dessen Cloud-Funktionen abschaltet (Cloud-Modelle und Websuche). Die App lehnt Cloud-Modelle ohnehin ab. Der Schalter ist eine **zusätzliche** Sicherung auf Ebene von Ollama.

| | macOS | Windows |
|---|---|---|
| Datei | `~/.ollama/server.json` | `%USERPROFILE%\.ollama\server.json` |
| Inhalt | `{"disable_ollama_cloud": true}` | `{"disable_ollama_cloud": true}` |
| Alternative | Umgebungsvariable `OLLAMA_NO_CLOUD=1`, z. B. `launchctl setenv OLLAMA_NO_CLOUD 1` | Benutzer-Umgebungsvariable `OLLAMA_NO_CLOUD=1` (Einstellungen → „Umgebungsvariablen für dieses Konto bearbeiten“) |
| Danach | Ollama beenden (Menüleiste → Quit Ollama) und neu öffnen | Ollama beenden (Infobereich → Quit Ollama) und über das Startmenü neu starten |
| Kontrolle | `grep "Ollama cloud disabled" ~/.ollama/logs/server.log` | `Select-String -Path "$env:LOCALAPPDATA\Ollama\server.log" -SimpleMatch "Ollama cloud disabled"` |

- Erwartete Protokollzeile laut Ollama-Dokumentation: `Ollama cloud disabled: true`.
- Gibt es `server.json` schon mit anderen Einstellungen, ergänzen Sie nur die Zeile `"disable_ollama_cloud": true`. Die Skripte überschreiben eine vorhandene Datei nie.
- Die Ollama-Dokumentation nennt den Pfad `~/.ollama/server.json`. Unter Windows entspricht `~` dem Benutzerordner (`%USERPROFILE%`). Auf Windows wurde das **nicht getestet**.

---

## 7. Warum keine Cloud-Modelle?

| Grund | Erklärung |
|---|---|
| **Datenschutz** | Cloud-Modelle rechnen auf Servern von Ollama. Seitenbilder mit Kundendaten würden den Rechner verlassen. Das widerspricht LOCAL ONLY. |
| **Technik** | Laut Ollama-Dokumentation unterstützen Cloud-Modelle derzeit **keine strukturierte Ausgabe**. Die App verlangt aber jede Antwort der Bildmodelle als JSON nach festem Schema. |
| **Konto und Kosten** | Cloud-Modelle brauchen ein Ollama-Konto bzw. einen API-Schlüssel und Nutzungsguthaben oder einen Bezahlplan. Für lokale Modelle ist beides nicht nötig. |

Die App lehnt deshalb Modellnamen ab, die den Bestandteil `cloud` enthalten (z. B. `gemma4:cloud` oder `…-cloud`), ebenso Namen mit `/` (fremder Host oder Namensraum). Meldung: „Cloud-Modelle sind nicht erlaubt. Diese App nutzt ausschließlich lokal installierte Modelle.“ Melden Sie Ollama für diese App **nicht** an und legen Sie keinen API-Schlüssel an.

---

## 8. Einstellungen in der App

**Ort:** „Einstellungen & Datenschutz“ → „Lokale KI (optional)“.

| Element | Wirkung |
|---|---|
| **„Lokale KI aktivieren“** | Erst dann darf die App den lokalen Ollama-Dienst nutzen. |
| **„★ Empfehlung für dieses Gerät“** und **„Profil „…“ übernehmen“** | Vorschlag anhand von Arbeitsspeicher und Speicherplatz (Abschnitt 4.1). Übernehmen setzt die drei Modelle des Profils, lädt aber nichts herunter. |
| **„Aktives Modell“** | Läuft zuerst. Sie entscheiden. Voreinstellung `glm-ocr`. |
| **„Ausweichmodell“** | Springt ein, wenn das aktive Modell fehlt oder bei einem Dokument nichts liefert. Voreinstellung `qwen3-vl:4b-instruct`. Wählen Sie dasselbe Modell wie bei „Aktives Modell“, bleibt das Ausweichmodell leer. |
| **„Gegenprüfung“** | Optional, z. B. `gemma4:e4b`. Leer = keine Gegenprüfung. |
| Tabelle **„Lokale KI-Modelle“** mit **„Herunterladen …“** / **„Entfernen …“** | Je Modell: Aufgabe, Vorteil, Download, Lizenz, installiert ja/nein. Download nur nach Platzprüfung und ausdrücklicher Zustimmung, Entfernen nur nach Rückfrage. |
| **„Status prüfen“** | Fragt den lokalen Ollama-Dienst (127.0.0.1), ob er läuft und welche Modelle vorhanden sind. |

**Statusmeldungen** (Auswahl):

| Meldung | Bedeutung | Was tun |
|---|---|---|
| „Lokale KI ist ausgeschaltet – die App arbeitet mit Textebene und Tesseract.“ | KI aus. | Nichts – oder aktivieren. |
| „Der lokale Ollama-Dienst läuft nicht.“ | Ollama nicht gestartet. | Ollama öffnen, erneut testen. |
| „Bereit: glm-ocr ist aktiv.“ | Alles in Ordnung. | „Lokale KI-Zweitprüfung“ im Vorgang nutzen. |
| „glm-ocr fehlt – das Ausweichmodell qwen3-vl:4b-instruct wird verwendet.“ | Aktives Modell nicht geladen. | Aktives Modell herunterladen – oder so lassen. |
| „Weder das aktive noch das Ausweichmodell ist heruntergeladen.“ | Keine KI nutzbar. | Modell herunterladen. |
| „Lokale KI nicht installiert“ | Ollama wurde nicht gefunden. | Ollama installieren (Abschnitt 5.1). |
| „Modell wird eingerichtet“ | Download läuft. | Warten oder abbrechen. |

**Weitere Einstellungen** (nur in `settings.json` im Datenordner; App vorher schließen):

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `ai_enabled` | `false` | Lokale KI an/aus |
| `ai_profile` | `standard` | Gewähltes Profil (nur Empfehlung) |
| `ai_model` | `glm-ocr` | Aktives Modell |
| `ai_fallback_model` | `qwen3-vl:4b-instruct` | Ausweichmodell |
| `ai_crosscheck_model` | leer (`null`) | Gegenprüfung |
| `ollama_url` | `http://127.0.0.1:11434` | Adresse von Ollama. Erlaubt sind nur Adressen auf dem eigenen Rechner. |
| `ai_timeout_s` | `600` | Zeitlimit je Seite in Sekunden |
| `ai_max_tokens` | `1024` | Höchstlänge der Antwort der Bildmodelle (GLM-OCR erhält mindestens 4096) |

---

## 9. Fehlerbehebung

| Problem | Lösung |
|---|---|
| „Der lokale Ollama-Dienst läuft nicht.“ | Ollama öffnen (macOS: Programme → Ollama; Windows: Startmenü → Ollama) oder im Terminal `ollama serve`. Dann „Status prüfen“. |
| „Lokale KI nicht installiert“, obwohl Ollama installiert ist | Ollama liegt an einem unüblichen Ort und läuft nicht. Ollama starten – dann erkennt die App es über die lokale Adresse. |
| „Die eingestellte Ollama-Adresse zeigt nicht auf diesen Rechner.“ | In `settings.json` `ollama_url` auf `http://127.0.0.1:11434` setzen. |
| „Cloud-Modelle sind nicht erlaubt …“ | Gewollt. Einen lokalen Modellnamen wählen, z. B. `qwen3-vl:4b-instruct`. |
| Download bricht ab oder stockt | Internetverbindung prüfen. Hinter einem Firmen-Proxy braucht **Ollama** die Variable `HTTPS_PROXY` (laut Ollama-Dokumentation; `HTTP_PROXY` vermeiden). Die App selbst lädt nichts. Erneut starten; geladene Teile bleiben. |
| Download meldet, dass eine neuere Ollama-Version nötig ist | Ollama aktualisieren (für `glm-ocr` und `qwen3-vl:4b` mindestens 0.30.0). |
| Sehr langsam | `ollama ps` prüfen: Steht bei `PROCESSOR` nicht „100% GPU“, rechnet der Prozessor mit. Kleineres Profil wählen, andere Programme schließen. Ohne Grafikkarte ist das normal (siehe [BENCHMARK.md](BENCHMARK.md)). |
| Zeitüberschreitung bei der Auswertung | `ai_timeout_s` erhöhen oder ein kleineres Modell wählen. |
| „Die lokale KI (Ollama) ist nicht erreichbar.“ während der Auswertung | Ollama wurde beendet. Neu starten und erneut prüfen. Die bisherigen Werte des Vorgangs bleiben erhalten. |
| Nur-lokal-Modus wirkt nicht | Ollama wirklich beenden und neu starten. Dann im Protokoll nach `Ollama cloud disabled: true` suchen (Abschnitt 6). |
| KI-Vorschläge wirken unpassend | Nicht übernehmen. Wert selbst eintragen. Bei Zweifeln die lokale KI ausschalten. |
| Windows: Skript startet nicht („Ausführung von Skripts ist deaktiviert“) | Aufruf mit `powershell -ExecutionPolicy Bypass -File …` (gilt nur für diesen Aufruf). |

---

## 10. Ehrlicher Teststand

| Bereich | Stand |
|---|---|
| Ollama-Client (nur Loopback, kein Proxy, keine Weiterleitungen, Download nur mit Zustimmung, Löschen nur mit Bestätigung, Cloud-Namen abgelehnt) | umgesetzt, automatisch getestet |
| Aktives Modell, Ausweichmodell, Gegenprüfung, Profile, Profilvorschlag | umgesetzt, automatisch getestet |
| Bildextraktion (Auftrag, JSON-Schema, Zitatpflicht, Ablehnung von Auftragsdaten und rechtlichen Feldern) | umgesetzt, automatisch getestet |
| Abgleich mit OCR-Werten (Konflikt statt Überschreiben) | umgesetzt, automatisch getestet |
| Automatische Tests | gegen einen **simulierten** Ollama-Server (`tests/test_local_ai/lai_fake_ollama.py`). Geprüft werden Protokoll, Fehlerbehandlung und Schutzregeln – **nicht** die Erkennungsqualität. |
| Echtes Ollama | In der Build-Umgebung: Ollama 0.40.16, nur Prozessor, `glm-ocr`, `qwen3-vl:4b-instruct` und `qwen3-vl:4b` heruntergeladen und an einem synthetischen Scan gemessen. `gemma4:e4b` wurde dort nicht geladen. |
| Laufzeit und Genauigkeit | auf CPU gemessen (Abschnitt 4.3), Einzelheiten in [BENCHMARK.md](BENCHMARK.md). Mit Grafikkarte: **nicht gemessen**. |
| macOS-Skript | Syntax geprüft (`bash -n`). Auf Linux im Testlauf (`--dry-run`) und mit nachgebildeten macOS-Befehlen durchgespielt. **Nicht auf einem echten Mac ausgeführt.** |
| Windows-Skript | **Nicht ausgeführt** – PowerShell war in der Build-Umgebung nicht installiert. Geprüft wurden nur: reine ASCII-Zeichen und geschlossene Klammern. |
| Modellgrößen, Lizenzangaben | aus den Ollama-Registry-Manifesten (13.11.2026); `glm-ocr` und `qwen3-vl:4b` zusätzlich mit `ollama show` und `ollama list` geprüft |

**Vor dem produktiven Einsatz** bitte auf der Zielhardware messen – nur mit synthetischen Musterdokumenten, nie mit echten Kundendaten:

- `python3 scripts/benchmark_extraction.py --with-vlm` erzeugt synthetische Testdokumente („MUSTER – KEINE ECHTDATEN“) und vergleicht „nur OCR“ mit „OCR + lokale KI“. Voraussetzung: Ollama läuft auf demselben Rechner, das Modell ist geladen. Weitere Optionen: `--model`, `--ollama-url` (nur lokale Adressen), `--vlm-timeout`, `--json`, `--markdown`.
- Entscheidend ist die Zahl der **falschen** Werte. Ein falscher Wert ist gefährlicher als ein fehlender.
- Vorgehen und Ergebnisse: [BENCHMARK.md](BENCHMARK.md).

---

## 11. Quellen

| Quelle | Wofür |
|---|---|
| [docs.ollama.com/quickstart](https://docs.ollama.com/quickstart.md) | Installation, lokale Modelle |
| [docs.ollama.com/api/introduction](https://docs.ollama.com/api/introduction.md) | Lokale API `http://localhost:11434/api`, lokal kein API-Schlüssel nötig |
| [docs.ollama.com/cloud](https://docs.ollama.com/cloud.md) | Cloud-Modelle: Konto nötig, „Cloud models currently do not support structured outputs“ |
| [docs.ollama.com/faq](https://docs.ollama.com/faq.md#how-do-i-disable-ollama-cloud-features) | Nur-lokal-Modus (`disable_ollama_cloud`, `OLLAMA_NO_CLOUD`), Proxy, Modellordner, Standardadresse 127.0.0.1:11434 |
| [docs.ollama.com/gpu](https://docs.ollama.com/gpu.md) | Unterstützte Grafikkarten (NVIDIA, AMD/ROCm, Apple/Metal, Vulkan) |
| [docs.ollama.com/macos](https://docs.ollama.com/macos.md), [docs.ollama.com/windows](https://docs.ollama.com/windows.md) | Systemvoraussetzungen (macOS 14+, Windows 10 22H2+, NVIDIA-Treiber 551.61+), Protokoll-Orte |
| [docs.ollama.com/context-length](https://docs.ollama.com/context-length.md) | Standard-Kontextlänge, `ollama ps` |
| [docs.z.ai/guides/vlm/glm-ocr](https://docs.z.ai/guides/vlm/glm-ocr) | GLM-OCR: Anbieter, Zweck, Größe |
| [Hugging Face Transformers: GLM-OCR](https://huggingface.co/docs/transformers/main/en/model_doc/glm_ocr) | GLM-OCR: Modellbeschreibung |
| Ollama-Registry (Manifeste von `glm-ocr`, `qwen3-vl:4b`, `gemma4:e4b`), abgerufen am 13.11.2026 | Exakte Downloadgrößen, Lizenzangaben |
| [NVIDIA: CUDA GPUs](https://developer.nvidia.com/cuda-gpus) | Compute Capability der eigenen Karte nachsehen |
| `src/insurance_app/local_ai/profiles.py`, `status.py` | Profile, Richtwerte, Modellrollen |
