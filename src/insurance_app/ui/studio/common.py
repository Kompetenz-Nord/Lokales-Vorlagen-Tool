"""Gemeinsame Hilfen für Katalog, Vorlagen-Studio und Einstellungen.

Alle Texte sind deutsch und in einfacher Sprache. Hier stehen keine
personenbezogenen Daten – nur Beschriftungen, Feldschlüssel und Formate.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image
from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QImage, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QLabel, QWidget

from ...core.fields import FIELD_REGISTRY, grouped
from ...templates.schema import MappingKind, TemplateStatus
from ..theme import TEMPLATE_STATUS_STYLE, TEXT_MUTED

NO_SOURCE_TEXT = "– nicht zuordnen –"

KIND_LABELS: dict[MappingKind, str] = {
    MappingKind.TEXT: "Text",
    MappingKind.MULTILINE: "Mehrzeiliger Text",
    MappingKind.DATE_PART: "Datumsteil",
    MappingKind.AMOUNT: "Betrag",
    MappingKind.CHECKBOX: "Ankreuzfeld",
    MappingKind.RADIO: "Optionsgruppe",
    MappingKind.STATIC: "Fester Text",
    MappingKind.SIGNATURE: "Unterschrift (bleibt leer)",
    MappingKind.SIGNATURE_DATE: "Datum Unterschrift (bleibt leer)",
}

KIND_HELP: dict[MappingKind, str] = {
    MappingKind.TEXT: "Einzeiliger Text aus einem Datenfeld (Datum als TT.MM.JJJJ).",
    MappingKind.MULTILINE: "Mehrzeiliger Text (Zeilenumbrüche bleiben erhalten).",
    MappingKind.DATE_PART: "Nur ein Teil eines Datums: Tag, Monat oder Jahr (für Kästchen-Felder).",
    MappingKind.AMOUNT: "Betrag im deutschen Format, z. B. 1.234,56.",
    MappingKind.CHECKBOX: "Kästchen wird angekreuzt, wenn das Datenfeld den gewählten Wert hat.",
    MappingKind.RADIO: "Optionsgruppe: jeder Auswahl wird ein Zustand im PDF zugeordnet.",
    MappingKind.STATIC: "Fester, nicht personenbezogener Text.",
    MappingKind.SIGNATURE: "Unterschriftsfelder bleiben immer leer – die App erzeugt keine Unterschriften.",
    MappingKind.SIGNATURE_DATE: "Datum der Unterschrift bleibt leer und wird handschriftlich ergänzt.",
}

# Welche Zuordnungsarten passen zu welchem PDF-Feldtyp (wie in der Vorlagen-Registry geprüft)
KINDS_FOR_FIELD_TYPE: dict[str, tuple[MappingKind, ...]] = {
    "text": (
        MappingKind.TEXT,
        MappingKind.MULTILINE,
        MappingKind.DATE_PART,
        MappingKind.AMOUNT,
        MappingKind.STATIC,
        MappingKind.SIGNATURE_DATE,
    ),
    "combobox": (MappingKind.TEXT,),
    "checkbox": (MappingKind.CHECKBOX, MappingKind.RADIO),
    "radio": (MappingKind.RADIO, MappingKind.CHECKBOX),
    "signature": (MappingKind.SIGNATURE,),
    "button": (),
    "listbox": (),
}
OVERLAY_KINDS: tuple[MappingKind, ...] = (MappingKind.TEXT, MappingKind.MULTILINE, MappingKind.AMOUNT)

FIELD_TYPE_LABELS: dict[str, str] = {
    "text": "Textfeld",
    "checkbox": "Ankreuzfeld",
    "radio": "Optionsgruppe",
    "signature": "Unterschriftsfeld",
    "button": "Schaltfläche",
    "combobox": "Auswahlliste",
    "listbox": "Liste",
    "overlay": "Koordinatenfeld",
}

DATE_PART_LABELS: dict[str, str] = {"day": "Tag", "month": "Monat", "year": "Jahr"}

SOURCE_LABELS: dict[str, str] = {
    "mitgeliefert": "mitgeliefert",
    "importiert": "importiert",
}

TOOLTIP_ACROFORM = (
    "Formularfelder (AcroForm) sind ausfüllbare Felder, die bereits in der PDF angelegt sind. "
    "Die App trägt Werte nur in diese Felder ein und verändert sonst nichts."
)
TOOLTIP_OVERLAY = (
    "Koordinatenfeld: Bei PDFs ohne ausfüllbare Felder wird Text an einer festen Stelle der Seite "
    "eingesetzt. Maßeinheit: PDF-Punkt (1 Punkt = 1/72 Zoll ≈ 0,35 mm), Ursprung oben links."
)
TOOLTIP_SHA = (
    "Prüfsumme (SHA-256): ein digitaler Fingerabdruck der Datei. Ändert sich die Datei, "
    "passt die Prüfsumme nicht mehr und die Vorlage wird gesperrt."
)


# ---------------------------------------------------------------------------
# Formatierung
# ---------------------------------------------------------------------------


def format_dt(value: datetime | None) -> str:
    """Zeitpunkt in Ortszeit, z. B. ``13.11.2026 10:15`` (leer -> „–“)."""
    if value is None:
        return "–"
    try:
        local = value.astimezone() if value.tzinfo else value
    except (OverflowError, OSError, ValueError):
        local = value
    return local.strftime("%d.%m.%Y %H:%M")


def human_size(num_bytes: int | float | None) -> str:
    """Dateigröße lesbar mit deutschem Dezimalkomma, z. B. ``1,2 MB``."""
    if num_bytes is None:
        return "–"
    size = float(num_bytes)
    for unit in ("Byte", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "Byte":
                return f"{int(size)} Byte"
            text = f"{size:.1f}".replace(".", ",")
            return f"{text} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover - unerreichbar


def gb_text(value: float | None) -> str:
    if value is None:
        return "unbekannt"
    return f"{value:.1f}".replace(".", ",") + " GB"


def template_status_text(status: TemplateStatus) -> str:
    return TEMPLATE_STATUS_STYLE[status][0]


def template_status_color(status: TemplateStatus) -> str:
    return TEMPLATE_STATUS_STYLE[status][1]


def field_label(key: str | None) -> str:
    if not key:
        return NO_SOURCE_TEXT
    if "{" in key:
        return f"Zusammengesetzt: {key}"
    fdef = FIELD_REGISTRY.get(key)
    return f"{fdef.label} ({fdef.group})" if fdef else key


# ---------------------------------------------------------------------------
# Auswahllisten
# ---------------------------------------------------------------------------

SOURCE_ROLE = Qt.ItemDataRole.UserRole


def source_model(extra: list[str] | None = None, include_none: bool = True) -> QStandardItemModel:
    """Datenfelder gruppiert (Gruppenüberschriften nicht wählbar) für eine Quell-Auswahlliste.

    ``extra`` sind zusätzliche Einträge (z. B. zusammengesetzte Ausdrücke wie
    ``{insured.last_name}, {insured.first_name}``), die oben eingefügt werden.
    """
    model = QStandardItemModel()
    if include_none:
        none_item = QStandardItem(NO_SOURCE_TEXT)
        none_item.setData(None, SOURCE_ROLE)
        model.appendRow(none_item)
    for expr in extra or []:
        item = QStandardItem(f"Zusammengesetzt: {expr}")
        item.setData(expr, SOURCE_ROLE)
        item.setToolTip("Zusammengesetzter Wert aus mehreren Datenfeldern. „?“ bedeutet: Teil darf fehlen.")
        model.appendRow(item)
    bold = QFont()
    bold.setBold(True)
    for group in grouped():
        header = QStandardItem(f"── {group.group} ──")
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        header.setData(None, SOURCE_ROLE)
        header.setFont(bold)
        model.appendRow(header)
        for key in group.keys:
            fdef = FIELD_REGISTRY[key]
            item = QStandardItem(f"{fdef.label} ({fdef.group})")
            item.setData(key, SOURCE_ROLE)
            tip = f"Feldschlüssel: {key}"
            if fdef.category.value == "ORDER":
                tip += "\nAuftragsdaten: werden nie automatisch aus Dokumenten übernommen."
            if fdef.legal_relevant:
                tip += "\nRechtlich relevant: nur mit ausdrücklicher Entscheidung."
            if fdef.help:
                tip += f"\n{fdef.help}"
            item.setToolTip(tip)
            model.appendRow(item)
    return model


def make_source_combo(
    current: str | None, extra: list[str] | None = None, parent: QWidget | None = None
) -> QComboBox:
    """Auswahlliste der Datenfelder; ``current`` (Schlüssel oder Ausdruck) wird vorausgewählt."""
    extras = list(extra or [])
    if current and "{" in current and current not in extras:
        extras.insert(0, current)
    combo = QComboBox(parent)
    combo.setModel(source_model(extras))
    combo.setMaxVisibleItems(20)
    combo.setAccessibleName("Quelle (Datenfeld)")
    select_source(combo, current)
    return combo


def select_source(combo: QComboBox, value: str | None) -> bool:
    """Eintrag mit diesem Schlüssel/Ausdruck wählen (fügt unbekannte Ausdrücke hinzu)."""
    if value is None:
        combo.setCurrentIndex(0)
        return True
    idx = combo.findData(value, SOURCE_ROLE)
    if idx < 0 and "{" in value:
        model = combo.model()
        if isinstance(model, QStandardItemModel):
            item = QStandardItem(f"Zusammengesetzt: {value}")
            item.setData(value, SOURCE_ROLE)
            model.insertRow(1, item)
            idx = 1
    if idx < 0:
        return False
    combo.setCurrentIndex(idx)
    return True


def combo_source(combo: QComboBox) -> str | None:
    value = combo.currentData(SOURCE_ROLE)
    return str(value) if value else None


# ---------------------------------------------------------------------------
# Fehlertexte
# ---------------------------------------------------------------------------

_MESSAGE_MAP: tuple[tuple[str, str], ...] = (
    ("source oder compose erforderlich", "Bitte eine Quelle (Datenfeld) wählen."),
    ("RADIO benötigt choice_map", "Bitte die Auswahlmöglichkeiten der Optionsgruppe zuordnen."),
    ("date_part muss", "Bitte wählen, ob Tag, Monat oder Jahr eingetragen wird."),
    ("STATIC benötigt static_value", "Bitte den festen Text eintragen."),
    ("fill_policy=never", "Unterschriftsfelder bleiben immer leer."),
    ("Doppelte Feldzuordnung", "Dieses PDF-Feld ist mehrfach zugeordnet."),
    ("Unbekannter Feldschlüssel", "Unbekanntes Datenfeld – bitte neu wählen."),
    ("Ungültiges Rechteck", "Das Rechteck ist zu klein oder ungültig."),
)


def friendly_message(raw: str) -> str:
    """Technische Prüfmeldung in verständliches Deutsch übersetzen."""
    text = raw.removeprefix("Value error, ").strip()
    for needle, friendly in _MESSAGE_MAP:
        if needle in text:
            return friendly
    return text or "Ungültige Angabe."


def friendly_errors(exc: ValidationError, subject: str | None = None) -> list[str]:
    """Pydantic-Fehler als deutsche Liste („Feld „X“: …“)."""
    out: list[str] = []
    for err in exc.errors():
        msg = str(err.get("msg", ""))
        friendly = friendly_message(msg)
        name = subject
        if name is None and "Doppelte Feldzuordnung:" in msg:
            name = msg.split("Doppelte Feldzuordnung:", 1)[1].strip()
        if name is None:
            loc = [str(p) for p in err.get("loc", ()) if not isinstance(p, int)]
            name = ".".join(loc) if loc else None
        line = f"„{name}“: {friendly}" if name else friendly
        if line not in out:
            out.append(line)
    return out or ["Die Zuordnung ist ungültig."]


# ---------------------------------------------------------------------------
# Bilder
# ---------------------------------------------------------------------------


def pil_to_qimage(img: Image.Image) -> QImage:
    """Pillow-Bild (RGB) -> eigenständige QImage-Kopie (thread-sicher)."""
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return qimg.copy()


def info_label(text: str, tooltip: str = "") -> QLabel:
    """Kleiner Hinweistext mit Symbol ⓘ (Text + Symbol, nicht nur Farbe)."""
    lbl = QLabel(f"ⓘ {text}")
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {TEXT_MUTED};")
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl


def bullet_list(lines: list[str], limit: int = 12) -> str:
    shown = [f"• {line}" for line in lines[:limit]]
    if len(lines) > limit:
        shown.append(f"• … und {len(lines) - limit} weitere")
    return "\n".join(shown)
