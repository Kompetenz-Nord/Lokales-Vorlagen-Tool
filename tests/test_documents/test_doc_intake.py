"""Eingangsprüfung (documents.intake) mit synthetischen Dateien."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pymupdf
import pytest
from doc_samples import (
    A4,
    make_embedded_ocr_pdf,
    make_encrypted_pdf,
    make_image_pdf,
    make_text_pdf,
    png_bytes,
    text_image,
)
from PIL import Image

from insurance_app.core.models import Severity
from insurance_app.documents.intake import (
    MAX_FILE_SIZE_BYTES,
    SUPPORTED_EXTENSIONS,
    check_document,
    detect_file_type,
    effective_a4_dpi,
)
from insurance_app.documents.pdf_analysis import format_page_list, page_format_name


def codes(result) -> dict[str, Severity]:
    return {i.code: i.severity for i in result.issues}


def fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_mtime_ns, stat.st_size


# ---------------------------------------------------------------------------
# Konstanten und Typerkennung
# ---------------------------------------------------------------------------


def test_constants():
    assert {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"} == SUPPORTED_EXTENSIONS
    assert MAX_FILE_SIZE_BYTES == 200 * 1024 * 1024


def test_detect_file_type_by_magic_bytes(tmp_path: Path):
    pdf = make_text_pdf(tmp_path / "a.pdf")
    png = tmp_path / "bild.pdf"  # falsche Endung, Inhalt PNG
    png.write_bytes(png_bytes(text_image()))
    jpg = tmp_path / "x.bin"
    text_image().save(jpg, format="JPEG")
    tif = tmp_path / "x.dat"
    text_image().save(tif, format="TIFF")
    txt = tmp_path / "notiz.pdf"
    txt.write_text("MUSTER – nur Text", encoding="utf-8")
    assert detect_file_type(pdf) == "pdf"
    assert detect_file_type(png) == "png"
    assert detect_file_type(jpg) == "jpeg"
    assert detect_file_type(tif) == "tiff"
    assert detect_file_type(txt) == "unknown"
    assert detect_file_type(tmp_path / "fehlt.pdf") == "unknown"


@pytest.mark.parametrize(
    ("w", "h", "name"),
    [
        (595.28, 841.89, "A4 hoch"),
        (841.89, 595.28, "A4 quer"),
        (600.0, 850.0, "A4 hoch"),  # innerhalb 2 %
        (419.53, 595.28, "A5 hoch"),
        (612, 792, "Letter hoch"),
        (612, 1008, "Legal hoch"),
        (500, 500, "sonstiges"),
        (0, 0, "sonstiges"),
    ],
)
def test_page_format_name(w, h, name):
    assert page_format_name(w, h) == name


def test_format_page_list():
    assert format_page_list([5, 1, 2, 3]) == "1–3, 5"
    assert format_page_list([]) == ""


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def test_text_pdf(tmp_path: Path):
    path = make_text_pdf(tmp_path / "police.pdf", pages=2)
    result = check_document(path)
    assert result.ok
    assert result.file_type == "pdf"
    assert result.page_count == 2
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.size_bytes == path.stat().st_size
    assert not result.encrypted and not result.needs_password and not result.damaged
    page = result.pages[0]
    assert page.format_name == "A4 hoch"
    assert page.width == pytest.approx(A4[0], abs=0.5)
    assert page.has_text_layer and not page.image_only
    assert page.text_chars > 100
    assert "NO_TEXT_LAYER" not in codes(result)
    assert result.issues == []


def test_image_only_pdf(tmp_path: Path):
    result = check_document(make_image_pdf(tmp_path / "scan.pdf"))
    assert result.ok
    page = result.pages[0]
    assert page.image_only
    assert not page.has_text_layer
    assert page.text_chars == 0
    msg = next(i for i in result.issues if i.code == "NO_TEXT_LAYER")
    assert msg.severity == Severity.INFO
    assert msg.message == "Keine Textebene – Texterkennung (OCR) erforderlich"


def test_partial_no_text_layer_names_pages(tmp_path: Path):
    path = tmp_path / "gemischt.pdf"
    make_text_pdf(path)
    doc = pymupdf.open(path)
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=png_bytes(text_image()))
    doc.saveIncr()
    doc.close()
    result = check_document(path)
    msg = next(i for i in result.issues if i.code == "NO_TEXT_LAYER")
    assert msg.message.endswith("(Seite 2)")
    assert [p.image_only for p in result.pages] == [False, True]


def test_embedded_ocr_suspected(tmp_path: Path):
    result = check_document(make_embedded_ocr_pdf(tmp_path / "scan_ocr.pdf"))
    assert result.ok
    assert codes(result).get("EMBEDDED_OCR_SUSPECTED") == Severity.INFO
    assert result.pages[0].has_text_layer
    assert not result.pages[0].image_only  # Text vorhanden


def test_encrypted_pdf_requires_password(tmp_path: Path):
    path = make_encrypted_pdf(tmp_path / "geschuetzt.pdf", user_pw="MUSTER-1234")
    before = fingerprint(path)

    locked = check_document(path)
    assert not locked.ok
    assert locked.encrypted and locked.needs_password
    issue = next(i for i in locked.issues if i.code == "PASSWORD_REQUIRED")
    assert issue.severity == Severity.ERROR
    assert issue.message == "Das PDF ist passwortgeschützt. Bitte Passwort eingeben."

    wrong = check_document(path, password="MUSTER-xyz-9")
    assert not wrong.ok
    assert codes(wrong) == {"WRONG_PASSWORD": Severity.ERROR}
    assert "MUSTER-xyz-9" not in wrong.model_dump_json()  # Passwort wird nirgends gespeichert

    unlocked = check_document(path, password="MUSTER-1234")
    assert unlocked.ok
    assert unlocked.needs_password and unlocked.encrypted
    assert unlocked.page_count == 1
    assert unlocked.pages[0].has_text_layer
    assert "MUSTER-1234" not in unlocked.model_dump_json()
    assert fingerprint(path) == before


def test_owner_password_only_is_encrypted_but_open(tmp_path: Path):
    result = check_document(make_encrypted_pdf(tmp_path / "rechte.pdf", user_pw=None))
    assert result.ok
    assert result.encrypted
    assert not result.needs_password


def test_truncated_pdf_is_repaired_with_warning(tmp_path: Path):
    source = make_text_pdf(tmp_path / "ganz.pdf").read_bytes()
    path = tmp_path / "abgeschnitten.pdf"
    path.write_bytes(source[: int(len(source) * 0.7)])
    before = fingerprint(path)
    result = check_document(path)
    assert result.damaged
    assert codes(result)["DAMAGED"] in (Severity.WARNING, Severity.ERROR)
    if codes(result)["DAMAGED"] == Severity.WARNING:
        assert result.ok
    assert fingerprint(path) == before  # Reparatur nur im Speicher


def test_unreadable_pdf_is_damaged_error(tmp_path: Path):
    path = tmp_path / "kaputt.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00MUSTER" * 50)
    result = check_document(path)
    assert not result.ok
    assert result.damaged
    assert codes(result) == {"DAMAGED": Severity.ERROR}


def test_rotated_page(tmp_path: Path):
    result = check_document(make_text_pdf(tmp_path / "quer.pdf", rotation=90))
    assert result.ok
    page = result.pages[0]
    assert page.rotation == 90
    assert page.format_name == "A4 quer"
    assert page.width > page.height
    issue = next(i for i in result.issues if i.code == "PAGE_ROTATED")
    assert issue.severity == Severity.INFO
    assert "90°" in issue.message


def test_unusual_page_format(tmp_path: Path):
    result = check_document(make_text_pdf(tmp_path / "streifen.pdf", size=(595.0, 280.0)))
    assert result.ok
    assert result.pages[0].format_name == "sonstiges"
    assert codes(result)["UNUSUAL_PAGE_FORMAT"] == Severity.INFO


def test_pdf_without_pages_is_error(tmp_path: Path):
    # Minimal-PDF mit leerem Seitenbaum (von Hand, da PyMuPDF keine leeren PDFs speichert)
    body = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    )
    xref_pos = len(body)
    offsets = [9, body.index(b"2 0 obj")]
    xref = b"xref\n0 3\n0000000000 65535 f \n" + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    trailer = b"trailer<</Size 3/Root 1 0 R>>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    path = tmp_path / "leer.pdf"
    path.write_bytes(body + xref + trailer)
    result = check_document(path)
    assert not result.ok
    assert "NO_PAGES" in codes(result)


# ---------------------------------------------------------------------------
# Bilder
# ---------------------------------------------------------------------------


def test_png_with_dpi(tmp_path: Path):
    path = tmp_path / "screenshot.png"
    text_image(size_px=(2480, 3508)).save(path, dpi=(300, 300))
    result = check_document(path)
    assert result.ok
    assert result.file_type == "png"
    assert result.page_count == 1
    page = result.pages[0]
    assert page.width == pytest.approx(2480 * 72 / 300, abs=0.2)
    assert page.height == pytest.approx(3508 * 72 / 300, abs=0.2)
    assert page.format_name == "A4 hoch"
    assert page.image_only and not page.has_text_layer
    assert codes(result) == {"NO_TEXT_LAYER": Severity.INFO}


def test_png_without_dpi_assumes_150(tmp_path: Path):
    path = tmp_path / "ohne_dpi.png"
    path.write_bytes(png_bytes(text_image(size_px=(1500, 1000))))
    page = check_document(path).pages[0]
    assert page.width == pytest.approx(1500 * 72 / 150)
    assert page.height == pytest.approx(1000 * 72 / 150)


def test_png_with_absurd_dpi_falls_back(tmp_path: Path):
    path = tmp_path / "dpi.png"
    text_image(size_px=(1500, 1000)).save(path, dpi=(5, 5))
    assert check_document(path).pages[0].width == pytest.approx(1500 * 72 / 150)


def test_jpeg_with_exif_rotation(tmp_path: Path):
    img = text_image(size_px=(1754, 1240))
    exif = Image.Exif()
    exif[274] = 6  # 90° im Uhrzeigersinn drehen zur Anzeige
    path = tmp_path / "foto.jpg"
    img.save(path, format="JPEG", exif=exif, dpi=(150, 150))
    result = check_document(path)
    assert result.ok
    assert result.file_type == "jpeg"
    page = result.pages[0]
    assert page.rotation == 90
    assert page.width == pytest.approx(1240 * 72 / 150)
    assert page.height == pytest.approx(1754 * 72 / 150)
    assert codes(result)["PAGE_ROTATED"] == Severity.INFO


def test_single_and_multipage_tiff(tmp_path: Path):
    single = tmp_path / "scan.tif"
    text_image().save(single, format="TIFF", dpi=(150, 150))
    r1 = check_document(single)
    assert r1.ok and r1.file_type == "tiff" and r1.page_count == 1

    multi = tmp_path / "scan_mehrseitig.tiff"
    frames = [text_image(color=c) for c in ("white", "#eeeeee", "#dddddd")]
    frames[0].save(multi, format="TIFF", save_all=True, append_images=frames[1:], dpi=(150, 150))
    r3 = check_document(multi)
    assert r3.ok
    assert r3.page_count == 3
    assert [p.page_number for p in r3.pages] == [1, 2, 3]
    assert all(p.format_name == "A4 hoch" for p in r3.pages)


def test_low_resolution_image(tmp_path: Path):
    path = tmp_path / "klein.png"
    text_image(size_px=(800, 1131), font_px=20).save(path, dpi=(96, 96))
    result = check_document(path)
    assert result.ok  # nur Warnung
    issue = next(i for i in result.issues if i.code == "LOW_RESOLUTION")
    assert issue.severity == Severity.WARNING
    assert "dpi" in issue.message


def test_effective_a4_dpi_rule():
    assert effective_a4_dpi(1240, 1754) == pytest.approx(150, abs=1)
    assert effective_a4_dpi(1000, 1414) < 150
    assert effective_a4_dpi(1170, 2532) > 150  # hoher Handy-Screenshot
    assert effective_a4_dpi(1920, 1080) > 150  # Bildschirmfoto quer


def test_truncated_png_is_damaged(tmp_path: Path):
    data = png_bytes(text_image())
    path = tmp_path / "defekt.png"
    path.write_bytes(data[: len(data) // 3])
    result = check_document(path)
    assert not result.ok
    assert result.damaged
    assert codes(result)["DAMAGED"] == Severity.ERROR


# ---------------------------------------------------------------------------
# Allgemeine Fehlerfälle
# ---------------------------------------------------------------------------


def test_extension_mismatch(tmp_path: Path):
    path = tmp_path / "eigentlich_png.pdf"
    path.write_bytes(png_bytes(text_image()))
    result = check_document(path)
    assert result.ok
    assert result.file_type == "png"
    assert codes(result)["EXTENSION_MISMATCH"] == Severity.WARNING


def test_empty_file(tmp_path: Path):
    path = tmp_path / "leer.pdf"
    path.write_bytes(b"")
    result = check_document(path)
    assert not result.ok
    assert codes(result) == {"EMPTY_FILE": Severity.ERROR}
    assert result.size_bytes == 0


def test_txt_is_unsupported(tmp_path: Path):
    path = tmp_path / "notiz.txt"
    path.write_text("MUSTER – KEINE ECHTDATEN", encoding="utf-8")
    result = check_document(path)
    assert not result.ok
    assert result.file_type == "unknown"
    assert codes(result) == {"UNSUPPORTED_TYPE": Severity.ERROR}


def test_missing_file(tmp_path: Path):
    result = check_document(tmp_path / "gibt_es_nicht.pdf")
    assert not result.ok
    assert codes(result) == {"FILE_NOT_FOUND": Severity.ERROR}


def test_too_large(tmp_path: Path):
    path = make_text_pdf(tmp_path / "gross.pdf")
    result = check_document(path, max_size=1000)
    assert not result.ok
    assert result.file_type == "pdf"
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert codes(result) == {"TOO_LARGE": Severity.ERROR}


@pytest.mark.parametrize("kind", ["text", "image", "encrypted", "damaged", "png", "jpeg", "tiff"])
def test_file_unchanged_after_check(tmp_path: Path, kind: str):
    if kind == "text":
        path = make_text_pdf(tmp_path / "a.pdf", rotation=90)
    elif kind == "image":
        path = make_image_pdf(tmp_path / "a.pdf")
    elif kind == "encrypted":
        path = make_encrypted_pdf(tmp_path / "a.pdf", user_pw="MUSTER-pw")
    elif kind == "damaged":
        data = make_text_pdf(tmp_path / "ok.pdf").read_bytes()
        path = tmp_path / "a.pdf"
        path.write_bytes(data[: len(data) // 2])
    else:
        fmt = {"png": "PNG", "jpeg": "JPEG", "tiff": "TIFF"}[kind]
        path = tmp_path / f"a.{kind}"
        buffer = io.BytesIO()
        text_image().save(buffer, format=fmt)
        path.write_bytes(buffer.getvalue())
    before = fingerprint(path)
    check_document(path, password="MUSTER-pw" if kind == "encrypted" else None)
    check_document(path)
    assert fingerprint(path) == before


# ---------------------------------------------------------------------------
# PRIV-05: Pixelbomben unterhalb der Pillow-Grenze werden abgelehnt
# ---------------------------------------------------------------------------


def _blank_gray_png(path: Path, width: int, height: int) -> Path:
    """Gültiges, sehr großes Graustufen-PNG zeilenweise schreiben (wenig Speicher, ca. 200 KB)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    comp = zlib.compressobj(9)
    row = b"\x00" + b"\xff" * width
    idat = bytearray()
    for _ in range(height):
        idat += comp.compress(row)
    idat += comp.flush()
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", bytes(idat)) + chunk(b"IEND", b"")
    )
    return path


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_priv_05_pixel_bomb_below_pillow_limit_is_rejected(tmp_path: Path):
    import time

    from insurance_app.documents.pages import MAX_IMAGE_PIXELS

    path = _blank_gray_png(tmp_path / "riesig.png", 13000, 13000)  # 169 Mio. Bildpunkte
    assert path.stat().st_size < 2 * 1024 * 1024
    assert MAX_IMAGE_PIXELS < 13000 * 13000
    started = time.monotonic()
    result = check_document(path)
    assert not result.ok
    assert codes(result).get("TOO_LARGE") == Severity.ERROR
    assert result.pages == [] and result.page_count == 0
    assert time.monotonic() - started < 5  # ohne vollständiges Dekodieren


def test_priv_05_cap_allows_a4_1200_dpi_and_uses_limit(tmp_path: Path, monkeypatch):
    import insurance_app.documents.intake as intake

    assert intake.MAX_IMAGE_PIXELS >= 9924 * 14031  # DIN A4 mit 1200 dpi bleibt zulässig
    path = tmp_path / "scan.png"
    path.write_bytes(png_bytes(text_image(["MUSTER"], size_px=(400, 300)), dpi=(150, 150)))
    monkeypatch.setattr(intake, "MAX_IMAGE_PIXELS", 400 * 300 - 1)
    assert codes(check_document(path)).get("TOO_LARGE") == Severity.ERROR
    monkeypatch.setattr(intake, "MAX_IMAGE_PIXELS", 400 * 300)
    assert check_document(path).ok
