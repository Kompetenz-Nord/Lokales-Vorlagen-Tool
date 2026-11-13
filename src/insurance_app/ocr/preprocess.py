"""Bildvorverarbeitung für die Texterkennung (Graustufen, Vergrößern, Begradigen, Entrauschen).

Alle Funktionen arbeiten im Speicher und verändern das übergebene Bild nicht.

Winkelkonvention: Winkel in Grad, positiv = gegen den Uhrzeigersinn – wie bei
``PIL.Image.rotate``. ``estimate_skew()`` liefert den Korrekturwinkel, d. h.
``image.rotate(estimate_skew(image))`` begradigt das Bild.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageFilter

from ..documents.pages import to_rgb

#: Zielauflösung für Tesseract.
TARGET_OCR_DPI = 300.0
#: Höchster Vergrößerungsfaktor.
MAX_UPSCALE_FACTOR = 3.0
#: Größte Bildfläche nach dem Vergrößern (ca. 1,5 × A4 bei 300 dpi).
MAX_UPSCALE_PIXELS = 14_000_000
#: Begradigung erst ab diesem Winkel (Grad).
MIN_DESKEW_ANGLE = 0.5
#: Gesuchter Winkelbereich (± Grad) und Schrittweite.
MAX_SKEW_ANGLE = 5.0
SKEW_STEP = 0.25

_SKEW_ANALYSIS_MAX_SIDE = 1200
_MIN_INK_SHARE = 0.002
_MAX_INK_SHARE = 0.5
_MIN_GAIN = 1.02  # bester Winkel muss das Profil um mind. 2 % gegenüber 0° verbessern


def to_grayscale(image: Image.Image) -> Image.Image:
    """Bild als 8-Bit-Graustufen ("L"); Transparenz wird auf Weiß gelegt."""
    if image.mode == "L":
        return image.copy()
    if image.mode == "1":
        return image.convert("L")
    return to_rgb(image).convert("L")


def upscale_if_small(
    image: Image.Image,
    dpi: float,
    target_dpi: float = TARGET_OCR_DPI,
    *,
    max_factor: float = MAX_UPSCALE_FACTOR,
    max_pixels: int = MAX_UPSCALE_PIXELS,
) -> tuple[Image.Image, float]:
    """Kleine Bilder auf ca. ``target_dpi`` vergrößern.

    Rückgabe: (Bild, angewendeter Faktor). Faktor 1.0 = unverändert. Begrenzt
    durch ``max_factor`` und ``max_pixels`` (Speicher/Laufzeit).
    """
    if dpi <= 0 or image.width == 0 or image.height == 0:
        return image, 1.0
    factor = min(target_dpi / dpi, max_factor)
    pixels = image.width * image.height
    if pixels * factor * factor > max_pixels:
        factor = math.sqrt(max_pixels / pixels)
    if factor < 1.1:
        return image, 1.0
    size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
    resized = image.resize(size, Image.Resampling.LANCZOS)
    # tatsächlicher Faktor (Rundung) – für die Rückrechnung der Koordinaten
    return resized, resized.width / image.width


def _otsu_threshold(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    if total == 0:
        return 128
    weights = np.arange(256, dtype=np.float64)
    cum_count = np.cumsum(hist)
    cum_mean = np.cumsum(hist * weights)
    global_mean = cum_mean[-1] / total
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (global_mean * cum_count - cum_mean) ** 2 / (cum_count * (total - cum_count))
    between = np.nan_to_num(between, nan=0.0, posinf=0.0, neginf=0.0)
    return int(np.argmax(between))


def binarize(image: Image.Image) -> np.ndarray:
    """Otsu-Binarisierung: ``True`` = dunkles Pixel (Schrift)."""
    gray = np.asarray(to_grayscale(image), dtype=np.uint8)
    return gray <= _otsu_threshold(gray)


def _profile_score(ink: Image.Image) -> float:
    rows = np.asarray(ink, dtype=np.float64).sum(axis=1)
    diffs = np.diff(rows)
    return float((diffs * diffs).sum())


def estimate_skew(image: Image.Image, max_angle: float = MAX_SKEW_ANGLE, step: float = SKEW_STEP) -> float:
    """Schräglage über Projektionsprofile schätzen (Korrekturwinkel in Grad, siehe Modul).

    Analyse auf einer verkleinerten, binarisierten Kopie; geprüft werden
    Winkel von ``-max_angle`` bis ``+max_angle`` in Schritten von ``step``.
    Ohne erkennbare Textzeilen wird 0.0 zurückgegeben.
    """
    if image.width < 16 or image.height < 16 or step <= 0:
        return 0.0
    gray = to_grayscale(image)
    longest = max(gray.size)
    if longest > _SKEW_ANALYSIS_MAX_SIDE:
        scale = _SKEW_ANALYSIS_MAX_SIDE / longest
        gray = gray.resize(
            (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
            Image.Resampling.BILINEAR,
        )
    ink_mask = binarize(gray)
    ink_share = float(ink_mask.mean())
    if not _MIN_INK_SHARE <= ink_share <= _MAX_INK_SHARE:
        return 0.0
    ink = Image.fromarray((ink_mask * 255).astype(np.uint8))
    steps = int(round(max_angle / step))
    angles = [i * step for i in range(-steps, steps + 1)]
    scores = {
        angle: _profile_score(ink.rotate(angle, resample=Image.Resampling.NEAREST, expand=False, fillcolor=0))
        for angle in angles
    }
    best_angle = max(scores, key=lambda a: (scores[a], -abs(a)))
    baseline = scores.get(0.0, 0.0)
    if best_angle == 0.0 or baseline <= 0 or scores[best_angle] < baseline * _MIN_GAIN:
        return 0.0
    return float(best_angle)


def deskew(image: Image.Image, min_angle: float = MIN_DESKEW_ANGLE) -> tuple[Image.Image, float]:
    """Bild begradigen, wenn die Schräglage größer als ``min_angle`` ist.

    Rückgabe: (Bild, angewendeter Winkel). Der Winkel ist 0.0, wenn nichts
    gedreht wurde. Größe bleibt gleich (Drehung um die Bildmitte, Rand weiß).
    """
    angle = estimate_skew(image)
    if abs(angle) <= min_angle:
        return image, 0.0
    fill: int | tuple[int, ...] = 255 if image.mode in ("L", "1") else (255,) * len(image.getbands())
    rotated = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=fill)
    return rotated, angle


def rotate_point_back(
    x: float, y: float, angle: float, center_x: float, center_y: float
) -> tuple[float, float]:
    """Punkt aus einem mit ``image.rotate(angle)`` gedrehten Bild ins Ausgangsbild zurückrechnen.

    Bildkoordinaten (y nach unten). ``image.rotate(angle)`` dreht den Inhalt
    sichtbar gegen den Uhrzeigersinn; hier wird diese Drehung umgekehrt.
    """
    if not angle:
        return x, y
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    dx, dy = x - center_x, y - center_y
    return center_x + dx * cos_a - dy * sin_a, center_y + dx * sin_a + dy * cos_a


def denoise_median(image: Image.Image, size: int = 3) -> Image.Image:
    """Punktrauschen („Salz und Pfeffer“) mit einem Medianfilter dämpfen.

    Der Filter ersetzt jeden Bildpunkt durch den mittleren Wert seiner
    ``size`` × ``size`` Nachbarn. Einzelne Störpunkte verschwinden, Buchstaben
    bleiben erhalten. ``size`` muss ungerade und mindestens 3 sein. Das Bild
    behält seine Größe; das Original bleibt unverändert.
    """
    if size < 3 or size % 2 == 0:
        raise ValueError("Die Filtergröße muss ungerade und mindestens 3 sein.")
    if image.width < size or image.height < size:
        return image.copy()
    return to_grayscale(image).filter(ImageFilter.MedianFilter(size))


def rotate_box_back(
    x0: float, y0: float, x1: float, y1: float, rotation: int, width: float, height: float
) -> tuple[float, float, float, float]:
    """Box aus einem aufgerichteten Bild ins Ausgangsbild zurückrechnen.

    ``rotation``: Drehung im Uhrzeigersinn (0/90/180/270), mit der das
    Ausgangsbild (``width`` × ``height``) aufgerichtet wurde – also
    ``image.rotate(-rotation, expand=True)``. Rückgabe: (x0, y0, x1, y1) im
    Ausgangsbild, Bildkoordinaten (y nach unten).
    """
    rotation %= 360
    if rotation == 90:
        return y0, height - x1, y1, height - x0
    if rotation == 180:
        return width - x1, height - y1, width - x0, height - y0
    if rotation == 270:
        return width - y1, x0, width - y0, x1
    if rotation:
        raise ValueError("Nur Drehungen um 0, 90, 180 oder 270 Grad werden unterstützt.")
    return x0, y0, x1, y1
