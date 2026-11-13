"""Auftragsdaten – bitte bewusst auswählen.

Auftragsdaten (``order.*``) stammen nie aus den Dokumenten. Ja/Nein- und Auswahlfelder
haben deshalb KEINE Vorauswahl; erst ein Klick speichert die Entscheidung (Status MANUAL).
Textfelder werden beim Verlassen des Feldes (oder mit Enter) übernommen; Eingabefehler
erscheinen direkt unter dem Feld.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Case
from ...core.fields import FIELD_REGISTRY, FieldType
from ...core.models import FieldStatus, FieldValue
from ...core.service import AppService
from ...letters.schema import PLACEHOLDER, LetterHint
from ...templates.schema import COMPOSE_PLACEHOLDER
from .. import theme
from ..widgets.common import muted, section_title
from .common import clear_layout, error_text
from .controller import CaseController
from .formatting import FORMAT_HINTS, edit_text, severity_color, severity_symbol

LEGAL_TEXT = "⚖ rechtlich relevant – bitte bewusst entscheiden"
WAIVE_REFUND_WARNING = "Achtung: Ein Kreuz bedeutet KEINE Rückerstattung"
HINT_LABEL = "prüfbedürftiger Hinweis"

# Empfängerangaben, die jeder Brief braucht (nicht „nicht relevant“ anbieten)
LETTER_REQUIRED_BASE = {
    "order.letter_date",
    "order.recipient_company",
    "order.recipient_street",
    "order.recipient_postal_code",
    "order.recipient_city",
}


def required_order_keys(tpl) -> set[str]:
    """Schlüssel, die die Vorlage zwingend (ggf. unter Bedingungen) braucht.

    ``tpl`` ist ein ``ActiveTemplate`` aus ``service.active_template``.
    """
    keys: set[str] = set()
    form = getattr(tpl, "form", None)
    letter = getattr(tpl, "letter", None)
    if form is not None:
        for m in form.fields:
            if m.required or m.legal_relevant:
                if m.source:
                    keys.add(m.source)
                if m.compose:
                    keys.update(k for k, opt in COMPOSE_PLACEHOLDER.findall(m.compose) if not opt)
            keys.update(c.field for c in m.conditions)
        for a in form.acknowledgments:
            keys.add(a.field)
    elif letter is not None:
        for text in letter.all_texts():
            for m in PLACEHOLDER.finditer(text):
                if m.group(2) and not m.group(3):
                    keys.add(m.group(2))
        for b in letter.subject_variants + letter.subject_lines + letter.blocks:
            keys.update(c.field for c in b.conditions)
        keys.update(d.field for d in letter.derived.values())
        keys.update(a.field for a in letter.acknowledgments)
        keys.update(a.field for a in letter.allowed_values)
        keys.update(LETTER_REQUIRED_BASE)
    return keys


def optional_keys(tpl, keys: list[str]) -> set[str]:
    required = required_order_keys(tpl)
    out = set()
    for k in keys:
        fdef = FIELD_REGISTRY.get(k)
        if fdef is None:
            continue
        if "optional" in fdef.label.lower() or k not in required:
            out.add(k)
    return out


@dataclass
class _Row:
    key: str
    frame: QFrame
    status: QLabel
    error: QLabel
    edit: QLineEdit | None = None
    group: QButtonGroup | None = None
    radios: dict[str, QRadioButton] = field(default_factory=dict)
    na_btn: QPushButton | None = None
    clear_btn: QPushButton | None = None


class OrderForm(QScrollArea):
    def __init__(
        self, service: AppService, controller: CaseController, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.controller = controller
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName("Auftragsdaten")
        inner = QWidget()
        self.layout_ = QVBoxLayout(inner)
        self.layout_.setContentsMargins(0, 0, 8, 0)
        self.layout_.setSpacing(8)
        self.setWidget(inner)
        self.rows: dict[str, _Row] = {}
        self._signature: tuple | None = None
        self.copy_btn: QPushButton | None = None
        self.hints_box: QVBoxLayout | None = None
        self.build(None, [], None, set())

    # ------------------------------------------------------------------
    def build(
        self,
        case: Case | None,
        keys: list[str],
        kind: str | None,
        optional: set[str],
        *,
        legal_hint: str | None = None,
    ) -> None:
        """Formular für die Auftragsfelder einer Vorlage aufbauen (nur bei Änderung neu)."""
        signature = (kind, tuple(keys), tuple(sorted(optional)), legal_hint)
        if signature == self._signature and self.rows:
            return
        self._signature = signature
        clear_layout(self.layout_)
        self.rows.clear()
        self.copy_btn = None
        self.hints_box = None

        self.layout_.addWidget(section_title("Auftragsdaten – bitte bewusst auswählen"))
        if kind is None:
            self.layout_.addWidget(
                muted("Bitte zuerst oben eine Vorlage wählen. Dann erscheinen hier die Angaben zum Auftrag.")
            )
            self.layout_.addStretch(1)
            return
        self.layout_.addWidget(
            muted(
                "Diese Angaben stammen nie aus den Dokumenten. Es gibt keine Vorauswahl – "
                "bitte jede Entscheidung selbst treffen."
            )
        )
        if legal_hint:
            legal = QLabel(f"⚖ {legal_hint}")
            legal.setWordWrap(True)
            legal.setStyleSheet(f"color: {theme.WARN};")
            self.layout_.addWidget(legal)
        if kind == "letter":
            self.copy_btn = QPushButton("Versicherer als Empfänger übernehmen")
            self.copy_btn.setToolTip(
                "Übernimmt den bestätigten Versicherer aus den Vertragsdaten als Empfänger. "
                "Die Anschrift bitte selbst ergänzen und prüfen."
            )
            self.copy_btn.setAccessibleName("Versicherer als Empfänger übernehmen")
            self.copy_btn.clicked.connect(self._copy_insurer)
            self.layout_.addWidget(self.copy_btn)
            hint_frame = QFrame()
            self.hints_box = QVBoxLayout(hint_frame)
            self.hints_box.setContentsMargins(0, 0, 0, 0)
            self.hints_box.setSpacing(4)
            self.layout_.addWidget(hint_frame)
        if not keys:
            self.layout_.addWidget(muted("Für diese Vorlage sind keine Auftragsdaten nötig."))
            self.layout_.addStretch(1)
            return
        groups: dict[str, list[str]] = {}
        for k in keys:
            fdef = FIELD_REGISTRY.get(k)
            if fdef is not None:
                groups.setdefault(fdef.group, []).append(k)
        for group, gkeys in groups.items():
            title = QLabel(group)
            title.setStyleSheet("font-weight: 700; font-size: 11pt; margin-top: 4px;")
            self.layout_.addWidget(title)
            for k in gkeys:
                row = self._make_row(k, k in optional)
                self.rows[k] = row
                self.layout_.addWidget(row.frame)
        self.layout_.addStretch(1)
        if case is not None:
            self.sync(case, [])

    def _make_row(self, key: str, optional: bool) -> _Row:
        fdef = FIELD_REGISTRY[key]
        frame = QFrame(objectName="Card")
        frame.setAccessibleName(fdef.label)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)
        head = QLabel(
            fdef.label + ("  (optional)" if optional and "optional" not in fdef.label.lower() else "")
        )
        head.setWordWrap(True)
        head.setStyleSheet("font-weight: 600;")
        lay.addWidget(head)
        if fdef.legal_relevant:
            legal = QLabel(LEGAL_TEXT)
            legal.setStyleSheet(f"color: {theme.WARN};")
            legal.setToolTip("Diese Angabe hat rechtliche Folgen. Die App setzt sie nie automatisch.")
            lay.addWidget(legal)
        if key == "order.waive_refund":
            warn = QLabel(f"⚠ {WAIVE_REFUND_WARNING}")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {theme.ERROR}; font-weight: 700;")
            warn.setAccessibleName(WAIVE_REFUND_WARNING)
            lay.addWidget(warn)
        if fdef.help:
            lay.addWidget(muted(fdef.help))

        row = _Row(key=key, frame=frame, status=QLabel(""), error=QLabel(""))
        line = QHBoxLayout()
        line.setSpacing(12)
        if fdef.dtype in (FieldType.BOOL, FieldType.CHOICE):
            group = QButtonGroup(frame)
            group.setExclusive(True)
            options = (
                {"true": "Ja", "false": "Nein"} if fdef.dtype == FieldType.BOOL else dict(fdef.choices or {})
            )
            short = all(len(t) <= 18 for t in options.values())
            choice_layout: QHBoxLayout | QVBoxLayout = (
                line if fdef.dtype == FieldType.BOOL or short else QVBoxLayout()
            )
            for value, label in options.items():
                rb = QRadioButton(label)
                rb.setAccessibleName(f"{fdef.label}: {label}")
                rb.setObjectName(f"choice_{value}")
                rb.setChecked(False)
                group.addButton(rb)
                rb.clicked.connect(lambda _=False, k=key, v=value: self._choose(k, v))
                choice_layout.addWidget(rb)
                row.radios[value] = rb
            if choice_layout is not line:
                lay.addLayout(choice_layout)
            row.group = group
            row.clear_btn = QPushButton("Auswahl löschen")
            row.clear_btn.setToolTip("Entscheidung zurücknehmen (Feld wieder offen)")
            row.clear_btn.setAccessibleName(f"Auswahl löschen: {fdef.label}")
            row.clear_btn.clicked.connect(lambda _=False, k=key: self._reset(k))
            line.addStretch(1)
            line.addWidget(row.clear_btn)
        else:
            placeholder, hint = FORMAT_HINTS.get(fdef.dtype, ("", ""))
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setToolTip(hint)
            edit.setAccessibleName(fdef.label)
            edit.setObjectName(f"edit_{key}")
            edit.editingFinished.connect(lambda k=key: self._edited(k))
            line.addWidget(edit, 1)
            row.edit = edit
        if optional:
            row.na_btn = QPushButton("nicht relevant")
            row.na_btn.setToolTip("Feld bewusst leer lassen (für diesen Auftrag nicht nötig)")
            row.na_btn.setAccessibleName(f"nicht relevant: {fdef.label}")
            row.na_btn.setStyleSheet("padding: 3px 10px;")
            row.na_btn.clicked.connect(lambda _=False, k=key: self._toggle_na(k))
            line.addWidget(row.na_btn)
        lay.addLayout(line)
        row.status.setWordWrap(True)
        row.error.setWordWrap(True)
        row.error.setStyleSheet(f"color: {theme.ERROR};")
        row.error.hide()
        lay.addWidget(row.status)
        lay.addWidget(row.error)
        return row

    # ------------------------------------------------------------------
    def sync(self, case: Case, hints: list[LetterHint]) -> None:
        """Anzeige an die gespeicherten Werte anpassen (ohne Widgets neu zu bauen)."""
        for key, row in self.rows.items():
            self._sync_row(row, case.get(key))
        if self.hints_box is not None:
            clear_layout(self.hints_box)
            for h in hints:
                color = severity_color(h.severity)
                src = (
                    f"<br><span style='color:{theme.TEXT_MUTED}'>Quelle: {html.escape(h.source)}</span>"
                    if h.source
                    else ""
                )
                lbl = QLabel(
                    f"<span style='color:{color}'>{severity_symbol(h.severity)} <b>{HINT_LABEL}:</b></span> "
                    f"{html.escape(h.text)}{src}"
                )
                lbl.setTextFormat(Qt.TextFormat.RichText)
                lbl.setWordWrap(True)
                lbl.setToolTip("Hinweis zur eigenen Prüfung – keine Rechtsberatung. Wird nicht gedruckt.")
                self.hints_box.addWidget(lbl)
        busy = self.controller.busy
        self.widget().setEnabled(not busy)

    def _sync_row(self, row: _Row, fv: FieldValue | None) -> None:
        fdef = FIELD_REGISTRY[row.key]
        status = fv.status if fv is not None else FieldStatus.MISSING
        norm = fv.normalized_value if fv is not None and status == FieldStatus.MANUAL else None
        if row.group is not None:
            row.group.setExclusive(False)
            for value, rb in row.radios.items():
                rb.blockSignals(True)
                rb.setChecked(norm == value)
                rb.blockSignals(False)
            row.group.setExclusive(True)
            if row.clear_btn is not None:
                row.clear_btn.setVisible(norm is not None)
        if row.edit is not None and not row.edit.hasFocus():
            row.edit.setText(edit_text(row.key, fv) if status == FieldStatus.MANUAL else "")
        if row.edit is not None:
            row.edit.setEnabled(status != FieldStatus.NOT_APPLICABLE)
        if row.na_btn is not None:
            row.na_btn.setText("rückgängig" if status == FieldStatus.NOT_APPLICABLE else "nicht relevant")
        lines: list[str] = []
        if status == FieldStatus.MANUAL:
            lines.append(f"<span style='color:{theme.OK}'>✔ übernommen</span>")
        elif status == FieldStatus.NOT_APPLICABLE:
            lines.append(
                f"<span style='color:{theme.TEXT_MUTED}'>– bewusst leer gelassen (nicht relevant)</span>"
            )
        elif fdef.legal_relevant and fdef.dtype in (FieldType.BOOL, FieldType.CHOICE):
            lines.append(f"<span style='color:{theme.ERROR}'>✖ noch nicht entschieden</span>")
        else:
            lines.append(f"<span style='color:{theme.TEXT_MUTED}'>○ noch leer</span>")
        for m in fv.validation if fv is not None else []:
            lines.append(
                f"<span style='color:{severity_color(m.severity)}'>{severity_symbol(m.severity)} "
                f"{html.escape(m.message)}</span>"
            )
        if fv is not None and fv.note and status == FieldStatus.MANUAL:
            lines.append(f"<span style='color:{theme.INFO}'>ℹ {html.escape(fv.note)}</span>")
        row.status.setTextFormat(Qt.TextFormat.RichText)
        row.status.setText("<br>".join(lines))

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------
    def _choose(self, key: str, value: str) -> None:
        fdef = FIELD_REGISTRY[key]
        val: str | bool = (value == "true") if fdef.dtype == FieldType.BOOL else value
        self._clear_error(key)
        self.controller.act(
            self, lambda c: self.service.set_choice(c, key, val), error_title="Auswahl nicht gespeichert"
        )

    def _reset(self, key: str) -> None:
        self._clear_error(key)
        self.controller.act(
            self, lambda c: self.service.reset_field(c, key), error_title="Nicht zurückgesetzt"
        )

    def _toggle_na(self, key: str) -> None:
        case = self.controller.case
        if case is None:
            return
        fv = case.get(key)
        self._clear_error(key)
        if fv is not None and fv.status == FieldStatus.NOT_APPLICABLE:
            self.controller.act(self, lambda c: self.service.reset_field(c, key))
        else:
            self.controller.act(self, lambda c: self.service.mark_not_applicable(c, key))

    def _edited(self, key: str) -> None:
        row = self.rows.get(key)
        case = self.controller.case
        if row is None or row.edit is None or case is None or self.controller.busy:
            return
        text = row.edit.text().strip()
        fv = case.get(key)
        current = edit_text(key, fv) if fv is not None and fv.status == FieldStatus.MANUAL else ""
        if text == current:
            return
        if not text:
            if fv is not None and fv.status == FieldStatus.MANUAL:
                self.controller.act(self, lambda c: self.service.reset_field(c, key))
            return
        try:
            new = self.service.edit_field(case, key, text)
        except Exception as exc:  # noqa: BLE001 – Eingabefehler direkt am Feld zeigen
            row.error.setText(f"✖ {error_text(exc)}")
            row.error.show()
            return
        self._clear_error(key)
        row.edit.setText(edit_text(key, new))
        self.controller.data_changed.emit()

    def _clear_error(self, key: str) -> None:
        row = self.rows.get(key)
        if row is not None:
            row.error.hide()
            row.error.setText("")

    def _copy_insurer(self) -> None:
        self.controller.act(
            self,
            lambda c: self.service.copy_insurer_to_recipient(c),
            error_title="Versicherer nicht übernommen",
        )

    # ------------------------------------------------------------------
    def error_text_of(self, key: str) -> str:
        row = self.rows.get(key)
        return row.error.text() if row is not None and not row.error.isHidden() else ""
