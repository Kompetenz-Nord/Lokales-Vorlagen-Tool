"""Schema für Vorlagenkatalog und Feldzuordnungen (JSON, validiert mit Pydantic).

Eine Vorlage besteht aus
* einem unveränderten Original-PDF (versioniert, SHA-256 geprüft) und
* einer Feldzuordnung (``TemplateDefinition``), die PDF-Felder auf das zentrale
  Datenmodell abbildet.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Platzhalter in ``compose``: {feld.schluessel} (Pflichtteil) oder {feld.schluessel?} (optionaler Teil)
COMPOSE_PLACEHOLDER = re.compile(r"\{([a-z_]+\.[a-z_]+)(\??)\}")


class TemplateStatus(StrEnum):
    ACTIVE = "Aktiv"
    MISSING_ORIGINAL = "Originalvorlage fehlt"
    NEEDS_REVIEW = "Zu prüfen"
    ERROR = "Fehler"


class MappingKind(StrEnum):
    TEXT = "text"  # einzeiliger Text
    MULTILINE = "multiline"  # mehrzeiliger Text
    DATE_PART = "date_part"  # Teilfeld eines Datums (Tag/Monat/Jahr)
    AMOUNT = "amount"  # Betrag (Acrobat-Zahlformat beachten)
    CHECKBOX = "checkbox"
    RADIO = "radio"  # Optionsgruppe
    STATIC = "static"  # fester, nicht personenbezogener Text
    SIGNATURE = "signature"  # Unterschriftsfeld – bleibt IMMER leer
    SIGNATURE_DATE = "signature_date"  # Datum zur Unterschrift – bleibt standardmäßig leer


class FillPolicy(StrEnum):
    AUTO = "auto"  # befüllen, wenn Wert freigegeben (CONFIRMED/MANUAL)
    NEVER = "never"  # niemals befüllen (Unterschrift, Unterschriftsdatum)
    MANUAL_ONLY = "manual_only"  # nur mit Status MANUAL (nie aus Dokument/KI)


class Condition(BaseModel):
    """Bedingung auf einen Feldwert (normalisiert)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    equals: str | None = None
    in_: list[str] | None = Field(default=None, alias="in")

    def matches(self, normalized_value: str | None) -> bool:
        if self.equals is not None:
            return normalized_value == self.equals
        if self.in_ is not None:
            return normalized_value in self.in_
        return normalized_value is not None


class FieldMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pdf_field: str  # vollqualifizierter Feldname im PDF (wie von PyMuPDF gemeldet)
    label: str  # deutsche Beschriftung für Oberfläche/Checkliste
    kind: MappingKind
    source: str | None = None  # Feldschlüssel aus dem Feldregister
    compose: str | None = None  # z. B. "{insured.last_name}, {insured.first_name}"
    date_part: str | None = None  # "day" | "month" | "year"
    checked_when: str | None = None  # CHECKBOX: Normalwert, bei dem angekreuzt wird (Standard "true")
    choice_map: dict[str, str] | None = (
        None  # RADIO: interner Wert -> PDF-Ein-Zustand (z. B. "reduced" -> "1")
    )
    static_value: str | None = None
    required: bool = False
    conditions: list[Condition] = Field(
        default_factory=list
    )  # UND-verknüpft; Feld nur relevant, wenn alle erfüllt
    legal_relevant: bool = False
    fill_policy: FillPolicy = FillPolicy.AUTO
    page: int | None = None  # 1-basiert, zur Kontrolle gegen das PDF
    max_len: int | None = None
    font_size: float | None = None  # nur für Overlay oder zur Kontrolle
    align: str | None = None  # "left" | "center" | "right"
    help: str = ""
    overlay_rect: list[float] | None = (
        None  # [x0, y0, x1, y1] für Koordinaten-Overlay (Seitenkoordinaten oben links)
    )

    @model_validator(mode="after")
    def _check(self) -> FieldMapping:
        if (
            self.kind in (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE)
            and self.fill_policy != FillPolicy.NEVER
        ):
            raise ValueError(f"{self.pdf_field}: Unterschriftsfelder müssen fill_policy=never haben")
        if self.kind == MappingKind.DATE_PART and self.date_part not in {"day", "month", "year"}:
            raise ValueError(f"{self.pdf_field}: date_part muss day|month|year sein")
        if self.kind == MappingKind.RADIO and not self.choice_map:
            raise ValueError(f"{self.pdf_field}: RADIO benötigt choice_map")
        if self.kind == MappingKind.STATIC and self.static_value is None:
            raise ValueError(f"{self.pdf_field}: STATIC benötigt static_value")
        needs_source = self.kind not in (
            MappingKind.SIGNATURE,
            MappingKind.SIGNATURE_DATE,
            MappingKind.STATIC,
        )
        if needs_source and not (self.source or self.compose):
            raise ValueError(f"{self.pdf_field}: source oder compose erforderlich")
        return self


class Acknowledgment(BaseModel):
    """Pflicht-Bestätigung vor dem Export (wird nicht ins PDF gedruckt)."""

    model_config = ConfigDict(extra="forbid")

    field: str  # BOOL-Auftragsfeld, das "true" sein muss
    text: str
    conditions: list[Condition] = Field(default_factory=list)


class TemplateDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    catalog_id: str
    version: str  # z. B. "2024-09"
    insurer: str
    insurer_code: str  # für Dateinamen, z. B. "SWISSLIFE"
    form_number: str
    title: str
    short_title: str  # für Dateinamen, z. B. "Freigabe"
    pdf_file: str  # Dateiname relativ zum Vorlagenordner
    sha256: str
    page_count: int
    pages_to_preserve: list[int] = Field(default_factory=list)  # Seiten, die unverändert bleiben müssen
    fields: list[FieldMapping]
    ignored_fields: list[str] = Field(default_factory=list)  # z. B. Schaltfläche "Formular zurücksetzen"
    acknowledgments: list[Acknowledgment] = Field(default_factory=list)
    plausibility_rules: list[str] = Field(default_factory=list)  # Codes, siehe validation.plausibility
    notes: list[str] = Field(default_factory=list)
    mapping_confirmed: bool = False  # vom Anwender/Entwickler geprüfte Zuordnung

    @model_validator(mode="after")
    def _unique(self) -> TemplateDefinition:
        seen: set[tuple[str, str | None]] = set()
        for m in self.fields:
            k = (m.pdf_field, m.date_part)
            if k in seen and m.kind != MappingKind.CHECKBOX:
                raise ValueError(f"Doppelte Feldzuordnung: {m.pdf_field}")
            seen.add(k)
        return self

    def mapping(self, pdf_field: str) -> FieldMapping | None:
        return next((m for m in self.fields if m.pdf_field == pdf_field), None)

    def source_keys(self) -> set[str]:
        keys: set[str] = set()
        for m in self.fields:
            if m.source:
                keys.add(m.source)
            if m.compose:
                keys.update(k for k, _ in COMPOSE_PLACEHOLDER.findall(m.compose))
            keys.update(c.field for c in m.conditions)
        for a in self.acknowledgments:
            keys.add(a.field)
            keys.update(c.field for c in a.conditions)
        return keys


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    number: int  # 1..9 (Anzeige-Reihenfolge)
    group: str
    title: str
    insurer: str
    form_number: str | None = None
    description: str
    mapping_file: str | None = None  # Datei in config/field_schemas
    original_file: str | None = None  # Datei in assets/templates/originals
    legal_hint: str | None = None  # sachlicher Hinweis (z. B. Grundlage ist vom Anwender zu bestätigen)


class TemplateCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    groups: list[str]
    entries: list[CatalogEntry]

    @model_validator(mode="after")
    def _check(self) -> TemplateCatalog:
        ids = [e.id for e in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Doppelte Katalog-IDs")
        for e in self.entries:
            if e.group not in self.groups:
                raise ValueError(f"Unbekannte Gruppe {e.group} in {e.id}")
        return self
