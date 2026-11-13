"""Musterfall-Generator: synthetische Beispieldokumente mit Soll-Werten.

Alle Inhalte sind frei erfunden und auf jeder Seite als
„MUSTER – KEINE ECHTDATEN“ gekennzeichnet. Verwendet werden ausschließlich
fiktive Namen, Adressen und veröffentlichte Beispiel-IBANs.

Die Erzeugung ist deterministisch (fester Zufalls-Seed, feste Metadaten), damit
die Dateien in ``tests/fixtures/synthetic`` reproduzierbar sind.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pydantic import BaseModel, ConfigDict, Field

from insurance_app.core.models import (
    BoundingBox,
    DocumentType,
    ExtractionMethod,
    PageText,
    TextLayerOrigin,
    Word,
)
from insurance_app.core.paths import resource_root

__all__ = [
    "CUSTOMER_IBAN",
    "FILE_AMENDMENT",
    "FILE_POLICY",
    "FILE_POLICY_SCAN",
    "FILE_POLICY_SCAN_LOWRES",
    "FILE_PORTAL",
    "FILE_STATUS_REPORT",
    "INSURER_IBAN",
    "MUSTER_MARK",
    "MUSTERFALL_FILES",
    "PORTAL_DPI",
    "SampleDocMeta",
    "SampleSet",
    "generate_sample_set",
    "portal_page_text",
    "portal_words",
    "sample_metadata",
]

log = logging.getLogger(__name__)

MUSTER_MARK = "MUSTER – KEINE ECHTDATEN"
INSURER_NAME = "Beispiel Lebensversicherung AG"
INSURER_HEADER = "Beispiel Lebensversicherung AG (MUSTER)"
INSURER_IBAN = "DE02120300000000202051"  # veröffentlichte Beispiel-IBAN (Versicherer, Fußzeile)
INSURER_IBAN_PRINTED = "DE02 1203 0000 0000 2020 51"
CUSTOMER_IBAN = "DE89370400440532013000"  # veröffentlichte Beispiel-IBAN (Kunde)
CUSTOMER_IBAN_PRINTED = "DE89 3704 0044 0532 0130 00"
CREDITOR_ID = "DE98ZZZ09999999999"

FILE_POLICY = "MUSTER_Police_Direktversicherung.pdf"
FILE_AMENDMENT = "MUSTER_Nachtrag_Beitrag.pdf"
FILE_PORTAL = "MUSTER_Vertragsuebersicht_Portal.png"
FILE_POLICY_SCAN = "MUSTER_Police_Scan.pdf"
FILE_POLICY_SCAN_LOWRES = "MUSTER_Police_Scan_lowres.png"
FILE_STATUS_REPORT = "MUSTER_Standmitteilung_Scan.pdf"

# Dokumente des 1-Klick-Musterfalls (ein Vertrag, Police + Nachtrag + Standmitteilung)
MUSTERFALL_FILES: tuple[str, ...] = (FILE_POLICY, FILE_AMENDMENT, FILE_STATUS_REPORT)

PORTAL_DPI = 96.0
PORTAL_SIZE = (1200, 470)

_A4 = (595.0, 842.0)
_FIXED_PDF_DATE = "D:20260101000000Z"
_GRAY_LABEL = (0.35, 0.35, 0.35)
_RED = (0.75, 0.0, 0.0)
_BLACK = (0.0, 0.0, 0.0)


class SampleDocMeta(BaseModel):
    """Beschreibung einer Musterdatei (für Tests, Klassifikation und Musterfall)."""

    model_config = ConfigDict(extra="forbid")

    doc_type: DocumentType
    description: str
    document_date: date | None = None
    effective_date: date | None = None
    needs_ocr: bool = False
    embedded_ocr: bool = False
    contract_number: str | None = None
    page_count: int = 1


class SampleSet(BaseModel):
    """Erzeugte Musterdateien und ihre Soll-Werte (normalisierte Vertragsdaten)."""

    model_config = ConfigDict(extra="forbid")

    files: dict[str, Path]
    ground_truth: dict[str, dict[str, str]]  # Dateiname -> {Feldschlüssel: normalisierter Wert}
    metadata: dict[str, SampleDocMeta] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Soll-Werte
# ---------------------------------------------------------------------------

_POLICY_PAGE1_TRUTH: dict[str, str] = {
    "contract.number": "8815201-7",
    "insurer.name": INSURER_NAME,
    "contract.product": "Direktversicherung gemäß § 3 Nr. 63 EStG (MUSTER)",
    "contract.contract_type": "Direktversicherung",
    "contract.tariff": "DV-Klassik (MUSTER)",
    "contract.start_date": "2019-01-01",
    "contract.end_date": "2053-01-01",
    "contract.pension_start_date": "2053-01-01",
    "contract.premium_end_date": "2052-12-01",
    "premium.amount": "150.00",
    "premium.currency": "EUR",
    "premium.frequency": "monthly",
    "policyholder.name": "Muster Bäckerei GmbH",
    "policyholder.legal_form": "GmbH",
    "policyholder.street": "Backstraße",
    "policyholder.house_number": "1",
    "policyholder.postal_code": "12345",
    "policyholder.city": "Musterstadt",
    "insured.last_name": "Musterfrau",
    "insured.first_name": "Erika",
    "insured.birth_date": "1988-08-12",
    "insured.nationality": "deutsch",
    "insured.street": "Beispielweg",
    "insured.house_number": "7",
    "insured.postal_code": "12345",
    "insured.city": "Musterstadt",
}
_POLICY_PAGE2_TRUTH: dict[str, str] = {
    "bank.account_holder": "Muster Bäckerei GmbH",
    "bank.iban": CUSTOMER_IBAN,
    "bank.institution": "Beispielbank (MUSTER)",
}
_AMENDMENT_TRUTH: dict[str, str] = {
    "contract.number": "8815201-7",
    "insurer.name": INSURER_NAME,
    "policyholder.name": "Muster Bäckerei GmbH",
    "policyholder.legal_form": "GmbH",
    "contract.changes": "Herabsetzung des Beitrags (MUSTER)",
    "premium.amount": "100.00",
    "premium.currency": "EUR",
    "premium.frequency": "monthly",
    "insured.last_name": "Musterfrau",
    "insured.first_name": "Erika",
    "insured.street": "Neue Straße",
    "insured.house_number": "3",
    "insured.postal_code": "54321",
    "insured.city": "Beispielstadt",
}
_PORTAL_TRUTH: dict[str, str] = {
    "contract.number": "5550123-4",
    "contract.start_date": "2022-02-01",
    "contract.end_date": "2065-08-01",
    "contract.status": "Ruhend",
    "contract.contract_type": "Eigenvertrag",
    "contract.product": "Basisrente Invest (MUSTER)",
    "insurer.name": INSURER_NAME,
    "contract.application_number": "123456789",
    "contract.intermediary": "Beispiel Finanz AG (MUSTER)",
    "policyholder.name": "Beispiel",
    "policyholder.first_name": "Max",
    "insured.last_name": "Beispiel",
    "insured.first_name": "Max",
}
_STATUS_TRUTH: dict[str, str] = {
    "contract.number": "8815201-7",
    "insurer.name": INSURER_NAME,
    "policyholder.name": "Muster Bäckerei GmbH",
    "policyholder.legal_form": "GmbH",
    "insured.last_name": "Musterfrau",
    "insured.first_name": "Erika",
    "contract.start_date": "2019-01-01",
    "contract.premium_end_date": "2052-12-01",
    "contract.pension_start_date": "2053-01-01",
}

# ---------------------------------------------------------------------------
# Seitenbeschreibung (wird sichtbar gezeichnet und/oder als Textebene gesetzt)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Op:
    x: float
    y: float  # Grundlinie (Seitenkoordinaten, oben links)
    text: str
    size: float = 10.0
    bold: bool = False
    color: tuple[float, float, float] = _BLACK
    role: str = "other"  # "label" | "value" | "other" | "watermark"
    ocr_text: str | None = None  # abweichender Text der (fremden) OCR-Textebene
    angle: float = 0.0


def _fonts_dir() -> Path:
    return resource_root() / "assets" / "fonts"


def _font_path(bold: bool) -> Path:
    return _fonts_dir() / ("LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf")


class _Measure:
    def __init__(self) -> None:
        self.regular = pymupdf.Font(fontfile=str(_font_path(False)))
        self.bold = pymupdf.Font(fontfile=str(_font_path(True)))

    def width(self, text: str, size: float, bold: bool = False) -> float:
        return (self.bold if bold else self.regular).text_length(text, fontsize=size)


def _wrap(measure: _Measure, text: str, width: float, size: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if measure.width(trial, size) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _letterhead(ops: list[_Op], measure: _Measure, page_no: int, page_count: int) -> None:
    w = _A4[0]
    ops.append(_Op(56, 50, INSURER_HEADER, size=12, bold=True))
    ops.append(_Op(56, 63, "Musterallee 1 · 12345 Musterstadt", size=8, color=_GRAY_LABEL))
    mark_w = measure.width(MUSTER_MARK, 9, bold=True)
    ops.append(_Op(w - 56 - mark_w, 50, MUSTER_MARK, size=9, bold=True, color=_RED))
    footer = [
        f"{INSURER_HEADER} · Sitz der Gesellschaft: Musterstadt · Registergericht Musterstadt HRB 00000",
        f"Bankverbindung: Beispielbank · IBAN {INSURER_IBAN_PRINTED} · BIC BYLADEM1001 · Gläubiger-ID {CREDITOR_ID}",
        f"{MUSTER_MARK} · Seite {page_no} von {page_count}",
    ]
    for i, line in enumerate(footer):
        ops.append(_Op(56, 790 + i * 10, line, size=7, color=_GRAY_LABEL))


def _watermark(ops: list[_Op]) -> None:
    ops.insert(0, _Op(95, 700, MUSTER_MARK, size=40, bold=True, color=(0.86, 0.86, 0.86), angle=35.0))


def _rows(
    ops: list[_Op],
    rows: list[tuple[str, str]],
    y: float,
    *,
    label_x: float = 56,
    value_x: float = 230,
    pitch: float = 17,
    value_dy: float = 0.0,
) -> float:
    for label, value in rows:
        if label:
            ops.append(_Op(label_x, y, label, color=_GRAY_LABEL, role="label"))
        if value:
            ops.append(_Op(value_x, y - value_dy, value, role="value"))
        y += pitch
    return y


def _paragraph(ops: list[_Op], measure: _Measure, text: str, y: float, size: float = 9.5) -> float:
    for line in _wrap(measure, text, 483, size):
        ops.append(_Op(56, y, line, size=size))
        y += size * 1.45
    return y


def _policy_pages(measure: _Measure) -> list[list[_Op]]:
    p1: list[_Op] = []
    _letterhead(p1, measure, 1, 2)
    p1.append(_Op(56, 112, "Versicherungsschein", size=18, bold=True))
    p1.append(_Op(56, 132, "Direktversicherung nach § 3 Nr. 63 EStG – MUSTER"))
    y = _rows(
        p1,
        [
            ("Versicherungsnummer", "8815201-7"),
            ("Produkt", "Direktversicherung gemäß § 3 Nr. 63 EStG (MUSTER)"),
            ("Tarif", "DV-Klassik (MUSTER)"),
            ("Versicherungsbeginn", "01.01.2019"),
            ("Ablauf der Versicherung", "01.01.2053"),
            ("Rentenbeginn", "01.01.2053"),
            ("Beitragszahlung bis", "01.12.2052"),
            ("Beitrag", "150,00 EUR"),
            ("Zahlweise", "monatlich"),
        ],
        165,
    )
    y += 12
    p1.append(_Op(56, y, "Versicherungsnehmer", size=11, bold=True))
    y = _rows(
        p1,
        [("Name / Firma", "Muster Bäckerei GmbH"), ("Anschrift", "Backstraße 1"), ("", "12345 Musterstadt")],
        y + 18,
    )
    y += 12
    p1.append(_Op(56, y, "Versicherte Person", size=11, bold=True))
    y = _rows(
        p1,
        [
            ("Name", "Musterfrau, Erika"),
            ("Geburtsdatum", "12.08.1988"),
            ("Staatsangehörigkeit", "deutsch"),
            ("Anschrift", "Beispielweg 7"),
            ("", "12345 Musterstadt"),
        ],
        y + 18,
    )
    _paragraph(
        p1,
        measure,
        "Dieser Versicherungsschein ist ein MUSTER für Test- und Vorführzwecke. Alle Namen, Anschriften, "
        "Nummern und Beträge sind frei erfunden. Es handelt sich um keine echten Daten.",
        y + 20,
    )
    _watermark(p1)

    p2: list[_Op] = []
    _letterhead(p2, measure, 2, 2)
    p2.append(_Op(56, 112, "Versicherungsschein – Fortsetzung", size=14, bold=True))
    p2.append(_Op(56, 150, "Bankverbindung", size=11, bold=True))
    y = _rows(
        p2,
        [
            ("Kontoinhaber", "Muster Bäckerei GmbH"),
            ("IBAN", CUSTOMER_IBAN_PRINTED),
            ("Kreditinstitut", "Beispielbank (MUSTER)"),
        ],
        168,
    )
    p2.append(_Op(56, y + 14, "Hinweise", size=11, bold=True))
    _paragraph(
        p2,
        measure,
        "Der Beitrag ist jeweils zum Ersten eines Monats fällig und wird im Lastschriftverfahren eingezogen. "
        "Der Versicherungsnehmer kann die Versicherung zum Schluss jeder Versicherungsperiode kündigen. "
        "Rentenbeginn und Ablauf richten sich nach den im Versicherungsschein genannten Daten. "
        "Für die Direktversicherung gelten die Allgemeinen Bedingungen (MUSTER). "
        "Die Versicherte Person ist bezugsberechtigt. Beitrag, Leistungen und Fristen in diesem Dokument "
        "sind frei erfunden. Dieses Dokument ist ein MUSTER und enthält keine echten Daten.",
        y + 34,
    )
    _watermark(p2)
    return [p1, p2]


def _amendment_pages(measure: _Measure) -> list[list[_Op]]:
    p: list[_Op] = []
    _letterhead(p, measure, 1, 1)
    p.append(_Op(56, 112, "Nachtrag zum Versicherungsschein", size=16, bold=True))
    p.append(_Op(56, 130, "Änderung gültig ab 01.07.2024 (MUSTER)"))
    y = _rows(
        p,
        [
            ("Versicherungsnummer", "8815201-7"),
            ("Versicherungsnehmer", "Muster Bäckerei GmbH"),
            ("Art der Änderung", "Herabsetzung des Beitrags (MUSTER)"),
            ("Neuer Beitrag", "100,00 EUR"),
            ("Zahlweise", "monatlich"),
        ],
        165,
    )
    y += 12
    p.append(_Op(56, y, "Versicherte Person", size=11, bold=True))
    y = _rows(
        p,
        [("Name", "Musterfrau, Erika"), ("Anschrift", "Neue Straße 3"), ("", "54321 Beispielstadt")],
        y + 18,
    )
    _paragraph(
        p,
        measure,
        "Alle übrigen Vereinbarungen des Versicherungsscheins bleiben unverändert. Dieser Nachtrag ist ein "
        "MUSTER und enthält keine echten Daten.",
        y + 20,
    )
    p.append(_Op(56, y + 70, "Musterstadt, 15.06.2024"))
    _watermark(p)
    return [p]


def _status_pages(measure: _Measure) -> list[list[_Op]]:
    p1: list[_Op] = []
    _letterhead(p1, measure, 1, 2)
    p1.append(
        _Op(56, 140, f"{INSURER_NAME} · Musterallee 1 · 12345 Musterstadt", size=6.5, color=_GRAY_LABEL)
    )
    # Briefanschrift = Vermittler (NICHT der Kunde)
    for i, line in enumerate(
        ["Beispiel Finanz AG (MUSTER)", "z. Hd. Herrn Paul Beispielmann", "Maklerweg 9", "12345 Musterstadt"]
    ):
        p1.append(_Op(56, 162 + i * 12, line))
    right_x = 360
    block = [
        ("Versicherung Nr.", "label", "Versicherung Nr"),
        ("8815201-7", "value", None),
        ("Versicherungsnehmer", "label", None),
        ("Muster Bäckerei GmbH", "value", None),
        ("Versicherte Person", "label", None),
        ("Erika Musterfrau", "value", None),
    ]
    for i, (text, role, ocr) in enumerate(block):
        y = 162 + (i // 2) * 30 + (i % 2) * 12
        p1.append(_Op(right_x, y, text, size=9, role=role, ocr_text=ocr))
    p1.append(_Op(right_x, 262, "Musterstadt, 13. März 2025", size=9))
    p1.append(
        _Op(
            56,
            318,
            "Jährliche Information zur Entwicklung Ihrer Versicherung Nr. 8815201-7",
            size=12,
            bold=True,
        )
    )
    p1.append(_Op(56, 350, "Sehr geehrte Damen und Herren,"))
    body = (
        "mit diesem Schreiben erhalten Sie die jährliche Information zur Entwicklung der Lebensversicherung "
        "Ihres Kunden. Die Werte auf Seite 2 beziehen sich auf den Stichtag 01.01.2025. Bitte leiten Sie "
        "die Information an Ihren Kunden weiter. Dieses Schreiben ist ein MUSTER und enthält keine echten Daten."
    )
    y = _paragraph(p1, measure, body, 372, size=10)
    p1.append(_Op(56, y + 16, "Mit freundlichen Grüßen"))
    p1.append(_Op(56, y + 30, INSURER_HEADER))

    p2: list[_Op] = []
    _letterhead(p2, measure, 2, 2)
    p2.append(_Op(71, 120, "Ihre Versicherung im Überblick (MUSTER)", size=13, bold=True))
    rows = [
        ("Versicherungsnehmer", "Muster Bäckerei GmbH"),
        ("Versicherte Person", "Erika Musterfrau"),
        ("Versicherungsbeginn", "01.01.2019"),
        ("Letzte Beitragsfälligkeit", "01.12.2052"),
        ("Geplanter Rentenbeginn", "01.01.2053"),
        ("Spätester Rentenbeginn", "01.01.2058"),
        ("Vertragsguthaben zum 01.01.2025", "1.170,52 EUR"),
        ("Garantierte Rente (monatlich)", "23,45 EUR"),
    ]
    _rows(p2, rows, 160, label_x=71, value_x=312, pitch=18, value_dy=2.0)
    _paragraph(
        p2,
        measure,
        "Die genannten Werte sind unverbindlich und können sich durch die Überschussbeteiligung ändern. "
        "Der Beitrag wird bis zur letzten Beitragsfälligkeit gezahlt. Dieses Dokument ist ein MUSTER.",
        330,
        size=10,
    )
    return [_with_ocr_errors(p1), _with_ocr_errors(p2)]


# Typische Fehler einer fremden OCR in NICHT kritischen Wörtern des Fließtexts
_OCR_ERRORS = {
    "Information": "lnformation",
    "Ihrer": "lhrer",
    "Lebensversicherung": "Lebensversicherunq",
    "unverbindlich": "unverbindIich",
    "Beitrag": "Beitraq",
}


def _with_ocr_errors(ops: list[_Op]) -> list[_Op]:
    out: list[_Op] = []
    for op in ops:
        in_body = 0.15 * _A4[1] < op.y < 0.82 * _A4[1]
        if op.role == "other" and op.ocr_text is None and in_body:
            noisy = " ".join(_OCR_ERRORS.get(tok, tok) for tok in op.text.split(" "))
            if noisy != op.text:
                op = replace(op, ocr_text=noisy)
        out.append(op)
    return out


# ---------------------------------------------------------------------------
# PDF-Ausgabe
# ---------------------------------------------------------------------------


def _new_doc() -> pymupdf.Document:
    return pymupdf.open()


def _prepare_page(doc: pymupdf.Document) -> pymupdf.Page:
    page = doc.new_page(width=_A4[0], height=_A4[1])
    page.insert_font(fontname="LSR", fontfile=str(_font_path(False)))
    page.insert_font(fontname="LSB", fontfile=str(_font_path(True)))
    return page


def _draw(page: pymupdf.Page, op: _Op, *, text: str | None = None, invisible: bool = False) -> None:
    kwargs: dict[str, object] = {
        "fontname": "LSB" if op.bold else "LSR",
        "fontsize": op.size,
        "color": op.color,
    }
    if invisible:
        kwargs["render_mode"] = 3
    if op.angle:
        pivot = pymupdf.Point(op.x, op.y)
        kwargs["morph"] = (pivot, pymupdf.Matrix(op.angle))  # gegen den Uhrzeigersinn
    page.insert_text(pymupdf.Point(op.x, op.y), text if text is not None else op.text, **kwargs)


def _save(doc: pymupdf.Document, path: Path, title: str) -> None:
    doc.set_metadata(
        {
            "title": title,
            "author": "Lokales Vorlagen-Tool (Mustergenerator)",
            "subject": MUSTER_MARK,
            "keywords": "MUSTER, synthetisch",
            "creator": "Lokales Vorlagen-Tool",
            "producer": "Lokales Vorlagen-Tool",
            "creationDate": _FIXED_PDF_DATE,
            "modDate": _FIXED_PDF_DATE,
        }
    )
    doc.subset_fonts()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path), garbage=4, deflate=True, no_new_id=True)


def _render_ops_pdf(pages: list[list[_Op]]) -> pymupdf.Document:
    doc = _new_doc()
    for ops in pages:
        page = _prepare_page(doc)
        for op in ops:
            _draw(page, op)
    return doc


def _rasterize(page: pymupdf.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _degrade(
    img: Image.Image, *, seed: int, sigma: float, speckle: float, angle: float, blur: float
) -> Image.Image:
    rng = np.random.default_rng(seed)
    if angle:
        img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    arr = np.asarray(img, dtype=np.float32)
    arr = arr + rng.normal(0.0, sigma, arr.shape).astype(np.float32)
    if speckle:
        mask = rng.random(arr.shape) < speckle
        arr[mask] = rng.choice(np.array([40.0, 230.0], dtype=np.float32), size=int(mask.sum()))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="L")


def _jpeg_bytes(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _write_policy(measure: _Measure, path: Path) -> pymupdf.Document:
    doc = _render_ops_pdf(_policy_pages(measure))
    _save(doc, path, "MUSTER Versicherungsschein Direktversicherung")
    doc.close()
    return pymupdf.open(str(path))


def _write_policy_scan(policy: pymupdf.Document, path: Path) -> None:
    img = _degrade(_rasterize(policy[0], 150), seed=1501, sigma=7.0, speckle=0.0008, angle=1.5, blur=0.4)
    data = _jpeg_bytes(img, quality=55)
    doc = _new_doc()
    page = doc.new_page(width=_A4[0], height=_A4[1])
    page.insert_image(page.rect, stream=data)
    _save(doc, path, "MUSTER Versicherungsschein (Scan)")
    doc.close()


def _write_policy_lowres(policy: pymupdf.Document, path: Path) -> None:
    img = _degrade(_rasterize(policy[0], 100), seed=1001, sigma=12.0, speckle=0.002, angle=-0.8, blur=0.5)
    img = img.point(lambda v: (v // 16) * 17)  # weniger Graustufen -> kleinere Datei
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True, dpi=(100, 100))


def _write_status_report(measure: _Measure, path: Path) -> None:
    pages = _status_pages(measure)
    visual = _render_ops_pdf(pages)
    doc = _new_doc()
    for idx, ops in enumerate(pages):
        img = _degrade(
            _rasterize(visual[idx], 150), seed=2025 + idx, sigma=6.0, speckle=0.0006, angle=0.0, blur=0.3
        )
        page = _prepare_page(doc)
        page.insert_image(page.rect, stream=_jpeg_bytes(img, quality=50))
        # Fremde OCR-Textebene: erst alle Labels, dann alle Werte, dann der Rest
        ordered = (
            [op for op in ops if op.role == "label"]
            + [op for op in ops if op.role == "value"]
            + [op for op in ops if op.role not in ("label", "value")]
        )
        for op in ordered:
            _draw(page, op, text=op.ocr_text if op.ocr_text is not None else op.text, invisible=True)
    visual.close()
    _save(doc, path, "MUSTER Jährliche Information (Scan mit OCR-Textebene)")
    doc.close()


def _write_amendment(measure: _Measure, path: Path) -> None:
    doc = _render_ops_pdf(_amendment_pages(measure))
    _save(doc, path, "MUSTER Nachtrag zum Versicherungsschein")
    doc.close()


# ---------------------------------------------------------------------------
# Portal-Screenshot (PNG) und passende Wortboxen
# ---------------------------------------------------------------------------

_PORTAL_FONT_PX = 16
_PORTAL_ROWS: list[tuple[str, str, str, str]] = [
    ("Produktbezeichnung", "Basisrente Invest (MUSTER)", "Unternehmen", "Beispiel Lebensversicherung AG"),
    ("Vertragsinhaber", "Max Beispiel", "Vertragsnummer", "5550123-4"),
    ("Laufzeit", "01.02.2022 bis 01.08.2065", "Antragsnummer(n)", "123456789"),
    ("Beitrag", "", "Abschlussvermittler", "Beispiel Finanz AG (MUSTER)"),
    ("Vertragstyp", "Eigenvertrag", "Letzte Aktualisierung", "30.09.2026"),
    ("Status", "Ruhend", "Rückkaufswert", "Unbekannt"),
    ("", "", "Rückkaufswert vom", "Unbekannt"),
]
_PORTAL_SECTION_ROWS: list[tuple[str, str, str, str]] = [
    ("Name", "Max Beispiel", "Rente (monatlich)", "Unbekannt"),
]
_PORTAL_COLS = (24, 230, 620, 830)
_PORTAL_LABEL_RGB = (107, 114, 128)
_PORTAL_VALUE_RGB = (17, 24, 39)


@dataclass(frozen=True)
class _PortalText:
    x: int
    baseline: int
    text: str
    bold: bool
    size: int
    rgb: tuple[int, int, int]


def _portal_layout() -> list[_PortalText]:
    items: list[_PortalText] = [_PortalText(24, 44, "Vertragsdetails", True, 22, _PORTAL_VALUE_RGB)]
    y = 92
    for row in _PORTAL_ROWS:
        for col, text in zip(_PORTAL_COLS, row, strict=True):
            if text:
                is_label = col in (_PORTAL_COLS[0], _PORTAL_COLS[2])
                rgb = _PORTAL_LABEL_RGB if is_label else _PORTAL_VALUE_RGB
                items.append(_PortalText(col, y, text, False, _PORTAL_FONT_PX, rgb))
        y += 36
    y += 14
    items.append(_PortalText(24, y, "Versicherte Person", True, 18, _PORTAL_VALUE_RGB))
    y += 40
    for row in _PORTAL_SECTION_ROWS:
        for col, text in zip(_PORTAL_COLS, row, strict=True):
            if text:
                is_label = col in (_PORTAL_COLS[0], _PORTAL_COLS[2])
                rgb = _PORTAL_LABEL_RGB if is_label else _PORTAL_VALUE_RGB
                items.append(_PortalText(col, y, text, False, _PORTAL_FONT_PX, rgb))
        y += 36
    return items


def _pil_font(size: int, bold: bool) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_font_path(bold)), size)


def _write_portal_png(path: Path) -> None:
    img = Image.new("RGB", PORTAL_SIZE, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.line([(24, 62), (PORTAL_SIZE[0] - 24, 62)], fill=(229, 231, 235), width=1)
    draw.line([(24, 326), (PORTAL_SIZE[0] - 24, 326)], fill=(229, 231, 235), width=1)
    for item in _portal_layout():
        draw.text(
            (item.x, item.baseline),
            item.text,
            font=_pil_font(item.size, item.bold),
            fill=item.rgb,
            anchor="ls",
        )
    # MUSTER-Stempel (klein, Ecke oben rechts)
    stamp_font = _pil_font(13, True)
    stamp_w = int(draw.textlength(MUSTER_MARK, font=stamp_font))
    x1, y0 = PORTAL_SIZE[0] - 20, 14
    draw.rectangle([x1 - stamp_w - 16, y0, x1, y0 + 26], outline=(190, 0, 0), width=2)
    draw.text((x1 - stamp_w - 8, y0 + 19), MUSTER_MARK, font=stamp_font, fill=(190, 0, 0), anchor="ls")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True, dpi=(int(PORTAL_DPI), int(PORTAL_DPI)))


def portal_words(dpi: float = PORTAL_DPI) -> list[Word]:
    """Wortboxen des Portal-Screenshots (wie eine ideale OCR sie liefern würde), in Punkten.

    Ermöglicht Extraktionstests ohne Texterkennung. Umrechnung: Punkte = Pixel × 72 / dpi.
    """
    scale = 72.0 / dpi
    words: list[Word] = []
    for item in _portal_layout():
        font = _pil_font(item.size, item.bold)
        pos = 0
        for token in item.text.split(" "):
            start = item.text.index(token, pos)
            pos = start + len(token)
            if not token:
                continue
            x = item.x + font.getlength(item.text[:start])
            left, top, right, bottom = font.getbbox(token, anchor="ls")
            words.append(
                Word(
                    text=token,
                    bbox=BoundingBox(
                        x0=(x + left) * scale,
                        y0=(item.baseline + top) * scale,
                        x1=(x + right) * scale,
                        y1=(item.baseline + bottom) * scale,
                    ),
                    confidence=0.95,
                )
            )
    return words


def portal_page_text(dpi: float = PORTAL_DPI) -> PageText:
    """``PageText`` des Portal-Screenshots ohne OCR (für Tests und Demonstration)."""
    words = portal_words(dpi)
    scale = 72.0 / dpi
    return PageText(
        page_number=1,
        width=PORTAL_SIZE[0] * scale,
        height=PORTAL_SIZE[1] * scale,
        method=ExtractionMethod.TESSERACT,
        origin=TextLayerOrigin.OCR,
        words=words,
        text=" ".join(w.text for w in words),
        mean_confidence=0.95,
        engine_version="synthetisch (ideale Wortboxen)",
        source_dpi=dpi,
    )


# ---------------------------------------------------------------------------
# Öffentliche Funktion
# ---------------------------------------------------------------------------


def sample_metadata() -> dict[str, SampleDocMeta]:
    """Beschreibung aller Musterdateien (Dokumenttyp, Datum, Gültig-ab, Vertragsnummer …)."""
    return {
        FILE_POLICY: SampleDocMeta(
            doc_type=DocumentType.POLICY,
            description="Versicherungsschein Direktversicherung (digital, 2 Seiten)",
            document_date=date(2019, 1, 1),
            contract_number="8815201-7",
            page_count=2,
        ),
        FILE_AMENDMENT: SampleDocMeta(
            doc_type=DocumentType.AMENDMENT,
            description="Nachtrag: Beitrag herabgesetzt, neue Anschrift der versicherten Person",
            document_date=date(2024, 6, 15),
            effective_date=date(2024, 7, 1),
            contract_number="8815201-7",
        ),
        FILE_PORTAL: SampleDocMeta(
            doc_type=DocumentType.PORTAL_OVERVIEW,
            description="Portal-Vertragsübersicht (Screenshot) eines ANDEREN Vertrags",
            needs_ocr=True,
            contract_number="5550123-4",
        ),
        FILE_POLICY_SCAN: SampleDocMeta(
            doc_type=DocumentType.POLICY,
            description="Seite 1 des Versicherungsscheins als Scan (150 dpi, leicht gedreht)",
            document_date=date(2019, 1, 1),
            needs_ocr=True,
            contract_number="8815201-7",
        ),
        FILE_POLICY_SCAN_LOWRES: SampleDocMeta(
            doc_type=DocumentType.POLICY,
            description="Seite 1 des Versicherungsscheins als Bild (ca. 100 dpi, stärker verrauscht)",
            document_date=date(2019, 1, 1),
            needs_ocr=True,
            contract_number="8815201-7",
        ),
        FILE_STATUS_REPORT: SampleDocMeta(
            doc_type=DocumentType.STATUS_REPORT,
            description="Jährliche Information (Scan mit fremder OCR-Textebene, 2 Seiten)",
            document_date=date(2025, 3, 13),
            embedded_ocr=True,
            contract_number="8815201-7",
            page_count=2,
        ),
    }


def generate_sample_set(out_dir: Path) -> SampleSet:
    """Alle Musterdateien deterministisch nach ``out_dir`` schreiben (inkl. ``<datei>.truth.json``)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    measure = _Measure()
    files = {name: out_dir / name for name in sample_metadata()}

    policy = _write_policy(measure, files[FILE_POLICY])
    try:
        _write_policy_scan(policy, files[FILE_POLICY_SCAN])
        _write_policy_lowres(policy, files[FILE_POLICY_SCAN_LOWRES])
    finally:
        policy.close()
    _write_amendment(measure, files[FILE_AMENDMENT])
    _write_portal_png(files[FILE_PORTAL])
    _write_status_report(measure, files[FILE_STATUS_REPORT])

    truth = {
        FILE_POLICY: {**_POLICY_PAGE1_TRUTH, **_POLICY_PAGE2_TRUTH},
        FILE_AMENDMENT: dict(_AMENDMENT_TRUTH),
        FILE_PORTAL: dict(_PORTAL_TRUTH),
        FILE_POLICY_SCAN: dict(_POLICY_PAGE1_TRUTH),
        FILE_POLICY_SCAN_LOWRES: dict(_POLICY_PAGE1_TRUTH),
        FILE_STATUS_REPORT: dict(_STATUS_TRUTH),
    }
    for name, values in truth.items():
        target = out_dir / f"{name}.truth.json"
        target.write_text(
            json.dumps(dict(sorted(values.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    log.info("Musterdateien erzeugt: %d Dateien", len(files))
    return SampleSet(files=files, ground_truth=truth, metadata=sample_metadata())
