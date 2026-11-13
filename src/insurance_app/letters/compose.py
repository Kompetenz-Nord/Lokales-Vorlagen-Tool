"""Briefinhalt aus freigegebenen Feldwerten zusammenstellen (``LetterPlan``).

Regeln (identisch zur Formularbefüllung):
* Nur Werte mit Status ``CONFIRMED`` oder ``MANUAL`` werden gedruckt.
* ``NOT_APPLICABLE`` gilt als bewusste Entscheidung „leer“ – außer bei rechtlich
  relevanten Ja/Nein- und Auswahlfeldern: Dort ist „nicht relevant“ keine
  Entscheidung, der Wert gilt als offen.
* Rechtlich relevante Auswahlen (``legal_relevant``) müssen ausdrücklich
  getroffen sein, bevor ein davon abhängiger Textbaustein entschieden werden kann.
* Fehlende Pflichtwerte blockieren den finalen Export; in der Vorschau erscheinen
  sie als sichtbare Markierung ``[Fehlt: …]``.
* Meldungen enthalten nur Feldbezeichnungen, nie personenbezogene Werte.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.fields import FIELD_REGISTRY, FREQUENCY_CHOICES, FieldType
from ..core.models import FieldStatus, FieldValue, Severity, ValidationMessage
from ..templates.resolver import ChecklistItem, is_legal_decision_field
from ..templates.schema import Condition
from .schema import PLACEHOLDER, LetterBlock, LetterHint, LetterTemplate

MISSING_MARK = "[Fehlt: {label}]"


class RenderedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    style: Literal["paragraph", "line", "list"] = "paragraph"
    has_missing: bool = False


class InfoLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


class LetterPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    template_version: str
    mode: Literal["preview", "final"] = "preview"
    sender_lines: list[str] = Field(default_factory=list)
    return_line: str = ""
    recipient_lines: list[str] = Field(default_factory=list)
    info: list[InfoLine] = Field(default_factory=list)
    subject: str = ""
    subject_lines: list[str] = Field(default_factory=list)
    salutation: str = ""
    blocks: list[RenderedBlock] = Field(default_factory=list)
    closing: str = ""
    signer: str = ""
    attachments: list[str] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)
    hints: list[LetterHint] = Field(default_factory=list)
    title: str = ""

    @property
    def blocking(self) -> bool:
        return any(i.blocking for i in self.checklist)

    @property
    def open_items(self) -> list[ChecklistItem]:
        return [i for i in self.checklist if i.blocking or i.severity != Severity.INFO]

    def printed_text(self) -> str:
        """Gesamter gedruckter Text (für Qualitätskontrolle)."""
        parts = [*self.sender_lines, self.return_line, *self.recipient_lines]
        parts += [f"{i.label} {i.value}" for i in self.info]
        parts += [self.subject, *self.subject_lines, self.salutation]
        parts += [b.text for b in self.blocks]
        parts += [self.closing, self.signer, *self.attachments]
        return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Formatierung
# ---------------------------------------------------------------------------


def format_amount_de(value: str) -> str | None:
    try:
        d = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{'.'.join(groups)},{frac} €"


def format_date_iso_de(value: str) -> str | None:
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None
    return f"{d.day:02d}.{d.month:02d}.{d.year:04d}"


def format_iban(value: str) -> str:
    compact = re.sub(r"\s+", "", value).upper()
    return " ".join(compact[i : i + 4] for i in range(0, len(compact), 4))


def _display(fv: FieldValue, flt: str | None) -> str | None:
    fdef = FIELD_REGISTRY[fv.key]
    norm = fv.normalized_value
    raw = fv.value
    if flt == "date" or (flt is None and fdef.dtype == FieldType.DATE):
        return format_date_iso_de(norm) if norm else None
    if flt == "amount" or (flt is None and fdef.dtype == FieldType.AMOUNT):
        return format_amount_de(norm) if norm else None
    if flt == "interval":
        return FREQUENCY_CHOICES.get(norm or "", None)
    if flt == "choice" or (flt is None and fdef.dtype == FieldType.CHOICE):
        return (fdef.choices or {}).get(norm or "", None)
    if flt == "iban" or (flt is None and fdef.dtype == FieldType.IBAN):
        return format_iban(norm or raw or "") or None
    text = norm if norm not in (None, "") else raw
    return text.strip() if text else None


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    return text.strip().strip(",").strip()


# ---------------------------------------------------------------------------
# Auflösung
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, contract: dict[str, FieldValue], order: dict[str, FieldValue], mode: str) -> None:
        self.contract = contract
        self.order = order
        self.mode = mode
        self.items: dict[tuple[str, str | None], ChecklistItem] = {}

    def fv(self, key: str) -> FieldValue | None:
        store = self.order if key.startswith("order.") else self.contract
        return store.get(key)

    def add(self, item: ChecklistItem) -> None:
        self.items.setdefault((item.code, item.field_key or item.message), item)

    @staticmethod
    def label(key: str) -> str:
        fdef = FIELD_REGISTRY.get(key)
        return fdef.label if fdef else key

    # -- Status ----------------------------------------------------------
    def decided(self, key: str) -> tuple[bool, str | None]:
        """(entschieden?, normalisierter Wert oder None)."""
        fv = self.fv(key)
        if fv is None:
            return False, None
        if fv.status.is_released:
            return True, fv.normalized_value if fv.normalized_value not in (None, "") else (fv.value or None)
        if fv.status == FieldStatus.NOT_APPLICABLE:
            # „nicht relevant“ ersetzt keine ausdrückliche rechtliche Auswahl
            return (False, None) if is_legal_decision_field(key) else (True, None)
        return False, None

    def report_unreleased(self, key: str, *, required: bool, reason: str = "value") -> None:
        fv = self.fv(key)
        status = fv.status if fv else FieldStatus.MISSING
        label = self.label(key)
        if status == FieldStatus.CONFLICT:
            code, msg = "CONFLICT", f"{label}: Konflikt zwischen Quellen nicht gelöst"
        elif status in (FieldStatus.EXTRACTED,) or (status == FieldStatus.UNCLEAR and fv and fv.value):
            code, msg = "UNCONFIRMED", f"{label}: Wert noch nicht bestätigt"
        elif status == FieldStatus.NOT_APPLICABLE and is_legal_decision_field(key):
            code, msg = (
                "DECISION_REQUIRED",
                f"Bitte ausdrücklich entscheiden: {label} („nicht relevant“ ist hier keine Entscheidung)",
            )
        elif status == FieldStatus.NOT_APPLICABLE:
            code, msg = (
                "NOT_APPLICABLE_REQUIRED",
                f"{label}: als „nicht relevant“ markiert, wird für diesen Brief aber benötigt",
            )
        elif reason == "decision":
            code, msg = "DECISION_REQUIRED", f"Bitte ausdrücklich entscheiden: {label}"
        else:
            code, msg = "MISSING_VALUE", f"{label}: Angabe fehlt"
        self.add(
            ChecklistItem(
                severity=Severity.ERROR if required else Severity.WARNING,
                code=code,
                message=msg,
                field_key=key,
                blocking=required,
            )
        )

    # -- Bedingungen -----------------------------------------------------
    def conditions(self, conds: list[Condition]) -> bool | None:
        """True/False oder None (unentschieden – rechtlich relevante Auswahl fehlt)."""
        for cond in conds:
            ok, value = self.decided(cond.field)
            if not ok:
                fdef = FIELD_REGISTRY.get(cond.field)
                if fdef is not None and fdef.legal_relevant:
                    self.report_unreleased(cond.field, required=True, reason="decision")
                    return None
                value = None
            if not cond.matches(value):
                return False
        return True

    # -- Platzhalter -----------------------------------------------------
    def render(self, text: str, template: LetterTemplate, depth: int = 0) -> tuple[str, bool]:
        """Text mit Platzhaltern füllen. Rückgabe (Text, enthält_fehlende_Werte)."""
        missing = False

        def repl(m: re.Match[str]) -> str:
            nonlocal missing
            derived, key, optional, flt = m.groups()
            if derived:
                d = template.derived[derived[1:]]
                ok, value = self.decided(d.field)
                if not ok:
                    self.report_unreleased(d.field, required=True, reason="decision")
                    missing = True
                    return MISSING_MARK.format(label=self.label(d.field))
                case = d.cases.get(value or "")
                if case is None:
                    self.add(
                        ChecklistItem(
                            severity=Severity.ERROR,
                            code="NOT_ALLOWED",
                            message=f"{self.label(d.field)}: Auswahl passt nicht zu dieser Vorlage",
                            field_key=d.field,
                            blocking=True,
                        )
                    )
                    missing = True
                    return MISSING_MARK.format(label=self.label(d.field))
                if depth > 3:  # pragma: no cover - Schutz vor Endlosschleifen
                    raise ValueError("Zu tief verschachtelte Textbausteine")
                sub, sub_missing = self.render(case, template, depth + 1)
                missing = missing or sub_missing
                return sub
            fv = self.fv(key)
            ok, _ = self.decided(key)
            shown = _display(fv, flt) if (ok and fv is not None and fv.status.is_released) else None
            if shown:
                return shown
            if optional:
                return ""
            if ok and fv is not None and fv.status.is_released and not shown:
                self.add(
                    ChecklistItem(
                        severity=Severity.ERROR,
                        code="FORMAT_ERROR",
                        message=f"{self.label(key)}: Wert kann nicht formatiert werden – bitte prüfen",
                        field_key=key,
                        blocking=True,
                    )
                )
            else:
                self.report_unreleased(key, required=True)
            missing = True
            return MISSING_MARK.format(label=self.label(key))

        return _clean(PLACEHOLDER.sub(repl, text)), missing


def _render_block(ctx: _Ctx, block: LetterBlock, template: LetterTemplate) -> RenderedBlock | None:
    if ctx.conditions(block.conditions) is not True:
        return None
    text, missing = ctx.render(block.text, template)
    if not text:
        return None
    return RenderedBlock(text=text, style=block.style, has_missing=missing)


def resolve_letter(
    template: LetterTemplate,
    contract: dict[str, FieldValue],
    order: dict[str, FieldValue],
    *,
    mode: Literal["preview", "final"] = "preview",
    extra_messages: list[ValidationMessage] | None = None,
) -> LetterPlan:
    ctx = _Ctx(contract, order, mode)
    plan = LetterPlan(
        template_id=template.template_id, template_version=template.version, mode=mode, title=template.title
    )

    # Absender (Versicherungsnehmer) ------------------------------------
    name, _ = ctx.render("{policyholder.first_name?} {policyholder.name}", template)
    street, _ = ctx.render("{policyholder.street} {policyholder.house_number?}", template)
    city, _ = ctx.render("{policyholder.postal_code} {policyholder.city}", template)
    plan.sender_lines = [x for x in (name, street, city) if x]
    plan.return_line = " · ".join(plan.sender_lines)
    plan.signer = name

    # Empfänger ------------------------------------------------------------
    rec = [
        ctx.render("{order.recipient_company}", template)[0],
        ctx.render("{order.recipient_department?}", template)[0],
        ctx.render("{order.recipient_street} {order.recipient_house_number?}", template)[0],
        ctx.render("{order.recipient_postal_code} {order.recipient_city}", template)[0],
    ]
    plan.recipient_lines = [x for x in rec if x]

    # Informationsblock ------------------------------------------------------
    for item in template.info_block:
        value, _ = ctx.render(item.text, template)
        if value or not item.optional:
            plan.info.append(InfoLine(label=item.label, value=value))

    # Betreff ----------------------------------------------------------------
    for variant in template.subject_variants:
        res = ctx.conditions(variant.conditions)
        if res is None:
            break
        if res:
            plan.subject, _ = ctx.render(variant.text, template)
            break
    if not plan.subject:
        ctx.add(
            ChecklistItem(
                severity=Severity.ERROR,
                code="NO_SUBJECT",
                message="Betreff kann noch nicht bestimmt werden – bitte die Auswahlfelder ausfüllen",
                blocking=True,
            )
        )
        plan.subject = MISSING_MARK.format(label="Betreff")
    for line in template.subject_lines:
        rb = _render_block(ctx, line, template)
        if rb and not rb.text.endswith(":"):
            plan.subject_lines.append(rb.text)

    # Text ----------------------------------------------------------------------
    plan.salutation = template.salutation
    for block in template.blocks:
        rb = _render_block(ctx, block, template)
        if rb:
            plan.blocks.append(rb)
    plan.closing = template.closing

    attachments, _ = ctx.render("{order.attachments?}", template)
    if attachments:
        plan.attachments = [a.strip() for a in re.split(r"[;\n]", attachments) if a.strip()]

    # Zulässige Werte ---------------------------------------------------------
    for allowed in template.allowed_values:
        if ctx.conditions(allowed.conditions) is not True:
            continue
        ok, value = ctx.decided(allowed.field)
        if ok and value is not None and value not in allowed.values:
            ctx.add(
                ChecklistItem(
                    severity=Severity.ERROR,
                    code="NOT_ALLOWED",
                    message=allowed.message,
                    field_key=allowed.field,
                    blocking=True,
                )
            )

    # Pflicht-Bestätigungen ---------------------------------------------------
    for ack in template.acknowledgments:
        if ctx.conditions(ack.conditions) is not True:
            continue
        ok, value = ctx.decided(ack.field)
        if not (ok and value == "true"):
            ctx.add(
                ChecklistItem(
                    severity=Severity.ERROR,
                    code="ACK_REQUIRED",
                    message=f"Bitte bestätigen: {ack.text}",
                    field_key=ack.field,
                    blocking=True,
                )
            )

    # Hinweise ------------------------------------------------------------------
    silent = _Ctx(contract, order, mode)  # Hinweise erzeugen keine Checklisten-Einträge
    for hint in template.hints:
        if silent.conditions(hint.conditions) is True:
            plan.hints.append(hint)

    for msg in extra_messages or []:
        ctx.add(
            ChecklistItem(
                severity=msg.severity,
                code=msg.code,
                message=msg.message,
                field_key=msg.field_key,
                blocking=msg.severity == Severity.ERROR,
            )
        )
    ctx.add(
        ChecklistItem(
            severity=Severity.INFO,
            code="SIGNATURE_MANUAL",
            message="Die Unterschrift erfolgt handschriftlich – der Unterschriftsbereich bleibt frei.",
            blocking=False,
        )
    )
    plan.checklist = sorted(ctx.items.values(), key=lambda i: (not i.blocking, i.severity != Severity.ERROR))
    return plan
