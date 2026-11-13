"""Seitenzugriff (documents.pages): Seitenzahl, Seitenbild, Koordinatenkonvention."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from doc_samples import A4, make_encrypted_pdf, make_text_pdf, png_bytes, text_image
from PIL import Image

from insurance_app.documents.pages import (
    DEFAULT_IMAGE_DPI,
    DocumentAccessError,
    PageNotFoundError,
    PageSource,
    PasswordRequiredError,
    WrongPasswordError,
    image_effective_dpi,
    load_page_image,
    metadata_dpi,
    page_count,
    page_size_points,
    to_rgb,
)


def test_pdf_page_count_and_size(tmp_path: Path):
    path = make_text_pdf(tmp_path / "a.pdf", pages=3)
    assert page_count(path) == 3
    w, h = page_size_points(path, 2)
    assert (w, h) == pytest.approx(A4, abs=0.5)


def test_pdf_render_matches_size_convention(tmp_path: Path):
    path = make_text_pdf(tmp_path / "a.pdf")
    img = load_page_image(path, 1, dpi=150)
    assert img.mode == "RGB"
    dpi = image_effective_dpi(img)
    assert dpi == pytest.approx(150, abs=0.5)
    w, h = page_size_points(path, 1)
    assert w / img.width == pytest.approx(72 / dpi, rel=1e-6)
    assert img.width == pytest.approx(A4[0] * 150 / 72, abs=1)


def test_pdf_render_respects_rotation(tmp_path: Path):
    path = make_text_pdf(tmp_path / "quer.pdf", rotation=90)
    img = load_page_image(path, 1, dpi=72)
    w, h = page_size_points(path, 1)
    assert w > h
    assert img.width > img.height
    with PageSource(path) as source:
        assert source.page_rotation(1) == 90


def test_page_out_of_range(tmp_path: Path):
    path = make_text_pdf(tmp_path / "a.pdf")
    with pytest.raises(PageNotFoundError):
        load_page_image(path, 2)
    with pytest.raises(PageNotFoundError):
        page_size_points(path, 0)


def test_password_errors(tmp_path: Path):
    path = make_encrypted_pdf(tmp_path / "zu.pdf", user_pw="MUSTER-pw")
    with pytest.raises(PasswordRequiredError) as excinfo:
        page_count(path)
    assert "passwortgeschützt" in str(excinfo.value)
    with pytest.raises(WrongPasswordError):
        page_count(path, password="MUSTER-nein")
    assert page_count(path, password="MUSTER-pw") == 1
    assert load_page_image(path, 1, dpi=50, password="MUSTER-pw").width > 0


def test_unsupported_and_missing(tmp_path: Path):
    txt = tmp_path / "a.txt"
    txt.write_text("MUSTER", encoding="utf-8")
    with pytest.raises(DocumentAccessError):
        page_count(txt)
    with pytest.raises(DocumentAccessError):
        page_count(tmp_path / "fehlt.pdf")


def test_png_page_uses_metadata_dpi(tmp_path: Path):
    path = tmp_path / "a.png"
    text_image(size_px=(1200, 900)).save(path, dpi=(200, 200))
    img = load_page_image(path, 1, dpi=999)  # dpi wird bei Bildern ignoriert
    assert img.size == (1200, 900)
    assert image_effective_dpi(img) == pytest.approx(200)
    assert page_size_points(path, 1) == pytest.approx((1200 * 72 / 200, 900 * 72 / 200))


def test_png_without_dpi(tmp_path: Path):
    path = tmp_path / "a.png"
    path.write_bytes(png_bytes(text_image(size_px=(300, 200))))
    img = load_page_image(path, 1)
    assert image_effective_dpi(img) == DEFAULT_IMAGE_DPI
    assert page_size_points(path, 1) == pytest.approx((300 * 72 / 150, 200 * 72 / 150))


def test_jpeg_exif_transpose(tmp_path: Path):
    img = Image.new("RGB", (400, 200), "white")
    img.paste((0, 0, 0), (0, 0, 40, 40))  # schwarzes Quadrat oben links (Rohbild)
    exif = Image.Exif()
    exif[274] = 6
    path = tmp_path / "foto.jpg"
    img.save(path, format="JPEG", exif=exif, quality=95, dpi=(150, 150))
    shown = load_page_image(path, 1)
    assert shown.size == (200, 400)
    arr = np.asarray(shown.convert("L"))
    # 90° im Uhrzeigersinn: oben links -> oben rechts
    assert arr[:30, -30:].mean() < 60
    assert arr[:30, :30].mean() > 200
    with PageSource(path) as source:
        assert source.page_rotation(1) == 90
        assert source.page_size_points(1) == pytest.approx((200 * 72 / 150, 400 * 72 / 150))


def test_multipage_tiff_frames(tmp_path: Path):
    path = tmp_path / "scan.tif"
    frames = [Image.new("RGB", (100, 140), c) for c in ("red", "green", "blue")]
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])
    assert page_count(path) == 3
    with PageSource(path) as source:
        colors = [source.render(n).getpixel((50, 70)) for n in (1, 2, 3)]
    assert colors[0][0] > 200 and colors[1][1] > 100 and colors[2][2] > 200


def test_rgba_is_flattened_on_white(tmp_path: Path):
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    path = tmp_path / "transparent.png"
    img.save(path)
    rendered = load_page_image(path, 1)
    assert rendered.mode == "RGB"
    assert rendered.getpixel((10, 10)) == (255, 255, 255)


def test_to_rgb_16bit():
    arr = (np.arange(0, 64 * 64, dtype=np.uint16).reshape(64, 64) * 16).astype(np.uint16)
    img = Image.fromarray(arr)
    assert img.mode.startswith("I;16")
    rgb = to_rgb(img)
    assert rgb.mode == "RGB"
    assert rgb.getpixel((0, 0)) == (0, 0, 0)
    assert rgb.getpixel((63, 63))[0] == 255


def test_metadata_dpi_fallback():
    img = Image.new("RGB", (10, 10))
    assert metadata_dpi(img) == (150.0, 150.0)
    img.info["dpi"] = (300, 300)
    assert metadata_dpi(img) == (300.0, 300.0)
    img.info["dpi"] = (2000, 2000)
    assert metadata_dpi(img) == (150.0, 150.0)


def test_source_sha256(tmp_path: Path):
    import hashlib

    path = make_text_pdf(tmp_path / "a.pdf")
    with PageSource(path) as source:
        assert source.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert source.is_pdf
        assert source.file_type == "pdf"
