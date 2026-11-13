"""Beleg-Ansicht: Quellseite mit hervorgehobener Fundstelle.

Die Seite wird über ``service.document_page_image`` lokal gerendert und auf die verfügbare
Fläche skaliert. Die Fundstelle (``Evidence.bbox`` in Seitenpunkten) wird über das
Verhältnis Bildbreite / Seitenbreite in Pixel umgerechnet (bei PDFs = dpi/72) und als
halbtransparentes Rechteck markiert.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Case
from ...core.fields import FIELD_REGISTRY
from ...core.models import BoundingBox, ExtractionMethod, FieldValue
from ...core.service import AppService
from .. import theme
from ..widgets.common import section_title
from .common import error_text, pil_to_qimage
from .formatting import candidate_display, currency_of, method_text, page_origin
from .importing import unlock_document

log = logging.getLogger(__name__)

PAGE_DPI = 110
LOCKED_PDF_TEXT = (
    "🔒 Dieses PDF ist passwortgeschützt. Die App speichert Passwörter nie – nach einem Neustart "
    "bitte erneut eingeben, damit die Quellseite angezeigt werden kann."
)


@dataclass
class EvidenceItem:
    document_id: str
    file_name: str
    page: int | None
    quote: str | None
    bbox: BoundingBox | None
    method: ExtractionMethod | None
    value: str = ""
    suggested: bool = False


def evidence_items(case: Case, key: str, fv: FieldValue | None) -> list[EvidenceItem]:
    """Alle Fundstellen eines Feldes (Hauptquelle zuerst, Dubletten entfernt)."""
    if fv is None:
        return []
    currency = currency_of(case)
    items: list[EvidenceItem] = []
    seen: set[tuple] = set()
    cands = list(enumerate(fv.candidates))

    def _primary_first(entry: tuple[int, object]) -> int:
        _i, c = entry
        ev = c.evidence  # type: ignore[attr-defined]
        return 0 if (ev.document_id == fv.source_document_id and ev.page == fv.source_page) else 1

    if fv.source_document_id:
        cands.sort(key=_primary_first)
    for idx, c in cands:
        ev = c.evidence
        bbox_key = (round(ev.bbox.x0), round(ev.bbox.y0)) if ev.bbox else None
        ident = (ev.document_id, ev.page, bbox_key)
        if ident in seen:
            continue
        seen.add(ident)
        items.append(
            EvidenceItem(
                document_id=ev.document_id,
                file_name=ev.file_name,
                page=ev.page,
                quote=ev.quote,
                bbox=ev.bbox,
                method=ev.method,
                value=candidate_display(key, c, currency),
                suggested=fv.suggestion_index == idx,
            )
        )
    if fv.source_document_id and not any(
        i.document_id == fv.source_document_id and i.page == fv.source_page for i in items
    ):
        doc = case.document(fv.source_document_id)
        items.insert(
            0,
            EvidenceItem(
                document_id=fv.source_document_id,
                file_name=doc.original_name if doc else fv.source_document_id,
                page=fv.source_page,
                quote=fv.source_quote,
                bbox=None,
                method=fv.extraction_method,
                value=fv.value or "",
            ),
        )
    return [i for i in items if case.document(i.document_id) is not None]


class PageCanvas(QWidget):
    """Zeichnet ein Seitenbild (passend skaliert) mit hervorgehobenen Rechtecken (Bildpixel)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image: QImage | None = None
        self.rects: list[QRectF] = []
        self.crop: QRectF | None = None  # sichtbarer Ausschnitt in Bildpixeln (None = ganze Seite)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(160, 120)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt-API)
        return QSize(340, 460)

    def set_page(self, image: QImage | None, rects: list[QRectF], crop: QRectF | None = None) -> None:
        self.image = image
        self.rects = rects
        self.crop = crop
        self.update()

    def target_rect(self) -> tuple[QRectF, QRectF] | None:
        """(Quellrechteck im Bild, Zielrechteck im Widget) oder None."""
        if self.image is None or self.image.isNull():
            return None
        src = self.crop or QRectF(0, 0, self.image.width(), self.image.height())
        if src.width() <= 0 or src.height() <= 0:
            return None
        scale = min(self.width() / src.width(), self.height() / src.height())
        w, h = src.width() * scale, src.height() * scale
        dst = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        return src, dst

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG_SUBTLE))
        geo = self.target_rect()
        if geo is None:
            p.end()
            return
        src, dst = geo
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(dst, self.image, src)
        scale = dst.width() / src.width()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for r in self.rects:
            mapped = QRectF(
                dst.x() + (r.x() - src.x()) * scale,
                dst.y() + (r.y() - src.y()) * scale,
                r.width() * scale,
                r.height() * scale,
            ).adjusted(-3, -3, 3, 3)
            p.setBrush(QColor(255, 196, 0, 90))
            p.setPen(QPen(QColor(theme.ACCENT), 3))
            p.drawRect(mapped)
        p.end()


class EvidenceView(QWidget):
    """Beleg zum ausgewählten Feld: Seite mit Markierung, Bildunterschrift und Textausschnitt."""

    CACHE_SIZE = 8

    def __init__(self, service: AppService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.case: Case | None = None
        self.key: str | None = None
        self.items: list[EvidenceItem] = []
        self._cache: OrderedDict[tuple[str, int], QImage] = OrderedDict()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(section_title("Beleg im Dokument"))
        self.field_label = QLabel("Bitte eine Zeile in der Tabelle auswählen.")
        self.field_label.setObjectName("Muted")
        self.field_label.setWordWrap(True)
        layout.addWidget(self.field_label)

        bar = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName("Fundstelle auswählen")
        self.source_combo.setToolTip("Bei mehreren Fundstellen hier zwischen den Belegen wechseln.")
        self.source_combo.currentIndexChanged.connect(self._show_current)
        bar.addWidget(self.source_combo, 1)
        self.zoom_btn = QPushButton("Vergrößern")
        self.zoom_btn.setAccessibleName("Ausschnitt vergrößern")
        self.zoom_btn.setCheckable(True)
        self.zoom_btn.setToolTip("Nur den Bereich um die Fundstelle vergrößert zeigen")
        self.zoom_btn.toggled.connect(self._show_current)
        bar.addWidget(self.zoom_btn)
        layout.addLayout(bar)

        self.canvas = PageCanvas()
        self.canvas.setAccessibleName("Quellseite mit markierter Fundstelle")
        layout.addWidget(self.canvas, 1)
        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.caption)
        self.quote = QLabel("")
        self.quote.setWordWrap(True)
        self.quote.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.quote.setObjectName("Muted")
        layout.addWidget(self.quote)
        self.unlock_btn = QPushButton("🔒 Passwort eingeben …")
        self.unlock_btn.setAccessibleName("Passwort für das geschützte Dokument eingeben")
        self.unlock_btn.setToolTip(
            "Passwörter werden nie gespeichert. Nach einem Neustart bitte erneut eingeben, "
            "damit die Quellseite angezeigt werden kann."
        )
        self.unlock_btn.clicked.connect(self._unlock_clicked)
        self.unlock_btn.hide()
        layout.addWidget(self.unlock_btn)
        self._password_declined: set[str] = set()  # nicht bei jeder Zeile erneut nachfragen
        self._set_empty("Bitte eine Zeile in der Tabelle auswählen.")

    # ------------------------------------------------------------------
    def invalidate_cache(self) -> None:
        self._cache.clear()

    def clear(self, message: str = "Bitte eine Zeile in der Tabelle auswählen.") -> None:
        self.key = None
        self._set_empty(message)

    def _set_empty(self, message: str) -> None:
        self.items = []
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.blockSignals(False)
        self.source_combo.setEnabled(False)
        self.zoom_btn.setEnabled(False)
        self.field_label.setText(message)
        self.canvas.set_page(None, [])
        self.caption.setText("")
        self.quote.setText("")
        self.unlock_btn.hide()

    def show_field(self, case: Case, key: str) -> None:
        if case is not self.case:
            self.case = case
        self.key = key
        label = FIELD_REGISTRY[key].label if key in FIELD_REGISTRY else key
        fv = case.get(key)
        items = evidence_items(case, key, fv)
        if not items:
            self._set_empty(
                f"{label}: keine Fundstelle in den Dokumenten (z. B. manuell eingetragen oder nicht gefunden)."
            )
            return
        self.items = items
        self.field_label.setText(
            f"{label}: {len(items)} Fundstelle(n)" if len(items) > 1 else f"{label}: Fundstelle"
        )
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for n, it in enumerate(items, start=1):
            page = f", S. {it.page}" if it.page else ""
            hint = " – Hinweis: vermutlich maßgeblich" if it.suggested else ""
            value = f"„{it.value}“ – " if it.value else ""
            self.source_combo.addItem(f"{n}: {value}{it.file_name}{page}{hint}")
        self.source_combo.setCurrentIndex(0)
        self.source_combo.blockSignals(False)
        self.source_combo.setEnabled(len(items) > 1)
        self._show_current()

    def current_item(self) -> EvidenceItem | None:
        idx = self.source_combo.currentIndex()
        if 0 <= idx < len(self.items):
            return self.items[idx]
        return None

    # ------------------------------------------------------------------
    def _page_image(self, document_id: str, page: int) -> QImage:
        key = (document_id, page)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        assert self.case is not None
        img = self.service.document_page_image(self.case, document_id, page, dpi=PAGE_DPI)
        qimg = pil_to_qimage(img)
        self._cache[key] = qimg
        while len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)
        return qimg

    def _ensure_unlocked(self, document_id: str, *, ask_again: bool = False) -> bool:
        """Geschütztes PDF: Passwort (nie gespeichert) einmal je Dokument erneut abfragen."""
        if self.case is None or not self.service.document_needs_password(self.case, document_id):
            return True
        if document_id in self._password_declined and not ask_again:
            return False
        if unlock_document(self, self.service, self.case, document_id):
            self._password_declined.discard(document_id)
            return True
        self._password_declined.add(document_id)
        return False

    def _unlock_clicked(self) -> None:
        it = self.current_item()
        if it is not None and self._ensure_unlocked(it.document_id, ask_again=True):
            self._show_current()

    def _scale(self, document_id: str, page: int, image: QImage) -> float:
        """Pixel je Seitenpunkt (über die gelesene Seitenbreite; Rückfall dpi/72)."""
        doc = self.case.document(document_id) if self.case else None
        if doc is not None and doc.text is not None:
            for p in doc.text.pages:
                if p.page_number == page and p.width > 0:
                    return image.width() / p.width
        return PAGE_DPI / 72.0

    def _show_current(self) -> None:
        it = self.current_item()
        if it is None or self.case is None:
            return
        method = method_text(it.method, page_origin(self.case, it.document_id, it.page))
        page_txt = f"Seite {it.page}" if it.page else "Seite unbekannt"
        self.caption.setText(
            f"{it.file_name}, {page_txt}, {method}" if method else f"{it.file_name}, {page_txt}"
        )
        self.quote.setText(
            f"Textausschnitt: „{it.quote}“" if it.quote else "Kein Textausschnitt gespeichert."
        )
        self.unlock_btn.hide()
        if not it.page:
            self.canvas.set_page(None, [])
            self.zoom_btn.setEnabled(False)
            return
        if not self._ensure_unlocked(it.document_id):
            self.canvas.set_page(None, [])
            self.zoom_btn.setEnabled(False)
            self.quote.setText(
                LOCKED_PDF_TEXT + (f"\nGespeicherter Textausschnitt: „{it.quote}“" if it.quote else "")
            )
            self.unlock_btn.show()
            return
        try:
            image = self._page_image(it.document_id, it.page)
        except Exception as exc:  # noqa: BLE001 – Anzeigefehler verständlich melden
            log.info("Seitenbild nicht verfügbar: %s", type(exc).__name__)
            self.canvas.set_page(None, [])
            self.quote.setText(f"Die Seite kann nicht angezeigt werden: {error_text(exc)}")
            self.zoom_btn.setEnabled(False)
            return
        rects: list[QRectF] = []
        if it.bbox is not None:
            s = self._scale(it.document_id, it.page, image)
            rects.append(QRectF(it.bbox.x0 * s, it.bbox.y0 * s, it.bbox.width * s, it.bbox.height * s))
        else:
            self.caption.setText(self.caption.text() + " (Position nicht bekannt – siehe Textausschnitt)")
        self.zoom_btn.setEnabled(bool(rects))
        crop = None
        if rects and self.zoom_btn.isChecked():
            r = rects[0]
            band = image.width() * 0.35
            top = max(0.0, r.center().y() - band / 2)
            crop = QRectF(0, top, image.width(), min(band, image.height() - top))
        self.canvas.set_page(image, rects, crop)
        self.canvas.setToolTip(
            "Gelb markiert: Fundstelle des Werts" if rects else "Fundstelle ohne Positionsangabe"
        )
        self.canvas.setStyleSheet(f"border: 1px solid {theme.BORDER};")
