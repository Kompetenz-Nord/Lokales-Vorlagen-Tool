"""Qualitätskontrolle für generierte DIN-5008-Briefe.

Liefert denselben ``QaReport`` wie die Formular-QA, damit Oberfläche und Export
einheitlich arbeiten. Geprüft wird die tatsächlich erzeugte Datei (nicht nur der Plan).
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import pymupdf
import pypdf

from ..core.models import Severity
from ..core.textutil import normalize_ws
from ..pdf.qa import QaCheck, QaReport
from ..pdf.render import available_renderers, render_page
from .compose import MISSING_MARK, LetterPlan
from .din5008 import din_layout

PT_PER_MM = 72 / 25.4
A4_PT = (595.28, 841.89)
MISSING_PREFIX = MISSING_MARK.split("{")[0].strip()  # "[Fehlt:"
# Platzhalter-Format der Briefvorlagen (bewusst etwas weiter als ``schema.PLACEHOLDER``,
# damit auch fehlerhaft geschriebene Platzhalter wie ``{Order.Feld|x}`` auffallen)
UNRESOLVED_PLACEHOLDER = re.compile(r"\{(?:@[A-Za-z_]+|[A-Za-z_]+\.[A-Za-z0-9_]+\??(?:\|[A-Za-z_]+)?)\}")


def has_unresolved_marks(text: str) -> bool:
    """Enthält der Text nicht aufgelöste Vorlagen-Platzhalter oder „[Fehlt: …]“-Markierungen?

    Geprüft wird nur das Platzhalter-Format der Briefvorlagen (``{feld.schluessel…}``,
    ``{@baustein}``) – eine geschweifte Klammer in einem Nutzerwert (z. B. „Abt. {K}“)
    ist kein Fehler.
    """
    return MISSING_PREFIX in text or UNRESOLVED_PLACEHOLDER.search(text) is not None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_letter_qa(
    plan: LetterPlan,
    pdf_path: Path,
    *,
    final: bool,
    form: str = "B",
    render_dir: Path | None = None,
    renderers: tuple[str, ...] = ("mupdf", "pdfium"),
    dpi: int = 100,
) -> QaReport:
    started = time.monotonic()
    lay = din_layout(form)  # unbekannte Briefform -> ValueError (wie beim Erzeugen)
    checks: list[QaCheck] = []

    def add(
        code: str,
        ok: bool,
        msg: str,
        sev: Severity = Severity.ERROR,
        page: int | None = None,
        renderer: str | None = None,
    ):
        checks.append(QaCheck(code=code, passed=ok, severity=sev, message=msg, page=page, renderer=renderer))

    rendered: dict[str, list[str]] = {}
    try:
        doc = pymupdf.open(pdf_path)
        pypdf.PdfReader(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 – jede Lesestörung ist ein QA-Fehler
        add("FILE_OPENS", False, f"Die Datei lässt sich nicht öffnen ({type(exc).__name__}).")
        return QaReport(
            passed=False,
            checks=checks,
            rendered_pages={},
            template_id=plan.template_id,
            template_version=plan.template_version,
            plan_mode=plan.mode,
            filled_sha256=None,
            flattened=False,
            duration_s=time.monotonic() - started,
        )
    add("FILE_OPENS", True, "Datei lässt sich mit MuPDF und pypdf öffnen.")

    sizes_ok = all(abs(p.rect.width - A4_PT[0]) < 1 and abs(p.rect.height - A4_PT[1]) < 1 for p in doc)
    add(
        "PAGE_FORMAT",
        sizes_ok,
        "Alle Seiten im Format A4." if sizes_ok else "Mindestens eine Seite ist nicht A4.",
    )

    full_text = normalize_ws(" ".join(p.get_text() for p in doc))
    missing_lines = [
        ln for ln in plan.printed_text().splitlines() if ln.strip() and normalize_ws(ln) not in full_text
    ]
    add(
        "TEXT_PRESENT",
        not missing_lines,
        "Alle Textbausteine sind im PDF enthalten."
        if not missing_lines
        else f"{len(missing_lines)} Textzeile(n) fehlen im PDF (z. B. durch Zeilenumbruch) – bitte Vorschau prüfen.",
        Severity.WARNING,
    )

    has_marks = has_unresolved_marks(full_text)
    add(
        "NO_PLACEHOLDERS",
        not has_marks or not final,
        "Keine offenen Platzhalter." if not has_marks else "Es sind noch fehlende Werte markiert.",
        Severity.ERROR if final else Severity.INFO,
    )
    watermark = "VORSCHAU" in full_text
    add(
        "NO_WATERMARK",
        (not watermark) if final else True,
        "Kein Vorschau-Wasserzeichen." if not watermark else "Vorschau-Wasserzeichen vorhanden.",
        Severity.ERROR if final else Severity.INFO,
    )

    span_fonts = {
        s["font"]
        for p in doc
        for b in p.get_text("dict")["blocks"]
        for ln in b.get("lines", [])
        for s in ln["spans"]
        if s["text"].strip()
    }
    embedded_names = {f[3] for p in doc for f in p.get_fonts() if f[1] == "ttf"}
    fonts_ok = bool(span_fonts) and all(any(f in e for e in embedded_names) for f in span_fonts)
    add(
        "FONTS_EMBEDDED",
        fonts_ok,
        "Alle verwendeten Schriften sind eingebettet."
        if fonts_ok
        else "Nicht eingebettete Schrift gefunden.",
    )

    box = pymupdf.Rect(
        (lay.addr_left) / 1,
        lay.anschrift_top / 1,
        (lay.addr_left + lay.addr_w) / 1,
        lay.addr_bottom / 1,
    )
    # DinLayout-Werte sind ReportLab-Punkte (mm * 72/25.4) -> direkt in PDF-Punkten vergleichbar
    in_window = True
    for line in plan.recipient_lines:
        hits = doc[0].search_for(line)
        if not hits or not any(box.contains(h) for h in hits):
            in_window = False
    add(
        "ADDRESS_IN_WINDOW",
        in_window and 0 < len(plan.recipient_lines) <= 6,
        "Empfängeranschrift liegt im Sichtfenster (DIN 5008, Anschriftzone)."
        if in_window
        else "Empfängeranschrift liegt nicht vollständig in der Anschriftzone.",
    )

    # Unterschriftsraum zwischen Grußformel und Namen
    sig_ok = False
    for p in doc:
        g = p.search_for(plan.closing) if plan.closing else []
        n = p.search_for(plan.signer) if plan.signer else []
        below = [h for h in n if g and h.y0 > g[-1].y1]
        if g and below:
            sig_ok = (below[0].y0 - g[-1].y1) >= 12 * PT_PER_MM
            break
    add(
        "SIGNATURE_SPACE",
        sig_ok,
        "Ausreichend Platz für die handschriftliche Unterschrift."
        if sig_ok
        else "Unterschriftsbereich zu klein oder nicht gefunden.",
    )

    meta = doc.metadata or {}
    meta_ok = not meta.get("author") and all(
        not part or part not in (meta.get("title") or "") for part in plan.sender_lines[:1]
    )
    add(
        "METADATA_NO_PII",
        meta_ok,
        "Metadaten ohne personenbezogene Angaben." if meta_ok else "Metadaten enthalten Personendaten.",
    )

    avail = set(available_renderers())
    for renderer in renderers:
        if renderer not in avail:
            add("RENDER_OK", False, f"Renderer {renderer} nicht verfügbar.", renderer=renderer)
            continue
        paths: list[str] = []
        try:
            for i in range(doc.page_count):
                img = render_page(pdf_path, i + 1, dpi=dpi, renderer=renderer)
                if render_dir is not None:
                    out = render_dir / renderer / f"seite_{i + 1:02d}.png"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    img.save(out)
                    paths.append(str(out))
            add("RENDER_OK", True, f"Alle Seiten mit {renderer} darstellbar.", renderer=renderer)
        except Exception as exc:  # noqa: BLE001
            add(
                "RENDER_OK",
                False,
                f"Darstellung mit {renderer} fehlgeschlagen ({type(exc).__name__}).",
                renderer=renderer,
            )
        rendered[renderer] = paths

    add(
        "PLAN_COMPLETE",
        not plan.blocking or not final,
        "Alle Pflichtangaben vollständig." if not plan.blocking else "Es gibt noch offene Pflichtpunkte.",
        Severity.ERROR if final else Severity.INFO,
    )
    doc.close()
    passed = all(c.passed for c in checks if c.severity == Severity.ERROR)
    return QaReport(
        passed=passed,
        checks=checks,
        rendered_pages=rendered,
        template_id=plan.template_id,
        template_version=plan.template_version,
        plan_mode=plan.mode,
        filled_sha256=_sha(pdf_path),
        flattened=False,
        duration_s=time.monotonic() - started,
    )
