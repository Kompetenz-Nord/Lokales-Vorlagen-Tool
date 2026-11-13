"""PDF-Ausgabe von Geschäftsbriefen nach DIN 5008 (Form B, optional Form A).

Maße (von oben links, in mm) – DIN 5008, Form B:
* Briefkopf 0–45; Anschriftfeld links 20, oben 45, 85 × 45
  (Zusatz- und Vermerkzone 17,7 mm mit Rücksendeangabe, Anschriftzone 27,3 mm, max. 6 Zeilen)
* Informationsblock links 125, oben 50, Breite 75
* Fluchtlinie (Textbeginn) 25 mm, rechter Rand 20 mm
* Betreff zwei Leerzeilen unter Anschriftfeld bzw. Informationsblock
* Falzmarken bei 105 und 210 mm, Lochmarke bei 148,5 mm
Form A: Briefkopf 27, Anschriftfeld oben 27, Informationsblock oben 32, Falzmarken 87/192.

Die Schrift (Liberation Sans, metrisch kompatibel zu Arial) wird eingebettet.
Unterschriftsbereich bleibt frei; es wird keine Unterschrift erzeugt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer

from .. import __version__
from .compose import MISSING_MARK, LetterPlan

FONT = "LVT-Sans"
FONT_BOLD = "LVT-Sans-Bold"
PAGE_W, PAGE_H = A4
LINE = 4.23 * mm  # eine Zeile nach DIN 5008 (1/6 Zoll)


@dataclass(frozen=True)
class DinLayout:
    form: Literal["A", "B"]
    head_h: float  # Briefkopfhöhe
    addr_top: float
    info_top: float
    folds: tuple[float, float]
    addr_left: float = 20 * mm
    addr_w: float = 85 * mm
    addr_h: float = 45 * mm
    vermerk_h: float = 17.7 * mm
    info_left: float = 125 * mm
    info_w: float = 75 * mm
    margin_left: float = 25 * mm
    margin_right: float = 20 * mm
    hole: float = 148.5 * mm

    @property
    def anschrift_top(self) -> float:
        return self.addr_top + self.vermerk_h

    @property
    def addr_bottom(self) -> float:
        return self.addr_top + self.addr_h

    @property
    def text_width(self) -> float:
        return PAGE_W - self.margin_left - self.margin_right


FORM_B = DinLayout(form="B", head_h=45 * mm, addr_top=45 * mm, info_top=50 * mm, folds=(105 * mm, 210 * mm))
FORM_A = DinLayout(form="A", head_h=27 * mm, addr_top=27 * mm, info_top=32 * mm, folds=(87 * mm, 192 * mm))


@dataclass
class RenderResult:
    path: Path
    page_count: int
    form: str
    warnings: list[str] = field(default_factory=list)
    recipient_box_mm: tuple[float, float, float, float] = (0, 0, 0, 0)  # x0, y0, x1, y1 von oben links


_fonts_registered: Path | None = None


def register_fonts(fonts_dir: Path) -> None:
    global _fonts_registered
    if _fonts_registered == fonts_dir:
        return
    pdfmetrics.registerFont(TTFont(FONT, str(fonts_dir / "LiberationSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(fonts_dir / "LiberationSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD)
    _fonts_registered = fonts_dir


def _top(y_from_top: float) -> float:
    """Abstand von oben -> ReportLab-Koordinate (Ursprung unten links)."""
    return PAGE_H - y_from_top


_MISSING_RE = re.compile(r"\[Fehlt: [^\]]+\]")


def _markup(text: str) -> str:
    """Text für Paragraph escapen; fehlende Werte rot hervorheben (nur Vorschau)."""
    out, pos = [], 0
    for m in _MISSING_RE.finditer(text):
        out.append(escape(text[pos : m.start()]))
        out.append(f'<font color="#C00000">{escape(m.group(0))}</font>')
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out)


class _NumberedCanvas(rl_canvas.Canvas):
    """Seitenzahlen „Seite x von y“ nur bei mehrseitigen Briefen."""

    def __init__(self, *args, **kwargs):
        # Startschrift = eingebettete Schrift (sonst registriert ReportLab eine nicht eingebettete Helvetica)
        kwargs.setdefault("initialFontName", FONT)
        kwargs.setdefault("initialFontSize", 11)
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []

    def showPage(self):  # noqa: N802 (ReportLab-API)
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            if total > 1:
                self.setFont(FONT, 8)
                self.setFillColor(colors.HexColor("#555555"))
                self.drawRightString(PAGE_W - 20 * mm, 12 * mm, f"Seite {self._pageNumber} von {total}")
            super().showPage()
        super().save()


def render_letter(
    plan: LetterPlan,
    out_path: Path,
    fonts_dir: Path,
    *,
    preview: bool = True,
    form: Literal["A", "B"] = "B",
    title: str | None = None,
) -> RenderResult:
    """Brief als PDF schreiben. ``preview=True`` setzt das Wasserzeichen „VORSCHAU“."""
    if not preview and plan.blocking:
        raise ValueError("Finale Ausgabe nicht möglich: Es gibt noch offene Pflichtpunkte.")
    if not preview and MISSING_MARK.split("{")[0] in plan.printed_text():
        raise ValueError("Finale Ausgabe nicht möglich: fehlende Werte im Text.")
    register_fonts(fonts_dir)
    lay = FORM_B if form == "B" else FORM_A
    result = RenderResult(path=out_path, page_count=0, form=lay.form)

    # --- Informationsblock vorab berechnen (Höhe bestimmt Betreff-Position) ---
    label_w = 30 * mm
    value_w = lay.info_w - label_w - 5 * mm
    info_rows: list[tuple[str, list[str]]] = []
    for item in plan.info:
        lines = simpleSplit(item.value, FONT, 9, value_w) or [""]
        info_rows.append((item.label, lines))
    info_lines = sum(len(v) for _, v in info_rows)
    info_bottom = lay.info_top + info_lines * LINE
    body_top = max(lay.addr_bottom, info_bottom) + 2 * LINE

    # --- Anschrift prüfen ---------------------------------------------------
    addr_size = 10.0
    max_w = lay.addr_w - 5 * mm - 2 * mm
    while addr_size > 8 and any(
        pdfmetrics.stringWidth(x, FONT, addr_size) > max_w for x in plan.recipient_lines
    ):
        addr_size -= 0.5
    if any(pdfmetrics.stringWidth(x, FONT, addr_size) > max_w for x in plan.recipient_lines):
        result.warnings.append("Eine Empfängerzeile ist zu lang für das Anschriftfeld – bitte kürzen.")
    if len(plan.recipient_lines) > 6:
        result.warnings.append("Mehr als 6 Empfängerzeilen – passt nicht in die Anschriftzone.")
    ret_size = 7.0
    while ret_size > 5.5 and pdfmetrics.stringWidth(plan.return_line, FONT, ret_size) > max_w:
        ret_size -= 0.5

    def first_page(c: rl_canvas.Canvas, doc) -> None:  # noqa: ANN001
        c.saveState()
        # Briefkopf: Absender
        y = lay.head_h * 0.38 if lay.form == "B" else 10 * mm
        if plan.sender_lines:
            c.setFont(FONT_BOLD, 12)
            c.setFillColor(colors.black)
            c.drawString(lay.margin_left, _top(y), plan.sender_lines[0])
            c.setFont(FONT, 9.5)
            c.setFillColor(colors.HexColor("#333333"))
            rest = " · ".join(plan.sender_lines[1:])
            if rest:
                c.drawString(lay.margin_left, _top(y + 5 * mm), rest)
        # Rücksendeangabe (unterste Zeile der Vermerkzone)
        c.setFont(FONT, ret_size)
        c.setFillColor(colors.HexColor("#444444"))
        c.drawString(lay.addr_left + 5 * mm, _top(lay.anschrift_top - 1.6 * mm), plan.return_line)
        # Anschriftzone
        c.setFillColor(colors.black)
        c.setFont(FONT, addr_size)
        yy = lay.anschrift_top + LINE
        for line in plan.recipient_lines[:6]:
            c.drawString(lay.addr_left + 5 * mm, _top(yy), line)
            yy += LINE  # konstante Zeilenhöhe nach DIN 5008
        # Informationsblock
        yy = lay.info_top + LINE * 0.75
        for label, lines in info_rows:
            c.setFont(FONT, 8)
            c.setFillColor(colors.HexColor("#555555"))
            c.drawString(lay.info_left, _top(yy), label)
            c.setFont(FONT, 9)
            c.setFillColor(colors.black)
            for ln in lines:
                c.drawString(lay.info_left + label_w, _top(yy), ln)
                yy += LINE
        _marks(c)
        c.restoreState()

    def later_pages(c: rl_canvas.Canvas, doc) -> None:  # noqa: ANN001
        c.saveState()
        _marks(c)
        c.restoreState()

    def _marks(c: rl_canvas.Canvas) -> None:
        c.setStrokeColor(colors.HexColor("#999999"))
        c.setLineWidth(0.3)
        for y in (*lay.folds, lay.hole):
            c.line(3 * mm, _top(y), 8 * mm if y != lay.hole else 10 * mm, _top(y))
        if preview:
            c.saveState()
            c.setFont(FONT_BOLD, 46)
            c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.12))
            c.translate(PAGE_W / 2, PAGE_H / 2)
            c.rotate(35)
            c.drawCentredString(0, 0, "VORSCHAU – NICHT VERSENDEN")
            c.restoreState()

    # --- Fließtext ------------------------------------------------------------
    body = ParagraphStyle("body", fontName=FONT, fontSize=11, leading=14, alignment=TA_LEFT)
    bold = ParagraphStyle("bold", parent=body, fontName=FONT_BOLD)
    listed = ParagraphStyle("list", parent=body, leftIndent=8 * mm)
    blank = Spacer(1, 14)

    story: list = [Paragraph(_markup(plan.subject), bold)]
    story += [Paragraph(_markup(s), body) for s in plan.subject_lines]
    story += [blank, blank, Paragraph(_markup(plan.salutation), body), blank]
    blocks = plan.blocks
    for i, blk in enumerate(blocks):
        style = listed if blk.style == "list" else body
        story.append(Paragraph(_markup(blk.text), style))
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if blk.style == "paragraph" or (blk.style == "list" and (nxt is None or nxt.style != "list")):
            story.append(blank)
    closing = [
        Paragraph(_markup(plan.closing), body),
        Spacer(1, 14 * 3 + 4),
        Paragraph(_markup(plan.signer), body),
    ]
    story.append(KeepTogether(closing))
    if plan.attachments:
        story += [blank, Paragraph("Anlage" if len(plan.attachments) == 1 else "Anlagen", bold)]
        story += [Paragraph(_markup(a), body) for a in plan.attachments]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=lay.margin_left,
        rightMargin=lay.margin_right,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
        title=title or plan.title or "Schreiben",
        author="",
        subject="",
        creator=f"Lokales Vorlagen-Tool {__version__}",
        invariant=1,
    )
    first_frame = Frame(
        lay.margin_left,
        20 * mm,
        lay.text_width,
        PAGE_H - body_top - 20 * mm,
        id="first",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
    )
    later_frame = Frame(
        lay.margin_left,
        20 * mm,
        lay.text_width,
        PAGE_H - 25 * mm - 20 * mm,
        id="later",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="First", frames=[first_frame], onPage=first_page, autoNextPageTemplate="Later"),
            PageTemplate(id="Later", frames=[later_frame], onPage=later_pages),
        ]
    )
    doc.build(story, canvasmaker=_NumberedCanvas)
    result.page_count = doc.page
    result.recipient_box_mm = (
        (lay.addr_left + 5 * mm) / mm,
        lay.anschrift_top / mm,
        (lay.addr_left + lay.addr_w) / mm,
        lay.addr_bottom / mm,
    )
    return result
