"""Zentrale Ablaufsteuerung (Orchestrierung der 9-Stufen-Pipeline).

Die Oberfläche ruft ausschließlich diese Klasse auf. Alle Methoden arbeiten lokal;
lange Schritte (Texterkennung, lokale KI, Vorschau, Export) werden von der
Oberfläche in einem Hintergrund-Thread ausgeführt und können über
``threading.Event`` abgebrochen werden.

Datenschutz: Passwörter für PDFs werden nur im Arbeitsspeicher gehalten
(nie gespeichert, nie geloggt). Logs enthalten nur Vorgangs-/Dokument-IDs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..documents.classify import classify_document
from ..documents.intake import check_document
from ..documents.pages import DocumentAccessError, PasswordRequiredError, load_page_image
from ..export.exporter import ExportBlockedError, Exporter, ExportLogEntry
from ..extraction.extractor import SourceDocInfo, extract_candidates
from ..extraction.merge import merge_candidates
from ..extraction.normalize import normalize_value
from ..letters import load_letter_template
from ..letters.compose import LetterPlan, resolve_letter
from ..letters.din5008 import render_letter
from ..letters.plausibility import check_letter_plausibility
from ..letters.qa import run_letter_qa
from ..letters.schema import LetterHint, LetterTemplate
from ..ocr.docling_backend import DoclingBackend
from ..ocr.rapidocr_backend import RapidOcrBackend
from ..ocr.service import RecognitionCancelled, RecognitionError, TextRecognitionService
from ..ocr.tesseract_backend import TesseractBackend
from ..pdf.filler import PdfFillError, TemplateFiller
from ..pdf.inspector import inspect_pdf_form
from ..pdf.qa import QaReport, run_quality_checks
from ..pdf.render import save_previews
from ..security.logging_setup import configure_logging
from ..security.network_guard import install_network_guard
from ..security.tempfiles import cleanup_stale, force_rmtree
from ..storage.db import Database
from ..storage.repository import CaseRepository
from ..templates.registry import TemplateRegistry, TemplateRegistryError
from ..templates.resolver import ChecklistItem, FillPlan, resolve_fill_values
from ..templates.schema import TemplateDefinition, TemplateStatus
from ..validation.plausibility import check_plausibility
from ..validation.validators import validate_case, validate_field
from .case import Case, CaseSummary, ExportRecord, PipelineStage, SourceDocument
from .fields import FIELD_REGISTRY, FieldCategory, FieldType, get_field
from .models import (
    Candidate,
    DocumentCheckResult,
    DocumentType,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
    HistoryEntry,
    Severity,
    ValidationMessage,
    utcnow,
)
from .paths import AppPaths
from .settings import Settings

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]

# Felder, die jeder generierte Brief zusätzlich zu den Textbausteinen verwendet
LETTER_BASE_KEYS: tuple[str, ...] = (
    "policyholder.first_name",
    "policyholder.name",
    "policyholder.street",
    "policyholder.house_number",
    "policyholder.postal_code",
    "policyholder.city",
    "order.letter_date",
    "order.sender_email",
    "order.sender_phone",
    "order.recipient_company",
    "order.recipient_department",
    "order.recipient_street",
    "order.recipient_house_number",
    "order.recipient_postal_code",
    "order.recipient_city",
    "order.attachments",
)


class ServiceError(Exception):
    """Fachlicher Fehler mit verständlicher deutscher Meldung."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message = message


class FieldInputError(ServiceError):
    """Eingabe konnte nicht verstanden werden (z. B. Datum im falschen Format)."""


class SettingsError(ServiceError, ValueError):
    """Ungültige Einstellung (wird nicht gespeichert)."""


DEFAULT_NOTE = "Vorschlag der Vorlage – bitte prüfen"
SOURCE_REMOVED_NOTE = "Die Quelle dieses Werts wurde entfernt – bitte erneut prüfen."


# ---------------------------------------------------------------------------
# Ergebnis-Modelle für die Oberfläche
# ---------------------------------------------------------------------------


class AddDocumentResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    file_name: str
    document: SourceDocument | None = None
    check: DocumentCheckResult | None = None
    needs_password: bool = False
    wrong_password: bool = False
    duplicate: bool = False
    accepted: bool = False
    message: str = ""


class TemplateChoice(BaseModel):
    """Katalogeintrag aus Sicht der Oberfläche (Formular oder generierter Brief)."""

    catalog_id: str
    number: int
    group: str
    title: str
    insurer: str
    form_number: str | None
    description: str
    kind: Literal["form", "letter"] | None
    status: TemplateStatus
    status_reason: str
    legal_hint: str | None = None
    version: str | None = None

    @property
    def available(self) -> bool:
        return self.status == TemplateStatus.ACTIVE and self.kind is not None


class ReviewStatus(BaseModel):
    template_id: str | None = None
    relevant_contract_keys: list[str] = Field(default_factory=list)
    relevant_order_keys: list[str] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)
    messages: list[ValidationMessage] = Field(default_factory=list)
    hints: list[LetterHint] = Field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.template_id is not None and not any(i.blocking for i in self.checklist)

    @property
    def blocking_items(self) -> list[ChecklistItem]:
        return [i for i in self.checklist if i.blocking]


class PreviewResult(BaseModel):
    kind: Literal["form", "letter"]
    pdf_path: Path
    page_images: list[Path]
    checklist: list[ChecklistItem]
    qa: QaReport | None = None
    hints: list[LetterHint] = Field(default_factory=list)
    blocking: bool = False
    flatten_allowed: bool = False
    flatten_reason: str = ""


class ExportOutcome(BaseModel):
    path: Path
    qa: QaReport
    log_entry: ExportLogEntry


class ExtractionSummary(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[ValidationMessage] = Field(default_factory=list)
    documents_used: int = 0


class LetterExportIdentity(BaseModel):
    """Angaben, die der Exporter für Dateiname und Protokoll benötigt (Brief statt Formular)."""

    template_id: str
    version: str
    insurer_code: str = "BRIEF"
    form_number: str = "DIN5008"
    short_title: str


@dataclass
class ActiveTemplate:
    catalog_id: str
    kind: Literal["form", "letter"]
    form: TemplateDefinition | None = None
    letter: LetterTemplate | None = None
    pdf_path: Path | None = None

    @property
    def template_id(self) -> str:
        return self.form.template_id if self.form else self.letter.template_id  # type: ignore[union-attr]

    @property
    def version(self) -> str:
        return self.form.version if self.form else self.letter.version  # type: ignore[union-attr]

    @property
    def title(self) -> str:
        return self.form.title if self.form else self.letter.title  # type: ignore[union-attr]

    @property
    def plausibility_rules(self) -> list[str]:
        return list(self.form.plausibility_rules if self.form else self.letter.plausibility_rules)  # type: ignore[union-attr]

    def referenced_keys(self) -> set[str]:
        if self.form:
            return self.form.source_keys()
        assert self.letter is not None
        return self.letter.referenced_keys() | set(LETTER_BASE_KEYS)


# ---------------------------------------------------------------------------


def find_tesseract(explicit: str | None = None) -> str | None:
    """Tesseract auch dann finden, wenn PATH (z. B. bei macOS-App-Start) unvollständig ist."""
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("tesseract")
    if found:
        return found
    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates += [
                    Path(base) / "Tesseract-OCR" / "tesseract.exe",
                    Path(base) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
                ]
    else:
        candidates += [
            Path("/opt/homebrew/bin/tesseract"),
            Path("/usr/local/bin/tesseract"),
            Path("/usr/bin/tesseract"),
        ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


class AppService:
    """Einstiegspunkt für die Oberfläche (eine Instanz je laufender App)."""

    def __init__(
        self,
        paths: AppPaths | None = None,
        settings: Settings | None = None,
        *,
        guard_network: bool = True,
        configure_logs: bool = True,
        today: Callable[[], date] = date.today,
    ) -> None:
        if guard_network:
            install_network_guard()  # so früh wie möglich: LOCAL ONLY
        self.paths = (paths or AppPaths.default()).ensure()
        self.settings = settings or Settings.load(self.paths.settings_file)
        if configure_logs:
            configure_logging(self.paths.logs_dir)
        import pymupdf

        with contextlib.suppress(AttributeError):
            pymupdf.TOOLS.mupdf_display_errors(False)  # Reparaturmeldungen nicht auf stderr
        removed = cleanup_stale(self.paths.temp_dir)
        if removed:
            log.info("Alte temporäre Ordner entfernt: %d", removed)
        self._today = today
        self.db = Database(self.paths.database)
        self.repo = CaseRepository(self.db, self.paths)
        self.registry = TemplateRegistry(self.paths)
        self.filler = TemplateFiller(self.paths.fonts_dir)
        self._passwords: dict[str, str] = {}  # document_id -> Passwort (nur Arbeitsspeicher)
        self._lock = threading.RLock()
        self._build_ocr()
        log.info("AppService gestartet (Datenordner bereit)")

    # ------------------------------------------------------------------
    # Lebenszyklus / Einstellungen
    # ------------------------------------------------------------------
    def _build_ocr(self) -> None:
        cmd = find_tesseract(self.settings.tesseract_cmd)
        s = self.settings
        backends = {
            "tesseract": TesseractBackend(s.ocr_languages, cmd, settings=s, timeout_s=float(s.ocr_timeout_s)),
            "rapidocr": RapidOcrBackend(settings=s),
            "docling": DoclingBackend(
                languages=s.ocr_languages,
                tesseract_cmd=cmd,
                artifacts_path=Path(s.docling_artifacts_path) if s.docling_artifacts_path else None,
                settings=s,
            ),
        }
        self.ocr = TextRecognitionService(s, backends=backends)
        self.tesseract_cmd = cmd

    def close(self) -> None:
        self.discard_previews()
        self.db.close()

    @property
    def export_dir(self) -> Path:
        return Path(self.settings.export_dir) if self.settings.export_dir else self.paths.exports

    @property
    def exporter(self) -> Exporter:
        return Exporter(self.export_dir, self.paths.export_log_file)

    def update_settings(self, **changes: object) -> Settings:
        data = self.settings.model_dump()
        data.update(changes)
        try:
            new = Settings.model_validate(data)
        except ValueError as exc:
            errors = getattr(exc, "errors", None)
            msg = errors()[0].get("msg", "") if callable(errors) and errors() else str(exc)
            raise SettingsError(msg.replace("Value error, ", "") or "Ungültige Einstellung.") from exc
        old = self.settings
        self.settings = new
        try:
            self._build_ocr()
        except (ValueError, OSError) as exc:
            self.settings = old
            self._build_ocr()
            raise SettingsError(f"Einstellung nicht übernommen: {getattr(exc, 'user_message', exc)}") from exc
        new.save(self.paths.settings_file)
        return new

    def today(self) -> date:
        return self._today()

    # ------------------------------------------------------------------
    # Status (Einstellungen / Datenschutz)
    # ------------------------------------------------------------------
    def ocr_status(self) -> dict[str, tuple[bool, str]]:
        return self.ocr.available_backends()

    def local_ai_status(self, pulling: bool = False):  # -> LocalAiStatus
        from ..local_ai.status import detect_status

        return detect_status(self.settings, pulling=pulling)

    def hardware_info(self):  # -> HardwareInfo
        from ..local_ai.status import hardware_info

        return hardware_info()

    def storage_overview(self) -> dict:
        return self.repo.storage_overview(export_dir=self.export_dir)

    # ------------------------------------------------------------------
    # Vorgänge
    # ------------------------------------------------------------------
    def create_case(self, title: str = "") -> Case:
        return self.repo.create_case(title)

    def list_cases(self, search: str | None = None, include_archived: bool = False) -> list[CaseSummary]:
        return self.repo.list_cases(search=search, include_archived=include_archived)

    def load_case(self, case_id: str) -> Case:
        return self.repo.load_case(case_id)

    def save_case(self, case: Case) -> None:
        with self._lock:
            self.repo.save_case(case)

    def rename_case(self, case: Case, title: str) -> None:
        case.title = title.strip()
        self.save_case(case)

    def archive_case(self, case_id: str, archived: bool = True) -> None:
        self.repo.archive_case(case_id, archived)

    def delete_case(self, case_id: str) -> None:
        try:
            case = self.repo.load_case(case_id)
            for d in case.documents:
                self._passwords.pop(d.id, None)
        except Exception:  # noqa: BLE001 – beschädigte Vorgänge müssen trotzdem löschbar sein
            log.warning("Vorgang %s nicht lesbar – wird ohne Laden gelöscht", case_id)
        self.discard_preview(case_id)
        self.repo.delete_case(case_id)

    # ------------------------------------------------------------------
    # Stufe 1–2: Ablage und Dokumentcheck
    # ------------------------------------------------------------------
    def add_document(self, case: Case, src_path: Path, password: str | None = None) -> AddDocumentResult:
        """Datei prüfen und als schreibgeschützte Kopie übernehmen (Original bleibt unverändert)."""
        src_path = Path(src_path)
        result = AddDocumentResult(file_name=src_path.name)
        check = check_document(src_path, password=password)
        result.check = check
        codes = {i.code for i in check.issues}
        if "PASSWORD_REQUIRED" in codes or "WRONG_PASSWORD" in codes:
            result.needs_password = True
            result.wrong_password = "WRONG_PASSWORD" in codes
            result.message = (
                "Das Passwort ist falsch. Bitte erneut eingeben."
                if result.wrong_password
                else "Das PDF ist passwortgeschützt. Bitte Passwort eingeben."
            )
            return result
        if not check.ok:
            errors = [i.message for i in check.issues if i.severity == Severity.ERROR]
            result.message = errors[0] if errors else "Die Datei kann nicht verarbeitet werden."
            return result
        doc = self.repo.add_document(case, src_path)
        stored_check = check
        if doc.sha256 != check.sha256:  # Datei wurde während des Imports verändert
            stored_check = check_document(Path(doc.stored_path), password=password)
        doc.check = stored_check
        if password:
            self._passwords[doc.id] = password
        result.document = doc
        result.duplicate = bool(doc.duplicate_of)
        result.accepted = not doc.excluded
        result.message = (
            "Diese Datei ist bereits im Vorgang enthalten (Dublette) und wird nicht ausgewertet."
            if doc.duplicate_of
            else "Datei übernommen."
        )
        case.stage = max(case.stage, PipelineStage.DOKUMENTCHECK)
        self.save_case(case)
        log.info("Dokument übernommen: vorgang=%s dokument=%s typ=%s", case.id, doc.id, check.file_type)
        return result

    def set_document_password(self, document_id: str, password: str) -> None:
        self._passwords[document_id] = password

    def _repoint_document_values(self, case: Case, old_id: str, new_id: str) -> None:
        """Belege auf ein inhaltsgleiches Duplikat umhängen (gleiche SHA-256)."""
        for key, fv in list(case.contract_fields.items()):
            cands = [
                c.model_copy(update={"evidence": c.evidence.model_copy(update={"document_id": new_id})})
                if c.evidence.document_id == old_id
                else c
                for c in fv.candidates
            ]
            update: dict[str, object] = {"candidates": cands}
            if fv.source_document_id == old_id:
                update["source_document_id"] = new_id
            case.contract_fields[key] = fv.model_copy(update=update)

    def _drop_document_values(self, case: Case, document_id: str) -> None:
        """Werte und Belege eines entfernten/ausgeschlossenen Dokuments zurücknehmen.

        Bestätigte Werte aus diesem Dokument werden zurückgesetzt (sie dürfen nicht mehr
        ohne Quelle in eine Vorlage gelangen). Manuell eingetragene Werte bleiben.
        """
        for key, fv in list(case.contract_fields.items()):
            remaining = [c for c in fv.candidates if c.evidence.document_id != document_id]
            if fv.status == FieldStatus.CONFIRMED and fv.source_document_id == document_id:
                case.contract_fields[key] = FieldValue(
                    key=key, status=FieldStatus.MISSING, candidates=remaining, note=SOURCE_REMOVED_NOTE
                )
                case.history.append(
                    HistoryEntry(
                        action="source_removed",
                        field_key=key,
                        old_status=fv.status,
                        new_status=FieldStatus.MISSING,
                        detail=document_id,
                    )
                )
            elif len(remaining) != len(fv.candidates):
                case.contract_fields[key] = fv.model_copy(update={"candidates": remaining})

    def remove_document(self, case: Case, document_id: str) -> None:
        removed = self._doc(case, document_id)
        duplicates = [d for d in case.documents if d.duplicate_of == document_id]
        successor = duplicates[0] if duplicates and not removed.excluded else None
        if successor is not None:
            # identische Datei bleibt im Vorgang: Erkennung übernehmen statt verwerfen
            if removed.text is not None:
                successor.text = removed.text.model_copy(update={"document_id": successor.id})
            successor.classification = removed.classification
            successor.user_doc_type_confirmed = removed.user_doc_type_confirmed
            successor.check = successor.check or removed.check
            if document_id in self._passwords:
                self._passwords[successor.id] = self._passwords[document_id]
            self._repoint_document_values(case, document_id, successor.id)
        else:
            self._drop_document_values(case, document_id)
        self.repo.remove_document(case, document_id)
        self._passwords.pop(document_id, None)
        self.extract_data(case)

    def set_document_excluded(self, case: Case, document_id: str, excluded: bool) -> None:
        doc = self._doc(case, document_id)
        doc.excluded = excluded
        case.add_history(
            HistoryEntry(
                actor="user",
                action="document_excluded" if excluded else "document_included",
                detail=document_id,
            )
        )
        if excluded:
            self._drop_document_values(case, document_id)
        self.extract_data(case)

    def set_document_type(self, case: Case, document_id: str, doc_type: DocumentType) -> None:
        doc = self._doc(case, document_id)
        if doc.classification is None:
            raise ServiceError("Bitte zuerst die Texterkennung ausführen.")
        doc.classification = doc.classification.model_copy(update={"doc_type": doc_type, "uncertain": False})
        doc.user_doc_type_confirmed = True
        case.add_history(
            HistoryEntry(actor="user", action="document_type_set", detail=f"{document_id}: {doc_type.value}")
        )
        self.save_case(case)

    def _doc(self, case: Case, document_id: str) -> SourceDocument:
        doc = case.document(document_id)
        if doc is None:
            raise ServiceError("Dokument nicht gefunden.")
        return doc

    def document_page_image(self, case: Case, document_id: str, page: int, dpi: int = 110):
        doc = self._doc(case, document_id)
        return load_page_image(Path(doc.stored_path), page, dpi=dpi, password=self._passwords.get(doc.id))

    # ------------------------------------------------------------------
    # Stufe 3: Texterkennung
    # ------------------------------------------------------------------
    def recognize_text(
        self,
        case: Case,
        *,
        force: bool = False,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> list[str]:
        """Text je Dokument lesen (Textebene zuerst, OCR nur wo nötig). Gibt Hinweise zurück."""
        notes: list[str] = []
        docs = [d for d in case.active_documents() if d.check and d.check.ok and (force or d.text is None)]
        total = len(docs)
        for idx, doc in enumerate(docs):
            if cancel is not None and cancel.is_set():
                raise RecognitionCancelled()

            def sub(cur: int, tot: int, msg: str, _idx: int = idx) -> None:
                if progress:
                    progress(
                        _idx * 100 + int(100 * cur / max(tot, 1)),
                        total * 100,
                        f"Dokument {_idx + 1}/{total}: {msg}",
                    )

            try:
                text = self.ocr.recognize_document(
                    Path(doc.stored_path),
                    doc.id,
                    doc.check,  # type: ignore[arg-type]
                    password=self._passwords.get(doc.id),
                    progress=sub,
                    cancel=cancel,
                )
            except PasswordRequiredError:
                notes.append(f"{doc.original_name}: Passwort erforderlich – bitte erneut eingeben.")
                continue
            except (RecognitionError, DocumentAccessError) as exc:
                notes.append(f"{doc.original_name}: {getattr(exc, 'user_message', None) or str(exc)}")
                continue
            doc.text = text
            cls = classify_document(text)
            if doc.user_doc_type_confirmed and doc.classification is not None:
                cls = cls.model_copy(update={"doc_type": doc.classification.doc_type, "uncertain": False})
            doc.classification = cls
            engines = sorted({p.engine_version for p in text.pages if p.engine_version})
            if engines:
                case.metadata["texterkennung"] = "; ".join(engines)
            for p in text.pages:
                for w in p.warnings:
                    notes.append(f"{doc.original_name}, Seite {p.page_number}: {w}")
            case.add_history(HistoryEntry(action="text_recognized", detail=doc.id, source=", ".join(engines)))
            log.info(
                "Texterkennung abgeschlossen: vorgang=%s dokument=%s seiten=%d",
                case.id,
                doc.id,
                len(text.pages),
            )
        if total:
            case.stage = max(case.stage, PipelineStage.TEXTERKENNUNG)
        self.save_case(case)
        if progress:
            progress(max(total, 1) * 100, max(total, 1) * 100, "Texterkennung abgeschlossen")
        return notes

    # ------------------------------------------------------------------
    # Stufe 4–5: Extraktion, Konflikte, Plausibilität
    # ------------------------------------------------------------------
    def extract_data(self, case: Case) -> ExtractionSummary:
        results = []
        for doc in case.active_documents():
            if doc.text is None:
                continue
            cls = doc.classification
            info = SourceDocInfo(
                document_id=doc.id,
                file_name=doc.original_name,
                doc_type=cls.doc_type if cls else DocumentType.UNKNOWN,
                document_date=cls.document_date if cls else None,
                effective_date=cls.effective_date if cls else None,
            )
            results.append((info, extract_candidates(doc.text, info)))
        existing = dict(case.contract_fields)
        outcome = merge_candidates(results, existing=existing)
        fields = outcome.fields
        kept_marks: set[str] = set()
        # Vom Nutzer als "unklar" markierte Felder: Markierung bleibt, neue Belege/Konflikte werden sichtbar
        for key, old in existing.items():
            if not (old.user_marked_unclear and key in fields):
                continue
            merged = fields[key]
            if merged.status == FieldStatus.CONFLICT:
                fields[key] = merged.model_copy(update={"user_marked_unclear": True})
            else:
                fields[key] = old.model_copy(update={"candidates": merged.candidates})
                kept_marks.add(key)
        case.contract_fields = fields
        for h in outcome.history:
            if h.field_key in kept_marks:
                continue
            case.history.append(h)
        warnings = list(outcome.warnings)
        case.metadata["merge_warnings"] = json.dumps([w.model_dump() for w in warnings], ensure_ascii=False)
        case.metadata["extracted_at"] = utcnow().isoformat()
        self._revalidate(case)
        case.stage = max(case.stage, PipelineStage.PRUEFUNG)
        self.save_case(case)
        counts: dict[str, int] = {}
        for fv in case.contract_fields.values():
            counts[fv.status.value] = counts.get(fv.status.value, 0) + 1
        log.info("Extraktion abgeschlossen: vorgang=%s dokumente=%d", case.id, len(results))
        return ExtractionSummary(counts=counts, warnings=warnings, documents_used=len(results))

    def process_documents(
        self, case: Case, *, progress: ProgressCallback | None = None, cancel: threading.Event | None = None
    ) -> tuple[list[str], ExtractionSummary]:
        """Stufen 3–5 in einem Schritt (für „Weiter“ nach dem Upload)."""
        notes = self.recognize_text(case, progress=progress, cancel=cancel)
        summary = self.extract_data(case)
        return notes, summary

    def _revalidate(self, case: Case) -> None:
        today = self.today()
        for store in (case.contract_fields, case.order_fields):
            for key, fv in store.items():
                fdef = FIELD_REGISTRY.get(key)
                if fdef is None:
                    continue
                fv.validation = validate_field(fdef, fv, today) if fv.status != FieldStatus.CONFLICT else []

    def case_messages(self, case: Case) -> list[ValidationMessage]:
        """Fall-übergreifende Meldungen: Kreuzprüfungen, Plausibilität, Dokumentzuordnung."""
        today = self.today()
        msgs = [
            m
            for m in validate_case(case.contract_fields, case.order_fields, today)
            if m.field_key is None or "DATE_ORDER" in m.code
        ]
        with contextlib.suppress(ValueError, TypeError):
            msgs += [
                ValidationMessage.model_validate(m)
                for m in json.loads(case.metadata.get("merge_warnings", "[]"))
            ]
        if case.template_id:
            tpl = self._try_active_template(case.template_id)
            if tpl is not None:
                msgs += self._plausibility(tpl, case, today)
        return msgs

    def _plausibility(self, tpl: ActiveTemplate, case: Case, today: date) -> list[ValidationMessage]:
        if tpl.kind == "form":
            return check_plausibility(tpl.plausibility_rules, case.contract_fields, case.order_fields, today)
        return check_letter_plausibility(
            tpl.plausibility_rules, case.contract_fields, case.order_fields, today
        )

    # ------------------------------------------------------------------
    # Lokale KI (optional)
    # ------------------------------------------------------------------
    def run_local_ai(
        self, case: Case, *, progress: ProgressCallback | None = None, cancel: threading.Event | None = None
    ) -> dict[str, object]:
        """Zweitprüfung schwieriger Seiten mit dem lokalen Vision-Modell (nur wenn aktiviert und bereit)."""
        from ..local_ai.ollama_client import OllamaClient
        from ..local_ai.reconcile import reconcile
        from ..local_ai.status import LocalAiState, detect_status
        from ..local_ai.vlm_extractor import VlmExtractor, check_field_keys

        status = detect_status(self.settings)
        if status.state != LocalAiState.READY:
            raise ServiceError(f"Lokale KI nicht verfügbar: {status.message}")
        targets = [
            k
            for k, fdef in FIELD_REGISTRY.items()
            if fdef.category == FieldCategory.CONTRACT
            and fdef.extractable
            and not fdef.legal_relevant
            and (
                case.contract_fields.get(k) is None
                or case.contract_fields[k].status
                in (FieldStatus.MISSING, FieldStatus.UNCLEAR, FieldStatus.CONFLICT)
            )
        ]
        if case.template_id:
            tpl = self._try_active_template(case.template_id)
            if tpl is not None:
                relevant = tpl.referenced_keys()
                targets = [k for k in targets if k in relevant] or targets
        targets = check_field_keys(targets) if targets else []
        if not targets:
            return {"ok": True, "message": "Keine offenen Felder für die lokale KI.", "candidates": 0}
        client = OllamaClient(self.settings.ollama_url, timeout=10.0)
        extractor = VlmExtractor(
            client,
            self.settings.ai_model,
            timeout=float(self.settings.ai_timeout_s),
            max_tokens=self.settings.ai_max_tokens,
            normalizer=lambda key, raw: normalize_value(get_field(key), raw)[0],
        )
        all_candidates: list[Candidate] = []
        errors: list[str] = []
        docs = [d for d in case.active_documents() if d.text is not None]
        for i, doc in enumerate(docs):
            if cancel is not None and cancel.is_set():
                break
            weak_pages = [p for p in doc.text.pages if p.quality.value != "GOOD"] or doc.text.pages[:2]  # type: ignore[union-attr]
            pages = [
                (p.page_number, self.document_page_image(case, doc.id, p.page_number, dpi=150))
                for p in weak_pages[:3]
            ]
            page_texts = {p.page_number: p.text for p in weak_pages[:3]}
            if progress:
                progress(i, len(docs), f"Lokale KI prüft Dokument {i + 1}/{len(docs)}")
            res = extractor.extract(pages, doc.id, doc.original_name, targets, page_texts=page_texts)
            all_candidates += res.candidates
            errors += res.errors
            if res.model_version:
                case.metadata["lokale_ki_modell"] = res.model_version
        new_fields, history = reconcile(case.contract_fields, all_candidates)
        case.contract_fields = new_fields
        case.history.extend(history)
        self._revalidate(case)
        self.save_case(case)
        return {"ok": not errors, "errors": errors, "candidates": len(all_candidates)}

    # ------------------------------------------------------------------
    # Stufe 6: Datenfreigabe (Nutzeraktionen)
    # ------------------------------------------------------------------
    def _set(
        self, case: Case, new: FieldValue, action: str, detail: str | None = None, actor: str = "user"
    ) -> FieldValue:
        old = case.get(new.key)
        fdef = FIELD_REGISTRY[new.key]
        new.validation = validate_field(fdef, new, self.today()) if new.status != FieldStatus.CONFLICT else []
        case.set_field(new)
        case.add_history(
            HistoryEntry(
                actor=actor,
                action=action,
                field_key=new.key,
                old_status=old.status if old else None,
                new_status=new.status,
                old_value=(old.normalized_value or old.value) if old else None,
                new_value=new.normalized_value or new.value,
                detail=detail,
            )
        )
        self.save_case(case)
        return new

    def confirm_field(self, case: Case, key: str) -> FieldValue:
        fv = case.field(key)
        if fv.status == FieldStatus.CONFLICT:
            raise ServiceError(
                "Bitte zuerst einen der widersprüchlichen Werte auswählen oder den Wert manuell eintragen."
            )
        if not (fv.value or fv.normalized_value):
            raise ServiceError("Für dieses Feld liegt kein Wert vor. Bitte manuell eintragen.")
        if fv.normalized_value is None and FIELD_REGISTRY[key].dtype not in (FieldType.TEXT, FieldType.NAME):
            raise ServiceError("Der Wert ist nicht eindeutig lesbar. Bitte korrigieren.")
        if FIELD_REGISTRY[key].category == FieldCategory.ORDER:
            raise ServiceError("Auftragsdaten werden direkt ausgewählt bzw. eingetragen.")
        new = fv.model_copy(
            deep=True,
            update={
                "status": FieldStatus.CONFIRMED,
                "user_confirmed_at": utcnow(),
                "user_marked_unclear": False,
            },
        )
        return self._set(case, new, "confirmed")

    def confirm_fields(self, case: Case, keys: list[str]) -> list[str]:
        """Mehrere eindeutig erkannte Felder bestätigen. Gibt die nicht bestätigten Schlüssel zurück."""
        skipped = []
        for key in keys:
            try:
                self.confirm_field(case, key)
            except ServiceError:
                skipped.append(key)
        return skipped

    def edit_field(
        self,
        case: Case,
        key: str,
        raw_value: str,
        *,
        actor: str = "user",
        detail: str | None = None,
        proposal: bool = False,
    ) -> FieldValue:
        """Wert manuell eintragen/korrigieren (Status MANUAL).

        ``proposal=True`` kennzeichnet einen Vorschlag der Vorlage (z. B. heutiges Briefdatum):
        sichtbar als „bitte prüfen“, ohne Bestätigungszeitpunkt des Nutzers.
        """
        fdef = get_field(key)
        raw = (raw_value or "").strip()
        if not raw:
            raise FieldInputError("Bitte einen Wert eingeben oder das Feld als „nicht relevant“ markieren.")
        normalized, note = normalize_value(fdef, raw, strict=True)
        if normalized is None:
            hint = {
                FieldType.DATE: "Bitte im Format TT.MM.JJJJ eingeben.",
                FieldType.MONTH: "Bitte im Format MM.JJJJ eingeben.",
                FieldType.AMOUNT: "Bitte einen Betrag wie 1.234,56 eingeben.",
                FieldType.IBAN: "Bitte eine gültige IBAN eingeben.",
                FieldType.POSTAL_CODE: "Bitte eine Postleitzahl eingeben.",
            }.get(fdef.dtype, note or "Eingabe nicht verständlich.")
            raise FieldInputError(f"{fdef.label}: {hint}")
        old = case.get(key)
        new = FieldValue(
            key=key,
            value=raw,
            normalized_value=normalized,
            status=FieldStatus.MANUAL,
            extraction_method=ExtractionMethod.MANUAL,
            user_confirmed_at=None if proposal else utcnow(),
            candidates=list(old.candidates) if old else [],
            note=DEFAULT_NOTE if proposal else note,
        )
        return self._set(case, new, "proposed" if proposal else "edited", detail=detail, actor=actor)

    def set_choice(self, case: Case, key: str, value: str | bool) -> FieldValue:
        """Ausdrückliche Auswahl für Ja/Nein- oder Auswahlfelder (Status MANUAL)."""
        fdef = get_field(key)
        if fdef.dtype == FieldType.BOOL:
            norm = (
                "true"
                if value in (True, "true", "ja", "Ja")
                else "false"
                if value in (False, "false", "nein", "Nein")
                else None
            )
            label = "ja" if norm == "true" else "nein"
        elif fdef.dtype == FieldType.CHOICE:
            norm = str(value) if fdef.choices and str(value) in fdef.choices else None
            label = (fdef.choices or {}).get(str(value), "")
        else:
            raise ServiceError("Dieses Feld ist kein Auswahlfeld.")
        if norm is None:
            raise FieldInputError(f"{fdef.label}: ungültige Auswahl.")
        new = FieldValue(
            key=key,
            value=label,
            normalized_value=norm,
            status=FieldStatus.MANUAL,
            extraction_method=ExtractionMethod.MANUAL,
            user_confirmed_at=utcnow(),
        )
        return self._set(case, new, "decided")

    def choose_candidate(self, case: Case, key: str, index: int) -> FieldValue:
        fv = case.field(key)
        if not 0 <= index < len(fv.candidates):
            raise ServiceError("Ungültige Auswahl.")
        c = fv.candidates[index]
        if c.normalized is None:
            raise ServiceError("Dieser Wert ist nicht eindeutig lesbar. Bitte manuell eintragen.")
        new = fv.model_copy(
            deep=True,
            update={
                "value": c.value,
                "normalized_value": c.normalized,
                "status": FieldStatus.CONFIRMED,
                "confidence": c.confidence,
                "source_document_id": c.evidence.document_id,
                "source_page": c.evidence.page,
                "source_quote": c.evidence.quote,
                "extraction_method": c.evidence.method,
                "model_version": c.evidence.model_version,
                "user_confirmed_at": utcnow(),
                "user_marked_unclear": False,
                "note": None,
            },
        )
        return self._set(
            case, new, "candidate_chosen", detail=f"Quelle: {c.evidence.file_name}, S. {c.evidence.page}"
        )

    def mark_unclear(self, case: Case, key: str, note: str | None = None) -> FieldValue:
        fv = case.field(key)
        if FIELD_REGISTRY[key].category == FieldCategory.ORDER:
            raise ServiceError("Auftragsdaten können nicht als unklar markiert werden – bitte leer lassen.")
        new = fv.model_copy(
            deep=True,
            update={
                "status": FieldStatus.UNCLEAR,
                "user_marked_unclear": True,
                "note": note or "Vom Nutzer als unklar markiert.",
            },
        )
        return self._set(case, new, "marked_unclear")

    def mark_not_applicable(self, case: Case, key: str) -> FieldValue:
        fdef = get_field(key)
        if fdef.legal_relevant and fdef.dtype in (FieldType.BOOL, FieldType.CHOICE):
            raise ServiceError(
                f"„{fdef.label}“ ist eine rechtlich relevante Entscheidung und kann nicht als "
                "„nicht relevant“ markiert werden. Bitte ausdrücklich auswählen."
            )
        fv = case.field(key)
        new = FieldValue(
            key=key,
            status=FieldStatus.NOT_APPLICABLE,
            candidates=list(fv.candidates),
            user_confirmed_at=utcnow(),
        )
        return self._set(case, new, "not_applicable")

    def reset_field(self, case: Case, key: str) -> FieldValue:
        fv = case.field(key)
        new = FieldValue(key=key, status=FieldStatus.MISSING, candidates=list(fv.candidates))
        if fv.candidates and FIELD_REGISTRY[key].category == FieldCategory.CONTRACT:
            # Erkennungsstand wiederherstellen: erneut zusammenführen
            self._set(case, new, "reset")
            self.extract_data(case)
            return case.field(key)
        return self._set(case, new, "reset")

    def copy_insurer_to_recipient(self, case: Case) -> FieldValue:
        ins = case.get("insurer.name")
        if ins is None or not ins.status.is_released:
            raise ServiceError("Bitte zuerst den Versicherer in den Vertragsdaten bestätigen.")
        return self.edit_field(case, "order.recipient_company", ins.normalized_value or ins.value or "")

    # ------------------------------------------------------------------
    # Stufe 7: Vorlagen
    # ------------------------------------------------------------------
    def template_choices(self, refresh: bool = False) -> list[TemplateChoice]:
        out: list[TemplateChoice] = []
        for item in self.registry.list_items(refresh=refresh):
            e = item.entry
            kind: Literal["form", "letter"] | None = None
            status, reason, version = item.status, item.status_reason, item.version
            if item.status == TemplateStatus.ACTIVE:
                kind = "form"
            elif e.letter_file:
                try:
                    lt = load_letter_template(self.paths.letter_templates_dir / e.letter_file)
                    pending = item.status != TemplateStatus.MISSING_ORIGINAL
                    kind, status, version = "letter", TemplateStatus.ACTIVE, lt.version
                    reason = "Kundenschreiben nach DIN 5008 (wird erzeugt – kein Versicherer-Formular)."
                    if pending:
                        reason += (
                            f" Eine importierte Originalvorlage ist noch nicht aktiv ({item.status.value})."
                        )
                except (OSError, ValueError) as exc:
                    status, reason = TemplateStatus.ERROR, f"Briefvorlage fehlerhaft ({type(exc).__name__})."
            out.append(
                TemplateChoice(
                    catalog_id=e.id,
                    number=e.number,
                    group=e.group,
                    title=e.title,
                    insurer=e.insurer,
                    form_number=e.form_number,
                    description=e.description,
                    kind=kind,
                    status=status,
                    status_reason=reason,
                    legal_hint=e.legal_hint,
                    version=version,
                )
            )
        return sorted(out, key=lambda c: c.number)

    def _try_active_template(self, catalog_id: str) -> ActiveTemplate | None:
        try:
            return self.active_template(catalog_id)
        except ServiceError:
            return None

    def active_template(self, catalog_id: str) -> ActiveTemplate:
        choice = next((c for c in self.template_choices() if c.catalog_id == catalog_id), None)
        if choice is None:
            raise ServiceError("Unbekannte Vorlage.")
        if not choice.available:
            raise ServiceError(f"Vorlage nicht verfügbar: {choice.status_reason}")
        if choice.kind == "form":
            try:
                return ActiveTemplate(
                    catalog_id,
                    "form",
                    form=self.registry.get_definition(catalog_id),
                    pdf_path=self.registry.get_pdf_path(catalog_id),
                )
            except TemplateRegistryError as exc:
                raise ServiceError(exc.user_message) from exc
        entry = self.registry.entry(catalog_id)
        return ActiveTemplate(
            catalog_id,
            "letter",
            letter=load_letter_template(self.paths.letter_templates_dir / entry.letter_file),
        )  # type: ignore[operator]

    @staticmethod
    def _acknowledgment_keys(tpl: ActiveTemplate) -> set[str]:
        acks = tpl.form.acknowledgments if tpl.form else tpl.letter.acknowledgments  # type: ignore[union-attr]
        return {a.field for a in acks}

    def select_template(self, case: Case, catalog_id: str) -> ActiveTemplate:
        tpl = self.active_template(catalog_id)
        changed = case.template_id != catalog_id or case.template_version != tpl.version
        case.template_id = catalog_id
        case.template_version = tpl.version
        case.add_history(
            HistoryEntry(actor="user", action="template_selected", detail=f"{catalog_id} ({tpl.version})")
        )
        if changed:
            # Bestätigungen gelten nur für die Vorlage, für die sie abgegeben wurden
            for key in self._acknowledgment_keys(tpl):
                cur = case.get(key)
                if cur is not None and cur.status != FieldStatus.MISSING:
                    self._set(
                        case,
                        FieldValue(key=key, status=FieldStatus.MISSING),
                        "reset",
                        detail="Vorlage gewechselt – Bestätigung erneut erforderlich",
                        actor="system",
                    )
        if tpl.kind == "letter" and tpl.letter is not None:
            for key, value in tpl.letter.defaults.items():
                cur = case.get(key)
                if cur is None or cur.status == FieldStatus.MISSING:
                    self.edit_field(
                        case,
                        key,
                        value,
                        actor="system",
                        proposal=True,
                        detail="Standardwert der Vorlage – bitte prüfen",
                    )
            self._propose_letter_date(case)
        case.stage = max(case.stage, PipelineStage.VORLAGE_WAEHLEN)
        self.save_case(case)
        return tpl

    def _propose_letter_date(self, case: Case) -> None:
        """Briefdatum vorschlagen bzw. einen unveränderten Vorschlag auf heute aktualisieren."""
        cur = case.get("order.letter_date")
        today = self.today()
        is_proposal = cur is not None and cur.note == DEFAULT_NOTE and cur.user_confirmed_at is None
        if cur is None or cur.status == FieldStatus.MISSING or (is_proposal and cur.as_date() != today):
            self.edit_field(
                case,
                "order.letter_date",
                f"{today.day:02d}.{today.month:02d}.{today.year}",
                actor="system",
                proposal=True,
                detail="Heutiges Datum als Briefdatum vorgeschlagen – bitte prüfen",
            )

    def _template_version_item(self, case: Case, tpl: ActiveTemplate) -> ChecklistItem | None:
        if case.template_version and case.template_version != tpl.version:
            return ChecklistItem(
                severity=Severity.ERROR,
                code="TEMPLATE_VERSION_CHANGED",
                message="Die Vorlage wurde inzwischen geändert (neue Version). Bitte die Vorlage erneut auswählen.",
                blocking=True,
            )
        return None

    def _proposal_items(self, case: Case, keys: set[str]) -> list[ChecklistItem]:
        items = []
        for key in sorted(keys):
            fv = case.get(key)
            if (
                fv is not None
                and fv.note == DEFAULT_NOTE
                and fv.user_confirmed_at is None
                and fv.status.is_released
            ):
                items.append(
                    ChecklistItem(
                        severity=Severity.WARNING,
                        code="PROPOSAL_UNREVIEWED",
                        message=f"Vorschlag übernommen – bitte prüfen: {self.field_label(key)}",
                        field_key=key,
                        blocking=False,
                    )
                )
        return items

    def clear_template(self, case: Case) -> None:
        case.template_id = None
        case.template_version = None
        self.save_case(case)

    # ------------------------------------------------------------------
    # Prüfstatus / Checkliste
    # ------------------------------------------------------------------
    def review_status(self, case: Case) -> ReviewStatus:
        if not case.template_id:
            return ReviewStatus(messages=self.case_messages(case))
        tpl = self.active_template(case.template_id)
        keys = tpl.referenced_keys()
        contract_keys = [
            k for k in FIELD_REGISTRY if k in keys and FIELD_REGISTRY[k].category == FieldCategory.CONTRACT
        ]
        order_keys = [
            k for k in FIELD_REGISTRY if k in keys and FIELD_REGISTRY[k].category == FieldCategory.ORDER
        ]
        messages = self.case_messages(case)
        plan = self._plan(case, tpl, mode="final", messages=messages)
        hints = plan.hints if isinstance(plan, LetterPlan) else []
        return ReviewStatus(
            template_id=case.template_id,
            relevant_contract_keys=contract_keys,
            relevant_order_keys=order_keys,
            checklist=self._merge_items(self._extra_items(case, tpl), plan.checklist),
            messages=messages,
            hints=hints,
        )

    def _extra_items(self, case: Case, tpl: ActiveTemplate) -> list[ChecklistItem]:
        items = self._proposal_items(case, tpl.referenced_keys())
        version_item = self._template_version_item(case, tpl)
        if version_item is not None:
            items.insert(0, version_item)
        return items

    @staticmethod
    def _merge_items(extra: list[ChecklistItem], items: list[ChecklistItem]) -> list[ChecklistItem]:
        merged = extra + items
        return sorted(merged, key=lambda i: (not i.blocking, i.severity != Severity.ERROR))

    def _field_messages(self, case: Case, keys: set[str]) -> list[ValidationMessage]:
        out: list[ValidationMessage] = []
        for key in keys:
            fv = case.get(key)
            if fv is not None and fv.status.is_released:
                out += [m.model_copy(update={"field_key": key}) for m in fv.validation]
        return out

    def _plan(
        self, case: Case, tpl: ActiveTemplate, mode: str, messages: list[ValidationMessage] | None = None
    ) -> FillPlan | LetterPlan:
        messages = messages if messages is not None else self.case_messages(case)
        if tpl.kind == "form":
            assert tpl.form is not None and tpl.pdf_path is not None
            inspection = inspect_pdf_form(tpl.pdf_path)
            return resolve_fill_values(
                tpl.form,
                case.contract_fields,
                case.order_fields,
                inspection,
                mode=mode,
                extra_messages=messages,
            )
        assert tpl.letter is not None
        extra = messages + self._field_messages(case, tpl.referenced_keys())
        return resolve_letter(
            tpl.letter, case.contract_fields, case.order_fields, mode=mode, extra_messages=extra
        )  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Stufe 8: Vorschau und Qualitätskontrolle
    # ------------------------------------------------------------------
    def _preview_dir(self, case_id: str) -> Path:
        return self.paths.preview_dir / case_id

    def discard_preview(self, case_id: str) -> None:
        force_rmtree(self._preview_dir(case_id))

    def discard_previews(self) -> None:
        force_rmtree(self.paths.preview_dir)

    def flatten_status(self, case: Case) -> tuple[bool, str]:
        if not case.template_id:
            return False, "Keine Vorlage gewählt."
        tpl = self.active_template(case.template_id)
        if tpl.kind == "letter":
            return False, "Briefe enthalten keine Formularfelder."
        perms = inspect_pdf_form(tpl.pdf_path).permissions  # type: ignore[arg-type]
        if perms.can_flatten:
            return True, "Formularfelder können fixiert werden."
        return (
            False,
            "Diese Vorlage erlaubt laut PDF-Berechtigungen kein Fixieren der Formularfelder. Die PDF bleibt ausfüllbar.",
        )

    def build_preview(self, case: Case, *, dpi: int = 110) -> PreviewResult:
        if not case.template_id:
            raise ServiceError("Bitte zuerst eine Vorlage wählen.")
        tpl = self.active_template(case.template_id)
        if tpl.kind == "letter":
            self._propose_letter_date(case)
        extra = self._extra_items(case, tpl)
        self.discard_preview(case.id)
        out_dir = self._preview_dir(case.id)
        out_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(out_dir, 0o700)
        pdf_path = out_dir / "vorschau.pdf"
        if tpl.kind == "form":
            plan = self._plan(case, tpl, mode="preview")
            assert isinstance(plan, FillPlan) and tpl.form is not None and tpl.pdf_path is not None
            try:
                self.filler.fill(tpl.form, tpl.pdf_path, plan, pdf_path)
            except PdfFillError as exc:
                raise ServiceError(exc.user_message) from exc
            qa = run_quality_checks(
                tpl.form, tpl.pdf_path, pdf_path, plan, render_dir=out_dir / "render", dpi=dpi
            )
            images = [Path(p) for p in qa.rendered_pages.get("mupdf", [])]
            allowed, reason = self.flatten_status(case)
            result = PreviewResult(
                kind="form",
                pdf_path=pdf_path,
                page_images=images,
                checklist=self._merge_items(extra, plan.checklist),
                qa=qa,
                blocking=plan.blocking or any(i.blocking for i in extra),
                flatten_allowed=allowed,
                flatten_reason=reason,
            )
        else:
            plan = self._plan(case, tpl, mode="preview")
            assert isinstance(plan, LetterPlan)
            render_letter(plan, pdf_path, self.paths.fonts_dir, preview=True, form=self.settings.letter_form)  # type: ignore[arg-type]
            qa = run_letter_qa(
                plan,
                pdf_path,
                final=False,
                form=self.settings.letter_form,
                render_dir=out_dir / "render",
                dpi=dpi,
            )
            images = [Path(p) for p in qa.rendered_pages.get("mupdf", [])] or save_previews(
                pdf_path, out_dir / "seiten", dpi=dpi
            )
            result = PreviewResult(
                kind="letter",
                pdf_path=pdf_path,
                page_images=images,
                checklist=self._merge_items(extra, plan.checklist),
                qa=qa,
                hints=plan.hints,
                blocking=plan.blocking or any(i.blocking for i in extra),
                flatten_allowed=False,
                flatten_reason="Briefe enthalten keine Formularfelder.",
            )
        case.stage = max(case.stage, PipelineStage.VORSCHAU)
        self.save_case(case)
        return result

    # ------------------------------------------------------------------
    # Stufe 9: Export ("PDF jetzt erzeugen")
    # ------------------------------------------------------------------
    def generate_pdf(self, case: Case, *, flatten: bool = False) -> ExportOutcome:
        if not case.template_id:
            raise ServiceError("Bitte zuerst eine Vorlage wählen.")
        tpl = self.active_template(case.template_id)
        if tpl.kind == "letter":
            self._propose_letter_date(case)
        version_item = self._template_version_item(case, tpl)
        if version_item is not None:
            raise ExportBlockedError(version_item.message)
        plan = self._plan(case, tpl, mode="final")
        if plan.blocking:
            items = "\n".join(f"• {i.message}" for i in plan.checklist if i.blocking)
            raise ExportBlockedError(f"Die PDF kann noch nicht erzeugt werden. Offene Punkte:\n{items}")
        work = self.paths.temp_dir / f"export-{case.id}"
        force_rmtree(work)
        work.mkdir(parents=True)
        os.chmod(work, 0o700)
        try:
            tmp_pdf = work / "ausgabe.pdf"
            if tpl.kind == "form":
                assert isinstance(plan, FillPlan) and tpl.form is not None and tpl.pdf_path is not None
                try:
                    res = self.filler.fill(tpl.form, tpl.pdf_path, plan, tmp_pdf, flatten=flatten)
                except PdfFillError as exc:
                    raise ExportBlockedError(exc.user_message) from exc
                qa = run_quality_checks(
                    tpl.form, tpl.pdf_path, res.output_path, plan, flattened=res.flattened
                )
                identity: object = tpl.form
                flattened = res.flattened
            else:
                assert isinstance(plan, LetterPlan) and tpl.letter is not None
                if flatten:
                    raise ExportBlockedError(
                        "Briefe enthalten keine Formularfelder – „Fixieren“ ist nicht nötig."
                    )
                render_letter(
                    plan, tmp_pdf, self.paths.fonts_dir, preview=False, form=self.settings.letter_form
                )  # type: ignore[arg-type]
                qa = run_letter_qa(plan, tmp_pdf, final=True, form=self.settings.letter_form)
                identity = LetterExportIdentity(
                    template_id=tpl.letter.template_id,
                    version=tpl.letter.version,
                    short_title=tpl.letter.short_title,
                )
                flattened = False
            if not qa.passed:
                failed = "\n".join(f"• {c.message}" for c in qa.errors)
                raise ExportBlockedError(f"Die Qualitätskontrolle ist nicht bestanden:\n{failed}")
            try:
                exported = self._finalize_export(case, identity, tmp_pdf, qa, flattened)
            except OSError as exc:
                raise ExportBlockedError(
                    "Der Exportordner ist nicht erreichbar oder nicht beschreibbar – bitte unter "
                    "„Einstellungen“ einen anderen Ordner wählen."
                ) from exc
        finally:
            force_rmtree(work)
        return self._record_export(case, tpl, exported, qa)

    def _finalize_export(self, case: Case, identity: object, tmp_pdf: Path, qa: QaReport, flattened: bool):
        return self.exporter.finalize(
            case.id, identity, tmp_pdf, qa, flattened=flattened, created=self.today()
        )  # type: ignore[arg-type]

    def _record_export(self, case: Case, tpl: ActiveTemplate, exported, qa: QaReport) -> ExportOutcome:  # noqa: ANN001
        entry = exported.log_entry
        self.repo.log_export(
            case.id,
            entry.file_name,
            entry.template_id,
            entry.template_version,
            entry.flattened,
            entry.qa_passed,
            entry.sha256,
        )
        case.exports.append(
            ExportRecord(
                template_id=entry.template_id,
                template_version=entry.template_version,
                file_path=str(exported.path),
                file_name=entry.file_name,
                sha256=entry.sha256,
                flattened=entry.flattened,
                qa_passed=True,
                qa_summary=f"{sum(1 for c in qa.checks if c.passed)}/{len(qa.checks)} Prüfungen bestanden",
                kind=tpl.kind,
            )
        )
        case.add_history(HistoryEntry(actor="user", action="export", detail=entry.file_name))
        case.stage = PipelineStage.EXPORT
        self.save_case(case)
        self.discard_preview(case.id)
        log.info("PDF erzeugt: vorgang=%s vorlage=%s", case.id, entry.template_id)
        return ExportOutcome(path=exported.path, qa=qa, log_entry=entry)

    # ------------------------------------------------------------------
    # Musterfall (synthetisch)
    # ------------------------------------------------------------------
    def create_sample_case(
        self, *, progress: ProgressCallback | None = None, cancel: threading.Event | None = None
    ) -> Case:
        """Vorgang mit synthetischen Musterdokumenten („MUSTER – KEINE ECHTDATEN“) anlegen und auswerten."""
        from ..samples import generate_sample_set
        from ..samples.generator import MUSTERFALL_FILES

        sample_dir = self.paths.temp_dir / "musterfall"
        force_rmtree(sample_dir)
        sample_set = generate_sample_set(sample_dir)
        case = self.create_case("MUSTER – Musterfall (synthetische Daten)")
        try:
            for name in MUSTERFALL_FILES:
                self.add_document(case, sample_set.files[name])
            self.process_documents(case, progress=progress, cancel=cancel)
        finally:
            force_rmtree(sample_dir)
        return case

    # ------------------------------------------------------------------
    # Hilfen für die Oberfläche
    # ------------------------------------------------------------------
    @staticmethod
    def field_label(key: str) -> str:
        fdef = FIELD_REGISTRY.get(key)
        return fdef.label if fdef else key

    def export_log(self) -> list[ExportLogEntry]:
        return self.exporter.read_log()

    def case_history(self, case_id: str) -> list[HistoryEntry]:
        return self.repo.list_history(case_id)
