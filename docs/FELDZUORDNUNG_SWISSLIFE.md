# Feldzuordnung Swiss Life (SL13065 und SL18477)

**Kurz gesagt:**

- Die App füllt die **Original-PDFs** von Swiss Life aus. Sie schreibt nur in die vorhandenen Formularfelder und ändert sonst nichts an der Datei.
- **Vertragsdaten** (z. B. Vertragsnummer, Name, Geburtsdatum) trägt sie erst ein, wenn Sie den Wert bestätigt oder selbst eingegeben haben.
- **Auftragsdaten** (Kreuze, Auswahlen, neuer Arbeitgeber, Bankverbindung) kommen **nur aus Ihrer ausdrücklichen Eingabe** – nie aus Dokumenten, nie von der KI.
- **Unterschriften und Unterschriftsdaten bleiben immer leer.**

Die Tabellen unten wurden aus den Zuordnungsdateien erzeugt:
`config/field_schemas/swisslife_sl13065.json` und `config/field_schemas/swisslife_sl18477.json` (Formularstand 2024-09, Zuordnung bestätigt). Die Feldbezeichnungen stammen aus `src/insurance_app/core/fields.py`. Ändert sich eine Zuordnungsdatei, gilt die Datei – nicht diese Übersicht.

---

## Überblick

| | SL13065 | SL18477 |
|---|---|---|
| Titel | Freigabeerklärung bei Direktversicherungen | Übernahme der Versicherungsnehmer-Eigenschaft |
| Stand / Seiten | 09/2024, 1 Seite | 09/2024, 6 Seiten |
| Datei | `SL13065_Freigabeerklaerung_Direktversicherung_2024-09.pdf` | `SL18477_Uebernahmeerklaerung_Direktversicherung_2024-09.pdf` |
| Prüfsumme (SHA-256) | `3272c5b1cbb2c685447b6522d2c0be7f52e54744bd27ed9b8005c7526736fc1a` | `df7ddc8a072173fabd64043a09d1d1c1acf3b1dd771767100a968032403b3b64` |
| Zuordnungen | 22 (davon 4 Unterschrift/Unterschriftsdatum) | 50 (davon 16 Unterschrift/Unterschriftsdatum) |
| Nicht befüllt | Schaltfläche „Formular zurücksetzen“ | Schaltfläche „Formular zurücksetzen“ |
| Plausibilitätsregeln | bAV-Produkt erwartet, Reihenfolge Betriebszugehörigkeit, Geburtsdatum plausibel | bAV-Produkt erwartet, Geburtsdatum plausibel, Hinweis bei ausländischer IBAN |
| Pflicht-Bestätigung vor dem Export | – | Beratungsverzicht (Abschnitt F) zur Kenntnis genommen |

Die Plausibilitätsregeln erzeugen nur Warnungen oder Hinweise. Sie blockieren den Export nicht.

## So lesen Sie die Tabellen

| Spalte | Bedeutung |
|---|---|
| PDF-Feldname | Name des Felds in der PDF, wie Swiss Life ihn vergeben hat (Schreibfehler wie „Stattsangehörigkeit“ stammen aus dem Original). |
| S. | Seite in der PDF |
| Art | **Text**; **Datumsteil** (Tag, Monat oder Jahr in einem eigenen Kästchen); **Betrag**; **Ankreuzfeld** (ein Kästchen); **Optionsgruppe** (mehrere Kästchen, genau eins wird angekreuzt); **Unterschrift**; **Datum zur Unterschrift** |
| Quelle im Datenmodell | Feldschlüssel und deutsche Bezeichnung. „zusammengesetzt“ heißt: mehrere Datenfelder ergeben einen Text, z. B. „Nachname, Vorname“. „(optional)“ = dieser Teil darf fehlen. |
| Pflicht / Bedingung | **Pflicht**: Ohne Wert ist kein Export möglich. **„nur wenn …“**: Das Feld zählt nur, wenn die Bedingung erfüllt ist. Sonst bleibt es leer. „und“ = alle Bedingungen müssen erfüllt sein, „oder“ = eine genügt. |
| rechtl. relevant | **ja**: Die App setzt nie selbst etwas. Auch „Nein“ müssen Sie ausdrücklich wählen. |
| Befüllung | **automatisch nach Bestätigung**: Vertragsdaten aus Dokumenten, eingetragen nur mit Status „Bestätigt“ oder „Manuell eingetragen“. **nur nach ausdrücklicher Eingabe/Auswahl**: Auftragsdaten. Die App sperrt diese technisch gegen automatisches Befüllen. **nie – bleibt leer**: Unterschriftsbereich. |

„Nicht relevant“ ist ebenfalls eine bewusste Entscheidung: Das Feld bleibt leer und blockiert den Export nicht. Bei rechtlich relevanten Ja/Nein- und Auswahlfeldern (Ankreuzfelder, Optionsgruppen) lässt die App „nicht relevant“ nicht zu – dort ist immer eine ausdrückliche Auswahl nötig.

---

## SL13065 – Freigabeerklärung bei Direktversicherungen

| # | PDF-Feldname | S. | Art | Quelle im Datenmodell | Pflicht / Bedingung | rechtl. relevant | Befüllung |
|---|---|---|---|---|---|---|---|
| 1 | `Versicherung Nummer` | 1 | Text | `contract.number` – Vertrags-/Versicherungsnummer | Pflicht | – | automatisch nach Bestätigung |
| 2 | `Versicherte Person` | 1 | Text | zusammengesetzt: `insured.first_name` + `insured.last_name` | Pflicht | – | automatisch nach Bestätigung |
| 3 | `Versicherungsnehmer` | 1 | Text | zusammengesetzt: `policyholder.first_name` (optional) + `policyholder.name` | Pflicht | – | automatisch nach Bestätigung |
| 4 | `Name Vorname` | 1 | Text | zusammengesetzt: `insured.last_name` + `insured.first_name` | Pflicht | – | automatisch nach Bestätigung |
| 5 | `Datum 1.1` | 1 | Datumsteil | `insured.birth_date` – Geburtsdatum; Teil: Tag | Pflicht | – | automatisch nach Bestätigung |
| 6 | `Datum 1.21` | 1 | Datumsteil | `insured.birth_date` – Geburtsdatum; Teil: Monat | Pflicht | – | automatisch nach Bestätigung |
| 7 | `Datum 1.32` | 1 | Datumsteil | `insured.birth_date` – Geburtsdatum; Teil: Jahr | Pflicht | – | automatisch nach Bestätigung |
| 8 | `Straße Hausnummer` | 1 | Text | zusammengesetzt: `insured.street` + `insured.house_number` (optional) | Pflicht | – | automatisch nach Bestätigung |
| 9 | `Postleitzahl` | 1 | Text | `insured.postal_code` – PLZ (versicherte Person) | Pflicht | – | automatisch nach Bestätigung |
| 10 | `Ort` | 1 | Text | `insured.city` – Ort (versicherte Person) | Pflicht | – | automatisch nach Bestätigung |
| 11 | `Datum 2.1` | 1 | Datumsteil | `employer.employment_start` – Beginn der Betriebszugehörigkeit; Teil: Tag | Pflicht | – | automatisch nach Bestätigung |
| 12 | `Datum 2.21` | 1 | Datumsteil | `employer.employment_start` – Beginn der Betriebszugehörigkeit; Teil: Monat | Pflicht | – | automatisch nach Bestätigung |
| 13 | `Datum 2.32` | 1 | Datumsteil | `employer.employment_start` – Beginn der Betriebszugehörigkeit; Teil: Jahr | Pflicht | – | automatisch nach Bestätigung |
| 14 | `Datum 3.1` | 1 | Datumsteil | `employer.employment_end` – Ende der Betriebszugehörigkeit; Teil: Tag | Pflicht | – | automatisch nach Bestätigung |
| 15 | `Datum 3.21` | 1 | Datumsteil | `employer.employment_end` – Ende der Betriebszugehörigkeit; Teil: Monat | Pflicht | – | automatisch nach Bestätigung |
| 16 | `Datum 3.32` | 1 | Datumsteil | `employer.employment_end` – Ende der Betriebszugehörigkeit; Teil: Jahr | Pflicht | – | automatisch nach Bestätigung |
| 17 | `Abfindung` | 1 | Ankreuzfeld | `order.severance_consent` – Zustimmung zur Abfindung gem. § 3 BetrAVG (im Kündigungsfall); Kreuz bei „ja“ | Pflicht | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 18 | `Rückerstattung` | 1 | Ankreuzfeld | `order.waive_refund` – Verzicht auf Rückerstattung überzahlter Beiträge (Kreuz = KEINE Rückerstattung); Kreuz bei „ja“ | Pflicht | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 19 | `Datum Unterschrift 1.1` | 1 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 20 | `Datum Unterschrift 1.21` | 1 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 21 | `Datum Unterschrift 1.32` | 1 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 22 | `Unterschrift` | 1 | Unterschrift | – | – | – | nie – bleibt leer |

### Besonderheiten SL13065

- **Beginn und Ende der Betriebszugehörigkeit** stehen nicht in der Police. Sie brauchen eine verlässliche Quelle oder werden manuell eingetragen.
- **„Abfindung“** (Zustimmung zur Abfindung gem. § 3 BetrAVG): aktive Erklärung des bisherigen Arbeitgebers. Ja → Kreuz. Nein → Kästchen bleibt leer.
- **„Rückerstattung“ – Achtung, umgekehrte Logik:** Das Kästchen heißt in der App „Verzicht auf Rückerstattung überzahlter Beiträge (Kreuz = KEINE Rückerstattung)“. Wählen Sie **Ja (Verzicht)**, setzt die App das Kreuz – überzahlte Beiträge werden dann **nicht** zurückerstattet. Wählen Sie **Nein**, bleibt das Kästchen leer (kein Verzicht erklärt). Die Oberfläche zeigt dazu den Warnhinweis „Achtung: Ein Kreuz bedeutet KEINE Rückerstattung“.
- Unterschrift und Datum (Abschnitt D) bleiben leer. Hinweis aus der Zuordnungsdatei: **Firmenstempel des Arbeitgebers** erforderlich.

---

## SL18477 – Übernahme der Versicherungsnehmer-Eigenschaft

| # | PDF-Feldname | S. | Art | Quelle im Datenmodell | Pflicht / Bedingung | rechtl. relevant | Befüllung |
|---|---|---|---|---|---|---|---|
| 1 | `Versicherung Nummer` | 1 | Text | `contract.number` – Vertrags-/Versicherungsnummer | Pflicht | – | automatisch nach Bestätigung |
| 2 | `Versicherte Person` | 1 | Text | zusammengesetzt: `insured.first_name` + `insured.last_name` | Pflicht | – | automatisch nach Bestätigung |
| 3 | `Fortführung der Versicherung durch den neuen Arbeitgeber` | 1 | Ankreuzfeld | `order.continuation_mode` – Art der Fortführung; Kreuz bei „A – Fortführung durch den neuen Arbeitgeber“ | Pflicht | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 4 | `Firma` | 1 | Text | `order.new_employer_company` – Neuer Arbeitgeber: Firma inkl. Rechtsform | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | – | nur nach ausdrücklicher Eingabe/Auswahl |
| 5 | `Ansprechpartner` | 1 | Text | `order.new_employer_contact` – Neuer Arbeitgeber: Ansprechpartner | optional, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | – | nur nach ausdrücklicher Eingabe/Auswahl |
| 6 | `Straße Hausnummer` | 1 | Text | zusammengesetzt: `order.new_employer_street` + `order.new_employer_house_number` (optional) | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | – | nur nach ausdrücklicher Eingabe/Auswahl |
| 7 | `Postleitzahl` | 1 | Text | `order.new_employer_postal_code` – Neuer Arbeitgeber: PLZ | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | – | nur nach ausdrücklicher Eingabe/Auswahl |
| 8 | `Ort` | 1 | Text | `order.new_employer_city` – Neuer Arbeitgeber: Ort | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | – | nur nach ausdrücklicher Eingabe/Auswahl |
| 9 | `Datum 1.1` | 1 | Datumsteil | `order.takeover_effective_month` – Übernahme mit Wirkung zum 01. (Monat/Jahr); Teil: Monat | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 10 | `Datum 1.21` | 1 | Datumsteil | `order.takeover_effective_month` – Übernahme mit Wirkung zum 01. (Monat/Jahr); Teil: Jahr | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 11 | `Datum 2.1` | 1 | Datumsteil | `order.premium_start_month` – Beitragszahlung beginnt ab 01. (Monat/Jahr); Teil: Monat | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 12 | `Datum 2.21` | 1 | Datumsteil | `order.premium_start_month` – Beitragszahlung beginnt ab 01. (Monat/Jahr); Teil: Jahr | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 13 | `Versicherung durch die versicherte Person` | 1 | Ankreuzfeld | `order.continuation_mode` – Art der Fortführung; Kreuz bei „C – Private Fortführung durch die versicherte Person“ | Pflicht | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 14 | `Name Vorname` | 1 | Text | zusammengesetzt: `insured.last_name` + `insured.first_name` | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 15 | `Datum 3.1` | 1 | Datumsteil | `insured.birth_date` – Geburtsdatum; Teil: Tag | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 16 | `Datum 3.21` | 1 | Datumsteil | `insured.birth_date` – Geburtsdatum; Teil: Monat | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 17 | `Datum 3.32` | 1 | Datumsteil | `insured.birth_date` – Geburtsdatum; Teil: Jahr | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 18 | `Straße Hausnummer 1` | 1 | Text | zusammengesetzt: `insured.street` + `insured.house_number` (optional) | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 19 | `Postleitzahl 1` | 1 | Text | `insured.postal_code` – PLZ (versicherte Person) | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 20 | `Ort 1` | 1 | Text | `insured.city` – Ort (versicherte Person) | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 21 | `Geburtsort` | 1 | Text | `insured.birth_place` – Geburtsort | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 22 | `Stattsangehörigkeit` | 1 | Text | `insured.nationality` – Staatsangehörigkeit | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person oder „Private Fortführung für den Zwischenzeitraum“ = ja | – | automatisch nach Bestätigung |
| 23 | `Fortführung der Versicherung` | 1 | Optionsgruppe | `order.private_contribution_option` – Private Fortführung: Beitragsvariante; Zustände: „0“ = mit unverändertem Beitrag fortgeführt, „1“ = mit reduziertem Beitrag fortgeführt, „2“ = beitragsfrei gestellt | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 24 | `reduzierter Beitrag` | 1 | Betrag | `order.reduced_premium_amount` – Reduzierter Beitrag (Euro) | Pflicht, nur wenn „Art der Fortführung“ = C – Private Fortführung durch die versicherte Person und „Private Fortführung: Beitragsvariante“ = mit reduziertem Beitrag fortgeführt | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 25 | `private Fortführung für den Zwischenzeitraum` | 1 | Ankreuzfeld | `order.interim_private_continuation` – Private Fortführung für den Zwischenzeitraum; Kreuz bei „ja“ | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 26 | `Beitrag für den Zwischenzeitraum` | 1 | Optionsgruppe | `order.interim_contribution_option` – Zwischenzeitraum: Beitragsvariante; Zustände: „0“ = beitragsfrei, „1“ = beitragspflichtig | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber und „Private Fortführung für den Zwischenzeitraum“ = ja | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 27 | `privat gezahlter Beitrag für den Zwischenzeitraum` | 1 | Betrag | `order.interim_premium_amount` – Zwischenzeitraum: Beitrag (Euro) | Pflicht, nur wenn „Art der Fortführung“ = A – Fortführung durch den neuen Arbeitgeber und „Private Fortführung für den Zwischenzeitraum“ = ja und „Zwischenzeitraum: Beitragsvariante“ = beitragspflichtig | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 28 | `politisch exponierte Person` | 2 | Optionsgruppe | `order.pep_status` – Politisch exponierte Person (PeP); Zustände: „0“ = nein, „1“ = ja – übt/übte selbst ein Amt aus, „2“ = ja – Familienmitglied/nahestehende Person einer PeP | Pflicht | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 29 | `Amt PeP` | 2 | Text | `order.pep_office` – PeP: Amt | Pflicht, nur wenn „Politisch exponierte Person (PeP)“ = ja – übt/übte selbst ein Amt aus | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 30 | `nahestehende Person Amt PeP` | 2 | Text | `order.pep_related_person` – PeP: Name, Vorname, Amt der Bezugsperson | Pflicht, nur wenn „Politisch exponierte Person (PeP)“ = ja – Familienmitglied/nahestehende Person einer PeP | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 31 | `Beitragszahlung` | 2 | Optionsgruppe | `order.payment_method` – Beitragszahlung; Zustände: „0“ = SEPA-Lastschriftmandat bis auf Widerruf, „1“ = per Rechnung (Überweisung) | Pflicht | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 32 | `IBAN` | 2 | Text | `order.payment_iban` – IBAN (für Beitragszahlung) | Pflicht, nur wenn „Beitragszahlung“ ist SEPA-Lastschriftmandat bis auf Widerruf oder per Rechnung (Überweisung) | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 33 | `Geldinstitut` | 2 | Text | `order.payment_institution` – Geldinstitut (für Beitragszahlung) | optional, nur wenn „Beitragszahlung“ ist SEPA-Lastschriftmandat bis auf Widerruf oder per Rechnung (Überweisung) | – | nur nach ausdrücklicher Eingabe/Auswahl |
| 34 | `Kontoinhaber` | 2 | Text | `order.payment_account_holder` – Kontoinhaber (für Beitragszahlung) | Pflicht, nur wenn „Beitragszahlung“ ist SEPA-Lastschriftmandat bis auf Widerruf oder per Rechnung (Überweisung) | ja | nur nach ausdrücklicher Eingabe/Auswahl |
| 35 | `Datum Unterschrift 1.1` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 36 | `Datum Unterschrift 1.21` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 37 | `Datum Unterschrift 1.32` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 38 | `Unterschriftsfeld 1` | 2 | Unterschrift | – | – | – | nie – bleibt leer |
| 39 | `Datum Unterschrift 2.1` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 40 | `Datum Unterschrift 2.21` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 41 | `Datum Unterschrift 2.32` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 42 | `Unterschriftsfeld 2` | 2 | Unterschrift | – | – | – | nie – bleibt leer |
| 43 | `Datum Unterschrift 3.1` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 44 | `Datum Unterschrift 3.21` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 45 | `Datum Unterschrift 3.32` | 2 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 46 | `Unterschriftsfeld 3` | 2 | Unterschrift | – | – | – | nie – bleibt leer |
| 47 | `Datum Unterschrift 4.1` | 3 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 48 | `Datum Unterschrift 4.21` | 3 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 49 | `Datum Unterschrift 4.32` | 3 | Datum zur Unterschrift | – | – | – | nie – bleibt leer |
| 50 | `Unterschriftsfeld 4` | 3 | Unterschrift | – | – | – | nie – bleibt leer |

### Besonderheiten SL18477

- **Art der Fortführung** (`order.continuation_mode`) steuert fast alles:
  - **A – Fortführung durch den neuen Arbeitgeber:** Kreuz bei Feld 3. Pflicht sind dann Firma, Anschrift, „Übernahme mit Wirkung zum 01.“ und „Beitragszahlung beginnt ab 01.“ (je Monat und Jahr, Eingabe als `MM.JJJJ`) sowie die Entscheidung zum Zwischenzeitraum.
  - **C – Private Fortführung durch die versicherte Person:** Kreuz bei Feld 13. Pflicht sind dann Name, Geburtsdatum, Anschrift, Geburtsort, Staatsangehörigkeit und die Beitragsvariante.
  - Beide Kreuze hängen an derselben Auswahl. Es kann also immer nur eins gesetzt sein.
- **Zwischenzeitraum** (nur bei A): Erst ausdrücklich entscheiden, ob privat fortgeführt wird. Nur bei „ja“ folgt beitragsfrei oder beitragspflichtig, nur bei beitragspflichtig der Betrag.
- **Personendaten in Abschnitt C bei Zwischenzeitraum:** Wählen Sie bei A die private Fortführung für den Zwischenzeitraum, füllt die App auch Name, Geburtsdatum, Anschrift, Geburtsort und Staatsangehörigkeit in Abschnitt C aus (Felder 14–22). Das beruht auf einer **Annahme** aus der Zuordnungsdatei: Die versicherte Person wird für diese Zeit Versicherungsnehmer. Diese Annahme ist **fachlich mit Swiss Life zu bestätigen**.
- **PeP** (politisch exponierte Person, z. B. Inhaber eines hohen öffentlichen Amts): immer ausdrücklich wählen. Bei „ja“ ist das Amt bzw. die Bezugsperson Pflicht.
- **Beitragszahlung und Bankverbindung:** Zahlungsart, IBAN und Kontoinhaber kommen **nur aus der Auftragserfassung**. Bankdaten aus hochgeladenen Dokumenten (`bank.iban` usw.) übernimmt die App **nie** in dieses Formular. So geraten keine fremden oder veralteten Kontodaten ins Formular.
- **Abschnitt B (Unterlagen) und F (Beratungsverzicht)** haben im PDF keine Formularfelder. Sie werden durch die Unterschriften in Abschnitt G bestätigt. Vor dem Export verlangt die App die Bestätigung: „Abschnitt F (Beratungsverzicht): Der neue Versicherungsnehmer verzichtet mit seiner Unterschrift auf eine Beratung durch Swiss Life. Der Hinweis auf mögliche Nachteile wurde mit dem Kunden besprochen.“ Diese Bestätigung wird **nicht** ins PDF gedruckt.
- **Seiten 3–6 bleiben unverändert:** Einwilligungs- und Schweigepflichtentbindungserklärung, Dienstleisterliste, Datenschutzhinweise. Die Qualitätskontrolle prüft, dass diese Seiten **pixelgleich** mit dem Original sind (mit beiden Darstellungsprogrammen). Die Einwilligung auf Seite 3 erteilt ausschließlich die versicherte Person selbst durch ihre Unterschrift – die App setzt keine Einwilligung.

---

## Nie automatisch befüllt

| Was | Felder | Regel |
|---|---|---|
| **Unterschriften** | SL13065: `Unterschrift`. SL18477: `Unterschriftsfeld 1` bis `Unterschriftsfeld 4` | bleiben **immer** leer; die App erzeugt keine Unterschriften |
| **Datum zur Unterschrift** | SL13065: `Datum Unterschrift 1.1/1.21/1.32`. SL18477: `Datum Unterschrift 1.x` bis `4.x` (je Tag, Monat, Jahr) | bleiben leer und werden bei der Unterschrift von Hand ergänzt |
| **Rechtlich relevante Erklärungen** | Abfindung, Rückerstattung, Art der Fortführung, Übernahme- und Beitragsbeginn, Beitragsvariante und reduzierter Beitrag, Zwischenzeitraum (Ja/Nein, Variante, Betrag), PeP (Status, Amt, Bezugsperson), Beitragszahlung, IBAN, Kontoinhaber | nur nach Ihrer ausdrücklichen Eingabe oder Auswahl. Ohne Entscheidung blockiert die Checkliste den Export. |
| **Weitere Auftragsdaten** | Neuer Arbeitgeber (Firma, Ansprechpartner, Anschrift), Geldinstitut | nur aus Ihrer Eingabe |
| **Beratungsverzicht** | – (kein PDF-Feld) | Bestätigung in der App; wird nicht gedruckt |
| **Seiten 3–6 von SL18477** | gesamter Inhalt | bleiben unverändert |

Technische Absicherung: Auftragsdaten (`order.*`) dürfen in der App nie den Status „Erkannt“ oder „Konflikt“ haben. Die Programmlogik lehnt das ab (`Case.set_field`). Die lokale KI darf solche Felder gar nicht abfragen. Unterschriftsfelder sind im Zuordnungsschema fest auf „nie befüllen“ gesetzt; eine Zuordnung mit anderer Einstellung ist ungültig.

---

## Technische Details der Befüllung

### PDF-Berechtigungen

Beide Originale sind mit einem Rechte-Passwort geschützt (AES, 128 Bit), aber **ohne Öffnungspasswort**. Gemessen mit der App (`pdf/inspector.py`):

| Recht | SL13065 | SL18477 |
|---|---|---|
| Formular ausfüllen | ja | ja |
| Kommentieren | ja | ja |
| Drucken | ja | ja |
| Kopieren | ja | ja |
| Ändern | **nein** | **nein** |
| Seiten zusammenstellen | **nein** | **nein** |

Folgen:

- Die App **füllt nur aus**. Sie behält Verschlüsselung und Rechte bei und umgeht nichts.
- **Kein „Fixieren“** (die Formularfelder in festen Seiteninhalt umwandeln, auch „flach machen“ genannt). Das wäre eine Änderung, die das PDF nicht erlaubt. Die fertige PDF bleibt ausfüllbar. Die Oberfläche begründet das: „Diese Vorlage erlaubt laut PDF-Berechtigungen kein Fixieren der Formularfelder. Die PDF bleibt ausfüllbar.“
- Gespeichert wird **inkrementell**: Die Änderungen werden hinten an die Datei angehängt. Die Ausgabe beginnt dadurch byte-identisch mit dem Original. Die Qualitätskontrolle prüft das.

### Ankreuzfelder und Optionsgruppen

- Die App setzt den Wert (`/V`) und den angezeigten Zustand (`/AS`) auf den **im PDF gemessenen Ein-Zustand**. Sie zeichnet nichts neu; das vorhandene Kreuz-Symbol des Formulars bleibt.
- Gemessene Ein-Zustände:

| Feld | Formular | Ein-Zustände im PDF | Bedeutung |
|---|---|---|---|
| `Abfindung`, `Rückerstattung` | SL13065 | `Ja` | angekreuzt |
| `Fortführung der Versicherung durch den neuen Arbeitgeber`, `Versicherung durch die versicherte Person`, `private Fortführung für den Zwischenzeitraum` | SL18477 | `Ja` | angekreuzt |
| `Fortführung der Versicherung` | SL18477 | `0` / `1` / `2` | `0` = mit unverändertem Beitrag fortgeführt, `1` = mit reduziertem Beitrag fortgeführt, `2` = beitragsfrei gestellt |
| `Beitrag für den Zwischenzeitraum` | SL18477 | `0` / `1` | `0` = beitragsfrei, `1` = beitragspflichtig |
| `politisch exponierte Person` | SL18477 | `0` / `1` / `2` | `0` = nein, `1` = ja – übt/übte selbst ein Amt aus, `2` = ja – Familienmitglied/nahestehende Person einer PeP |
| `Beitragszahlung` | SL18477 | `0` / `1` | `0` = SEPA-Lastschriftmandat bis auf Widerruf, `1` = per Rechnung (Überweisung) |

- Beim Prüfen der Vorlage (Status „Aktiv“) kontrolliert die App, dass jeder zugeordnete Zustand im PDF existiert. Fehlt einer, wird die Vorlage auf „Fehler“ gesetzt („Auswahlzustand … fehlt“).
- Nach dem Ausfüllen liest die Qualitätskontrolle alle Zustände zurück.

### Texte, Datumsteile und Beträge

- **Datum**: Geburtsdatum und Betriebszugehörigkeit werden auf je drei Kästchen verteilt (Tag, Monat, Jahr). „Übernahme mit Wirkung zum 01.“ und „Beitragszahlung beginnt ab 01.“ haben nur Monat und Jahr; der Tag „01.“ ist vorgedruckt.
- **Betrag**: gespeichert als `1234.56`, angezeigt als `1.234,56`. Das passt zum Zahlformat, das im Formular hinterlegt ist (Acrobat `AFNumber_Format`).
- **Schrift**: Beim Ausfüllen verwendet die Bibliothek PyMuPDF Helvetica. Sie hat praktisch dieselben Zeichenbreiten wie das im Formular hinterlegte Arial.
- **Zu langer Text**: Die Schriftgröße wird bis auf 6 pt verkleinert. Passt der Text dann noch nicht, meldet die Checkliste „Text passt nicht ins Feld – bitte kürzen.“ und der Export ist gesperrt.
- **Sonderzeichen**: Darstellbar sind die Zeichen des westeuropäischen Zeichensatzes (WinAnsi), also auch Umlaute und ß. Andere Zeichen meldet die App: „Zeichen … kann in dieser Vorlage nicht dargestellt werden – bitte ersetzen.“

### Qualitätskontrolle vor dem Export

Die App öffnet die erzeugte Datei erneut und prüft unter anderem: Seitenzahl und Seitenformate, byte-identischer Anfang mit dem Original, unveränderte Verschlüsselung und Rechte, zurückgelesene Feldwerte, leere Unterschriftsfelder, Text passt ins Feld, Zeichen darstellbar, keine sonstigen Änderungen an Seiten und Anmerkungen, bei SL18477 pixelgleiche Seiten 3–6. Jede Seite wird mit **MuPDF und PDFium** dargestellt (PDFium ist die PDF-Komponente von Google Chrome). Erst wenn alles bestanden ist, gelangt die PDF in den Exportordner.
