"""Feldzuordnung bearbeiten: PDF-Formularfelder -> Datenfelder des Vorgangs.

Eine Zeile je PDF-Feld (bzw. je Zuordnung). Spalten: Name, Typ, Seite,
Zustände, Art, Quelle, Pflicht, rechtlich relevant, Details, Hinweis.

Regeln
* Unterschriftsfelder sind fest „Unterschrift (bleibt leer)“ und nicht änderbar.
* Koordinatenfelder werden im Koordinateneditor bearbeitet (hier nur Anzeige).
* Mitgelieferte Zuordnungen (Swiss Life) sind zunächst nur lesbar. „Bearbeiten“
  legt beim Speichern eine eigene Kopie an – die Programmdateien bleiben unverändert.
* „Als Entwurf speichern“ speichert unbestätigt (Status „Zu prüfen“).
  „Zuordnung prüfen & bestätigen“ prüft gegen die PDF, fragt nach und bestätigt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.fields import FIELD_REGISTRY, FieldType
from ...pdf.inspector import FormInspection, PdfFieldInfo, option_label_near
from ...templates.registry import TemplateRegistryError, TemplateVersionInfo, validate_definition_against_pdf
from ...templates.schema import FieldMapping, FillPolicy, MappingKind, TemplateDefinition, TemplateStatus
from ...templates.studio import TemplateDraft
from .. import dialogs
from ..theme import ERROR, OK, TEXT_MUTED, WARN
from ..widgets.common import muted, primary_button
from .common import (
    DATE_PART_LABELS,
    FIELD_TYPE_LABELS,
    KIND_HELP,
    KIND_LABELS,
    KINDS_FOR_FIELD_TYPE,
    NO_SOURCE_TEXT,
    TOOLTIP_ACROFORM,
    TOOLTIP_OVERLAY,
    bullet_list,
    combo_source,
    field_label,
    friendly_errors,
    make_source_combo,
    template_status_text,
)

log = logging.getLogger(__name__)

COLUMNS = (
    "PDF-Feld",
    "Typ",
    "Seite",
    "Zustände",
    "Art",
    "Quelle (Datenfeld)",
    "Pflicht",
    "Rechtlich\nrelevant",
    "Details",
    "Stand",
)
COL_NAME, COL_TYPE, COL_PAGE, COL_STATES, COL_KIND, COL_SOURCE, COL_REQ, COL_LEGAL, COL_DETAIL, COL_HINT = (
    range(len(COLUMNS))
)
SIGNATURE_KINDS = (MappingKind.SIGNATURE, MappingKind.SIGNATURE_DATE)
NO_SOURCE_KINDS = (*SIGNATURE_KINDS, MappingKind.STATIC)


class MappingInputError(Exception):
    """Die Eingaben im Zuordnungseditor sind unvollständig oder ungültig."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.user_message = "Bitte die Zuordnung ergänzen:\n" + bullet_list(errors)


@dataclass
class MappingRow:
    """Zustand einer Tabellenzeile (Quelle der Wahrheit für die Widgets)."""

    pdf_field: str
    field_type: str  # PDF-Feldtyp, "overlay" oder "fehlt"
    pages: list[int]
    states: list[str]
    info: PdfFieldInfo | None
    original: FieldMapping | None
    kind: MappingKind | None
    source: str | None = None  # Feldschlüssel oder zusammengesetzter Ausdruck
    required: bool = False
    legal: bool = False
    date_part: str | None = None
    checked_when: str | None = None
    choice_map: dict[str, str] | None = None
    static_value: str | None = None
    locked: bool = False
    lock_reason: str = ""
    suggestion: str | None = None
    hint: str | None = None
    extra_sources: list[str] = field(default_factory=list)

    @property
    def allowed_kinds(self) -> tuple[MappingKind, ...]:
        if self.field_type == "overlay":
            return (self.kind,) if self.kind else ()
        if self.field_type == "fehlt":
            return (self.kind,) if self.kind else ()
        return KINDS_FOR_FIELD_TYPE.get(self.field_type, ())

    @property
    def assignable(self) -> bool:
        return bool(self.allowed_kinds) and not self.locked


@dataclass
class _RowWidgets:
    kind: QComboBox
    source: QComboBox
    required: QCheckBox
    legal: QCheckBox
    detail: QWidget | None = None


# ---------------------------------------------------------------------------
# Auswahlzustände einer Optionsgruppe zuordnen
# ---------------------------------------------------------------------------


def _choices_for(source: str | None) -> dict[str, str]:
    fdef = FIELD_REGISTRY.get(source or "")
    if fdef is None:
        return {}
    if fdef.choices:
        return dict(fdef.choices)
    if fdef.dtype == FieldType.BOOL:
        return {"true": "Ja", "false": "Nein"}
    return {}


class ChoiceMapDialog(QDialog):
    """Je Ein-Zustand der PDF eine Auswahl des Datenfelds wählen (-> ``choice_map``)."""

    def __init__(
        self,
        pdf_field: str,
        states: list[str],
        source: str | None,
        current: dict[str, str] | None,
        labels: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Optionsgruppe zuordnen")
        self.states = states
        self.choices = _choices_for(source)
        inverse = {state: value for value, state in (current or {}).items()}
        layout = QVBoxLayout(self)
        layout.addWidget(
            muted(
                f"PDF-Feld „{pdf_field}“: Welche Auswahl im Vorgang soll welches Kästchen ankreuzen? "
                f"Datenfeld: {field_label(source)}."
            )
        )
        form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        for state in states:
            combo = QComboBox()
            combo.addItem("– keine –", None)
            for value, label in self.choices.items():
                combo.addItem(label, value)
            if not self.choices:
                combo.setEditable(True)
            want = inverse.get(state)
            if want is not None:
                idx = combo.findData(want)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                elif combo.isEditable():
                    combo.setEditText(want)
            near = (labels or {}).get(state, "")
            caption = f"Zustand „{state}“" + (f" – {near}" if near else "")
            combo.setAccessibleName(caption)
            form.addRow(caption, combo)
            self.combos[state] = combo
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

    def result_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for state, combo in self.combos.items():
            value = combo.currentData()
            if value is None and combo.isEditable():
                text = combo.currentText().strip()
                value = text if text and text != "– keine –" else None
            if value is not None:
                out[str(value)] = state
        return out

    def _accept(self) -> None:
        values = []
        for combo in self.combos.values():
            v = combo.currentData() or (combo.currentText().strip() if combo.isEditable() else None)
            if v and v != "– keine –":
                values.append(v)
        if len(values) != len(set(values)):
            self.error.setText("✖ Jede Auswahl darf nur einem Zustand zugeordnet werden.")
            return
        if not values:
            self.error.setText("✖ Bitte mindestens einen Zustand zuordnen.")
            return
        self.accept()


def edit_choice_map(
    parent: QWidget | None,
    pdf_field: str,
    states: list[str],
    source: str | None,
    current: dict[str, str] | None,
    labels: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Dialog anzeigen; ``None`` bei Abbruch (in Tests per ``monkeypatch`` ersetzbar)."""
    dlg = ChoiceMapDialog(pdf_field, states, source, current, labels, parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.result_map()
    return None


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------


class MappingEditor(QWidget):
    """Tabelle zum Prüfen und Bearbeiten einer Feldzuordnung."""

    #: (Katalog-ID, bestätigt?) nach erfolgreichem Speichern
    saved = Signal(str, bool)

    def __init__(self, service, parent: QWidget | None = None) -> None:  # service: AppService
        super().__init__(parent)
        self.service = service
        self.catalog_id: str | None = None
        self.version: TemplateVersionInfo | None = None
        self.definition: TemplateDefinition | None = None
        self.inspection: FormInspection | None = None
        self.rows: list[MappingRow] = []
        self._widgets: list[_RowWidgets] = []
        self.read_only = False
        self._loading = False
        self._dirty = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.heading = QLabel("Feldzuordnung")
        self.heading.setStyleSheet("font-weight: 700;")
        layout.addWidget(self.heading)
        self.note = muted("")
        layout.addWidget(self.note)

        bar = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color: {TEXT_MUTED};")
        bar.addWidget(self.summary)
        bar.addStretch(1)
        self.suggest_button = QPushButton("Vorschläge übernehmen")
        self.suggest_button.setToolTip(
            "Übernimmt die automatisch erkannten, unsicheren Vorschläge in Felder ohne Zuordnung. "
            "Bitte danach jede Zeile prüfen."
        )
        self.suggest_button.clicked.connect(self.apply_suggestions)
        bar.addWidget(self.suggest_button)
        self.edit_button = QPushButton("Bearbeiten (eigene Kopie)")
        self.edit_button.setToolTip(
            "Mitgelieferte Zuordnungen bleiben unverändert. Beim Speichern entsteht eine eigene, "
            "importierte Kopie derselben Version."
        )
        self.edit_button.clicked.connect(lambda: self.set_read_only(False))
        bar.addWidget(self.edit_button)
        self.save_button = QPushButton("Als Entwurf speichern")
        self.save_button.setToolTip("Speichert die Zuordnung unbestätigt (Status „Zu prüfen“).")
        self.save_button.clicked.connect(self.save_draft)
        bar.addWidget(self.save_button)
        self.confirm_button = primary_button(
            "Zuordnung prüfen && bestätigen",
            "Prüft die Zuordnung gegen die PDF. Nach Ihrer Bestätigung ist die Vorlage „Aktiv“.",
        )
        self.confirm_button.setAccessibleName("Zuordnung prüfen und bestätigen")
        self.confirm_button.clicked.connect(self.confirm)
        bar.addWidget(self.confirm_button)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setAccessibleName("Feldzuordnung")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTabKeyNavigation(False)  # Tab springt durch die Auswahllisten/Kästchen der Zeilen
        header = self.table.horizontalHeader()
        for col, width in (
            (COL_NAME, 140),
            (COL_TYPE, 88),
            (COL_PAGE, 44),
            (COL_STATES, 76),
            (COL_KIND, 132),
            (COL_SOURCE, 199),
            (COL_REQ, 54),
            (COL_LEGAL, 74),
            (COL_DETAIL, 104),
        ):
            header.resizeSection(col, width)
        header.setSectionResizeMode(COL_HINT, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(40)
        tips = {
            COL_NAME: "Name des Felds in der PDF (vom Hersteller der Vorlage vergeben).",
            COL_TYPE: TOOLTIP_ACROFORM,
            COL_STATES: "Ein-Zustände von Ankreuzfeldern/Optionsgruppen laut PDF (z. B. „Ja“, „0“).",
            COL_KIND: "Wie der Wert eingetragen wird (Text, Datumsteil, Betrag, Ankreuzfeld …).",
            COL_SOURCE: "Aus welchem Datenfeld des Vorgangs der Wert stammt.",
            COL_REQ: "Pflicht: Ohne diesen Wert ist kein Export möglich.",
            COL_LEGAL: "Rechtlich relevant: nur mit ausdrücklicher Entscheidung der Nutzerin/des Nutzers.",
            COL_DETAIL: "Zusatzangaben je Art (Tag/Monat/Jahr, Ankreuz-Wert, Auswahlzustände, fester Text).",
            COL_HINT: "Stand der Zeile (Symbol + Wort). Der vollständige Hinweis steht unter der Tabelle.",
        }
        for col, tip in tips.items():
            item = self.table.horizontalHeaderItem(col)
            if item is not None:
                item.setToolTip(tip)
        self.table.currentCellChanged.connect(lambda row, *_: self._show_row_info(row))
        layout.addWidget(self.table, 1)
        self.row_info = muted("")
        self.row_info.setAccessibleName("Hinweis zur ausgewählten Zeile")
        layout.addWidget(self.row_info)

        self.placeholder = muted("")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.placeholder, 1)
        self.clear("Bitte links eine Vorlage auswählen.")

    # ------------------------------------------------------------------
    # Laden
    # ------------------------------------------------------------------
    def clear(self, message: str) -> None:
        self.catalog_id = None
        self.version = None
        self.definition = None
        self.inspection = None
        self.rows = []
        self._widgets = []
        self.table.setRowCount(0)
        self.table.setVisible(False)
        self.row_info.setText("")
        self.placeholder.setText(message)
        self.placeholder.setVisible(True)
        self.heading.setText("Feldzuordnung")
        self.note.setText("")
        self.summary.setText("")
        for btn in (self.suggest_button, self.edit_button, self.save_button, self.confirm_button):
            btn.setVisible(False)

    def load(
        self,
        catalog_id: str,
        version: TemplateVersionInfo,
        definition: TemplateDefinition,
        inspection: FormInspection | None,
        draft: TemplateDraft | None = None,
        status: TemplateStatus | None = None,
    ) -> None:
        self.catalog_id = catalog_id
        self.version = version
        self.definition = definition
        self.inspection = inspection
        self.rows = self._build_rows(definition, inspection, draft)
        shipped = version.source == "mitgeliefert"
        self.heading.setText(
            f"Feldzuordnung – Version {version.version} ({version.source})"
            + (f" · {template_status_text(status)}" if status else "")
        )
        self.placeholder.setVisible(False)
        self.table.setVisible(True)
        for btn in (self.suggest_button, self.save_button, self.confirm_button):
            btn.setVisible(True)
        self.edit_button.setVisible(shipped)
        self.set_read_only(shipped)
        self._dirty = False

    def set_read_only(self, read_only: bool) -> None:
        self.read_only = read_only
        shipped = self.version is not None and self.version.source == "mitgeliefert"
        if read_only:
            self.note.setText(
                "ⓘ Mitgelieferte, geprüfte Zuordnung – nur Ansicht. Zum Ändern „Bearbeiten (eigene Kopie)“ "
                "wählen. Die mitgelieferte Datei bleibt dabei unverändert."
            )
        elif shipped:
            self.note.setText(
                "✎ Bearbeitungsmodus: Beim Speichern entsteht eine eigene Kopie dieser Version (Quelle "
                "„importiert“). Die Originalvorlage wird nie verändert."
            )
        elif self.definition is not None and self.definition.mapping_confirmed:
            self.note.setText(
                "✔ Diese Zuordnung ist bestätigt. Nach einer Änderung muss sie erneut bestätigt werden."
            )
        else:
            self.note.setText(
                "◐ Noch nicht bestätigt: Bitte jede Zeile prüfen. Automatisch erkannte Zuordnungen und "
                "Vorschläge können falsch sein. Ankreuzfelder werden nie automatisch zugeordnet."
            )
        self.edit_button.setVisible(shipped and read_only)
        has_suggestions = any(r.suggestion for r in self.rows)
        self.suggest_button.setVisible(not read_only and has_suggestions)
        self.suggest_button.setEnabled(not read_only and has_suggestions)
        self.save_button.setEnabled(not read_only)
        self.confirm_button.setEnabled(not read_only)
        self.save_button.setVisible(not read_only)
        self.confirm_button.setVisible(not read_only)
        self._render_table()

    def _build_rows(
        self,
        definition: TemplateDefinition,
        inspection: FormInspection | None,
        draft: TemplateDraft | None,
    ) -> list[MappingRow]:
        rows: list[MappingRow] = []
        mapped: set[str] = set()
        suggestions = dict(draft.suggestions) if draft else {}
        hints = dict(draft.hints) if draft else {}
        for m in definition.fields:
            info = inspection.field(m.pdf_field) if inspection is not None else None
            mapped.add(m.pdf_field)
            if m.overlay_rect is not None:
                ftype = "overlay"
            elif info is not None:
                ftype = info.field_type
            else:
                ftype = "fehlt"
            row = MappingRow(
                pdf_field=m.pdf_field,
                field_type=ftype,
                pages=list(info.pages) if info else ([m.page] if m.page else []),
                states=list(dict.fromkeys(info.on_states)) if info else [],
                info=info,
                original=m,
                kind=m.kind,
                source=m.compose or m.source,
                required=m.required,
                legal=m.legal_relevant,
                date_part=m.date_part,
                checked_when=m.checked_when,
                choice_map=dict(m.choice_map) if m.choice_map else None,
                static_value=m.static_value,
            )
            if m.kind in SIGNATURE_KINDS:
                row.locked = True
                row.lock_reason = "Bleibt immer leer – Unterschrift erfolgt handschriftlich."
            elif ftype == "overlay":
                row.locked = True
                row.lock_reason = "Koordinatenfeld – Änderungen im Koordinateneditor."
            elif ftype == "fehlt":
                row.hint = "⚠ Feld fehlt in der PDF – bitte „nicht zuordnen“ wählen."
            if m.conditions:
                cond = ", ".join(field_label(c.field).split(" (")[0] for c in m.conditions)
                row.hint = (row.hint + " " if row.hint else "") + f"Nur relevant abhängig von: {cond}."
            rows.append(row)
        for info in inspection.fields if inspection is not None else []:
            if info.name in mapped:
                continue
            row = MappingRow(
                pdf_field=info.name,
                field_type=info.field_type,
                pages=list(info.pages),
                states=list(dict.fromkeys(info.on_states)),
                info=info,
                original=None,
                kind=None,
            )
            if info.field_type == "signature":
                row.kind = MappingKind.SIGNATURE
                row.locked = True
                row.lock_reason = "Unterschriftsfeld – bleibt immer leer."
            elif info.field_type in ("button", "listbox"):
                row.locked = True
                row.lock_reason = "Wird nicht befüllt (Schaltfläche/Liste)."
            sugg = suggestions.get(info.name)
            if sugg:
                row.suggestion = sugg
                row.extra_sources = [sugg] if "{" in sugg else []
                row.hint = f"Vorschlag: {field_label(sugg)} (unsicher – bitte prüfen)"
            elif info.name in hints:
                row.hint = hints[info.name]
            rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Darstellung
    # ------------------------------------------------------------------
    def _render_table(self) -> None:
        self._loading = True
        try:
            self.table.setRowCount(0)
            self._widgets = []
            self.table.setRowCount(len(self.rows))
            for i, row in enumerate(self.rows):
                self._render_row(i, row)
            self.table.resizeRowsToContents()
        finally:
            self._loading = False
        self._update_summary()

    def _item(self, text: str, tooltip: str = "") -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if tooltip:
            item.setToolTip(tooltip)
        return item

    def _render_row(self, i: int, row: MappingRow) -> None:
        self.table.setItem(i, COL_NAME, self._item(row.pdf_field, row.pdf_field))
        type_text = FIELD_TYPE_LABELS.get(row.field_type, row.field_type)
        if row.field_type == "fehlt":
            type_text = "⚠ fehlt in PDF"
        self.table.setItem(
            i, COL_TYPE, self._item(type_text, TOOLTIP_OVERLAY if row.field_type == "overlay" else "")
        )
        pages = ", ".join(str(p) for p in row.pages) or "–"
        self.table.setItem(i, COL_PAGE, self._item(pages))
        states = ", ".join(row.states) if row.states else "–"
        self.table.setItem(i, COL_STATES, self._item(states, states))
        editable = not self.read_only and row.assignable

        kind_combo = QComboBox()
        kind_combo.setAccessibleName(f"Art für {row.pdf_field}")
        if not row.locked:
            kind_combo.addItem(NO_SOURCE_TEXT, None)
        kinds = row.allowed_kinds if not row.locked else ((row.kind,) if row.kind else ())
        for kind in kinds:
            kind_combo.addItem(KIND_LABELS[kind], kind.value)
            kind_combo.setItemData(kind_combo.count() - 1, KIND_HELP[kind], Qt.ItemDataRole.ToolTipRole)
        if row.locked and not kinds:
            kind_combo.addItem("– wird nicht befüllt –", None)
        idx = kind_combo.findData(row.kind.value if row.kind else None)
        kind_combo.setCurrentIndex(max(idx, 0))
        kind_combo.setEnabled(editable)
        kind_combo.setToolTip(row.lock_reason or (KIND_HELP.get(row.kind, "") if row.kind else ""))
        kind_combo.currentIndexChanged.connect(lambda _=0, r=i: self._on_kind(r))
        self.table.setCellWidget(i, COL_KIND, kind_combo)

        source_combo = make_source_combo(row.source, row.extra_sources)
        source_combo.setAccessibleName(f"Quelle für {row.pdf_field}")
        source_combo.setEnabled(editable and row.kind not in NO_SOURCE_KINDS)
        source_combo.setToolTip(field_label(row.source) if row.source else "Datenfeld wählen")
        source_combo.currentIndexChanged.connect(lambda _=0, r=i: self._on_source(r))
        self.table.setCellWidget(i, COL_SOURCE, source_combo)

        req = QCheckBox()
        req.setChecked(row.required)
        req.setEnabled(editable and row.kind is not None and row.kind not in SIGNATURE_KINDS)
        req.setAccessibleName(f"Pflicht für {row.pdf_field}")
        req.setToolTip("Pflicht: Ohne diesen Wert ist kein Export möglich.")
        req.toggled.connect(lambda checked, r=i: self._on_flag(r, "required", checked))
        self.table.setCellWidget(i, COL_REQ, _centered(req))

        legal = QCheckBox()
        legal.setChecked(row.legal)
        legal.setEnabled(editable and row.kind is not None and row.kind not in SIGNATURE_KINDS)
        legal.setAccessibleName(f"Rechtlich relevant für {row.pdf_field}")
        legal.setToolTip(
            "Rechtlich relevante Angaben werden nie automatisch gesetzt – der Mensch entscheidet ausdrücklich."
        )
        legal.toggled.connect(lambda checked, r=i: self._on_flag(r, "legal", checked))
        self.table.setCellWidget(i, COL_LEGAL, _centered(legal))

        widgets = _RowWidgets(kind=kind_combo, source=source_combo, required=req, legal=legal)
        if i < len(self._widgets):
            self._widgets[i] = widgets
        else:
            self._widgets.append(widgets)
        self._render_detail(i)
        self._render_hint(i)

    def _render_detail(self, i: int) -> None:
        row = self.rows[i]
        editable = not self.read_only and row.assignable
        widget: QWidget
        if row.kind == MappingKind.DATE_PART:
            combo = QComboBox()
            combo.addItem("– bitte wählen –", None)
            for part, label in DATE_PART_LABELS.items():
                combo.addItem(label, part)
            combo.setCurrentIndex(max(combo.findData(row.date_part), 0))
            combo.setAccessibleName(f"Datumsteil für {row.pdf_field}")
            combo.setToolTip("Welcher Teil des Datums in dieses Feld kommt.")
            combo.currentIndexChanged.connect(lambda _=0, r=i, c=combo: self._set_detail(r, "date_part", c))
            widget = combo
        elif row.kind == MappingKind.CHECKBOX:
            combo = QComboBox()
            choices = _choices_for(row.source if row.source and "{" not in row.source else None)
            if choices:
                for value, label in choices.items():
                    combo.addItem(f"angekreuzt bei: {label}", value)
                current = row.checked_when or ("true" if "true" in choices else next(iter(choices)))
                idx = combo.findData(current)
                if idx < 0:
                    combo.addItem(f"angekreuzt bei: {current}", current)
                    idx = combo.count() - 1
                combo.setCurrentIndex(idx)
                row.checked_when = combo.currentData()
                combo.currentIndexChanged.connect(
                    lambda _=0, r=i, c=combo: self._set_detail(r, "checked_when", c)
                )
            else:
                combo.setEditable(True)
                combo.setEditText(row.checked_when or "true")
                combo.editTextChanged.connect(lambda text, r=i: self._set_checked_text(r, text))
            combo.setAccessibleName(f"Ankreuzen bei Wert für {row.pdf_field}")
            combo.setToolTip("Das Kästchen wird angekreuzt, wenn das Datenfeld diesen Wert hat.")
            widget = combo
        elif row.kind == MappingKind.RADIO:
            mapped = len(row.choice_map or {})
            btn = QPushButton(f"Zustände zuordnen … ({mapped}/{len(row.states)})")
            btn.setAccessibleName(f"Auswahlzustände für {row.pdf_field} zuordnen")
            btn.setToolTip("Je Auswahlmöglichkeit festlegen, welches Kästchen der PDF angekreuzt wird.")
            btn.clicked.connect(lambda _=False, r=i: self.edit_radio(r))
            widget = btn
        elif row.kind == MappingKind.STATIC:
            edit = QLineEdit(row.static_value or "")
            edit.setPlaceholderText("fester Text (ohne Personendaten)")
            edit.setAccessibleName(f"Fester Text für {row.pdf_field}")
            edit.textChanged.connect(lambda text, r=i: self._set_static(r, text))
            widget = edit
        elif row.field_type == "overlay" and row.original is not None and row.original.overlay_rect:
            x0, y0, x1, y1 = row.original.overlay_rect
            widget = QLabel(f"S. {row.original.page}: {x0:.0f}/{y0:.0f} pt, {x1 - x0:.0f}×{y1 - y0:.0f}")
            widget.setToolTip(TOOLTIP_OVERLAY)
        else:
            widget = QLabel("–")
            widget.setStyleSheet(f"color: {TEXT_MUTED};")
        widget.setEnabled(editable or isinstance(widget, QLabel))
        self.table.setCellWidget(i, COL_DETAIL, widget)
        self._widgets[i].detail = widget

    def row_state(self, row: MappingRow) -> tuple[str, str, str]:
        """-> (Kurztext mit Symbol, Farbe, ausführlicher Hinweis)."""
        detail = row.hint or row.lock_reason or ""
        problem = self._row_problem(row) if not row.locked else ""
        if row.field_type == "fehlt" and row.kind is not None:
            return "⚠ fehlt", ERROR, detail
        if row.locked:
            return "■ fest", TEXT_MUTED, detail
        if row.kind is not None and problem:
            return "✖ unvollständig", ERROR, f"{problem}" + (f" · {detail}" if detail else "")
        if row.kind is not None:
            return "✔ zugeordnet", OK, detail
        if row.suggestion:
            return "◐ Vorschlag", WARN, detail
        return "○ offen", TEXT_MUTED, detail or "Ohne Zuordnung – das Feld bleibt leer."

    def _render_hint(self, i: int) -> None:
        short, color, detail = self.row_state(self.rows[i])
        item = self._item(short, detail or short)
        item.setForeground(_brush(color))
        self.table.setItem(i, COL_HINT, item)

    def _update_summary(self) -> None:
        total = len(self.rows)
        assigned = sum(1 for r in self.rows if r.kind is not None)
        open_ = sum(1 for r in self.rows if r.kind is None and r.assignable)
        problems = sum(1 for r in self.rows if r.kind is not None and not r.locked and self._row_problem(r))
        text = f"{total} Felder · {assigned} zugeordnet · {open_} offen"
        if problems:
            text += f" · ✖ {problems} unvollständig"
        self.summary.setText(text)
        self.summary.setAccessibleName(text)

    @staticmethod
    def _row_problem(row: MappingRow) -> str:
        if row.kind is None or row.kind in SIGNATURE_KINDS:
            return ""
        if row.kind == MappingKind.STATIC:
            return "" if (row.static_value or "").strip() else "fester Text fehlt"
        if not row.source:
            return "Quelle fehlt"
        if row.kind == MappingKind.DATE_PART and row.date_part not in DATE_PART_LABELS:
            return "Tag/Monat/Jahr wählen"
        if row.kind == MappingKind.RADIO and not row.choice_map:
            return "Zustände zuordnen"
        return ""

    # ------------------------------------------------------------------
    # Eingaben
    # ------------------------------------------------------------------
    def _on_kind(self, i: int) -> None:
        if self._loading:
            return
        row = self.rows[i]
        value = self._widgets[i].kind.currentData()
        row.kind = MappingKind(value) if value else None
        if row.kind in SIGNATURE_KINDS:
            row.source = None
        self._dirty = True
        self._refresh_row(i)

    def _on_source(self, i: int) -> None:
        if self._loading:
            return
        row = self.rows[i]
        row.source = combo_source(self._widgets[i].source)
        if row.source and row.kind is None:
            defaults = row.allowed_kinds
            if defaults:
                row.kind = defaults[0]
                fdef = FIELD_REGISTRY.get(row.source)
                if fdef is not None:
                    row.legal = fdef.legal_relevant
                    if row.kind == MappingKind.TEXT and fdef.dtype == FieldType.AMOUNT:
                        row.kind = MappingKind.AMOUNT
        elif row.source is None and row.original is None and row.kind not in NO_SOURCE_KINDS:
            row.kind = None
        if row.kind == MappingKind.CHECKBOX:
            row.checked_when = None
        if row.kind == MappingKind.RADIO:
            row.choice_map = None
        self._dirty = True
        self._refresh_row(i)

    def _on_flag(self, i: int, name: str, checked: bool) -> None:
        if self._loading:
            return
        setattr(self.rows[i], name, bool(checked))
        self._dirty = True
        self._render_hint(i)

    def _set_detail(self, i: int, attr: str, combo: QComboBox) -> None:
        if self._loading:
            return
        setattr(self.rows[i], attr, combo.currentData())
        self._dirty = True
        self._render_hint(i)
        self._update_summary()

    def _set_checked_text(self, i: int, text: str) -> None:
        if self._loading:
            return
        self.rows[i].checked_when = text.strip() or None
        self._dirty = True

    def _set_static(self, i: int, text: str) -> None:
        if self._loading:
            return
        self.rows[i].static_value = text
        self._dirty = True
        self._render_hint(i)
        self._update_summary()

    def _refresh_row(self, i: int) -> None:
        self._loading = True
        try:
            self._render_row(i, self.rows[i])
            self.table.resizeRowToContents(i)
        finally:
            self._loading = False
        self._update_summary()
        self._show_row_info(i)

    def _show_row_info(self, i: int) -> None:
        """Vollständiger Hinweis zur Zeile unter der Tabelle (die Spalte „Hinweis“ ist oft schmal)."""
        if not 0 <= i < len(self.rows):
            self.row_info.setText("")
            return
        row = self.rows[i]
        type_text = FIELD_TYPE_LABELS.get(row.field_type, row.field_type)
        pages = ", ".join(str(p) for p in row.pages) or "–"
        parts = [f"„{row.pdf_field}“ ({type_text}, Seite {pages})"]
        if row.kind is not None:
            parts.append(
                f"{KIND_LABELS[row.kind]} ← {field_label(row.source)}"
                if row.source
                else KIND_LABELS[row.kind]
            )
        problem = self._row_problem(row) if not row.locked else ""
        if problem:
            parts.append(f"✖ {problem}")
        for extra in (row.hint, row.lock_reason):
            if extra:
                parts.append(extra)
        self.row_info.setText("ⓘ " + " · ".join(parts))

    def edit_radio(self, i: int) -> None:
        row = self.rows[i]
        source = row.source if row.source and "{" not in row.source else None
        if source is None:
            dialogs.show_info(self, "Optionsgruppe", "Bitte zuerst eine Quelle (Datenfeld) wählen.")
            return
        labels: dict[str, str] = {}
        if self.version is not None and row.info is not None:
            for state in row.states:
                try:
                    labels[state] = option_label_near(Path(self.version.pdf_path), row.pdf_field, state)[:60]
                except Exception:  # noqa: BLE001 – Beschriftung ist nur eine Hilfe
                    labels[state] = ""
        result = edit_choice_map(self, row.pdf_field, row.states, source, row.choice_map, labels)
        if result is None:
            return
        row.choice_map = result or None
        self._dirty = True
        self._refresh_row(i)

    def apply_suggestions(self) -> int:
        """Unsichere Vorschläge in Zeilen ohne Zuordnung übernehmen -> Anzahl."""
        if self.read_only:
            return 0
        count = 0
        for row in self.rows:
            if row.kind is None and row.suggestion and row.assignable:
                row.source = row.suggestion
                row.kind = row.allowed_kinds[0] if row.allowed_kinds else None
                fdef = FIELD_REGISTRY.get(row.suggestion)
                row.legal = bool(fdef and fdef.legal_relevant)
                row.hint = f"Vorschlag übernommen: {field_label(row.suggestion)} – bitte prüfen"
                count += 1
        if count:
            self._dirty = True
            self._render_table()
        return count

    # ------------------------------------------------------------------
    # Hilfen für Tests/Studio
    # ------------------------------------------------------------------
    def row_index(self, pdf_field: str) -> int:
        for i, row in enumerate(self.rows):
            if row.pdf_field == pdf_field:
                return i
        raise KeyError(pdf_field)

    def widgets_for(self, pdf_field: str) -> _RowWidgets:
        return self._widgets[self.row_index(pdf_field)]

    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        """Ungespeicherte Änderungen verwerfen (z. B. weil eine neue Version geladen wird)."""
        self._dirty = False

    # ------------------------------------------------------------------
    # Ergebnis
    # ------------------------------------------------------------------
    def _to_mapping(self, row: MappingRow) -> FieldMapping | None:
        if row.kind is None:
            return None
        if row.locked and row.original is not None:
            return row.original
        base: dict = row.original.model_dump(by_alias=True) if row.original is not None else {}
        page = row.pages[0] if row.pages else None
        if not base:
            base = {
                "pdf_field": row.pdf_field,
                "label": row.pdf_field,
                "page": page,
                "max_len": row.info.max_len if row.info else None,
            }
        kind = row.kind
        source_changed = row.original is None or (row.original.compose or row.original.source) != row.source
        if source_changed:
            fdef = FIELD_REGISTRY.get(row.source or "")
            base["label"] = fdef.label if fdef else row.pdf_field
            if kind == MappingKind.DATE_PART and row.date_part:
                base["label"] += f" ({DATE_PART_LABELS[row.date_part]})"
        base.update(
            kind=kind.value,
            required=row.required,
            legal_relevant=row.legal,
            source=None,
            compose=None,
            date_part=row.date_part if kind == MappingKind.DATE_PART else None,
            checked_when=(row.checked_when or "true") if kind == MappingKind.CHECKBOX else None,
            choice_map=row.choice_map if kind == MappingKind.RADIO else None,
            static_value=row.static_value if kind == MappingKind.STATIC else None,
        )
        if kind in SIGNATURE_KINDS:
            base["fill_policy"] = FillPolicy.NEVER.value
            base["required"] = False
            base["legal_relevant"] = False
            if row.original is None:
                base["label"] = (
                    "Unterschrift (bleibt leer)" if kind == MappingKind.SIGNATURE else "Datum Unterschrift"
                )
        else:
            if base.get("fill_policy") == FillPolicy.NEVER.value:
                base["fill_policy"] = FillPolicy.AUTO.value
            if kind != MappingKind.STATIC and row.source:
                if "{" in row.source:
                    base["compose"] = row.source
                else:
                    base["source"] = row.source
        return FieldMapping.model_validate(base)

    def build_definition(self) -> TemplateDefinition:
        """Zuordnung aus den Zeilen erzeugen (wirft ``MappingInputError`` mit deutschen Hinweisen)."""
        if self.definition is None:
            raise MappingInputError(["Keine Vorlage geladen."])
        errors: list[str] = []
        mappings: list[FieldMapping] = []
        for row in self.rows:
            problem = self._row_problem(row) if not row.locked else ""
            if problem:
                errors.append(f"„{row.pdf_field}“: {problem}.")
                continue
            try:
                mapping = self._to_mapping(row)
            except ValidationError as exc:
                errors.extend(friendly_errors(exc, row.pdf_field))
                continue
            if mapping is not None:
                mappings.append(mapping)
        if errors:
            raise MappingInputError(errors)
        ignored = list(self.definition.ignored_fields)
        for row in self.rows:
            if row.kind is None and row.field_type in ("button", "listbox") and row.pdf_field not in ignored:
                ignored.append(row.pdf_field)
        data = self.definition.model_dump(by_alias=True)
        data["fields"] = [m.model_dump(by_alias=True) for m in mappings]
        data["ignored_fields"] = ignored
        try:
            return TemplateDefinition.model_validate(data)
        except ValidationError as exc:
            raise MappingInputError(friendly_errors(exc)) from exc

    def save_draft(self) -> bool:
        return self._save(confirmed=False)

    def confirm(self) -> bool:
        return self._save(confirmed=True)

    def _save(self, confirmed: bool) -> bool:
        if self.read_only or self.catalog_id is None or self.version is None:
            return False
        try:
            definition = self.build_definition()
        except MappingInputError as exc:
            dialogs.show_error(self, "Zuordnung unvollständig", exc.user_message)
            return False
        if confirmed:
            errors, _perms, _insp = validate_definition_against_pdf(definition, Path(self.version.pdf_path))
            if errors:
                dialogs.show_error(
                    self,
                    "Zuordnung kann noch nicht bestätigt werden",
                    "Die Prüfung gegen die PDF hat Probleme gefunden:\n"
                    + bullet_list(errors)
                    + "\n\nSie können die Zuordnung als Entwurf speichern und später weiter bearbeiten.",
                )
                return False
            ok = dialogs.ask_confirm(
                self,
                "Zuordnung bestätigen",
                "Haben Sie jede Zeile geprüft?\n\n"
                "Mit der Bestätigung wird die Vorlage „Aktiv“ und kann in Vorgängen verwendet werden. "
                "Ins PDF gelangen später nur Werte, die im Vorgang geprüft und freigegeben wurden.",
                ok_text="Ja, Zuordnung bestätigen",
            )
            if not ok:
                return False
        try:
            self.service.registry.save_mapping(self.catalog_id, definition, confirmed=confirmed)
            report = self.service.registry.verify(self.catalog_id)
        except TemplateRegistryError as exc:
            dialogs.show_error(self, "Speichern nicht möglich", exc.user_message)
            return False
        log.info("Feldzuordnung im Studio gespeichert (bestätigt=%s)", confirmed)
        self._dirty = False
        status = template_status_text(report.status)
        reasons = ("\n" + bullet_list(report.reasons)) if report.reasons else ""
        dialogs.show_info(
            self,
            "Zuordnung gespeichert",
            ("Die Zuordnung ist bestätigt." if confirmed else "Die Zuordnung wurde als Entwurf gespeichert.")
            + f"\n\nNeuer Status: {status}{reasons}",
        )
        self.saved.emit(self.catalog_id, confirmed)
        return True


def _centered(widget: QWidget) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignCenter)
    return box


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(color))
