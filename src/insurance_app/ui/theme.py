"""Gestaltung: Farben, Abstände, Stylesheet (ruhig, gut lesbar, ausreichender Kontrast).

Regeln für alle Seiten
* Status nie nur über Farbe zeigen – immer auch Text/Symbol (Barrierefreiheit).
* Schriftgröße mindestens 10 pt, Hauptaktionen als ``primary``-Buttons.
* Texte in einfacher Sprache, Fachbegriffe kurz erklären (Tooltip oder Hilfetext).
"""

from __future__ import annotations

from ..core.models import FieldStatus, Severity
from ..templates.schema import TemplateStatus

# Farbpalette (WCAG-AA-Kontrast auf Weiß geprüft für Textfarben)
PRIMARY = "#1F5FAD"
PRIMARY_DARK = "#174A87"
BG = "#F5F7FA"
SURFACE = "#FFFFFF"
BORDER = "#D5DBE3"
TEXT = "#1C2430"
TEXT_MUTED = "#55606E"
OK = "#1E7B34"
WARN = "#8A5A00"
ERROR = "#B3261E"
INFO = "#1F5FAD"
SIDEBAR = "#16263D"
SIDEBAR_TEXT = "#E8EEF6"

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
#Sidebar QPushButton:hover {{ background: #22395A; }}
#Sidebar QPushButton:checked {{ background: {PRIMARY}; font-weight: 600; }}
#AppTitle {{ color: white; font-size: 13pt; font-weight: 700; padding: 18px 16px 6px 16px; }}
#AppSubtitle {{ color: #AFC0D6; font-size: 9pt; padding: 0 16px 14px 16px; }}
#LocalBadge {{
    color: {OK}; background: #E6F4EA; border: 1px solid #B7DFC2; border-radius: 12px;
    padding: 4px 12px; font-weight: 600;
}}
#PageTitle {{ font-size: 17pt; font-weight: 700; color: {TEXT}; }}
#SectionTitle {{ font-size: 12.5pt; font-weight: 700; color: {TEXT}; margin-top: 6px; }}
#Muted {{ color: {TEXT_MUTED}; }}
#Card, QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
#DropArea {{
    background: {SURFACE}; border: 2px dashed {PRIMARY}; border-radius: 14px;
}}
#DropArea[dragActive="true"] {{ background: #E8F0FB; }}
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {PRIMARY}; }}
QPushButton:disabled {{ color: #9AA4B1; background: #EEF1F5; }}
QPushButton[primary="true"] {{
    background: {PRIMARY}; color: white; border: 1px solid {PRIMARY_DARK}; font-weight: 600;
}}
QPushButton[primary="true"]:hover {{ background: {PRIMARY_DARK}; }}
QPushButton[primary="true"]:disabled {{ background: #9DB5D6; border-color: #9DB5D6; color: #F2F5F9; }}
QPushButton[danger="true"] {{ color: {ERROR}; border-color: #E3B4B0; }}
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {PRIMARY}; }}
QTableWidget, QTableView, QTreeWidget, QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: #EDF0F4;
    selection-background-color: #DCE8F7; selection-color: {TEXT};
}}
QHeaderView::section {{
    background: #EEF2F7; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; font-weight: 600;
}}
QTabBar::tab {{ padding: 8px 16px; }}
QTabBar::tab:selected {{ font-weight: 600; color: {PRIMARY}; }}
QToolTip {{ background: #FFFDF5; color: {TEXT}; border: 1px solid {BORDER}; }}
"""


def status_text(status: FieldStatus) -> str:
    sym, txt, _ = STATUS_STYLE[status]
    return f"{sym} {txt}"


def status_color(status: FieldStatus) -> str:
    return STATUS_STYLE[status][2]
