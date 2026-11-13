"""Hilfen für Vorlagen-Tests (Werteauflösung, Registry, Studio). Nur MUSTER-Daten."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf
import pytest

from conftest import ROOT, SL13065_PDF, SL18477_PDF
from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory
from insurance_app.core.models import FieldStatus, FieldValue
from insurance_app.pdf.inspector import FormInspection, PdfFieldInfo, PdfPermissions, WidgetInfo
from insurance_app.templates.schema import Acknowledgment, FieldMapping, TemplateDefinition

SCHEMAS = ROOT / "config" / "field_schemas"

requires_originals = pytest.mark.skipif(
    not (SL13065_PDF.exists() and SL18477_PDF.exists()), reason="Originalvorlagen nicht vorhanden"
)

PERSONAL_VALUES = [
    "Musterfrau",
    "Erika",
    "DE89370400440532013000",
    "MUSTER-4711",
    "Große Straße",
    "Musterstadt",
    "Muster Bäckerei",
    "Mustermann",
    "12345",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_template(name: str) -> TemplateDefinition:
    return TemplateDefinition.model_validate_json((SCHEMAS / name).read_text("utf-8"))


def fv(
    key: str,
    value: str | None,
    normalized: str | None = None,
    status: FieldStatus = FieldStatus.CONFIRMED,
) -> FieldValue:
    return FieldValue(
        key=key,
        value=value,
        normalized_value=normalized if normalized is not None else value,
        status=status,
    )


def manual(key: str, value: str) -> FieldValue:
    return fv(key, value, status=FieldStatus.MANUAL)


def split(values: list[FieldValue]) -> tuple[dict[str, FieldValue], dict[str, FieldValue]]:
    contract: dict[str, FieldValue] = {}
    order: dict[str, FieldValue] = {}
    for v in values:
        target = order if FIELD_REGISTRY[v.key].category == FieldCategory.ORDER else contract
        target[v.key] = v
    return contract, order


def person() -> list[FieldValue]:
    return [
        fv("contract.number", "MUSTER-4711"),
        fv("insured.first_name", "Erika"),
        fv("insured.last_name", "Musterfrau"),
        fv("insured.birth_date", "01.02.1980", "1980-02-01"),
        fv("insured.street", "Große Straße"),
        fv("insured.house_number", "12a"),
        fv("insured.postal_code", "12345"),
        fv("insured.city", "Musterstadt"),
        fv("insured.birth_place", "Musterhausen"),
        fv("insured.nationality", "deutsch"),
    ]


def sl13065_values() -> list[FieldValue]:
    return [
        *person(),
        fv("policyholder.name", "Muster Bäckerei GmbH & Co. KG"),
        fv("employer.employment_start", "01.04.2010", "2010-04-01"),
        fv("employer.employment_end", "30.09.2026", "2026-09-30"),
        manual("order.severance_consent", "true"),
        manual("order.waive_refund", "false"),
    ]


def sl18477_private() -> list[FieldValue]:
    return [
        *person(),
        manual("order.continuation_mode", "private"),
        manual("order.private_contribution_option", "reduced"),
        manual("order.reduced_premium_amount", "1234.56"),
        manual("order.pep_status", "yes_related"),
        manual("order.pep_related_person", "Mustermann, Max, Bürgermeister"),
        manual("order.payment_method", "transfer"),
        manual("order.payment_iban", "DE89370400440532013000"),
        manual("order.payment_account_holder", "Erika Musterfrau"),
        manual("order.advice_waiver_acknowledged", "true"),
    ]


def sl18477_employer() -> list[FieldValue]:
    return [
        *person(),
        manual("order.continuation_mode", "employer"),
        manual("order.new_employer_company", "Muster Bäckerei GmbH & Co. KG"),
        manual("order.new_employer_street", "Große Straße"),
        manual("order.new_employer_house_number", "12a"),
        manual("order.new_employer_postal_code", "12345"),
        manual("order.new_employer_city", "Musterstadt"),
        manual("order.takeover_effective_month", "2027-01"),
        manual("order.premium_start_month", "2027-02"),
        manual("order.interim_private_continuation", "false"),
        manual("order.pep_status", "no"),
        manual("order.payment_method", "sepa"),
        manual("order.payment_iban", "DE89370400440532013000"),
        manual("order.payment_account_holder", "Muster Bäckerei GmbH & Co. KG"),
        manual("order.advice_waiver_acknowledged", "true"),
    ]


# ---------------------------------------------------------------------------
# Synthetische Vorlagen und Inspektionen (ohne PDF)
# ---------------------------------------------------------------------------


def template(*fields: FieldMapping, acks: list[Acknowledgment] | None = None) -> TemplateDefinition:
    return TemplateDefinition(
        template_id="muster_test_1",
        catalog_id="tecis_bestandsuebertrag",
        version="1",
        insurer="tecis",
        insurer_code="TECIS",
        form_number="MUSTER1",
        title="Muster",
        short_title="Muster",
        pdf_file="muster.pdf",
        sha256="0" * 64,
        page_count=1,
        fields=list(fields),
        acknowledgments=acks or [],
        mapping_confirmed=True,
    )


def text_info(
    name: str,
    width: float = 200,
    size: float = 9,
    max_len: int | None = None,
    number_format: dict | None = None,
    multiline: bool = False,
    height: float = 14,
) -> PdfFieldInfo:
    return PdfFieldInfo(
        name=name,
        field_type="text",
        widgets=[WidgetInfo(page=1, rect=[50, 100, 50 + width, 100 + height], xref=10)],
        pages=[1],
        max_len=max_len,
        font="Arial",
        font_size=size,
        number_format=number_format,
        multiline=multiline,
    )


def button_info(name: str, states: list[str], field_type: str = "checkbox") -> PdfFieldInfo:
    widgets = [
        WidgetInfo(page=1, rect=[50, 100 + 20 * i, 62, 112 + 20 * i], on_state=s, xref=20 + i, states=[s])
        for i, s in enumerate(states)
    ]
    return PdfFieldInfo(
        name=name,
        field_type=field_type,  # type: ignore[arg-type]
        widgets=widgets,
        pages=[1],
        choice_values=list(states),
    )


def inspection(*fields: PdfFieldInfo) -> FormInspection:
    return FormInspection(
        page_count=1,
        permissions=PdfPermissions(
            encrypted=False,
            needs_password=False,
            can_fill_forms=True,
            can_modify=True,
            can_annotate=True,
            can_assemble=True,
            can_print=True,
            can_copy=True,
        ),
        fields=list(fields),
        page_sizes=[(595.0, 842.0)],
        has_acroform=True,
    )


# ---------------------------------------------------------------------------
# Synthetische PDFs für Import/Studio
# ---------------------------------------------------------------------------


def _text(page: pymupdf.Page, name: str, rect, max_len: int = 0) -> int:
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = pymupdf.Rect(rect)
    w.text_font = "Helv"
    w.text_fontsize = 9
    if max_len:
        w.text_maxlen = max_len
    page.add_widget(w)
    return w.xref


def make_import_pdf(path: Path) -> Path:
    """Synthetisches Formular (MUSTER) mit typischen Feldnamen für die Auto-Zuordnung."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "MUSTER - Bestandsübertrag (KEINE ECHTDATEN)", fontsize=12)
    _text(page, "Versicherungsnummer", (150, 80, 300, 94))
    _text(page, "Name", (150, 100, 400, 114))
    _text(page, "IBAN", (150, 120, 400, 134))
    _text(page, "Geburtsort", (150, 140, 300, 154))
    _text(page, "Staatsangehörigkeit", (310, 140, 450, 154))
    _text(page, "Geburtsdatum Tag", (150, 160, 167, 174), max_len=2)
    _text(page, "Geburtsdatum Monat", (172, 160, 189, 174), max_len=2)
    _text(page, "Geburtsdatum Jahr", (194, 160, 234, 174), max_len=4)
    _text(page, "Datum Unterschrift.1", (50, 700, 67, 714), max_len=2)
    _text(page, "Datum Unterschrift.2", (72, 700, 89, 714), max_len=2)
    _text(page, "Datum Unterschrift.3", (94, 700, 134, 714), max_len=4)
    _text(page, "Bemerkung", (150, 200, 500, 214))
    cb = pymupdf.Widget()
    cb.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    cb.field_name = "Einverstanden"
    cb.rect = pymupdf.Rect(50, 230, 62, 242)
    page.add_widget(cb)
    bt = pymupdf.Widget()
    bt.field_type = pymupdf.PDF_WIDGET_TYPE_BUTTON
    bt.field_name = "Zurücksetzen"
    bt.rect = pymupdf.Rect(10, 10, 40, 30)
    bt.button_caption = "X"
    page.add_widget(bt)
    sig = _text(page, "Unterschrift", (200, 690, 450, 720))
    kind, parent = doc.xref_get_key(sig, "Parent")
    field = int(parent.split()[0]) if kind == "xref" else sig
    for xref in {sig, field}:
        doc.xref_set_key(xref, "FT", "/Sig")
    doc.save(path)
    doc.close()
    return Path(path)


def make_password_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "MUSTER", fontsize=12)
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_128, owner_pw="owner-muster", user_pw="muster")
    doc.close()
    return Path(path)
