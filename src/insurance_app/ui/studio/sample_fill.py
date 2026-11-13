"""Testbefüllung mit offensichtlichen Musterwerten (nichts wird gespeichert).

* Für jedes zugeordnete Datenfeld entsteht ein ``FieldValue`` mit Status
  ``MANUAL`` und einem erkennbar synthetischen Wert („Erika Musterfrau“,
  „MUSTER-0001“ …). Diese Werte existieren nur im Arbeitsspeicher.
* Bedingungen werden für die Testansicht ignoriert, damit jedes zugeordnete
  Feld sichtbar wird. Ankreuzfelder/Optionsgruppen erhalten die erste Option.
* Die befüllte PDF entsteht in einem zufälligen Unterordner von
  ``paths.temp_dir`` und wird nach dem Rendern sofort gelöscht.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.fields import FIELD_REGISTRY, FieldCategory, FieldDef, FieldType
from ...core.models import ExtractionMethod, FieldStatus, FieldValue
from ...pdf.filler import TemplateFiller
from ...pdf.inspector import inspect_pdf_form
from ...pdf.render import render_page
from ...security.tempfiles import force_rmtree
from ...templates.resolver import ChecklistItem, resolve_fill_values
from ...templates.schema import COMPOSE_PLACEHOLDER, MappingKind, TemplateDefinition
from ..widgets.common import muted
from .common import pil_to_qimage

MUSTER_IBAN = "DE02120300000000202051"  # bekannte Test-IBAN (synthetisch)

# Teilzeichenfolge im Feldschlüssel -> (Anzeige, Normalwert)
_TEXT_HINTS: tuple[tuple[str, str], ...] = (
    ("first_name", "Erika"),
    ("last_name", "Musterfrau"),
    ("account_holder", "Erika Musterfrau"),
    ("house_number", "12"),
    ("street", "Musterweg"),
    ("postal_code", "12345"),
    ("birth_place", "Musterstadt"),
    ("city", "Musterstadt"),
    ("nationality", "deutsch"),
    ("institution", "Musterbank (MUSTER)"),
    ("company", "Muster GmbH (MUSTER)"),
    ("contact", "Max Mustermann"),
    ("email", "erika.musterfrau@example.org"),
    ("phone", "0123 456789"),
    ("insurer", "Beispiel Lebensversicherung AG (MUSTER)"),
    ("department", "Vertragsservice (MUSTER)"),
    (".name", "Musterfrau"),
)


@dataclass
class SampleFillResult:
    images: list[QImage] = field(default_factory=list)
    filled_fields: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # Hinweise aus der Werteauflösung (ohne Werte)


def _sample_for(fdef: FieldDef | None, key: str) -> tuple[str, str]:
    """(Anzeige-/Rohwert, Normalwert) – erkennbar synthetisch."""
    dtype = fdef.dtype if fdef else FieldType.TEXT
    if dtype == FieldType.DATE:
        return "15.01.1980", "1980-01-15"
    if dtype == FieldType.MONTH:
        return "01.2027", "2027-01"
    if dtype == FieldType.AMOUNT:
        return "123,45", "123.45"
    if dtype == FieldType.IBAN:
        return MUSTER_IBAN, MUSTER_IBAN
    if dtype == FieldType.POSTAL_CODE:
        return "12345", "12345"
    if dtype == FieldType.COUNTRY:
        return "Deutschland", "DE"
    if dtype == FieldType.CURRENCY:
        return "EUR", "EUR"
    if dtype == FieldType.INTEGER:
        return "1", "1"
    if dtype == FieldType.CONTRACT_NUMBER:
        return "MUSTER-0001", "MUSTER-0001"
    if dtype == FieldType.BOOL:
        return "ja", "true"
    if dtype == FieldType.CHOICE and fdef and fdef.choices:
        first = next(iter(fdef.choices))
        return fdef.choices[first], first
    for needle, text in _TEXT_HINTS:
        if needle in key:
            return text, text
    if dtype == FieldType.NAME:
        return "Erika Musterfrau", "Erika Musterfrau"
    return "MUSTER", "MUSTER"


def sample_values(definition: TemplateDefinition) -> tuple[dict[str, FieldValue], dict[str, FieldValue]]:
    """Synthetische Werte für alle Datenfelder der Zuordnung -> (Vertrag, Auftrag)."""
    preferred: dict[str, str] = {}  # Normalwert für Ankreuzfelder/Optionsgruppen (erste Option)
    keys: list[str] = []
    for m in definition.fields:
        if m.source:
            keys.append(m.source)
            if m.kind == MappingKind.RADIO and m.choice_map:
                preferred.setdefault(m.source, next(iter(m.choice_map)))
            elif m.kind == MappingKind.CHECKBOX:
                preferred.setdefault(m.source, m.checked_when or "true")
        if m.compose:
            keys.extend(k for k, _ in COMPOSE_PLACEHOLDER.findall(m.compose))
    contract: dict[str, FieldValue] = {}
    order: dict[str, FieldValue] = {}
    for key in dict.fromkeys(keys):
        fdef = FIELD_REGISTRY.get(key)
        raw, norm = _sample_for(fdef, key)
        if key in preferred:
            norm = preferred[key]
            if fdef and fdef.choices and norm in fdef.choices:
                raw = fdef.choices[norm]
            elif norm in ("true", "false"):
                raw = "ja" if norm == "true" else "nein"
            else:
                raw = norm
        fv = FieldValue(
            key=key,
            value=raw,
            normalized_value=norm,
            status=FieldStatus.MANUAL,
            extraction_method=ExtractionMethod.MANUAL,
            note="Musterwert (Testbefüllung, wird nicht gespeichert)",
        )
        target = order if fdef is not None and fdef.category == FieldCategory.ORDER else contract
        target[key] = fv
    return contract, order


def run_sample_fill(
    definition: TemplateDefinition,
    pdf_path: Path,
    fonts_dir: Path,
    temp_dir: Path,
    *,
    dpi: int = 90,
    progress: Callable[[int, int, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> SampleFillResult:
    """Vorlage mit Musterwerten befüllen, Seiten rendern, temporäre Dateien löschen."""
    test_def = definition.model_copy(
        update={
            "fields": [m.model_copy(update={"conditions": []}) for m in definition.fields],
            "acknowledgments": [],
        }
    )
    contract, order = sample_values(test_def)
    inspection = inspect_pdf_form(pdf_path)
    plan = resolve_fill_values(test_def, contract, order, inspection, mode="preview")
    seen: set[str] = set()
    unique = []
    for value in plan.values:  # ohne Bedingungen kann ein Feld mehrfach vorkommen -> erster Wert
        if value.pdf_field in seen:
            continue
        seen.add(value.pdf_field)
        unique.append(value)
    plan = plan.model_copy(update={"values": unique})
    notes = [_note(item) for item in plan.checklist if item.blocking]

    work = Path(temp_dir) / f"testbefuellung-{secrets.token_hex(8)}"
    work.mkdir(parents=True, exist_ok=False)
    work.chmod(0o700)
    try:
        out = work / "testbefuellung.pdf"
        if progress:
            progress(0, 2, "Musterwerte werden eingetragen …")
        result = TemplateFiller(fonts_dir).fill(definition, pdf_path, plan, out)
        images: list[QImage] = []
        pages = inspection.page_count or 1
        for page in range(1, pages + 1):
            if cancel is not None and cancel.is_set():
                break
            if progress:
                progress(1, 2, f"Seite {page} von {pages} wird dargestellt …")
            images.append(pil_to_qimage(render_page(out, page, dpi=dpi)))
        return SampleFillResult(
            images=images,
            filled_fields=list(result.filled_fields),
            skipped_fields=list(result.skipped_fields),
            notes=notes,
        )
    finally:
        force_rmtree(work)


def _note(item: ChecklistItem) -> str:
    return item.message


class PagePreviewDialog(QDialog):
    """Einfache Seitenansicht (Bilder untereinander, scrollbar)."""

    def __init__(self, title: str, result: SampleFillResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 760)
        self.result = result
        layout = QVBoxLayout(self)
        head = QHBoxLayout()
        info = QLabel(
            f"✔ {len(result.filled_fields)} Felder mit Musterwerten befüllt · {len(result.images)} Seite(n)"
        )
        info.setAccessibleName(info.text())
        head.addWidget(info, 1)
        layout.addLayout(head)
        layout.addWidget(
            muted(
                "Nur zur Kontrolle der Feldzuordnung: Alle Werte sind erfundene Musterwerte. "
                "Die befüllte Datei wurde bereits wieder gelöscht – es wird nichts gespeichert."
            )
        )
        if result.notes:
            layout.addWidget(
                muted(
                    "Nicht befüllt: " + "; ".join(result.notes[:4]) + (" …" if len(result.notes) > 4 else "")
                )
            )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        pages = QVBoxLayout(inner)
        pages.setSpacing(12)
        self.page_labels: list[QLabel] = []
        for idx, image in enumerate(result.images, start=1):
            caption = muted(f"Seite {idx}")
            pages.addWidget(caption)
            lbl = QLabel()
            lbl.setPixmap(QPixmap.fromImage(image))
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl.setAccessibleName(f"Seite {idx} der Testbefüllung")
            pages.addWidget(lbl)
            self.page_labels.append(lbl)
        pages.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
