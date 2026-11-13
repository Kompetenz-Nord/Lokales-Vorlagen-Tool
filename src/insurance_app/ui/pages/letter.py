"""Seite „Brief schreiben“: ein Formular, das wie der fertige Brief aussieht.

Ablauf für den Anwender
1. Dokument ablegen (Versicherungsschein, Schreiben, Foto) – die App füllt den Brief aus.
2. Angaben kurz gegenlesen und bei Bedarf ändern.
3. „PDF jetzt erzeugen“.

Automatisch übernommene Angaben sind hinterlegt und per Text gekennzeichnet, bis der
Anwender sie ändert. Nichts wird versendet; die Unterschrift erfolgt von Hand.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...core.case import Case
from ...core.service import AppService
from ...letters.simple import (
    DEFAULT_LETTER_TYPE,
    MAX_REFERENCES,
    SIMPLE_LETTER_TYPES,
    Prefill,
    Reference,
    SimpleLetter,
)
from .. import dialogs, theme
from ..case.drop_area import DropArea
from ..widgets.common import card, muted, page_title, primary_button, section_title
from ..workers import run_task, user_message

log = logging.getLogger(__name__)

AUTO_TOOLTIP = "Automatisch aus dem Dokument übernommen – bitte kurz prüfen."
AUTO_LEGEND = "Farbig hinterlegte Felder stammen aus dem Dokument (siehe Liste oben) – bitte kurz prüfen."
AUTO_NAMES = {
    "sender_name": "Ihr Name",
    "sender_street": "Straße",
    "sender_postal_city": "PLZ und Ort",
    "recipient": "Empfänger",
}
READY_TEXT = "✔ Alles vollständig – der Brief kann erzeugt werden."
SIGNATURE_HINT = "Unterschrift: Bitte nach dem Ausdrucken von Hand unterschreiben. Die App unterschreibt nie."
DROP_TEXT = (
    "📥  Versicherungsschein, Schreiben oder Foto hier ablegen – die App füllt den Brief automatisch aus"
)
STEPS_TEXT = "So geht es: 1. Dokument ablegen · 2. Angaben kurz prüfen · 3. „PDF jetzt erzeugen“"


def _set_auto(widget: QWidget, on: bool) -> None:
    """Feld als „automatisch übernommen“ kennzeichnen (Hinterlegung + Hinweistext)."""
    if bool(widget.property("autoFilled")) == on:
        return
    widget.setProperty("autoFilled", on)
    widget.setToolTip(AUTO_TOOLTIP if on else "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class ReferenceRow(QWidget):
    """Eine Bezugsangabe: Bezeichnung, Wert und „Entfernen“."""

    def __init__(
        self, label: str, value: str, on_change: Callable[[], None], on_remove: Callable[[ReferenceRow], None]
    ):
        super().__init__()
        # Bezeichnung oben, Wert darunter – so sind beide vollständig lesbar
        row = QGridLayout(self)
        row.setContentsMargins(0, 0, 0, 6)
        row.setHorizontalSpacing(6)
        row.setVerticalSpacing(4)
        self.label_edit = QLineEdit(label)
        self.label_edit.setPlaceholderText("Bezeichnung")
        self.label_edit.setAccessibleName("Bezeichnung der Bezugsangabe")
        self.label_edit.setStyleSheet("font-weight: 700;")
        self.value_edit = QLineEdit(value)
        self.value_edit.setPlaceholderText("Wert")
        self.value_edit.setAccessibleName(f"Wert für {label or 'Bezugsangabe'}")
        self.remove_btn = QPushButton("✕")
        self.remove_btn.setFixedWidth(34)
        self.remove_btn.setToolTip("Diese Angabe entfernen")
        self.remove_btn.setAccessibleName(f"{label or 'Angabe'} entfernen")
        self.label_edit.setMinimumWidth(190)  # „Versicherungsnummer“ muss ganz lesbar sein
        row.addWidget(self.label_edit, 0, 0)
        row.addWidget(self.remove_btn, 0, 1)
        row.addWidget(self.value_edit, 1, 0)
        self.label_edit.textEdited.connect(lambda _t: on_change())
        self.value_edit.textEdited.connect(lambda _t: (_set_auto(self.value_edit, False), on_change()))
        self.remove_btn.clicked.connect(lambda: on_remove(self))

    def reference(self) -> Reference:
        return Reference(label=self.label_edit.text(), value=self.value_edit.text())


class LetterPage(QWidget):
    def __init__(self, service: AppService, open_export_dir: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.service = service
        self.case: Case | None = None
        self.busy = False
        self._loading = False
        self._type_key = DEFAULT_LETTER_TYPE
        self._open_export_dir = open_export_dir
        self.last_export: Path | None = None
        self.rows: list[ReferenceRow] = []
        self.problems: list[str] = []
        self._pixmap: QPixmap | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 12)
        outer.setSpacing(8)
        outer.addWidget(page_title("Brief schreiben"))
        outer.addWidget(muted(STEPS_TEXT))

        top = QHBoxLayout()
        top.addWidget(QLabel("Briefart:"))
        self.type_combo = QComboBox()
        self.type_combo.setAccessibleName("Briefart")
        for ltype in service.simple_letter_types():
            self.type_combo.addItem(ltype.title, ltype.key)
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        top.addWidget(self.type_combo, 1)
        self.choose_btn = primary_button("Dokument auswählen …", "Dokument von diesem Computer auswählen")
        self.choose_btn.clicked.connect(self.choose_files)
        top.addWidget(self.choose_btn)
        self.sample_btn = QPushButton("Beispiel laden (synthetisch)")
        self.sample_btn.setToolTip("Lädt einen frei erfundenen Kfz-Versicherungsschein zum Ausprobieren.")
        self.sample_btn.clicked.connect(self.load_sample)
        top.addWidget(self.sample_btn)
        self.clear_btn = QPushButton("Neuer Brief")
        self.clear_btn.setToolTip("Alle Felder leeren und von vorn beginnen.")
        self.clear_btn.clicked.connect(self.new_letter)
        top.addWidget(self.clear_btn)
        outer.addLayout(top)

        self.drop = DropArea()
        dl = QVBoxLayout(self.drop)
        dl.setContentsMargins(12, 10, 12, 10)
        drop_label = QLabel(DROP_TEXT)
        drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_label.setWordWrap(True)
        dl.addWidget(drop_label)
        self.drop.files_dropped.connect(self.import_files)
        outer.addWidget(self.drop)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Ergebnis der automatischen Befüllung")
        outer.addWidget(self.status_label)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_form())
        split.addWidget(self._build_preview())
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 4)
        outer.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.generate_btn = primary_button("PDF jetzt erzeugen", "Erzeugt den Brief als PDF im Exportordner.")
        self.generate_btn.clicked.connect(self.generate)
        bottom.addWidget(self.generate_btn)
        self.open_btn = QPushButton("Ordner öffnen")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        bottom.addWidget(self.open_btn)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        bottom.addWidget(self.result_label, 1)
        outer.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh_preview)
        self.set_letter(service.prefill_simple_letter(None, DEFAULT_LETTER_TYPE))

    # ------------------------------------------------------------------ Aufbau
    def _build_form(self) -> QWidget:
        frame, lay = card()
        frame.setAccessibleName("Briefformular")
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(section_title("Absender (Sie)"))
        self.sender_name = self._line("Ihr Name", "Absender: Name")
        self.sender_street = self._line("Straße und Hausnummer", "Absender: Straße und Hausnummer")
        self.sender_postal_city = self._line("PLZ und Ort", "Absender: PLZ und Ort")
        for w in (self.sender_name, self.sender_street, self.sender_postal_city):
            left.addWidget(w)
        left.addSpacing(8)
        left.addWidget(section_title("Empfänger"))
        self.recipient = QPlainTextEdit()
        self.recipient.setPlaceholderText("Name des Versicherers\nStraße oder Postfach\nPLZ und Ort")
        self.recipient.setAccessibleName("Empfänger: Name und Anschrift")
        self.recipient.setFixedHeight(84)
        self.recipient.setTabChangesFocus(True)
        self.recipient.textChanged.connect(lambda: self._edited(self.recipient))
        left.addWidget(self.recipient)
        self.book_combo = QComboBox()
        self.book_combo.setAccessibleName("Gemerkte Empfänger")
        self.book_combo.activated.connect(self._book_chosen)
        left.addWidget(self.book_combo)
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(section_title("Bezug"))
        right.addWidget(muted("Damit der Empfänger den Vertrag findet."))
        self.refs_box = QVBoxLayout()
        self.refs_box.setSpacing(6)
        right.addLayout(self.refs_box)
        self.add_ref_btn = QPushButton("+ Info hinzufügen")
        self.add_ref_btn.setToolTip("Weitere Angabe ergänzen, z. B. Kundennummer oder Schadennummer.")
        self.add_ref_btn.clicked.connect(lambda: self.add_reference("", "", focus=True))
        right.addWidget(self.add_ref_btn, 0, Qt.AlignmentFlag.AlignRight)
        right.addStretch(1)

        grid.addLayout(left, 0, 0)
        grid.addLayout(right, 0, 1)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 5)
        lay.addLayout(grid)

        line = QHBoxLayout()
        self.subject = self._line("Betreff, z. B. Kündigung", "Betreff")
        self.subject.setStyleSheet("font-weight: 700;")
        self.date = self._line("TT.MM.JJJJ", "Datum")
        self.date.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.date.setFixedWidth(130)
        line.addWidget(self.subject, 1)
        line.addWidget(self.date)
        lay.addLayout(line)

        self.body = QPlainTextEdit()
        self.body.setAccessibleName("Brieftext")
        self.body.setPlaceholderText("Brieftext")
        self.body.setMinimumHeight(190)
        self.body.textChanged.connect(lambda: self._edited(self.body))
        lay.addWidget(self.body, 1)

        self.signature_hint = muted(SIGNATURE_HINT)
        lay.addWidget(self.signature_hint)
        self.signer = self._line("Ihr Name unter dem Brief", "Name unter dem Brief")
        self.signer.setMaximumWidth(360)
        lay.addWidget(self.signer)
        self.legend = muted(AUTO_LEGEND)
        lay.addWidget(self.legend)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(frame)
        scroll.setMinimumWidth(560)
        return scroll

    def _build_preview(self) -> QWidget:
        frame, lay = card()
        lay.addWidget(section_title("Vorschau"))
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setAccessibleName("Offene Punkte")
        lay.addWidget(self.problem_label)
        self.hint_label = muted("")
        lay.addWidget(self.hint_label)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.preview.setAccessibleName("Vorschau des Briefs")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.preview)
        scroll.setMinimumWidth(320)
        self.preview_scroll = scroll
        lay.addWidget(scroll, 1)
        return frame

    def _line(self, placeholder: str, name: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setAccessibleName(name)
        edit.textEdited.connect(lambda _t, w=edit: self._edited(w))
        return edit

    # ------------------------------------------------------------------ Formular <-> Modell
    def letter(self) -> SimpleLetter:
        return SimpleLetter(
            letter_type=self._type_key,
            sender_name=self.sender_name.text(),
            sender_street=self.sender_street.text(),
            sender_postal_city=self.sender_postal_city.text(),
            recipient=self.recipient.toPlainText(),
            references=[r.reference() for r in self.rows],
            subject=self.subject.text(),
            date=self.date.text(),
            body=self.body.toPlainText(),
            signer=self.signer.text(),
        )

    def set_letter(self, prefill: Prefill, source: str = "") -> None:
        """Formular füllen; automatisch übernommene Felder kennzeichnen."""
        letter = prefill.letter
        self._loading = True
        try:
            self._type_key = letter.letter_type
            idx = self.type_combo.findData(letter.letter_type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
            auto = set(prefill.auto_fields)
            for name in ("sender_name", "sender_street", "sender_postal_city", "subject", "date", "signer"):
                widget: QLineEdit = getattr(self, name)
                widget.setText(getattr(letter, name))
                _set_auto(widget, name in auto)
            self.recipient.setPlainText(letter.recipient)
            _set_auto(self.recipient, "recipient" in auto)
            self.body.setPlainText(letter.body)
            _set_auto(self.body, False)
            for row in list(self.rows):
                self._drop_row(row)
            for ref in letter.references:
                row = self.add_reference(ref.label, ref.value)
                _set_auto(row.value_edit, f"reference:{ref.label}" in auto)
        finally:
            self._loading = False
        self._show_status(prefill, source)
        self._refresh_book()
        self.result_label.setText("")
        self.refresh_preview()

    def add_reference(self, label: str, value: str, *, focus: bool = False) -> ReferenceRow:
        row = ReferenceRow(label, value, self._changed, self._remove_row)
        self.rows.append(row)
        self.refs_box.addWidget(row)
        self.add_ref_btn.setEnabled(len(self.rows) < MAX_REFERENCES)
        if focus:
            row.label_edit.setFocus()
            self._changed()
        return row

    def _drop_row(self, row: ReferenceRow) -> None:
        self.rows.remove(row)
        self.refs_box.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self.add_ref_btn.setEnabled(len(self.rows) < MAX_REFERENCES)

    def _remove_row(self, row: ReferenceRow) -> None:
        self._drop_row(row)
        self._changed()

    def _show_status(self, prefill: Prefill, source: str) -> None:
        names = [
            AUTO_NAMES.get(f, f.removeprefix("reference:")) for f in prefill.auto_fields if f != "signer"
        ]
        lines: list[str] = []
        color = theme.TEXT_MUTED
        if names:
            origin = f" aus „{source}“" if source else ""
            lines.append(
                f"✔ Automatisch ausgefüllt ({len(names)}){origin}: {', '.join(names)} – bitte kurz prüfen."
            )
            color = theme.OK
        elif source:
            lines.append(f"⚠ In „{source}“ wurden keine passenden Angaben gefunden – bitte selbst eintragen.")
            color = theme.WARN
        lines += [f"ℹ {n}" for n in prefill.notes]
        self.status_label.setText("\n".join(lines))
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 700;" if lines else "")
        self.status_label.setVisible(bool(lines))

    # ------------------------------------------------------------------ Ereignisse
    def _edited(self, widget: QWidget) -> None:
        if self._loading:
            return
        _set_auto(widget, False)
        self._changed()

    def _changed(self) -> None:
        if self._loading:
            return
        self.result_label.setText("")
        self._timer.start()

    def _type_changed(self, _index: int) -> None:
        if self._loading:
            return
        new_key = self.type_combo.currentData()
        old, new = SIMPLE_LETTER_TYPES[self._type_key], SIMPLE_LETTER_TYPES[new_key]
        own_text = self.body.toPlainText().strip() not in ("", old.body().strip())
        if own_text and not dialogs.ask_confirm(
            self,
            "Text ersetzen?",
            f"Der Brieftext wird durch den Standardtext für „{new.title}“ ersetzt. Ihre Änderungen am Text "
            "gehen dabei verloren.",
            ok_text="Text ersetzen",
        ):
            self._loading = True
            self.type_combo.setCurrentIndex(self.type_combo.findData(self._type_key))
            self._loading = False
            return
        self._type_key = new_key
        self._loading = True
        try:
            self.body.setPlainText(new.body())
            if self.subject.text().strip() in ("", old.subject):
                self.subject.setText(new.subject)
            have = {r.label_edit.text().strip() for r in self.rows}
            fresh = self.service.prefill_simple_letter(self.case, new_key)
            for ref in fresh.letter.references:
                if ref.label not in have and len(self.rows) < MAX_REFERENCES:
                    row = self.add_reference(ref.label, ref.value)
                    _set_auto(row.value_edit, bool(ref.value))
        finally:
            self._loading = False
        self.refresh_preview()

    def _refresh_book(self) -> None:
        book = self.service.recipient_book()
        self.book_combo.blockSignals(True)
        self.book_combo.clear()
        self.book_combo.addItem("Gemerkten Empfänger wählen …", None)
        for lines in book:
            self.book_combo.addItem(", ".join(lines), lines)
        self.book_combo.blockSignals(False)
        self.book_combo.setVisible(bool(book))

    def _book_chosen(self, index: int) -> None:
        lines = self.book_combo.itemData(index)
        if lines:
            self.recipient.setPlainText("\n".join(lines))
        self.book_combo.setCurrentIndex(0)

    # ------------------------------------------------------------------ Dokument einlesen
    def choose_files(self) -> None:
        paths = dialogs.pick_open_files(self, "Dokument auswählen")
        if paths:
            self.import_files(paths)

    def load_sample(self) -> None:
        from ...samples.kfz import generate_kfz_sample

        sample = generate_kfz_sample(self.service.paths.temp_dir / "musterbrief")
        self.type_combo.setCurrentIndex(self.type_combo.findData("kuendigung_kfz"))
        self.import_files([sample])

    def import_files(self, paths: list[Path]) -> None:
        if self.busy or not paths:
            return
        self.busy = True
        self._set_enabled(False)
        names = ", ".join(Path(p).name for p in paths[:3]) + (" …" if len(paths) > 3 else "")
        type_key = self._type_key

        def work(progress, cancel):  # noqa: ANN001
            return self.service.letter_from_documents(
                [Path(p) for p in paths], type_key, progress=progress, cancel=cancel
            )

        def done(result) -> None:  # noqa: ANN001
            self.busy = False
            self._set_enabled(True)
            case, prefill, notes = result
            self.case = case
            if notes:
                prefill = prefill.model_copy(update={"notes": [*notes, *prefill.notes]})
            self.set_letter(prefill, source=names)

        def failed(exc: BaseException) -> None:
            self.busy = False
            self._set_enabled(True)
            dialogs.show_error(self, "Dokument konnte nicht gelesen werden", user_message(exc))

        self.task = run_task(self, "Dokument wird gelesen …", work, on_done=done, on_error=failed)

    def _set_enabled(self, on: bool) -> None:
        for w in (
            self.choose_btn,
            self.sample_btn,
            self.clear_btn,
            self.generate_btn,
            self.type_combo,
            self.drop,
        ):
            w.setEnabled(on)

    def new_letter(self) -> None:
        self.case = None
        self.last_export = None
        self.open_btn.setEnabled(False)
        self.set_letter(self.service.prefill_simple_letter(None, self._type_key))

    def open_case(self, case: Case) -> bool:
        """Früher erzeugten Brief eines Vorgangs wieder öffnen. Rückgabe: gab es einen?"""
        stored = self.service.stored_simple_letter(case)
        if stored is None:
            return False
        self.case = case
        self.set_letter(Prefill(letter=stored))
        return True

    # ------------------------------------------------------------------ Vorschau und Erzeugung
    def refresh_preview(self) -> None:
        if self.busy:
            return
        letter = self.letter()
        ltype = SIMPLE_LETTER_TYPES[self._type_key]
        self.hint_label.setText(f"⚖ {ltype.hint}" if ltype.hint else "")
        self.hint_label.setVisible(bool(ltype.hint))
        try:
            preview = self.service.preview_simple_letter(letter)
        except Exception as exc:  # noqa: BLE001 – Vorschau darf die Eingabe nie blockieren
            log.warning("Briefvorschau fehlgeschlagen: %s", type(exc).__name__)
            self.problems = self.service.check_simple_letter(letter)
            self.preview.setText("Die Vorschau konnte nicht erzeugt werden.")
        else:
            self.problems = [*preview.problems, *preview.warnings]
            if preview.page_images:
                self._pixmap = QPixmap(str(preview.page_images[0]))
                self._fit_preview()
        if self.problems:
            self.problem_label.setText("Noch offen:\n" + "\n".join(f"✖ {p}" for p in self.problems))
            self.problem_label.setStyleSheet(f"color: {theme.ERROR}; font-weight: 700;")
        else:
            self.problem_label.setText(READY_TEXT)
            self.problem_label.setStyleSheet(f"color: {theme.OK}; font-weight: 700;")
        self.generate_btn.setToolTip(
            "Erzeugt den Brief als PDF im Exportordner."
            if not self.problems
            else "Bitte zuerst die offenen Punkte in der Vorschau erledigen."
        )

    def _fit_preview(self) -> None:
        """Vorschaubild auf die Breite des Vorschaubereichs bringen (ohne waagerechten Rollbalken)."""
        if self._pixmap is None or self._pixmap.isNull():
            return
        bar = self.preview_scroll.verticalScrollBar().sizeHint().width()
        width = max(260, self.preview_scroll.width() - bar - 10)
        self.preview.setPixmap(self._pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        super().resizeEvent(event)
        self._fit_preview()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        super().showEvent(event)
        QTimer.singleShot(0, self, self._fit_preview)

    def generate(self) -> None:
        self._timer.stop()
        self.refresh_preview()
        if self.problems:
            dialogs.show_error(
                self,
                "Der Brief ist noch nicht vollständig",
                "Bitte zuerst erledigen:\n\n" + "\n".join(f"• {p}" for p in self.problems),
            )
            return
        try:
            self.case, outcome = self.service.generate_simple_letter(self.case, self.letter())
        except Exception as exc:  # noqa: BLE001 – verständliche Meldung statt Absturz
            dialogs.show_error(self, "Der Brief konnte nicht erzeugt werden", user_message(exc))
            return
        self.last_export = outcome.path
        self.open_btn.setEnabled(True)
        self.result_label.setText(
            f"✔ PDF erzeugt: {outcome.path.name}\nBitte ausdrucken, von Hand unterschreiben und selbst versenden."
        )
        self.result_label.setStyleSheet(f"color: {theme.OK}; font-weight: 700;")
        self._refresh_book()

    def _open_folder(self) -> None:
        if self._open_export_dir is not None:
            self._open_export_dir()
        elif self.last_export is not None:
            dialogs.open_path(self.last_export.parent)

    def refresh(self) -> None:
        """Beim Anzeigen der Seite: Adressbuch und Vorschau auffrischen."""
        self._refresh_book()
        self.refresh_preview()
