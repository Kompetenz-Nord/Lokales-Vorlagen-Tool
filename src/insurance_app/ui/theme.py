"""Gestaltung nach dem tecis Corporate Design Manual (Version 1.0, 2020): Farben, Schriften, Stylesheet.

Regeln aus dem Styleguide (Basiselemente „Farben“ und „Typografie“)
* Primärfarben: tecis Blau (Flächen, Schrift) und tecis Himmelblau (Gestaltungsfarbe, nur 100 %).
* Sekundärfarben: tecis Limette und tecis Sand; dazu Schwarz und Weiß.
* Aufrasterung (hellere Töne) nur zwischen 100 % und 15 %; tecis Blau wird nie aufgerastert,
  tecis Himmelblau nur in Infografiken – :func:`tint` setzt das durch.
* Freigegebene Kombinationen (Schrift auf Fläche): auf Blau → Himmelblau, Limette, Weiß;
  auf Himmelblau → Weiß; auf Limette und auf Sand → Blau, Schwarz (:data:`ALLOWED_ON`).
* Schriften: DM Sans (Regular, Medium, Bold) für Fließtext und Zwischenüberschriften,
  Tinos (Regular, Bold) ausschließlich für Überschriften. Ersatz: Arial bzw. Times New Roman.

Regeln für alle Seiten
* Status nie nur über Farbe zeigen – immer auch Text/Symbol (Barrierefreiheit).
* Schriftgröße mindestens 10 pt, Hauptaktionen als ``primary``-Buttons.
* Texte in einfacher Sprache, Fachbegriffe kurz erklären (Tooltip oder Hilfetext).

Bewusste Ergänzung: Der Styleguide kennt keine Warnfarben. Für Prüfstatus und Fehlermeldungen
bleiben drei Signalfarben (grün, ocker, rot) – nur für Statustext und -symbole, nie für Flächen.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core.models import FieldStatus, Severity
from ..templates.schema import TemplateStatus

log = logging.getLogger(__name__)

# --- Markenfarben laut Styleguide ------------------------------------------------------------
TECIS_BLAU = "#00325B"  # Pantone 540 C · RGB 0, 50, 91
TECIS_HIMMELBLAU = "#009EE3"  # Pantone 2394 C · RGB 0, 158, 227
TECIS_LIMETTE = "#D3D800"  # Pantone 381 C · RGB 211, 216, 0
TECIS_SAND = "#CBCDB9"  # Pantone 7538 C · RGB 203, 205, 185
SCHWARZ = "#000000"
WEISS = "#FFFFFF"

#: Freigegebene Schrift-/Flächenfarben je Hintergrund (Styleguide, „Farbkombinationen“).
ALLOWED_ON: dict[str, frozenset[str]] = {
    TECIS_BLAU: frozenset({TECIS_HIMMELBLAU, TECIS_LIMETTE, WEISS}),
    TECIS_HIMMELBLAU: frozenset({WEISS}),
    TECIS_LIMETTE: frozenset({TECIS_BLAU, SCHWARZ}),
    TECIS_SAND: frozenset({TECIS_BLAU, SCHWARZ}),
}

MIN_TINT_PERCENT = 15


def tint(color: str, percent: int, *, infographic: bool = False) -> str:
    """Aufgerasterter (hellerer) Ton einer Farbe auf Weiß, ``percent`` zwischen 15 und 100.

    tecis Blau darf nie aufgerastert werden, tecis Himmelblau nur in Infografiken.
    """
    if not MIN_TINT_PERCENT <= percent <= 100:
        raise ValueError("Aufrasterung nur zwischen 15 % und 100 % erlaubt.")
    base = color.upper()
    if percent < 100 and base == TECIS_BLAU:
        raise ValueError("tecis Blau darf nicht aufgerastert werden.")
    if percent < 100 and base == TECIS_HIMMELBLAU and not infographic:
        raise ValueError("tecis Himmelblau darf nur in Infografiken aufgerastert werden.")
    channels = [int(base[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(255 - (255 - c) * percent / 100) for c in channels]
    return "#{:02X}{:02X}{:02X}".format(*mixed)


# --- Rollen in der Oberfläche ----------------------------------------------------------------
PRIMARY = TECIS_BLAU  # Hauptaktionen, Flächen
PRIMARY_DARK = TECIS_BLAU  # tecis Blau hat keine Abstufung
ACCENT = TECIS_HIMMELBLAU  # Gestaltungsfarbe: Akzentlinien, Fokus, Markierungen
HIGHLIGHT = TECIS_LIMETTE  # Auswahl und Hervorhebung (mit Schrift in tecis Blau)
BG = tint(TECIS_SAND, 15)
BG_SUBTLE = tint(TECIS_SAND, 30)  # Tabellenköpfe, Hinweisboxen
SURFACE = WEISS
BORDER = TECIS_SAND
TEXT = TECIS_BLAU
TEXT_MUTED = tint(SCHWARZ, 65)
HIGHLIGHT_BG = tint(TECIS_LIMETTE, 15)  # Erfolgs-/Hervorhebungsflächen
SELECT_BG = tint(TECIS_LIMETTE, 30)  # Auswahl in Tabellen, gedrückte Schaltflächen
DISABLED_TEXT = tint(SCHWARZ, 45)
DISABLED_BG = tint(TECIS_SAND, 50)
SIDEBAR = TECIS_BLAU
SIDEBAR_TEXT = WEISS
INFO = TECIS_BLAU
# Signalfarben nur für Statustext/-symbole (der Styleguide definiert keine Warnfarben)
OK = "#1E7B34"
WARN = "#8A5A00"
ERROR = "#B3261E"

# --- Schriften -------------------------------------------------------------------------------
FONT_BODY = "DM Sans"
FONT_HEADLINE = "Tinos"
#: Ersatzschriften laut Styleguide, falls DM Sans nicht geladen werden kann.
BODY_FALLBACK_FAMILIES = ["Arial", "Helvetica", "Liberation Sans"]
#: Schriften für Statussymbole (◐ ✔ ⚠ …), die DM Sans nicht enthält – je Betriebssystem eine.
SYMBOL_FAMILIES = ["Segoe UI Symbol", "Apple Symbols", "DejaVu Sans"]
BODY_FAMILIES = [FONT_BODY, *SYMBOL_FAMILIES, *BODY_FALLBACK_FAMILIES]
HEADLINE_STACK = '"Tinos", "Times New Roman", "Liberation Serif", serif'


def body_families(loaded: list[str]) -> list[str]:
    """Schriftfolge für Fließtext: DM Sans, sonst der Ersatz aus dem Styleguide (Arial)."""
    if FONT_BODY in loaded:
        return list(BODY_FAMILIES)
    return [*BODY_FALLBACK_FAMILIES, *SYMBOL_FAMILIES]


#: Schriftschnitte laut Styleguide: DM Sans Regular/Medium/Bold, Tinos Regular/Bold (je mit Kursiv).
BRAND_FONT_FILES = (
    "DMSans-Regular.ttf",
    "DMSans-Italic.ttf",
    "DMSans-Medium.ttf",
    "DMSans-MediumItalic.ttf",
    "DMSans-Bold.ttf",
    "DMSans-BoldItalic.ttf",
    "Tinos-Regular.ttf",
    "Tinos-Italic.ttf",
    "Tinos-Bold.ttf",
    "Tinos-BoldItalic.ttf",
)


def brand_fonts_dir() -> Path:
    from ..core.paths import resource_root

    return resource_root() / "assets" / "fonts" / "ui"


def load_brand_fonts(fonts_dir: Path | None = None) -> list[str]:
    """Mitgelieferte Hausschriften bei Qt anmelden -> Liste der geladenen Familien.

    Fehlt eine Datei, bleibt die App benutzbar: Qt nimmt dann den Ersatz aus dem
    Styleguide (Arial bzw. Times New Roman).
    """
    from PySide6.QtGui import QFontDatabase

    base = fonts_dir or brand_fonts_dir()
    families: list[str] = []
    for name in BRAND_FONT_FILES:
        path = base / name
        if not path.is_file():
            log.warning("Hausschrift fehlt: %s – es wird die Ersatzschrift verwendet", name)
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            log.warning("Hausschrift konnte nicht geladen werden: %s", name)
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if family not in families:
                families.append(family)
    return families


STATUS_STYLE: dict[FieldStatus, tuple[str, str, str]] = {
    # Status -> (Symbol, Kurztext, Farbe)
    FieldStatus.EXTRACTED: ("◐", "Erkannt – bitte prüfen", WARN),
    FieldStatus.CONFIRMED: ("✔", "Bestätigt", OK),
    FieldStatus.MANUAL: ("✎", "Manuell eingetragen", OK),
    FieldStatus.CONFLICT: ("⚠", "Konflikt", ERROR),
    FieldStatus.UNCLEAR: ("?", "Unklar", ERROR),
    FieldStatus.MISSING: ("○", "Fehlt", TEXT_MUTED),
    FieldStatus.NOT_APPLICABLE: ("–", "Nicht relevant", TEXT_MUTED),
}

SEVERITY_STYLE: dict[Severity, tuple[str, str]] = {
    Severity.ERROR: ("✖", ERROR),
    Severity.WARNING: ("⚠", WARN),
    Severity.INFO: ("ℹ", INFO),
}

TEMPLATE_STATUS_STYLE: dict[TemplateStatus, tuple[str, str]] = {
    TemplateStatus.ACTIVE: ("✔ Aktiv", OK),
    TemplateStatus.MISSING_ORIGINAL: ("○ Originalvorlage fehlt", TEXT_MUTED),
    TemplateStatus.NEEDS_REVIEW: ("◐ Zu prüfen", WARN),
    TemplateStatus.ERROR: ("✖ Fehler", ERROR),
}

LOCAL_ONLY_TEXT = "🔒 Lokale Verarbeitung – keine Cloud-Übertragung"

STYLESHEET = f"""
QWidget {{ color: {TEXT}; font-size: 10.5pt; }}
QMainWindow, QStackedWidget > QWidget {{ background: {BG}; }}
#Sidebar {{ background: {SIDEBAR}; }}
#Sidebar QPushButton {{
    color: {SIDEBAR_TEXT}; background: transparent; border: none; text-align: left;
    padding: 10px 16px; font-size: 11pt; border-radius: 6px; margin: 2px 8px;
}}
#Sidebar QPushButton:hover {{ color: {TECIS_LIMETTE}; }}
#Sidebar QPushButton:checked {{ background: {TECIS_LIMETTE}; color: {TECIS_BLAU}; font-weight: 700; }}
#AppTitle {{
    color: {WEISS}; font-family: {HEADLINE_STACK}; font-size: 16pt; font-weight: 700;
    padding: 18px 16px 6px 16px;
}}
#AppSubtitle {{ color: {WEISS}; font-size: 9pt; padding: 0 16px 14px 16px; }}
#LocalBadge {{
    color: {TECIS_BLAU}; background: {TECIS_LIMETTE}; border: 1px solid {TECIS_LIMETTE}; border-radius: 12px;
    padding: 4px 12px; font-weight: 700;
}}
#PageTitle {{
    font-family: {HEADLINE_STACK}; font-size: 20pt; font-weight: 700; color: {TEXT};
    border: none; border-left: 6px solid {ACCENT}; padding-left: 10px;
}}
#SectionTitle {{ font-size: 12.5pt; font-weight: 700; color: {TEXT}; margin-top: 6px; }}
#Muted {{ color: {TEXT_MUTED}; }}
#Card, QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
#DropArea {{
    background: {SURFACE}; border: 2px dashed {ACCENT}; border-radius: 14px;
}}
#DropArea[dragActive="true"] {{ background: {HIGHLIGHT_BG}; }}
QPushButton {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px;
    font-weight: 500;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {DISABLED_TEXT}; background: {BG_SUBTLE}; }}
QPushButton[primary="true"] {{
    background: {PRIMARY}; color: {WEISS}; border: 1px solid {PRIMARY}; font-weight: 700;
}}
QPushButton[primary="true"]:hover {{ color: {TECIS_LIMETTE}; border-color: {TECIS_LIMETTE}; }}
QPushButton[primary="true"]:disabled {{
    background: {DISABLED_BG}; border-color: {DISABLED_BG}; color: {DISABLED_TEXT};
}}
QPushButton[danger="true"] {{ color: {ERROR}; border-color: {ERROR}; }}
QPushButton[danger="true"]:disabled {{ color: {DISABLED_TEXT}; border-color: {BORDER}; background: {BG_SUBTLE}; }}
QPushButton:checked {{ background: {SELECT_BG}; border-color: {PRIMARY}; color: {PRIMARY}; font-weight: 700; }}
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QTableWidget, QTableView, QTreeWidget, QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: {BG_SUBTLE};
    selection-background-color: {SELECT_BG}; selection-color: {TEXT};
}}
QHeaderView::section {{
    background: {BG_SUBTLE}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; font-weight: 700;
}}
QTabBar::tab {{ padding: 8px 16px; }}
QTabBar::tab:selected {{ font-weight: 700; color: {TEXT}; border-bottom: 3px solid {ACCENT}; }}
QToolTip {{ background: {WEISS}; color: {TEXT}; border: 1px solid {BORDER}; }}
QProgressBar {{ border: 1px solid {BORDER}; border-radius: 6px; background: {SURFACE}; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
"""


def status_text(status: FieldStatus) -> str:
    sym, txt, _ = STATUS_STYLE[status]
    return f"{sym} {txt}"


def status_color(status: FieldStatus) -> str:
    return STATUS_STYLE[status][2]
