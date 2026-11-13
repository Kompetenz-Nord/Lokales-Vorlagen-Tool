"""Dialog „Wert bearbeiten“ mit Formathinweis je Datentyp.

Fehler bei der Eingabe (``FieldInputError``) erscheinen direkt im Dialog; er bleibt offen.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Case
from ...core.fields import FIELD_REGISTRY, FieldType
from ...core.service import AppService
from .. import theme
from ..widgets.common import muted, primary_button
from .common import error_text
from .formatting import FORMAT_HINTS, display_value, edit_text, source_text


class FieldEditDialog(QDialog):
    def __init__(self, service: AppService, case: Case, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.case = case
        self.key = key
        self.fdef = FIELD_REGISTRY[key]
        fv = case.get(key)
        self.setWindowTitle(f"Wert bearbeiten – {self.fdef.label}")
        self.setMinimumWidth(520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        title = QLabel(self.fdef.label)
        title.setStyleSheet("font-size: 12pt; font-weight: 700;")
        title.setWordWrap(True)
        layout.addWidget(title)
        if fv is not None and fv.status.name != "MISSING":
            src, _tip = source_text(case, fv)
            layout.addWidget(muted(f"Bisher: {display_value(key, fv)}" + (f"  ({src})" if src else "")))
        placeholder, hint = FORMAT_HINTS.get(self.fdef.dtype, ("", ""))
        self.hint = muted(f"Format: {hint}")
        layout.addWidget(self.hint)
        if self.fdef.help:
            layout.addWidget(muted(self.fdef.help))

        self.combo: QComboBox | None = None
        self.edit: QLineEdit | None = None
        if self.fdef.dtype == FieldType.CHOICE and self.fdef.choices:
            self.combo = QComboBox()
            self.combo.setAccessibleName(self.fdef.label)
            self.combo.addItem("– bitte wählen –", None)
            for k, label in self.fdef.choices.items():
                self.combo.addItem(label, k)
            current = fv.normalized_value if fv is not None else None
            idx = self.combo.findData(current) if current else 0
            self.combo.setCurrentIndex(max(0, idx))
            layout.addWidget(self.combo)
        else:
            self.edit = QLineEdit(edit_text(key, fv))
            self.edit.setPlaceholderText(placeholder)
            self.edit.setAccessibleName(self.fdef.label)
            self.edit.setToolTip(hint)
            self.edit.returnPressed.connect(self.apply)
            layout.addWidget(self.edit)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.ERROR};")
        self.error.hide()
        layout.addWidget(self.error)
        layout.addWidget(
            muted("Der Wert wird als „manuell eingetragen“ gespeichert und gilt damit als von Ihnen geprüft.")
        )

        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        self.ok_btn = primary_button("Übernehmen")
        self.ok_btn.setAccessibleName("Wert übernehmen")
        self.ok_btn.clicked.connect(self.apply)
        bar.addWidget(cancel)
        bar.addWidget(self.ok_btn)
        layout.addLayout(bar)

    def raw_value(self) -> str:
        if self.combo is not None:
            data = self.combo.currentData()
            return str(self.combo.currentText()) if data else ""
        assert self.edit is not None
        return self.edit.text()

    def set_value(self, text: str) -> None:
        if self.combo is not None:
            idx = self.combo.findText(text)
            if idx < 0:
                idx = self.combo.findData(text)
            self.combo.setCurrentIndex(max(0, idx))
        elif self.edit is not None:
            self.edit.setText(text)

    def apply(self) -> None:
        try:
            self.service.edit_field(self.case, self.key, self.raw_value())
        except Exception as exc:  # noqa: BLE001 – Eingabefehler direkt im Dialog zeigen
            self.error.setText(f"✖ {error_text(exc)}")
            self.error.show()
            return
        self.accept()


def edit_field_value(parent: QWidget | None, service: AppService, case: Case, key: str) -> bool:
    """Dialog modal anzeigen. ``True``, wenn ein Wert gespeichert wurde."""
    dlg = FieldEditDialog(service, case, key, parent)
    try:
        return dlg.exec() == QDialog.DialogCode.Accepted
    finally:
        dlg.deleteLater()
