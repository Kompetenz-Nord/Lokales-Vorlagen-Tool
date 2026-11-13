"""Texterkennung mit einem lokalen OCR-Modell über Ollama (Standard: ``glm-ocr``).

GLM-OCR liefert Text bzw. Markdown (Tabellen als ``| a | b |``), aber keine Wortpositionen.
Damit die regelbasierte Label-Wert-Extraktion der App weiter funktioniert, wird die Antwort
in ein **synthetisches Seitenlayout** übersetzt:

* jede Textzeile erhält eine eigene y-Position (gleichmäßig über die Seite verteilt),
* Tabellenzellen werden spaltenweise nebeneinander gesetzt (Label links, Wert rechts).

Die Koordinaten sind daher **nicht** die echten Positionen auf der Seite. Belege zeigen
deshalb Seite + Textzitat; eine Markierung im Seitenbild entfällt (``bbox`` wird nicht gesetzt).
Methode der Werte: ``LOCAL_VLM`` mit Modellversion. Keine Konfidenz je Wort (``None``).
"""

from __future__ import annotations

import logging
import re
import time

from PIL import Image

from ..core.models import BoundingBox, ExtractionMethod, PageQuality, PageText, TextLayerOrigin, Word
from ..core.settings import Settings
from ..core.textutil import fold
from .ollama_client import OllamaClient, OllamaError
from .status import MODEL_CATALOG
from .vlm_extractor import encode_image

log = logging.getLogger(__name__)

OCR_PROMPT = "Text Recognition:"
SYNTHETIC_NOTE = (
    "Text aus lokaler KI-Texterkennung (GLM-OCR) – Positionen sind angenähert, bitte Werte prüfen."
)
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}")
_MD_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|[-*]\s+|>\s+)")
_HTML_TAG = re.compile(r"</?(?:table|thead|tbody|tr|td|th|br|p|div|span)[^>]*>", re.IGNORECASE)


_LABEL_PHRASES: list[list[str]] | None = None
_HEADINGS: set[tuple[str, ...]] | None = None


def _tokens(text: str) -> list[str]:
    return [t for t in (fold(w).strip(":.;,") for w in text.split()) if t]


def _label_phrases() -> tuple[list[list[str]], set[tuple[str, ...]]]:
    """Bekannte Label-Phrasen der Regel-Extraktion (längste zuerst) und Abschnittsüberschriften."""
    global _LABEL_PHRASES, _HEADINGS
    if _LABEL_PHRASES is None or _HEADINGS is None:
        from ..extraction.rules import LABELS, SECTION_LABELS, STOP_LABELS

        phrases: set[tuple[str, ...]] = set()
        for lst in LABELS.values():
            phrases.update(tuple(_tokens(x)) for x in lst)
        headings: set[tuple[str, ...]] = set()
        for heading, mapping in SECTION_LABELS.items():
            headings.add(tuple(_tokens(heading)))
            for lst in mapping.values():
                phrases.update(tuple(_tokens(x)) for x in lst)
        phrases.update(tuple(_tokens(x)) for x in STOP_LABELS)
        phrases.update(tuple(_tokens(x)) for x in ("Name / Firma", "Name", "Anschrift", "Adresse"))
        _LABEL_PHRASES = sorted((list(p) for p in phrases if p), key=len, reverse=True)
        _HEADINGS = headings
    return _LABEL_PHRASES, _HEADINGS


def _split_label(line: str) -> tuple[str, str] | None:
    """„Versicherungsnummer 8815201-7“ -> („Versicherungsnummer“, „8815201-7“), sonst ``None``."""
    words = line.split()
    toks = [fold(w).strip(":.;,") for w in words]
    phrases, _ = _label_phrases()
    for ph in phrases:
        n = len(ph)
        if len(toks) > n and toks[:n] == ph:
            return " ".join(words[:n]), " ".join(words[n:])
    return None


def markdown_to_page_text(
    text: str,
    page_number: int,
    page_width: float,
    page_height: float,
    *,
    engine: str,
    dpi: float | None = None,
) -> PageText:
    """GLM-OCR-Ausgabe (Text/Markdown) in ein ``PageText`` mit synthetischen Wortpositionen umwandeln.

    GLM-OCR fasst zweispaltige Label/Wert-Layouts zu einer Zeile zusammen („Beitrag 150,00 EUR“).
    Beginnt eine Zeile mit einem bekannten Label der Regel-Extraktion, wird der Wert in eine eigene
    Wertspalte gesetzt (wie im Original). Folgezeilen eines Werts (z. B. „12345 Musterstadt“) landen
    ebenfalls in der Wertspalte, Tabellenzellen spaltenweise nebeneinander.
    """
    phrases, headings = _label_phrases()
    phrase_set = {tuple(p) for p in phrases}
    rows: list[list[str]] = []
    prev_split = False
    for raw in text.replace("\r", "").split("\n"):
        line = _HTML_TAG.sub(" ", raw).rstrip()
        if not line.strip() or _TABLE_SEP.match(line):
            prev_split = False
            continue
        if "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
            prev_split = len(cells) >= 2
            continue
        clean = _MD_PREFIX.sub("", line).replace("**", "").replace("__", "").strip()
        toks = tuple(_tokens(clean))
        if toks in headings or toks in phrase_set:
            # Abschnittsüberschrift oder Label ohne Wert (z. B. „Versicherte Person“) – nicht aufteilen
            rows.append([clean])
            prev_split = False
            continue
        split = _split_label(clean)
        if split is not None:
            rows.append([split[0], split[1]])
            prev_split = True
        elif prev_split and len(clean) <= 60:
            rows.append(["", clean])  # Folgezeile in der Wertspalte
        else:
            rows.append([clean])
            prev_split = False
    words: list[Word] = []
    lines_out: list[str] = []
    n = max(len(rows), 1)
    margin = page_height * 0.05
    step = min((page_height - 2 * margin) / n, 22.0)
    height = max(min(step * 0.7, 12.0), 4.0)
    value_x = page_width * 0.45
    for li, cells in enumerate(rows):
        y0 = margin + li * step
        if len(cells) == 2:
            starts = [page_width * 0.05, value_x]
            widths = [value_x - page_width * 0.07, page_width * 0.5]
        else:
            col_w = (page_width * 0.9) / max(len(cells), 1)
            starts = [page_width * 0.05 + ci * col_w for ci in range(len(cells))]
            widths = [col_w] * len(cells)
        for ci, cell in enumerate(cells):
            if not cell:
                continue
            x = starts[ci]
            char_w = min(widths[ci] / max(len(cell), 1), 5.0)
            for token in cell.split():
                w = max(len(token) * char_w, 2.0)
                words.append(
                    Word(
                        text=token,
                        bbox=BoundingBox(x0=x, y0=y0, x1=min(x + w, page_width), y1=y0 + height),
                        confidence=None,
                        block_no=0,
                        line_no=li,
                    )
                )
                x += w + char_w
        lines_out.append("    ".join(c for c in cells if c))
    return PageText(
        page_number=page_number,
        width=page_width,
        height=page_height,
        method=ExtractionMethod.LOCAL_VLM,
        origin=TextLayerOrigin.OCR,
        words=words,
        text="\n".join(lines_out),
        mean_confidence=None,
        quality=PageQuality.GOOD if words else PageQuality.EMPTY,
        warnings=[SYNTHETIC_NOTE]
        if words
        else ["Die lokale KI-Texterkennung hat auf dieser Seite keinen Text geliefert."],
        engine_version=engine,
        source_dpi=dpi,
    )


class OllamaOcrBackend:
    """OCR-Backend (Protokoll ``ocr.base.OcrBackend``) über das lokale Ollama, nur Loopback."""

    method = ExtractionMethod.LOCAL_VLM

    def __init__(
        self, settings: Settings, model: str | None = None, client: OllamaClient | None = None
    ) -> None:
        self.settings = settings
        self.model = model or settings.ai_model or "glm-ocr"
        self.name = self.model
        self.client = client or OllamaClient(settings.ollama_url, timeout=10.0)
        self._digest: str | None = None

    def is_available(self) -> tuple[bool, str]:
        try:
            if not self.client.is_running():
                return False, "Der lokale Ollama-Dienst läuft nicht."
            info = self.client.find_model(self.model)
        except OllamaError as exc:
            return False, str(exc)
        if info is None:
            return False, f"Das Modell „{self.model}“ ist noch nicht heruntergeladen."
        self._digest = info.digest
        return True, f"{self.model} bereit."

    def version(self) -> str | None:
        return f"{self.model} ({(self._digest or '')[:12]})" if self._digest else self.model

    def recognize(
        self, image: Image.Image, page_number: int, page_width: float, page_height: float, dpi: float
    ) -> PageText:
        entry = MODEL_CATALOG.get(self.model)
        started = time.monotonic()
        text = self.client.chat_text(
            self.model,
            OCR_PROMPT,
            [encode_image(image)],
            max_tokens=max(self.settings.ai_max_tokens, 4096),
            timeout=float(self.settings.ai_timeout_s),
            num_ctx=entry.num_ctx if entry else 16384,
        )
        log.info("KI-Texterkennung Seite %d: %.1f s", page_number, time.monotonic() - started)
        return markdown_to_page_text(
            text, page_number, page_width, page_height, engine=self.version() or self.model, dpi=dpi
        )
