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
