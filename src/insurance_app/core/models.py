"""Zentrale, modulübergreifende Datenmodelle.

Diese Datei ist der gemeinsame Vertrag zwischen OCR, Extraktion, Validierung,
Vorlagenbefüllung, Speicherung und Oberfläche. Änderungen hier betreffen alle
Module und müssen abwärtskompatibel (Schema-Version) erfolgen.

Konventionen
------------
* Seitennummern sind 1-basiert.
* ``PageText.rotation`` / ``PageInfo.rotation``: Drehung (im Uhrzeigersinn) des
  Koordinatensystems gegenüber der Rohseite (PDF /Rotate bzw. EXIF, bei OCR zzgl.
  automatischer Aufrichtung). Wörter, Breite und Höhe beziehen sich auf die
  angezeigte (aufgerichtete) Seite.
* Eine Textzeile ist eindeutig durch das Paar ``(Word.block_no, Word.line_no)``
  bestimmt – bei schrägen Scans nicht allein über die y-Koordinate gruppieren.
* Koordinaten (``BoundingBox``) liegen im Seitenkoordinatensystem mit Ursprung
  oben links. Für PDFs in PDF-Punkten (1/72 Zoll), für Bilddateien in Punkten
  bezogen auf die angenommene Bildauflösung (siehe ``PageText.source_dpi``).
* Normalisierte Werte (``normalized_value``) sind kanonische Zeichenketten:
  - Datum:   ``YYYY-MM-DD``
  - Monat:   ``YYYY-MM``
  - Betrag:  ``1234.56`` (Punkt als Dezimaltrenner, keine Tausendertrennung);
             die Währung steht in einem eigenen Feld (z. B. ``premium.currency``)
  - IBAN:    Großbuchstaben ohne Leerzeichen
  - Bool:    ``true`` / ``false``
  - Auswahl: der interne Options-Schlüssel (z. B. ``employer``)
  Formatiert wird erst bei der PDF-Ausgabe.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    """Zeitstempel in UTC (zeitzonenbewusst)."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Aufzählungen
# ---------------------------------------------------------------------------


class FieldStatus(StrEnum):
    """Prüfstatus eines Feldwerts (siehe Lastenheft, Abschnitt "Feldstatus")."""

    EXTRACTED = "EXTRACTED"  # automatisch gefunden, noch nicht bestätigt
    CONFIRMED = "CONFIRMED"  # vom Nutzer geprüft und bestätigt
    MANUAL = "MANUAL"  # manuell eingetragen
    CONFLICT = "CONFLICT"  # unterschiedliche Quellen/Werte gefunden
    UNCLEAR = "UNCLEAR"  # nicht sicher lesbar / nicht eindeutig zuzuordnen
    MISSING = "MISSING"  # keine Quelle gefunden
    NOT_APPLICABLE = "NOT_APPLICABLE"  # für den Vorgang ausdrücklich nicht relevant

    @property
    def label_de(self) -> str:
        return {
            FieldStatus.EXTRACTED: "Erkannt – nicht bestätigt",
            FieldStatus.CONFIRMED: "Bestätigt",
            FieldStatus.MANUAL: "Manuell eingetragen",
            FieldStatus.CONFLICT: "Konflikt",
            FieldStatus.UNCLEAR: "Unklar",
            FieldStatus.MISSING: "Fehlt",
            FieldStatus.NOT_APPLICABLE: "Nicht relevant",
        }[self]

    @property
    def is_released(self) -> bool:
        """Darf der Wert in eine Vorlage geschrieben werden?"""
        return self in (FieldStatus.CONFIRMED, FieldStatus.MANUAL)


class ExtractionMethod(StrEnum):
    PDF_TEXT = "PDF_TEXT"  # native PDF-Textebene
    DOCLING_OCR = "DOCLING_OCR"
    TESSERACT = "TESSERACT"
    RAPID_OCR = "RAPID_OCR"
    PADDLE_OCR = "PADDLE_OCR"
    LOCAL_VLM = "LOCAL_VLM"  # lokales Vision-Language-Modell (Ollama)
    MANUAL = "MANUAL"  # Eingabe durch den Nutzer


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"  # blockiert den nächsten Schritt / den Export


class DocumentType(StrEnum):
    POLICY = "POLICY"
    AMENDMENT = "AMENDMENT"
    PREMIUM_NOTICE = "PREMIUM_NOTICE"
    STATUS_REPORT = "STATUS_REPORT"
    CANCELLATION_CONFIRMATION = "CANCELLATION_CONFIRMATION"
    PORTAL_OVERVIEW = "PORTAL_OVERVIEW"
    UNKNOWN = "UNKNOWN"

    @property
    def label_de(self) -> str:
        return {
            DocumentType.POLICY: "Police / Versicherungsschein",
            DocumentType.AMENDMENT: "Nachtrag",
            DocumentType.PREMIUM_NOTICE: "Beitragsmitteilung",
            DocumentType.STATUS_REPORT: "Standmitteilung / Jährliche Information",
            DocumentType.CANCELLATION_CONFIRMATION: "Kündigungsbestätigung",
            DocumentType.PORTAL_OVERVIEW: "Vertragsübersicht (Portal/Screenshot)",
            DocumentType.UNKNOWN: "Unbekannt",
        }[self]


class PageQuality(StrEnum):
    GOOD = "GOOD"  # brauchbarer Text
    LOW = "LOW"  # Text vorhanden, aber unsicher (niedrige OCR-Konfidenz, Fremd-OCR mit Fehlern)
    EMPTY = "EMPTY"  # kein brauchbarer Text


class TextLayerOrigin(StrEnum):
    NATIVE = "NATIVE"  # digital erzeugtes PDF mit echter Textebene
    EMBEDDED_OCR = "EMBEDDED_OCR"  # Scan mit (fremder) unsichtbarer OCR-Textebene
    OCR = "OCR"  # in dieser App lokal erkannt
    NONE = "NONE"


# ---------------------------------------------------------------------------
# Hilfsmodelle
# ---------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ValidationMessage(_Model):
    severity: Severity
    code: str  # stabiler maschinenlesbarer Code, z. B. "IBAN_CHECKSUM"
    message: str  # verständliche deutsche Meldung, OHNE vollständige Kundendaten
    field_key: str | None = None
    pages: list[int] | None = None  # betroffene Seiten (1-basiert), falls seitenbezogen


class BoundingBox(_Model):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    def union(self, other: BoundingBox) -> BoundingBox:
        return BoundingBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )


class Word(_Model):
    text: str
    bbox: BoundingBox
    confidence: float | None = None  # 0..1, None bei nativer Textebene
    block_no: int | None = None
    line_no: int | None = None


class PageText(_Model):
    page_number: int  # 1-basiert
    width: float
    height: float
    method: ExtractionMethod
    origin: TextLayerOrigin = TextLayerOrigin.NATIVE
    words: list[Word] = Field(default_factory=list)
    text: str = ""
    mean_confidence: float | None = None  # 0..1, nur bei OCR aussagekräftig
    quality: PageQuality = PageQuality.GOOD
    warnings: list[str] = Field(default_factory=list)
    engine_version: str | None = None  # z. B. "tesseract 5.3.4 deu+eng"
    source_dpi: float | None = None  # Auflösung des gerenderten/gelesenen Bildes
    rotation: int = 0


class DocumentText(_Model):
    document_id: str
    pages: list[PageText] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


class PageInfo(_Model):
    page_number: int
    width: float
    height: float
    rotation: int = 0
    format_name: str = "unbekannt"  # z. B. "A4 hoch"
    has_text_layer: bool = False
    image_only: bool = False
    text_chars: int = 0


class DocumentCheckResult(_Model):
    ok: bool
    file_type: str  # "pdf" | "png" | "jpeg" | "tiff" | "unknown"
    size_bytes: int
    sha256: str
    page_count: int = 0
    encrypted: bool = False
    needs_password: bool = False
    damaged: bool = False
    pages: list[PageInfo] = Field(default_factory=list)
    issues: list[ValidationMessage] = Field(default_factory=list)


class ClassificationResult(_Model):
    doc_type: DocumentType = DocumentType.UNKNOWN
    confidence: float = 0.0  # heuristischer Wert 0..1, NICHT kalibriert
    uncertain: bool = True
    matched_keywords: list[str] = Field(default_factory=list)
    document_date: date | None = None  # Ausstellungs-/Briefdatum
    effective_date: date | None = None  # "gültig ab" / "Wirksam ab" (z. B. Nachtrag)
    contract_numbers: list[str] = Field(default_factory=list)
    insurer_hint: str | None = None
    notes: list[str] = Field(default_factory=list)


class Evidence(_Model):
    """Beleg für einen extrahierten Wert."""

    document_id: str
    file_name: str
    page: int | None = None
    quote: str | None = None  # kurzer Textausschnitt (max. ~160 Zeichen)
    bbox: BoundingBox | None = None
    method: ExtractionMethod
    model_version: str | None = None  # OCR-Engine oder lokales Modell inkl. Version
    rule_id: str | None = None  # welche Regel/welches Muster hat getroffen


class Candidate(_Model):
    """Ein Wertvorschlag aus genau einer Quelle."""

    field_key: str
    value: str  # wie in der Quelle gelesen (bereinigt um Whitespace)
    normalized: str | None = None
    confidence: float | None = None  # heuristisch 0..1, NICHT kalibriert
    evidence: Evidence
    document_type: DocumentType = DocumentType.UNKNOWN
    document_date: date | None = None
    effective_date: date | None = None
    note: str | None = None


class FieldValue(_Model):
    """Aktueller Stand eines Feldes im Vorgang inkl. Herkunft und Prüfstatus."""

    key: str
    value: str | None = None
    normalized_value: str | None = None
    status: FieldStatus = FieldStatus.MISSING
    confidence: float | None = None
    source_document_id: str | None = None
    source_page: int | None = None
    source_quote: str | None = None
    extraction_method: ExtractionMethod | None = None
    model_version: str | None = None
    user_confirmed_at: datetime | None = None
    candidates: list[Candidate] = Field(default_factory=list)  # alle Quellen, bei CONFLICT Alternativen
    note: str | None = None  # z. B. Hinweis zur Konfliktlage oder Aufteilung Vor-/Nachname
    suggestion_index: int | None = None  # Kandidat, der fachlich vermutlich maßgeblich ist (nur Hinweis!)
    validation: list[ValidationMessage] = Field(default_factory=list)
    user_marked_unclear: bool = (
        False  # vom Nutzer bewusst als "unklar" markiert (bleibt bei Neu-Extraktion erhalten)
    )

    # -- typisierte Zugriffe ------------------------------------------------
    def as_date(self) -> date | None:
        if not self.normalized_value:
            return None
        try:
            return date.fromisoformat(self.normalized_value)
        except ValueError:
            return None

    def as_decimal(self) -> Decimal | None:
        if not self.normalized_value:
            return None
        try:
            return Decimal(self.normalized_value)
        except (InvalidOperation, ValueError):
            return None

    def as_bool(self) -> bool | None:
        if self.normalized_value == "true":
            return True
        if self.normalized_value == "false":
            return False
        return None

    @property
    def effective_value(self) -> str | None:
        """Kanonischer Wert, falls vorhanden, sonst Rohwert."""
        return self.normalized_value if self.normalized_value is not None else self.value


class HistoryEntry(_Model):
    """Lokaler Änderungsverlauf (bleibt auf dem Gerät, nie in Logs)."""

    timestamp: datetime = Field(default_factory=utcnow)
    actor: str = "system"  # "system" | "user"
    action: str  # z. B. "extracted", "confirmed", "edited", "marked_unclear", "export"
    field_key: str | None = None
    old_status: FieldStatus | None = None
    new_status: FieldStatus | None = None
    old_value: str | None = None
    new_value: str | None = None
    source: str | None = None  # z. B. "Police.pdf, S. 1, PDF_TEXT"
    detail: str | None = None
