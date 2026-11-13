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


def markdown_to_page_text(
    text: str,
    page_number: int,
    page_width: float,
    page_height: float,
    *,
    engine: str,
    dpi: float | None = None,
) -> PageText:
    """GLM-OCR-Ausgabe (Text/Markdown) in ein ``PageText`` mit synthetischen Wortpositionen umwandeln."""
    rows: list[list[str]] = []
    for raw in text.replace("\r", "").split("\n"):
        line = _HTML_TAG.sub(" ", raw).rstrip()
        if not line.strip() or _TABLE_SEP.match(line):
            continue
        if "|" in line and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
            continue
        rows.append([_MD_PREFIX.sub("", line).replace("**", "").replace("__", "").strip()])
    words: list[Word] = []
    lines_out: list[str] = []
    n = max(len(rows), 1)
    margin = page_height * 0.05
    step = min((page_height - 2 * margin) / n, 22.0)
    height = max(min(step * 0.7, 12.0), 4.0)
    for li, cells in enumerate(rows):
        y0 = margin + li * step
        col_w = (page_width * 0.9) / max(len(cells), 1)
        for ci, cell in enumerate(cells):
            x = page_width * 0.05 + ci * col_w
            char_w = min(col_w / max(len(cell), 1), 6.0)
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
        lines_out.append("    ".join(cells))
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
