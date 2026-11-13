"""Kachel eines Katalogeintrags (Vorlagen & Formulare)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout

from ...core.service import TemplateChoice
from ...templates.schema import TemplateStatus
from ..theme import BORDER, TEXT_MUTED
from ..widgets.common import badge, muted, primary_button
from .common import template_status_color, template_status_text

LETTER_FORM_TEXT = "Brief nach DIN 5008 (wird erzeugt)"
REASON_NO_CASE = "Bitte zuerst einen Vorgang öffnen (Dashboard / Vorgänge)."
REASON_MISSING_ORIGINAL = "Originalvorlage fehlt – im Vorlagen-Studio importieren."
REASON_NEEDS_REVIEW = "Feldzuordnung noch nicht bestätigt – im Vorlagen-Studio prüfen."
REASON_ERROR = "Vorlage fehlerhaft – Details im Vorlagen-Studio."
IMPORT_BUTTON_TEXT = "Originalvorlage importieren …"

_UNAVAILABLE_STYLE = (
    f"QFrame#Card {{ background: #F3F5F8; border: 1px dashed {BORDER}; border-radius: 10px; }}"
)


def form_line(choice: TemplateChoice) -> str:
    if choice.kind == "letter":
        return LETTER_FORM_TEXT
    if choice.form_number:
        return f"Formular {choice.form_number}"
    return "Formular des Versicherers (Originalvorlage nötig)"


def unavailable_reason(choice: TemplateChoice) -> str:
    return {
        TemplateStatus.MISSING_ORIGINAL: REASON_MISSING_ORIGINAL,
        TemplateStatus.NEEDS_REVIEW: REASON_NEEDS_REVIEW,
        TemplateStatus.ERROR: REASON_ERROR,
    }.get(choice.status, choice.status_reason)


class TemplateTile(QFrame):
    """Karte mit Nummer, Titel, Versicherer, Formularart, Status, Beschreibung und Aktionen."""

    def __init__(
        self,
        choice: TemplateChoice,
        on_use: Callable[[str], None],
        on_studio: Callable[[str], None],
        on_import: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(objectName="Card")
        self.choice = choice
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        if not choice.available:
            self.setStyleSheet(_UNAVAILABLE_STYLE)
        self.setAccessibleName(f"Vorlage {choice.number}: {choice.title}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        head = QHBoxLayout()
        number = QLabel(f"Nr. {choice.number}")
        number.setStyleSheet(
            f"color: {TEXT_MUTED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 1px 8px;"
        )
        number.setAccessibleName(f"Nummer {choice.number}")
        head.addWidget(number)
        title = QLabel(choice.title)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 700; font-size: 11.5pt;")
        head.addWidget(title, 1)
        self.status_badge = badge(
            template_status_text(choice.status), template_status_color(choice.status), choice.status_reason
        )
        self.status_badge.setAccessibleName(f"Status: {choice.status.value}")
        head.addWidget(self.status_badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        meta = QLabel(f"Versicherer: {choice.insurer}   ·   {form_line(choice)}")
        meta.setWordWrap(True)
        if choice.kind == "letter":
            meta.setToolTip(
                "DIN 5008 ist die deutsche Norm für den Aufbau von Geschäftsbriefen. "
                "Die App erzeugt das Anschreiben selbst – es ist kein Formular des Versicherers."
            )
        layout.addWidget(meta)

        reason = muted(choice.status_reason)
        reason.setAccessibleName(f"Statusbegründung: {choice.status_reason}")
        self.reason_label = reason
        layout.addWidget(reason)

        desc = QLabel(choice.description)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        if choice.legal_hint:
            legal = muted(f"⚖ {choice.legal_hint}")
            legal.setToolTip("Rechtlicher Hinweis: Diese Angaben entscheidet immer der Mensch, nie die App.")
            layout.addWidget(legal)

        buttons = QHBoxLayout()
        self.use_button: QPushButton = primary_button("Für aktuellen Vorgang verwenden")
        self.use_button.setAccessibleName(f"Vorlage {choice.number} für aktuellen Vorgang verwenden")
        self.use_button.clicked.connect(lambda: on_use(choice.catalog_id))
        buttons.addWidget(self.use_button)
        self.studio_button = QPushButton("Im Studio öffnen")
        self.studio_button.setToolTip(
            "Im Vorlagen-Studio sehen Sie Version, Feldzuordnung und können eine Originalvorlage importieren."
        )
        self.studio_button.setAccessibleName(f"Vorlage {choice.number} im Vorlagen-Studio öffnen")
        self.studio_button.clicked.connect(lambda: on_studio(choice.catalog_id))
        buttons.addWidget(self.studio_button)
        # Fehlt die Originalvorlage, führt eine eigene Schaltfläche direkt zum Import im Studio
        self.import_button: QPushButton | None = None
        if choice.status == TemplateStatus.MISSING_ORIGINAL and on_import is not None:
            self.import_button = QPushButton(IMPORT_BUTTON_TEXT)
            self.import_button.setToolTip(
                "Wechselt ins Vorlagen-Studio. Dieser Eintrag ist dort schon ausgewählt – Sie wählen "
                "nur noch die Original-PDF des Versicherers aus."
            )
            self.import_button.setAccessibleName(f"Originalvorlage für Vorlage {choice.number} importieren")
            self.import_button.clicked.connect(lambda: on_import(choice.catalog_id))
            buttons.addWidget(self.import_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.block_reason = muted("")
        self.block_reason.setVisible(False)
        layout.addWidget(self.block_reason)

    def update_case_state(self, has_case: bool) -> None:
        """Schaltfläche „verwenden“ nur bei verfügbarer Vorlage UND geöffnetem Vorgang aktiv."""
        if not self.choice.available:
            reason = unavailable_reason(self.choice)
        elif not has_case:
            reason = REASON_NO_CASE
        else:
            reason = ""
        enabled = not reason
        self.use_button.setEnabled(enabled)
        self.use_button.setToolTip(
            "Diese Vorlage im geöffneten Vorgang auswählen." if enabled else f"Nicht möglich: {reason}"
        )
        self.block_reason.setText(f"ℹ {reason}" if reason else "")
        self.block_reason.setVisible(bool(reason))
