"""Eingangsprüfung hochgeladener Dokumente (Stufe 2 "Dokumentcheck").

``check_document()`` liest die Datei ausschließlich (einmal als Bytes, im
Speicher) und verändert sie nie. Ergebnis ist ein ``DocumentCheckResult`` mit
SHA-256, Größe, Seitenanzahl, Verschlüsselungs-/Passwortstatus, Seiteninfos
und Hinweisen mit stabilen Codes.

Codes (Schweregrad)
-------------------
``FILE_NOT_FOUND`` (ERROR), ``EMPTY_FILE`` (ERROR), ``TOO_LARGE`` (ERROR),
``UNSUPPORTED_TYPE`` (ERROR), ``PASSWORD_REQUIRED`` (ERROR),
``WRONG_PASSWORD`` (ERROR), ``DAMAGED`` (ERROR; WARNING, wenn die Datei nach
Reparatur im Speicher lesbar war), ``NO_PAGES`` (ERROR),
``EXTENSION_MISMATCH`` (WARNING), ``LOW_RESOLUTION`` (WARNING),
``PAGE_ROTATED`` (INFO), ``NO_TEXT_LAYER`` (INFO),
``EMBEDDED_OCR_SUSPECTED`` (INFO), ``UNUSUAL_PAGE_FORMAT`` (INFO).
``ok`` ist ``True``, wenn kein ERROR vorliegt.

Regeln
------
* Dateityp nur über Magic Bytes; eine abweichende Endung ergibt
  ``EXTENSION_MISMATCH``.
* Bildseiten: Größe in Punkten = Pixel × 72 / dpi (dpi aus Metadaten, wenn
  50..1200, sonst 150), Drehung aus der EXIF-Ausrichtung.
* ``LOW_RESOLUTION``: effektive Auflösung eines Bildes bezogen auf DIN A4 in
  gleicher Ausrichtung = max(Breite_px / Breite_A4_Zoll, Höhe_px / Höhe_A4_Zoll).
  Liegt sie unter 150 dpi, gibt es eine Warnung. Für ein Hochformat-Bild mit
  A4-Seitenverhältnis heißt das: weniger als ca. 1240 Pixel Breite.
  Hohe, schmale Handy-Screenshots gelten damit nicht als zu gering aufgelöst.
* ``UNUSUAL_PAGE_FORMAT`` nur für PDFs (Bildschirmfotos haben kein Papierformat).
* ``NO_TEXT_LAYER``: Seiten mit weniger als ``min_text_chars`` Buchstaben/Ziffern
  in der Textebene sowie alle Bildseiten.
* Heuristiken für Nur-Bild-Seiten und fremde OCR-Ebenen: siehe
  ``documents.pdf_analysis``.
* Passwörter werden nur zum Öffnen im Speicher verwendet, nie gespeichert oder
  protokolliert. ``needs_password`` bleibt ``True``, auch wenn das Passwort
  stimmt – Folgeschritte brauchen es ebenfalls.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image, ImageSequence, UnidentifiedImageError

from ..core.models import DocumentCheckResult, PageInfo, Severity, ValidationMessage
from .filetypes import (
    EXTENSIONS_BY_TYPE,
    FILE_TYPE_LABELS,
    MAX_FILE_SIZE_BYTES,
    SUPPORTED_EXTENSIONS,
    detect_file_type,
    detect_file_type_from_bytes,
)
from .pages import exif_orientation, metadata_dpi, orientation_rotation, orientation_swaps_axes
from .pdf_analysis import (
    DEFAULT_MIN_TEXT_CHARS,
    OTHER_FORMAT,
    count_meaningful_chars,
    image_coverage,
    is_embedded_ocr,
    is_image_only,
    page_format_name,
    pages_phrase,
    points_to_mm,
    text_visibility,
)

__all__ = [
    "LOW_RESOLUTION_DPI",
    "MAX_FILE_SIZE_BYTES",
    "SUPPORTED_EXTENSIONS",
    "check_document",
    "detect_file_type",
    "detect_file_type_from_bytes",
    "effective_a4_dpi",
]

log = logging.getLogger(__name__)

#: Unter dieser effektiven Auflösung (bezogen auf A4) gibt es ``LOW_RESOLUTION``.
LOW_RESOLUTION_DPI = 150.0
_A4_SHORT_IN = 210.0 / 25.4
_A4_LONG_IN = 297.0 / 25.4
_HASH_CHUNK = 1024 * 1024

# Meldungstexte (einfache Sprache)
MSG_PDF_LOCKED = "Das PDF ist passwortgeschützt. Bitte Passwort eingeben."
MSG_PDF_UNLOCK_FAILED = "Das eingegebene Passwort ist falsch. Bitte erneut eingeben."
MSG_NO_TEXT_LAYER = "Keine Textebene – Texterkennung (OCR) erforderlich"


@dataclass
class _Builder:
    file_type: str = "unknown"
    size_bytes: int = 0
    sha256: str = ""
    page_count: int = 0
    encrypted: bool = False
    needs_password: bool = False
    damaged: bool = False
    pages: list[PageInfo] = field(default_factory=list)
    issues: list[ValidationMessage] = field(default_factory=list)

    def add(self, severity: Severity, code: str, message: str) -> None:
        self.issues.append(ValidationMessage(severity=severity, code=code, message=message))

    def result(self) -> DocumentCheckResult:
        ok = not any(i.severity == Severity.ERROR for i in self.issues)
        return DocumentCheckResult(
            ok=ok,
            file_type=self.file_type,
            size_bytes=self.size_bytes,
            sha256=self.sha256,
            page_count=self.page_count,
            encrypted=self.encrypted,
            needs_password=self.needs_password,
            damaged=self.damaged,
            pages=self.pages,
            issues=self.issues,
        )


def effective_a4_dpi(width_px: int, height_px: int) -> float:
    """Effektive Auflösung eines Bildes, wenn es eine A4-Seite gleicher Ausrichtung darstellt."""
    if height_px >= width_px:
        return max(width_px / _A4_SHORT_IN, height_px / _A4_LONG_IN)
    return max(width_px / _A4_LONG_IN, height_px / _A4_SHORT_IN)


def _hash_file(path: Path) -> tuple[str, bytes]:
    """SHA-256 streamend berechnen; liefert zusätzlich die ersten Bytes."""
    digest = hashlib.sha256()
    head = b""
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            if not head:
                head = chunk[:2048]
            digest.update(chunk)
    return digest.hexdigest(), head


def check_document(
    path: Path,
    password: str | None = None,
    max_size: int = MAX_FILE_SIZE_BYTES,
    *,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
) -> DocumentCheckResult:
    """Datei prüfen, ohne sie zu verändern (siehe Moduldokumentation)."""
    started = time.perf_counter()
    path = Path(path)
    b = _Builder()
    try:
        _check(path, password, max_size, min_text_chars, b)
    finally:
        log.info(
            "Dokumentprüfung: typ=%s seiten=%d hinweise=%d dauer=%.2fs",
            b.file_type,
            b.page_count,
            len(b.issues),
            time.perf_counter() - started,
        )
    return b.result()


def _check(path: Path, password: str | None, max_size: int, min_text_chars: int, b: _Builder) -> None:
    try:
        if not path.is_file():
            raise FileNotFoundError
        size = path.stat().st_size
    except OSError:
        b.sha256 = hashlib.sha256(b"").hexdigest()
        b.add(
            Severity.ERROR, "FILE_NOT_FOUND", "Die Datei wurde nicht gefunden oder kann nicht gelesen werden."
        )
        return

    if size == 0:
        b.sha256 = hashlib.sha256(b"").hexdigest()
        b.add(Severity.ERROR, "EMPTY_FILE", "Die Datei ist leer.")
        return

    if size > max_size:
        b.size_bytes = size
        try:
            b.sha256, head = _hash_file(path)
        except OSError:
            b.add(Severity.ERROR, "FILE_NOT_FOUND", "Die Datei kann nicht gelesen werden.")
            return
        b.file_type = detect_file_type_from_bytes(head)
        b.add(
            Severity.ERROR,
            "TOO_LARGE",
            f"Die Datei ist zu groß ({size / 1024 / 1024:.0f} MB). "
            f"Erlaubt sind höchstens {max_size / 1024 / 1024:.0f} MB.",
        )
        return

    try:
        with path.open("rb") as fh:
            data = fh.read(max_size + 1)
    except OSError:
        b.add(Severity.ERROR, "FILE_NOT_FOUND", "Die Datei kann nicht gelesen werden.")
        return
    b.size_bytes = len(data)
    b.sha256 = hashlib.sha256(data).hexdigest()
    if not data:
        b.add(Severity.ERROR, "EMPTY_FILE", "Die Datei ist leer.")
        return
    if len(data) > max_size:
        b.add(
            Severity.ERROR,
            "TOO_LARGE",
            f"Die Datei ist zu groß. Erlaubt sind höchstens {max_size / 1024 / 1024:.0f} MB.",
        )
        return

    b.file_type = detect_file_type_from_bytes(data[:2048])
    if b.file_type == "unknown":
        b.add(
            Severity.ERROR,
            "UNSUPPORTED_TYPE",
            "Dieser Dateityp wird nicht unterstützt. Erlaubt sind PDF, PNG, JPG und TIFF.",
        )
        return

    ext = path.suffix.lower()
    if ext not in EXTENSIONS_BY_TYPE[b.file_type]:
        shown = f"„{ext}“" if ext else "(keine)"
        b.add(
            Severity.WARNING,
            "EXTENSION_MISMATCH",
            f"Die Dateiendung {shown} passt nicht zum Inhalt. "
            f"Die Datei ist ein {FILE_TYPE_LABELS[b.file_type]} und wird als solches gelesen.",
        )

    if b.file_type == "pdf":
        _check_pdf(data, password, min_text_chars, b)
    else:
        _check_image(data, b)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _check_pdf(data: bytes, password: str | None, min_text_chars: int, b: _Builder) -> None:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        log.info("PDF nicht lesbar (%s)", type(exc).__name__)
        b.damaged = True
        b.add(Severity.ERROR, "DAMAGED", "Die PDF-Datei ist beschädigt und kann nicht geöffnet werden.")
        return

    with doc:
        b.needs_password = bool(doc.needs_pass)
        b.encrypted = b.needs_password or bool(doc.is_encrypted)
        if b.needs_password:
            if not password:
                b.add(Severity.ERROR, "PASSWORD_REQUIRED", MSG_PDF_LOCKED)
                return
            if not doc.authenticate(password):
                b.add(Severity.ERROR, "WRONG_PASSWORD", MSG_PDF_UNLOCK_FAILED)
                return
        with contextlib.suppress(RuntimeError, ValueError):
            b.encrypted = b.encrypted or bool((doc.metadata or {}).get("encryption"))

        if doc.is_repaired:
            b.damaged = True
            b.add(
                Severity.WARNING,
                "DAMAGED",
                "Die PDF-Datei war beschädigt und wurde beim Lesen repariert. "
                "Bitte prüfen, ob alle Seiten vollständig sind.",
            )

        b.page_count = doc.page_count
        if b.page_count == 0:
            b.add(Severity.ERROR, "NO_PAGES", "Das Dokument enthält keine Seiten.")
            return

        unreadable: list[int] = []
        embedded: list[int] = []
        for index in range(b.page_count):
            page_number = index + 1
            try:
                info, embedded_ocr = _analyze_pdf_page(doc.load_page(index), page_number, min_text_chars)
            except (RuntimeError, ValueError) as exc:
                log.info("Seite %d nicht lesbar (%s)", page_number, type(exc).__name__)
                unreadable.append(page_number)
                info = PageInfo(page_number=page_number, width=0.0, height=0.0, format_name=OTHER_FORMAT)
                embedded_ocr = False
            b.pages.append(info)
            if embedded_ocr:
                embedded.append(page_number)

        if unreadable:
            b.damaged = True
            b.add(
                Severity.WARNING,
                "DAMAGED",
                f"{pages_phrase(unreadable)} konnte(n) nicht vollständig gelesen werden.",
            )
        _pdf_page_issues(b, min_text_chars, unreadable, embedded)


def _analyze_pdf_page(page: pymupdf.Page, page_number: int, min_text_chars: int) -> tuple[PageInfo, bool]:
    rect = page.rect
    text = page.get_text("text")
    meaningful = count_meaningful_chars(text)
    coverage = image_coverage(page)
    invisible, total = text_visibility(page)
    info = PageInfo(
        page_number=page_number,
        width=float(rect.width),
        height=float(rect.height),
        rotation=int(page.rotation) % 360,
        format_name=page_format_name(rect.width, rect.height),
        has_text_layer=any(not c.isspace() for c in text),
        image_only=is_image_only(coverage, meaningful, min_text_chars),
        text_chars=meaningful,
    )
    return info, is_embedded_ocr(coverage, invisible, total, meaningful)


def _pdf_page_issues(b: _Builder, min_text_chars: int, unreadable: list[int], embedded: list[int]) -> None:
    pages = [p for p in b.pages if p.page_number not in unreadable]

    rotated = [p for p in pages if p.rotation]
    if rotated:
        if len(rotated) == 1:
            text = f"Seite {rotated[0].page_number} ist gedreht ({rotated[0].rotation}°)."
        else:
            text = "Gedrehte Seiten: " + ", ".join(f"{p.page_number} ({p.rotation}°)" for p in rotated) + "."
        b.add(
            Severity.INFO,
            "PAGE_ROTATED",
            text + " Die Drehung wird bei Anzeige und Texterkennung berücksichtigt.",
        )

    no_text = [p.page_number for p in pages if p.text_chars < min_text_chars]
    if no_text:
        suffix = "" if len(no_text) == b.page_count else f" ({pages_phrase(no_text)})"
        b.add(Severity.INFO, "NO_TEXT_LAYER", MSG_NO_TEXT_LAYER + suffix)

    if embedded:
        b.add(
            Severity.INFO,
            "EMBEDDED_OCR_SUSPECTED",
            f"Die Textebene stammt vermutlich aus einer fremden Texterkennung ({pages_phrase(embedded)}). "
            "Werte bitte besonders sorgfältig prüfen.",
        )

    unusual = [p for p in pages if p.format_name == OTHER_FORMAT and p.width > 0 and p.height > 0]
    if unusual:
        details = ", ".join(
            f"Seite {p.page_number}: {points_to_mm(p.width):.0f} × {points_to_mm(p.height):.0f} mm"
            for p in unusual[:5]
        )
        more = " …" if len(unusual) > 5 else ""
        b.add(Severity.INFO, "UNUSUAL_PAGE_FORMAT", f"Ungewöhnliches Seitenformat ({details}{more}).")


# ---------------------------------------------------------------------------
# Bilder
# ---------------------------------------------------------------------------


def _check_image(data: bytes, b: _Builder) -> None:
    low_res: list[tuple[int, float]] = []
    rotated: list[tuple[int, int]] = []
    try:
        image = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        b.add(
            Severity.ERROR,
            "TOO_LARGE",
            "Das Bild hat zu viele Bildpunkte und kann nicht sicher verarbeitet werden.",
        )
        return
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        log.info("Bild nicht lesbar (%s)", type(exc).__name__)
        b.damaged = True
        b.add(Severity.ERROR, "DAMAGED", "Die Bilddatei ist beschädigt und kann nicht geöffnet werden.")
        return

    with image:
        frames = ImageSequence.Iterator(image) if b.file_type == "tiff" else [image]
        try:
            for index, frame in enumerate(frames):
                page_number = index + 1
                frame.load()  # vollständig dekodieren: erkennt abgeschnittene Dateien
                orientation = exif_orientation(frame)
                dpi_x, dpi_y = metadata_dpi(frame)
                w_px, h_px = frame.size
                if orientation_swaps_axes(orientation):
                    w_px, h_px, dpi_x, dpi_y = h_px, w_px, dpi_y, dpi_x
                width, height = w_px * 72.0 / dpi_x, h_px * 72.0 / dpi_y
                rotation = orientation_rotation(orientation)
                b.pages.append(
                    PageInfo(
                        page_number=page_number,
                        width=width,
                        height=height,
                        rotation=rotation,
                        format_name=page_format_name(width, height),
                        has_text_layer=False,
                        image_only=True,
                        text_chars=0,
                    )
                )
                if rotation:
                    rotated.append((page_number, rotation))
                eff = effective_a4_dpi(w_px, h_px)
                if eff < LOW_RESOLUTION_DPI:
                    low_res.append((page_number, eff))
        except Image.DecompressionBombError:
            b.pages.clear()
            b.add(
                Severity.ERROR,
                "TOO_LARGE",
                "Das Bild hat zu viele Bildpunkte und kann nicht sicher verarbeitet werden.",
            )
            return
        except (OSError, ValueError, SyntaxError, EOFError) as exc:
            log.info("Bild unvollständig (%s)", type(exc).__name__)
            b.damaged = True
            b.pages.clear()
            b.add(
                Severity.ERROR,
                "DAMAGED",
                "Die Bilddatei ist beschädigt oder unvollständig und kann nicht gelesen werden.",
            )
            return

    b.page_count = len(b.pages)
    if b.page_count == 0:
        b.add(Severity.ERROR, "NO_PAGES", "Das Dokument enthält keine Seiten.")
        return

    if rotated:
        if len(rotated) == 1 and b.page_count == 1:
            text = f"Das Bild ist laut Kamera-Angaben gedreht ({rotated[0][1]}°)."
        else:
            text = "Gedrehte Bilder: " + ", ".join(f"Seite {p} ({r}°)" for p, r in rotated) + "."
        b.add(Severity.INFO, "PAGE_ROTATED", text + " Die Drehung wird automatisch ausgeglichen.")

    b.add(Severity.INFO, "NO_TEXT_LAYER", MSG_NO_TEXT_LAYER)

    if low_res:
        worst = min(eff for _, eff in low_res)
        where = "" if len(low_res) == b.page_count else f"{pages_phrase([p for p, _ in low_res])}, "
        b.add(
            Severity.WARNING,
            "LOW_RESOLUTION",
            f"Niedrige Bildauflösung ({where}ca. {worst:.0f} dpi bezogen auf DIN A4). "
            "Die Texterkennung kann ungenau sein. Besser ist ein Scan mit mindestens 200 dpi.",
        )
