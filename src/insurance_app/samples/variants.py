"""Benchmark-Varianten: synthetische Testdokumente mit Sollwerten für die Extraktionsmessung.

Wozu?
-----
``generate_benchmark_set()`` erzeugt rund 20 künstliche Dokumente, mit denen sich
messen lässt, wie gut die Texterkennung (OCR) und die regelbasierte Extraktion mit
schwierigen Vorlagen zurechtkommen. Jede Datei hat eine Musterlösung
(``<datei>.truth.json``): Feldschlüssel -> erwarteter, normalisierter Wert.

Variiert werden:

* **Rauschen** (ohne / niedrig / hoch) bei 300 dpi,
* **Schräglage** (0°, 1,5°, 3°, 4,5°),
* **Auflösung** (300 / 200 / 150 / 100 dpi),
* **JPEG-Artefakte** (starke Kompression, auch beim Portal-Screenshot),
* ein **um 90° gedrehter** Scan,
* ein **Tabellenlayout** (Beitragsverlauf mit mehreren Zeilen),
* eine **mehrseitige Police** (3 Seiten) mit **Nachtrag** sowie eine Standmitteilung als reiner Scan,
* **verwechselbare Zeichen** (0/O, 1/I/l): Vertragsnummer „1011010-1“, Name „Olli Illner“,
  IBAN mit vielen Nullen und Einsen.

Datenschutz: Alle Inhalte sind frei erfunden. Jede Seite trägt sichtbar
„MUSTER – KEINE ECHTDATEN“; Bilddateien zusätzlich in den Metadaten. Versicherer ist die
fiktive „Beispiel Lebensversicherung AG (MUSTER)“. Es werden nur veröffentlichte
Beispiel-IBANs verwendet.

Die Erzeugung ist deterministisch (fester Seed, feste PDF-Metadaten): gleicher Seed,
gleiche Bibliotheksversionen -> byteidentische Dateien.

Die Layouts der Musterfall-Dokumente stammen aus ``samples.generator`` (wiederverwendet,
nicht verändert).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, PngImagePlugin

from insurance_app.core.models import DocumentType

from .generator import (
    _A4,
    _GRAY_LABEL,
    _POLICY_PAGE1_TRUTH,
    _PORTAL_TRUTH,
    _STATUS_TRUTH,
    CUSTOMER_IBAN,
    CUSTOMER_IBAN_PRINTED,
    INSURER_NAME,
    MUSTER_MARK,
    PORTAL_DPI,
    _degrade,
    _draw,
    _letterhead,
    _Measure,
    _new_doc,
    _Op,
    _paragraph,
    _policy_pages,
    _prepare_page,
    _rasterize,
    _rows,
    _save,
    _status_pages,
    _watermark,
    _write_portal_png,
)

__all__ = [
    "CATEGORY_LABELS",
    "CONFUSABLE_IBAN",
    "MANIFEST_NAME",
    "NOISE_LEVELS",
    "BenchmarkItem",
    "generate_benchmark_set",
    "load_benchmark_set",
    "variant_ids",
]

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1

# Veröffentlichte Beispiel-IBAN (Postbank-Testkonto) mit vielen 0/1 – gültige Prüfziffer
CONFUSABLE_IBAN = "DE02100100100006820101"
CONFUSABLE_IBAN_PRINTED = "DE02 1001 0010 0006 8201 01"

#: Rauschstufen für ``generator._degrade`` (Standardabweichung, Salz-und-Pfeffer-Anteil, Unschärfe)
NOISE_LEVELS: dict[str, dict[str, float]] = {
    "ohne": {"sigma": 0.0, "speckle": 0.0, "blur": 0.0},
    "niedrig": {"sigma": 6.0, "speckle": 0.0005, "blur": 0.3},
    "hoch": {"sigma": 24.0, "speckle": 0.004, "blur": 0.9},
}

#: Kategorien (Reihenfolge = Reihenfolge im Bericht)
CATEGORY_LABELS: dict[str, str] = {
    "digital": "Digitale PDF mit Textebene (ohne OCR)",
    "rauschen": "Scan 300 dpi – Bildrauschen ohne / niedrig / hoch",
    "schraeglage": "Scan 300 dpi – Schräglage 0° / 1,5° / 3° / 4,5°",
    "aufloesung": "Scan – Auflösung 300 / 200 / 150 / 100 dpi",
    "jpeg": "Starke JPEG-Kompression (Foto/Screenshot)",
    "drehung": "Scan um 90° gedreht",
    "tabelle": "Tabellenlayout (Beitragsverlauf)",
    "verwechslung": "Verwechselbare Zeichen (0/O, 1/I/l)",
    "mehrseitig": "Mehrseitige Dokumente (Police 3 S., Standmitteilung 2 S.)",
}

_SCANNER_JPEG_QUALITY = 80  # übliche Scanner-Voreinstellung
_LINE_GRAY = (0.45, 0.45, 0.45)


# ---------------------------------------------------------------------------
# Öffentliche Datenstruktur
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkItem:
    """Eine Benchmark-Datei mit Musterlösung.

    ``ground_truth``: Feldschlüssel -> normalisierter Sollwert (Datum ``YYYY-MM-DD``,
    Betrag ``1234.56``, IBAN kompakt, Zahlweise als Schlüssel wie ``monthly``).
    ``meta``: Beschreibung der Variante (``id``, ``category``, ``also_in``, ``layout``,
    ``format``, ``dpi``, ``noise``, ``skew_deg``, ``jpeg_quality``, ``rotation_deg``,
    ``page_count``, ``needs_ocr``, ``doc_type``, ``sha256`` …).
    """

    file: Path
    ground_truth: dict[str, str]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return str(self.meta.get("id", self.file.name))

    @property
    def category(self) -> str:
        return str(self.meta.get("category", ""))

    @property
    def categories(self) -> tuple[str, ...]:
        """Hauptkategorie und weitere Kategorien, in denen die Datei als Referenz mitzählt."""
        return (self.category, *[str(c) for c in self.meta.get("also_in", [])])

    @property
    def needs_ocr(self) -> bool:
        return bool(self.meta.get("needs_ocr", False))


# ---------------------------------------------------------------------------
# Seiten mit optionalen Tabellenlinien
# ---------------------------------------------------------------------------


@dataclass
class _Page:
    ops: list[_Op]
    lines: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class _Layout:
    name: str
    doc_type: DocumentType
    pages: Callable[[_Measure], list[_Page]]
    truth: dict[str, str]
    title: str
    document_date: date | None = None
    effective_date: date | None = None
    contract_number: str | None = None
    page_numbers: tuple[int, ...] | None = None  # verwendete Seiten (1-basiert), None = alle


def _section(ops: list[_Op], title: str, y: float) -> float:
    ops.append(_Op(56, y, title, size=11, bold=True))
    return y + 18


# -- Police über drei Seiten -------------------------------------------------

_P3_NUMBER = "4471203-5"


def _police3_pages(measure: _Measure) -> list[_Page]:
    p1: list[_Op] = []
    _letterhead(p1, measure, 1, 3)
    p1.append(_Op(56, 112, "Versicherungsschein", size=18, bold=True))
    p1.append(_Op(56, 132, "Private Rentenversicherung – MUSTER"))
    y = _section(p1, "Vertragsdaten", 162)
    y = _rows(
        p1,
        [
            ("Versicherungsnummer", _P3_NUMBER),
            ("Produkt", "Private Rentenversicherung (MUSTER)"),
            ("Tarif", "PR-Flex (MUSTER)"),
            ("Versicherungsbeginn", "01.04.2021"),
            ("Ablauf der Versicherung", "01.04.2056"),
            ("Rentenbeginn", "01.04.2056"),
            ("Beitragszahlung bis", "01.03.2056"),
        ],
        y,
    )
    y = _section(p1, "Versicherungsnehmer", y + 12)
    y = _rows(p1, [("Name", "Musterfrau, Erika"), ("Anschrift", "Musterweg 12"), ("", "12345 Musterstadt")], y)
    _paragraph(
        p1,
        measure,
        "Die Angaben zur versicherten Person und zur Bankverbindung stehen auf Seite 2, Beitrag und "
        "Zahlweise auf Seite 3. Dieses Dokument ist ein MUSTER und enthält keine echten Daten.",
        y + 20,
    )
    _watermark(p1)

    p2: list[_Op] = []
    _letterhead(p2, measure, 2, 3)
    p2.append(_Op(56, 112, "Versicherungsschein – Fortsetzung", size=14, bold=True))
    p2.append(_Op(56, 128, f"Versicherungsschein-Nr. {_P3_NUMBER}", size=9, color=_GRAY_LABEL))
    y = _section(p2, "Versicherte Person", 160)
    y = _rows(
        p2,
        [
            ("Name", "Musterfrau, Erika"),
            ("Geburtsdatum", "03.05.1985"),
            ("Staatsangehörigkeit", "deutsch"),
            ("Anschrift", "Musterweg 12"),
            ("", "12345 Musterstadt"),
        ],
        y,
    )
    y = _section(p2, "Bankverbindung", y + 12)
    y = _rows(
        p2,
        [
            ("Kontoinhaber", "Erika Musterfrau"),
            ("IBAN", CUSTOMER_IBAN_PRINTED),
            ("Kreditinstitut", "Beispielbank (MUSTER)"),
        ],
        y,
    )
    _paragraph(
        p2,
        measure,
        "Der Beitrag wird im Lastschriftverfahren von dem oben genannten Konto eingezogen. "
        "Alle Angaben sind frei erfunden (MUSTER).",
        y + 20,
    )
    _watermark(p2)

    p3: list[_Op] = []
    _letterhead(p3, measure, 3, 3)
    p3.append(_Op(56, 112, "Versicherungsschein – Fortsetzung", size=14, bold=True))
    p3.append(_Op(56, 128, f"Versicherungsschein-Nr. {_P3_NUMBER}", size=9, color=_GRAY_LABEL))
    y = _section(p3, "Beitrag und Zahlweise", 160)
    y = _rows(p3, [("Beitrag", "85,50 EUR"), ("Zahlweise", "monatlich"), ("Zahlungsbeginn", "01.04.2021")], y)
    y = _section(p3, "Hinweise", y + 12)
    _paragraph(
        p3,
        measure,
        "Der Beitrag ist jeweils zum Ersten eines Monats fällig. Sie können die Versicherung zum Schluss "
        "jeder Versicherungsperiode kündigen. Rentenbeginn und Ablauf richten sich nach den Angaben auf "
        "Seite 1. Beträge, Daten und Namen in diesem Dokument sind frei erfunden. Dieses Dokument ist ein "
        "MUSTER und enthält keine echten Daten.",
        y,
    )
    _watermark(p3)
    return [_Page(p1), _Page(p2), _Page(p3)]


_POLICE3_TRUTH: dict[str, str] = {
    "contract.number": _P3_NUMBER,
    "insurer.name": INSURER_NAME,
    "contract.product": "Private Rentenversicherung (MUSTER)",
    "contract.tariff": "PR-Flex (MUSTER)",
    "contract.start_date": "2021-04-01",
    "contract.end_date": "2056-04-01",
    "contract.pension_start_date": "2056-04-01",
    "contract.premium_end_date": "2056-03-01",
    "policyholder.name": "Musterfrau",
    "policyholder.first_name": "Erika",
    "policyholder.street": "Musterweg",
    "policyholder.house_number": "12",
    "policyholder.postal_code": "12345",
    "policyholder.city": "Musterstadt",
    "insured.last_name": "Musterfrau",
    "insured.first_name": "Erika",
    "insured.birth_date": "1985-05-03",
    "insured.nationality": "deutsch",
    "insured.street": "Musterweg",
    "insured.house_number": "12",
    "insured.postal_code": "12345",
    "insured.city": "Musterstadt",
    "bank.account_holder": "Erika Musterfrau",
    "bank.iban": CUSTOMER_IBAN,
    "bank.institution": "Beispielbank (MUSTER)",
    "premium.amount": "85.50",
    "premium.currency": "EUR",
    "premium.frequency": "monthly",
    "premium.start_date": "2021-04-01",
}


# -- Nachtrag zur dreiseitigen Police ----------------------------------------


def _nachtrag_pages(measure: _Measure) -> list[_Page]:
    p: list[_Op] = []
    _letterhead(p, measure, 1, 1)
    p.append(_Op(56, 112, "Nachtrag zum Versicherungsschein", size=16, bold=True))
    p.append(_Op(56, 130, "Änderung gültig ab 01.10.2025 (MUSTER)"))
    y = _rows(
        p,
        [
            ("Versicherungsnummer", _P3_NUMBER),
            ("Versicherungsnehmer", "Erika Musterfrau"),
            ("Art der Änderung", "Erhöhung des Beitrags (MUSTER)"),
            ("Neuer Beitrag", "95,00 EUR"),
            ("Zahlweise", "monatlich"),
        ],
        165,
    )
    y = _section(p, "Versicherte Person", y + 12)
    y = _rows(
        p, [("Name", "Musterfrau, Erika"), ("Anschrift", "Am Beispielpark 4"), ("", "54321 Beispielstadt")], y
    )
    _paragraph(
        p,
        measure,
        "Alle übrigen Vereinbarungen des Versicherungsscheins bleiben unverändert. Dieser Nachtrag ist ein "
        "MUSTER und enthält keine echten Daten.",
        y + 20,
    )
    p.append(_Op(56, y + 70, "Musterstadt, 15.09.2025"))
    _watermark(p)
    return [_Page(p)]


_NACHTRAG_TRUTH: dict[str, str] = {
    "contract.number": _P3_NUMBER,
    "insurer.name": INSURER_NAME,
    "policyholder.name": "Musterfrau",
    "policyholder.first_name": "Erika",
    "contract.changes": "Erhöhung des Beitrags (MUSTER)",
    "premium.amount": "95.00",
    "premium.currency": "EUR",
    "premium.frequency": "monthly",
    "insured.last_name": "Musterfrau",
    "insured.first_name": "Erika",
    "insured.street": "Am Beispielpark",
    "insured.house_number": "4",
    "insured.postal_code": "54321",
    "insured.city": "Beispielstadt",
}


# -- Beitragsmitteilung mit Tabellen -----------------------------------------

_TABLE_NUMBER = "6602381-9"


def _table(
    page: _Page,
    top: float,
    columns: list[float],
    right: float,
    header: list[str],
    rows: list[list[str]],
    pitch: float = 17.0,
) -> float:
    """Tabelle mit Rahmenlinien: Kopfzeile (Beschriftungen) und Datenzeilen darunter."""
    lefts = [c - 6 for c in columns] + [right]
    y = top
    page.lines.append((lefts[0], y, right, y))
    baseline = y + 12
    for x, text in zip(columns, header, strict=True):
        page.ops.append(_Op(x, baseline, text, size=9, color=_GRAY_LABEL, role="label"))
    y += pitch
    page.lines.append((lefts[0], y, right, y))
    for row in rows:
        baseline = y + 12
        for x, text in zip(columns, row, strict=True):
            page.ops.append(_Op(x, baseline, text, size=9.5, role="value"))
        y += pitch
        page.lines.append((lefts[0], y, right, y))
    for x in lefts:
        page.lines.append((x, top, x, y))
    return y


def _tabelle_pages(measure: _Measure) -> list[_Page]:
    page = _Page([])
    ops = page.ops
    _letterhead(ops, measure, 1, 1)
    ops.append(_Op(56, 112, "Beitragsmitteilung 2026", size=16, bold=True))
    ops.append(_Op(56, 130, "Ihre Beiträge im Überblick (MUSTER)"))
    y = _rows(
        ops,
        [
            ("Versicherungsnummer", _TABLE_NUMBER),
            ("Versicherungsnehmer", "Muster Tischlerei GmbH"),
            ("Versicherte Person", "Max Mustermann"),
        ],
        162,
    )
    y = _section(ops, "Vertragsdaten", y + 10)
    cols = [62.0, 186.0, 296.0, 426.0]
    y = _table(
        page,
        y - 6,
        cols,
        539.0,
        ["Versicherungsbeginn", "Ablauf", "Tarif", "Zahlweise"],
        [["01.01.2022", "01.01.2057", "BU-Plus (MUSTER)", "monatlich"]],
    )
    y = _section(ops, "Beitragsverlauf", y + 26)
    y = _table(
        page,
        y - 6,
        cols[:3],
        426.0,
        ["Gültig ab", "Beitrag", "Zahlweise"],
        [
            ["01.01.2022", "120,00 EUR", "monatlich"],
            ["01.01.2024", "130,00 EUR", "monatlich"],
            ["01.01.2026", "140,00 EUR", "monatlich"],
        ],
    )
    _paragraph(
        ops,
        measure,
        "Maßgeblich ist der zuletzt gültige Beitrag in der Tabelle. Diese Mitteilung ist ein MUSTER und "
        "enthält keine echten Daten.",
        y + 26,
    )
    _watermark(ops)
    return [page]


_TABELLE_TRUTH: dict[str, str] = {
    "contract.number": _TABLE_NUMBER,
    "insurer.name": INSURER_NAME,
    "policyholder.name": "Muster Tischlerei GmbH",
    "policyholder.legal_form": "GmbH",
    "insured.last_name": "Mustermann",
    "insured.first_name": "Max",
    "contract.start_date": "2022-01-01",
    "contract.end_date": "2057-01-01",
    "contract.tariff": "BU-Plus (MUSTER)",
    "premium.frequency": "monthly",
    "premium.amount": "140.00",  # zuletzt gültige Tabellenzeile (ab 01.01.2026)
    "premium.currency": "EUR",
}


# -- Verwechselbare Zeichen ----------------------------------------------------

_CONF_NUMBER = "1011010-1"


def _verwechslung_pages(measure: _Measure) -> list[_Page]:
    p: list[_Op] = []
    _letterhead(p, measure, 1, 1)
    p.append(_Op(56, 112, "Versicherungsschein", size=18, bold=True))
    p.append(_Op(56, 132, "Fondsgebundene Rentenversicherung – MUSTER"))
    y = _rows(
        p,
        [
            ("Versicherungsnummer", _CONF_NUMBER),
            ("Antragsnummer", "0110100111"),
            ("Produkt", "Fondsgebundene Rentenversicherung (MUSTER)"),
            ("Tarif", "FR-10 (MUSTER)"),
            ("Versicherungsbeginn", "01.10.2011"),
            ("Ablauf der Versicherung", "01.11.2041"),
            ("Beitrag", "101,10 EUR"),
            ("Zahlweise", "vierteljährlich"),
        ],
        162,
    )
    y = _section(p, "Versicherungsnehmer", y + 10)
    y = _rows(p, [("Name", "Illner, Olli"), ("Anschrift", "Ilmweg 11"), ("", "10110 Oberillstadt")], y)
    y = _section(p, "Versicherte Person", y + 10)
    y = _rows(
        p,
        [
            ("Name", "Illner, Olli"),
            ("Geburtsdatum", "11.01.1970"),
            ("Anschrift", "Ilmweg 11"),
            ("", "10110 Oberillstadt"),
        ],
        y,
    )
    y = _section(p, "Bankverbindung", y + 10)
    y = _rows(
        p,
        [
            ("Kontoinhaber", "Olli Illner"),
            ("IBAN", CONFUSABLE_IBAN_PRINTED),
            ("Kreditinstitut", "Beispielbank (MUSTER)"),
        ],
        y,
    )
    _paragraph(
        p,
        measure,
        "Dieser Versicherungsschein ist ein MUSTER. Namen, Nummern und Beträge sind frei erfunden und "
        "enthalten absichtlich leicht verwechselbare Zeichen (0/O, 1/I/l).",
        y + 16,
    )
    _watermark(p)
    return [_Page(p)]


_VERWECHSLUNG_TRUTH: dict[str, str] = {
    "contract.number": _CONF_NUMBER,
    "contract.application_number": "0110100111",
    "insurer.name": INSURER_NAME,
    "contract.product": "Fondsgebundene Rentenversicherung (MUSTER)",
    "contract.tariff": "FR-10 (MUSTER)",
    "contract.start_date": "2011-10-01",
    "contract.end_date": "2041-11-01",
    "premium.amount": "101.10",
    "premium.currency": "EUR",
    "premium.frequency": "quarterly",
    "policyholder.name": "Illner",
    "policyholder.first_name": "Olli",
    "policyholder.street": "Ilmweg",
    "policyholder.house_number": "11",
    "policyholder.postal_code": "10110",
    "policyholder.city": "Oberillstadt",
    "insured.last_name": "Illner",
    "insured.first_name": "Olli",
    "insured.birth_date": "1970-01-11",
    "insured.street": "Ilmweg",
    "insured.house_number": "11",
    "insured.postal_code": "10110",
    "insured.city": "Oberillstadt",
    "bank.account_holder": "Olli Illner",
    "bank.iban": CONFUSABLE_IBAN,
    "bank.institution": "Beispielbank (MUSTER)",
}


# -- Layouts aus dem Musterfall-Generator ------------------------------------


def _musterfall_policy_pages(measure: _Measure) -> list[_Page]:
    return [_Page(ops) for ops in _policy_pages(measure)]


def _standmitteilung_pages(measure: _Measure) -> list[_Page]:
    return [_Page(ops) for ops in _status_pages(measure)]


_LAYOUTS: dict[str, _Layout] = {
    "police_musterfall": _Layout(
        name="police_musterfall",
        doc_type=DocumentType.POLICY,
        pages=_musterfall_policy_pages,
        truth=dict(_POLICY_PAGE1_TRUTH),
        title="MUSTER Versicherungsschein (Scan-Variante)",
        document_date=date(2019, 1, 1),
        contract_number="8815201-7",
        page_numbers=(1,),
    ),
    "police_3seiten": _Layout(
        name="police_3seiten",
        doc_type=DocumentType.POLICY,
        pages=_police3_pages,
        truth=_POLICE3_TRUTH,
        title="MUSTER Versicherungsschein Rentenversicherung (3 Seiten)",
        document_date=date(2021, 4, 1),
        contract_number=_P3_NUMBER,
    ),
    "nachtrag": _Layout(
        name="nachtrag",
        doc_type=DocumentType.AMENDMENT,
        pages=_nachtrag_pages,
        truth=_NACHTRAG_TRUTH,
        title="MUSTER Nachtrag Beitragserhöhung",
        document_date=date(2025, 9, 15),
        effective_date=date(2025, 10, 1),
        contract_number=_P3_NUMBER,
    ),
    "tabelle": _Layout(
        name="tabelle",
        doc_type=DocumentType.PREMIUM_NOTICE,
        pages=_tabelle_pages,
        truth=_TABELLE_TRUTH,
        title="MUSTER Beitragsmitteilung mit Tabellen",
        contract_number=_TABLE_NUMBER,
    ),
    "verwechslung": _Layout(
        name="verwechslung",
        doc_type=DocumentType.POLICY,
        pages=_verwechslung_pages,
        truth=_VERWECHSLUNG_TRUTH,
        title="MUSTER Versicherungsschein mit verwechselbaren Zeichen",
        document_date=date(2011, 10, 1),
        contract_number=_CONF_NUMBER,
    ),
    "standmitteilung": _Layout(
        name="standmitteilung",
        doc_type=DocumentType.STATUS_REPORT,
        pages=_standmitteilung_pages,
        truth=dict(_STATUS_TRUTH),
        title="MUSTER Jährliche Information (reiner Scan)",
        document_date=date(2025, 3, 13),
        contract_number="8815201-7",
    ),
    # Sonderfall: Bild wird von generator._write_portal_png gezeichnet
    "portal": _Layout(
        name="portal",
        doc_type=DocumentType.PORTAL_OVERVIEW,
        pages=lambda _m: [],
        truth=dict(_PORTAL_TRUTH),
        title="MUSTER Portal-Vertragsübersicht",
        contract_number="5550123-4",
    ),
}


# ---------------------------------------------------------------------------
# Variantenliste
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Spec:
    id: str
    category: str
    layout: str
    description: str
    fmt: str  # "pdf_text" | "pdf_scan" | "png" | "jpeg"
    dpi: int | None = None
    noise: str = "ohne"
    skew: float = 0.0
    jpeg_quality: int | None = None  # None -> verlustfrei (PNG)
    rotation: int = 0  # Drehung im Uhrzeigersinn (Grad)
    also_in: tuple[str, ...] = ()


_PM = "police_musterfall"
_JQ = _SCANNER_JPEG_QUALITY

_SPECS: tuple[_Spec, ...] = (
    # -- digital: Textebene, keine OCR --------------------------------------
    _Spec(
        "digital_police_3seiten",
        "digital",
        "police_3seiten",
        "Police über 3 Seiten, digital erzeugt",
        "pdf_text",
        also_in=("mehrseitig",),
    ),
    _Spec("digital_nachtrag", "digital", "nachtrag", "Nachtrag zur 3-seitigen Police, digital", "pdf_text"),
    _Spec(
        "digital_tabelle",
        "digital",
        "tabelle",
        "Beitragsmitteilung mit Tabellen, digital",
        "pdf_text",
        also_in=("tabelle",),
    ),
    _Spec(
        "digital_verwechslung",
        "digital",
        "verwechslung",
        "Police mit verwechselbaren Zeichen, digital",
        "pdf_text",
        also_in=("verwechslung",),
    ),
    # -- Rauschen (300 dpi, gerade) -----------------------------------------
    _Spec(
        "rauschen_ohne",
        "rauschen",
        _PM,
        "Scan 300 dpi, ohne Rauschen, verlustfrei",
        "pdf_scan",
        dpi=300,
        noise="ohne",
    ),
    _Spec(
        "rauschen_niedrig",
        "rauschen",
        _PM,
        "Scan 300 dpi, leichtes Rauschen (Referenz für Schräglage/Auflösung)",
        "pdf_scan",
        dpi=300,
        noise="niedrig",
        jpeg_quality=_JQ,
        also_in=("schraeglage", "aufloesung"),
    ),
    _Spec(
        "rauschen_hoch",
        "rauschen",
        _PM,
        "Scan 300 dpi, starkes Rauschen und Unschärfe",
        "pdf_scan",
        dpi=300,
        noise="hoch",
        jpeg_quality=_JQ,
    ),
    # -- Schräglage -----------------------------------------------------------
    *(
        _Spec(
            f"schraeglage_{str(angle).replace('.', '_')}",
            "schraeglage",
            _PM,
            f"Scan 300 dpi, {str(angle).replace('.', ',')}° schräg",
            "pdf_scan",
            dpi=300,
            noise="niedrig",
            skew=angle,
            jpeg_quality=_JQ,
        )
        for angle in (1.5, 3.0, 4.5)
    ),
    # -- Auflösung --------------------------------------------------------------
    _Spec(
        "aufloesung_200",
        "aufloesung",
        _PM,
        "Scan 200 dpi, leichtes Rauschen",
        "pdf_scan",
        dpi=200,
        noise="niedrig",
        jpeg_quality=_JQ,
    ),
    _Spec(
        "aufloesung_150",
        "aufloesung",
        _PM,
        "Scan 150 dpi, leichtes Rauschen",
        "pdf_scan",
        dpi=150,
        noise="niedrig",
        jpeg_quality=_JQ,
    ),
    _Spec(
        "aufloesung_100",
        "aufloesung",
        _PM,
        "Bilddatei (PNG) 100 dpi, leichtes Rauschen",
        "png",
        dpi=100,
        noise="niedrig",
    ),
    # -- JPEG-Artefakte -----------------------------------------------------------
    _Spec(
        "jpeg_q20_police",
        "jpeg",
        _PM,
        "Foto/Scan als JPEG-Datei, 200 dpi, Qualität 20 (deutliche Blockartefakte)",
        "jpeg",
        dpi=200,
        noise="niedrig",
        jpeg_quality=20,
    ),
    _Spec(
        "jpeg_q25_portal",
        "jpeg",
        "portal",
        "Portal-Screenshot (96 dpi) als JPEG, Qualität 25 (z. B. per Messenger verschickt)",
        "jpeg",
        dpi=int(PORTAL_DPI),
        jpeg_quality=25,
    ),
    # -- Drehung ---------------------------------------------------------------
    _Spec(
        "drehung_90",
        "drehung",
        _PM,
        "Scan 200 dpi, um 90° im Uhrzeigersinn gedreht (Querformat-Seite)",
        "pdf_scan",
        dpi=200,
        noise="niedrig",
        jpeg_quality=_JQ,
        rotation=90,
    ),
    # -- Tabelle ---------------------------------------------------------------
    _Spec(
        "tabelle_scan",
        "tabelle",
        "tabelle",
        "Beitragsmitteilung mit Tabellen als Scan, 200 dpi, 0,8° schräg",
        "pdf_scan",
        dpi=200,
        noise="niedrig",
        skew=0.8,
        jpeg_quality=_JQ,
    ),
    # -- Verwechselbare Zeichen ----------------------------------------------------
    _Spec(
        "verwechslung_scan",
        "verwechslung",
        "verwechslung",
        "Police mit verwechselbaren Zeichen als Scan, 200 dpi",
        "pdf_scan",
        dpi=200,
        noise="niedrig",
        jpeg_quality=_JQ,
    ),
    # -- Mehrseitig ---------------------------------------------------------------
    _Spec(
        "mehrseitig_police_scan",
        "mehrseitig",
        "police_3seiten",
        "Police über 3 Seiten als Scan, 150 dpi, JPEG-Qualität 70",
        "pdf_scan",
        dpi=150,
        noise="niedrig",
        jpeg_quality=70,
    ),
    _Spec(
        "mehrseitig_standmitteilung_scan",
        "mehrseitig",
        "standmitteilung",
        "Standmitteilung über 2 Seiten als reiner Scan (ohne Textebene), 200 dpi",
        "pdf_scan",
        dpi=200,
        noise="niedrig",
        jpeg_quality=_JQ,
    ),
)

_EXTENSIONS = {"pdf_text": ".pdf", "pdf_scan": ".pdf", "png": ".png", "jpeg": ".jpg"}


def variant_ids() -> list[str]:
    """IDs aller Varianten in fester Reihenfolge."""
    return [s.id for s in _SPECS]


# ---------------------------------------------------------------------------
# Erzeugung
# ---------------------------------------------------------------------------


def _render(pages: list[_Page]) -> pymupdf.Document:
    doc = _new_doc()
    for spec_page in pages:
        page = _prepare_page(doc)
        for x0, y0, x1, y1 in spec_page.lines:
            page.draw_line(pymupdf.Point(x0, y0), pymupdf.Point(x1, y1), color=_LINE_GRAY, width=0.6)
        for op in spec_page.ops:
            _draw(page, op)
    return doc


def _file_name(spec: _Spec) -> str:
    return f"MUSTER_BENCH_{spec.id}{_EXTENSIONS[spec.fmt]}"


def _encode(img: Image.Image, quality: int | None) -> tuple[bytes, str]:
    buf = io.BytesIO()
    if quality is None:
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "png"
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), "jpeg"


def _scan_images(doc: pymupdf.Document, spec: _Spec, page_numbers: list[int], seed: int) -> list[Image.Image]:
    noise = NOISE_LEVELS[spec.noise]
    images: list[Image.Image] = []
    for offset, number in enumerate(page_numbers):
        img = _rasterize(doc[number - 1], int(spec.dpi or 300))
        img = _degrade(
            img,
            seed=seed + offset,
            sigma=noise["sigma"],
            speckle=noise["speckle"],
            angle=spec.skew,
            blur=noise["blur"],
        )
        if spec.rotation:
            # rotate() dreht gegen den Uhrzeigersinn -> negativer Winkel = im Uhrzeigersinn
            img = img.rotate(-spec.rotation, expand=True)
        images.append(img)
    return images


def _write_pdf_scan(images: list[Image.Image], spec: _Spec, path: Path, title: str) -> None:
    doc = _new_doc()
    for img in images:
        landscape = img.width > img.height
        width, height = (_A4[1], _A4[0]) if landscape else _A4
        page = doc.new_page(width=width, height=height)
        data, _kind = _encode(img, spec.jpeg_quality)
        page.insert_image(page.rect, stream=data)
    _save(doc, path, title)
    doc.close()


def _write_image_file(img: Image.Image, spec: _Spec, path: Path) -> None:
    dpi = (int(spec.dpi or 150), int(spec.dpi or 150))
    path.parent.mkdir(parents=True, exist_ok=True)
    if spec.fmt == "png":
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", MUSTER_MARK)
        info.add_text("Software", "Lokales Vorlagen-Tool (Benchmark-Varianten)")
        img.save(path, format="PNG", optimize=True, dpi=dpi, pnginfo=info)
    else:
        img.save(
            path,
            format="JPEG",
            quality=int(spec.jpeg_quality or _SCANNER_JPEG_QUALITY),
            dpi=dpi,
            comment=MUSTER_MARK.encode("utf-8"),
        )


def _portal_image(tmp_dir: Path) -> Image.Image:
    tmp = tmp_dir / ".portal_tmp.png"
    try:
        _write_portal_png(tmp)
        with Image.open(tmp) as im:
            return im.convert("RGB")
    finally:
        tmp.unlink(missing_ok=True)


def _page_count(spec: _Spec, layout: _Layout, rendered: int) -> int:
    if spec.layout == "portal":
        return 1
    return len(layout.page_numbers) if layout.page_numbers else rendered


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(spec: _Spec, index: int, out_dir: Path, measure: _Measure, seed: int) -> BenchmarkItem:
    layout = _LAYOUTS[spec.layout]
    path = out_dir / _file_name(spec)
    variant_seed = seed * 1000 + index * 10
    if spec.layout == "portal":
        img = _portal_image(out_dir)
        if spec.fmt != "jpeg":
            raise ValueError("Portal-Variante nur als JPEG vorgesehen")
        _write_image_file(img, spec, path)
        rendered = 1
    else:
        pages = layout.pages(measure)
        doc = _render(pages)
        rendered = doc.page_count
        try:
            numbers = list(layout.page_numbers or range(1, doc.page_count + 1))
            if spec.fmt == "pdf_text":
                if layout.page_numbers:
                    doc.select([n - 1 for n in numbers])
                _save(doc, path, layout.title)
            else:
                images = _scan_images(doc, spec, numbers, variant_seed)
                if spec.fmt == "pdf_scan":
                    _write_pdf_scan(images, spec, path, f"{layout.title} – {spec.id}")
                elif len(images) == 1:
                    _write_image_file(images[0], spec, path)
                else:
                    raise ValueError("Bilddateien sind nur einseitig möglich")
        finally:
            doc.close()

    meta: dict[str, Any] = {
        "id": spec.id,
        "category": spec.category,
        "also_in": list(spec.also_in),
        "layout": spec.layout,
        "description": spec.description,
        "format": spec.fmt,
        "dpi": spec.dpi,
        "noise": spec.noise if spec.fmt != "pdf_text" else None,
        "skew_deg": spec.skew,
        "jpeg_quality": spec.jpeg_quality,
        "rotation_deg": spec.rotation,
        "page_count": _page_count(spec, layout, rendered),
        "needs_ocr": spec.fmt != "pdf_text",
        "doc_type": layout.doc_type.value,
        "document_date": layout.document_date.isoformat() if layout.document_date else None,
        "effective_date": layout.effective_date.isoformat() if layout.effective_date else None,
        "contract_number": layout.contract_number,
        "seed": variant_seed,
        "base_seed": seed,
        "synthetic": True,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }
    truth = dict(sorted(layout.truth.items()))
    (out_dir / f"{path.name}.truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return BenchmarkItem(file=path, ground_truth=truth, meta=meta)


def generate_benchmark_set(
    out_dir: Path, seed: int = 0, only: Iterable[str] | None = None
) -> list[BenchmarkItem]:
    """Benchmark-Dateien deterministisch nach ``out_dir`` schreiben.

    Schreibt je Datei ``<datei>.truth.json`` und eine ``manifest.json`` mit den
    Beschreibungen. ``only``: nur diese Varianten-IDs erzeugen (z. B. für schnelle Tests).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(only) if only is not None else None
    if wanted is not None:
        unknown = wanted - set(variant_ids())
        if unknown:
            raise ValueError(f"Unbekannte Varianten: {', '.join(sorted(unknown))}")
    measure = _Measure()
    items: list[BenchmarkItem] = []
    for index, spec in enumerate(_SPECS):
        if wanted is not None and spec.id not in wanted:
            continue
        items.append(_build(spec, index, out_dir, measure, seed))
    manifest = {
        "generator": "insurance_app.samples.variants",
        "version": MANIFEST_VERSION,
        "seed": seed,
        "synthetic": True,
        "mark": MUSTER_MARK,
        "categories": CATEGORY_LABELS,
        "items": [{"file": item.file.name, "meta": item.meta} for item in items],
    }
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log.info("Benchmark-Varianten erzeugt: %d Dateien", len(items))
    return items


def load_benchmark_set(set_dir: Path) -> list[BenchmarkItem]:
    """Vorhandenen Benchmark-Satz (``manifest.json`` + ``*.truth.json``) laden.

    Akzeptiert nur Sätze, die dieser Generator als synthetisch gekennzeichnet hat.
    """
    set_dir = Path(set_dir)
    manifest_path = set_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Keine {MANIFEST_NAME} im Ordner – bitte zuerst den Satz erzeugen.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not True or manifest.get("generator") != "insurance_app.samples.variants":
        raise ValueError("Der Ordner enthält keinen synthetischen Benchmark-Satz dieses Generators.")
    items: list[BenchmarkItem] = []
    for entry in manifest.get("items", []):
        path = set_dir / entry["file"]
        truth_path = set_dir / f"{entry['file']}.truth.json"
        if not path.is_file() or not truth_path.is_file():
            raise FileNotFoundError(f"Datei oder Musterlösung fehlt: {entry['file']}")
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        items.append(BenchmarkItem(file=path, ground_truth=truth, meta=dict(entry["meta"])))
    return items
