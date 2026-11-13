"""Gestaltung nach dem tecis Corporate Design Manual (Farben, Kombinationen, Schriften)."""

from __future__ import annotations

import re

import pytest
from PySide6.QtGui import QFontDatabase, QFontInfo
from PySide6.QtWidgets import QLabel

from insurance_app.ui import theme
from insurance_app.ui.app import create_application
from insurance_app.ui.widgets.common import page_title


def test_brand_colours_match_the_style_guide():
    assert (theme.TECIS_BLAU, theme.TECIS_HIMMELBLAU) == ("#00325B", "#009EE3")
    assert (theme.TECIS_LIMETTE, theme.TECIS_SAND) == ("#D3D800", "#CBCDB9")
    assert theme.PRIMARY == theme.SIDEBAR == theme.TEXT == theme.TECIS_BLAU
    assert theme.ACCENT == theme.TECIS_HIMMELBLAU and theme.BORDER == theme.TECIS_SAND


def test_tints_follow_the_rules():
    assert theme.tint(theme.TECIS_SAND, 100) == theme.TECIS_SAND
    assert theme.tint(theme.TECIS_SAND, 15) == theme.BG == "#F7F8F4"
    assert theme.tint(theme.SCHWARZ, 65) == theme.TEXT_MUTED == "#595959"
    for percent in (14, 0, 101):  # nie heller als 15 %
        with pytest.raises(ValueError):
            theme.tint(theme.TECIS_LIMETTE, percent)
    with pytest.raises(ValueError, match="Blau"):  # tecis Blau wird nie aufgerastert
        theme.tint(theme.TECIS_BLAU, 50)
    with pytest.raises(ValueError, match="Himmelblau"):  # nur in Infografiken
        theme.tint(theme.TECIS_HIMMELBLAU, 50)
    assert theme.tint(theme.TECIS_HIMMELBLAU, 50, infographic=True) == "#80CEF1"


def test_only_palette_colours_and_allowed_tints_are_used():
    """Im Stylesheet stehen nur Markenfarben, erlaubte Aufrasterungen und die drei Signalfarben."""
    allowed = {theme.TECIS_BLAU, theme.TECIS_HIMMELBLAU, theme.TECIS_LIMETTE, theme.TECIS_SAND}
    allowed |= {theme.SCHWARZ, theme.WEISS, theme.OK, theme.WARN, theme.ERROR}
    for base in (theme.TECIS_LIMETTE, theme.TECIS_SAND, theme.SCHWARZ):
        allowed |= {theme.tint(base, p) for p in range(15, 101)}
    used = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}", theme.STYLESHEET)}
    assert used <= allowed, sorted(used - allowed)


def test_combinations_used_by_the_app_are_approved():
    on = theme.ALLOWED_ON
    assert theme.SIDEBAR_TEXT in on[theme.SIDEBAR]  # Weiß auf Blau (Navigation, Hauptschaltflächen)
    assert theme.TECIS_LIMETTE in on[theme.TECIS_BLAU]  # Hover in der Navigation
    assert theme.TECIS_BLAU in on[theme.TECIS_LIMETTE]  # gewählter Navigationspunkt, Hinweisplakette
    assert theme.TECIS_BLAU in on[theme.TECIS_SAND]  # Text auf Sand-Flächen
    assert theme.WEISS not in on[theme.TECIS_LIMETTE] and theme.WEISS not in on[theme.TECIS_SAND]
    sheet = theme.STYLESHEET
    assert (
        f"#Sidebar QPushButton:checked {{ background: {theme.TECIS_LIMETTE}; color: {theme.TECIS_BLAU};"
        in sheet
    )
    assert f"background: {theme.PRIMARY}; color: {theme.WEISS};" in sheet


def test_no_hard_coded_colours_outside_the_theme():
    from pathlib import Path

    ui_dir = Path(theme.__file__).parent
    offenders = [
        f"{path.relative_to(ui_dir)}: {match}"
        for path in ui_dir.rglob("*.py")
        if path.name != "theme.py"
        for match in re.findall(r"#[0-9A-Fa-f]{6}\b", path.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_house_fonts_are_bundled_and_used(qtbot):
    for name in theme.BRAND_FONT_FILES:
        assert (theme.brand_fonts_dir() / name).is_file(), name
    assert (theme.brand_fonts_dir() / "LICENSE-DMSans-OFL.txt").is_file()
    assert (theme.brand_fonts_dir() / "LICENSE-Tinos-OFL.txt").is_file()
    app = create_application([])
    families = set(QFontDatabase.families())
    assert {"DM Sans", "Tinos"} <= families
    assert QFontInfo(app.font()).family() == "DM Sans"  # Fließtext
    body = QLabel("Fließtext")
    title = page_title("Überschrift")
    qtbot.addWidget(body)
    qtbot.addWidget(title)
    body.show()
    title.show()
    title.ensurePolished()
    assert QFontInfo(body.font()).family() == "DM Sans"
    assert QFontInfo(title.font()).family() == "Tinos" and title.font().bold()  # Tinos nur für Überschriften


def test_only_regular_medium_bold_weights():
    """Der Styleguide kennt nur Regular (400), Medium (500) und Bold (700)."""
    from pathlib import Path

    ui_dir = Path(theme.__file__).parent
    weights = {
        w
        for path in ui_dir.rglob("*.py")
        for w in re.findall(r"font-weight:\s*(\d+)", path.read_text(encoding="utf-8"))
    }
    assert weights <= {"400", "500", "700"}, weights


def test_fallback_fonts_follow_the_style_guide():
    """Ohne DM Sans gilt der Ersatz aus dem Styleguide (Arial); Symbole haben eigene Schriften."""
    with_brand = theme.body_families(["DM Sans", "Tinos"])
    assert with_brand[0] == "DM Sans"
    # Symbolschriften vor Arial: sonst stammt z. B. „◐“ aus einer Schrift mit unvollständigem Zeichen
    assert with_brand.index("DejaVu Sans") < with_brand.index("Arial")
    assert theme.body_families([])[0] == "Arial"
    assert "Times New Roman" in theme.HEADLINE_STACK and theme.HEADLINE_STACK.startswith('"Tinos"')
    assert "font-family" not in theme.STYLESHEET.split("#Sidebar")[0]  # Fließtext kommt aus der App-Schrift
