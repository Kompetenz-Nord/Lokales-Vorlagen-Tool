# Briefvorlagen nach DIN 5008

**Kurz gesagt:**

- Die App erzeugt **6 Kundenschreiben** an Versicherer (Katalog-Nr. 3–8). Es sind keine Formulare des Versicherers und sie enthalten kein Logo.
- Der Text besteht aus **festen Textbausteinen** in `config/letter_templates/*.json`. Welche Absätze erscheinen, hängt von Ihren Auswahlen ab.
- Gedruckt werden nur **bestätigte oder selbst eingegebene Werte**. Fehlt etwas, sperrt die App den Export.
- Hinweise mit Paragrafen erscheinen nur in der App, nicht im Brief. Sie sind **prüfbedürftig und keine Rechtsberatung**. Die App berechnet keine Fristen und stellt keine Berechtigung fest.
- **Empfehlung:** Die Textbausteine vor dem produktiven Einsatz **rechtlich prüfen lassen**. Eine juristische Prüfung ist im Projekt nicht dokumentiert.

DIN 5008 ist die deutsche Norm für den Aufbau von Geschäftsbriefen (Ränder, Anschriftfeld, Betreff usw.).

---

## Übersicht

| Nr. | Katalogeintrag | Datei | Betreff (fett) | Kurztitel im Dateinamen |
|---|---|---|---|---|
| 3 | Ordentliche Kündigung | `kuendigung_ordentlich.json` | Kündigung des Versicherungsvertrags | `Kuendigung` |
| 4 | Außerordentliche Kündigung / Sonderkündigung | `kuendigung_ausserordentlich.json` | Außerordentliche Kündigung des Versicherungsvertrags | `Sonderkuendigung` |
| 5 | Befristete Beitragsfreistellung / Beitragsreduzierung (HDI) | `hdi_beitragsfreistellung.json` | je nach Auswahl: Antrag auf (befristete) Beitragsfreistellung bzw. Beitragsreduzierung | `Beitragsaenderung` |
| 6 | Beitragserhöhung / Vertragsanpassung (Continentale) | `continentale_beitragserhoehung.json` | Antrag auf Erhöhung des Beitrags | `Beitragserhoehung` |
| 7 | Bestandsübertrag / Betreuerwechsel an tecis AG | `tecis_bestandsuebertrag.json` | Übertragung der Betreuung meines Vertrags | `Betreuerwechsel` |
| 8 | Widerruf – Antrag / Vertrag / Kündigung | `widerruf.json` | Widerruf meines Antrags / Widerruf meiner Vertragserklärung / Rücknahme meiner Kündigung | `Widerruf` |

- Alle Vorlagen haben die Version `2026-11`.
- Die Texte sind **versichererneutral**. „HDI“, „Continentale“ und „tecis“ im Katalognamen beschreiben den ursprünglichen Anwendungsfall. Den Empfänger tragen Sie selbst ein.
- Dateiname des Exports: `JJJJ-MM-TT_BRIEF_DIN5008_<Kurztitel>_<Vorgangs-ID>.pdf`, z. B. `2026-11-13_BRIEF_DIN5008_Kuendigung_V20261113-AB12CD.pdf`.
- Unter jedem Betreff stehen zwei weitere Zeilen: „Versicherungsnummer: …“ (Pflicht) und „Versicherte Person: Vorname Nachname“ (nur wenn der Nachname der versicherten Person bestätigt ist).

---

## Gemeinsame Regeln für alle Briefe

**Angaben, die jeder Brief braucht:**

| Angabe | Datenfeld | Pflicht? |
|---|---|---|
| Absender = Versicherungsnehmer: Name bzw. Firma | `policyholder.name` | Pflicht |
| Vorname des Versicherungsnehmers | `policyholder.first_name` | optional |
| Straße, PLZ, Ort des Versicherungsnehmers | `policyholder.street`, `policyholder.postal_code`, `policyholder.city` | Pflicht |
| Hausnummer des Versicherungsnehmers | `policyholder.house_number` | optional |
| Versicherungsnummer | `contract.number` | Pflicht |
| Datum des Schreibens | `order.letter_date` | Pflicht. Die App schlägt das heutige Datum vor und führt einen unveränderten Vorschlag auf das jeweils aktuelle Datum nach. Die Checkliste erinnert: „Vorschlag übernommen – bitte prüfen: Datum des Schreibens“, bis Sie das Datum selbst eintragen. |
| Empfänger: Versicherer/Gesellschaft, Straße oder Postfach, PLZ, Ort | `order.recipient_company`, `order.recipient_street`, `order.recipient_postal_code`, `order.recipient_city` | Pflicht |
| Empfänger: Abteilung, Hausnummer | `order.recipient_department`, `order.recipient_house_number` | optional |
| Telefon, E-Mail des Absenders | `order.sender_phone`, `order.sender_email` | optional |
| Anlagen | `order.attachments` | optional; mehrere Einträge mit `;` oder Zeilenumbruch trennen |

**So entscheidet die App:**

- Gedruckt werden nur Werte mit Status „Bestätigt“ oder „Manuell eingetragen“.
- Fehlt ein Pflichtwert, zeigt die Vorschau rot `[Fehlt: …]`. Der Export ist dann gesperrt.
- **Rechtlich relevante Auswahlen** (z. B. Beendigungszeitpunkt, SEPA widerrufen ja/nein) haben **keine Vorauswahl**. Ohne Entscheidung meldet die Checkliste „Bitte ausdrücklich entscheiden: …“.
- Das gilt auch für rechtlich relevante **optionale** Angaben, von denen ein Absatz abhängt (z. B. „Befristet bis“). Entweder einen Wert eintragen oder bewusst „nicht relevant“ wählen.
- Knopf **„Versicherer als Empfänger übernehmen“**: übernimmt den bestätigten Versicherernamen als Empfänger. Die Anschrift ergänzen und prüfen Sie selbst.
- Jede Vorlage außer Nr. 7 verlangt vor dem Export eine **Pflicht-Bestätigung** (siehe je Brief). Wechseln Sie die Vorlage, setzt die App diese Bestätigung zurück („Vorlage gewechselt – Bestätigung erneut erforderlich“).
- Die Unterschrift erfolgt handschriftlich. Der Platz dafür bleibt frei.

**Plausibilitätsprüfungen** (Warnung = Hinweis; Fehler = Export gesperrt):

| Regel | gilt für Nr. | Meldung bei … | Schwere |
|---|---|---|---|
| Briefdatum plausibel | 3–8 | Briefdatum mehr als 14 Tage in der Zukunft oder mehr als 30 Tage zurück | Warnung |
| Gewünschter Termin | 3, 4 | gewünschter Beendigungszeitpunkt liegt in der Vergangenheit | Warnung |
| Direktversicherung (bAV) | 3, 4 | Produkt/Vertragstyp deutet auf Direktversicherung bzw. betriebliche Altersversorgung: Versicherungsnehmer ist meist der Arbeitgeber, Verfügungsbeschränkungen (§ 2 Abs. 2 BetrAVG) | Warnung |
| Datum der Mitteilung | 4 | liegt in der Zukunft | **Fehler** |
| Datum der Mitteilung | 4 | liegt mehr als einen Monat zurück (kurze Fristen könnten abgelaufen sein) | Warnung |
| Zeitraum der Befristung | 5 | Ende liegt vor oder auf dem Beginn | **Fehler** |
| Beginn der Änderung | 5, 6 | liegt in der Vergangenheit | Warnung |
| Reduzierung | 5 | neuer Beitrag ist nicht niedriger als der bestätigte bisherige Beitrag | Warnung |
| Erhöhung | 6 | Erhöhung ist 0 oder negativ | **Fehler** |
| Erhöhung | 6 | bisheriger Beitrag + Erhöhung ≠ angegebener Gesamtbeitrag | Warnung |

---

## Die 6 Briefe im Einzelnen

### Nr. 3 – Ordentliche Kündigung

**Zweck:** Fristgerechte Kündigung eines Versicherungsvertrags.

| Eingabe | Pflicht? |
|---|---|
| Beendigungszeitpunkt: „zum angegebenen Datum (hilfsweise zum nächstmöglichen Zeitpunkt)“ oder „zum nächstmöglichen Zeitpunkt“ | Pflicht, rechtlich relevant. „Zum Wirksamwerden der Beitragserhöhung“ ist hier nicht erlaubt (Fehler). |
| Gewünschter Termin | Pflicht bei „zum angegebenen Datum“ |
| SEPA-Lastschriftmandat zum Vertragsende widerrufen? | ja/nein ausdrücklich wählen |
| Auszahlung eines Guthabens auf ein Konto erbitten? | ja/nein ausdrücklich wählen; bei „ja“: Kontoinhaber und IBAN Pflicht, Kreditinstitut optional |
| **Pflicht-Bestätigung:** „Fristen und Termine wurden anhand der Vertragsunterlagen geprüft. Die App berechnet keine Fristen und stellt keine Berechtigung fest.“ | Pflicht |

| Textbaustein | erscheint, wenn … |
|---|---|
| „hiermit kündige ich den oben genannten Versicherungsvertrag fristgerecht ‹Zeitpunkt›.“ | „Beendigungszeitpunkt“ = zum angegebenen Datum (hilfsweise zum nächstmöglichen Zeitpunkt) oder zum nächstmöglichen Zeitpunkt |
| „Bitte bestätigen Sie mir den Eingang dieser Kündigung sowie den Zeitpunkt, zu dem der Vertrag endet, schriftlich.“ | immer |
| „Das Ihnen erteilte SEPA-Lastschriftmandat widerrufe ich mit Wirkung zum Vertragsende.“ | „SEPA-Lastschriftmandat zum Vertragsende widerrufen“ = ja |
| „Ein etwaiges Guthaben bzw. einen Rückkaufswert überweisen Sie bitte auf folgendes Konto:“ | „Auszahlung eines Guthabens auf das angegebene Konto erbitten“ = ja |
| • „Kontoinhaber: ‹Kontoinhaber›“ | „Auszahlung eines Guthabens auf das angegebene Konto erbitten“ = ja |
| • „IBAN: ‹IBAN›“ | „Auszahlung eines Guthabens auf das angegebene Konto erbitten“ = ja |
| • „Kreditinstitut: ‹Kreditinstitut›“ | „Auszahlung eines Guthabens auf das angegebene Konto erbitten“ = ja und „Geldinstitut (für Beitragszahlung)“ ausgefüllt |

‹Zeitpunkt› ist je nach Auswahl „zum ‹Datum›, hilfsweise zum nächstmöglichen Zeitpunkt“ oder „zum nächstmöglichen Zeitpunkt“. Platzhalter in ‹spitzen Klammern› stehen für Ihre Eingaben.

**Hinweise (prüfbedürftig, keine Rechtsberatung):**

- Die Erklärung wird erst mit Zugang beim Versicherer wirksam. Bitte einen Versandweg mit Zugangsnachweis wählen (z. B. Einschreiben, Fax mit Sendebericht) und eine Kopie aufbewahren. Quelle: § 130 Abs. 1 BGB.
- Warnung: Hat der Vertrag einen Rückkaufswert (z. B. kapitalbildende Lebens- oder Rentenversicherung), wird er bei Kündigung ausgezahlt; er kann unter den eingezahlten Beiträgen liegen. Bei manchen Vertragsarten ist eine Auszahlung ausgeschlossen, z. B. bei der Basisrente (Rürup-Rente, nur Beitragsfreistellung möglich) und teilweise in der betrieblichen Altersversorgung. Alternativen (z. B. Beitragsfreistellung) vorher besprechen. Quelle: § 169 VVG, § 168 Abs. 3 VVG, § 2 Abs. 2 BetrAVG.

Der Text nennt bewusst keine Kündigungsfrist. „Hilfsweise zum nächstmöglichen Zeitpunkt“ sichert ab, falls das gewünschte Datum nicht erreichbar ist.

### Nr. 4 – Außerordentliche Kündigung / Sonderkündigung

**Zweck:** Kündigung aus besonderem Anlass, z. B. nach einer Beitragserhöhung oder einem Schadenfall.

| Eingabe | Pflicht? |
|---|---|
| Anlass: Beitragserhöhung / Schadenfall / anderer Anlass | Pflicht, rechtlich relevant |
| Datum der Mitteilung des Versicherers bzw. des Schadenfalls | Pflicht bei Beitragserhöhung und Schadenfall |
| Grund | Pflicht bei „anderer Anlass“ |
| Beendigungszeitpunkt: zum angegebenen Datum (hilfsweise nächstmöglich) / zum nächstmöglichen Zeitpunkt / zum Wirksamwerden der Beitragserhöhung | Pflicht. Die dritte Möglichkeit passt nur zum Anlass Beitragserhöhung (sonst Fehler). |
| Gewünschter Termin | Pflicht bei „zum angegebenen Datum“ |
| Rechtsgrundlage (Freitext) | rechtlich relevant: eintragen **oder** bewusst „nicht relevant“ wählen. Wird nur gedruckt, wenn eingetragen. |
| SEPA widerrufen? Guthaben auszahlen? | wie bei Nr. 3 |
| **Pflicht-Bestätigung:** „Sonderkündigungsrecht und Kündigungsfrist wurden im Einzelfall geprüft. Die App stellt keine Berechtigung fest.“ | Pflicht |

| Textbaustein | erscheint, wenn … |
|---|---|
| „mit Schreiben vom ‹Datum› haben Sie mir eine Beitragserhöhung für den oben genannten Vertrag mitgeteilt. Aus diesem Anlass kündige ich den Vertrag hiermit außerordentlich ‹Zeitpunkt›.“ | „Anlass der außerordentlichen Kündigung“ = Beitragserhöhung |
| „im Zusammenhang mit dem Schadenfall vom ‹Datum› kündige ich den oben genannten Versicherungsvertrag hiermit außerordentlich ‹Zeitpunkt›.“ | „Anlass der außerordentlichen Kündigung“ = Schadenfall |
| „hiermit kündige ich den oben genannten Versicherungsvertrag außerordentlich ‹Zeitpunkt›. Anlass: ‹Grund›“ | „Anlass der außerordentlichen Kündigung“ = anderer Anlass |
| „Ich stütze die Kündigung auf ‹Rechtsgrundlage›.“ | „Rechtsgrundlage (optional, nur nach Prüfung angeben)“ ausgefüllt |
| „Hilfsweise kündige ich den Vertrag ordentlich zum nächstmöglichen Zeitpunkt.“ | immer |
| „Bitte bestätigen Sie mir den Eingang dieser Kündigung sowie den Zeitpunkt, zu dem der Vertrag endet, schriftlich.“ | immer |
| „Das Ihnen erteilte SEPA-Lastschriftmandat widerrufe ich mit Wirkung zum Vertragsende.“ | „SEPA-Lastschriftmandat zum Vertragsende widerrufen“ = ja |
| „Ein etwaiges Guthaben bzw. einen Rückkaufswert überweisen Sie bitte auf folgendes Konto:“ | „Auszahlung eines Guthabens auf das angegebene Konto erbitten“ = ja |
| • „Kontoinhaber: ‹Kontoinhaber›“ | „Auszahlung eines Guthabens auf das angegebene Konto erbitten“ = ja |
| • „IBAN: ‹IBAN›“ | „Auszahlung eines Guthabens auf das angegebene Konto erbitten“ = ja |
| • „Kreditinstitut: ‹Kreditinstitut›“ | „Auszahlung eines Guthabens auf das angegebene Konto erbitten“ = ja und „Geldinstitut (für Beitragszahlung)“ ausgefüllt |

‹Zeitpunkt› ist je nach Auswahl: „zum ‹Datum›, hilfsweise zum nächstmöglichen Zeitpunkt“, „zum nächstmöglichen Zeitpunkt“ oder „zu dem Zeitpunkt, zu dem die Beitragserhöhung wirksam werden soll“. Der Satz „Hilfsweise kündige ich den Vertrag ordentlich zum nächstmöglichen Zeitpunkt.“ steht immer im Brief. Er sichert ab, falls das Sonderkündigungsrecht nicht besteht.

**Hinweise (prüfbedürftig, keine Rechtsberatung):**

- Die Erklärung wird erst mit Zugang beim Versicherer wirksam. Bitte einen Versandweg mit Zugangsnachweis wählen (z. B. Einschreiben, Fax mit Sendebericht) und eine Kopie aufbewahren. Quelle: § 130 Abs. 1 BGB.
- Bei einer Beitragserhöhung aufgrund einer Anpassungsklausel sieht § 40 VVG ein Kündigungsrecht innerhalb eines Monats nach Zugang der Mitteilung vor, frühestens zum Wirksamwerden der Erhöhung. In der privaten Krankenversicherung gilt § 205 Abs. 4 VVG mit eigener Frist. Bitte im Einzelfall prüfen. Quelle: § 40 VVG, § 205 Abs. 4 VVG. *(nur wenn „Anlass der außerordentlichen Kündigung“ = Beitragserhöhung)*
- Nach einem Schadenfall sehen z. B. § 92 VVG (Sachversicherung) und § 111 VVG (Haftpflichtversicherung) ein Kündigungsrecht mit Monatsfrist vor; der Fristbeginn ist unterschiedlich geregelt. Bitte im Einzelfall prüfen. Quelle: § 92 VVG, § 111 VVG. *(nur wenn „Anlass der außerordentlichen Kündigung“ = Schadenfall)*
- Der Brief enthält immer zusätzlich eine hilfsweise ordentliche Kündigung zum nächstmöglichen Zeitpunkt. Sie greift, falls das Sonderkündigungsrecht nicht besteht oder die Frist versäumt ist. Bei Lebens- und Rentenversicherungen vorher Rückkaufswert und Alternativen (z. B. Beitragsfreistellung) besprechen. In der privaten Krankenversicherung mit Versicherungspflicht wird eine Kündigung nur wirksam, wenn innerhalb von zwei Monaten eine lückenlose Anschlussversicherung nachgewiesen wird. Bitte im Einzelfall prüfen. Quelle: § 169 VVG, § 205 Abs. 6 VVG.

### Nr. 5 – Befristete Beitragsfreistellung / Beitragsreduzierung

**Zweck:** Antrag, den Beitrag (befristet oder unbefristet) auszusetzen oder zu senken.

| Eingabe | Pflicht? |
|---|---|
| Art der Beitragsänderung: Beitragsfreistellung / Beitragsreduzierung | Pflicht, rechtlich relevant |
| Änderung ab (Datum) | Pflicht |
| Befristet bis (Datum) | rechtlich relevant: Datum eintragen **oder** „nicht relevant“ wählen (= unbefristet) |
| Neuer Beitrag (Euro) und Zahlweise | Pflicht bei Reduzierung |
| Beitrag nach Ende der Befristung (Euro) | nur bei Befristung: eintragen oder „nicht relevant“ wählen |
| Bestehendes SEPA-Lastschriftmandat soll bestehen bleiben? | ja/nein ausdrücklich wählen |
| **Pflicht-Bestätigung:** Fristen und Termine geprüft (wie Nr. 3) | Pflicht |

| Textbaustein | erscheint, wenn … |
|---|---|
| „für den oben genannten Vertrag beantrage ich eine befristete Beitragsfreistellung vom ‹Beginn› bis zum ‹Ende›.“ | „Art der Beitragsänderung“ = Beitragsfreistellung und „Befristet bis (Datum, optional)“ ausgefüllt |
| „für den oben genannten Vertrag beantrage ich eine Beitragsfreistellung ab dem ‹Beginn›.“ | „Art der Beitragsänderung“ = Beitragsfreistellung und „Befristet bis (Datum, optional)“ leer bzw. nicht relevant |
| „für den oben genannten Vertrag beantrage ich, den Beitrag vom ‹Beginn› bis zum ‹Ende› auf ‹Betrag› ‹Zahlweise› zu reduzieren.“ | „Art der Beitragsänderung“ = Beitragsreduzierung und „Befristet bis (Datum, optional)“ ausgefüllt |
| „für den oben genannten Vertrag beantrage ich, den Beitrag ab dem ‹Beginn› auf ‹Betrag› ‹Zahlweise› zu reduzieren.“ | „Art der Beitragsänderung“ = Beitragsreduzierung und „Befristet bis (Datum, optional)“ leer bzw. nicht relevant |
| „Nach Ablauf dieses Zeitraums bitte ich darum, die Beitragszahlung in Höhe von ‹Betrag› ‹Zahlweise› wieder aufzunehmen.“ | „Befristet bis (Datum, optional)“ ausgefüllt und „Beitrag nach Ende der Befristung (Euro, optional)“ ausgefüllt |
| „Das bestehende SEPA-Lastschriftmandat soll unverändert bestehen bleiben.“ | „Bestehendes SEPA-Lastschriftmandat soll bestehen bleiben“ = ja |
| „Bitte bestätigen Sie mir die Änderung schriftlich und teilen Sie mir mit, wie sie sich auf die Leistungen und den Versicherungsschutz meines Vertrags auswirkt.“ | immer |

**Hinweise (prüfbedürftig, keine Rechtsberatung):**

- Ob eine Beitragsfreistellung oder -reduzierung möglich ist, regeln die Vertragsbedingungen (z. B. Mindestbeitrag, Mindestvertragsguthaben). Bei Lebensversicherungen siehe auch § 165 VVG (prämienfreie Versicherung). Quelle: Vertragsbedingungen, § 165 VVG.
- Warnung: Bei Berufsunfähigkeits- oder Risikoversicherungen kann eine Freistellung den Versicherungsschutz mindern oder entfallen lassen. Bitte vorher klären. *(nur wenn „Art der Beitragsänderung“ = Beitragsfreistellung)*

### Nr. 6 – Beitragserhöhung / Vertragsanpassung

**Zweck:** Antrag, den Beitrag zu erhöhen.

| Eingabe | Pflicht? |
|---|---|
| Änderung ab (Datum) | Pflicht |
| Erhöhung um (Euro) und Zahlweise | Pflicht |
| Neuer Gesamtbeitrag (Euro) | rechtlich relevant: eintragen **oder** „nicht relevant“ wählen |
| Bestehendes SEPA-Lastschriftmandat soll bestehen bleiben? | ja/nein ausdrücklich wählen |
| **Pflicht-Bestätigung:** Fristen und Termine geprüft (wie Nr. 3) | Pflicht |

| Textbaustein | erscheint, wenn … |
|---|---|
| „für den oben genannten Vertrag beantrage ich, den Beitrag zum ‹Beginn› um ‹Betrag› ‹Zahlweise› zu erhöhen.“ | immer |
| „Der neue Gesamtbeitrag beträgt damit ‹Betrag› ‹Zahlweise›.“ | „Neuer Beitrag (Euro)“ ausgefüllt |
| „Das bestehende SEPA-Lastschriftmandat soll für den neuen Beitrag unverändert bestehen bleiben.“ | „Bestehendes SEPA-Lastschriftmandat soll bestehen bleiben“ = ja |
| „Bitte bestätigen Sie mir die Vertragsänderung schriftlich und senden Sie mir einen Nachtrag zum Versicherungsschein zu. Sollten für die Erhöhung weitere Angaben oder Formulare erforderlich sein (z. B. Gesundheitsfragen), bitte ich um Zusendung.“ | immer |

**Hinweise (prüfbedürftig, keine Rechtsberatung):**

- Je nach Tarif kann eine Erhöhung eine erneute Risikoprüfung (z. B. Gesundheitsfragen) erfordern; für den Erhöhungsteil können neue Wartezeiten oder Fristen gelten. Quelle: Vertragsbedingungen.

### Nr. 7 – Bestandsübertrag / Betreuerwechsel

**Zweck:** Bitte an den Versicherer, die **Betreuung** des Vertrags auf eine neue Vermittlerin bzw. einen neuen Vermittler zu übertragen. Der Vertrag selbst bleibt beim Versicherer.

| Eingabe | Pflicht? |
|---|---|
| Neue Betreuung: Gesellschaft | Pflicht. Vorschlag der Vorlage: „tecis Finanzdienstleistungen AG“ (markiert mit „Vorschlag der Vorlage – bitte prüfen“) |
| Beraterin/Berater: Name, Straße, PLZ, Ort | Pflicht |
| Hausnummer, Vermittlernummer | optional |
| Vertragsänderungen dürfen auch über die neue Betreuung veranlasst werden (Vollmacht)? | ja/nein ausdrücklich wählen |
| Einverständnis: Vertragsdaten an die neue Betreuung übermitteln? | ja/nein ausdrücklich wählen |

| Textbaustein | erscheint, wenn … |
|---|---|
| „hiermit bitte ich Sie, die Betreuung des oben genannten Vertrags auf die ‹Gesellschaft› zu übertragen. Meine neue Ansprechpartnerin bzw. mein neuer Ansprechpartner ist:“ | immer |
| • „‹Name›“ | immer |
| • „‹Straße› ‹Hausnummer›“ | immer |
| • „‹PLZ› ‹Ort›“ | immer |
| • „Vermittlernummer: ‹Vermittlernummer›“ | „Neue Betreuung: Vermittlernummer (optional)“ ausgefüllt |
| „Vertragsänderungen können künftig auch über die neue Betreuung veranlasst werden.“ | „Vertragsänderungen dürfen auch über die neue Betreuung veranlasst werden (Vollmacht)“ = ja |
| „Ich bin damit einverstanden, dass Sie der neuen Betreuung die für die Betreuung meines Vertrags erforderlichen Vertragsdaten übermitteln.“ | „Einverständnis: Vertragsdaten an die neue Betreuung übermitteln“ = ja |
| „Bitte bestätigen Sie mir die Übertragung der Betreuung schriftlich.“ | immer |

**Hinweise (prüfbedürftig, keine Rechtsberatung):**

- Viele Versicherer verlangen für einen Betreuerwechsel zusätzlich eine Maklervollmacht oder ein eigenes Formular. Bitte vorab beim Versicherer bzw. bei der neuen Betreuung klären.
- Bei Lebens-, Kranken- und Unfallversicherungen unterliegen Vertragsdaten einer besonderen Schweigepflicht; eine Weitergabe an die neue Betreuung erfordert eine Einwilligung der Kundin bzw. des Kunden. Quelle: § 203 Abs. 1 StGB, Art. 6 DSGVO.

### Nr. 8 – Widerruf (Antrag / Vertrag) und Rücknahme einer Kündigung

**Zweck:** Widerruf eines Antrags oder einer Vertragserklärung – oder die **Bitte**, eine eigene Kündigung zurückzunehmen.

| Eingabe | Pflicht? |
|---|---|
| Gegenstand: Antrag / Versicherungsvertrag (Vertragserklärung) / Rücknahme einer eigenen Kündigung | Pflicht, rechtlich relevant |
| Antrag vom (Datum) | Pflicht bei „Antrag“ |
| Kündigung vom (Datum) | Pflicht bei „Rücknahme einer Kündigung“ |
| Erstattung bereits gezahlter Beiträge auf ein Konto erbitten? | bei Antrag/Vertrag ja/nein wählen; bei „ja“: Kontoinhaber und IBAN Pflicht, Kreditinstitut optional |
| **Pflicht-Bestätigung:** „Widerrufsfrist bzw. Möglichkeit der Rücknahme wurden geprüft. Die App prüft keine Fristen.“ | Pflicht |

| Textbaustein | erscheint, wenn … |
|---|---|
| „hiermit widerrufe ich meinen Antrag vom ‹Datum› auf Abschluss des oben genannten Versicherungsvertrags.“ | „Gegenstand des Widerrufs“ = Antrag |
| „hiermit widerrufe ich meine Vertragserklärung zum oben genannten Versicherungsvertrag.“ | „Gegenstand des Widerrufs“ = Versicherungsvertrag (Vertragserklärung) |
| „Bitte bestätigen Sie mir den Eingang dieses Widerrufs sowie die Rückabwicklung des Vertrags schriftlich.“ | „Gegenstand des Widerrufs“ = Antrag oder Versicherungsvertrag (Vertragserklärung) |
| „Bereits gezahlte Beiträge erstatten Sie bitte, soweit sie mir zustehen, auf folgendes Konto:“ | „Gegenstand des Widerrufs“ = Antrag oder Versicherungsvertrag (Vertragserklärung) und „Erstattung bereits gezahlter Beiträge auf das angegebene Konto erbitten“ = ja |
| • „Kontoinhaber: ‹Kontoinhaber›“ | „Gegenstand des Widerrufs“ = Antrag oder Versicherungsvertrag (Vertragserklärung) und „Erstattung bereits gezahlter Beiträge auf das angegebene Konto erbitten“ = ja |
| • „IBAN: ‹IBAN›“ | „Gegenstand des Widerrufs“ = Antrag oder Versicherungsvertrag (Vertragserklärung) und „Erstattung bereits gezahlter Beiträge auf das angegebene Konto erbitten“ = ja |
| • „Kreditinstitut: ‹Kreditinstitut›“ | „Gegenstand des Widerrufs“ = Antrag oder Versicherungsvertrag (Vertragserklärung) und „Erstattung bereits gezahlter Beiträge auf das angegebene Konto erbitten“ = ja und „Geldinstitut (für Beitragszahlung)“ ausgefüllt |
| „meine Kündigung vom ‹Datum› zum oben genannten Vertrag nehme ich hiermit zurück. Ich bitte Sie, den Vertrag unverändert fortzuführen, und um eine schriftliche Bestätigung.“ | „Gegenstand des Widerrufs“ = Rücknahme einer eigenen Kündigung |

**Hinweise (prüfbedürftig, keine Rechtsberatung):**

- Widerrufsfrist in der Regel 14 Tage, bei Lebensversicherungen 30 Tage. Sie beginnt erst, wenn Versicherungsschein, Vertragsbestimmungen, Informationen und Widerrufsbelehrung in Textform zugegangen sind. Textform genügt, eine Begründung ist nicht nötig; zur Fristwahrung genügt die rechtzeitige Absendung. Quelle: § 8 Abs. 1 und 2 VVG, § 152 Abs. 1 VVG. *(nur wenn „Gegenstand des Widerrufs“ = Antrag oder Versicherungsvertrag (Vertragserklärung))*
- Warnung: Eine Kündigung wird mit Zugang wirksam und kann nur widerrufen werden, wenn der Widerruf vorher oder gleichzeitig zugeht. Danach ist eine Fortführung nur mit Zustimmung des Versicherers möglich – der Brief ist deshalb als Bitte formuliert. Quelle: § 130 Abs. 1 Satz 2 BGB. *(nur wenn „Gegenstand des Widerrufs“ = Rücknahme einer eigenen Kündigung)*
- Die Erklärung wird erst mit Zugang beim Versicherer wirksam. Bitte einen Versandweg mit Zugangsnachweis wählen (z. B. Einschreiben, Fax mit Sendebericht) und eine Kopie aufbewahren. Quelle: § 130 Abs. 1 BGB.

---

## Aufbau nach DIN 5008 (wie die App den Brief setzt)

Umgesetzt in `src/insurance_app/letters/din5008.py`. Form B ist Standard. Form A wählen Sie unter „Einstellungen & Datenschutz“ → „Export“ → „Briefaufbau nach DIN 5008“.

| Element | Form B (Standard) | Form A |
|---|---|---|
| Briefkopf (Absender) | Höhe 45 mm. Name fett 12 pt, darunter Anschrift in einer Zeile 9,5 pt | Höhe 27 mm |
| Anschriftfeld | links 20 mm, oben 45 mm, 85 × 45 mm | oben 27 mm |
| – Zusatz- und Vermerkzone | 17,7 mm hoch; enthält die Rücksendeangabe (Absender in einer Zeile, 7 pt, bei Bedarf kleiner bis 5,5 pt) | gleich |
| – Anschriftzone | 27,3 mm, höchstens 6 Zeilen, 10 pt (bei zu langen Zeilen kleiner bis 8 pt) | gleich |
| Informationsblock | links 125 mm, oben 50 mm, 75 mm breit: Versicherungsnehmer, Telefon, E-Mail, Datum | oben 32 mm |
| Textränder | links 25 mm (Fluchtlinie), rechts 20 mm | gleich |
| Betreff | fett, ohne das Wort „Betreff“, zwei Leerzeilen unter Anschriftfeld bzw. Informationsblock | gleich |
| Falzmarken | 105 mm und 210 mm | 87 mm und 192 mm |
| Lochmarke | 148,5 mm | 148,5 mm |

Weitere Regeln:

- **Schrift:** Liberation Sans (gleiche Zeichenbreiten wie Arial), in die PDF eingebettet. Fließtext 11 pt, Zeilenabstand 14 pt.
- **Anrede:** „Sehr geehrte Damen und Herren,“ – der Text beginnt danach klein („hiermit …“).
- **Grußformel:** „Mit freundlichen Grüßen“ **ohne Komma**. Darunter bleibt Platz für die Unterschrift, dann folgt der Name.
- **Anlagen:** Überschrift „Anlage“ bzw. „Anlagen“ (fett), darunter die Einträge.
- **Mehrseitige Briefe:** „Seite x von y“ unten rechts.
- **Vorschau:** Wasserzeichen „VORSCHAU – NICHT VERSENDEN“, fehlende Werte rot als `[Fehlt: …]`.
- **Metadaten der PDF:** Titel = Vorlagentitel, Autor und Betreff leer.

**Brief-Qualitätskontrolle** (`letters/qa.py`) vor dem Export: Datei lässt sich öffnen, Format A4, Text vorhanden, keine Platzhalter `[Fehlt: …]`, kein Wasserzeichen, Schriften eingebettet, Anschrift liegt im Sichtfenster, genug Platz für die Unterschrift, Metadaten ohne Absendername, Darstellung mit MuPDF und PDFium, keine offenen Pflichtpunkte.

---

## Prüfung der bisherigen Beispielvorlagen

Der Anwender hat vorhandene Musterschreiben zur Prüfung bereitgestellt. **Personenbezogene Angaben aus diesen Schreiben sind hier bewusst weggelassen.** Gefundene Probleme und wie die neuen Vorlagen sie vermeiden:

| # | Befund in den bisherigen Schreiben | Warum problematisch | Lösung in den neuen Vorlagen |
|---|---|---|---|
| 1 | Beitragsfreistellung: **Ende der Befristung lag vor dem Beginn** | Der Antrag ist in sich widersprüchlich und nicht bearbeitbar. | Plausibilitätsregel „Zeitraum der Befristung“: Fehler, Export gesperrt. Automatisch getestet. |
| 2 | **Beitragsfreistellung und Beitragsreduzierung vermischt** | Zwei verschiedene Anträge mit unterschiedlichen Folgen. | Getrennte Auswahl „Art der Beitragsänderung“; eigener Betreff und eigener Text je Variante. |
| 3 | Verweis auf **§ 175 SGB V** | Die Vorschrift betrifft nur die gesetzlichen Krankenkassen, nicht private Versicherungsverträge. | Verweis entfernt. Ein Test stellt sicher, dass der Widerrufsbrief „175“ nicht enthält. |
| 4 | **Widerruf und Rücktritt vermischt** | Unterschiedliche Rechte mit unterschiedlichen Voraussetzungen und Folgen. | Der Brief erklärt nur einen Widerruf. Ein Test stellt sicher, dass er keine Rücktrittserklärung („trete … zurück“) enthält. |
| 5 | **Kündigung „widerrufen“** | Eine zugegangene Kündigung kann nicht einseitig widerrufen werden (§ 130 Abs. 1 Satz 2 BGB). | Variante „Rücknahme meiner Kündigung“ als **Bitte** um Fortführung, mit Warnhinweis in der App. |
| 6 | „**Vertrag übertragen**“ statt Betreuung | Beim Betreuerwechsel bleibt der Vertrag beim Versicherer; nur die Betreuung wechselt. | Formulierung „Übertragung der Betreuung“. Vollmacht und Datenweitergabe sind getrennte, ausdrückliche Entscheidungen. Automatisch getestet. |
| 7 | **Empfängeranschriften fehlten** | Ohne Anschrift kein ordnungsgemäßer Zugang. | Gesellschaft, Straße/Postfach, PLZ und Ort sind Pflicht. Die Brief-QA prüft die Lage im Sichtfenster. Knopf „Versicherer als Empfänger übernehmen“. |
| 8 | **Komma nach der Grußformel** | Nach DIN 5008 steht nach „Mit freundlichen Grüßen“ kein Komma. | Grußformel fest ohne Komma. |

---

## Textbausteine bearbeiten

### Wo und wie

- Je Brief eine Datei in `config/letter_templates/`. Der Katalog verweist über `letter_file` in `config/template_catalog.json` darauf.
- Format: JSON (ein strukturiertes Textformat), Zeichensatz UTF-8.
- **Nur beim Start aus dem Quellcode änderbar.** In der gebauten App liegen die Dateien im Programmpaket. Änderungen erfordern dort einen neuen Build.

Aufbau einer Datei:

| Schlüssel | Bedeutung |
|---|---|
| `template_id`, `version` | Kennung und Version, z. B. `brief_kuendigung_ordentlich_2026-11` und `2026-11` |
| `catalog_id` | Katalogeintrag, zu dem der Brief gehört |
| `title`, `short_title` | Anzeigename; Kurztitel für den Dateinamen (Umlaute und Sonderzeichen werden beim Export umgeschrieben bzw. entfernt) |
| `subject_variants` | Betreff-Varianten mit Bedingungen; die **erste zutreffende** wird fett gedruckt |
| `subject_lines` | weitere Betreffzeilen (normal gedruckt) |
| `info_block` | Zeilen im Informationsblock rechts (`optional: true` = leere Zeile entfällt) |
| `salutation`, `closing` | Anrede und Grußformel |
| `blocks` | Textbausteine: `text`, `conditions`, `style` (`paragraph` = Absatz mit Leerzeile danach, `line` = ohne Leerzeile, `list` = eingerückte Zeile) |
| `derived` | abgeleitete Bausteine, deren Text von einer Auswahl abhängt (z. B. Beendigungszeitpunkt) |
| `defaults` | feste Vorschlagswerte, **nur ohne Personenbezug** (z. B. Name einer Gesellschaft) |
| `allowed_values` | erlaubte Auswahlen mit Fehlermeldung |
| `acknowledgments` | Pflicht-Bestätigungen vor dem Export |
| `plausibility_rules` | Namen der Plausibilitätsregeln (siehe Tabelle oben) |
| `hints` | prüfbedürftige Hinweise mit `source` und `severity` (`INFO`/`WARNING`); werden **nicht** gedruckt |
| `notes` | interne Anmerkungen |

**Platzhalter im Text:**

| Schreibweise | Wirkung |
|---|---|
| `{policyholder.name}` | Pflichtwert; fehlt er, ist der Export gesperrt |
| `{order.recipient_department?}` | optionaler Wert; fehlt er, bleibt die Stelle leer |
| `{order.desired_date\|date}` | mit Format: `date` (TT.MM.JJJJ), `amount` (1.234,56 €), `interval` (monatlich …), `choice` (Beschriftung der Auswahl), `iban` (Vierergruppen), `text` |
| `{@timing_phrase}` | abgeleiteter Baustein aus `derived` |

Feldschlüssel müssen im Feldregister stehen (`src/insurance_app/core/fields.py`). Neue Felder erfordern eine Programmänderung.

**Bedingungen** (alle Bedingungen einer Liste müssen erfüllt sein):

| Schreibweise | erfüllt, wenn … |
|---|---|
| `{"field": "order.revoke_sepa", "equals": "true"}` | der Wert genau so lautet (Ja/Nein: `true`/`false`; Auswahl: interner Schlüssel wie `exemption`) |
| `{"field": "order.revocation_subject", "in": ["application", "contract"]}` | der Wert einer der genannten ist |
| `{"field": "order.change_end_date", "empty": true}` | das Feld leer bzw. „nicht relevant“ ist |
| `{"field": "order.legal_basis_text"}` | das Feld ausgefüllt ist |

Hängt eine Bedingung an einem **rechtlich relevanten** Feld, verlangt die App dafür eine ausdrückliche Entscheidung.

### Schritte

1. App schließen. Datei sichern (am besten über Git).
2. Text ändern. Keine Personendaten in Texte oder `defaults` schreiben.
3. **Version erhöhen:** `version` (z. B. `2027-01`) und die Endung von `template_id` (z. B. `brief_kuendigung_ordentlich_2027-01`). Offene Vorgänge mit diesem Brief verlangen danach eine erneute Vorlagenwahl.
4. Prüfen:

   ```bash
   python app.py --selftest                 # Brief muss „Aktiv (Brief DIN 5008)“ zeigen
   python -m pytest -q tests/test_letters   # Tests der Briefvorlagen
   ```

   Ist die Datei ungültig (z. B. JSON-Fehler, unbekannter Filter), zeigt der Katalog den Status „Fehler“ mit „Briefvorlage fehlerhaft (…)“.
   Einige Tests prüfen bestimmte Formulierungen. Nach Textänderungen müssen sie ggf. angepasst werden.
5. Musterfall öffnen, Brief wählen, Vorschau ansehen.
6. Geänderte Texte rechtlich prüfen lassen.

### Versionierung

- Bei der Vorlagenwahl speichert der Vorgang die Kennung und Version der Vorlage. Das Exportprotokoll vermerkt bei jedem Export Vorlagen-ID und Version.
- Ändert sich die Version **nach** der Auswahl, sperrt die App Vorschau und Export mit dem Hinweis „Die Vorlage wurde inzwischen geändert (neue Version). Bitte die Vorlage erneut auswählen.“ So entsteht kein Brief mit einem Text, den Sie nicht gesehen haben.
- Bereits erzeugte PDFs bleiben unverändert.
- Die App bewahrt alte Brieftexte nicht auf. Für eine Historie der Texte: Änderungen mit Git festhalten.
