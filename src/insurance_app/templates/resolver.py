"""Werteauflösung: aus freigegebenen Feldwerten einen Befüllungsplan (``FillPlan``) erzeugen.

Grundregeln
-----------
* Ins PDF gelangen nur Werte mit Status ``CONFIRMED`` oder ``MANUAL``.
  ``NOT_APPLICABLE`` bedeutet „bewusst leer“ und erfüllt eine Pflichtangabe.
* Bedingungen (``conditions``, UND-verknüpft) werden auf freigegebenen,
  normalisierten Werten ausgewertet. Ist eine Bedingung noch offen und das Feld
  Pflicht, entsteht genau ein blockierender Punkt je Bedingungsfeld.
* Unterschriften und Unterschriftsdaten werden nie befüllt.
* Rechtlich relevante Felder brauchen eine ausdrückliche, freigegebene
  Entscheidung – auch für „Nein“ (Kästchen bleibt leer).
* Checklisten-Meldungen enthalten nur Beschriftungen, nie personenbezogene Werte.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from insurance_app.core.fields import FIELD_REGISTRY, FieldDef, FieldType
from insurance_app.core.models import FieldStatus, FieldValue, Severity, ValidationMessage
from insurance_app.core.textutil import format_date_de, normalize_ws, parse_german_date, parse_month
from insurance_app.pdf.fonts import fit_font_size, fit_font_size_multiline, unsupported_chars
from insurance_app.pdf.inspector import FormInspection, PdfFieldInfo, format_af_number
from insurance_app.templates.schema import (
    COMPOSE_PLACEHOLDER,
    Condition,
    FieldMapping,
    FillPolicy,
    MappingKind,
    TemplateDefinition,
)

__all__ = [
    "DEFAULT_CHECKBOX_ON_STATE",
    "DEFAULT_FONT_SIZE",
    "ChecklistItem",
    "FillPlan",
    "FillValue",
    "format_output_text",
    "resolve_fill_values",
]

DEFAULT_CHECKBOX_ON_STATE = "Ja"
DEFAULT_FONT_SIZE = 10.0  # wenn die Feldschrift auf "automatisch" (0) steht
MIN_FONT_SIZE = 6.0
FIELD_PADDING = 4.0  # 2 pt links + 2 pt rechts (PyMuPDF-Darstellung)

TEXT_KINDS = (MappingKind.TEXT, MappingKind.MULTILINE, MappingKind.STATIC)
VALUE_KINDS = (*TEXT_KINDS, MappingKind.DATE_PART, MappingKind.AMOUNT)
STATE_KINDS = (MappingKind.CHECKBOX, MappingKind.RADIO)
NEVER_KINDS = (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FillValue(_Model):
    """Ein Wert, der in genau ein PDF-Feld geschrieben wird."""

    pdf_field: str
    kind: MappingKind
    text: str | None = None  # Anzeige-Text (Text, Datumsteil, Betrag "1.234,56")
    raw_value: str | None = None  # Betrag: Feldwert /V "1234.56" (Acrobat-Zahlformat)
    state: str | None = None  # Checkbox/Radio: Ein-Zustand oder "Off"
    font_size: float | None = None  # gesetzt, wenn kleiner als die Feldschrift (passend gemacht)
    source_keys: list[str] = Field(default_factory=list)
    page: int | None = None  # 1-basiert (aus der Zuordnung)
    overlay_rect: list[float] | None = None  # nur für Koordinaten-Overlay
    align: str | None = None  # Overlay: "left" | "center" | "right"

    @property
    def display(self) -> str | None:
        """Was im Feld sichtbar sein soll (Text bzw. Zustand)."""
        return self.text if self.kind not in STATE_KINDS else self.state

    @property
    def stored_value(self) -> str | None:
        """Was als Feldwert (/V) im PDF stehen soll."""
        if self.kind in STATE_KINDS:
            return self.state
        return self.raw_value if self.raw_value is not None else self.text


class ChecklistItem(_Model):
    severity: Severity
    code: str
    message: str
    field_key: str | None = None
    pdf_field: str | None = None
    blocking: bool


class FillPlan(_Model):
    template_id: str
    template_version: str
    mode: Literal["preview", "final"] = "preview"
    values: list[FillValue] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """Gibt es blockierende Punkte (Export nicht möglich)?"""
        return any(item.blocking for item in self.checklist)

    @property
    def open_items(self) -> list[ChecklistItem]:
        """Offene Punkte: alles Blockierende sowie Warnungen (ohne reine Hinweise)."""
        return [i for i in self.checklist if i.blocking or i.severity in (Severity.ERROR, Severity.WARNING)]

    @property
    def blocking_items(self) -> list[ChecklistItem]:
        return [i for i in self.checklist if i.blocking]

    def value_for(self, pdf_field: str) -> FillValue | None:
        return next((v for v in self.values if v.pdf_field == pdf_field), None)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


class _Checklist:
    """Sammelt Punkte, dedupliziert und hebt bei Bedarf auf „blockierend“ an."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str | None, str | None, str | None], ChecklistItem] = {}

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        *,
        blocking: bool,
        field_key: str | None = None,
        pdf_field: str | None = None,
        dedup_by_field: bool = True,
    ) -> None:
        if dedup_by_field and field_key:
            key = (code, field_key, None, None)
        else:
            key = (code, field_key, pdf_field, None if pdf_field else message)
        existing = self._items.get(key)
        if existing is None:
            self._items[key] = ChecklistItem(
                severity=severity,
                code=code,
                message=message,
                field_key=field_key,
                pdf_field=pdf_field,
                blocking=blocking,
            )
            return
        if blocking and not existing.blocking:
            self._items[key] = existing.model_copy(
                update={"blocking": True, "severity": Severity.ERROR, "message": message}
            )

    def has_blocking_for(self, field_key: str, exclude_code: str) -> bool:
        return any(
            i.blocking and i.field_key == field_key and i.code != exclude_code for i in self._items.values()
        )

    def items(self) -> list[ChecklistItem]:
        items = list(self._items.values())
        # Bedingungs-Punkte entfallen, wenn dasselbe Feld bereits blockierend gemeldet ist
        items = [
            i
            for i in items
            if not (
                i.code == "CONDITION_UNDECIDED" and i.field_key and self.has_blocking_for(i.field_key, i.code)
            )
        ]
        order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        return sorted(items, key=lambda i: (not i.blocking, order[i.severity]))


def _label(key: str) -> str:
    fdef = FIELD_REGISTRY.get(key)
    return fdef.label if fdef else key


def _released(fv: FieldValue | None) -> bool:
    return fv is not None and fv.status.is_released


def _norm(fv: FieldValue) -> str | None:
    value = fv.normalized_value if fv.normalized_value is not None else fv.value
    if value is None:
        return None
    value = value.strip()
    return value or None


def _group_iban(iban: str) -> str:
    compact = re.sub(r"\s+", "", iban).upper()
    return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))


def format_output_text(fdef: FieldDef | None, fv: FieldValue) -> str | None:
    """Freigegebenen Wert für ein Textfeld formatieren (Datum TT.MM.JJJJ, Betrag 1.234,56 …)."""
    value = _norm(fv)
    if value is None:
        return None
    if fdef is None:
        return value
    dtype = fdef.dtype
    if dtype == FieldType.DATE:
        d = _as_date(value)
        return format_date_de(d) if d else value
    if dtype == FieldType.MONTH:
        ym = _as_month(value)
        return f"{ym[1]:02d}.{ym[0]:04d}" if ym else value
    if dtype == FieldType.AMOUNT:
        try:
            return format_af_number(value, 2, 2)
        except ValueError:
            return value
    if dtype == FieldType.IBAN:
        return _group_iban(value)
    if dtype == FieldType.BOOL:
        return {"true": "ja", "false": "nein"}.get(value, value)
    if dtype == FieldType.CHOICE and fdef.choices:
        return fdef.choices.get(value, value)
    return value


def _as_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return parse_german_date(value)


def _as_month(value: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        return (year, month) if 1 <= month <= 12 else None
    d = _as_date(value)
    if d:
        return d.year, d.month
    return parse_month(value)


def _date_part(value: str, part: str) -> str | None:
    """``YYYY-MM-DD`` oder ``YYYY-MM`` (bzw. lesbare Rohform) -> ``DD``/``MM``/``YYYY``."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or re.search(r"\d{1,2}\.\d{1,2}\.\d{2,4}", value):
        d = _as_date(value)
        if d is None:
            return None
        return {"day": f"{d.day:02d}", "month": f"{d.month:02d}", "year": f"{d.year:04d}"}[part]
    ym = _as_month(value)
    if ym is None:
        return None
    if part == "day":
        return None
    return f"{ym[1]:02d}" if part == "month" else f"{ym[0]:04d}"


def _clean_composed(text: str) -> str:
    text = normalize_ws(text)
    text = re.sub(r"\s+([,;])", r"\1", text)
    text = re.sub(r"([,;])(\s*[,;])+", r"\1", text)
    text = re.sub(r"^[\s,;/\-–]+", "", text)
    text = re.sub(r"[\s,;/\-–]+$", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Auflösung
# ---------------------------------------------------------------------------


class _Resolver:
    def __init__(
        self,
        template: TemplateDefinition,
        contract: dict[str, FieldValue],
        order: dict[str, FieldValue],
        inspection: FormInspection | None,
        mode: str,
    ) -> None:
        self.t = template
        self.contract = contract
        self.order = order
        self.inspection = inspection
        self.mode = mode
        self.final = mode == "final"
        self.check = _Checklist()
        self.values: list[FillValue] = []
        self.used_keys: set[str] = set()

    # -- Feldzugriff --------------------------------------------------------
    def fv(self, key: str) -> FieldValue | None:
        fdef = FIELD_REGISTRY.get(key)
        if fdef is not None and fdef.category.value == "ORDER":
            return self.order.get(key) or self.contract.get(key)
        return self.contract.get(key) or self.order.get(key)

    # -- Bedingungen ----------------------------------------------------------
    def conditions_state(self, conditions: list[Condition]) -> tuple[str, list[str]]:
        """-> ("met" | "unmet" | "undecided", offene Bedingungsfelder)."""
        undecided: list[str] = []
        for cond in conditions:
            fv = self.fv(cond.field)
            if fv is not None and fv.status == FieldStatus.NOT_APPLICABLE:
                if not cond.matches(None):
                    return "unmet", []
                continue
            if not _released(fv):
                undecided.append(cond.field)
                continue
            assert fv is not None
            if not cond.matches(_norm(fv)):
                return "unmet", []
        if undecided:
            return "undecided", undecided
        return "met", []

    # -- Statusmeldungen --------------------------------------------------------
    def report_unreleased(
        self,
        mapping: FieldMapping,
        key: str,
        fv: FieldValue | None,
        *,
        required: bool,
    ) -> None:
        """Meldung für einen nicht freigegebenen Quellwert (dedupliziert je Feld)."""
        status = fv.status if fv is not None else FieldStatus.MISSING
        label = _label(key)
        legal = mapping.legal_relevant
        decision_kind = mapping.kind in STATE_KINDS
        if status == FieldStatus.CONFLICT:
            self.check.add(
                Severity.ERROR,
                "CONFLICT_UNRESOLVED",
                f"{label}: Konflikt nicht gelöst – bitte den richtigen Wert auswählen.",
                blocking=True,
                field_key=key,
                pdf_field=mapping.pdf_field,
            )
            return
        if status == FieldStatus.EXTRACTED:
            blocking = required or legal
            self.check.add(
                Severity.ERROR if blocking else Severity.WARNING,
                "VALUE_NOT_CONFIRMED",
                f"{label}: Wert noch nicht bestätigt"
                + ("" if blocking else " – das Feld bleibt leer, solange er nicht bestätigt ist."),
                blocking=blocking,
                field_key=key,
                pdf_field=mapping.pdf_field,
            )
            return
        # MISSING / UNCLEAR
        if legal and decision_kind:
            self.check.add(
                Severity.ERROR,
                "LEGAL_DECISION_MISSING",
                f"Bitte ausdrücklich entscheiden: {label}",
                blocking=True,
                field_key=key,
                pdf_field=mapping.pdf_field,
            )
            return
        if status == FieldStatus.UNCLEAR:
            blocking = required or legal
            self.check.add(
                Severity.ERROR if blocking else Severity.WARNING,
                "VALUE_UNCLEAR",
                f"{label}: Wert unklar – bitte prüfen und bestätigen.",
                blocking=blocking,
                field_key=key,
                pdf_field=mapping.pdf_field,
            )
            return
        if required or legal:
            self.check.add(
                Severity.ERROR,
                "REQUIRED_MISSING",
                f"{label}: Angabe fehlt",
                blocking=True,
                field_key=key,
                pdf_field=mapping.pdf_field,
            )

    def mapping_error(self, mapping: FieldMapping, code: str, message: str, key: str | None = None) -> None:
        self.check.add(
            Severity.ERROR,
            code,
            f"{mapping.label}: {message}",
            blocking=True,
            field_key=key,
            pdf_field=mapping.pdf_field,
            dedup_by_field=False,
        )

    # -- Hauptablauf ------------------------------------------------------------
    def run(self, extra_messages: list[ValidationMessage] | None) -> FillPlan:
        never_count = 0
        if self.inspection is None and self.final:
            self.check.add(
                Severity.ERROR,
                "NO_INSPECTION",
                "Die Vorlage wurde nicht technisch geprüft (Feldbreiten und Ankreuz-Zustände ungeprüft). "
                "Bitte die Vorschau neu erzeugen.",
                blocking=True,
            )
        for mapping in self.t.fields:
            if mapping.kind in NEVER_KINDS or mapping.fill_policy == FillPolicy.NEVER:
                never_count += 1
                continue
            self.resolve_mapping(mapping)
        if never_count:
            self.check.add(
                Severity.INFO,
                "SIGNATURE_FIELDS_EMPTY",
                f"{never_count} Unterschrifts- und Datumsfelder bleiben leer "
                "(Unterschrift erfolgt handschriftlich).",
                blocking=False,
            )
        self.resolve_acknowledgments()
        self.collect_validation()
        for msg in extra_messages or []:
            self.check.add(
                msg.severity,
                msg.code,
                msg.message,
                blocking=msg.severity == Severity.ERROR,
                field_key=msg.field_key,
                dedup_by_field=True,
            )
        return FillPlan(
            template_id=self.t.template_id,
            template_version=self.t.version,
            mode="final" if self.final else "preview",
            values=self.values,
            checklist=self.check.items(),
        )

    def resolve_mapping(self, mapping: FieldMapping) -> None:
        state, undecided = self.conditions_state(mapping.conditions)
        if state == "unmet":
            return
        if state == "undecided":
            if mapping.required:
                for key in undecided:
                    self.check.add(
                        Severity.ERROR,
                        "CONDITION_UNDECIDED",
                        f"Bitte zuerst eine Auswahl treffen: {_label(key)}",
                        blocking=True,
                        field_key=key,
                        pdf_field=mapping.pdf_field,
                    )
            return

        if (
            self.inspection is not None
            and mapping.overlay_rect is None
            and self.inspection.field(mapping.pdf_field) is None
        ):
            self.mapping_error(
                mapping,
                "FIELD_MISSING_IN_PDF",
                "Das Feld fehlt in der PDF-Vorlage – Zuordnung im Vorlagen-Studio prüfen.",
            )
            return

        if mapping.kind == MappingKind.STATIC:
            self.emit_text(mapping, mapping.static_value or "", [])
            return

        if mapping.compose:
            self.resolve_compose(mapping)
            return

        key = mapping.source
        if key is None:  # durch das Schema ausgeschlossen, defensiv
            self.mapping_error(mapping, "MAPPING_INVALID", "Zuordnung ohne Quelle.")
            return
        self.used_keys.add(key)
        fv = self.fv(key)
        if fv is not None and fv.status == FieldStatus.NOT_APPLICABLE:
            return  # bewusst leer – erfüllt die Pflichtangabe
        if not _released(fv):
            self.report_unreleased(mapping, key, fv, required=mapping.required)
            return
        assert fv is not None
        if mapping.fill_policy == FillPolicy.MANUAL_ONLY and fv.status != FieldStatus.MANUAL:
            blocking = mapping.required or mapping.legal_relevant
            self.check.add(
                Severity.ERROR if blocking else Severity.WARNING,
                "MANUAL_ENTRY_REQUIRED",
                f"{_label(key)}: Bitte manuell eintragen (Übernahme aus Dokumenten ist hier nicht erlaubt).",
                blocking=blocking,
                field_key=key,
                pdf_field=mapping.pdf_field,
            )
            return
        value = _norm(fv)
        if value is None:
            # freigegeben, aber leer -> wie fehlend behandeln
            self.report_unreleased(
                mapping, key, FieldValue(key=key, status=FieldStatus.MISSING), required=mapping.required
            )
            return

        fdef = FIELD_REGISTRY.get(key)
        if mapping.kind in (MappingKind.TEXT, MappingKind.MULTILINE):
            text = format_output_text(fdef, fv)
            if text:
                self.emit_text(mapping, text, [key])
        elif mapping.kind == MappingKind.DATE_PART:
            part = _date_part(value, mapping.date_part or "")
            if part is None:
                self.mapping_error(
                    mapping, "INVALID_VALUE", "Wert passt nicht zu diesem Datumsfeld – bitte prüfen.", key
                )
                return
            self.emit_text(mapping, part, [key])
        elif mapping.kind == MappingKind.AMOUNT:
            self.resolve_amount(mapping, key, value)
        elif mapping.kind == MappingKind.CHECKBOX:
            self.resolve_checkbox(mapping, key, value, fdef)
        elif mapping.kind == MappingKind.RADIO:
            self.resolve_radio(mapping, key, value)

    def resolve_compose(self, mapping: FieldMapping) -> None:
        assert mapping.compose is not None
        problems = False
        intentionally_empty = False
        parts: dict[str, str] = {}
        for key, optional in COMPOSE_PLACEHOLDER.findall(mapping.compose):
            self.used_keys.add(key)
            fv = self.fv(key)
            status = fv.status if fv is not None else FieldStatus.MISSING
            if status == FieldStatus.NOT_APPLICABLE:
                parts[key] = ""
                intentionally_empty = True
                continue
            if _released(fv):
                assert fv is not None
                if mapping.fill_policy == FillPolicy.MANUAL_ONLY and fv.status != FieldStatus.MANUAL:
                    self.check.add(
                        Severity.ERROR if mapping.required else Severity.WARNING,
                        "MANUAL_ENTRY_REQUIRED",
                        f"{_label(key)}: Bitte manuell eintragen.",
                        blocking=mapping.required,
                        field_key=key,
                        pdf_field=mapping.pdf_field,
                    )
                    problems = True
                    continue
                parts[key] = format_output_text(FIELD_REGISTRY.get(key), fv) or ""
                continue
            if optional and status == FieldStatus.MISSING:
                parts[key] = ""
                continue
            # Pflichtteil fehlt oder optionaler Teil ist vorhanden, aber nicht freigegeben
            self.report_unreleased(mapping, key, fv, required=mapping.required)
            problems = True
        if problems:
            return

        def repl(m: re.Match[str]) -> str:
            return parts.get(m.group(1), "")

        text = _clean_composed(COMPOSE_PLACEHOLDER.sub(repl, mapping.compose))
        if text:
            self.emit_text(mapping, text, list(parts))
        elif mapping.required and not intentionally_empty:
            self.check.add(
                Severity.ERROR,
                "REQUIRED_MISSING",
                f"{mapping.label}: Angabe fehlt",
                blocking=True,
                pdf_field=mapping.pdf_field,
                dedup_by_field=False,
            )

    def resolve_amount(self, mapping: FieldMapping, key: str, value: str) -> None:
        try:
            amount = Decimal(value.replace(" ", ""))
        except (InvalidOperation, ValueError):
            amount = None
        if amount is None or not amount.is_finite():
            self.mapping_error(mapping, "INVALID_VALUE", "Kein gültiger Betrag – bitte prüfen.", key)
            return
        if amount < 0:
            self.mapping_error(mapping, "INVALID_VALUE", "Der Betrag darf nicht negativ sein.", key)
            return
        info = self.pdf_info(mapping)
        number_format = info.number_format if info else None
        decimals = int(number_format.get("decimals", 2)) if number_format else 2
        sep_style = int(number_format.get("sep_style", 2)) if number_format else 2
        text = format_af_number(amount, decimals, sep_style)
        raw: str | None = None
        if info is None or number_format is not None:
            raw = format_af_number(amount, decimals, 1)  # "1234.56" (Punkt, ohne Tausender)
        self.emit_text(mapping, text, [key], raw_value=raw)

    def resolve_checkbox(self, mapping: FieldMapping, key: str, value: str, fdef: FieldDef | None) -> None:
        checked_when = mapping.checked_when or "true"
        if fdef is not None:
            if fdef.dtype == FieldType.BOOL and value not in ("true", "false"):
                self.mapping_error(mapping, "INVALID_VALUE", "Bitte ausdrücklich Ja oder Nein wählen.", key)
                return
            if fdef.dtype == FieldType.CHOICE and fdef.choices and value not in fdef.choices:
                self.mapping_error(
                    mapping, "INVALID_VALUE", "Diese Auswahl ist ungültig – bitte neu wählen.", key
                )
                return
        on_state = self.on_state(mapping)
        if on_state is None:
            return
        state = on_state if value == checked_when else "Off"
        self.values.append(
            FillValue(
                pdf_field=mapping.pdf_field,
                kind=mapping.kind,
                state=state,
                source_keys=[key],
                page=mapping.page,
            )
        )

    def resolve_radio(self, mapping: FieldMapping, key: str, value: str) -> None:
        choice_map = mapping.choice_map or {}
        state = choice_map.get(value)
        if state is None:
            self.mapping_error(
                mapping, "INVALID_CHOICE", "Diese Auswahl ist in der Vorlage nicht vorgesehen.", key
            )
            return
        info = self.pdf_info(mapping)
        if info is not None and state not in info.on_states:
            self.mapping_error(
                mapping,
                "ONSTATE_MISSING",
                "Der Auswahlzustand fehlt in der PDF-Vorlage – Zuordnung im Vorlagen-Studio prüfen.",
                key,
            )
            return
        self.values.append(
            FillValue(
                pdf_field=mapping.pdf_field,
                kind=mapping.kind,
                state=state,
                source_keys=[key],
                page=mapping.page,
            )
        )

    # -- PDF-Bezug ----------------------------------------------------------------
    def pdf_info(self, mapping: FieldMapping) -> PdfFieldInfo | None:
        if self.inspection is None or mapping.overlay_rect is not None:
            return None
        return self.inspection.field(mapping.pdf_field)

    def on_state(self, mapping: FieldMapping) -> str | None:
        if self.inspection is None:
            self.check.add(
                Severity.ERROR if self.final else Severity.WARNING,
                "ONSTATE_UNVERIFIED",
                f"{mapping.label}: Ankreuz-Zustand nicht geprüft (Standard „{DEFAULT_CHECKBOX_ON_STATE}“).",
                blocking=self.final,
                pdf_field=mapping.pdf_field,
                dedup_by_field=False,
            )
            return DEFAULT_CHECKBOX_ON_STATE
        info = self.inspection.field(mapping.pdf_field)
        states = info.on_states if info else []
        if not states:
            self.mapping_error(
                mapping,
                "ONSTATE_MISSING",
                "Das Ankreuzfeld hat in der PDF keinen Ein-Zustand – Zuordnung im Vorlagen-Studio prüfen.",
            )
            return None
        return states[0]

    def emit_text(
        self,
        mapping: FieldMapping,
        text: str,
        keys: list[str],
        raw_value: str | None = None,
    ) -> None:
        multiline = mapping.kind == MappingKind.MULTILINE
        if multiline:
            text = "\n".join(normalize_ws(line) for line in text.replace("\r\n", "\n").split("\n")).strip()
        else:
            text = normalize_ws(text)
        if not text:
            return
        ok = True
        bad = unsupported_chars(text)
        if bad:
            shown = " ".join(f"„{c}“" if c.isprintable() else f"U+{ord(c):04X}" for c in bad[:5])
            self.mapping_error(
                mapping,
                "GLYPH_UNSUPPORTED",
                f"Zeichen {shown} kann in dieser Vorlage nicht dargestellt werden – bitte ersetzen.",
            )
            ok = False
        info = self.pdf_info(mapping)
        max_len = info.max_len if info and info.max_len else mapping.max_len
        if max_len and len(text) > max_len:
            self.mapping_error(
                mapping, "MAX_LENGTH_EXCEEDED", f"höchstens {max_len} Zeichen erlaubt – bitte kürzen."
            )
            ok = False
        font_size: float | None = None
        if ok:
            font_size, fits = self.fit(mapping, info, text, multiline)
            if not fits:
                self.mapping_error(mapping, "TEXT_TOO_LONG", "Text passt nicht ins Feld – bitte kürzen.")
                ok = False
        if not ok:
            return
        self.values.append(
            FillValue(
                pdf_field=mapping.pdf_field,
                kind=mapping.kind,
                text=text,
                raw_value=raw_value,
                font_size=font_size,
                source_keys=keys,
                page=mapping.page,
                overlay_rect=list(mapping.overlay_rect) if mapping.overlay_rect else None,
                align=mapping.align,
            )
        )

    def fit(
        self, mapping: FieldMapping, info: PdfFieldInfo | None, text: str, multiline: bool
    ) -> tuple[float | None, bool]:
        """-> (verkleinerte Schriftgröße oder None, passt?)."""
        if mapping.overlay_rect is not None:
            x0, y0, x1, y1 = mapping.overlay_rect
            base = mapping.font_size or DEFAULT_FONT_SIZE
            size = fit_font_size_multiline(text, x1 - x0, y1 - y0, base, MIN_FONT_SIZE, padding=0.0)
            if size is None:
                return None, False
            return (size if size < base else None), True
        if info is None or not info.widgets:
            return None, True
        if info.comb:
            return None, True  # Kammfeld: ein Zeichen je Kästchen, max_len genügt
        base = info.font_size if info.font_size else (mapping.font_size or DEFAULT_FONT_SIZE)
        width = min(w.width for w in info.widgets)
        height = min(w.height for w in info.widgets)
        if multiline or info.multiline:
            size = fit_font_size_multiline(text, width, height, base, MIN_FONT_SIZE, FIELD_PADDING)
        else:
            size = fit_font_size(text, width, base, MIN_FONT_SIZE, FIELD_PADDING)
        if size is None:
            return None, False
        if size < base or not info.font_size:
            # automatische Schriftgröße (0) wird auf einen festen Wert gesetzt
            return size, True
        return None, True

    # -- Bestätigungen und Validierung -------------------------------------------
    def resolve_acknowledgments(self) -> None:
        for ack in self.t.acknowledgments:
            state, undecided = self.conditions_state(ack.conditions)
            if state == "unmet":
                continue
            if state == "undecided":
                for key in undecided:
                    self.check.add(
                        Severity.ERROR,
                        "CONDITION_UNDECIDED",
                        f"Bitte zuerst eine Auswahl treffen: {_label(key)}",
                        blocking=True,
                        field_key=key,
                    )
                continue
            fv = self.fv(ack.field)
            if _released(fv) and fv is not None and _norm(fv) == "true":
                continue
            self.check.add(
                Severity.ERROR,
                "ACKNOWLEDGMENT_MISSING",
                f"Bitte bestätigen: {ack.text}",
                blocking=True,
                field_key=ack.field,
            )

    def collect_validation(self) -> None:
        """Prüfmeldungen freigegebener, verwendeter Felder übernehmen."""
        for key in sorted(self.used_keys):
            fv = self.fv(key)
            if not _released(fv) or fv is None:
                continue
            if not any(v.source_keys and key in v.source_keys for v in self.values):
                continue
            for msg in fv.validation:
                self.check.add(
                    msg.severity,
                    msg.code,
                    msg.message,
                    blocking=msg.severity == Severity.ERROR,
                    field_key=msg.field_key or key,
                )


def resolve_fill_values(
    template: TemplateDefinition,
    contract: dict[str, FieldValue],
    order: dict[str, FieldValue],
    inspection: FormInspection | None = None,
    mode: str = "preview",
    extra_messages: list[ValidationMessage] | None = None,
) -> FillPlan:
    """Befüllungsplan für eine Vorlage erzeugen (siehe Modul-Dokumentation).

    ``mode="final"`` behandelt fehlende technische Prüfungen (keine Inspektion)
    als blockierend; sonst sind die Regeln identisch.
    """
    if mode not in ("preview", "final"):
        raise ValueError("mode muss 'preview' oder 'final' sein")
    return _Resolver(template, contract, order, inspection, mode).run(extra_messages)
