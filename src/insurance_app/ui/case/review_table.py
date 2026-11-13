"""Prüftabelle der Vertragsdaten: Feld | Erkannter Wert | Quelle / Seite | Status | Aktionen.

Mit gewählter Vorlage stehen die benötigten Felder oben („Für die gewählte Vorlage benötigt“);
weitere erkannte Angaben sind eingeklappt und müssen nicht bestätigt werden.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Case
from ...core.fields import FIELD_REGISTRY, FieldCategory
from ...core.models import FieldStatus, FieldValue
from .. import theme
from ..widgets.common import primary_button
from .formatting import (
    CONFIDENCE_TOOLTIP,
    confidence_text,
    currency_of,
    display_value,
    severity_symbol,
    source_text,
)

COL_FIELD, COL_VALUE, COL_SOURCE, COL_STATUS, COL_ACTIONS = range(5)
COLUMNS = ("Feld", "Erkannter Wert", "Quelle / Seite", "Status", "Aktionen")

SECTION_REQUIRED = "required"
SECTION_OTHER = "other"
SECTION_ALL = "all"

SECTION_TITLES = {
    SECTION_REQUIRED: "Für die gewählte Vorlage benötigt",
    SECTION_OTHER: "Weitere erkannte Angaben (nicht erforderlich)",
    SECTION_ALL: "Erkannte Angaben",
}

ACTION_CONFIRM = "confirm"
ACTION_EDIT = "edit"
ACTION_UNCLEAR = "unclear"
ACTION_NOT_APPLICABLE = "not_applicable"
ACTION_COMPARE = "compare"
ACTION_RESET = "reset"

ACTION_TOOLTIPS = {
    ACTION_CONFIRM: "Wert ist richtig – bestätigen",
    ACTION_EDIT: "Wert selbst eintragen oder korrigieren",
    ACTION_UNCLEAR: "Wert ist nicht sicher lesbar oder nicht eindeutig – als unklar markieren",
    ACTION_NOT_APPLICABLE: "Angabe wird für diesen Vorgang nicht gebraucht – bewusst leer lassen",
    ACTION_COMPARE: "Gefundene Werte nebeneinander vergleichen und den richtigen auswählen",
    ACTION_RESET: "Eigene Entscheidung verwerfen und den erkannten Stand wiederherstellen",
}

ACTION_LABELS = {
    ACTION_CONFIRM: "Bestätigen",
    ACTION_EDIT: "Bearbeiten …",
    ACTION_UNCLEAR: "Als unklar markieren",
    ACTION_NOT_APPLICABLE: "Nicht relevant",
    ACTION_COMPARE: "Werte vergleichen …",
    ACTION_RESET: "Zurücksetzen (Erkennung wiederherstellen)",
}

KEY_ROLE = Qt.ItemDataRole.UserRole
SECTION_ROLE = Qt.ItemDataRole.UserRole + 1
COUNT_ROLE = Qt.ItemDataRole.UserRole + 2


@dataclass
class RowSpec:
    key: str
    fv: FieldValue | None
    section: str


def rows_for(case: Case, relevant: list[str] | None) -> list[RowSpec]:
    """Zeilen in Anzeigereihenfolge (Feldregister-Reihenfolge innerhalb der Abschnitte)."""
    contract_keys = [k for k, f in FIELD_REGISTRY.items() if f.category == FieldCategory.CONTRACT]
    rows: list[RowSpec] = []
    if relevant is not None:
        rel = [k for k in contract_keys if k in set(relevant)]
        for k in rel:
            rows.append(RowSpec(k, case.get(k), SECTION_REQUIRED))
        for k in contract_keys:
            fv = case.get(k)
            if k not in rel and fv is not None and fv.status != FieldStatus.MISSING:
                rows.append(RowSpec(k, fv, SECTION_OTHER))
    else:
        for k in contract_keys:
            fv = case.get(k)
            if fv is not None and fv.status != FieldStatus.MISSING:
                rows.append(RowSpec(k, fv, SECTION_ALL))
    return rows


def available_actions(fv: FieldValue | None) -> list[str]:
    status = fv.status if fv is not None else FieldStatus.MISSING
    has_value = fv is not None and bool(fv.normalized_value or fv.value)
    actions: list[str] = []
    if status == FieldStatus.CONFLICT:
        actions.append(ACTION_COMPARE)
    elif status in (FieldStatus.EXTRACTED, FieldStatus.UNCLEAR) and has_value:
        actions.append(ACTION_CONFIRM)
    actions.append(ACTION_EDIT)
    if status not in (FieldStatus.UNCLEAR, FieldStatus.MISSING, FieldStatus.NOT_APPLICABLE):
        actions.append(ACTION_UNCLEAR)
    if status != FieldStatus.NOT_APPLICABLE:
        actions.append(ACTION_NOT_APPLICABLE)
    if (
        fv is not None
        and fv.candidates
        and status
        in (
            FieldStatus.CONFIRMED,
            FieldStatus.MANUAL,
            FieldStatus.UNCLEAR,
            FieldStatus.NOT_APPLICABLE,
        )
    ):
        actions.append(ACTION_RESET)
    return actions


def main_action(actions: list[str]) -> str:
    for a in (ACTION_COMPARE, ACTION_CONFIRM, ACTION_EDIT):
        if a in actions:
            return a
    return actions[0]


def main_action_text(action: str, fv: FieldValue | None) -> str:
    if action == ACTION_EDIT:
        status = fv.status if fv is not None else FieldStatus.MISSING
        if status in (FieldStatus.CONFIRMED, FieldStatus.MANUAL):
            return "Ändern …"
        if status in (FieldStatus.MISSING, FieldStatus.NOT_APPLICABLE) or not (
            fv and (fv.value or fv.normalized_value)
        ):
            return "Eintragen …"
        return "Bearbeiten …"
    return ACTION_LABELS[action]


class ReviewTable(QWidget):
    """Baum mit Abschnitten; meldet Aktionen über ``action_requested(action, key)``."""

    action_requested = Signal(str, str)
    current_key_changed = Signal(str)  # "" = keine Feldzeile
    confirm_selected = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.tree = QTreeWidget()
        self.tree.setAccessibleName("Prüftabelle der erkannten Vertragsdaten")
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setRootIsDecorated(False)  # Abschnitte zeigen ▾/▸ selbst (spart Platz)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.itemSelectionChanged.connect(self._update_bulk)
        self.tree.itemActivated.connect(self._activated)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemExpanded.connect(self._expanded_changed)
        self.tree.itemCollapsed.connect(self._expanded_changed)
        hdr = self.tree.header()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.setIndentation(12)
        self.tree.setColumnWidth(COL_FIELD, 130)
        self.tree.setColumnWidth(COL_VALUE, 150)
        self.tree.setColumnWidth(COL_SOURCE, 95)
        self.tree.setColumnWidth(COL_STATUS, 165)
        hdr.setMinimumSectionSize(60)
        hdr.sectionResized.connect(lambda *_: self._schedule_heights())
        self._heights_pending = False
        self.tree.headerItem().setToolTip(COL_STATUS, "Prüfstatus und " + CONFIDENCE_TOOLTIP)
        layout.addWidget(self.tree, 1)

        bar = QHBoxLayout()
        self.select_open_btn = QPushButton("Erkannte auswählen")
        self.select_open_btn.setToolTip(
            "Alle erkannten, noch nicht bestätigten Werte im oberen Abschnitt auswählen (zum gemeinsamen Bestätigen)"
        )
        self.select_open_btn.clicked.connect(self.select_open_rows)
        self.bulk_btn = primary_button(
            "Ausgewählte bestätigen",
            "Alle ausgewählten Zeilen bestätigen. Konflikte und Felder ohne Wert werden übersprungen.",
        )
        self.bulk_btn.setAccessibleName("Ausgewählte bestätigen")
        self.bulk_btn.clicked.connect(self._bulk_confirm)
        self.bulk_info = QLabel("")
        self.bulk_info.setWordWrap(True)
        bar.addWidget(self.select_open_btn)
        bar.addWidget(self.bulk_btn)
        bar.addWidget(self.bulk_info, 1)
        layout.addLayout(bar)

        self._items: dict[str, QTreeWidgetItem] = {}
        self._row_actions: dict[str, list[str]] = {}
        self._sections: dict[str, QTreeWidgetItem] = {}
        self._other_expanded = False
        self._enabled = True
        self._update_bulk()

    # ------------------------------------------------------------------
    def set_rows(self, case: Case, relevant: list[str] | None) -> None:
        current = self.current_key()
        selected = set(self.selected_keys())
        scroll = self.tree.verticalScrollBar().value()
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items.clear()
        self._row_actions.clear()
        self._sections.clear()
        currency = currency_of(case)
        rows = rows_for(case, relevant)
        for section in (SECTION_REQUIRED, SECTION_OTHER, SECTION_ALL):
            specs = [r for r in rows if r.section == section]
            if section == SECTION_ALL and relevant is not None:
                continue
            if section != SECTION_ALL and relevant is None:
                continue
            head = QTreeWidgetItem(self.tree)
            head.setData(0, SECTION_ROLE, section)
            head.setData(0, COUNT_ROLE, len(specs))
            head.setFirstColumnSpanned(True)
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = QFont(head.font(0))
            font.setBold(True)
            head.setFont(0, font)
            head.setBackground(0, QColor("#EEF2F7"))
            if section == SECTION_OTHER:
                head.setToolTip(
                    0, "Diese Angaben braucht die gewählte Vorlage nicht. Sie müssen nicht bestätigt werden."
                )
            self._sections[section] = head
            for spec in specs:
                self._add_row(head, spec, currency, case)
            expanded = self._other_expanded if section == SECTION_OTHER else True
            head.setExpanded(expanded)
            self._update_section_text(head)
        self.tree.blockSignals(False)
        # Aktions-Schaltflächen erst nach dem Aufklappen setzen (sonst unsichtbar)
        for key, item in self._items.items():
            fv = case.get(key)
            self.tree.setItemWidget(item, COL_ACTIONS, self._actions_widget(key, fv))
        if current and current in self._items:
            self.tree.setCurrentItem(self._items[current])
        for key in selected:
            if key in self._items:
                self._items[key].setSelected(True)
        self._relayout_heights()
        self.tree.verticalScrollBar().setValue(scroll)
        self._update_bulk()

    def _schedule_heights(self) -> None:
        if not self._heights_pending:
            self._heights_pending = True
            QTimer.singleShot(0, self._relayout_heights)

    def _relayout_heights(self) -> None:
        """Zeilenhöhen für umbrochene Texte berechnen (QTreeView berücksichtigt Umbruch nicht selbst)."""
        self._heights_pending = False
        fm = self.tree.fontMetrics()
        widths = [self.tree.columnWidth(c) for c in range(COL_ACTIONS)]
        indent = self.tree.indentation() * 2
        for item in self._items.values():
            h = 0
            for col, width in enumerate(widths):
                avail = max(30, width - 12 - (indent if col == COL_FIELD else 0))
                rect = fm.boundingRect(0, 0, avail, 10_000, int(Qt.TextFlag.TextWordWrap), item.text(col))
                h = max(h, rect.height())
            widget = self.tree.itemWidget(item, COL_ACTIONS)
            wh = widget.sizeHint().height() if widget is not None else 0
            item.setSizeHint(COL_FIELD, QSize(1, max(h + 10, wh + 4, 32)))  # Breite muss >= 0 sein

    def _add_row(self, parent: QTreeWidgetItem, spec: RowSpec, currency: str, case: Case) -> None:
        key, fv = spec.key, spec.fv
        fdef = FIELD_REGISTRY[key]
        item = QTreeWidgetItem(parent)
        item.setData(0, KEY_ROLE, key)
        item.setText(COL_FIELD, fdef.label)
        tip = fdef.label
        if fdef.help:
            tip += f"\n{fdef.help}"
        item.setToolTip(COL_FIELD, tip)

        status = fv.status if fv is not None else FieldStatus.MISSING
        value_lines = [display_value(key, fv, currency) if fv is not None else "— (nicht gefunden)"]
        value_tip = [value_lines[0]]
        if status == FieldStatus.CONFLICT:
            value_tip[0] = "Unterschiedliche Werte in den Dokumenten: " + value_lines[0]
        for m in fv.validation if fv is not None else []:
            line = f"{severity_symbol(m.severity)} {m.message}"
            value_lines.append(line)
            value_tip.append(line)
        if fv is not None and fv.note:
            note = fv.note if len(fv.note) <= 72 else fv.note[:70].rstrip() + " …"
            value_lines.append(f"ℹ {note}")
            value_tip.append(f"ℹ {fv.note}")
        item.setText(COL_VALUE, "\n".join(value_lines))
        item.setToolTip(COL_VALUE, "\n".join(value_tip))
        if fv is not None and any(m.severity.name == "ERROR" for m in fv.validation):
            item.setForeground(COL_VALUE, QColor(theme.ERROR))

        src, src_tip = source_text(case, fv)
        item.setText(COL_SOURCE, src)
        item.setToolTip(COL_SOURCE, src_tip)

        sym, txt, color = theme.STATUS_STYLE[status]
        conf = confidence_text(fv.confidence) if fv is not None and status == FieldStatus.EXTRACTED else ""
        item.setText(COL_STATUS, f"{sym} {txt}" + (f"\n{conf}" if conf else ""))
        item.setForeground(COL_STATUS, QColor(color))
        item.setToolTip(COL_STATUS, f"{txt}" + (f"\n{conf} – {CONFIDENCE_TOOLTIP}" if conf else ""))
        item.setData(COL_STATUS, KEY_ROLE, status.value)
        self._items[key] = item
        self._row_actions[key] = available_actions(fv)

    def _actions_widget(self, key: str, fv: FieldValue | None) -> QWidget:
        """Eine passende Hauptaktion als Schaltfläche, alle weiteren im Menü „⋯“."""
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(4)
        label = FIELD_REGISTRY[key].label
        actions = self._row_actions.get(key) or available_actions(fv)
        main = main_action(actions)
        text = main_action_text(main, fv)
        btn = QPushButton(text)
        btn.setObjectName(f"action_{main}")
        btn.setAccessibleName(f"{text.rstrip(' …')}: {label}")
        btn.setToolTip(ACTION_TOOLTIPS[main])
        btn.setStyleSheet("padding: 3px 7px; font-size: 10pt;")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if main == ACTION_COMPARE:
            btn.setProperty("primary", True)
        btn.clicked.connect(lambda _=False, a=main, k=key: self.action_requested.emit(a, k))
        lay.addWidget(btn)
        more = [a for a in actions if a != main]
        if more:
            tool = QToolButton()
            tool.setObjectName("action_more")
            tool.setText("⋯")
            tool.setAccessibleName(f"Weitere Aktionen: {label}")
            tool.setToolTip("Weitere Aktionen: " + ", ".join(ACTION_LABELS[a].rstrip(" …") for a in more))
            tool.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            tool.setStyleSheet(
                "QToolButton { padding: 3px 6px; } QToolButton::menu-indicator { image: none; }"
            )
            menu = QMenu(tool)
            for action in more:
                act = QAction(ACTION_LABELS[action], menu)
                act.setObjectName(f"action_{action}")
                act.setToolTip(ACTION_TOOLTIPS[action])
                act.triggered.connect(lambda _=False, a=action, k=key: self.action_requested.emit(a, k))
                menu.addAction(act)
            tool.setMenu(menu)
            lay.addWidget(tool)
        lay.addStretch(1)
        box.setEnabled(self._enabled)
        return box

    def trigger(self, key: str, action: str) -> bool:
        """Aktion über die sichtbare Schaltfläche bzw. den Menüeintrag auslösen (für Tastatur/Tests)."""
        btn = self.action_button(key, action)
        if btn is not None:
            btn.click()
            return True
        act = self.menu_action(key, action)
        if act is not None:
            act.trigger()
            return True
        return False

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Alle Zeilen entfernen (z. B. kein Vorgang geöffnet)."""
        self.tree.clear()
        self._items.clear()
        self._row_actions.clear()
        self._sections.clear()
        self.bulk_info.setText("")
        self._update_bulk()

    def set_actions_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        for item in self._items.values():
            w = self.tree.itemWidget(item, COL_ACTIONS)
            if w is not None:
                w.setEnabled(enabled)
        self._update_bulk()

    def field_keys(self) -> list[str]:
        return list(self._items)

    def keys_in_section(self, section: str) -> list[str]:
        head = self._sections.get(section)
        if head is None:
            return []
        return [head.child(i).data(0, KEY_ROLE) for i in range(head.childCount())]

    def section_item(self, section: str) -> QTreeWidgetItem | None:
        return self._sections.get(section)

    def item_for(self, key: str) -> QTreeWidgetItem | None:
        return self._items.get(key)

    def status_text_of(self, key: str) -> str:
        item = self._items.get(key)
        return item.text(COL_STATUS) if item else ""

    def action_button(self, key: str, action: str) -> QPushButton | None:
        item = self._items.get(key)
        if item is None:
            return None
        w = self.tree.itemWidget(item, COL_ACTIONS)
        return w.findChild(QPushButton, f"action_{action}") if w else None

    def menu_action(self, key: str, action: str) -> QAction | None:
        item = self._items.get(key)
        if item is None:
            return None
        w = self.tree.itemWidget(item, COL_ACTIONS)
        tool = w.findChild(QToolButton, "action_more") if w else None
        if tool is None or tool.menu() is None:
            return None
        return next((a for a in tool.menu().actions() if a.objectName() == f"action_{action}"), None)

    def current_key(self) -> str | None:
        item = self.tree.currentItem()
        key = item.data(0, KEY_ROLE) if item is not None else None
        return str(key) if key else None

    def select_key(self, key: str) -> bool:
        item = self._items.get(key)
        if item is None:
            return False
        parent = item.parent()
        if parent is not None and not parent.isExpanded():
            parent.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        return True

    def selected_keys(self) -> list[str]:
        out = []
        for item in self.tree.selectedItems():
            key = item.data(0, KEY_ROLE)
            if key:
                out.append(str(key))
        return out

    def select_open_rows(self) -> None:
        section = SECTION_REQUIRED if SECTION_REQUIRED in self._sections else SECTION_ALL
        self.tree.clearSelection()
        for key in self.keys_in_section(section):
            item = self._items[key]
            if item.data(COL_STATUS, KEY_ROLE) == FieldStatus.EXTRACTED.value:
                item.setSelected(True)
        self._update_bulk()

    # ------------------------------------------------------------------
    def _update_bulk(self) -> None:
        n = len(self.selected_keys())
        self.bulk_btn.setEnabled(self._enabled and n > 0)
        self.bulk_btn.setText(f"Ausgewählte bestätigen ({n})" if n else "Ausgewählte bestätigen")
        self.select_open_btn.setEnabled(self._enabled and bool(self._items))

    def _bulk_confirm(self) -> None:
        keys = self.selected_keys()
        if keys:
            self.confirm_selected.emit(keys)

    def _current_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        key = current.data(0, KEY_ROLE) if current is not None else None
        self.current_key_changed.emit(str(key) if key else "")

    def _activated(self, item: QTreeWidgetItem, _column: int) -> None:
        key = item.data(0, KEY_ROLE)
        if not key:
            item.setExpanded(not item.isExpanded())

    def _clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, SECTION_ROLE):
            item.setExpanded(not item.isExpanded())

    def _expanded_changed(self, item: QTreeWidgetItem) -> None:
        section = item.data(0, SECTION_ROLE)
        if section == SECTION_OTHER:
            self._other_expanded = item.isExpanded()
        if section:
            self._update_section_text(item)

    @staticmethod
    def _update_section_text(head: QTreeWidgetItem) -> None:
        section = head.data(0, SECTION_ROLE)
        arrow = "▾" if head.isExpanded() else "▸"
        state = "" if head.isExpanded() else " – zum Aufklappen anklicken"
        head.setText(0, f"{arrow} {SECTION_TITLES[section]} ({head.data(0, COUNT_ROLE)}){state}")

    def set_other_expanded(self, expanded: bool) -> None:
        head = self._sections.get(SECTION_OTHER)
        if head is not None:
            head.setExpanded(expanded)

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        key = item.data(0, KEY_ROLE) if item is not None else None
        if not key or not self._enabled:
            return
        menu = QMenu(self)
        for action in self._row_actions.get(str(key), []):
            act = menu.addAction(ACTION_LABELS[action])
            act.triggered.connect(lambda _=False, a=action, k=str(key): self.action_requested.emit(a, k))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
