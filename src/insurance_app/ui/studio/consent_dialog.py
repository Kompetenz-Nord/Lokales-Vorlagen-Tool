"""Ausdrückliche Zustimmung vor einem Modell-Download (lokale KI).

Der Download wird vom lokal installierten Ollama-Dienst aus der Ollama-Bibliothek
durchgeführt. Die App selbst sendet dabei keine Dokumentdaten. Ohne Häkchen bei
„Ich stimme dem Download ausdrücklich zu“ bleibt die Schaltfläche gesperrt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..theme import WARN
from ..widgets.common import muted
from .common import gb_text

CONSENT_TEXT = "Ich stimme dem Download ausdrücklich zu"


@dataclass
class DownloadInfo:
    model: str
    label: str = ""
    approx_gb: float | None = None
    free_disk_gb: float | None = None
    license_hint: str = ""
    verified: bool = False
    hints: list[str] = field(default_factory=list)  # Hardware-/Speicherhinweise


class ConsentDialog(QDialog):
    def __init__(self, info: DownloadInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modell herunterladen?")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        title = QLabel(f"Modell „{info.model}“ herunterladen")
        title.setStyleSheet("font-weight: 700; font-size: 12pt;")
        layout.addWidget(title)
        if info.label:
            layout.addWidget(muted(info.label))
        size = f"etwa {gb_text(info.approx_gb)}" if info.approx_gb else "unbekannt"
        facts = QLabel(
            f"• Ungefähre Downloadgröße: {size}\n"
            f"• Freier Speicherplatz am Modellordner: {gb_text(info.free_disk_gb)}\n"
            f"• Lizenz: {info.license_hint or 'bitte in der Ollama-Bibliothek prüfen'}"
        )
        facts.setWordWrap(True)
        layout.addWidget(facts)
        if not info.verified:
            warn = QLabel(
                "⚠ Größe und Lizenz sind ungeprüfte Angaben – bitte vorher in der Ollama-Bibliothek prüfen."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {WARN};")
            layout.addWidget(warn)
        for hint in info.hints:
            if hint and "ungeprüft" not in hint:
                h = QLabel(f"⚠ {hint}")
                h.setWordWrap(True)
                h.setStyleSheet(f"color: {WARN};")
                layout.addWidget(h)
        explain = QLabel(
            "So funktioniert der Download:\n"
            "• Den Download führt der lokal installierte Ollama-Dienst aus. Er lädt das Modell aus der "
            "Ollama-Bibliothek im Internet.\n"
            "• Die App sendet dabei keine Dokumente und keine Vorgangsdaten – nur den Modellnamen an Ollama "
            "auf diesem Rechner.\n"
            "• Sie können den Download jederzeit abbrechen und später fortsetzen.\n"
            "• Die App installiert keine Software. Ollama richten Sie selbst ein."
        )
        explain.setWordWrap(True)
        layout.addWidget(explain)
        self.consent = QCheckBox(CONSENT_TEXT)
        self.consent.setAccessibleName(CONSENT_TEXT)
        layout.addWidget(self.consent)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Download starten")
        self.ok_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        self.consent.toggled.connect(self.ok_button.setEnabled)
        layout.addWidget(self.buttons)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)

    def _accept(self) -> None:
        if self.consent.isChecked():  # ohne ausdrückliche Zustimmung kein Download
            self.accept()

    @property
    def consented(self) -> bool:
        return self.consent.isChecked() and self.result() == QDialog.DialogCode.Accepted


def ask_download_consent(parent: QWidget | None, info: DownloadInfo) -> bool:
    """Zustimmungsdialog anzeigen (in Tests per ``monkeypatch`` ersetzbar)."""
    dlg = ConsentDialog(info, parent)
    dlg.exec()
    return dlg.consented
