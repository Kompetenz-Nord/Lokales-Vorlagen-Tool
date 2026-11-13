"""Befüllung der ORIGINAL-Vorlage auf einer Kopie.

Ablauf (``TemplateFiller.fill``)
--------------------------------
1. SHA-256 der Originalvorlage prüfen (sonst ``TemplateIntegrityError``).
2. Berechtigungen, vorhandene Signaturen und XFA vorab prüfen – bevor eine Datei
   geschrieben wird.
3. Kopie anlegen und öffnen; Werte setzen:
   * Textfelder nativ (``widget.field_value`` + ``widget.update()``),
   * Beträge: Darstellung ``1.234,56``, Feldwert ``1234.56`` (Acrobat-Zahlformat),
   * Checkboxen/Optionsgruppen nur über ``/V`` und ``/AS`` mit dem im PDF
     vorhandenen Ein-Zustand – die Original-Darstellung (Kreuz) bleibt erhalten,
   * Unterschriftsfelder und Unterschriftsdaten werden nie berührt.
4. Inkrementell speichern, Verschlüsselung und Rechte unverändert
   (``PDF_ENCRYPT_KEEP``): Die Ausgabe beginnt byte-identisch mit dem Original.
5. Optional „Formular fixieren“ – nur wenn die PDF-Rechte Änderungen erlauben.

Die Originaldatei wird nur gelesen und bleibt byte-identisch.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

from insurance_app.pdf.fonts import font_file
from insurance_app.pdf.inspector import (
    PdfPermissions,
    field_dict_xref,
    inherited_key,
    inspect_document,
    pdf_name,
    read_permissions,
    widget_states,
)
from insurance_app.pdf.overlay import OverlayNotPermittedError, apply_overlay
from insurance_app.templates.resolver import STATE_KINDS, FillPlan, FillValue
from insurance_app.templates.schema import FillPolicy, MappingKind, TemplateDefinition

__all__ = [
    "FLATTEN_NOT_PERMITTED_MESSAGE",
    "FillResult",
    "FlattenNotPermittedError",
    "FormPermissionError",
    "MappingError",
    "PdfFillError",
    "TemplateFiller",
    "TemplateIntegrityError",
    "sha256_file",
]

log = logging.getLogger(__name__)

FLATTEN_NOT_PERMITTED_MESSAGE = (
    "Diese Vorlage erlaubt laut PDF-Berechtigungen kein Fixieren der Formularfelder. "
    "Die PDF bleibt ausfüllbar."
)


class PdfFillError(Exception):
    """Basisklasse: Befüllung nicht möglich. ``user_message`` ist für die Oberfläche gedacht."""

    default_message = "Die PDF konnte nicht befüllt werden."

    def __init__(self, message: str | None = None) -> None:
        msg = message or self.default_message
        super().__init__(msg)
        self.user_message = msg


class TemplateIntegrityError(PdfFillError):
    default_message = (
        "Die Originalvorlage ist verändert oder beschädigt (Prüfsumme stimmt nicht). "
        "Bitte die Vorlage im Vorlagen-Studio neu importieren."
    )


class FormPermissionError(PdfFillError):
    default_message = "Diese Vorlage erlaubt laut PDF-Berechtigungen kein Ausfüllen der Formularfelder."


class FlattenNotPermittedError(PdfFillError):
    default_message = FLATTEN_NOT_PERMITTED_MESSAGE


class MappingError(PdfFillError):
    default_message = "Die Feldzuordnung passt nicht zur Vorlage. Bitte im Vorlagen-Studio prüfen."


class FillResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_path: Path
    filled_fields: list[str] = Field(default_factory=list)  # geschriebene PDF-Felder (inkl. Overlay)
    skipped_fields: list[str] = Field(default_factory=list)  # zugeordnete Felder, die leer blieben
    used_overlay: bool = False
    incremental: bool = True
    encryption_kept: bool = True
    warnings: list[str] = Field(default_factory=list)
    flattened: bool = False
    sha256: str = ""  # Prüfsumme der Ausgabe
    duration_s: float = 0.0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_encryption(a: PdfPermissions, b: PdfPermissions) -> bool:
    return a.encrypted == b.encrypted and a.raw == b.raw and a.encryption_method == b.encryption_method


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - z. B. Datei gesperrt (Windows)
        log.warning("Temporäre Datei konnte nicht gelöscht werden")


_DA_FONT = re.compile(r"(/[^\s/]+)\s+[\d.]+\s+Tf")


def _force_font_size(doc: pymupdf.Document, widget: pymupdf.Widget, size: float) -> None:
    """Schriftgröße direkt in der DA des Widgets setzen.

    PyMuPDF übernimmt eine geänderte Größe bei Kind-Widgets hierarchischer Felder
    sonst nicht in die Darstellung.
    """
    kind, da = inherited_key(doc, widget.xref, "DA")
    if kind == "string" and _DA_FONT.search(da):
        new_da = _DA_FONT.sub(lambda m: f"{m.group(1)} {size:g} Tf", da, count=1)
    else:
        new_da = f"/Helv {size:g} Tf 0 g"
    doc.xref_set_key(widget.xref, "DA", pymupdf.get_pdf_str(new_da))


class TemplateFiller:
    """Befüllt Originalvorlagen gemäß ``FillPlan`` (siehe Modul-Dokumentation)."""

    def __init__(self, fonts_dir: Path) -> None:
        self.fonts_dir = Path(fonts_dir)

    # ------------------------------------------------------------------
    def fill(
        self,
        template: TemplateDefinition,
        template_pdf: Path,
        plan: FillPlan,
        out_path: Path,
        flatten: bool = False,
    ) -> FillResult:
        started = time.monotonic()
        template_pdf = Path(template_pdf)
        out_path = Path(out_path)
        if not template_pdf.is_file():
            raise TemplateIntegrityError("Die Originalvorlage wurde nicht gefunden.")
        original = template_pdf.read_bytes()
        if hashlib.sha256(original).hexdigest() != template.sha256:
            raise TemplateIntegrityError()
        if plan.template_id != template.template_id:
            raise MappingError("Der Befüllungsplan gehört zu einer anderen Vorlage.")
        if out_path.exists() and out_path.resolve() == template_pdf.resolve():
            raise MappingError("Die Ausgabe darf die Originalvorlage nicht überschreiben.")

        self._check_plan_against_template(template, plan)
        uses_overlay = any(self._overlay_rect(template, v) for v in plan.values)
        uses_fields = any(not self._overlay_rect(template, v) for v in plan.values)

        # --- Vorabprüfung am Original (noch keine Datei geschrieben) ---------
        try:
            probe = pymupdf.open(stream=original, filetype="pdf")
        except Exception as exc:
            raise TemplateIntegrityError("Die Originalvorlage kann nicht gelesen werden.") from exc
        try:
            if probe.needs_pass:
                raise FormPermissionError("Die Vorlage ist mit einem Passwort geschützt.")
            inspection = inspect_document(probe)
        finally:
            probe.close()
        perms = inspection.permissions
        if inspection.is_xfa:
            raise MappingError("XFA-Formulare werden nicht unterstützt.")
        if inspection.signed_signature_fields:
            raise FormPermissionError(
                "Die Vorlage enthält bereits eine digitale Unterschrift und darf nicht verändert werden."
            )
        if uses_fields and not perms.can_fill_forms:
            raise FormPermissionError()
        if uses_overlay and not perms.can_flatten:
            raise FormPermissionError(
                "Diese Vorlage erlaubt laut PDF-Berechtigungen keine Änderungen am Seiteninhalt. "
                "Text kann nicht per Koordinaten eingesetzt werden."
            )
        if flatten and not perms.can_flatten:
            raise FlattenNotPermittedError()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        work = out_path.with_name(out_path.name + ".part")
        flat_tmp = out_path.with_name(out_path.name + ".flat.part")
        warnings: list[str] = []
        try:
            work.write_bytes(original)
            filled, used_overlay, incremental = self._apply(template, plan, work, warnings)
            flattened = False
            if flatten:
                self._flatten(work, flat_tmp)
                os.replace(flat_tmp, work)
                incremental = False
                flattened = True
            os.replace(work, out_path)
        except Exception:
            _remove_quietly(work)
            _remove_quietly(flat_tmp)
            raise

        # Original unverändert?
        if sha256_file(template_pdf) != template.sha256:  # pragma: no cover - reine Absicherung
            raise TemplateIntegrityError("Die Originalvorlage wurde während der Befüllung verändert.")

        out_doc = pymupdf.open(out_path)
        try:
            out_perms = read_permissions(out_doc)
        finally:
            out_doc.close()
        mapped = [m.pdf_field for m in template.fields]
        skipped = list(dict.fromkeys(f for f in mapped if f not in filled))
        result = FillResult(
            output_path=out_path,
            filled_fields=filled,
            skipped_fields=skipped,
            used_overlay=used_overlay,
            incremental=incremental,
            encryption_kept=_same_encryption(perms, out_perms),
            warnings=warnings,
            flattened=flattened,
            sha256=sha256_file(out_path),
            duration_s=round(time.monotonic() - started, 3),
        )
        log.info(
            "Vorlage befüllt: template=%s felder=%d overlay=%s inkrementell=%s fixiert=%s dauer=%.2fs",
            template.template_id,
            len(filled),
            used_overlay,
            incremental,
            flattened,
            result.duration_s,
        )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _overlay_rect(template: TemplateDefinition, value: FillValue) -> list[float] | None:
        if value.overlay_rect:
            return value.overlay_rect
        mapping = template.mapping(value.pdf_field)
        return mapping.overlay_rect if mapping and mapping.overlay_rect else None

    @staticmethod
    def _check_plan_against_template(template: TemplateDefinition, plan: FillPlan) -> None:
        seen: dict[str, FillValue] = {}
        for value in plan.values:
            mappings = [m for m in template.fields if m.pdf_field == value.pdf_field]
            if not mappings:
                raise MappingError(f"Das Feld „{value.pdf_field}“ ist in der Feldzuordnung nicht vorgesehen.")
            if value.kind in (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE) or any(
                m.kind in (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE)
                or m.fill_policy == FillPolicy.NEVER
                for m in mappings
            ):
                raise MappingError("Unterschriftsfelder und Unterschriftsdaten werden nie befüllt.")
            if value.kind in STATE_KINDS:
                if not value.state:
                    raise MappingError(f"Für das Feld „{value.pdf_field}“ fehlt der Ankreuz-Zustand.")
            elif value.text is None:
                raise MappingError(f"Für das Feld „{value.pdf_field}“ fehlt der Text.")
            previous = seen.get(value.pdf_field)
            if previous is not None and (previous.stored_value, previous.text) != (
                value.stored_value,
                value.text,
            ):
                raise MappingError(f"Für das Feld „{value.pdf_field}“ gibt es widersprüchliche Werte.")
            seen[value.pdf_field] = value

    def _apply(
        self,
        template: TemplateDefinition,
        plan: FillPlan,
        work: Path,
        warnings: list[str],
    ) -> tuple[list[str], bool, bool]:
        """Werte in ``work`` schreiben und inkrementell speichern -> (Felder, Overlay?, inkrementell?)."""
        doc = pymupdf.open(work)
        try:
            field_values: dict[str, FillValue] = {}
            overlay_values: list[FillValue] = []
            for value in plan.values:
                if self._overlay_rect(template, value):
                    overlay_values.append(value)
                else:
                    field_values[value.pdf_field] = value

            if not field_values and not overlay_values:
                # Nichts einzutragen: ``work`` ist bereits die bytegenaue Kopie des Originals.
                # Ein Speichern würde nur einen leeren Änderungsabschnitt anhängen.
                return [], False, True

            filled: list[str] = []
            found: set[str] = set()
            state_hits: dict[str, bool] = {}
            for page in doc:
                widgets = list(page.widgets() or [])
                for widget in widgets:
                    name = widget.field_name
                    value = field_values.get(name)
                    if value is None:
                        continue
                    found.add(name)
                    if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                        raise MappingError("Unterschriftsfelder werden nie befüllt.")
                    if value.kind in STATE_KINDS:
                        hit = self._set_state(doc, widget, value)
                        state_hits[name] = state_hits.get(name, False) or hit
                    else:
                        self._set_text(doc, widget, value, warnings)
                    if name not in filled:
                        filled.append(name)

            missing = [n for n in field_values if n not in found]
            if missing:
                raise MappingError(f"Das Feld „{missing[0]}“ fehlt in der Vorlage.")
            for name, hit in state_hits.items():
                state = field_values[name].state
                if state != "Off" and not hit:
                    raise MappingError(
                        f"Die Vorlage enthält für das Feld „{name}“ keinen Ein-Zustand „{state}“."
                    )

            used_overlay = False
            if overlay_values:
                font = font_file(self.fonts_dir)
                for value in overlay_values:
                    mapping = template.mapping(value.pdf_field)
                    rect = self._overlay_rect(template, value)
                    assert rect is not None
                    page_no = value.page or (mapping.page if mapping else None) or 1
                    base = (mapping.font_size if mapping and mapping.font_size else None) or 10.0
                    if value.font_size:
                        base = min(base, value.font_size)
                    try:
                        ok = apply_overlay(
                            doc,
                            page_no,
                            rect,
                            value.text or "",
                            font,
                            base_size=base,
                            align=value.align or (mapping.align if mapping else None) or "left",
                        )
                    except OverlayNotPermittedError as exc:
                        raise FormPermissionError(exc.user_message) from exc
                    if not ok:
                        raise MappingError(
                            f"Der Text für „{value.pdf_field}“ passt nicht in den vorgesehenen Bereich."
                        )
                    used_overlay = True
                    filled.append(value.pdf_field)

            incremental = bool(doc.can_save_incrementally())
            if incremental:
                doc.save(work, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
            else:
                warnings.append(
                    "Die Vorlage ließ sich nicht inkrementell speichern; sie wurde vollständig neu geschrieben."
                )
                tmp = work.with_name(work.name + ".full")
                doc.save(tmp, encryption=pymupdf.PDF_ENCRYPT_KEEP)
                doc.close()
                os.replace(tmp, work)
                return filled, used_overlay, False
            return filled, used_overlay, incremental
        finally:
            if not doc.is_closed:
                doc.close()

    @staticmethod
    def _set_text(
        doc: pymupdf.Document, widget: pymupdf.Widget, value: FillValue, warnings: list[str]
    ) -> None:
        name = widget.field_name
        if widget.field_type not in (pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.PDF_WIDGET_TYPE_COMBOBOX):
            raise MappingError(f"Das Feld „{name}“ ist kein Textfeld.")
        text = value.text or ""
        if widget.text_maxlen and len(text) > int(widget.text_maxlen):
            raise MappingError(f"Der Text für „{name}“ ist länger als im Feld erlaubt.")
        if int(widget.field_flags or 0) & 1:
            warnings.append(f"Das Feld „{name}“ ist als schreibgeschützt markiert und wurde dennoch befüllt.")
        size = float(value.font_size) if value.font_size else (None if widget.text_fontsize else 10.0)
        if size:
            _force_font_size(doc, widget, size)
            widget.text_fontsize = size
        widget.field_value = text
        widget.update()
        stored = value.stored_value or ""
        fx = field_dict_xref(doc, widget.xref)
        doc.xref_set_key(fx, "V", pymupdf.get_pdf_str(stored))
        if fx != widget.xref and doc.xref_get_key(widget.xref, "V")[0] != "null":
            doc.xref_set_key(widget.xref, "V", "null")

    @staticmethod
    def _set_state(doc: pymupdf.Document, widget: pymupdf.Widget, value: FillValue) -> bool:
        """``/AS`` und ``/V`` setzen. -> True, wenn dieses Widget den Ein-Zustand trägt."""
        name = widget.field_name
        if widget.field_type not in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
            raise MappingError(f"Das Feld „{name}“ ist kein Ankreuzfeld.")
        state = value.state or "Off"
        states = widget_states(doc, widget.xref)
        hit = state != "Off" and state in states
        doc.xref_set_key(widget.xref, "AS", pdf_name(state) if hit else "/Off")
        fx = field_dict_xref(doc, widget.xref)
        doc.xref_set_key(fx, "V", pdf_name(state))
        return hit

    @staticmethod
    def _flatten(work: Path, target: Path) -> None:
        doc = pymupdf.open(work)
        try:
            doc.bake(annots=False, widgets=True)
            doc.save(target, garbage=1, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        finally:
            doc.close()
