"""Koordinateneditor für PDFs ohne Formularfelder (Release 2, Grundfunktion).

Bedienung
* Seite wählen, mit der Maus ein Rechteck aufziehen -> Dialog (Beschriftung,
  Datenfeld, Art, Schriftgröße) -> neues Koordinatenfeld.
* Rechtecke lassen sich verschieben (Maus oder Pfeiltasten, 1 Punkt je Druck),
  in der Größe ändern (Umschalt + Pfeiltasten oder Eingabefelder x/y/Breite/Höhe)
  und mit „Entf“ löschen.
* Koordinaten werden in PDF-Punkten gespeichert (1 Punkt = 1/72 Zoll),
  Ursprung oben links – passend zu ``FieldMapping.overlay_rect``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeyEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.fields import FIELD_REGISTRY
from ...pdf.render import render_page
from ...templates.registry import TemplateRegistryError, validate_definition_against_pdf
from ...templates.schema import FieldMapping, MappingKind, TemplateDefinition
from ...templates.studio import coordinate_field
from .. import dialogs
from ..theme import ERROR, PRIMARY
from ..widgets.common import danger_button, muted, primary_button
from .common import (
    KIND_LABELS,
    OVERLAY_KINDS,
    TOOLTIP_OVERLAY,
    bullet_list,
    combo_source,
    field_label,
    friendly_message,
    make_source_combo,
    pil_to_qimage,
    template_status_text,
)

log = logging.getLogger(__name__)

RENDER_DPI = 100
SCALE = RENDER_DPI / 72.0  # Szene-Pixel je PDF-Punkt
MIN_SIZE_PT = 4.0


@dataclass
class OverlaySpec:
    label: str
    source: str
    kind: MappingKind = MappingKind.TEXT
    font_size: float = 10.0


@dataclass
class OverlayField:
    name: str
    page: int
    rect: list[float]  # [x0, y0, x1, y1] in PDF-Punkten, oben links
    spec: OverlaySpec
    original: FieldMapping | None = None
    item: OverlayRectItem | None = field(default=None, repr=False)

    def to_mapping(self) -> FieldMapping:
        mapping = coordinate_field(
            self.name,
            self.page,
            [round(v, 1) for v in self.rect],
            self.spec.label,
            self.spec.source,
            kind=self.spec.kind,
            font_size=self.spec.font_size,
            align=(self.original.align if self.original and self.original.align else "left"),
            required=bool(self.original and self.original.required),
        )
        if self.original is not None:
            mapping = mapping.model_copy(
                update={
                    "conditions": self.original.conditions,
                    "legal_relevant": self.original.legal_relevant,
                    "help": self.original.help,
                    "fill_policy": self.original.fill_policy,
                }
            )
        return mapping


# ---------------------------------------------------------------------------
# Dialog für die Eigenschaften eines Koordinatenfelds
# ---------------------------------------------------------------------------


class OverlayFieldDialog(QDialog):
    def __init__(self, spec: OverlaySpec | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Koordinatenfeld")
        layout = QVBoxLayout(self)
        layout.addWidget(muted("Welcher Wert soll an dieser Stelle der Seite stehen?"))
        form = QFormLayout()
        self.label_edit = QLineEdit(spec.label if spec else "")
        self.label_edit.setPlaceholderText("z. B. Vertragsnummer")
        self.label_edit.setAccessibleName("Beschriftung")
        form.addRow("Beschriftung", self.label_edit)
        self.source_combo = make_source_combo(spec.source if spec else None)
        form.addRow("Datenfeld", self.source_combo)
        self.kind_combo = QComboBox()
        for kind in OVERLAY_KINDS:
            self.kind_combo.addItem(KIND_LABELS[kind], kind.value)
        if spec:
            self.kind_combo.setCurrentIndex(max(self.kind_combo.findData(spec.kind.value), 0))
        self.kind_combo.setAccessibleName("Art")
        form.addRow("Art", self.kind_combo)
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(6.0, 24.0)
        self.size_spin.setSingleStep(0.5)
        self.size_spin.setValue(spec.font_size if spec else 10.0)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setAccessibleName("Schriftgröße")
        self.size_spin.setToolTip("Schriftgröße in Punkt. Zu langer Text wird automatisch verkleinert.")
        form.addRow("Schriftgröße", self.size_spin)
        layout.addLayout(form)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {ERROR};")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Übernehmen")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.source_combo.currentIndexChanged.connect(self._auto_label)

    def _auto_label(self) -> None:
        key = combo_source(self.source_combo)
        if key and not self.label_edit.text().strip():
            fdef = FIELD_REGISTRY.get(key)
            if fdef:
                self.label_edit.setText(fdef.label)

    def spec(self) -> OverlaySpec | None:
        key = combo_source(self.source_combo)
        if not key:
            return None
        label = self.label_edit.text().strip() or field_label(key)
        return OverlaySpec(
            label=label,
            source=key,
            kind=MappingKind(self.kind_combo.currentData()),
            font_size=float(self.size_spin.value()),
        )

    def _accept(self) -> None:
        if self.spec() is None:
            self.error.setText("✖ Bitte ein Datenfeld wählen.")
            return
        self.accept()


def ask_overlay_field(parent: QWidget | None, spec: OverlaySpec | None = None) -> OverlaySpec | None:
    """Eigenschaften abfragen (in Tests per ``monkeypatch`` ersetzbar)."""
    dlg = OverlayFieldDialog(spec, parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.spec()
    return None


# ---------------------------------------------------------------------------
# Grafik
# ---------------------------------------------------------------------------


class OverlayRectItem(QGraphicsRectItem):
    """Verschiebbares Rechteck eines Koordinatenfelds (lokales Rechteck ab 0/0, Position = Szene)."""

    def __init__(self, ofield: OverlayField, on_moved: Callable[[OverlayField], None]) -> None:
        x0, y0, x1, y1 = ofield.rect
        super().__init__(0, 0, (x1 - x0) * SCALE, (y1 - y0) * SCALE)
        self.ofield = ofield
        self._on_moved = on_moved
        self.setPos(x0 * SCALE, y0 * SCALE)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        pen = QPen(QColor(PRIMARY))
        pen.setWidthF(1.5)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(31, 95, 173, 40)))
        self.text = QGraphicsSimpleTextItem(ofield.spec.label, self)
        self.text.setBrush(QBrush(QColor(PRIMARY)))
        self.text.setPos(2, -16)
        self.setToolTip(f"{ofield.spec.label} – {field_label(ofield.spec.source)}")
        self.setZValue(10)

    def sync_from_field(self) -> None:
        x0, y0, x1, y1 = self.ofield.rect
        self.prepareGeometryChange()
        self.setRect(0, 0, (x1 - x0) * SCALE, (y1 - y0) * SCALE)
        self.setPos(x0 * SCALE, y0 * SCALE)
        self.text.setText(self.ofield.spec.label)
        self.setToolTip(f"{self.ofield.spec.label} – {field_label(self.ofield.spec.source)}")

    def itemChange(self, change, value):  # noqa: N802 (Qt-API)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene() is not None:
            bounds = self.scene().sceneRect()
            pos = QPointF(value)
            w, h = self.rect().width(), self.rect().height()
            x = min(max(pos.x(), bounds.left()), bounds.right() - w)
            y = min(max(pos.y(), bounds.top()), bounds.bottom() - h)
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            x0 = self.pos().x() / SCALE
            y0 = self.pos().y() / SCALE
            w = self.rect().width() / SCALE
            h = self.rect().height() / SCALE
            self.ofield.rect = [x0, y0, x0 + w, y0 + h]
            self._on_moved(self.ofield)
        return super().itemChange(change, value)


class PageView(QGraphicsView):
    """Seitenansicht: Rechteck aufziehen erzeugt ein neues Feld; Tasten bewegen/ändern die Auswahl."""

    rect_drawn = Signal(QRectF)  # Szene-Koordinaten
    nudge = Signal(float, float, bool)  # dx, dy (Punkte), Größe ändern?
    delete_requested = Signal()

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self._origin: QPointF | None = None
        self._rubber: QGraphicsRectItem | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Seitenansicht – Rechteck aufziehen, um ein Feld anzulegen")

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is None or isinstance(item, QGraphicsPixmapItem):
                self._origin = self.mapToScene(event.position().toPoint())
                pen = QPen(QColor(PRIMARY))
                pen.setStyle(Qt.PenStyle.DashLine)
                self._rubber = self.scene().addRect(QRectF(self._origin, self._origin), pen)
                self.scene().clearSelection()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        if self._rubber is not None and self._origin is not None:
            pos = self.mapToScene(event.position().toPoint())
            self._rubber.setRect(QRectF(self._origin, pos).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        if self._rubber is not None:
            rect = self._rubber.rect().normalized().intersected(self.scene().sceneRect())
            self.scene().removeItem(self._rubber)
            self._rubber = None
            self._origin = None
            if rect.width() >= MIN_SIZE_PT * SCALE and rect.height() >= MIN_SIZE_PT * SCALE:
                self.rect_drawn.emit(rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt-API)
        key = event.key()
        resize = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        step = 1.0
        moves = {
            Qt.Key.Key_Left: (-step, 0.0),
            Qt.Key.Key_Right: (step, 0.0),
            Qt.Key.Key_Up: (0.0, -step),
            Qt.Key.Key_Down: (0.0, step),
        }
        if key in moves:
            dx, dy = moves[key]
            self.nudge.emit(dx, dy, resize)
            event.accept()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class CoordinateEditorDialog(QDialog):
    """Koordinatenfelder anlegen, verschieben, ändern und speichern."""

    saved = Signal(str, bool)

    def __init__(
        self,
        service,  # AppService
        catalog_id: str,
        definition: TemplateDefinition,
        pdf_path: Path,
        page_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.catalog_id = catalog_id
        self.definition = definition
        self.pdf_path = Path(pdf_path)
        self.page_count = max(1, page_count)
        self.page = 1
        self.fields: list[OverlayField] = []
        self._syncing = False
        self.setWindowTitle(f"Koordinateneditor – {definition.title}")
        self.resize(1180, 820)

        for m in definition.fields:
            if m.overlay_rect is not None:
                self.fields.append(
                    OverlayField(
                        name=m.pdf_field,
                        page=m.page or 1,
                        rect=list(m.overlay_rect),
                        spec=OverlaySpec(
                            label=m.label,
                            source=m.compose or m.source or "",
                            kind=m.kind if m.kind in OVERLAY_KINDS else MappingKind.TEXT,
                            font_size=m.font_size or 10.0,
                        ),
                        original=m,
                    )
                )

        root = QVBoxLayout(self)
        root.addWidget(
            muted(
                "Diese PDF hat keine ausfüllbaren Formularfelder. Ziehen Sie mit der Maus ein Rechteck dort "
                "auf, wo später ein Wert stehen soll. Verschieben: Maus oder Pfeiltasten · Größe: Umschalt + "
                "Pfeiltasten oder die Eingabefelder rechts · Löschen: Entf-Taste."
            )
        )
        top = QHBoxLayout()
        top.addWidget(QLabel("Seite"))
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, self.page_count)
        self.page_spin.setAccessibleName("Seite wählen")
        self.page_spin.valueChanged.connect(self.show_page)
        top.addWidget(self.page_spin)
        top.addWidget(QLabel(f"von {self.page_count}"))
        top.addStretch(1)
        unit = QLabel("Einheit: PDF-Punkt (1 pt ≈ 0,35 mm), Ursprung oben links")
        unit.setToolTip(TOOLTIP_OVERLAY)
        top.addWidget(unit)
        root.addLayout(top)

        body = QHBoxLayout()
        self.scene = QGraphicsScene(self)
        self.view = PageView(self.scene)
        self.view.rect_drawn.connect(self._on_rect_drawn)
        self.view.nudge.connect(self._on_nudge)
        self.view.delete_requested.connect(self.delete_selected)
        self.scene.selectionChanged.connect(self._on_scene_selection)
        body.addWidget(self.view, 3)

        side = QVBoxLayout()
        side.addWidget(QLabel("Koordinatenfelder"))
        self.list = QListWidget()
        self.list.setAccessibleName("Liste der Koordinatenfelder")
        self.list.currentRowChanged.connect(self._on_list_row)
        side.addWidget(self.list, 1)
        form = QFormLayout()
        self.spins: dict[str, QDoubleSpinBox] = {}
        for key, label in (("x", "x (links)"), ("y", "y (oben)"), ("w", "Breite"), ("h", "Höhe")):
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 5000.0)
            spin.setDecimals(1)
            spin.setSuffix(" pt")
            spin.setAccessibleName(label)
            spin.valueChanged.connect(self._on_spin)
            form.addRow(label, spin)
            self.spins[key] = spin
        side.addLayout(form)
        self.props_button = QPushButton("Eigenschaften …")
        self.props_button.setToolTip("Beschriftung, Datenfeld, Art und Schriftgröße ändern.")
        self.props_button.clicked.connect(self.edit_selected)
        side.addWidget(self.props_button)
        self.delete_button = danger_button("Feld löschen", "Ausgewähltes Koordinatenfeld entfernen (Entf).")
        self.delete_button.clicked.connect(self.delete_selected)
        side.addWidget(self.delete_button)
        side_box = QWidget()
        side_box.setLayout(side)
        side_box.setMaximumWidth(330)
        body.addWidget(side_box, 1)
        root.addLayout(body, 1)

        buttons = QHBoxLayout()
        self.status_label = muted("")
        buttons.addWidget(self.status_label, 1)
        self.save_button = QPushButton("Als Entwurf speichern")
        self.save_button.clicked.connect(lambda: self.save(confirmed=False))
        buttons.addWidget(self.save_button)
        self.confirm_button = primary_button("Speichern && bestätigen")
        self.confirm_button.setAccessibleName("Speichern und bestätigen")
        self.confirm_button.clicked.connect(lambda: self.save(confirmed=True))
        buttons.addWidget(self.confirm_button)
        close = QPushButton("Schließen")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self.show_page(1)
        self._refresh_list()

    # ------------------------------------------------------------------
    def show_page(self, page: int) -> None:
        self.page = int(page)
        self.scene.clear()
        for f in self.fields:
            f.item = None
        try:
            image = render_page(self.pdf_path, self.page, dpi=RENDER_DPI)
            pixmap = QPixmap.fromImage(pil_to_qimage(image))
        except Exception as exc:  # noqa: BLE001 – Darstellung darf den Editor nicht beenden
            log.warning("Seite für Koordinateneditor nicht darstellbar (%s)", type(exc).__name__)
            pixmap = QPixmap(int(595 * SCALE), int(842 * SCALE))
            pixmap.fill(QColor("white"))
        bg = self.scene.addPixmap(pixmap)
        bg.setZValue(0)
        self.scene.setSceneRect(QRectF(pixmap.rect()))
        for f in self.fields:
            if f.page == self.page:
                self._add_item(f)
        self._refresh_list()

    def _add_item(self, ofield: OverlayField) -> OverlayRectItem:
        item = OverlayRectItem(ofield, self._on_item_moved)
        self.scene.addItem(item)
        ofield.item = item
        return item

    def _next_name(self) -> str:
        used = {f.name for f in self.fields} | {m.pdf_field for m in self.definition.fields}
        n = 1
        while f"Koordinatenfeld {n}" in used:
            n += 1
        return f"Koordinatenfeld {n}"

    # -- Anlegen --------------------------------------------------------
    def _on_rect_drawn(self, scene_rect: QRectF) -> None:
        rect = [
            scene_rect.left() / SCALE,
            scene_rect.top() / SCALE,
            scene_rect.right() / SCALE,
            scene_rect.bottom() / SCALE,
        ]
        self.add_field(rect)

    def add_field(self, rect_pt: list[float], spec: OverlaySpec | None = None) -> OverlayField | None:
        """Neues Feld aus einem Rechteck (PDF-Punkte) – fragt nach den Eigenschaften, falls nötig."""
        if spec is None:
            spec = ask_overlay_field(self)
            if spec is None:
                return None
        name = self._next_name()
        try:
            coordinate_field(name, self.page, rect_pt, spec.label, spec.source, spec.kind, spec.font_size)
        except ValueError as exc:
            dialogs.show_error(self, "Koordinatenfeld", friendly_message(str(exc)))
            return None
        ofield = OverlayField(name=name, page=self.page, rect=[float(v) for v in rect_pt], spec=spec)
        self.fields.append(ofield)
        item = self._add_item(ofield)
        self.scene.clearSelection()
        item.setSelected(True)
        self._refresh_list(select=ofield)
        return ofield

    # -- Auswahl --------------------------------------------------------
    def selected_field(self) -> OverlayField | None:
        for item in self.scene.selectedItems():
            if isinstance(item, OverlayRectItem):
                return item.ofield
        row = self.list.currentRow()
        page_fields = self._page_fields()
        if 0 <= row < len(page_fields):
            return page_fields[row]
        return None

    def _page_fields(self) -> list[OverlayField]:
        return [f for f in self.fields if f.page == self.page]

    def _refresh_list(self, select: OverlayField | None = None) -> None:
        self._syncing = True
        try:
            self.list.clear()
            for f in self._page_fields():
                item = QListWidgetItem(f"{f.spec.label} · {KIND_LABELS[f.spec.kind]}")
                item.setToolTip(field_label(f.spec.source))
                self.list.addItem(item)
            if select is not None and select in self._page_fields():
                self.list.setCurrentRow(self._page_fields().index(select))
        finally:
            self._syncing = False
        total = len(self.fields)
        self.status_label.setText(f"{total} Koordinatenfeld(er) insgesamt · Seite {self.page}")
        self._sync_spins()

    def _on_list_row(self, row: int) -> None:
        if self._syncing:
            return
        fields = self._page_fields()
        if 0 <= row < len(fields) and fields[row].item is not None:
            self._syncing = True
            try:
                self.scene.clearSelection()
                fields[row].item.setSelected(True)
            finally:
                self._syncing = False
        self._sync_spins()

    def _on_scene_selection(self) -> None:
        if self._syncing:
            return
        f = self.selected_field()
        if f is not None and f in self._page_fields():
            self._syncing = True
            try:
                self.list.setCurrentRow(self._page_fields().index(f))
            finally:
                self._syncing = False
        self._sync_spins()

    def _sync_spins(self) -> None:
        f = self.selected_field()
        has = f is not None
        for spin in self.spins.values():
            spin.setEnabled(has)
        self.props_button.setEnabled(has)
        self.delete_button.setEnabled(has)
        if f is None:
            return
        x0, y0, x1, y1 = f.rect
        values = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
        old = self._syncing
        self._syncing = True
        try:
            for key, spin in self.spins.items():
                spin.setValue(values[key])
        finally:
            self._syncing = old

    # -- Ändern ---------------------------------------------------------
    def _on_item_moved(self, ofield: OverlayField) -> None:
        if not self._syncing and ofield is self.selected_field():
            self._sync_spins()

    def _on_spin(self) -> None:
        if self._syncing:
            return
        f = self.selected_field()
        if f is None:
            return
        x = self.spins["x"].value()
        y = self.spins["y"].value()
        w = max(self.spins["w"].value(), MIN_SIZE_PT)
        h = max(self.spins["h"].value(), MIN_SIZE_PT)
        self.set_rect(f, [x, y, x + w, y + h])

    def set_rect(self, ofield: OverlayField, rect_pt: list[float]) -> None:
        ofield.rect = [float(v) for v in rect_pt]
        if ofield.item is not None:
            self._syncing = True
            try:
                ofield.item.sync_from_field()
            finally:
                self._syncing = False
        self._sync_spins()

    def _on_nudge(self, dx: float, dy: float, resize: bool) -> None:
        f = self.selected_field()
        if f is None:
            return
        x0, y0, x1, y1 = f.rect
        if resize:
            x1 = max(x0 + MIN_SIZE_PT, x1 + dx)
            y1 = max(y0 + MIN_SIZE_PT, y1 + dy)
        else:
            x0, x1 = x0 + dx, x1 + dx
            y0, y1 = y0 + dy, y1 + dy
            if x0 < 0 or y0 < 0:
                return
        self.set_rect(f, [x0, y0, x1, y1])

    def edit_selected(self) -> None:
        f = self.selected_field()
        if f is None:
            return
        spec = ask_overlay_field(self, f.spec)
        if spec is None:
            return
        f.spec = spec
        if f.item is not None:
            f.item.sync_from_field()
        self._refresh_list(select=f)

    def delete_selected(self) -> None:
        f = self.selected_field()
        if f is None:
            return
        if f.item is not None and f.item.scene() is not None:
            self.scene.removeItem(f.item)
        f.item = None
        self.fields.remove(f)
        self._refresh_list()

    # -- Speichern ------------------------------------------------------
    def build_definition(self) -> TemplateDefinition:
        others = [m for m in self.definition.fields if m.overlay_rect is None]
        overlay = [f.to_mapping() for f in self.fields]
        data = self.definition.model_dump(by_alias=True)
        data["fields"] = [m.model_dump(by_alias=True) for m in [*others, *overlay]]
        return TemplateDefinition.model_validate(data)

    def save(self, confirmed: bool) -> bool:
        if not self.fields:
            dialogs.show_error(self, "Koordinatenfelder", "Bitte zuerst mindestens ein Feld anlegen.")
            return False
        try:
            definition = self.build_definition()
        except ValueError as exc:
            dialogs.show_error(self, "Koordinatenfelder", friendly_message(str(exc)))
            return False
        if confirmed:
            errors, _perms, _insp = validate_definition_against_pdf(definition, self.pdf_path)
            if errors:
                dialogs.show_error(
                    self,
                    "Zuordnung kann noch nicht bestätigt werden",
                    "Die Prüfung gegen die PDF hat Probleme gefunden:\n" + bullet_list(errors),
                )
                return False
            if not dialogs.ask_confirm(
                self,
                "Koordinatenfelder bestätigen",
                "Haben Sie Lage und Datenfeld jedes Rechtecks geprüft?\n\n"
                "Tipp: Mit „Testbefüllung mit Musterdaten“ sehen Sie, wie die Werte auf der Seite stehen.",
                ok_text="Ja, bestätigen",
            ):
                return False
        try:
            self.service.registry.save_mapping(self.catalog_id, definition, confirmed=confirmed)
            report = self.service.registry.verify(self.catalog_id)
        except TemplateRegistryError as exc:
            dialogs.show_error(self, "Speichern nicht möglich", exc.user_message)
            return False
        self.definition = definition
        for f in self.fields:
            f.original = definition.mapping(f.name)
        dialogs.show_info(
            self,
            "Koordinatenfelder gespeichert",
            ("Die Zuordnung ist bestätigt." if confirmed else "Als Entwurf gespeichert.")
            + f"\n\nNeuer Status: {template_status_text(report.status)}"
            + (("\n" + bullet_list(report.reasons)) if report.reasons else ""),
        )
        self.saved.emit(self.catalog_id, confirmed)
        return True
