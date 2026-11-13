"""Seitenzugriff für PDFs und Bilddateien: Seitenzahl, Seitengröße, Seitenbild.

Alles geschieht **nur lesend und im Speicher**: Die Datei wird einmal als
Bytes gelesen; es entstehen keine temporären Dateien.

Koordinatenkonvention (passend zu ``core/models.py`` und zur OCR)
------------------------------------------------------------------
* Seitenkoordinaten haben den Ursprung oben links, Einheit PDF-Punkt (1/72 Zoll).
* **PDF**: Maßgeblich ist die *angezeigte* Seite (CropBox, ``/Rotate``
  angewendet). ``page_size_points()`` liefert ``page.rect``; ``load_page_image()``
  rendert genau diese Ansicht.
* **Bilddateien** (PNG/JPEG/TIFF): Maßgeblich ist das *angezeigte* Bild
  (EXIF-Ausrichtung angewendet). Punkte = Pixel × 72 / dpi. Die dpi stammen aus
  den Bild-Metadaten, wenn sie zwischen 50 und 1200 liegen, sonst gelten 150 dpi.
* Für jedes Bild aus ``load_page_image()`` gilt
  ``page_size_points()[0] / image.width == 72 / image_effective_dpi(image)``.
  OCR-Wortboxen in Pixeln werden also mit ``page_width / image.width`` in
  Seitenkoordinaten umgerechnet.
* ``PageText.rotation`` bzw. ``PageInfo.rotation``: Drehung (im Uhrzeigersinn)
  des Koordinatensystems gegenüber der ungedrehten Rohseite (PDF ``/Rotate``
  bzw. EXIF-Ausrichtung).
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
from pathlib import Path
from types import TracebackType

import numpy as np
import pymupdf
from PIL import Image, ImageSequence, UnidentifiedImageError

from .filetypes import MAX_FILE_SIZE_BYTES, FileType, detect_file_type_from_bytes

log = logging.getLogger(__name__)

#: Annahme, wenn ein Bild keine brauchbare Auflösung in den Metadaten hat.
DEFAULT_IMAGE_DPI = 150.0
#: Gültiger Bereich für dpi-Angaben aus Bild-Metadaten.
MIN_METADATA_DPI = 50.0
MAX_METADATA_DPI = 1200.0
#: Obergrenze für gerenderte Seitenbilder (Pixel); größere Seiten werden mit weniger dpi gerendert.
MAX_RENDER_PIXELS = 60_000_000
#: Zulässiger dpi-Bereich beim Rendern von PDF-Seiten.
MIN_RENDER_DPI = 18
MAX_RENDER_DPI = 1200

# Exakte Auflösung gerenderter/geladener Seitenbilder (zusätzlich zu info["dpi"]).
_DPI_INFO_KEY = "insurance_app_dpi"
_EXIF_ORIENTATION_TAG = 274

# EXIF-Ausrichtung -> (Transponierung, Drehung im Uhrzeigersinn in Grad)
_ORIENTATION_OPS: dict[int, tuple[Image.Transpose | None, int]] = {
    1: (None, 0),
    2: (Image.Transpose.FLIP_LEFT_RIGHT, 0),
    3: (Image.Transpose.ROTATE_180, 180),
    4: (Image.Transpose.FLIP_TOP_BOTTOM, 180),
    5: (Image.Transpose.TRANSPOSE, 90),
    6: (Image.Transpose.ROTATE_270, 90),
    7: (Image.Transpose.TRANSVERSE, 270),
    8: (Image.Transpose.ROTATE_90, 270),
}


# ---------------------------------------------------------------------------
# Fehler
# ---------------------------------------------------------------------------


class DocumentAccessError(RuntimeError):
    """Dokument kann nicht gelesen werden (Meldung auf Deutsch, ohne Inhalte)."""


class PasswordRequiredError(DocumentAccessError):
    """PDF benötigt ein Öffnungspasswort."""

    def __init__(self, message: str = "Das PDF ist passwortgeschützt. Bitte Passwort eingeben.") -> None:
        super().__init__(message)


class WrongPasswordError(PasswordRequiredError):
    """Das angegebene Passwort passt nicht."""

    def __init__(self, message: str = "Das eingegebene Passwort ist falsch. Bitte erneut eingeben.") -> None:
        super().__init__(message)


class PageNotFoundError(DocumentAccessError):
    """Seitennummer außerhalb des Dokuments."""


# ---------------------------------------------------------------------------
# Bild-Hilfen
# ---------------------------------------------------------------------------


def _valid_dpi(value: object) -> float | None:
    try:
        dpi = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(dpi) or not MIN_METADATA_DPI <= dpi <= MAX_METADATA_DPI:
        return None
    # PNG speichert Punkte je Meter (z. B. 199.9996 statt 200) -> auf 0,01 runden
    return round(dpi, 2)


def metadata_dpi(image: Image.Image) -> tuple[float, float]:
    """(x-dpi, y-dpi) aus den Metadaten; ungültig/fehlend -> 150 dpi (je Achse)."""
    raw = image.info.get("dpi")
    if isinstance(raw, tuple | list) and len(raw) >= 2:
        x, y = _valid_dpi(raw[0]), _valid_dpi(raw[1])
    else:
        x = y = _valid_dpi(raw) if raw is not None else None
    if x is None and y is None:
        return DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI
    return (x or y or DEFAULT_IMAGE_DPI), (y or x or DEFAULT_IMAGE_DPI)


def exif_orientation(image: Image.Image) -> int:
    """EXIF-Ausrichtung (1..8), 1 bei fehlender/ungültiger Angabe."""
    try:
        value = image.getexif().get(_EXIF_ORIENTATION_TAG, 1)
    except (AttributeError, OSError, ValueError, SyntaxError):
        return 1
    try:
        orientation = int(value)
    except (TypeError, ValueError):
        return 1
    return orientation if orientation in _ORIENTATION_OPS else 1


def orientation_rotation(orientation: int) -> int:
    """Drehung im Uhrzeigersinn (0/90/180/270) zu einer EXIF-Ausrichtung."""
    return _ORIENTATION_OPS.get(orientation, (None, 0))[1]


def orientation_swaps_axes(orientation: int) -> bool:
    return orientation in (5, 6, 7, 8)


def apply_orientation(image: Image.Image, orientation: int) -> Image.Image:
    """Bild gemäß EXIF-Ausrichtung in die Anzeigelage bringen."""
    op = _ORIENTATION_OPS.get(orientation, (None, 0))[0]
    return image.transpose(op) if op is not None else image


def to_rgb(image: Image.Image) -> Image.Image:
    """Beliebigen Bildmodus nach RGB wandeln (Transparenz auf Weiß, 16 Bit skaliert)."""
    mode = image.mode
    if mode == "RGB":
        return image
    if mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        arr = np.asarray(image, dtype=np.float64)
        lo, hi = float(arr.min()), float(arr.max())
        scale = 255.0 / (hi - lo) if hi > lo else 0.0
        arr8 = ((arr - lo) * scale).clip(0, 255).astype(np.uint8)
        return Image.fromarray(arr8).convert("RGB")
    if mode == "P" and "transparency" in image.info:
        image = image.convert("RGBA")
        mode = "RGBA"
    if mode in ("RGBA", "LA", "PA", "RGBa", "La"):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _set_image_dpi(image: Image.Image, dpi_x: float, dpi_y: float) -> None:
    image.info["dpi"] = (dpi_x, dpi_y)
    image.info[_DPI_INFO_KEY] = (dpi_x, dpi_y)


def image_effective_dpi(image: Image.Image) -> float:
    """Auflösung eines Seitenbilds in dpi (horizontal).

    Für Bilder aus ``load_page_image()``/``PageSource.render()`` exakt (passt zu
    ``page_size_points``), sonst aus den Metadaten (50..1200) bzw. 150 dpi.
    """
    exact = image.info.get(_DPI_INFO_KEY)
    if isinstance(exact, tuple) and exact and float(exact[0]) > 0:
        return float(exact[0])
    return metadata_dpi(image)[0]


# ---------------------------------------------------------------------------
# Dokumentzugriff
# ---------------------------------------------------------------------------


def read_document_bytes(path: Path, max_size: int = MAX_FILE_SIZE_BYTES) -> bytes:
    """Datei vollständig lesen (nur lesend); Fehler als ``DocumentAccessError``."""
    try:
        with Path(path).open("rb") as fh:
            data = fh.read(max_size + 1)
    except FileNotFoundError as exc:
        raise DocumentAccessError("Die Datei wurde nicht gefunden.") from exc
    except OSError as exc:
        raise DocumentAccessError("Die Datei kann nicht gelesen werden.") from exc
    if len(data) > max_size:
        raise DocumentAccessError("Die Datei ist zu groß.")
    return data


class _ImageFrame:
    __slots__ = ("dpi_x", "dpi_y", "height_px", "orientation", "width_px")

    def __init__(self, width_px: int, height_px: int, dpi_x: float, dpi_y: float, orientation: int) -> None:
        self.width_px = width_px
        self.height_px = height_px
        self.dpi_x = dpi_x
        self.dpi_y = dpi_y
        self.orientation = orientation


class PageSource:
    """Geöffnetes Dokument (PDF oder Bild) für seitenweisen Zugriff.

    Nur lesend, vollständig im Speicher. Nicht threadsicher: je Thread eine
    eigene Instanz verwenden. Seitennummern sind 1-basiert.
    """

    def __init__(self, path: Path, password: str | None = None, *, data: bytes | None = None) -> None:
        self.path = Path(path)
        self._data = data if data is not None else read_document_bytes(self.path)
        self._sha256: str | None = None
        self._doc: pymupdf.Document | None = None
        self._image: Image.Image | None = None
        self._frames: list[_ImageFrame] = []
        file_type = detect_file_type_from_bytes(self._data[:2048])
        if file_type == "unknown":
            raise DocumentAccessError("Dateityp wird nicht unterstützt.")
        self.file_type: FileType = file_type
        if file_type == "pdf":
            self._open_pdf(password)
        else:
            self._open_image()

    # -- Öffnen -----------------------------------------------------------
    def _open_pdf(self, password: str | None) -> None:
        try:
            doc = pymupdf.open(stream=self._data, filetype="pdf")
        except (RuntimeError, ValueError) as exc:
            raise DocumentAccessError("Die PDF-Datei ist beschädigt und kann nicht geöffnet werden.") from exc
        if doc.needs_pass:
            if not password:
                doc.close()
                raise PasswordRequiredError()
            if not doc.authenticate(password):
                doc.close()
                raise WrongPasswordError()
        self._doc = doc

    def _open_image(self) -> None:
        try:
            image = Image.open(io.BytesIO(self._data))
            frames: list[_ImageFrame] = []
            iterator = ImageSequence.Iterator(image) if self.file_type == "tiff" else [image]
            for frame in iterator:
                orientation = exif_orientation(frame)
                dpi_x, dpi_y = metadata_dpi(frame)
                w, h = frame.size
                if orientation_swaps_axes(orientation):
                    w, h, dpi_x, dpi_y = h, w, dpi_y, dpi_x
                frames.append(_ImageFrame(w, h, dpi_x, dpi_y, orientation))
        except Image.DecompressionBombError as exc:
            raise DocumentAccessError("Das Bild hat zu viele Bildpunkte.") from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as exc:
            raise DocumentAccessError("Die Bilddatei ist beschädigt und kann nicht gelesen werden.") from exc
        if not frames:
            raise DocumentAccessError("Die Bilddatei enthält keine Seiten.")
        self._image = image
        self._frames = frames

    # -- Eigenschaften ----------------------------------------------------
    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            self._sha256 = hashlib.sha256(self._data).hexdigest()
        return self._sha256

    @property
    def is_pdf(self) -> bool:
        return self._doc is not None

    @property
    def page_count(self) -> int:
        if self._doc is not None:
            return self._doc.page_count
        return len(self._frames)

    @property
    def pdf_document(self) -> pymupdf.Document:
        if self._doc is None:
            raise DocumentAccessError("Das Dokument ist kein PDF.")
        return self._doc

    def _check_page(self, page_number: int) -> None:
        if not 1 <= page_number <= self.page_count:
            raise PageNotFoundError(f"Seite {page_number} gibt es nicht (Dokument hat {self.page_count}).")

    def pdf_page(self, page_number: int) -> pymupdf.Page:
        """PyMuPDF-Seite (1-basiert)."""
        self._check_page(page_number)
        try:
            return self.pdf_document.load_page(page_number - 1)
        except (RuntimeError, ValueError) as exc:
            raise DocumentAccessError(f"Seite {page_number} kann nicht gelesen werden.") from exc

    def page_size_points(self, page_number: int) -> tuple[float, float]:
        """(Breite, Höhe) der angezeigten Seite in PDF-Punkten."""
        self._check_page(page_number)
        if self._doc is not None:
            rect = self.pdf_page(page_number).rect
            return float(rect.width), float(rect.height)
        frame = self._frames[page_number - 1]
        return frame.width_px * 72.0 / frame.dpi_x, frame.height_px * 72.0 / frame.dpi_y

    def page_rotation(self, page_number: int) -> int:
        """Drehung im Uhrzeigersinn gegenüber der Rohseite (PDF /Rotate bzw. EXIF)."""
        self._check_page(page_number)
        if self._doc is not None:
            return int(self.pdf_page(page_number).rotation) % 360
        return orientation_rotation(self._frames[page_number - 1].orientation)

    def render(self, page_number: int, dpi: int = 200) -> Image.Image:
        """Seitenbild (RGB) in der Anzeigelage.

        PDF: gerendert mit ``dpi`` (bei sehr großen Seiten automatisch weniger,
        siehe ``MAX_RENDER_PIXELS``). Bilddateien: native Auflösung, ``dpi``
        wird ignoriert. Die tatsächliche Auflösung liefert ``image_effective_dpi``.
        """
        self._check_page(page_number)
        if self._doc is not None:
            return self._render_pdf(page_number, dpi)
        return self._render_image(page_number)

    def _render_pdf(self, page_number: int, dpi: int) -> Image.Image:
        page = self.pdf_page(page_number)
        rect = page.rect
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            raise DocumentAccessError(f"Seite {page_number} hat keine gültige Größe.")
        target = float(min(max(int(dpi), MIN_RENDER_DPI), MAX_RENDER_DPI))
        pixels = (rect.width * target / 72.0) * (rect.height * target / 72.0)
        if pixels > MAX_RENDER_PIXELS:
            target *= math.sqrt(MAX_RENDER_PIXELS / pixels)
            log.info("Seite %d wird mit reduzierter Auflösung gerendert (%.0f dpi)", page_number, target)
        zoom = target / 72.0
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, colorspace=pymupdf.csRGB)
        except (RuntimeError, ValueError, MemoryError) as exc:
            raise DocumentAccessError(f"Seite {page_number} kann nicht dargestellt werden.") from exc
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples, "raw", "RGB", pix.stride)
        _set_image_dpi(image, pix.width * 72.0 / rect.width, pix.height * 72.0 / rect.height)
        return image

    def _render_image(self, page_number: int) -> Image.Image:
        assert self._image is not None
        frame_info = self._frames[page_number - 1]
        try:
            if self.file_type == "tiff":
                self._image.seek(page_number - 1)
            raw = self._image.copy()
        except Image.DecompressionBombError as exc:
            raise DocumentAccessError("Das Bild hat zu viele Bildpunkte.") from exc
        except (OSError, ValueError, SyntaxError, EOFError) as exc:
            raise DocumentAccessError("Die Bilddatei ist beschädigt und kann nicht gelesen werden.") from exc
        image = to_rgb(apply_orientation(raw, frame_info.orientation))
        image.info.pop("exif", None)
        _set_image_dpi(image, frame_info.dpi_x, frame_info.dpi_y)
        return image

    # -- Lebenszyklus -----------------------------------------------------
    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        if self._image is not None:
            self._image.close()
            self._image = None

    def __enter__(self) -> PageSource:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def open_document(path: Path, password: str | None = None) -> PageSource:
    """Dokument für seitenweisen Zugriff öffnen (mit ``with`` verwenden)."""
    return PageSource(path, password)


def page_count(path: Path, password: str | None = None) -> int:
    """Seitenanzahl eines PDFs oder Bilds (TIFF: Anzahl Bilder)."""
    with PageSource(path, password) as source:
        return source.page_count


def load_page_image(path: Path, page_number: int, dpi: int = 200, password: str | None = None) -> Image.Image:
    """Seitenbild (RGB, 1-basierte Seitennummer) wie angezeigt, inkl. Drehung.

    PDF: mit ``dpi`` gerendert. Bilder: native Auflösung, EXIF-Ausrichtung
    angewendet, TIFF-Seite gewählt. Auflösung: ``image_effective_dpi()``.
    """
    with PageSource(path, password) as source:
        return source.render(page_number, dpi)


def page_size_points(path: Path, page_number: int, password: str | None = None) -> tuple[float, float]:
    """(Breite, Höhe) der angezeigten Seite in PDF-Punkten (Bilder: Pixel × 72 / dpi)."""
    with PageSource(path, password) as source:
        return source.page_size_points(page_number)
