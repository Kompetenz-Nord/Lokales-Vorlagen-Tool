# Datenschutz

**Kurz gesagt:**

- Die App verarbeitet alle Daten **nur auf dem eigenen Rechner (LOCAL ONLY)**. Es gibt keine Cloud, kein Benutzerkonto, keine Telemetrie (keine Nutzungsdaten) und keine automatische Übermittlung an Dritte.
- Eingebaute Schutzmaßnahmen: Netzwerk-Wächter, Logs ohne Personendaten, schreibgeschützte Dokumentkopien, Aufräumen temporärer Dateien, Passwörter nur im Arbeitsspeicher, Löschfunktion, Dateinamen ohne Personendaten.
- **Wichtigste Grenze:** Die lokale Datenbank und die Dokumentkopien sind **nicht verschlüsselt**. Bitte die Geräteverschlüsselung einschalten (BitLocker unter Windows, FileVault unter macOS).
- Die App ist ein Werkzeug. Sie macht den Einsatz **nicht automatisch DSGVO-konform**. Verarbeitungsverzeichnis, Löschkonzept und Zugriffsschutz bleiben Aufgabe des Betreibers (Abschnitt 5).

---

## 1. Technische Schutzmaßnahmen

| Maßnahme | Umsetzung in der App | Grenze |
|---|---|---|
| **LOCAL ONLY** | Kein Cloud-Dienst. Die Einstellung `local_only` lässt sich nicht abschalten (Version 0.1.0 lehnt jeden anderen Wert ab). | – |
| **Netzwerk-Wächter** | Wird beim Start aktiviert (`security/network_guard.py`). Er sperrt im Python-Socket-Modul alle Verbindungen und Namensauflösungen (DNS) außer zum eigenen Rechner: `127.0.0.0/8`, `::1`, `localhost` und lokale Unix-Sockets. Ein Verstoß bricht sofort ab – vor dem ersten Datenpaket. | Gilt **nur für Python-Sockets**. Nicht erfasst: Qt-eigene Netzwerkfunktionen, externe Programme (z. B. Tesseract, Ollama) und Bibliotheken mit eigenem Netzwerkcode. Die App startet solche Netzwerkzugriffe nicht selbst. Der Wächter ersetzt keine Firewall. |
| **Lokale KI nur über Loopback** | Die Adresse des Ollama-Dienstes muss auf den eigenen Rechner zeigen (Loopback = 127.0.0.1, ::1 oder localhost). Geprüft wird beim Anlegen **und** vor jeder Anfrage. Proxy-Einstellungen werden ignoriert, Weiterleitungen abgelehnt. Cloud-Modelle (Namensbestandteil `cloud`) und Modellnamen mit `/` (fremder Host oder Namensraum) weist die App ab. | Ollama ist ein eigenes Programm. Den Modell-Download führt Ollama aus dem Internet aus – nur nach Ihrer ausdrücklichen Zustimmung. Dabei sendet die App nur den Modellnamen an Ollama, keine Dokumente. |
| **Keine Telemetrie** | Kein Nutzungs-Tracking, keine Absturzberichte, keine Update-Prüfung. Im Programmcode (`src/`) steht keine Internetadresse außerhalb des eigenen Rechners. | – |
| **Logs ohne Inhalte** | Protokolliert werden nur technische Angaben: Zeit, Status, Fehlerklasse, Vorgangs- und Dokument-IDs. Keine Dokumentinhalte, Namen oder Dateinamen. Zusätzlich schwärzt ein Filter typische Muster: IBAN → `[IBAN]`, E-Mail → `[E-MAIL]`, Datum → `[DATUM]`, Telefonnummer → `[TELEFON]`, längere Ziffernfolgen → `[ZAHL]`. Logdatei `logs/app.log` mit Dateirechten nur für den Eigentümer (macOS/Linux), rotierend 1 MB × 3. | Der Filter ist nur eine zweite Sicherung. Namen oder Anschriften erkennt er nicht. Der eigentliche Schutz ist, dass der Code keine Inhalte protokolliert. |
| **Originale bleiben unverändert** | Die App liest hochgeladene Dateien nur. Sie speichert eine **Kopie** im Vorgangsordner. Die Kopie ist schreibgeschützt und heißt wie die Dokument-ID (z. B. `D-1A2B3C4D.pdf`), nicht wie die Person. Vorgangsordner: Rechte nur für den Eigentümer (macOS/Linux). | Der ursprüngliche Dateiname wird in der Datenbank gespeichert (Anzeige in der App). |
| **Temporäre Dateien** | Arbeitsordner je Arbeitsschritt mit Rechten nur für den Eigentümer. Sie werden nach Abschluss oder Abbruch gelöscht. Vorschauen werden beim Schließen der App und nach dem Export gelöscht. Reste älter als 24 Stunden räumt die App beim Start auf. Tesseract erhält Seitenbilder direkt über den Datenstrom (stdin), ohne Zwischendatei. | Stürzt die App ab, bleiben Reste bis zum nächsten Start (bzw. 24 Stunden) im Ordner `tmp/`. |
| **Passwörter für PDFs** | Abfrage nur im Dialog mit verdeckter Eingabe. Das Passwort bleibt nur im Arbeitsspeicher der laufenden App. Es wird nie gespeichert und nie protokolliert. | Nach einem Neustart muss es erneut eingegeben werden. |
| **Datenbank (SQLite)** | Eine Datei `vorgaenge.sqlite3` im Datenordner. Beim Anlegen erhält sie Rechte nur für den Eigentümer (`0600`, macOS/Linux). Gelöschte Inhalte werden in der Datei überschrieben (`secure_delete`). Nach dem Löschen eines Vorgangs schreibt die App das Änderungsjournal (WAL) sofort zurück. | **Nicht verschlüsselt.** Wer Zugriff auf das Benutzerkonto oder die Festplatte hat, kann die Datei lesen. Unter Windows gelten die Rechte des Benutzerprofils. |
| **Vorgang löschen** | Dashboard → Vorgang wählen → „Löschen …“ → „Endgültig löschen“. Gelöscht werden: Vorgang in der Datenbank, Verlauf, erkannte Texte und Werte, Exportliste des Vorgangs, Vorgangsordner mit allen Dokumentkopien und die Vorschau. | **Bereits erzeugte PDFs im Exportordner bleiben erhalten** (das sagt auch der Bestätigungsdialog). Ebenso Einträge im Exportprotokoll (ohne Personendaten). Kopien in Datensicherungen löscht die App nicht. Auf SSDs garantiert Löschen kein physisches Überschreiben. |
| **Exportprotokoll ohne Personendaten** | `logs/export_log.jsonl` enthält je Export nur: Zeitpunkt, Vorgangs-ID, Vorlagen-ID, Vorlagenversion, Dateiname, „fixiert“ ja/nein, Ergebnis der Qualitätskontrolle, Prüfsumme (SHA-256). | – |
| **Dateinamen ohne Personendaten** | Exporte heißen `JJJJ-MM-TT_<VERSICHERER>_<FORMULAR>_<Kurztitel>_<Vorgangs-ID>.pdf`, z. B. `2026-11-13_SWISSLIFE_SL13065_Freigabe_V20261113-AB12CD.pdf`. Briefe: `…_BRIEF_DIN5008_Kuendigung_…`. Die Vorgangs-ID ist zufällig und nicht sprechend. | Der frei wählbare Vorgangstitel kann Personendaten enthalten, wenn Sie welche eintragen. Die App bittet beim Umbenennen: „bitte ohne sensible Daten“. |
| **PDF-Metadaten der Briefe** | Autor und Betreff bleiben leer, der Titel ist der Vorlagentitel. Die Brief-Qualitätskontrolle prüft, dass kein Autor eingetragen ist und der Titel nicht den Absendernamen enthält. | Bei Formularen bleiben die Metadaten des Originals unverändert. |

**Automatisch getestet** (Auszug): Netzwerk-Wächter blockiert externe Verbindungen; Logs enthalten nach einem vollständigen Durchlauf keine Personendaten; Passwort eines PDFs wird nicht gespeichert; hochgeladenes Original bleibt byte-gleich. Siehe `tests/test_security/` und `tests/test_integration/test_end_to_end.py`.

---

## 2. Was wird wo gespeichert?

Speicherorte je Startart: siehe [README – Wo liegen Daten und Exporte?](../README.md#wo-liegen-daten-und-exporte).

| Ort | Inhalt | Personenbezogen? |
|---|---|---|
| `vorgaenge.sqlite3` | Je Vorgang: ursprüngliche Dateinamen, Prüfergebnisse, **vollständiger erkannter Text** aller Seiten mit Wortpositionen, alle Feldwerte mit Textausschnitt (Beleg), Auftragsdaten, **Verlauf mit alten und neuen Werten**, Liste der Exporte; dazu eine Suchspalte mit Namen und Vertragsnummern | **ja** |
| `cases/<Vorgangs-ID>/documents/` | Schreibgeschützte Kopien der Dokumente | **ja** |
| Exportordner | Fertige PDFs (ausgefüllte Formulare, Briefe) | **ja** |
| `tmp/` | Vorschauen und Zwischenstände während der Bearbeitung | **ja**, nur kurzzeitig |
| `templates/` | Importierte Originalvorlagen und Feldzuordnungen | nein (leere Formulare) |
| `logs/app.log` | Technisches Protokoll | nein (siehe Grenzen oben) |
| `logs/export_log.jsonl` | Exportprotokoll | nein |
| `settings.json` | Einstellungen (z. B. Exportordner, OCR, lokale KI an/aus) | nein; Ordnerpfade können den Benutzernamen des Rechners enthalten |
| Ollama-Modellordner (nur bei lokaler KI, z. B. `~/.ollama/models`) | KI-Modelle | nein. Protokolle und Einstellungen von Ollama liegen außerhalb der App |

Hinweise zur Datensparsamkeit:

- Die App speichert den **gesamten** erkannten Text der Dokumente, nicht nur die verwendeten Felder. Unterlagen können besondere Kategorien personenbezogener Daten enthalten (z. B. Gesundheitsangaben bei Berufsunfähigkeitsverträgen, Art. 9 DSGVO). Laden Sie nur Unterlagen hoch, die für den Vorgang nötig sind.
- Bankdaten aus Dokumenten werden erkannt und gespeichert. In die Swiss-Life-Formulare gelangen Bankdaten aber **nur** aus der eigenen Eingabe im Auftrag, nie automatisch aus Dokumenten.
- Der Verlauf wird nur ergänzt, nie überschrieben. Ein korrigierter Wert bleibt im Verlauf sichtbar, bis der ganze Vorgang gelöscht wird.

---

## 3. Aufbewahrung und Löschung

- Die App **löscht nichts automatisch** (außer temporären Dateien). Es gibt keine eingebauten Löschfristen.
- **Archivieren** blendet einen Vorgang nur aus. Die Daten bleiben vollständig gespeichert.
- **Löschen** entfernt den Vorgang endgültig aus der App (Umfang siehe Abschnitt 1). Rückgängig machen ist nicht möglich.
- **Exportierte PDFs** löschen Sie selbst im Exportordner.
- **Datensicherungen** (z. B. Time Machine, Windows-Dateiversionsverlauf, Server-Backup) enthalten Kopien. Sie müssen nach Ihrem Löschkonzept behandelt werden.
- Vollständig entfernen: App schließen, Datenordner und Exportordner löschen. Danach ggf. Ollama-Modelle in Ollama entfernen.

---

## 4. Netzwerk im Überblick

| Situation | Netzwerk |
|---|---|
| Normale Nutzung (Dokumente, OCR, Datenprüfung, Export) | keins – funktioniert vollständig offline |
| Lokale KI eingeschaltet | nur zum Ollama-Dienst auf demselben Rechner (Loopback) |
| Modell-Download (nur nach ausdrücklicher Zustimmung) | Ollama lädt das Modell aus der Ollama-Bibliothek im Internet. Dabei wird die IP-Adresse des Rechners an den Download-Server übertragen. Dokumente werden nicht übertragen. |
| Einrichtung und Update (`pip install`, `git pull`, Homebrew, winget) | ja – das sind Werkzeuge außerhalb der App |

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
| **Ollama** (nur bei lokaler KI) | Ollama ist Software eines Dritten. Herkunft, Lizenz und Einstellungen prüft der Betreiber. Den Dienst nur auf dem eigenen Rechner lauschen lassen (Standard: `127.0.0.1:11434`). |
| **Aktualität** | Betriebssystem, Python-Pakete und die App aktuell halten. |
