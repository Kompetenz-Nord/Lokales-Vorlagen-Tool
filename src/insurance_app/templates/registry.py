"""Vorlagen-Registry: Katalog, Status, Versionen, Import und Paketformat.

Ablage
------
* Mitgeliefert: Originale in ``paths.original_templates_dir``, Zuordnungen in
  ``paths.config_dir / "field_schemas"`` (nur lesen).
* Importiert (versioniert): ``paths.user_templates_dir/<katalog-id>/<version>/``
  mit ``original.pdf`` (schreibgeschützt), ``mapping.json`` und ``meta.json``.

Aktive Version: neueste bestätigte importierte Version, sonst die mitgelieferte
(falls deren Original vorhanden ist), sonst der neueste unbestätigte Import.

Status
------
* ``Originalvorlage fehlt`` – kein Original vorhanden.
* ``Fehler`` – Prüfsumme falsch, PDF nicht lesbar, zugeordnetes Feld oder
  Ein-Zustand fehlt, keine Ausfüllberechtigung (ohne Overlay) usw.
* ``Zu prüfen`` – Zuordnung noch nicht bestätigt.
* ``Aktiv`` – alles geprüft und bestätigt.

Paketformat (ZIP): ``manifest.json`` {catalog_id, version, sha256, created},
``mapping.json``, ``original.pdf``. Importierte Pakete müssen im
Vorlagen-Studio erneut bestätigt werden (Status „Zu prüfen“).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import zipfile
import zlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from insurance_app.core.models import utcnow
from insurance_app.core.paths import AppPaths
from insurance_app.pdf.inspector import FormInspection, PdfOpenError, PdfPermissions, inspect_pdf_form
from insurance_app.templates.schema import (
    CatalogEntry,
    FillPolicy,
    MappingKind,
    TemplateCatalog,
    TemplateDefinition,
    TemplateStatus,
)
from insurance_app.templates.studio import IMPORTED_PDF_NAME, TemplateDraft, auto_mapping

__all__ = [
    "MAX_MEMBER_BYTES",
    "MISSING_ORIGINAL_REASON",
    "CatalogItem",
    "TemplateCheckReport",
    "TemplateNotAvailableError",
    "TemplateRegistry",
    "TemplateRegistryError",
    "TemplateVersionInfo",
]

log = logging.getLogger(__name__)

MISSING_ORIGINAL_REASON = "Originalvorlage fehlt – bitte im Vorlagen-Studio importieren."
NOT_CONFIRMED_REASON = "Feldzuordnung noch nicht bestätigt – bitte im Vorlagen-Studio prüfen und bestätigen."
ACTIVE_REASON = "Originalvorlage geprüft, Feldzuordnung bestätigt."
MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
PACKAGE_MEMBERS = ("manifest.json", "mapping.json", "original.pdf")
PACKAGE_FORMAT = 1
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

Source = Literal["mitgeliefert", "importiert"]


class TemplateRegistryError(Exception):
    """Fehler bei Vorlagenverwaltung, Import oder Paket. ``user_message`` für die Oberfläche."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message = message


class TemplateNotAvailableError(TemplateRegistryError):
    """Für die Vorlage gibt es (noch) keine nutzbare Version."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogItem(_Model):
    entry: CatalogEntry
    status: TemplateStatus
    status_reason: str
    version: str | None = None
    updated_at: datetime | None = None
    source: Source | None = None


class TemplateCheckReport(_Model):
    catalog_id: str
    status: TemplateStatus
    reasons: list[str] = Field(default_factory=list)  # Begründungen (bei Aktiv leer)
    version: str | None = None
    pdf_path: Path | None = None
    permissions: PdfPermissions | None = None
    notes: list[str] = Field(default_factory=list)  # zusätzliche Hinweise (z. B. neue Version wartet)
    source: Source | None = None


class TemplateVersionInfo(_Model):
    catalog_id: str
    version: str
    pdf_path: Path
    mapping_path: Path
    sha256: str
    imported_at: datetime | None = None
    confirmed: bool = False
    source: Source


class _VersionMeta(_Model):
    catalog_id: str
    version: str
    sha256: str
    imported_at: datetime
    confirmed: bool = False
    confirmed_at: datetime | None = None
    source: Literal["importiert"] = "importiert"
    from_package: bool = False


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, data: bytes, read_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if read_only:
            tmp.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_json(path: Path, model: BaseModel) -> None:
    text = json.dumps(
        model.model_dump(mode="json", by_alias=True, exclude_none=False), ensure_ascii=False, indent=2
    )
    _atomic_write(path, (text + "\n").encode("utf-8"))


def _check_version(version: str) -> str:
    v = version.strip()
    if not _VERSION_RE.fullmatch(v) or ".." in v:
        raise TemplateRegistryError(
            "Ungültige Versionsbezeichnung. Erlaubt sind Buchstaben, Ziffern, Punkt, Binde- und Unterstrich."
        )
    return v


def _looks_like_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def _check_pdf_bytes(data: bytes) -> None:
    if len(data) > MAX_PDF_BYTES:
        raise TemplateRegistryError("Die PDF ist zu groß (mehr als 50 MB).")
    if not _looks_like_pdf(data):
        raise TemplateRegistryError("Die Datei ist keine PDF.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise TemplateRegistryError("Die PDF ist beschädigt und kann nicht gelesen werden.") from exc
    try:
        if doc.needs_pass:
            raise TemplateRegistryError(
                "Die PDF ist mit einem Öffnungspasswort geschützt und kann nicht als Vorlage dienen."
            )
        if doc.page_count < 1:
            raise TemplateRegistryError("Die PDF enthält keine Seiten.")
    finally:
        doc.close()


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


_FIELD_TYPES_FOR_KIND: dict[MappingKind, tuple[str, ...]] = {
    MappingKind.TEXT: ("text", "combobox"),
    MappingKind.MULTILINE: ("text",),
    MappingKind.DATE_PART: ("text",),
    MappingKind.AMOUNT: ("text",),
    MappingKind.STATIC: ("text",),
    MappingKind.SIGNATURE_DATE: ("text",),
    MappingKind.CHECKBOX: ("checkbox", "radio"),
    MappingKind.RADIO: ("radio", "checkbox"),
    MappingKind.SIGNATURE: ("signature",),
}

_TYPE_LABEL = {
    "text": "Textfeld",
    "checkbox": "Ankreuzfeld",
    "radio": "Optionsgruppe",
    "signature": "Unterschriftsfeld",
    "button": "Schaltfläche",
    "combobox": "Auswahlliste",
    "listbox": "Liste",
}


def _names(names: list[str], limit: int = 5) -> str:
    shown = ", ".join(f"„{n}“" for n in names[:limit])
    more = len(names) - limit
    return shown + (f" und {more} weitere" if more > 0 else "")


def validate_definition_against_pdf(
    definition: TemplateDefinition, pdf_path: Path
) -> tuple[list[str], PdfPermissions | None, FormInspection | None]:
    """Zuordnung gegen die PDF prüfen -> (Fehler, Berechtigungen, Inspektion)."""
    errors: list[str] = []
    try:
        sha = _sha256_file(pdf_path)
    except OSError:
        return ["Originalvorlage kann nicht gelesen werden."], None, None
    if sha != definition.sha256:
        errors.append(
            "Prüfsumme der Originalvorlage passt nicht zur Feldzuordnung (Datei verändert oder ersetzt)."
        )
    try:
        inspection = inspect_pdf_form(pdf_path)
    except PdfOpenError:
        errors.append("Originalvorlage kann nicht gelesen werden (beschädigt oder keine PDF).")
        return errors, None, None
    perms = inspection.permissions
    if perms.needs_password:
        errors.append("Originalvorlage ist mit einem Öffnungspasswort geschützt.")
        return errors, perms, inspection
    if inspection.page_count != definition.page_count:
        errors.append(
            f"Seitenzahl der Originalvorlage ({inspection.page_count}) passt nicht zur Zuordnung "
            f"({definition.page_count})."
        )
    if inspection.is_xfa:
        errors.append("XFA-Formulare werden nicht unterstützt.")
    if inspection.signed_signature_fields:
        errors.append("Die Originalvorlage enthält bereits eine digitale Unterschrift.")
    for page in definition.pages_to_preserve:
        if not 1 <= page <= inspection.page_count:
            errors.append(f"Zu erhaltende Seite {page} existiert nicht.")

    missing: list[str] = []
    wrong_type: list[str] = []
    wrong_page: list[str] = []
    state_errors: list[str] = []
    overlay = 0
    fillable_fields = 0
    for m in definition.fields:
        if m.overlay_rect is not None:
            overlay += 1
            if m.page is not None and not 1 <= m.page <= inspection.page_count:
                wrong_page.append(m.pdf_field)
            continue
        info = inspection.field(m.pdf_field)
        if info is None:
            missing.append(m.pdf_field)
            continue
        if info.field_type not in _FIELD_TYPES_FOR_KIND.get(m.kind, ()):
            wrong_type.append(f"{m.pdf_field} ({_TYPE_LABEL.get(info.field_type, info.field_type)})")
            continue
        if m.page is not None and m.page not in info.pages:
            wrong_page.append(m.pdf_field)
        if (
            m.kind not in (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE)
            and m.fill_policy != FillPolicy.NEVER
        ):
            fillable_fields += 1
        if m.kind == MappingKind.CHECKBOX and not info.on_states:
            state_errors.append(f"„{m.pdf_field}“: Ein-Zustand fehlt")
        if m.kind == MappingKind.RADIO:
            absent = [s for s in (m.choice_map or {}).values() if s not in info.on_states]
            if absent:
                state_errors.append(f"„{m.pdf_field}“: Auswahlzustand {', '.join(absent)} fehlt")
    if missing:
        errors.append(f"Zugeordnete Felder fehlen in der PDF: {_names(missing)}.")
    if wrong_type:
        errors.append(f"Feldtyp passt nicht zur Zuordnung: {_names(wrong_type)}.")
    if wrong_page:
        errors.append(f"Seitenangabe passt nicht: {_names(wrong_page)}.")
    if state_errors:
        errors.append("Ankreuz-Zustände fehlen in der PDF: " + "; ".join(state_errors[:5]) + ".")
    if fillable_fields and not perms.can_fill_forms:
        errors.append("Die Vorlage erlaubt laut PDF-Berechtigungen kein Ausfüllen der Formularfelder.")
    if overlay and not perms.can_flatten:
        errors.append(
            "Die Vorlage erlaubt laut PDF-Berechtigungen keine Änderungen am Seiteninhalt (Koordinaten-Felder)."
        )
    if not definition.fields:
        errors.append("Die Feldzuordnung enthält keine Felder.")
    return errors, perms, inspection


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TemplateRegistry:
    """Verwaltung der Vorlagen (siehe Modul-Dokumentation)."""

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self._catalog: TemplateCatalog | None = None
        self._cache: dict[str, TemplateCheckReport] = {}

    # -- Katalog ------------------------------------------------------------
    @property
    def catalog_file(self) -> Path:
        return self.paths.config_dir / "template_catalog.json"

    @property
    def field_schema_dir(self) -> Path:
        return self.paths.config_dir / "field_schemas"

    def catalog(self) -> TemplateCatalog:
        if self._catalog is None:
            try:
                self._catalog = TemplateCatalog.model_validate_json(self.catalog_file.read_text("utf-8"))
            except (OSError, ValidationError, ValueError) as exc:
                log.error("Vorlagenkatalog nicht lesbar (%s)", type(exc).__name__)
                raise TemplateRegistryError("Der Vorlagenkatalog kann nicht gelesen werden.") from exc
        return self._catalog

    def entry(self, catalog_id: str) -> CatalogEntry:
        for e in self.catalog().entries:
            if e.id == catalog_id:
                return e
        raise TemplateRegistryError("Diese Vorlage ist im Katalog nicht vorhanden.")

    def _invalidate(self, catalog_id: str | None = None) -> None:
        if catalog_id is None:
            self._cache.clear()
        else:
            self._cache.pop(catalog_id, None)

    # -- Versionen ------------------------------------------------------------
    def _user_dir(self, catalog_id: str) -> Path:
        return self.paths.user_templates_dir / catalog_id

    def _shipped(self, entry: CatalogEntry) -> TemplateVersionInfo | None:
        if not entry.mapping_file:
            return None
        mapping_path = self.field_schema_dir / entry.mapping_file
        version = "mitgeliefert"
        sha = ""
        confirmed = False
        pdf_name = entry.original_file or ""
        try:
            definition = TemplateDefinition.model_validate_json(mapping_path.read_text("utf-8"))
            version, sha, confirmed = definition.version, definition.sha256, definition.mapping_confirmed
            pdf_name = entry.original_file or definition.pdf_file
        except (OSError, ValidationError, ValueError):
            pass
        if not pdf_name:
            return None
        return TemplateVersionInfo(
            catalog_id=entry.id,
            version=version,
            pdf_path=self.paths.original_templates_dir / pdf_name,
            mapping_path=mapping_path,
            sha256=sha,
            imported_at=None,
            confirmed=confirmed,
            source="mitgeliefert",
        )

    def _imported(self, catalog_id: str) -> list[TemplateVersionInfo]:
        base = self._user_dir(catalog_id)
        if not base.is_dir():
            return []
        out: list[TemplateVersionInfo] = []
        for vdir in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
            meta_path = vdir / "meta.json"
            try:
                meta = _VersionMeta.model_validate_json(meta_path.read_text("utf-8"))
            except (OSError, ValidationError, ValueError):
                log.warning("Importierte Vorlagenversion ohne gültige Metadaten übersprungen")
                continue
            mapping_path = vdir / "mapping.json"
            confirmed = meta.confirmed
            try:
                definition = TemplateDefinition.model_validate_json(mapping_path.read_text("utf-8"))
                confirmed = confirmed and definition.mapping_confirmed
            except (OSError, ValidationError, ValueError):
                confirmed = False
            out.append(
                TemplateVersionInfo(
                    catalog_id=catalog_id,
                    version=meta.version,
                    pdf_path=vdir / IMPORTED_PDF_NAME,
                    mapping_path=mapping_path,
                    sha256=meta.sha256,
                    imported_at=meta.imported_at,
                    confirmed=confirmed,
                    source="importiert",
                )
            )
        out.sort(key=lambda v: (v.imported_at or datetime.min.replace(tzinfo=UTC), v.version), reverse=True)
        return out

    def list_versions(self, catalog_id: str) -> list[TemplateVersionInfo]:
        """Alle Versionen: importierte (neueste zuerst), danach die mitgelieferte."""
        entry = self.entry(catalog_id)
        versions = self._imported(catalog_id)
        shipped = self._shipped(entry)
        if shipped is not None:
            versions.append(shipped)
        return versions

    def _active(self, catalog_id: str) -> TemplateVersionInfo | None:
        entry = self.entry(catalog_id)
        imported = self._imported(catalog_id)
        confirmed = [v for v in imported if v.confirmed and v.pdf_path.is_file()]
        if confirmed:
            return confirmed[0]
        shipped = self._shipped(entry)
        if shipped is not None and shipped.pdf_path.is_file():
            return shipped
        drafts = [v for v in imported if v.pdf_path.is_file()]
        if drafts:
            return drafts[0]
        return shipped

    def _version(self, catalog_id: str, version: str | None) -> TemplateVersionInfo:
        if version is None:
            info = self._active(catalog_id)
            if info is None:
                raise TemplateNotAvailableError(MISSING_ORIGINAL_REASON)
            return info
        for v in self.list_versions(catalog_id):
            if v.version == version:
                return v
        raise TemplateNotAvailableError("Diese Vorlagenversion ist nicht vorhanden.")

    # -- Status -------------------------------------------------------------------
    def verify(self, catalog_id: str) -> TemplateCheckReport:
        """Status einer Katalogvorlage neu prüfen (Ergebnis wird zwischengespeichert)."""
        report = self._verify(catalog_id)
        self._cache[catalog_id] = report
        return report

    def _verify(self, catalog_id: str) -> TemplateCheckReport:
        entry = self.entry(catalog_id)
        info = self._active(catalog_id)
        if info is None or not info.pdf_path.is_file():
            return TemplateCheckReport(
                catalog_id=catalog_id,
                status=TemplateStatus.MISSING_ORIGINAL,
                reasons=[MISSING_ORIGINAL_REASON],
                version=info.version if info else None,
                pdf_path=None,
                source=info.source if info else None,
                notes=self._letter_notes(entry),
            )
        try:
            definition = TemplateDefinition.model_validate_json(info.mapping_path.read_text("utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            log.warning("Feldzuordnung nicht lesbar (%s)", type(exc).__name__)
            return TemplateCheckReport(
                catalog_id=catalog_id,
                status=TemplateStatus.ERROR,
                reasons=["Die Feldzuordnung ist fehlerhaft und kann nicht gelesen werden."],
                version=info.version,
                pdf_path=info.pdf_path,
                source=info.source,
            )
        errors, perms, _ = validate_definition_against_pdf(definition, info.pdf_path)
        if definition.catalog_id != entry.id:
            errors.insert(0, "Die Feldzuordnung gehört zu einer anderen Katalogvorlage.")
        notes: list[str] = []
        newer = [v for v in self._imported(catalog_id) if not v.confirmed and v.version != info.version]
        if newer and info.confirmed:
            notes.append(f"Importierte Version {newer[0].version} wartet auf Prüfung im Vorlagen-Studio.")
        if errors:
            status, reasons = TemplateStatus.ERROR, errors
        elif not (info.confirmed and definition.mapping_confirmed):
            status, reasons = TemplateStatus.NEEDS_REVIEW, [NOT_CONFIRMED_REASON]
        else:
            status, reasons = TemplateStatus.ACTIVE, []
        return TemplateCheckReport(
            catalog_id=catalog_id,
            status=status,
            reasons=reasons,
            version=info.version,
            pdf_path=info.pdf_path,
            permissions=perms,
            notes=notes,
            source=info.source,
        )

    def _letter_notes(self, entry: CatalogEntry) -> list[str]:
        letter = getattr(entry, "letter_file", None)
        if letter and (self.paths.config_dir / "letter_templates" / letter).is_file():
            return ["Ein Kundenschreiben nach DIN 5008 ist auch ohne Originalvorlage verfügbar."]
        return []

    def _report(self, catalog_id: str, refresh: bool = False) -> TemplateCheckReport:
        if refresh or catalog_id not in self._cache:
            return self.verify(catalog_id)
        return self._cache[catalog_id]

    def list_items(self, refresh: bool = False) -> list[CatalogItem]:
        """Alle Katalogeinträge mit Status, sortiert nach Nummer."""
        if refresh:
            self._invalidate()
        items: list[CatalogItem] = []
        for entry in sorted(self.catalog().entries, key=lambda e: e.number):
            report = self._report(entry.id)
            updated: datetime | None = None
            if report.pdf_path is not None:
                imported = next(
                    (
                        v
                        for v in self._imported(entry.id)
                        if v.version == report.version and v.source == report.source
                    ),
                    None,
                )
                updated = imported.imported_at if imported else _mtime(report.pdf_path)
            items.append(
                CatalogItem(
                    entry=entry,
                    status=report.status,
                    status_reason=report.reasons[0] if report.reasons else ACTIVE_REASON,
                    version=report.version if report.pdf_path is not None else None,
                    updated_at=updated,
                    source=report.source if report.pdf_path is not None else None,
                )
            )
        return items

    def is_active(self, catalog_id: str) -> bool:
        return self._report(catalog_id).status == TemplateStatus.ACTIVE

    def get_definition(self, catalog_id: str) -> TemplateDefinition:
        """Zuordnung der aktiven Version (unabhängig vom Status – vor dem Befüllen ``is_active`` prüfen)."""
        info = self._version(catalog_id, None)
        if not info.pdf_path.is_file():
            raise TemplateNotAvailableError(MISSING_ORIGINAL_REASON)
        try:
            return TemplateDefinition.model_validate_json(info.mapping_path.read_text("utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise TemplateRegistryError(
                "Die Feldzuordnung ist fehlerhaft und kann nicht gelesen werden."
            ) from exc

    def get_pdf_path(self, catalog_id: str) -> Path:
        """Pfad der Original-PDF der aktiven Version."""
        info = self._version(catalog_id, None)
        if not info.pdf_path.is_file():
            raise TemplateNotAvailableError(MISSING_ORIGINAL_REASON)
        return info.pdf_path

    def find_by_template_id(self, template_id: str) -> tuple[str, TemplateVersionInfo] | None:
        """Version zu einer ``template_id`` suchen (z. B. beim Wiederaufnehmen eines Vorgangs)."""
        for entry in self.catalog().entries:
            for v in self.list_versions(entry.id):
                try:
                    definition = TemplateDefinition.model_validate_json(v.mapping_path.read_text("utf-8"))
                except (OSError, ValidationError, ValueError):
                    continue
                if definition.template_id == template_id:
                    return entry.id, v
        return None

    # -- Import -------------------------------------------------------------------
    def _new_version_name(self, catalog_id: str, version: str | None) -> str:
        base = _check_version(version) if version else date.today().isoformat()
        candidate = base
        counter = 2
        while (self._user_dir(catalog_id) / candidate).exists():
            if version:
                raise TemplateRegistryError(
                    "Diese Version ist bereits importiert. Bitte eine andere Bezeichnung wählen."
                )
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def import_original(self, catalog_id: str, pdf_path: Path, version: str | None = None) -> TemplateDraft:
        """Original-PDF übernehmen (Kopie, schreibgeschützt) und Erst-Zuordnung erzeugen."""
        entry = self.entry(catalog_id)
        source = Path(pdf_path)
        if not source.is_file():
            raise TemplateRegistryError("Die gewählte Datei wurde nicht gefunden.")
        if source.stat().st_size > MAX_PDF_BYTES:
            raise TemplateRegistryError("Die PDF ist zu groß (mehr als 50 MB).")
        data = source.read_bytes()  # Quelle wird nur gelesen
        _check_pdf_bytes(data)
        name = self._new_version_name(catalog_id, version)
        vdir = self._user_dir(catalog_id) / name
        vdir.mkdir(parents=True, exist_ok=False)
        try:
            target = vdir / IMPORTED_PDF_NAME
            _atomic_write(target, data, read_only=True)
            inspection = inspect_pdf_form(target)
            draft = auto_mapping(entry, target, inspection, name)
            assert draft.definition is not None
            _write_json(vdir / "mapping.json", draft.definition)
            meta = _VersionMeta(
                catalog_id=catalog_id,
                version=name,
                sha256=_sha256_bytes(data),
                imported_at=utcnow(),
                confirmed=False,
            )
            _write_json(vdir / "meta.json", meta)
        except BaseException:
            _remove_tree(vdir)
            raise
        self._invalidate(catalog_id)
        log.info(
            "Vorlage importiert: katalog=%s version=%s felder=%d zugeordnet=%d",
            catalog_id,
            name,
            len(inspection.fields),
            len(draft.definition.fields),
        )
        return draft

    def save_mapping(self, catalog_id: str, definition: TemplateDefinition, confirmed: bool) -> None:
        """Zuordnung einer importierten Version speichern (optional bestätigen).

        Für die mitgelieferte Version wird dabei eine importierte Kopie angelegt
        (die Programmdateien bleiben unverändert).
        """
        entry = self.entry(catalog_id)
        if definition.catalog_id != catalog_id:
            raise TemplateRegistryError("Die Feldzuordnung gehört zu einer anderen Katalogvorlage.")
        version = _check_version(definition.version)
        vdir = self._user_dir(catalog_id) / version
        meta_path = vdir / "meta.json"
        if not meta_path.is_file():
            shipped = self._shipped(entry)
            if shipped is None or shipped.version != version or not shipped.pdf_path.is_file():
                raise TemplateRegistryError("Zu dieser Version gibt es keine importierte Originalvorlage.")
            data = shipped.pdf_path.read_bytes()
            if _sha256_bytes(data) != definition.sha256:
                raise TemplateRegistryError("Die Feldzuordnung passt nicht zur Originalvorlage (Prüfsumme).")
            vdir.mkdir(parents=True, exist_ok=True)
            target = vdir / IMPORTED_PDF_NAME
            if not (target.is_file() and _sha256_file(target) == definition.sha256):
                if target.exists():
                    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
                _atomic_write(target, data, read_only=True)
            meta = _VersionMeta(
                catalog_id=catalog_id, version=version, sha256=definition.sha256, imported_at=utcnow()
            )
        else:
            try:
                meta = _VersionMeta.model_validate_json(meta_path.read_text("utf-8"))
            except (OSError, ValidationError, ValueError) as exc:
                raise TemplateRegistryError("Die Versionsdaten sind beschädigt.") from exc
        pdf = vdir / IMPORTED_PDF_NAME
        if not pdf.is_file() or _sha256_file(pdf) != definition.sha256 or meta.sha256 != definition.sha256:
            raise TemplateRegistryError("Die Feldzuordnung passt nicht zur Originalvorlage (Prüfsumme).")
        try:
            updated = TemplateDefinition.model_validate(
                {
                    **definition.model_dump(by_alias=True),
                    "mapping_confirmed": bool(confirmed),
                    "pdf_file": IMPORTED_PDF_NAME,
                    "version": version,
                }
            )
        except ValidationError as exc:
            raise TemplateRegistryError("Die Feldzuordnung ist ungültig.") from exc
        _write_json(vdir / "mapping.json", updated)
        meta = meta.model_copy(
            update={"confirmed": bool(confirmed), "confirmed_at": utcnow() if confirmed else None}
        )
        _write_json(meta_path, meta)
        self._invalidate(catalog_id)
        log.info(
            "Feldzuordnung gespeichert: katalog=%s version=%s bestätigt=%s", catalog_id, version, confirmed
        )

    # -- Pakete -------------------------------------------------------------------
    def export_package(self, catalog_id: str, dest_zip: Path, version: str | None = None) -> Path:
        """Version als ZIP-Paket (manifest.json, mapping.json, original.pdf) speichern."""
        info = self._version(catalog_id, version)
        if not info.pdf_path.is_file():
            raise TemplateNotAvailableError(MISSING_ORIGINAL_REASON)
        data = info.pdf_path.read_bytes()
        try:
            definition = TemplateDefinition.model_validate_json(info.mapping_path.read_text("utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise TemplateRegistryError(
                "Die Feldzuordnung ist fehlerhaft und kann nicht gelesen werden."
            ) from exc
        sha = _sha256_bytes(data)
        if sha != definition.sha256:
            raise TemplateRegistryError("Die Originalvorlage passt nicht zur Feldzuordnung (Prüfsumme).")
        mapping = definition.model_copy(update={"pdf_file": IMPORTED_PDF_NAME})
        manifest = {
            "format": PACKAGE_FORMAT,
            "catalog_id": catalog_id,
            "version": definition.version,
            "sha256": sha,
            "created": utcnow().isoformat(),
            "mapping_confirmed": definition.mapping_confirmed,
        }
        dest = Path(dest_zip)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".zip", dir=dest.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                zf.writestr(
                    "mapping.json",
                    json.dumps(mapping.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2),
                )
                zf.writestr("original.pdf", data)
            os.replace(tmp, dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        log.info("Vorlagenpaket exportiert: katalog=%s version=%s", catalog_id, definition.version)
        return dest

    def import_package(self, zip_path: Path) -> TemplateVersionInfo:
        """Vorlagenpaket prüfen und als (unbestätigte) importierte Version ablegen."""
        members = _read_package(Path(zip_path))
        try:
            manifest = json.loads(members["manifest.json"].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise TemplateRegistryError("Das Paket enthält kein gültiges Manifest.") from exc
        if not isinstance(manifest, dict):
            raise TemplateRegistryError("Das Paket enthält kein gültiges Manifest.")
        catalog_id = str(manifest.get("catalog_id", ""))
        sha = str(manifest.get("sha256", "")).lower()
        version = _check_version(str(manifest.get("version", "")))
        self.entry(catalog_id)  # unbekannte Katalog-ID -> Fehler
        if not _SHA_RE.fullmatch(sha):
            raise TemplateRegistryError("Das Manifest enthält keine gültige Prüfsumme.")
        pdf = members["original.pdf"]
        if _sha256_bytes(pdf) != sha:
            raise TemplateRegistryError("Die Prüfsumme der Originalvorlage im Paket stimmt nicht.")
        try:
            definition = TemplateDefinition.model_validate_json(members["mapping.json"].decode("utf-8"))
        except (UnicodeDecodeError, ValidationError, ValueError) as exc:
            raise TemplateRegistryError("Die Feldzuordnung im Paket ist ungültig.") from exc
        if definition.catalog_id != catalog_id or definition.version != version or definition.sha256 != sha:
            raise TemplateRegistryError("Manifest und Feldzuordnung im Paket passen nicht zusammen.")
        _check_pdf_bytes(pdf)

        definition = definition.model_copy(update={"pdf_file": IMPORTED_PDF_NAME, "mapping_confirmed": False})
        vdir = self._user_dir(catalog_id) / version
        if vdir.exists():
            existing = next((v for v in self._imported(catalog_id) if v.version == version), None)
            if existing is not None and existing.sha256 == sha and existing.pdf_path.is_file():
                return existing
            raise TemplateRegistryError("Diese Version ist bereits mit anderem Inhalt vorhanden.")
        vdir.mkdir(parents=True)
        try:
            _atomic_write(vdir / IMPORTED_PDF_NAME, pdf, read_only=True)
            _write_json(vdir / "mapping.json", definition)
            meta = _VersionMeta(
                catalog_id=catalog_id,
                version=version,
                sha256=sha,
                imported_at=utcnow(),
                confirmed=False,
                from_package=True,
            )
            _write_json(vdir / "meta.json", meta)
        except BaseException:
            _remove_tree(vdir)
            raise
        self._invalidate(catalog_id)
        log.info("Vorlagenpaket importiert: katalog=%s version=%s", catalog_id, version)
        return TemplateVersionInfo(
            catalog_id=catalog_id,
            version=version,
            pdf_path=vdir / IMPORTED_PDF_NAME,
            mapping_path=vdir / "mapping.json",
            sha256=sha,
            imported_at=meta.imported_at,
            confirmed=False,
            source="importiert",
        )


def _remove_tree(path: Path) -> None:
    """Ordner samt schreibgeschützter Dateien entfernen (Aufräumen nach Fehlern)."""
    if not path.exists():
        return
    for p in path.rglob("*"):
        with contextlib.suppress(OSError):
            p.chmod(stat.S_IRWXU if p.is_dir() else stat.S_IRUSR | stat.S_IWUSR)
    shutil.rmtree(path, ignore_errors=True)


def _safe_member_name(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name:
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    parts = name.split("/")
    return all(p not in ("", ".", "..") for p in parts)


def _zip_read_errors() -> tuple[type[BaseException], ...]:
    """Ausnahmen, die beim Lesen eines beschädigten ZIP-Archivs auftreten können."""
    errors: list[type[BaseException]] = [
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        OSError,  # u. a. bz2: "Invalid data stream"
        RuntimeError,
        NotImplementedError,  # unbekanntes Kompressionsverfahren
        zlib.error,  # zerstörter Deflate-Datenstrom
        EOFError,  # abgeschnittener Datenstrom
        ValueError,
    ]
    with contextlib.suppress(ImportError):
        import lzma

        errors.append(lzma.LZMAError)
    return tuple(errors)


_ZIP_READ_ERRORS = _zip_read_errors()


def _read_package(zip_path: Path) -> dict[str, bytes]:
    if not zip_path.is_file() or not zipfile.is_zipfile(zip_path):
        raise TemplateRegistryError("Die Datei ist kein gültiges Vorlagenpaket (ZIP).")
    out: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > 16:
                raise TemplateRegistryError("Das Paket enthält zu viele Dateien.")
            for info in infos:
                name = info.filename
                if not _safe_member_name(name):
                    raise TemplateRegistryError(
                        "Das Paket enthält unzulässige Dateipfade und wurde abgelehnt."
                    )
                if info.is_dir():
                    raise TemplateRegistryError("Das Paket enthält unerwartete Ordner.")
                if name not in PACKAGE_MEMBERS:
                    raise TemplateRegistryError(f"Das Paket enthält eine unerwartete Datei: {name}")
                if name in out:
                    raise TemplateRegistryError("Das Paket enthält doppelte Einträge.")
                if info.flag_bits & 0x1:
                    raise TemplateRegistryError("Verschlüsselte Pakete werden nicht unterstützt.")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise TemplateRegistryError("Eine Datei im Paket ist zu groß (mehr als 50 MB).")
                with zf.open(info) as fh:
                    data = fh.read(MAX_MEMBER_BYTES + 1)
                if len(data) > MAX_MEMBER_BYTES:
                    raise TemplateRegistryError("Eine Datei im Paket ist zu groß (mehr als 50 MB).")
                out[name] = data
    except TemplateRegistryError:
        raise
    except _ZIP_READ_ERRORS as exc:
        raise TemplateRegistryError("Das Vorlagenpaket ist beschädigt.") from exc
    missing = [m for m in PACKAGE_MEMBERS if m not in out]
    if missing:
        raise TemplateRegistryError(f"Im Paket fehlt: {', '.join(missing)}")
    return out
