"""Qualitätskontrolle einer befüllten Vorlage (vor dem Export).

Geprüft wird gegen die Originalvorlage und den ``FillPlan``. Alle Meldungen sind
deutsch und enthalten nur Feldnamen der Vorlage, nie die eingetragenen Werte.

Prüfcodes
---------
``FILE_OPENS`` ``PAGE_COUNT`` ``PAGE_SIZES`` ``ORIGINAL_PREFIX`` ``ENCRYPTION_KEPT``
``FIELD_VALUES`` ``SIGNATURES_EMPTY`` ``PRESERVED_PAGES`` ``TEXT_FITS`` ``GLYPHS``
``RENDER_OK`` ``VALUES_VISIBLE`` ``NO_PAGE_CHANGES`` ``PLAN_COMPLETE``

``QaReport.passed`` ist wahr, wenn alle Prüfungen mit Schweregrad ``ERROR``
bestanden sind.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from insurance_app.core.models import Severity
from insurance_app.core.textutil import normalize_ws
from insurance_app.pdf.fonts import fit_font_size_multiline, text_width, unsupported_chars
from insurance_app.pdf.inspector import FormInspection, PdfFieldInfo, inspect_document
from insurance_app.pdf.render import RenderError, render_all
from insurance_app.templates.resolver import FIELD_PADDING, STATE_KINDS, FillPlan, FillValue
from insurance_app.templates.schema import FillPolicy, MappingKind, TemplateDefinition

__all__ = ["QaCheck", "QaReport", "run_quality_checks"]

log = logging.getLogger(__name__)

_MAX_NAMES = 5


class QaCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    passed: bool
    severity: Severity = Severity.ERROR
    message: str
    page: int | None = None
    renderer: str | None = None


class QaReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    checks: list[QaCheck] = Field(default_factory=list)
    rendered_pages: dict[str, list[str]] = Field(default_factory=dict)  # Renderer -> PNG-Pfade
    template_id: str = ""
    template_version: str = ""
    plan_mode: str = "preview"
    filled_sha256: str | None = None  # Prüfsumme der geprüften Datei (Export prüft dagegen)
    flattened: bool = False
    duration_s: float = 0.0

    @property
    def failed_checks(self) -> list[QaCheck]:
        return [c for c in self.checks if not c.passed]

    @property
    def errors(self) -> list[QaCheck]:
        return [c for c in self.checks if not c.passed and c.severity == Severity.ERROR]

    @property
    def technical_passed(self) -> bool:
        """Alle technischen Prüfungen bestanden (ohne Vollständigkeit des Plans)."""
        return all(
            c.passed for c in self.checks if c.severity == Severity.ERROR and c.code != "PLAN_COMPLETE"
        )

    def check(self, code: str) -> list[QaCheck]:
        return [c for c in self.checks if c.code == code]


def _names(names: list[str]) -> str:
    shown = ", ".join(f"„{n}“" for n in names[:_MAX_NAMES])
    more = len(names) - _MAX_NAMES
    return shown + (f" und {more} weitere" if more > 0 else "")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Qa:
    def __init__(
        self,
        template: TemplateDefinition,
        original_pdf: Path,
        filled_pdf: Path,
        plan: FillPlan,
        render_dir: Path | None,
        renderers: tuple[str, ...],
        dpi: int,
        flattened: bool,
    ) -> None:
        self.t = template
        self.original_pdf = Path(original_pdf)
        self.filled_pdf = Path(filled_pdf)
        self.plan = plan
        self.render_dir = Path(render_dir) if render_dir else None
        self.renderers = renderers
        self.dpi = dpi
        self.flattened = flattened
        self.checks: list[QaCheck] = []
        self.rendered: dict[str, list[str]] = {}
        self._ap_sizes: dict[int, float] = {}

    def add(
        self,
        code: str,
        passed: bool,
        message: str,
        severity: Severity = Severity.ERROR,
        page: int | None = None,
        renderer: str | None = None,
    ) -> None:
        self.checks.append(
            QaCheck(
                code=code, passed=passed, severity=severity, message=message, page=page, renderer=renderer
            )
        )

    # ------------------------------------------------------------------
    def overlay_values(self) -> list[FillValue]:
        out = []
        for v in self.plan.values:
            m = self.t.mapping(v.pdf_field)
            if v.overlay_rect or (m and m.overlay_rect):
                out.append(v)
        return out

    def field_values(self) -> dict[str, FillValue]:
        overlay = {id(v) for v in self.overlay_values()}
        return {v.pdf_field: v for v in self.plan.values if id(v) not in overlay}

    # ------------------------------------------------------------------
    def run(self) -> QaReport:
        started = time.monotonic()
        filled_bytes: bytes | None = None
        try:
            original_bytes = self.original_pdf.read_bytes()
            filled_bytes = self.filled_pdf.read_bytes()
        except OSError:
            self.add("FILE_OPENS", False, "Die erzeugte oder die Original-PDF kann nicht gelesen werden.")
            return self.report(None, started)

        try:
            odoc = pymupdf.open(stream=original_bytes, filetype="pdf")
        except Exception:
            self.add("FILE_OPENS", False, "Die Originalvorlage kann nicht geöffnet werden.")
            return self.report(filled_bytes, started)
        try:
            fdoc = pymupdf.open(stream=filled_bytes, filetype="pdf")
        except Exception:
            odoc.close()
            self.add("FILE_OPENS", False, "Die erzeugte PDF kann nicht geöffnet werden.")
            return self.report(filled_bytes, started)
        try:
            if fdoc.needs_pass or odoc.needs_pass:
                self.add("FILE_OPENS", False, "Die PDF verlangt ein Passwort zum Öffnen.")
                return self.report(filled_bytes, started)
            pypdf_fields = self.check_pypdf()
            if pypdf_fields is None:
                return self.report(filled_bytes, started)
            self.add("FILE_OPENS", True, "Die PDF lässt sich mit MuPDF und pypdf öffnen.")
            ins_o = inspect_document(odoc)
            ins_f = inspect_document(fdoc)
            self._ap_sizes = _appearance_font_sizes(fdoc, ins_f)
            self.check_pages(odoc, fdoc)
            self.check_prefix(original_bytes, filled_bytes)
            self.check_encryption(ins_o, ins_f)
            self.check_field_values(ins_o, ins_f, pypdf_fields, fdoc)
            self.check_signatures(ins_f)
            self.check_text_fits(ins_o, ins_f)
            self.check_glyphs(ins_f, fdoc)
            self.check_page_changes(odoc, fdoc)
        finally:
            odoc.close()
            fdoc.close()
        self.check_plan()
        self.check_rendering(ins_o)
        return self.report(filled_bytes, started)

    def report(self, filled_bytes: bytes | None, started: float) -> QaReport:
        passed = all(c.passed for c in self.checks if c.severity == Severity.ERROR) and any(
            c.code == "FILE_OPENS" and c.passed for c in self.checks
        )
        report = QaReport(
            passed=passed,
            checks=self.checks,
            rendered_pages=self.rendered,
            template_id=self.t.template_id,
            template_version=self.t.version,
            plan_mode=self.plan.mode,
            filled_sha256=_sha256(filled_bytes) if filled_bytes is not None else None,
            flattened=self.flattened,
            duration_s=round(time.monotonic() - started, 3),
        )
        log.info(
            "Qualitätskontrolle: template=%s bestanden=%s fehler=%d prüfungen=%d dauer=%.2fs",
            self.t.template_id,
            report.passed,
            len(report.errors),
            len(report.checks),
            report.duration_s,
        )
        return report

    # ------------------------------------------------------------------
    def check_pypdf(self) -> dict[str, object] | None:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(self.filled_pdf))
            if reader.is_encrypted and not reader.decrypt(""):
                self.add("FILE_OPENS", False, "pypdf kann die verschlüsselte PDF nicht öffnen.")
                return None
            _ = len(reader.pages)
            fields = reader.get_fields() or {}
            return {name: field.get("/V") for name, field in fields.items()}
        except Exception as exc:
            log.info("pypdf-Prüfung fehlgeschlagen (%s)", type(exc).__name__)
            self.add("FILE_OPENS", False, "Die erzeugte PDF kann mit pypdf nicht gelesen werden.")
            return None

    def check_pages(self, odoc: pymupdf.Document, fdoc: pymupdf.Document) -> None:
        ok = fdoc.page_count == odoc.page_count == self.t.page_count
        self.add(
            "PAGE_COUNT",
            ok,
            f"Seitenzahl {fdoc.page_count} (erwartet {self.t.page_count})."
            if not ok
            else f"Seitenzahl stimmt ({fdoc.page_count}).",
        )
        bad: list[int] = []
        for index in range(min(odoc.page_count, fdoc.page_count)):
            a, b = odoc[index].rect, fdoc[index].rect
            if (
                abs(a.width - b.width) > 0.01
                or abs(a.height - b.height) > 0.01
                or (odoc[index].rotation != fdoc[index].rotation)
            ):
                bad.append(index + 1)
        self.add(
            "PAGE_SIZES",
            not bad,
            "Seitenformate unverändert." if not bad else f"Seitenformat geändert auf Seite {bad}.",
            page=bad[0] if bad else None,
        )

    def check_prefix(self, original: bytes, filled: bytes) -> None:
        if self.flattened:
            self.add(
                "ORIGINAL_PREFIX",
                True,
                "Nicht anwendbar: Die Formularfelder wurden fixiert (vollständig neu gespeichert).",
                severity=Severity.INFO,
            )
            return
        ok = len(filled) > len(original) and filled.startswith(original)
        self.add(
            "ORIGINAL_PREFIX",
            ok,
            "Die Ausgabe beginnt byte-identisch mit der Originalvorlage (inkrementell gespeichert)."
            if ok
            else "Die Ausgabe beginnt nicht byte-identisch mit der Originalvorlage.",
        )

    def check_encryption(self, ins_o: FormInspection, ins_f: FormInspection) -> None:
        a, b = ins_o.permissions, ins_f.permissions
        ok = a.encrypted == b.encrypted and a.raw == b.raw and a.encryption_method == b.encryption_method
        self.add(
            "ENCRYPTION_KEPT",
            ok,
            "Verschlüsselung und PDF-Berechtigungen sind unverändert."
            if ok
            else "Verschlüsselung oder PDF-Berechtigungen haben sich verändert.",
        )

    def check_field_values(
        self,
        ins_o: FormInspection,
        ins_f: FormInspection,
        pypdf_values: dict[str, object],
        fdoc: pymupdf.Document,
    ) -> None:
        expected = self.field_values()
        problems: list[str] = []
        if self.flattened:
            # Felder sind fixiert: Textwerte müssen als Seitentext vorhanden sein
            page_text = {i + 1: normalize_ws(fdoc[i].get_text()) for i in range(fdoc.page_count)}
            for name, value in expected.items():
                if value.kind in STATE_KINDS:
                    continue
                pages = [value.page] if value.page else list(page_text)
                if not any(normalize_ws(value.text or "") in page_text.get(p, "") for p in pages):
                    problems.append(name)
            stray = [f.name for f in ins_f.fields]
            self.add(
                "FIELD_VALUES",
                not problems and not stray,
                "Fixierte Werte sind als Seitentext vorhanden."
                if not problems and not stray
                else f"Werte nicht auffindbar oder Felder nicht fixiert: {_names(problems + stray)}.",
            )
            self.check_overlay_text(fdoc)
            return

        ignored = set(self.t.ignored_fields)
        for name, value in expected.items():
            info = ins_f.field(name)
            if info is None:
                problems.append(name)
                continue
            if value.kind in STATE_KINDS:
                state = value.state or "Off"
                if state == "Off":
                    ok = (info.value in (None, "Off")) and all(
                        w.current_state in (None, "Off") for w in info.widgets
                    )
                else:
                    ok = info.value == state and all(
                        (w.current_state == state)
                        if w.on_state == state
                        else (w.current_state in (None, "Off"))
                        for w in info.widgets
                    )
                pv = pypdf_values.get(name)
                pv_s = str(pv) if pv is not None else None
                ok = ok and (pv_s == f"/{state}" or (state == "Off" and pv_s in (None, "/Off")))
            else:
                stored = value.stored_value or ""
                pv = pypdf_values.get(name)
                ok = info.value == stored and (str(pv) if pv is not None else "") == stored
            if not ok:
                problems.append(name)

        stray: list[str] = []
        for info in ins_f.fields:
            if info.name in expected or info.name in ignored or info.field_type == "button":
                continue
            before = ins_o.field(info.name)
            before_value = before.value if before else None
            before_states = [w.current_state for w in before.widgets] if before else []
            after_states = [w.current_state for w in info.widgets]
            changed = info.value != before_value or (before is not None and before_states != after_states)
            if changed or (before is None and info.has_value):
                stray.append(info.name)
        missing_fields = [f.name for f in ins_o.fields if ins_f.field(f.name) is None]
        ok = not problems and not stray and not missing_fields
        if ok:
            msg = f"Alle {len(expected)} Feldwerte wurden zurückgelesen; keine weiteren Felder verändert."
        else:
            parts = []
            if problems:
                parts.append(f"abweichend: {_names(problems)}")
            if stray:
                parts.append(f"unerwartet verändert: {_names(stray)}")
            if missing_fields:
                parts.append(f"fehlend: {_names(missing_fields)}")
            msg = "Feldwerte stimmen nicht: " + "; ".join(parts) + "."
        self.add("FIELD_VALUES", ok, msg)
        self.check_overlay_text(fdoc)

    def check_overlay_text(self, fdoc: pymupdf.Document) -> None:
        overlay = self.overlay_values()
        if not overlay:
            return
        missing: list[str] = []
        for value in overlay:
            page_no = value.page or 1
            if not 1 <= page_no <= fdoc.page_count:
                missing.append(value.pdf_field)
                continue
            text = normalize_ws(fdoc[page_no - 1].get_text())
            if normalize_ws(value.text or "") not in text:
                missing.append(value.pdf_field)
        self.add(
            "FIELD_VALUES",
            not missing,
            "Overlay-Texte sind auf der Seite vorhanden."
            if not missing
            else f"Overlay-Text nicht gefunden: {_names(missing)}.",
        )

    def check_signatures(self, ins_f: FormInspection) -> None:
        never = {
            m.pdf_field
            for m in self.t.fields
            if m.kind in (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE)
            or m.fill_policy == FillPolicy.NEVER
        }
        planned = [v.pdf_field for v in self.plan.values if v.pdf_field in never]
        filled = [
            f.name
            for f in ins_f.fields
            if (f.field_type == "signature" or f.name in never)
            and (f.has_value or any(w.current_state not in (None, "Off") for w in f.widgets))
        ]
        bad = list(dict.fromkeys(planned + filled))
        self.add(
            "SIGNATURES_EMPTY",
            not bad,
            "Unterschriftsfelder und Unterschriftsdaten sind leer."
            if not bad
            else f"Unterschriftsfelder dürfen nicht befüllt sein: {_names(bad)}.",
        )

    def check_text_fits(self, ins_o: FormInspection, ins_f: FormInspection) -> None:
        bad: list[str] = []
        for name, value in self.field_values().items():
            if value.kind in STATE_KINDS or not value.text:
                continue
            info = ins_o.field(name)
            if info is None or not info.widgets:
                continue
            out_info = ins_f.field(name)
            size = self.ap_font_size(out_info) if out_info else None
            size = (
                size or (out_info.font_size if out_info and out_info.font_size else None) or value.font_size
            )
            size = size or info.font_size or 10.0
            if info.max_len and len(value.text) > info.max_len:
                bad.append(name)
                continue
            if info.comb:
                continue
            width = min(w.width for w in info.widgets)
            height = min(w.height for w in info.widgets)
            if info.multiline or value.kind == MappingKind.MULTILINE:
                fits = (
                    fit_font_size_multiline(
                        value.text, width, height, size, min_size=size, padding=FIELD_PADDING
                    )
                    is not None
                )
            else:
                fits = text_width(value.text, size) <= width - FIELD_PADDING + 0.01
            if not fits:
                bad.append(name)
        self.add(
            "TEXT_FITS",
            not bad,
            "Alle Texte passen in ihre Felder." if not bad else f"Text zu lang für: {_names(bad)}.",
        )

    def ap_font_size(self, info: PdfFieldInfo) -> float | None:
        """Tatsächlich dargestellte Schriftgröße (größter Wert über alle Widgets)."""
        sizes = [size for w in info.widgets if (size := self._ap_sizes.get(w.xref))]
        return max(sizes) if sizes else None

    def check_glyphs(self, ins_f: FormInspection, fdoc: pymupdf.Document) -> None:
        bad = [v.pdf_field for v in self.plan.values if v.text and unsupported_chars(v.text)]
        self.add(
            "GLYPHS",
            not bad,
            "Alle Zeichen sind mit der Feldschrift darstellbar (WinAnsi)."
            if not bad
            else f"Nicht darstellbare Zeichen in: {_names(bad)}.",
        )
        if self.flattened:
            return
        # Darstellung prüfen: Die Feld-Darstellung muss den Text enthalten.
        missing: list[str] = []
        for name, value in self.field_values().items():
            if value.kind in STATE_KINDS or not value.text or unsupported_chars(value.text):
                continue
            info = ins_f.field(name)
            if info is None:
                continue
            for widget in info.widgets:
                stream = _appearance_stream(fdoc, widget.xref)
                if stream is None or not _contains_words(stream, value.text):
                    missing.append(name)
                    break
        self.add(
            "GLYPHS",
            not missing,
            "Die Felddarstellungen enthalten die eingetragenen Texte."
            if not missing
            else f"Felddarstellung ohne erwarteten Text: {_names(missing)}.",
            severity=Severity.WARNING,
        )

    def check_page_changes(self, odoc: pymupdf.Document, fdoc: pymupdf.Document) -> None:
        overlay_pages = {v.page or 1 for v in self.overlay_values()}
        bad: list[int] = []
        for index in range(min(odoc.page_count, fdoc.page_count)):
            po, pf = odoc[index], fdoc[index]
            annots_o = len(list(po.annots() or []))
            annots_f = len(list(pf.annots() or []))
            widgets_o = len(list(po.widgets() or []))
            widgets_f = len(list(pf.widgets() or []))
            if annots_o != annots_f:
                bad.append(index + 1)
                continue
            if self.flattened:
                if widgets_f != 0:
                    bad.append(index + 1)
                continue
            if widgets_o != widgets_f:
                bad.append(index + 1)
                continue
            if (index + 1) not in overlay_pages and po.read_contents() != pf.read_contents():
                bad.append(index + 1)
        self.add(
            "NO_PAGE_CHANGES",
            not bad,
            "Seiteninhalte, Anmerkungen und Feldanzahl sind unverändert."
            if not bad
            else f"Unerwartete Änderungen auf Seite {', '.join(map(str, bad))}.",
            page=bad[0] if bad else None,
        )

    def check_plan(self) -> None:
        if self.plan.template_id != self.t.template_id:
            self.add("PLAN_COMPLETE", False, "Der Befüllungsplan gehört zu einer anderen Vorlage.")
            return
        open_items = self.plan.blocking_items
        self.add(
            "PLAN_COMPLETE",
            not open_items,
            "Keine offenen Pflichtpunkte."
            if not open_items
            else f"Es gibt noch {len(open_items)} offene Pflichtpunkte in der Checkliste.",
        )

    # ------------------------------------------------------------------
    def check_rendering(self, ins_o: FormInspection) -> None:
        for renderer in self.renderers:
            try:
                original = render_all(self.original_pdf, self.dpi, renderer)
                filled = render_all(self.filled_pdf, self.dpi, renderer)
            except (RenderError, ValueError) as exc:
                msg = getattr(exc, "user_message", None) or "Rendering nicht möglich."
                self.add("RENDER_OK", False, f"{renderer}: {msg}", renderer=renderer)
                continue
            sizes_ok = len(original) == len(filled) and all(
                a.size == b.size for a, b in zip(original, filled, strict=False)
            )
            self.add(
                "RENDER_OK",
                sizes_ok and len(filled) == self.t.page_count,
                f"{renderer}: alle {len(filled)} Seiten dargestellt."
                if sizes_ok
                else f"{renderer}: Seiten konnten nicht vollständig dargestellt werden.",
                renderer=renderer,
            )
            if self.render_dir is not None:
                self.rendered[renderer] = self.save_pngs(renderer, filled)
            if not sizes_ok:
                continue
            arrays_o = [np.asarray(img) for img in original]
            arrays_f = [np.asarray(img) for img in filled]
            self.check_preserved(renderer, arrays_o, arrays_f)
            self.check_visible(renderer, ins_o, arrays_o, arrays_f)

    def save_pngs(self, renderer: str, images: list[Image.Image]) -> list[str]:
        assert self.render_dir is not None
        target = self.render_dir / renderer
        target.mkdir(parents=True, exist_ok=True)
        out: list[str] = []
        for index, image in enumerate(images, start=1):
            path = target / f"seite_{index:02d}.png"
            image.save(path, format="PNG")
            out.append(str(path))
        return out

    def check_preserved(self, renderer: str, arrays_o: list[np.ndarray], arrays_f: list[np.ndarray]) -> None:
        pages = self.t.pages_to_preserve
        if not pages:
            return
        bad = [
            p
            for p in pages
            if not (1 <= p <= len(arrays_f)) or not np.array_equal(arrays_o[p - 1], arrays_f[p - 1])
        ]
        self.add(
            "PRESERVED_PAGES",
            not bad,
            f"{renderer}: Seiten {', '.join(map(str, pages))} sind pixelgleich mit dem Original."
            if not bad
            else f"{renderer}: Seite {', '.join(map(str, bad))} weicht vom Original ab.",
            page=bad[0] if bad else None,
            renderer=renderer,
        )

    def check_visible(
        self,
        renderer: str,
        ins_o: FormInspection,
        arrays_o: list[np.ndarray],
        arrays_f: list[np.ndarray],
    ) -> None:
        scale = self.dpi / 72.0
        changed: list[tuple[int, list[float], str]] = []  # (Seite, Rechteck, Feld) – muss sich ändern
        unchanged: list[tuple[int, list[float], str]] = []  # muss gleich bleiben
        planned = self.field_values()
        for info in ins_o.fields:
            value = planned.get(info.name)
            if self.flattened and info.field_type == "signature":
                # MuPDF markiert unsignierte Signaturfelder selbst; nach dem Fixieren entfällt
                # diese Viewer-Markierung (kein Dokumentinhalt) – daher hier nicht vergleichen.
                continue
            for w in info.widgets:
                if value is None:
                    unchanged.append((w.page, w.rect, info.name))
                elif value.kind in STATE_KINDS:
                    target = (
                        changed
                        if (value.state not in (None, "Off") and w.on_state == value.state)
                        else unchanged
                    )
                    target.append((w.page, w.rect, info.name))
                elif value.text:
                    changed.append((w.page, w.rect, info.name))
                else:
                    unchanged.append((w.page, w.rect, info.name))
        for value in self.overlay_values():
            rect = value.overlay_rect or (self.t.mapping(value.pdf_field).overlay_rect or [])  # type: ignore[union-attr]
            if rect:
                changed.append((value.page or 1, list(rect), value.pdf_field))

        masks: dict[int, np.ndarray] = {}
        for page, rect, _ in changed:
            if not 1 <= page <= len(arrays_f):
                continue
            h, w = arrays_f[page - 1].shape[:2]
            mask = masks.setdefault(page, np.zeros((h, w), dtype=bool))
            x0, y0, x1, y1 = _pixel_box(rect, scale, w, h, grow=2)
            mask[y0:y1, x0:x1] = True

        invisible: list[str] = []
        for page, rect, name in changed:
            if not 1 <= page <= len(arrays_f):
                invisible.append(name)
                continue
            a, b = arrays_o[page - 1], arrays_f[page - 1]
            h, w = a.shape[:2]
            x0, y0, x1, y1 = _pixel_box(rect, scale, w, h)
            if x1 <= x0 or y1 <= y0 or np.array_equal(a[y0:y1, x0:x1], b[y0:y1, x0:x1]):
                invisible.append(name)
        altered: list[str] = []
        for page, rect, name in unchanged:
            if not 1 <= page <= len(arrays_f):
                continue
            a, b = arrays_o[page - 1], arrays_f[page - 1]
            h, w = a.shape[:2]
            x0, y0, x1, y1 = _pixel_box(rect, scale, w, h)
            region_a, region_b = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
            diff = np.any(region_a != region_b, axis=-1)
            mask = masks.get(page)
            if mask is not None:
                diff &= ~mask[y0:y1, x0:x1]
            if diff.any():
                altered.append(name)
        invisible = list(dict.fromkeys(invisible))
        altered = list(dict.fromkeys(altered))
        ok = not invisible and not altered
        parts = []
        if invisible:
            parts.append(f"nicht sichtbar: {_names(invisible)}")
        if altered:
            parts.append(f"unerwartet sichtbar verändert: {_names(altered)}")
        self.add(
            "VALUES_VISIBLE",
            ok,
            f"{renderer}: Alle eingetragenen Werte sind sichtbar; leere Felder sind unverändert."
            if ok
            else f"{renderer}: " + "; ".join(parts) + ".",
            renderer=renderer,
        )


def _pixel_box(
    rect: list[float], scale: float, width: int, height: int, grow: int = 0
) -> tuple[int, int, int, int]:
    x0 = max(0, int(np.floor(rect[0] * scale)) - grow)
    y0 = max(0, int(np.floor(rect[1] * scale)) - grow)
    x1 = min(width, int(np.ceil(rect[2] * scale)) + grow)
    y1 = min(height, int(np.ceil(rect[3] * scale)) + grow)
    return x0, y0, x1, y1


def _appearance_stream(doc: pymupdf.Document, widget_xref: int) -> bytes | None:
    kind, val = doc.xref_get_key(widget_xref, "AP/N")
    if kind != "xref":
        return None
    try:
        xref = int(val.split()[0])
        return doc.xref_stream(xref)
    except (ValueError, RuntimeError):
        return None


_TF_RE = re.compile(rb"/[^\s/]+\s+([\d.]+)\s+Tf")


def _appearance_font_sizes(doc: pymupdf.Document, inspection: FormInspection) -> dict[int, float]:
    """Schriftgröße je Text-Widget laut Darstellungsstrom (``/Helv 9 Tf``)."""
    sizes: dict[int, float] = {}
    for info in inspection.fields:
        if info.field_type != "text":
            continue
        for widget in info.widgets:
            stream = _appearance_stream(doc, widget.xref)
            if not stream:
                continue
            found = [float(m.group(1)) for m in _TF_RE.finditer(stream)]
            if found:
                sizes[widget.xref] = max(found)
    return sizes


def _pdf_escape(data: bytes) -> bytes:
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _contains_words(stream: bytes, text: str) -> bool:
    for word in text.split():
        try:
            encoded = word.encode("cp1252")
        except UnicodeEncodeError:
            return False
        if _pdf_escape(encoded) not in stream and encoded not in stream:
            return False
    return True


def run_quality_checks(
    template: TemplateDefinition,
    original_pdf: Path,
    filled_pdf: Path,
    plan: FillPlan,
    render_dir: Path | None = None,
    renderers: tuple[str, ...] = ("mupdf", "pdfium"),
    dpi: int = 100,
    flattened: bool = False,
) -> QaReport:
    """Qualitätskontrolle ausführen (siehe Modul-Dokumentation).

    ``flattened=True`` für fixierte Ausgaben: Feldwerte werden dann über den
    Seitentext und die Darstellung geprüft, der Byte-Präfix entfällt.
    """
    return _Qa(template, original_pdf, filled_pdf, plan, render_dir, tuple(renderers), dpi, flattened).run()
