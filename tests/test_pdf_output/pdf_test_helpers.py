"""Hilfen für PDF-/Export-Tests. Alle Werte sind synthetisch („MUSTER“)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf
import pytest

from conftest import ROOT, SL13065_PDF, SL18477_PDF
from insurance_app.core.fields import FIELD_REGISTRY, FieldCategory
from insurance_app.core.models import FieldStatus, FieldValue
from insurance_app.templates.schema import FieldMapping, MappingKind, TemplateDefinition

FONTS_DIR = ROOT / "assets" / "fonts"
SCHEMAS = ROOT / "config" / "field_schemas"

requires_originals = pytest.mark.skipif(
    not (SL13065_PDF.exists() and SL18477_PDF.exists()), reason="Originalvorlagen nicht vorhanden"
)

# Werte, die nie in Logs, Checklisten oder Dateinamen auftauchen dürfen
PERSONAL_VALUES = [
    "Musterfrau",
    "Erika",
    "DE89370400440532013000",
    "DE89 3704",
    "MUSTER-4711",
    "Große Straße",
    "Musterstadt",
    "Muster Bäckerei",
    "Musterhausen",
    "Mustermann",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_template(name: str) -> TemplateDefinition:
    return TemplateDefinition.model_validate_json((SCHEMAS / name).read_text("utf-8"))


def fv(
    key: str, value: str, normalized: str | None = None, status: FieldStatus = FieldStatus.CONFIRMED
) -> FieldValue:
    return FieldValue(
        key=key, value=value, normalized_value=normalized if normalized is not None else value, status=status
    )


def split(values: list[FieldValue]) -> tuple[dict[str, FieldValue], dict[str, FieldValue]]:
    contract: dict[str, FieldValue] = {}
    order: dict[str, FieldValue] = {}
    for v in values:
        target = order if FIELD_REGISTRY[v.key].category == FieldCategory.ORDER else contract
        target[v.key] = v
    return contract, order


def muster_person() -> list[FieldValue]:
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


def order(key: str, value: str) -> FieldValue:
    return fv(key, value, status=FieldStatus.MANUAL)


def sl13065_values(severance: str = "true", waive_refund: str = "false") -> list[FieldValue]:
    return [
        *muster_person(),
        fv("policyholder.name", "Muster Bäckerei GmbH & Co. KG"),
        fv("employer.employment_start", "01.04.2010", "2010-04-01"),
        fv("employer.employment_end", "30.09.2026", "2026-09-30"),
        order("order.severance_consent", severance),
        order("order.waive_refund", waive_refund),
    ]


def sl18477_employer_values() -> list[FieldValue]:
    return [
        *muster_person(),
        order("order.continuation_mode", "employer"),
        order("order.new_employer_company", "Muster Bäckerei GmbH & Co. KG"),
        order("order.new_employer_contact", "Max Mustermann"),
        order("order.new_employer_street", "Große Straße"),
        order("order.new_employer_house_number", "12a"),
        order("order.new_employer_postal_code", "12345"),
        order("order.new_employer_city", "Musterstadt"),
        order("order.takeover_effective_month", "2027-01"),
        order("order.premium_start_month", "2027-02"),
        order("order.interim_private_continuation", "true"),
        order("order.interim_contribution_option", "paying"),
        order("order.interim_premium_amount", "89.5"),
        order("order.pep_status", "no"),
        order("order.payment_method", "sepa"),
        order("order.payment_iban", "DE89370400440532013000"),
        order("order.payment_account_holder", "Muster Bäckerei GmbH & Co. KG"),
        order("order.payment_institution", "Musterbank Musterstadt"),
        order("order.advice_waiver_acknowledged", "true"),
    ]


def sl18477_private_values() -> list[FieldValue]:
    return [
        *muster_person(),
        order("order.continuation_mode", "private"),
        order("order.private_contribution_option", "reduced"),
        order("order.reduced_premium_amount", "1234.56"),
        order("order.pep_status", "yes_related"),
        order("order.pep_related_person", "Mustermann, Max, Bürgermeister"),
        order("order.payment_method", "transfer"),
        order("order.payment_iban", "DE89370400440532013000"),
        order("order.payment_account_holder", "Erika Musterfrau"),
        order("order.advice_waiver_acknowledged", "true"),
    ]


# ---------------------------------------------------------------------------
# Synthetische PDFs
# ---------------------------------------------------------------------------


def _text_widget(
    page: pymupdf.Page, name: str, rect: tuple[float, float, float, float], size: float = 10
) -> int:
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = pymupdf.Rect(rect)
    w.text_font = "Helv"
    w.text_fontsize = size
    page.add_widget(w)
    return w.xref


def make_form_pdf(
    path: Path,
    *,
    encryption: str | None = None,
    signed: bool = False,
) -> Path:
    """Synthetisches AcroForm-PDF (MUSTER) mit Text-, Ankreuz- und Unterschriftsfeld.

    ``encryption``: None, "no_modify" (Ausfüllen ja, Ändern nein), "modify" oder "password".
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 60), "MUSTER - KEINE ECHTDATEN", fontsize=12)
    page.insert_text((50, 95), "Versicherungsnummer", fontsize=9)
    _text_widget(page, "Versicherungsnummer", (160, 84, 320, 98))
    page.insert_text((50, 125), "Name", fontsize=9)
    _text_widget(page, "Name", (160, 114, 420, 128))
    cb = pymupdf.Widget()
    cb.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    cb.field_name = "Zustimmung"
    cb.rect = pymupdf.Rect(50, 140, 62, 152)
    page.add_widget(cb)
    page.insert_text((70, 150), "Ich stimme zu (MUSTER)", fontsize=9)
    sig = _text_widget(page, "Unterschrift", (300, 700, 500, 730))
    kind, parent = doc.xref_get_key(sig, "Parent")
    field = int(parent.split()[0]) if kind == "xref" else sig
    for xref in {sig, field}:
        doc.xref_set_key(xref, "FT", "/Sig")
    if signed:
        doc.xref_set_key(field, "V", "<</Type/Sig/Filter/Adobe.PPKLite/Contents <00>>>")
    base = pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_COPY
    if encryption is None:
        doc.save(path)
    elif encryption == "no_modify":
        doc.save(
            path,
            encryption=pymupdf.PDF_ENCRYPT_AES_128,
            owner_pw="owner-muster",
            user_pw="",
            permissions=base,
        )
    elif encryption == "modify":
        doc.save(
            path,
            encryption=pymupdf.PDF_ENCRYPT_AES_128,
            owner_pw="owner-muster",
            user_pw="",
            permissions=base | pymupdf.PDF_PERM_MODIFY,
        )
    elif encryption == "password":
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_128, owner_pw="owner-muster", user_pw="muster")
    else:  # pragma: no cover
        raise ValueError(encryption)
    doc.close()
    return Path(path)


def make_plain_pdf(path: Path, *, encrypted_no_modify: bool = False) -> Path:
    """Synthetisches PDF ohne Formularfelder (für Koordinaten-Overlay)."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 60), "MUSTER - Kündigung (KEINE ECHTDATEN)", fontsize=12)
    page.insert_text((50, 110), "Vertragsnummer:", fontsize=10)
    page.insert_text((50, 140), "Name:", fontsize=10)
    if encrypted_no_modify:
        doc.save(
            path,
            encryption=pymupdf.PDF_ENCRYPT_AES_128,
            owner_pw="owner-muster",
            user_pw="",
            permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ANNOTATE,
        )
    else:
        doc.save(path)
    doc.close()
    return Path(path)


def form_template(pdf: Path, *, confirmed: bool = True) -> TemplateDefinition:
    """Feldzuordnung zum synthetischen Formular aus ``make_form_pdf``."""
    return TemplateDefinition(
        template_id="muster_formular_1",
        catalog_id="tecis_bestandsuebertrag",
        version="1",
        insurer="tecis",
        insurer_code="TECIS",
        form_number="MUSTER1",
        title="Muster-Formular",
        short_title="Muster",
        pdf_file=pdf.name,
        sha256=sha256(pdf),
        page_count=1,
        fields=[
            FieldMapping(
                pdf_field="Versicherungsnummer",
                label="Versicherungsnummer",
                kind=MappingKind.TEXT,
                source="contract.number",
                required=True,
                page=1,
            ),
            FieldMapping(
                pdf_field="Name",
                label="Name, Vorname",
                kind=MappingKind.TEXT,
                compose="{insured.last_name}, {insured.first_name}",
                required=True,
                page=1,
            ),
            FieldMapping(
                pdf_field="Zustimmung",
                label="Zustimmung",
                kind=MappingKind.CHECKBOX,
                source="order.severance_consent",
                legal_relevant=True,
                required=True,
                page=1,
            ),
            FieldMapping(
                pdf_field="Unterschrift",
                label="Unterschrift",
                kind=MappingKind.SIGNATURE,
                fill_policy="never",
                page=1,
            ),
        ],
        mapping_confirmed=confirmed,
    )


def form_values(consent: str = "true") -> list[FieldValue]:
    return [
        fv("contract.number", "MUSTER-4711"),
        fv("insured.first_name", "Erika"),
        fv("insured.last_name", "Musterfrau"),
        order("order.severance_consent", consent),
    ]
