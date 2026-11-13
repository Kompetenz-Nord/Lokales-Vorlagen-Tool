"""Schriftmetriken, Zeichenabdeckung und Rendering (MuPDF/PDFium)."""

from __future__ import annotations

import pytest
from pdf_test_helpers import FONTS_DIR, make_form_pdf

from insurance_app.pdf.fonts import (
    fit_font_size,
    fit_font_size_multiline,
    font_file,
    is_winansi_encodable,
    text_width,
    unsupported_chars,
    wrap_text,
)
from insurance_app.pdf.render import (
    RenderError,
    available_renderers,
    render_all,
    render_page,
    save_previews,
)


def test_text_width_helvetica_metrics():
    assert text_width("", 9) == 0.0
    assert text_width("1.234,56", 9) == pytest.approx(35.03, abs=0.05)
    assert text_width("Muster", 10) == pytest.approx(2 * text_width("Muster", 5))


def test_fit_font_size_keeps_base_when_text_fits():
    assert fit_font_size("Musterstadt", 136, 9) == 9


def test_fit_font_size_shrinks_and_fits():
    text = "Muster Bäckerei GmbH & Co. KG Zweigniederlassung"
    size = fit_font_size(text, 160, 9)
    assert size is not None and 6.0 <= size < 9
    assert text_width(text, size) <= 160 - 4.0


def test_fit_font_size_returns_none_when_too_long():
    assert fit_font_size("Ein sehr langer Mustertext " * 4, 60, 9) is None
    assert fit_font_size("x", 3, 9) is None  # Rand größer als Feld
    with pytest.raises(ValueError):
        fit_font_size("x", 50, 0)


def test_multiline_fit_and_wrap():
    text = "Erste Zeile mit Mustertext\nZweite Zeile mit noch mehr Mustertext"
    lines = wrap_text(text, 120, 9)
    assert len(lines) >= 2
    assert fit_font_size_multiline(text, 200, 40, 9) is not None
    assert fit_font_size_multiline(text * 10, 100, 20, 9) is None


def test_winansi_coverage():
    assert is_winansi_encodable("Große Straße 12a – Bäckerei € ÄÖÜ äöü ß")
    assert unsupported_chars("Łódź Gdańsk") == ["Ł", "ź", "ń"]
    assert "ő" in unsupported_chars("Erdős")
    assert unsupported_chars("ok\u0007") == ["\u0007"]
    assert unsupported_chars("Zeile\nZeile") == []
    assert not is_winansi_encodable("😀")


def test_font_file_present():
    assert font_file(FONTS_DIR).name == "LiberationSans-Regular.ttf"
    assert font_file(FONTS_DIR, bold=True).name == "LiberationSans-Bold.ttf"
    with pytest.raises(FileNotFoundError):
        font_file(FONTS_DIR / "fehlt")


def test_available_renderers():
    assert available_renderers()[0] == "mupdf"
    assert "pdfium" in available_renderers()


@pytest.mark.slow
@pytest.mark.parametrize("renderer", ["mupdf", "pdfium"])
def test_render_page_and_all(tmp_path, renderer):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    img = render_page(pdf, 1, dpi=72, renderer=renderer)
    assert img.mode == "RGB" and img.size == (595, 842)
    images = render_all(pdf, dpi=50, renderer=renderer)
    assert len(images) == 1
    paths = save_previews(pdf, tmp_path / "png", dpi=40, renderer=renderer)
    assert [p.name for p in paths] == ["seite_01.png"] and paths[0].stat().st_size > 0


@pytest.mark.slow
def test_render_encrypted_template_with_empty_password(tmp_path):
    pdf = make_form_pdf(tmp_path / "muster.pdf", encryption="no_modify")
    for renderer in ("mupdf", "pdfium"):
        assert render_page(pdf, 1, dpi=40, renderer=renderer).size[0] > 0


def test_render_errors(tmp_path):
    pdf = make_form_pdf(tmp_path / "muster.pdf")
    with pytest.raises(RenderError):
        render_page(pdf, 2)
    with pytest.raises(RenderError):
        render_page(tmp_path / "fehlt.pdf", 1)
    with pytest.raises(ValueError):
        render_page(pdf, 1, renderer="unbekannt")
    with pytest.raises(ValueError):
        render_page(pdf, 1, dpi=5000)
    locked = make_form_pdf(tmp_path / "pw.pdf", encryption="password")
    with pytest.raises(RenderError):
        render_page(locked, 1, renderer="mupdf")
    assert render_page(locked, 1, dpi=40, renderer="mupdf", password="muster").size[0] > 0
    with pytest.raises(RenderError):
        render_page(locked, 1, dpi=40, renderer="pdfium")
