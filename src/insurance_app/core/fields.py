"""Feldregister des zentralen Datenmodells.

Strikte Trennung:
* ``FieldCategory.CONTRACT`` – Vertragsdaten, die aus Quelldokumenten stammen
  (oder manuell erfasst werden).
* ``FieldCategory.ORDER`` – Auftrags-/Wunschdaten, die der Anwender aktiv
  auswählt (z. B. Kündigungsgrund, Fortführungsart, PEP-Angabe). Diese Felder
  dürfen NIEMALS automatisch aus Dokumenten oder durch KI befüllt werden.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FieldCategory(StrEnum):
    CONTRACT = "CONTRACT"
    ORDER = "ORDER"


class FieldType(StrEnum):
    TEXT = "TEXT"
    NAME = "NAME"
    DATE = "DATE"  # TT.MM.JJJJ  -> normalisiert YYYY-MM-DD
    MONTH = "MONTH"  # MM.JJJJ     -> normalisiert YYYY-MM
    AMOUNT = "AMOUNT"  # Betrag      -> normalisiert 1234.56
    CURRENCY = "CURRENCY"  # ISO 4217, z. B. EUR
    IBAN = "IBAN"
    POSTAL_CODE = "POSTAL_CODE"
    COUNTRY = "COUNTRY"
    CONTRACT_NUMBER = "CONTRACT_NUMBER"
    INTEGER = "INTEGER"
    BOOL = "BOOL"  # ausdrückliche Ja/Nein-Entscheidung
    CHOICE = "CHOICE"  # Auswahl aus ``choices``


class FieldDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    label: str
    group: str
    dtype: FieldType
    category: FieldCategory = FieldCategory.CONTRACT
    sensitive: bool = False  # personenbezogen / Bankdaten -> nie in Logs
    legal_relevant: bool = False  # rechtlich relevante Erklärung -> nur explizite Nutzerentscheidung
    choices: dict[str, str] | None = None  # Schlüssel -> deutsche Beschriftung
    help: str = ""
    extractable: bool = True  # darf aus Dokumenten extrahiert werden (ORDER: immer False)


_C = FieldCategory.CONTRACT
_O = FieldCategory.ORDER
T = FieldType

FREQUENCY_CHOICES = {
    "monthly": "monatlich",
    "quarterly": "vierteljährlich",
    "semiannual": "halbjährlich",
    "annual": "jährlich",
    "single": "Einmalbeitrag",
}


def _f(key: str, label: str, group: str, dtype: FieldType, **kw) -> FieldDef:
    category = kw.get("category", _C)
    if category == _O:
        kw["extractable"] = False
    return FieldDef(key=key, label=label, group=group, dtype=dtype, **kw)


_FIELDS: list[FieldDef] = [
    # --- Vertrag ---------------------------------------------------------
    _f("insurer.name", "Versicherer", "Vertrag", T.TEXT),
    _f("contract.product", "Produkt / Versicherungsart", "Vertrag", T.TEXT),
    _f("contract.contract_type", "Vertragstyp (z. B. Direktversicherung, Eigenvertrag)", "Vertrag", T.TEXT),
    _f("contract.tariff", "Tarif", "Vertrag", T.TEXT),
    _f("contract.number", "Vertrags-/Versicherungsnummer", "Vertrag", T.CONTRACT_NUMBER, sensitive=True),
    _f(
        "contract.policy_number",
        "Policennummer (falls abweichend)",
        "Vertrag",
        T.CONTRACT_NUMBER,
        sensitive=True,
    ),
    _f("contract.application_number", "Antragsnummer", "Vertrag", T.CONTRACT_NUMBER, sensitive=True),
    _f("contract.start_date", "Versicherungsbeginn", "Vertrag", T.DATE),
    _f(
        "contract.end_date",
        "Ablaufdatum / Vertragsende",
        "Vertrag",
        T.DATE,
        help="Nur übernehmen, wenn ausdrücklich als Ablauf/Vertragsende belegt (nicht Rentenbeginn).",
    ),
    _f("contract.end_age", "Ablaufalter", "Vertrag", T.INTEGER, help="Nur wenn ausdrücklich belegt."),
    _f("contract.premium_end_date", "Letzte Beitragsfälligkeit", "Vertrag", T.DATE),
    _f("contract.pension_start_date", "Geplanter Rentenbeginn", "Vertrag", T.DATE),
    _f("contract.status", "Vertragsstatus (z. B. aktiv, ruhend, beitragsfrei)", "Vertrag", T.TEXT),
    _f("contract.intermediary", "Vermittler / Bestandsbetreuer", "Vertrag", T.TEXT),
    _f("contract.changes", "Dokumentierte Vertragsänderungen", "Vertrag", T.TEXT),
    # --- Versicherungsnehmer ----------------------------------------------
    _f(
        "policyholder.salutation", "Anrede Versicherungsnehmer", "Versicherungsnehmer", T.TEXT, sensitive=True
    ),
    _f(
        "policyholder.name", "Name / Firma Versicherungsnehmer", "Versicherungsnehmer", T.NAME, sensitive=True
    ),
    _f(
        "policyholder.first_name",
        "Vorname Versicherungsnehmer",
        "Versicherungsnehmer",
        T.NAME,
        sensitive=True,
    ),
    _f("policyholder.legal_form", "Rechtsform", "Versicherungsnehmer", T.TEXT),
    _f("policyholder.street", "Straße (VN)", "Versicherungsnehmer", T.TEXT, sensitive=True),
    _f("policyholder.house_number", "Hausnummer (VN)", "Versicherungsnehmer", T.TEXT, sensitive=True),
    _f("policyholder.postal_code", "PLZ (VN)", "Versicherungsnehmer", T.POSTAL_CODE, sensitive=True),
    _f("policyholder.city", "Ort (VN)", "Versicherungsnehmer", T.TEXT, sensitive=True),
    _f("policyholder.country", "Land (VN)", "Versicherungsnehmer", T.COUNTRY),
    # --- Versicherte Person -----------------------------------------------
    _f("insured.last_name", "Nachname versicherte Person", "Versicherte Person", T.NAME, sensitive=True),
    _f("insured.first_name", "Vorname versicherte Person", "Versicherte Person", T.NAME, sensitive=True),
    _f("insured.birth_date", "Geburtsdatum", "Versicherte Person", T.DATE, sensitive=True),
    _f("insured.birth_place", "Geburtsort", "Versicherte Person", T.TEXT, sensitive=True),
    _f("insured.nationality", "Staatsangehörigkeit", "Versicherte Person", T.TEXT, sensitive=True),
    _f("insured.street", "Straße (versicherte Person)", "Versicherte Person", T.TEXT, sensitive=True),
    _f(
        "insured.house_number",
        "Hausnummer (versicherte Person)",
        "Versicherte Person",
        T.TEXT,
        sensitive=True,
    ),
    _f(
        "insured.postal_code", "PLZ (versicherte Person)", "Versicherte Person", T.POSTAL_CODE, sensitive=True
    ),
    _f("insured.city", "Ort (versicherte Person)", "Versicherte Person", T.TEXT, sensitive=True),
    _f("insured.country", "Land (versicherte Person)", "Versicherte Person", T.COUNTRY),
    # --- Arbeitgeber / bisheriger VN (bAV) -------------------------------
    _f(
        "employer.contact_person",
        "Ansprechpartner Arbeitgeber",
        "Arbeitgeber (bisher)",
        T.NAME,
        sensitive=True,
    ),
    _f(
        "employer.employment_start",
        "Beginn der Betriebszugehörigkeit",
        "Arbeitgeber (bisher)",
        T.DATE,
        sensitive=True,
        help="Nur aus verlässlicher Quelle (z. B. Arbeitgeberbescheinigung) oder manuell erfassen.",
    ),
    _f(
        "employer.employment_end",
        "Ende der Betriebszugehörigkeit",
        "Arbeitgeber (bisher)",
        T.DATE,
        sensitive=True,
        help="Nur aus verlässlicher Quelle (z. B. Kündigungs-/Austrittsbestätigung) oder manuell erfassen.",
    ),
    # --- Fahrzeug (Kfz-Versicherung) ---------------------------------------
    _f("vehicle.license_plate", "Amtliches Kennzeichen", "Fahrzeug", T.TEXT, sensitive=True),
    # --- Beitrag ------------------------------------------------------------
    _f("premium.amount", "Beitrag", "Beitrag", T.AMOUNT),
    _f("premium.currency", "Währung", "Beitrag", T.CURRENCY),
    _f("premium.frequency", "Zahlweise", "Beitrag", T.CHOICE, choices=FREQUENCY_CHOICES),
    _f("premium.start_date", "Zahlungsbeginn", "Beitrag", T.DATE),
    _f("premium.reduction_note", "Reduzierung / Beitragsfreistellung (dokumentiert)", "Beitrag", T.TEXT),
    # --- Bankdaten aus Quellen (nur wenn belegt und benötigt) -------------
    _f("bank.account_holder", "Kontoinhaber (laut Dokument)", "Bankdaten (Dokument)", T.NAME, sensitive=True),
    _f("bank.iban", "IBAN (laut Dokument)", "Bankdaten (Dokument)", T.IBAN, sensitive=True),
    _f("bank.institution", "Kreditinstitut (laut Dokument)", "Bankdaten (Dokument)", T.TEXT, sensitive=True),
    # =====================================================================
    # Auftrags-/Wunschdaten (NIE automatisch)
    # =====================================================================
    # Swiss Life SL13065 Freigabeerklärung
    _f(
        "order.severance_consent",
        "Zustimmung zur Abfindung gem. § 3 BetrAVG (im Kündigungsfall)",
        "Auftrag: Freigabe",
        T.BOOL,
        category=_O,
        legal_relevant=True,
        help="Aktive Erklärung des bisherigen Versicherungsnehmers/Arbeitgebers. Nie aus der Police ableiten.",
    ),
    _f(
        "order.waive_refund",
        "Verzicht auf Rückerstattung überzahlter Beiträge (Kreuz = KEINE Rückerstattung)",
        "Auftrag: Freigabe",
        T.BOOL,
        category=_O,
        legal_relevant=True,
        help="Achtung: Ein Kreuz im Formular bedeutet, dass überzahlte Beiträge NICHT zurückerstattet werden.",
    ),
    # Swiss Life SL18477 Übernahmeerklärung
    _f(
        "order.continuation_mode",
        "Art der Fortführung",
        "Auftrag: Übernahme",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={
            "employer": "A – Fortführung durch den neuen Arbeitgeber",
            "private": "C – Private Fortführung durch die versicherte Person",
        },
    ),
    _f(
        "order.new_employer_company",
        "Neuer Arbeitgeber: Firma inkl. Rechtsform",
        "Auftrag: Übernahme",
        T.TEXT,
        category=_O,
    ),
    _f(
        "order.new_employer_contact",
        "Neuer Arbeitgeber: Ansprechpartner",
        "Auftrag: Übernahme",
        T.NAME,
        category=_O,
        sensitive=True,
    ),
    _f("order.new_employer_street", "Neuer Arbeitgeber: Straße", "Auftrag: Übernahme", T.TEXT, category=_O),
    _f(
        "order.new_employer_house_number",
        "Neuer Arbeitgeber: Hausnummer",
        "Auftrag: Übernahme",
        T.TEXT,
        category=_O,
    ),
    _f(
        "order.new_employer_postal_code",
        "Neuer Arbeitgeber: PLZ",
        "Auftrag: Übernahme",
        T.POSTAL_CODE,
        category=_O,
    ),
    _f("order.new_employer_city", "Neuer Arbeitgeber: Ort", "Auftrag: Übernahme", T.TEXT, category=_O),
    _f(
        "order.takeover_effective_month",
        "Übernahme mit Wirkung zum 01. (Monat/Jahr)",
        "Auftrag: Übernahme",
        T.MONTH,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.premium_start_month",
        "Beitragszahlung beginnt ab 01. (Monat/Jahr)",
        "Auftrag: Übernahme",
        T.MONTH,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.private_contribution_option",
        "Private Fortführung: Beitragsvariante",
        "Auftrag: Übernahme",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={
            "unchanged": "mit unverändertem Beitrag fortgeführt",
            "reduced": "mit reduziertem Beitrag fortgeführt",
            "exempt": "beitragsfrei gestellt",
        },
    ),
    _f(
        "order.reduced_premium_amount",
        "Reduzierter Beitrag (Euro)",
        "Auftrag: Übernahme",
        T.AMOUNT,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.interim_private_continuation",
        "Private Fortführung für den Zwischenzeitraum",
        "Auftrag: Übernahme",
        T.BOOL,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.interim_contribution_option",
        "Zwischenzeitraum: Beitragsvariante",
        "Auftrag: Übernahme",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={"exempt": "beitragsfrei", "paying": "beitragspflichtig"},
    ),
    _f(
        "order.interim_premium_amount",
        "Zwischenzeitraum: Beitrag (Euro)",
        "Auftrag: Übernahme",
        T.AMOUNT,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.pep_status",
        "Politisch exponierte Person (PeP)",
        "Auftrag: Übernahme",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={
            "no": "nein",
            "yes_office": "ja – übt/übte selbst ein Amt aus",
            "yes_related": "ja – Familienmitglied/nahestehende Person einer PeP",
        },
    ),
    _f(
        "order.pep_office",
        "PeP: Amt",
        "Auftrag: Übernahme",
        T.TEXT,
        category=_O,
        legal_relevant=True,
        sensitive=True,
    ),
    _f(
        "order.pep_related_person",
        "PeP: Name, Vorname, Amt der Bezugsperson",
        "Auftrag: Übernahme",
        T.TEXT,
        category=_O,
        legal_relevant=True,
        sensitive=True,
    ),
    _f(
        "order.payment_method",
        "Beitragszahlung",
        "Auftrag: Übernahme",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={"sepa": "SEPA-Lastschriftmandat bis auf Widerruf", "transfer": "per Rechnung (Überweisung)"},
    ),
    _f(
        "order.payment_account_holder",
        "Kontoinhaber (für Beitragszahlung)",
        "Auftrag: Übernahme",
        T.NAME,
        category=_O,
        sensitive=True,
        legal_relevant=True,
    ),
    _f(
        "order.payment_iban",
        "IBAN (für Beitragszahlung)",
        "Auftrag: Übernahme",
        T.IBAN,
        category=_O,
        sensitive=True,
        legal_relevant=True,
    ),
    _f(
        "order.payment_institution",
        "Geldinstitut (für Beitragszahlung)",
        "Auftrag: Übernahme",
        T.TEXT,
        category=_O,
        sensitive=True,
    ),
    _f(
        "order.advice_waiver_acknowledged",
        "Beratungsverzicht (Abschnitt F) zur Kenntnis genommen",
        "Auftrag: Übernahme",
        T.BOOL,
        category=_O,
        legal_relevant=True,
        help="Mit der Unterschrift erklärt der neue VN den Verzicht auf Beratung durch Swiss Life. Nur nach Rücksprache bestätigen.",
    ),
    # Allgemeine Vorgänge (Kündigung, Widerruf, Beitragsänderung …) – für spätere Vorlagen
    _f(
        "order.measure", "Gewünschte Maßnahme", "Auftrag: allgemein", T.TEXT, category=_O, legal_relevant=True
    ),
    _f(
        "order.reason",
        "Grund (z. B. Kündigungs-/Widerrufsgrund)",
        "Auftrag: allgemein",
        T.TEXT,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.desired_date",
        "Gewünschter Termin / Beendigungszeitpunkt",
        "Auftrag: allgemein",
        T.DATE,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.insurer_notice_reference",
        "Relevante Mitteilung des Versicherers (Datum/Bezug)",
        "Auftrag: allgemein",
        T.TEXT,
        category=_O,
        legal_relevant=True,
    ),
    # =====================================================================
    # Brief-Vorlagen (DIN 5008) – Absender/Empfänger und Vorgangsdaten
    # =====================================================================
    _f("order.letter_date", "Datum des Schreibens", "Brief: Absender & Empfänger", T.DATE, category=_O),
    _f(
        "order.sender_email",
        "E-Mail des Absenders (optional)",
        "Brief: Absender & Empfänger",
        T.TEXT,
        category=_O,
        sensitive=True,
    ),
    _f(
        "order.sender_phone",
        "Telefon des Absenders (optional)",
        "Brief: Absender & Empfänger",
        T.TEXT,
        category=_O,
        sensitive=True,
    ),
    _f(
        "order.recipient_company",
        "Empfänger: Versicherer / Gesellschaft",
        "Brief: Absender & Empfänger",
        T.TEXT,
        category=_O,
        help="Kann mit einem Klick aus dem erkannten Versicherer übernommen werden – bitte Anschrift prüfen.",
    ),
    _f(
        "order.recipient_department",
        "Empfänger: Abteilung (optional)",
        "Brief: Absender & Empfänger",
        T.TEXT,
        category=_O,
    ),
    _f(
        "order.recipient_street",
        "Empfänger: Straße oder Postfach",
        "Brief: Absender & Empfänger",
        T.TEXT,
        category=_O,
    ),
    _f(
        "order.recipient_house_number",
        "Empfänger: Hausnummer",
        "Brief: Absender & Empfänger",
        T.TEXT,
        category=_O,
    ),
    _f(
        "order.recipient_postal_code",
        "Empfänger: PLZ",
        "Brief: Absender & Empfänger",
        T.POSTAL_CODE,
        category=_O,
    ),
    _f("order.recipient_city", "Empfänger: Ort", "Brief: Absender & Empfänger", T.TEXT, category=_O),
    _f(
        "order.attachments",
        "Anlagen (optional, z. B. Kopie Personalausweis)",
        "Brief: Absender & Empfänger",
        T.TEXT,
        category=_O,
    ),
    _f(
        "order.deadline_checked",
        "Fristen und Termine anhand der Vertragsunterlagen geprüft",
        "Brief: Prüfung",
        T.BOOL,
        category=_O,
        legal_relevant=True,
        help="Die App berechnet keine Fristen. Bitte Kündigungs-/Widerrufsfrist selbst anhand der Bedingungen prüfen.",
    ),
    # Kündigung
    _f(
        "order.termination_timing",
        "Beendigungszeitpunkt",
        "Auftrag: Kündigung",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={
            "date": "zum angegebenen Datum (hilfsweise zum nächstmöglichen Zeitpunkt)",
            "next_possible": "zum nächstmöglichen Zeitpunkt",
            "increase_effective": "zum Zeitpunkt des Wirksamwerdens der Beitragserhöhung",
        },
    ),
    _f(
        "order.special_reason",
        "Anlass der außerordentlichen Kündigung",
        "Auftrag: Kündigung",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={"premium_increase": "Beitragserhöhung", "claim": "Schadenfall", "other": "anderer Anlass"},
    ),
    _f(
        "order.insurer_notice_date",
        "Datum der Mitteilung des Versicherers / des Schadenfalls",
        "Auftrag: Kündigung",
        T.DATE,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.legal_basis_text",
        "Rechtsgrundlage (optional, nur nach Prüfung angeben)",
        "Auftrag: Kündigung",
        T.TEXT,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.legal_basis_confirmed",
        "Sonderkündigungsrecht und Frist wurden geprüft",
        "Auftrag: Kündigung",
        T.BOOL,
        category=_O,
        legal_relevant=True,
        help="Die App stellt kein Sonderkündigungsrecht fest. Bestätigen Sie nur nach eigener Prüfung.",
    ),
    _f(
        "order.revoke_sepa",
        "SEPA-Lastschriftmandat zum Vertragsende widerrufen",
        "Auftrag: Kündigung",
        T.BOOL,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.payout_to_account",
        "Auszahlung eines Guthabens auf das angegebene Konto erbitten",
        "Auftrag: Kündigung",
        T.BOOL,
        category=_O,
        legal_relevant=True,
    ),
    # Beitragsänderung
    _f(
        "order.premium_change_type",
        "Art der Beitragsänderung",
        "Auftrag: Beitrag",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={"exemption": "Beitragsfreistellung", "reduction": "Beitragsreduzierung"},
    ),
    _f(
        "order.change_start_date",
        "Änderung ab (Datum)",
        "Auftrag: Beitrag",
        T.DATE,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.change_end_date",
        "Befristet bis (Datum, optional)",
        "Auftrag: Beitrag",
        T.DATE,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.new_premium_amount",
        "Neuer Beitrag (Euro)",
        "Auftrag: Beitrag",
        T.AMOUNT,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.increase_amount",
        "Erhöhung um (Euro)",
        "Auftrag: Beitrag",
        T.AMOUNT,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.resume_premium_amount",
        "Beitrag nach Ende der Befristung (Euro, optional)",
        "Auftrag: Beitrag",
        T.AMOUNT,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.premium_interval",
        "Zahlweise für die Beitragsangaben",
        "Auftrag: Beitrag",
        T.CHOICE,
        category=_O,
        choices=FREQUENCY_CHOICES,
    ),
    _f(
        "order.sepa_mandate_continues",
        "Bestehendes SEPA-Lastschriftmandat soll bestehen bleiben",
        "Auftrag: Beitrag",
        T.BOOL,
        category=_O,
        legal_relevant=True,
    ),
    # Betreuerwechsel / Bestandsübertrag
    _f(
        "order.new_advisor_company",
        "Neue Betreuung: Gesellschaft",
        "Auftrag: Betreuerwechsel",
        T.TEXT,
        category=_O,
    ),
    _f(
        "order.new_advisor_name",
        "Neue Betreuung: Beraterin/Berater (Name)",
        "Auftrag: Betreuerwechsel",
        T.NAME,
        category=_O,
        sensitive=True,
    ),
    _f(
        "order.new_advisor_id",
        "Neue Betreuung: Vermittlernummer (optional)",
        "Auftrag: Betreuerwechsel",
        T.TEXT,
        category=_O,
    ),
    _f("order.new_advisor_street", "Neue Betreuung: Straße", "Auftrag: Betreuerwechsel", T.TEXT, category=_O),
    _f(
        "order.new_advisor_house_number",
        "Neue Betreuung: Hausnummer",
        "Auftrag: Betreuerwechsel",
        T.TEXT,
        category=_O,
    ),
    _f(
        "order.new_advisor_postal_code",
        "Neue Betreuung: PLZ",
        "Auftrag: Betreuerwechsel",
        T.POSTAL_CODE,
        category=_O,
    ),
    _f("order.new_advisor_city", "Neue Betreuung: Ort", "Auftrag: Betreuerwechsel", T.TEXT, category=_O),
    _f(
        "order.grant_change_authority",
        "Vertragsänderungen dürfen auch über die neue Betreuung veranlasst werden (Vollmacht)",
        "Auftrag: Betreuerwechsel",
        T.BOOL,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.data_sharing_consent",
        "Einverständnis: Vertragsdaten an die neue Betreuung übermitteln",
        "Auftrag: Betreuerwechsel",
        T.BOOL,
        category=_O,
        legal_relevant=True,
        help="Einwilligung der Kundin/des Kunden in die Datenweitergabe – nur nach ausdrücklicher Zustimmung bestätigen.",
    ),
    # Widerruf
    _f(
        "order.revocation_subject",
        "Gegenstand des Widerrufs",
        "Auftrag: Widerruf",
        T.CHOICE,
        category=_O,
        legal_relevant=True,
        choices={
            "application": "Antrag",
            "contract": "Versicherungsvertrag (Vertragserklärung)",
            "termination": "Rücknahme einer eigenen Kündigung",
        },
    ),
    _f(
        "order.application_date",
        "Antrag vom (Datum)",
        "Auftrag: Widerruf",
        T.DATE,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.termination_letter_date",
        "Kündigung vom (Datum)",
        "Auftrag: Widerruf",
        T.DATE,
        category=_O,
        legal_relevant=True,
    ),
    _f(
        "order.refund_to_account",
        "Erstattung bereits gezahlter Beiträge auf das angegebene Konto erbitten",
        "Auftrag: Widerruf",
        T.BOOL,
        category=_O,
        legal_relevant=True,
    ),
]

FIELD_REGISTRY: dict[str, FieldDef] = {f.key: f for f in _FIELDS}

if len(FIELD_REGISTRY) != len(_FIELDS):  # pragma: no cover - Schutz gegen Tippfehler
    raise RuntimeError("Doppelte Feldschlüssel im Feldregister")


def get_field(key: str) -> FieldDef:
    try:
        return FIELD_REGISTRY[key]
    except KeyError as exc:
        raise KeyError(f"Unbekannter Feldschlüssel: {key}") from exc


def contract_fields() -> list[FieldDef]:
    return [f for f in _FIELDS if f.category == FieldCategory.CONTRACT]


def order_fields() -> list[FieldDef]:
    return [f for f in _FIELDS if f.category == FieldCategory.ORDER]


def is_order_field(key: str) -> bool:
    return key in FIELD_REGISTRY and FIELD_REGISTRY[key].category == FieldCategory.ORDER


class FieldGroupView(BaseModel):
    """Für die Oberfläche: Felder nach Gruppen geordnet."""

    group: str
    keys: list[str] = Field(default_factory=list)


def grouped(category: FieldCategory | None = None) -> list[FieldGroupView]:
    out: dict[str, FieldGroupView] = {}
    for f in _FIELDS:
        if category is not None and f.category != category:
            continue
        out.setdefault(f.group, FieldGroupView(group=f.group)).keys.append(f.key)
    return list(out.values())
