"""Anzeige der neun Arbeitsschritte (nummerierte Kreise mit Beschriftung).

Zustände werden nie nur über Farbe gezeigt: erledigte Schritte tragen ein ✔,
der aktuelle Schritt hat einen zusätzlichen Ring und eine fette Beschriftung, offene
Schritte zeigen ihre Nummer in Grau. Tooltip und Bedienhilfe nennen den Zustand als Wort. Jeder Schritt ist per Tastatur erreichbar und anklickbar.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextOption
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QWidget

from ...core.case import PipelineStage
from .. import theme

STEP_DONE = "done"
STEP_CURRENT = "current"
STEP_OPEN = "open"

STATE_TEXT = {STEP_DONE: "erledigt", STEP_CURRENT: "aktuell", STEP_OPEN: "offen"}

# Anzeige mit weichen Trennstellen (U+00AD), damit lange Wörter sauber umbrechen
SOFT_LABELS: dict[PipelineStage, str] = {
    PipelineStage.DOKUMENTCHECK: "Dokument\u00adcheck",
    PipelineStage.TEXTERKENNUNG: "Text\u00aderkennung",
    PipelineStage.DATENEXTRAKTION: "Daten\u00adextraktion",
    PipelineStage.PRUEFUNG: "Konflikt- und Plausibilitäts\u00adprüfung",
    PipelineStage.DATENFREIGABE: "Daten\u00adfreigabe",
    PipelineStage.VORSCHAU: "PDF-Vorschau und Qualitäts\u00adkontrolle",
}

STEP_HELP: dict[PipelineStage, str] = {
    PipelineStage.ABLAGE: "Dateien in den Vorgang übernehmen (Police, Nachtrag, Screenshot …).",
    PipelineStage.DOKUMENTCHECK: "Dateityp, Seiten, Passwort und Lesbarkeit werden geprüft.",
    PipelineStage.TEXTERKENNUNG: "Text lesen: zuerst die Textebene des PDFs, nur wenn nötig OCR "
    "(automatische Texterkennung aus dem Bild).",
    PipelineStage.DATENEXTRAKTION: "Werte wie Vertragsnummer oder Geburtsdatum werden mit Fundstelle erkannt.",
    PipelineStage.PRUEFUNG: "Widersprüche zwischen Dokumenten und unplausible Angaben werden markiert.",
    PipelineStage.DATENFREIGABE: "Sie prüfen und bestätigen die Werte und wählen die Auftragsdaten bewusst aus.",
    PipelineStage.VORLAGE_WAEHLEN: "Die passende Originalvorlage oder den Brief auswählen.",
    PipelineStage.VORSCHAU: "Probe-PDF mit Checkliste und technischer Qualitätskontrolle.",
    PipelineStage.EXPORT: "Erst nach Ihrem Klick wird die PDF erzeugt und im Exportordner abgelegt.",
}


class StepButton(QAbstractButton):
    CIRCLE = 28

    def __init__(self, stage: PipelineStage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stage = stage
        self.state = STEP_OPEN
        self.setText(stage.label_de)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._update_texts()

    def set_state(self, state: str) -> None:
        self.state = state
        self._update_texts()
        self.update()

    def _update_texts(self) -> None:
        n = int(self.stage)
        state = STATE_TEXT[self.state]
        self.setAccessibleName(f"Schritt {n} von 9: {self.stage.label_de} – {state}")
        self.setToolTip(f"Schritt {n}: {self.stage.label_de} ({state})\n{STEP_HELP[self.stage]}")

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt-API)
        return QSize(110, 86)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt-API)
        return QSize(70, 86)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        d = self.CIRCLE
        circle = QRectF((w - d) / 2, 2, d, d)
        if self.state == STEP_DONE:
            fill, border, fg = QColor(theme.OK), QColor(theme.OK), QColor("white")
            symbol = "✔"
        elif self.state == STEP_CURRENT:
            fill, border, fg = QColor(theme.PRIMARY), QColor(theme.PRIMARY_DARK), QColor("white")
            symbol = str(int(self.stage))
        else:
            fill, border, fg = QColor(theme.SURFACE), QColor(theme.BORDER), QColor(theme.TEXT_MUTED)
            symbol = str(int(self.stage))
        if self.state == STEP_CURRENT:  # zusätzlicher Ring: nicht nur Farbe
            p.setPen(QPen(QColor(theme.PRIMARY), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle.adjusted(-3, -3, 3, 3))
        p.setPen(QPen(border, 1.5))
        p.setBrush(fill)
        p.drawEllipse(circle)
        font = QFont(self.font())
        font.setPointSizeF(10.5)
        font.setBold(True)
        p.setFont(font)
        p.setPen(fg)
        p.drawText(circle, Qt.AlignmentFlag.AlignCenter, symbol)

        label_font = QFont(self.font())
        label_font.setPointSizeF(10)
        label_font.setBold(self.state == STEP_CURRENT)
        p.setFont(label_font)
        p.setPen(QColor(theme.TEXT if self.state != STEP_OPEN else theme.TEXT_MUTED))
        opt = QTextOption(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        label_rect = QRectF(2, d + 6, w - 4, self.height() - d - 8)
        p.drawText(label_rect, SOFT_LABELS.get(self.stage, self.stage.label_de), opt)
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.PRIMARY), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, w - 2, self.height() - 2), 6, 6)
        p.end()


class StageStepper(QWidget):
    """Neun Schritte nebeneinander; ``step_clicked(int)`` meldet die angeklickte Stufe."""

    step_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Arbeitsschritte des Vorgangs")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(2)
        self.buttons: dict[PipelineStage, StepButton] = {}
        for stage in PipelineStage:
            btn = StepButton(stage)
            btn.clicked.connect(lambda _=False, s=stage: self.step_clicked.emit(int(s)))
            layout.addWidget(btn, 1)
            self.buttons[stage] = btn

    def set_stage(self, stage: PipelineStage | int | None, *, finished: bool = False) -> None:
        """Bis ``stage`` erledigt, ``stage`` aktuell, danach offen. ``finished``: alles erledigt."""
        current = int(stage) if stage is not None else 0
        for st, btn in self.buttons.items():
            n = int(st)
            if finished or n < current:
                btn.set_state(STEP_DONE)
            elif n == current:
                btn.set_state(STEP_CURRENT)
            else:
                btn.set_state(STEP_OPEN)

    def states(self) -> dict[int, str]:
        return {int(s): b.state for s, b in self.buttons.items()}
