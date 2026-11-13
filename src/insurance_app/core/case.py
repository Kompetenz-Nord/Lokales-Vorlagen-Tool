"""Vorgang (Case) mit Quelldokumenten, Feldwerten, Auftragsdaten und Verlauf."""

from __future__ import annotations

import secrets
from datetime import datetime
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field

from .fields import FIELD_REGISTRY, FieldCategory
from .models import (
    SCHEMA_VERSION,
    ClassificationResult,
    DocumentCheckResult,
    DocumentText,
    FieldStatus,
    FieldValue,
    HistoryEntry,
    utcnow,
)


class PipelineStage(IntEnum):
    """Neun-Stufen-Pipeline (Anzeige in der Oberfläche)."""

    ABLAGE = 1
    DOKUMENTCHECK = 2
    TEXTERKENNUNG = 3
    DATENEXTRAKTION = 4
    PRUEFUNG = 5
    DATENFREIGABE = 6
    VORLAGE_WAEHLEN = 7
    VORSCHAU = 8
    EXPORT = 9

    @property
    def label_de(self) -> str:
        return {
            PipelineStage.ABLAGE: "Ablage",
            PipelineStage.DOKUMENTCHECK: "Dokumentcheck",
            PipelineStage.TEXTERKENNUNG: "Texterkennung",
            PipelineStage.DATENEXTRAKTION: "Datenextraktion",
            PipelineStage.PRUEFUNG: "Konflikt- und Plausibilitätsprüfung",
            PipelineStage.DATENFREIGABE: "Datenfreigabe",
            PipelineStage.VORLAGE_WAEHLEN: "Vorlage wählen",
            PipelineStage.VORSCHAU: "PDF-Vorschau und Qualitätskontrolle",
            PipelineStage.EXPORT: "Export",
        }[self]


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str
    original_name: str  # Dateiname beim Import (nur lokal angezeigt, nie geloggt)
    stored_path: str  # Pfad der unveränderlichen Arbeitskopie im Vorgangsordner
    sha256: str
    added_at: datetime = Field(default_factory=utcnow)
    check: DocumentCheckResult | None = None
    classification: ClassificationResult | None = None
    text: DocumentText | None = None
    user_doc_type_confirmed: bool = False
    excluded: bool = False  # vom Nutzer aus der Auswertung genommen (z. B. Dublette)
    duplicate_of: str | None = None


class ExportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=utcnow)
    template_id: str
    template_version: str
    file_path: str
    flattened: bool = False
    qa_passed: bool = False
    qa_summary: str = ""


def new_case_id() -> str:
    """Kurze, nicht sprechende Vorgangs-ID (keine personenbezogenen Daten)."""
    return f"V{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"


def new_document_id() -> str:
    return f"D-{secrets.token_hex(4).upper()}"


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=new_case_id)
    title: str = ""  # frei wählbarer Titel; Standard ohne personenbezogene Daten
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    stage: PipelineStage = PipelineStage.ABLAGE
    documents: list[SourceDocument] = Field(default_factory=list)
    contract_fields: dict[str, FieldValue] = Field(default_factory=dict)
    order_fields: dict[str, FieldValue] = Field(default_factory=dict)
    template_id: str | None = None
    template_version: str | None = None
    history: list[HistoryEntry] = Field(default_factory=list)
    exports: list[ExportRecord] = Field(default_factory=list)
    archived: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)  # z. B. verwendete OCR-/KI-Version

    # ------------------------------------------------------------------
    def display_title(self) -> str:
        return self.title or f"Vorgang {self.id}"

    def touch(self) -> None:
        self.updated_at = utcnow()

    def field(self, key: str) -> FieldValue:
        """Feldwert holen (legt fehlende Felder als MISSING an)."""
        fdef = FIELD_REGISTRY.get(key)
        if fdef is None:
            raise KeyError(f"Unbekannter Feldschlüssel: {key}")
        store = self.order_fields if fdef.category == FieldCategory.ORDER else self.contract_fields
        if key not in store:
            store[key] = FieldValue(key=key, status=FieldStatus.MISSING)
        return store[key]

    def get(self, key: str) -> FieldValue | None:
        fdef = FIELD_REGISTRY.get(key)
        if fdef is None:
            return None
        store = self.order_fields if fdef.category == FieldCategory.ORDER else self.contract_fields
        return store.get(key)

    def set_field(self, fv: FieldValue) -> None:
        fdef = FIELD_REGISTRY.get(fv.key)
        if fdef is None:
            raise KeyError(f"Unbekannter Feldschlüssel: {fv.key}")
        if fdef.category == FieldCategory.ORDER and fv.status in (
            FieldStatus.EXTRACTED,
            FieldStatus.CONFLICT,
        ):
            raise ValueError(
                f"Auftragsdaten ({fv.key}) dürfen nicht automatisch befüllt werden (Status {fv.status})."
            )
        store = self.order_fields if fdef.category == FieldCategory.ORDER else self.contract_fields
        store[fv.key] = fv

    def all_fields(self) -> dict[str, FieldValue]:
        return {**self.contract_fields, **self.order_fields}

    def active_documents(self) -> list[SourceDocument]:
        return [d for d in self.documents if not d.excluded]

    def document(self, document_id: str) -> SourceDocument | None:
        return next((d for d in self.documents if d.id == document_id), None)

    def add_history(self, entry: HistoryEntry) -> None:
        self.history.append(entry)
        self.touch()


class CaseSummary(BaseModel):
    """Leichtgewichtige Listenansicht (Dashboard)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    stage: PipelineStage
    template_id: str | None = None
    document_count: int = 0
    archived: bool = False
    open_issues: int = 0
