"""Bildvorverarbeitung (ocr.preprocess)."""

from __future__ import annotations

import numpy as np
import pytest
from ocr_samples import font
from PIL import Image, ImageDraw

from insurance_app.ocr.preprocess import (
    binarize,
    deskew,
    estimate_skew,
    rotate_point_back,
    to_grayscale,
    upscale_if_small,
)


def lines_image() -> Image.Image:
    img = Image.new("L", (1600, 1100), 255)
    draw = ImageDraw.Draw(img)
    f = font(36)
    for i in range(10):
        draw.text((80, 60 + i * 100), f"MUSTER Versicherungsnummer 4711-0815 Zeile {i}", font=f, fill=0)
    return img


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L", "1", "P", "LA", "CMYK"])
def test_to_grayscale_modes(mode: str):
    img = Image.new("RGB", (20, 10), "white").convert(mode)
    gray = to_grayscale(img)
    assert gray.mode == "L"
    assert gray.size == (20, 10)


def test_to_grayscale_transparent_is_white():
    gray = to_grayscale(Image.new("RGBA", (5, 5), (0, 0, 0, 0)))
    assert gray.getpixel((2, 2)) == 255


def test_upscale_if_small():
    img = Image.new("L", (600, 800), 255)
    up, factor = upscale_if_small(img, 100)
    assert factor == pytest.approx(3.0)
    assert up.size == (1800, 2400)
    same, factor = upscale_if_small(img, 300)
    assert factor == 1.0 and same is img
    # Pixelgrenze
    big, factor = upscale_if_small(Image.new("L", (3000, 3000), 255), 72, max_pixels=20_000_000)
    assert big.width * big.height <= 20_000_000 * 1.01
    assert 1.0 < factor < 3.0


def test_binarize_marks_dark_pixels():
    img = Image.new("L", (10, 10), 255)
    img.paste(0, (0, 0, 5, 10))
    mask = binarize(img)
    assert mask[:, :5].all()
    assert not mask[:, 5:].any()


@pytest.mark.parametrize("skew", [2.0, -3.0, 1.25])
def test_estimate_skew_recovers_angle(skew: float):
    tilted = lines_image().rotate(skew, resample=Image.Resampling.BICUBIC, fillcolor=255)
    assert estimate_skew(tilted) == pytest.approx(-skew, abs=0.3)


def test_estimate_skew_straight_and_blank():
    assert estimate_skew(lines_image()) == 0.0
    assert estimate_skew(Image.new("L", (500, 500), 255)) == 0.0


def test_deskew_threshold():
    img = lines_image()
    same, angle = deskew(img)
    assert angle == 0.0 and same is img
    tilted = img.rotate(2.0, resample=Image.Resampling.BICUBIC, fillcolor=255)
    fixed, angle = deskew(tilted)
    assert angle == pytest.approx(-2.0, abs=0.3)
    assert fixed.size == tilted.size
    assert estimate_skew(fixed) == pytest.approx(0.0, abs=0.3)


def test_rotate_point_back_inverts_pil_rotation():
    img = Image.new("L", (400, 300), 255)
    img.paste(0, (300, 100, 305, 105))
    for angle in (3.0, -4.0, 25.0):
        rotated = img.rotate(angle, resample=Image.Resampling.NEAREST, fillcolor=255)
        ys, xs = np.where(np.asarray(rotated) < 128)
        x, y = rotate_point_back(float(xs.mean()), float(ys.mean()), angle, 200, 150)
        assert x == pytest.approx(302, abs=1.5)
        assert y == pytest.approx(102, abs=1.5)
    assert rotate_point_back(5, 6, 0.0, 1, 1) == (5, 6)
