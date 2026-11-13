# Datenschutz

**Kurz gesagt:**

- Die App verarbeitet alle Daten **nur auf dem eigenen Rechner (LOCAL ONLY)**. Es gibt keine Cloud, kein Benutzerkonto, keine Telemetrie (keine Nutzungsdaten) und keine automatische Übermittlung an Dritte.
- Eingebaute Schutzmaßnahmen: Netzwerk-Wächter, Logs ohne Personendaten, schreibgeschützte Dokumentkopien, Aufräumen temporärer Dateien, Passwörter nur im Arbeitsspeicher, Löschfunktion, Dateinamen ohne Personendaten.
- **Lokale KI (optional):** Die App spricht nur mit Ollama auf demselben Rechner (`127.0.0.1`) und lehnt Cloud-Modelle ab. Modelle lädt Ollama nur nach Ihrer ausdrücklichen Zustimmung. Empfehlung: den Nur-lokal-Modus von Ollama einschalten (Abschnitt 1.1).
- **Wichtigste Grenze:** Die lokale Datenbank und die Dokumentkopien sind **nicht verschlüsselt**. Bitte die Geräteverschlüsselung einschalten (BitLocker unter Windows, FileVault unter macOS).
- Die App ist ein Werkzeug. Sie macht den Einsatz **nicht automatisch DSGVO-konform**. Verarbeitungsverzeichnis, Löschkonzept und Zugriffsschutz bleiben Aufgabe des Betreibers (Abschnitt 5).

---

## 1. Technische Schutzmaßnahmen

| Maßnahme | Umsetzung in der App | Grenze |
|---|---|---|
| **LOCAL ONLY** | Kein Cloud-Dienst. Die Einstellung `local_only` lässt sich nicht abschalten (Version 0.1.0 lehnt jeden anderen Wert ab). | – |
| **Netzwerk-Wächter** | Wird beim Start aktiviert (`security/network_guard.py`). Er sperrt im Python-Socket-Modul alle Verbindungen und Namensauflösungen (DNS) außer zum eigenen Rechner: `127.0.0.0/8`, `::1`, `localhost` und lokale Unix-Sockets. Ein Verstoß bricht sofort ab – vor dem ersten Datenpaket. | Gilt **nur für Python-Sockets**. Nicht erfasst: Qt-eigene Netzwerkfunktionen, externe Programme (z. B. Tesseract, Ollama) und Bibliotheken mit eigenem Netzwerkcode. Die App startet solche Netzwerkzugriffe nicht selbst. Der Wächter ersetzt keine Firewall. |
| **Lokale KI nur über Loopback** | Die Adresse des Ollama-Dienstes muss auf den eigenen Rechner zeigen (Loopback = 127.0.0.1, ::1 oder localhost). Geprüft wird beim Anlegen **und** vor jeder Anfrage. Proxy-Einstellungen werden ignoriert, Weiterleitungen abgelehnt. Cloud-Modelle (Namensbestandteil `cloud`) und Modellnamen mit `/` (fremder Host oder Namensraum) weist die App ab. | Ollama ist ein eigenes Programm. Den Modell-Download führt Ollama aus dem Internet aus – nur nach Ihrer ausdrücklichen Zustimmung. Dabei sendet die App nur den Modellnamen an Ollama, keine Dokumente. Zusätzliche Sicherung in Ollama selbst: Nur-lokal-Modus (Abschnitt 1.1). |
| **Keine Telemetrie** | Kein Nutzungs-Tracking, keine Absturzberichte, keine Update-Prüfung. Der Programmcode (`src/`) baut keine Verbindung zu Internetadressen auf. Die einzigen Internetadressen im Code sind Quellenangaben im Modellkatalog (`local_ai/status.py`) – reiner Text, kein Abruf. | – |
| **Logs ohne Inhalte** | Protokolliert werden nur technische Angaben: Zeit, Status, Fehlerklasse, Vorgangs- und Dokument-IDs. Keine Dokumentinhalte, Namen oder Dateinamen. Zusätzlich schwärzt ein Filter typische Muster: IBAN → `[IBAN]`, E-Mail → `[E-MAIL]`, Datum → `[DATUM]`, Telefonnummer → `[TELEFON]`, längere Ziffernfolgen → `[ZAHL]`. Logdatei `logs/app.log`, rotierend 1 MB × 3 Sicherungen; beim Start setzt die App ihre Rechte auf „nur Eigentümer“ (macOS/Linux). | Der Filter ist nur eine zweite Sicherung. Namen oder Anschriften erkennt er nicht. Der eigentliche Schutz ist, dass der Code keine Inhalte protokolliert. |
| **Originale bleiben unverändert** | Die App liest hochgeladene Dateien nur. Sie speichert eine **Kopie** im Vorgangsordner. Die Kopie ist schreibgeschützt und heißt wie die Dokument-ID (z. B. `D-1A2B3C4D.pdf`), nicht wie die Person. Vorgangsordner: Rechte nur für den Eigentümer (macOS/Linux). | Der ursprüngliche Dateiname wird in der Datenbank gespeichert (Anzeige in der App). |
| **Temporäre Dateien** | Arbeitsordner je Arbeitsschritt mit Rechten nur für den Eigentümer. Sie werden nach Abschluss oder Abbruch gelöscht. Vorschauen werden beim Schließen der App und nach dem Export gelöscht. Reste älter als 24 Stunden räumt die App beim Start auf. Tesseract erhält Seitenbilder direkt über den Datenstrom (stdin), ohne Zwischendatei. | Stürzt die App ab, bleiben Reste bis zum nächsten Start (bzw. 24 Stunden) im Ordner `tmp/`. |
| **Passwörter für PDFs** | Abfrage nur im Dialog mit verdeckter Eingabe. Das Passwort bleibt nur im Arbeitsspeicher der laufenden App. Es wird nie gespeichert und nie protokolliert. Das gilt auch für die Warteschlange einer Kundenmappe. | Nach einem Neustart muss es erneut eingegeben werden. |
| **Kundenmappen** | Die App legt je Vertrag einen Vorgang an. Dessen Titel setzt sie aus dem Mappentitel, dem erkannten Versicherer und den letzten 4 Zeichen der Vertragsnummer zusammen. Protokolliert werden nur Mappen-ID und Anzahlen. Die Exportdateinamen ändern sich durch Kundenmappen nicht (Muster siehe „Dateinamen ohne Personendaten“). | Vorgangstitel stehen in der Datenbank. Den Mappentitel daher **ohne** Namen wählen (z. B. ein Kürzel). |
| **Datenbank (SQLite)** | Eine Datei `vorgaenge.sqlite3` im Datenordner. Sie erhält Rechte nur für den Eigentümer (`0600`, macOS/Linux). Gelöschte Inhalte werden in der Datei überschrieben (`secure_delete`). Nach dem Löschen eines Vorgangs schreibt die App das Änderungsjournal (WAL, Zusatzdatei `-wal`) sofort in die Hauptdatei zurück. | **Nicht verschlüsselt.** Wer Zugriff auf das Benutzerkonto oder die Festplatte hat, kann die Datei lesen. Der Datenordner selbst und der Exportordner erhalten die Standardrechte des Systems. Unter Windows gelten die Rechte des Benutzerprofils. |
| **Vorgang löschen** | Dashboard → Vorgang wählen → „Löschen …“ → „Endgültig löschen“. Gelöscht werden: Vorgang in der Datenbank, Verlauf, erkannte Texte und Werte, Exportliste des Vorgangs, Vorgangsordner mit allen Dokumentkopien und die Vorschau. | **Bereits erzeugte PDFs im Exportordner bleiben erhalten** (das sagt auch der Bestätigungsdialog). Ebenso Einträge im Exportprotokoll (ohne Personendaten). Kopien in Datensicherungen löscht die App nicht. Auf SSDs garantiert Löschen kein physisches Überschreiben. |
| **Exportprotokoll ohne Personendaten** | `logs/export_log.jsonl` enthält je Export nur: Zeitpunkt, Vorgangs-ID, Vorlagen-ID, Vorlagenversion, Dateiname, „fixiert“ ja/nein, Ergebnis der Qualitätskontrolle, Prüfsumme (SHA-256). | – |
| **Dateinamen ohne Personendaten** | Exporte heißen `JJJJ-MM-TT_<VERSICHERER>_<FORMULAR>_<Kurztitel>_<Vorgangs-ID>.pdf`, z. B. `2026-11-13_SWISSLIFE_SL13065_Freigabe_V20261113-AB12CD.pdf`. Briefe: `…_BRIEF_DIN5008_Kuendigung_…`. Die Vorgangs-ID ist zufällig und nicht sprechend. | Der frei wählbare Vorgangstitel kann Personendaten enthalten, wenn Sie welche eintragen. Die App bittet beim Umbenennen: „bitte ohne sensible Daten“. |
| **PDF-Metadaten der Briefe** | Autor und Betreff bleiben leer, der Titel ist der Vorlagentitel. Die Brief-Qualitätskontrolle prüft, dass kein Autor eingetragen ist und der Titel nicht den Absendernamen enthält. | Bei Formularen bleiben die Metadaten des Originals unverändert. |

### 1.1 Lokale KI: so bleibt sie lokal

**Kurz gesagt:** Die App spricht nur mit Ollama auf demselben Rechner (`127.0.0.1`) und lehnt Cloud-Modelle ab. Zusätzlich lässt sich Ollama selbst in einen Nur-lokal-Modus schalten. Modelle lädt Ollama nur nach Ihrer ausdrücklichen Zustimmung herunter.

| Ebene | Schutz | Grenze |
|---|---|---|
| **App** | Ollama-Adresse nur auf dem eigenen Rechner (`127.0.0.1`, `::1`, `localhost`), geprüft beim Anlegen **und** vor jeder Anfrage. Proxy-Einstellungen werden ignoriert, Weiterleitungen abgelehnt. Modellnamen mit dem Bestandteil `cloud` (z. B. `gemma4:cloud`, `…-cloud`) und mit `/` weist die App ab. Dazu der Netzwerk-Wächter (oben). | Schützt nur die Verbindungen der App. Was Ollama selbst tut, steuert Ollama. |
| **Ollama: Nur-lokal-Modus** (empfohlen) | `{"disable_ollama_cloud": true}` in `~/.ollama/server.json` (Windows: `%USERPROFILE%\.ollama\server.json`) oder Umgebungsvariable `OLLAMA_NO_CLOUD=1`. Danach Ollama neu starten. Laut Ollama-Dokumentation sind dann Cloud-Modelle und Websuche aus; das Protokoll zeigt `Ollama cloud disabled: true`. Die Einrichtungsskripte bieten das an (Rückfrage, vorhandene Datei wird nie überschrieben). | Muss vom Betreiber eingeschaltet werden. Unter Windows nicht getestet. |
| **Ollama: Adresse** | Ollama hört standardmäßig nur auf `127.0.0.1:11434`. Bitte so lassen (`OLLAMA_HOST` nicht ändern). Dann ist Ollama aus dem Netz nicht erreichbar. | Eine geänderte Einstellung würde Ollama im Netz erreichbar machen. |
| **Downloads** | Nur nach ausdrücklicher Zustimmung: in der App über den Dialog „Ich stimme dem Download ausdrücklich zu“, in den Skripten über eine Rückfrage je Modell. Den Download führt der lokale Ollama-Dienst aus der Ollama-Bibliothek aus. Die App sendet dabei nur den Modellnamen an Ollama auf diesem Rechner – keine Dokumente, keine Vorgangsdaten. | Der Download-Server sieht die IP-Adresse des Rechners. |
| **Kein Konto** | Für lokale Modelle braucht Ollama keine Anmeldung und keinen API-Schlüssel. Bitte Ollama für diese App **nicht** anmelden. | – |

Laut Ollama-Dokumentation sieht Ollama bei lokaler Ausführung weder Eingaben noch Daten („We don't see your prompts or data when you run locally“). Cloud-Modelle würden Seitenbilder an Server von Ollama senden – deshalb sind sie in dieser App gesperrt. Details: [LOKALE_KI.md, Abschnitt 6 und 7](LOKALE_KI.md#6-nur-lokal-modus-von-ollama).

**Automatisch getestet** (Auszug): Netzwerk-Wächter blockiert externe Verbindungen; Logs enthalten nach einem vollständigen Durchlauf keine Personendaten; Passwort eines PDFs wird nicht gespeichert; hochgeladenes Original bleibt byte-gleich. Siehe `tests/test_security/` und `tests/test_integration/test_end_to_end.py`.

---

## 2. Was wird wo gespeichert?

Speicherorte je Startart: siehe [README – Wo liegen Daten und Exporte?](../README.md#wo-liegen-daten-und-exporte).

| Ort | Inhalt | Personenbezogen? |
|---|---|---|
| `vorgaenge.sqlite3` | Je Vorgang: ursprüngliche Dateinamen, Prüfergebnisse, **vollständiger erkannter Text** aller Seiten mit Wortpositionen, alle Feldwerte mit Textausschnitt (Beleg), Auftragsdaten, **Verlauf mit alten und neuen Werten**, Liste der Exporte; dazu eine Suchspalte mit Namen und Vertragsnummern; Kundenmappen (Titel, Zuordnung der Vorgänge) | **ja** |
| `cases/<Vorgangs-ID>/documents/` | Schreibgeschützte Kopien der Dokumente | **ja** |
| Exportordner | Fertige PDFs (ausgefüllte Formulare, Briefe) | **ja** |
| `tmp/` | Vorschauen und Zwischenstände während der Bearbeitung | **ja**, nur kurzzeitig |
| `templates/` | Importierte Originalvorlagen und Feldzuordnungen | nein (leere Formulare) |
| `empfaenger.json` | Gemerkte Briefempfänger: Name und Anschrift, die Sie in einem erzeugten Brief verwendet haben (in der Regel Versicherer) | in der Regel nein; **ja**, wenn Sie an eine Privatperson schreiben |
| `logs/app.log` | Technisches Protokoll | nein (siehe Grenzen oben) |
| `logs/export_log.jsonl` | Exportprotokoll | nein |
| `settings.json` | Einstellungen (z. B. Exportordner, OCR, lokale KI an/aus) | nein; Ordnerpfade können den Benutzernamen des Rechners enthalten |
| Ollama-Modellordner (nur bei lokaler KI, z. B. `~/.ollama/models`) | KI-Modelle | nein |
| Ollama-Einstellungen und -Protokolle (nur bei lokaler KI): `~/.ollama/server.json`, Protokolle unter `~/.ollama/logs` (macOS) bzw. `%LOCALAPPDATA%\Ollama` (Windows) | Einstellungen und Protokolle von Ollama | Was Ollama protokolliert, legt Ollama fest, nicht die App. Die App ändert die Protokoll-Einstellungen von Ollama nicht. |

Hinweise zur Datensparsamkeit:

- Die App speichert den **gesamten** erkannten Text der Dokumente, nicht nur die verwendeten Felder. Unterlagen können besondere Kategorien personenbezogener Daten enthalten (z. B. Gesundheitsangaben bei Berufsunfähigkeitsverträgen, Art. 9 DSGVO). Laden Sie nur Unterlagen hoch, die für den Vorgang nötig sind.
- Bankdaten aus Dokumenten werden erkannt und gespeichert. In die Swiss-Life-Formulare gelangen Bankdaten aber **nur** aus der eigenen Eingabe im Auftrag, nie automatisch aus Dokumenten.
- Der Verlauf wird nur ergänzt, nie überschrieben. Ein korrigierter Wert bleibt im Verlauf sichtbar, bis der ganze Vorgang gelöscht wird.

---

## 3. Aufbewahrung und Löschung

- Die App **löscht nichts automatisch** (außer temporären Dateien). Es gibt keine eingebauten Löschfristen.
- **Archivieren** blendet einen Vorgang nur aus. Die Daten bleiben vollständig gespeichert.
- **Löschen** entfernt den Vorgang endgültig aus der App (Umfang siehe Abschnitt 1). Rückgängig machen ist nicht möglich.
- **Kundenmappe löschen** entfernt standardmäßig nur die Mappe. Ihre Vorgänge bleiben erhalten und werden aus der Mappe gelöst. Mitgelöscht werden sie nur auf ausdrücklichen Wunsch.
- **Exportierte PDFs** löschen Sie selbst im Exportordner.
- **Datensicherungen** (z. B. Time Machine, Windows-Dateiversionsverlauf, Server-Backup) enthalten Kopien. Sie müssen nach Ihrem Löschkonzept behandelt werden.
- Vollständig entfernen: App schließen, Datenordner und Exportordner löschen. Danach ggf. Ollama-Modelle in Ollama entfernen.

---

## 4. Netzwerk im Überblick

| Situation | Netzwerk |
|---|---|
| Normale Nutzung (Dokumente, OCR, Datenprüfung, Export) | keins – funktioniert vollständig offline |
| Lokale KI eingeschaltet | nur zum Ollama-Dienst auf demselben Rechner (Loopback, `127.0.0.1:11434`). Cloud-Modelle lehnt die App ab. |
| Modell-Download (nur nach ausdrücklicher Zustimmung) | Ollama lädt das Modell aus der Ollama-Bibliothek im Internet. Dabei wird die IP-Adresse des Rechners an den Download-Server übertragen. Dokumente werden nicht übertragen. |
| Einrichtung und Update (`pip install`, `git pull`, Homebrew, winget) | ja – das sind Werkzeuge außerhalb der App |
| Einrichtungsskripte der lokalen KI (`scripts/install_local_ai_macos.sh`, `scripts/install_local_ai_windows.ps1`) | Das Skript selbst spricht Ollama nur über `127.0.0.1` an. Ins Internet gehen nur: die Installation von Ollama (Homebrew bzw. winget – nur mit Schalter und nach Rückfrage) und die Modell-Downloads durch Ollama (nach Rückfrage je Modell). |

---

## 5. Verbleibende Aufgaben des Betreibers

Die App liefert technische Schutzmaßnahmen. Ob sie im Einzelfall ausreichen, bewertet der Betreiber. **Eine Aussage „DSGVO-konform“ trifft diese Dokumentation ausdrücklich nicht.**

| Thema | Was zu tun ist |
|---|---|
| **Geräteverschlüsselung** | Pflicht-Empfehlung, weil die Datenbank nicht verschlüsselt ist: **BitLocker** (Windows) bzw. **FileVault** (macOS) einschalten. Gilt auch für externe Laufwerke mit Sicherungen. |
| **Zugriffsschutz** | Die App hat keine eigene Anmeldung. Wer das Benutzerkonto nutzt, sieht alle Vorgänge. Daher: eigenes Benutzerkonto je Person, sicheres Kennwort, automatische Bildschirmsperre. |
| **Cloud-Synchronisation vermeiden** | Daten- und Exportordner nicht in einem synchronisierten Ordner ablegen. Prüfen: macOS „iCloud Drive → Ordner ‚Schreibtisch‘ und ‚Dokumente‘“, Windows „OneDrive-Ordnersicherung“. Der Standard-Exportordner liegt unter „Documents“ – bei aktiver Synchronisation bitte in den Einstellungen ändern. Beim Start aus dem Quellcode liegt `user_data/` im Projektordner: Projektordner daher ebenfalls nicht synchronisieren. |
| **Verarbeitungsverzeichnis** (Art. 30 DSGVO) | Tätigkeit eintragen, z. B. „Vorbereitung von Versicherungsformularen und Kundenschreiben“. Betroffene: Versicherungsnehmer, versicherte Personen, Arbeitgeber, Kontoinhaber. Datenarten: Name, Anschrift, Geburtsdatum, Geburtsort, Staatsangehörigkeit, Vertragsnummern, Beiträge, Bankverbindung, PeP-Angabe, ggf. Inhalte der Unterlagen. Speicherort und technische Maßnahmen: dieses Dokument. |
| **Löschkonzept** | Aufbewahrungs- und Löschfristen festlegen (z. B. gesetzliche Aufbewahrungspflichten beachten). Vorgänge, Exporte und Sicherungen danach löschen (Abschnitt 3). |
| **Auftragsverarbeitung (AV-Vertrag)** | Für die App selbst nicht nötig: Es gibt keinen Dienstleister, der Daten im Auftrag verarbeitet. Für andere Dienste (z. B. IT-Betreuung mit Zugriff auf den Rechner, Cloud-Backup) prüft der Betreiber das selbst. |
| **Informationspflichten** | Kunden über die Verarbeitung informieren (Art. 13/14 DSGVO) – unabhängig von der App. |
| **Datensicherung** | Den Datenordner bei Bedarf sichern. Sicherungen verschlüsseln. Vor einer Sicherung die App schließen. |
| **Weitergabe von Logs** | Logs sind auf technische Angaben beschränkt. Vor einer Weitergabe (z. B. an den Support) trotzdem kurz ansehen. |
| **Ollama** (nur bei lokaler KI) | Ollama ist Software eines Dritten. Herkunft, Lizenz und Einstellungen prüft der Betreiber. Den Dienst nur auf dem eigenen Rechner lauschen lassen (Standard: `127.0.0.1:11434`). Nur-lokal-Modus einschalten (Abschnitt 1.1). Ollama nicht mit einem Konto anmelden. Modelllizenzen prüfen: Für `glm-ocr` enthält das Ollama-Manifest keine Lizenzangabe. |
| **Aktualität** | Betriebssystem, Python-Pakete und die App aktuell halten. |
