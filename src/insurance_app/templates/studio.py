"""Vorlagen-Studio: automatische Erst-Zuordnung importierter Originalvorlagen.

Die Automatik ordnet nur offensichtliche VERTRAGSdaten über den Feldnamen zu
(z. B. „Versicherungsnummer“ -> ``contract.number``), erkennt Unterschriften
und Datums-Teilfelder und lässt alles andere unzugeordnet. Rechtlich relevante
Auswahlen (Kästchen, Optionsgruppen) werden nie automatisch zugeordnet.
Das Ergebnis ist immer ``mapping_confirmed=False`` und muss geprüft werden.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from insurance_app.core.fields import FIELD_REGISTRY
from insurance_app.core.textutil import fold
from insurance_app.pdf.inspector import FormInspection, PdfFieldInfo
from insurance_app.templates.schema import (
    COMPOSE_PLACEHOLDER,
    CatalogEntry,
    FieldMapping,
    FillPolicy,
    MappingKind,
    TemplateDefinition,
)

__all__ = [
    "IMPORTED_PDF_NAME",
    "TemplateDraft",
    "auto_mapping",
    "coordinate_field",
    "derive_insurer_code",
    "derive_short_title",
]

IMPORTED_PDF_NAME = "original.pdf"


class TemplateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: TemplateDefinition | None
    unassigned_fields: list[str] = Field(default_factory=list)  # PDF-Felder ohne Zuordnung
    suggestions: dict[str, str] = Field(default_factory=dict)  # PDF-Feld -> Feldschlüssel/compose (unsicher)
    hints: dict[str, str] = Field(default_factory=dict)  # PDF-Feld -> deutscher Hinweis


def _key(name: str) -> str:
    """Vergleichsform eines Feldnamens: gefaltet, nur a–z/0–9, Endziffern entfernt."""
    compact = re.sub(r"[^a-z0-9]", "", fold(name))
    return re.sub(r"\d+$", "", compact)


# Sichere Zuordnungen (exakte Vergleichsform) – nur Vertragsdaten
_CONTRACT_EXACT: dict[str, str] = {
    **dict.fromkeys(
        [
            "versicherungnummer",
            "versicherungsnummer",
            "versicherungnr",
            "versicherungsnr",
            "vertragsnummer",
            "vertragnummer",
            "vertragsnr",
            "vertragnr",
            "versicherungsscheinnummer",
            "versicherungsscheinnr",
            "vsnr",
        ],
        "contract.number",
    ),
    **dict.fromkeys(["policennummer", "policennr", "polizzennummer"], "contract.policy_number"),
    **dict.fromkeys(["antragsnummer", "antragsnr"], "contract.application_number"),
    **dict.fromkeys(["geburtsdatum", "gebdatum"], "insured.birth_date"),
    "geburtsort": "insured.birth_place",
    **dict.fromkeys(
        ["staatsangehoerigkeit", "stattsangehoerigkeit", "staatsangehorigkeit", "nationalitaet"],
        "insured.nationality",
    ),
    **dict.fromkeys(["versicherungsbeginn", "vertragsbeginn"], "contract.start_date"),
}

# Unsichere Vorschläge (werden NICHT übernommen)
_SUGGEST_EXACT: dict[str, str] = {
    "namevorname": "{insured.last_name}, {insured.first_name}",
    "name": "insured.last_name",
    "nachname": "insured.last_name",
    "vorname": "insured.first_name",
    "versichertePerson": "{insured.first_name} {insured.last_name}",
    "versichertepersonname": "{insured.first_name} {insured.last_name}",
    "strassehausnummer": "{insured.street} {insured.house_number?}",
    "strasse": "insured.street",
    "hausnummer": "insured.house_number",
    "postleitzahl": "insured.postal_code",
    "plz": "insured.postal_code",
    "ort": "insured.city",
    "wohnort": "insured.city",
    "versicherungsnehmer": "{policyholder.first_name?} {policyholder.name}",
    "iban": "order.payment_iban",
    "kontoinhaber": "order.payment_account_holder",
    "geldinstitut": "order.payment_institution",
    "kreditinstitut": "order.payment_institution",
    "tarif": "contract.tariff",
    "produkt": "contract.product",
}
_SUGGEST_EXACT = {k.lower(): v for k, v in _SUGGEST_EXACT.items()}
_CONTRACT_EXACT = {k.lower(): v for k, v in _CONTRACT_EXACT.items()}

_PART_WORDS = {
    "day": ("tag", "tt"),
    "month": ("monat", "mm"),
    "year": ("jahr", "jjjj", "jj", "yyyy"),
}


def derive_insurer_code(insurer: str) -> str:
    """Kurzcode für Dateinamen, z. B. „tecis“ -> ``TECIS``."""
    folded = fold(insurer)
    if "unabhaengig" in folded or not folded:
        return "ALLGEMEIN"
    first = re.sub(r"[^a-z0-9]", "", folded.split(" ")[0])
    return (first or "ALLGEMEIN").upper()[:20]


def derive_short_title(title: str) -> str:
    """Kurztitel für Dateinamen, z. B. „Bestandsübertrag / …“ -> ``Bestandsuebertrag``."""
    word = re.split(r"[\s/–-]+", title.strip())[0] if title.strip() else "Vorlage"
    folded = re.sub(r"[^A-Za-z0-9]", "", fold(word))
    return (folded[:1].upper() + folded[1:])[:30] if folded else "Vorlage"


def _date_part_from_name(name: str) -> str | None:
    words = re.findall(r"[a-z]+", fold(name))
    for part, keys in _PART_WORDS.items():
        if any(w in keys for w in words[1:]):
            return part
    return None


def _row_groups(fields: list[PdfFieldInfo]) -> list[list[PdfFieldInfo]]:
    """Datums-Teilfelder (MaxLen 2/2/4 bzw. 2/4) in einer Zeile gruppieren."""
    small = [
        f
        for f in fields
        if f.field_type == "text" and f.max_len in (2, 4) and len(f.widgets) == 1 and not f.multiline
    ]
    small.sort(key=lambda f: (f.widgets[0].page, round(f.widgets[0].rect[1]), f.widgets[0].rect[0]))
    groups: list[list[PdfFieldInfo]] = []
    for f in small:
        w = f.widgets[0]
        if groups:
            last = groups[-1][-1].widgets[0]
            same_row = last.page == w.page and abs(last.rect[1] - w.rect[1]) < 2.0
            close = 0 <= w.rect[0] - last.rect[2] < 20
            if same_row and close and groups[-1][-1].max_len == 2:
                groups[-1].append(f)
                continue
        groups.append([f])
    result: list[list[PdfFieldInfo]] = []
    for g in groups:
        pattern = [f.max_len for f in g]
        if pattern in ([2, 2, 4], [2, 4]):
            result.append(g)
    return result


def _mapping_label(key: str) -> str:
    fdef = FIELD_REGISTRY.get(key)
    return fdef.label if fdef else key


def auto_mapping(
    entry: CatalogEntry,
    pdf_path: Path,
    inspection: FormInspection,
    version: str,
) -> TemplateDraft:
    """Erst-Zuordnung für eine importierte Originalvorlage (immer unbestätigt)."""
    sha = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    mappings: list[FieldMapping] = []
    unassigned: list[str] = []
    suggestions: dict[str, str] = {}
    hints: dict[str, str] = {}
    ignored: list[str] = []
    assigned: set[str] = set()

    # 1) Datums-Teilfelder (Gruppen)
    for group in _row_groups(inspection.fields):
        parts = ["day", "month", "year"] if len(group) == 3 else ["month", "year"]
        prefix = _key(group[0].name)
        is_signature_date = "unterschrift" in prefix
        is_birth = "geburt" in prefix
        for f, part in zip(group, parts, strict=True):
            page = f.pages[0] if f.pages else None
            part_label = {"day": "Tag", "month": "Monat", "year": "Jahr"}[part]
            if is_signature_date:
                mappings.append(
                    FieldMapping(
                        pdf_field=f.name,
                        label=f"Datum Unterschrift ({part_label})",
                        kind=MappingKind.SIGNATURE_DATE,
                        fill_policy=FillPolicy.NEVER,
                        page=page,
                        help="Datum zur Unterschrift bleibt leer – wird handschriftlich ergänzt.",
                    )
                )
                assigned.add(f.name)
            elif is_birth and len(group) == 3:
                mappings.append(
                    FieldMapping(
                        pdf_field=f.name,
                        label=f"Geburtsdatum ({part_label})",
                        kind=MappingKind.DATE_PART,
                        source="insured.birth_date",
                        date_part=part,
                        page=page,
                    )
                )
                assigned.add(f.name)
            else:
                hints[f.name] = f"Vermutlich Datumsteil ({part_label}) – bitte Quelle wählen."

    # 2) Einzelne Felder
    for f in inspection.fields:
        if f.name in assigned:
            continue
        page = f.pages[0] if f.pages else None
        key = _key(f.name)
        if f.field_type == "signature":
            mappings.append(
                FieldMapping(
                    pdf_field=f.name,
                    label="Unterschrift (bleibt leer)",
                    kind=MappingKind.SIGNATURE,
                    fill_policy=FillPolicy.NEVER,
                    page=page,
                    help="Unterschriftsfeld bleibt immer leer. Die App erzeugt keine Unterschriften.",
                )
            )
            continue
        if f.field_type == "button":
            ignored.append(f.name)
            continue
        if f.field_type == "text" and "unterschrift" in key and "datum" in key:
            mappings.append(
                FieldMapping(
                    pdf_field=f.name,
                    label="Datum Unterschrift",
                    kind=MappingKind.SIGNATURE_DATE,
                    fill_policy=FillPolicy.NEVER,
                    page=page,
                )
            )
            continue
        if f.field_type == "text" and f.name not in hints:
            part = _date_part_from_name(f.name)
            base = key
            for words in _PART_WORDS.values():
                for w in words:
                    if part and base.endswith(w):
                        base = base[: -len(w)]
                        break
            source = _CONTRACT_EXACT.get(base if part else key)
            if source == "insured.birth_date" and part:
                mappings.append(
                    FieldMapping(
                        pdf_field=f.name,
                        label=f"{_mapping_label(source)} ({part})",
                        kind=MappingKind.DATE_PART,
                        source=source,
                        date_part=part,
                        page=page,
                    )
                )
                continue
            if source and not part:
                mappings.append(
                    FieldMapping(
                        pdf_field=f.name,
                        label=_mapping_label(source),
                        kind=MappingKind.TEXT,
                        source=source,
                        required=source == "contract.number",
                        page=page,
                    )
                )
                continue
            suggestion = _SUGGEST_EXACT.get(key)
            if suggestion:
                suggestions[f.name] = suggestion
        elif f.field_type in ("checkbox", "radio"):
            hints.setdefault(
                f.name, "Ankreuzfeld/Auswahl: rechtlich relevante Entscheidung – bitte manuell zuordnen."
            )
        unassigned.append(f.name)

    definition = TemplateDefinition(
        template_id=f"{entry.id}_{version}",
        catalog_id=entry.id,
        version=version,
        insurer=entry.insurer,
        insurer_code=derive_insurer_code(entry.insurer),
        form_number=entry.form_number or f"VORLAGE{entry.number}",
        title=entry.title,
        short_title=derive_short_title(entry.title),
        pdf_file=IMPORTED_PDF_NAME,
        sha256=sha,
        page_count=inspection.page_count,
        pages_to_preserve=[],
        fields=mappings,
        ignored_fields=ignored,
        mapping_confirmed=False,
        notes=["Automatisch erzeugte Erst-Zuordnung – bitte jedes Feld prüfen und bestätigen."],
    )
    return TemplateDraft(
        definition=definition,
        unassigned_fields=unassigned,
        suggestions=suggestions,
        hints=hints,
    )


def coordinate_field(
    pdf_field_name: str,
    page: int,
    rect: Sequence[float],
    label: str,
    source: str,
    kind: MappingKind = MappingKind.TEXT,
    font_size: float = 10.0,
    align: str = "left",
    required: bool = False,
) -> FieldMapping:
    """Zuordnung für nicht ausfüllbare PDFs: Text per Koordinaten (``overlay_rect``).

    ``rect`` = [x0, y0, x1, y1] in Seitenkoordinaten (oben links, PDF-Punkte).
    ``source`` ist ein Feldschlüssel oder ein compose-Ausdruck mit ``{…}``.
    """
    if kind not in (MappingKind.TEXT, MappingKind.MULTILINE, MappingKind.AMOUNT):
        raise ValueError("Koordinaten-Felder unterstützen nur Text (auch Datum als Text) und Beträge")
    if page < 1:
        raise ValueError("Seitenzahl muss mindestens 1 sein")
    coords = [float(v) for v in rect]
    if len(coords) != 4 or coords[2] <= coords[0] or coords[3] <= coords[1]:
        raise ValueError("Ungültiges Rechteck: [x0, y0, x1, y1] mit x1 > x0 und y1 > y0 erwartet")
    if align not in ("left", "center", "right"):
        raise ValueError("align muss left, center oder right sein")
    if not pdf_field_name.strip():
        raise ValueError("Name des Koordinaten-Felds fehlt")
    is_compose = "{" in source
    if is_compose:
        if not COMPOSE_PLACEHOLDER.search(source):
            raise ValueError("compose-Ausdruck ohne gültige Platzhalter")
        unknown = [k for k, _ in COMPOSE_PLACEHOLDER.findall(source) if k not in FIELD_REGISTRY]
    else:
        unknown = [source] if source not in FIELD_REGISTRY else []
    if unknown:
        raise ValueError(f"Unbekannter Feldschlüssel: {unknown[0]}")
    return FieldMapping(
        pdf_field=pdf_field_name,
        label=label,
        kind=kind,
        source=None if is_compose else source,
        compose=source if is_compose else None,
        page=page,
        overlay_rect=coords,
        font_size=font_size,
        align=align,
        required=required,
    )
