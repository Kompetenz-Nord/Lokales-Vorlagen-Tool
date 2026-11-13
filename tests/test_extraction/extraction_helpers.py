"""Test-Hilfen: DocumentText/PageText ohne das OCR-Modul aufbauen.

Bewusst eigenständig (kein Import von ``insurance_app.ocr``/``documents``):
PDF-Textebene über ``page.get_text("words")``, Bilder optional über pytesseract.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from insurance_app.core.models import (
    BoundingBox,
    DocumentText,
    ExtractionMethod,
    PageText,
    TextLayerOrigin,
    Word,
)
from insurance_app.extraction.extractor import SourceDocInfo
from insurance_app.samples.generator import SampleSet, sample_metadata

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"


def page_text_from_pdf_page(
    page: pymupdf.Page, origin: TextLayerOrigin = TextLayerOrigin.NATIVE, engine: str | None = None
) -> PageText:
    words = [
        Word(
            text=w[4],
            bbox=BoundingBox(x0=w[0], y0=w[1], x1=w[2], y1=w[3]),
            block_no=int(w[5]),
            line_no=int(w[6]),
        )
        for w in page.get_text("words")
        if str(w[4]).strip()
    ]
    return PageText(
        page_number=page.number + 1,
        width=page.rect.width,
        height=page.rect.height,
        method=ExtractionMethod.PDF_TEXT,
        origin=origin,
        words=words,
        text=page.get_text(),
        engine_version=engine,
    )


def document_text_from_pdf(
    path: Path, document_id: str = "D-TEST", origin: TextLayerOrigin = TextLayerOrigin.NATIVE
) -> DocumentText:
    with pymupdf.open(str(path)) as doc:
        engine = "fremde OCR-Textebene" if origin == TextLayerOrigin.EMBEDDED_OCR else None
        pages = [page_text_from_pdf_page(p, origin, engine) for p in doc]
    return DocumentText(document_id=document_id, pages=pages)


def info_for(sample: SampleSet | None, file_name: str, document_id: str) -> SourceDocInfo:
    meta = (sample.metadata if sample else sample_metadata())[file_name]
    return SourceDocInfo(
        document_id=document_id,
        file_name=file_name,
        doc_type=meta.doc_type,
        document_date=meta.document_date,
        effective_date=meta.effective_date,
    )


def make_word(text: str, x0: float, y0: float, *, h: float = 11.0, conf: float | None = None) -> Word:
    """Wort mit grob geschätzter Breite (5,5 pt je Zeichen)."""
    return Word(
        text=text, bbox=BoundingBox(x0=x0, y0=y0, x1=x0 + 5.5 * len(text), y1=y0 + h), confidence=conf
    )


def words_for_line(
    text: str, x0: float, y0: float, *, h: float = 11.0, conf: float | None = None
) -> list[Word]:
    """Eine Textzeile in Wörter mit normalem Wortabstand zerlegen."""
    out: list[Word] = []
    x = x0
    for token in text.split(" "):
        if token:
            w = make_word(token, x, y0, h=h, conf=conf)
            out.append(w)
            x = w.bbox.x1 + 3.0
    return out


def page_from_words(
    words: list[Word],
    *,
    width: float = 595.0,
    height: float = 842.0,
    page_no: int = 1,
    origin=TextLayerOrigin.NATIVE,
    method=ExtractionMethod.PDF_TEXT,
) -> PageText:
    return PageText(
        page_number=page_no,
        width=width,
        height=height,
        method=method,
        origin=origin,
        words=words,
        text=" ".join(w.text for w in words),
    )


def ocr_page_text_from_image(image, dpi: float, page_no: int = 1) -> PageText:
    """Tesseract-Wörter (deu) als PageText – nur für langsame Robustheitstests."""
    import pytesseract

    data = pytesseract.image_to_data(image, lang="deu", config="--psm 3", output_type=pytesseract.Output.DICT)
    scale = 72.0 / dpi
    words: list[Word] = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(
            Word(
                text=text,
                bbox=BoundingBox(x0=x * scale, y0=y * scale, x1=(x + w) * scale, y1=(y + h) * scale),
                confidence=max(0.0, min(1.0, conf / 100.0)),
            )
        )
    width, height = image.size
    return PageText(
        page_number=page_no,
        width=width * scale,
        height=height * scale,
        method=ExtractionMethod.TESSERACT,
        origin=TextLayerOrigin.OCR,
        words=words,
        text=" ".join(w.text for w in words),
        engine_version=f"tesseract {pytesseract.get_tesseract_version()} deu",
        source_dpi=dpi,
    )
