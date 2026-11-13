"""Bereich „Lokale KI (optional)“ der Einstellungen.

* Statusprüfung nur gegen den lokalen Ollama-Dienst (Loopback) im Hintergrund.
* Modell-Download ausschließlich nach Klick auf „Modell herunterladen…“ UND
  ausdrücklicher Zustimmung im Dialog. Abbruch jederzeit möglich.
* Die App installiert keine Software; Ollama richtet die Anwenderin/der Anwender selbst ein.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ...local_ai.ollama_client import OllamaClient, OllamaError, PullCancelledError
from ...local_ai.status import (
    MODEL_CATALOG,
    UNVERIFIED_NOTE,
    HardwareInfo,
    LocalAiState,
    LocalAiStatus,
    check_model_requirements,
)
from .. import dialogs
from ..theme import ERROR, OK, TEXT_MUTED, WARN
from ..widgets.common import danger_button, muted
from ..workers import Task, run_task, user_message
from . import consent_dialog
from .common import gb_text

log = logging.getLogger(__name__)

STATE_STYLE: dict[LocalAiState, tuple[str, str]] = {
    LocalAiState.READY: ("✔", OK),
    LocalAiState.SETTING_UP: ("◐", WARN),
    LocalAiState.NOT_INSTALLED: ("○", TEXT_MUTED),
    LocalAiState.DISABLED: ("○", TEXT_MUTED),
    LocalAiState.ERROR: ("✖", ERROR),
}

CANCELLED_TEXT = (
    "Der Download wurde abgebrochen. Er kann später fortgesetzt werden – "
    "bereits geladene Teile behält Ollama."
)

_PULL_STATUS = (
    ("pulling manifest", "Modellbeschreibung wird geladen"),
    ("pulling", "Modelldaten werden geladen"),
    ("verifying", "Prüfsumme wird geprüft"),
    ("writing manifest", "Modell wird gespeichert"),
    ("removing", "Aufräumen"),
    ("success", "Fertig"),
)


def pull_status_text(status: str) -> str:
    low = status.lower()
    for needle, text in _PULL_STATUS:
        if low.startswith(needle):
            return text
    return status or "Download läuft"


def state_text(state: LocalAiState) -> str:
    return f"{STATE_STYLE[state][0]} {state.value}"


class LocalAiPanel(QWidget):
    """Status, Ein/Aus, Modellwahl, Hardware und Download/Entfernen."""

    def __init__(self, service, on_settings_changed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.service = service
        self._on_settings_changed = on_settings_changed
        self.status: LocalAiStatus | None = None
        self.hardware: HardwareInfo | None = None
        self.busy = False
        self.checking = False
        self.task: Task | None = None
        self._status_task: Task | None = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(
            muted(
                "Optional: Ein Vision-Sprachmodell (KI, die Bilder und Text versteht) kann schwer lesbare Seiten "
                "zusätzlich auswerten. Es läuft vollständig auf diesem Rechner über das Programm Ollama. "
                "Die App funktioniert auch ganz ohne KI. KI-Vorschläge müssen Sie immer selbst prüfen."
            )
        )

        status_row = QHBoxLayout()
        self.state_label = QLabel("○ Status noch nicht geprüft")
        self.state_label.setStyleSheet("font-weight: 700;")
        self.state_label.setAccessibleName("Status der lokalen KI")
        status_row.addWidget(self.state_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)
        self.message_label = muted("")
        self.message_label.setAccessibleName("Erläuterung zum Status der lokalen KI")
        layout.addWidget(self.message_label)

        self.not_installed_box = QFrame()
        self.not_installed_box.setStyleSheet(
            "QFrame { background: #FFF8E6; border: 1px solid #E9D49A; border-radius: 8px; }"
            "QLabel { border: none; background: transparent; }"
        )
        nib = QVBoxLayout(self.not_installed_box)
        nib.setContentsMargins(12, 10, 12, 10)
        self.not_installed_text = QLabel(
            "ⓘ Ollama wurde auf diesem Rechner nicht gefunden. Ollama ist ein kostenloses Programm, das "
            "KI-Modelle lokal ausführt. Die App installiert keine Software automatisch – bitte richten Sie "
            "Ollama selbst ein. Eine Schritt-für-Schritt-Anleitung steht in „docs/LOKALE_KI.md“ im "
            "Programmordner."
        )
        self.not_installed_text.setWordWrap(True)
        nib.addWidget(self.not_installed_text)
        doc_row = QHBoxLayout()
        self.docs_button = QPushButton("Anleitung öffnen")
        self.docs_button.setToolTip("Öffnet die lokale Anleitung docs/LOKALE_KI.md (keine Internetseite).")
        self.docs_button.clicked.connect(self._open_docs)
        doc_row.addWidget(self.docs_button)
        doc_row.addStretch(1)
        nib.addLayout(doc_row)
        layout.addWidget(self.not_installed_box)
        self.not_installed_box.setVisible(False)

        self.enable_check = QCheckBox("Lokale KI aktivieren")
        self.enable_check.setAccessibleName("Lokale KI aktivieren")
        self.enable_check.setToolTip(
            "Nur wenn aktiviert, darf die App den lokalen Ollama-Dienst für die Auswertung nutzen. "
            "Es werden nie Daten an fremde Server gesendet."
        )
        self.enable_check.toggled.connect(self._on_enabled)
        layout.addWidget(self.enable_check)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Modell"))
        self.model_combo = QComboBox()
        self.model_combo.setAccessibleName("KI-Modell")
        self.model_combo.setToolTip("Kleinere Modelle sind schneller, größere oft genauer.")
        for name, entry in MODEL_CATALOG.items():
            self.model_combo.addItem(entry.label, name)
        self.model_combo.currentIndexChanged.connect(self._on_model)
        model_row.addWidget(self.model_combo, 1)
        layout.addLayout(model_row)

        self.model_info = QLabel("")
        self.model_info.setWordWrap(True)
        self.model_info.setTextFormat(Qt.TextFormat.PlainText)
        self.model_info.setAccessibleName("Angaben zum Modell")
        layout.addWidget(self.model_info)

        hw_grid = QGridLayout()
        hw_grid.setHorizontalSpacing(16)
        self.hw_labels: dict[str, QLabel] = {}
        for idx, (key, title, tip) in enumerate(
            (
                ("ram", "Arbeitsspeicher (RAM)", "Arbeitsspeicher des Rechners (gesamt / frei)."),
                ("disk", "Freier Speicherplatz", "Freier Platz am Ordner, in dem Ollama Modelle ablegt."),
                ("cpu", "Prozessor", "Anzahl der Prozessorkerne und Plattform."),
            )
        ):
            t = QLabel(title)
            t.setToolTip(tip)
            v = QLabel("–")
            v.setAccessibleName(title)
            hw_grid.addWidget(t, idx, 0)
            hw_grid.addWidget(v, idx, 1)
            self.hw_labels[key] = v
        hw_grid.setColumnStretch(1, 1)
        layout.addLayout(hw_grid)

        buttons = QHBoxLayout()
        self.test_button = QPushButton("Lokale Laufzeit testen")
        self.test_button.setToolTip("Prüft, ob Ollama auf diesem Rechner läuft und das Modell vorhanden ist.")
        self.test_button.clicked.connect(self.check_status)
        buttons.addWidget(self.test_button)
        self.pull_button = QPushButton("Modell herunterladen …")
        self.pull_button.setToolTip(
            "Startet erst nach Ihrer ausdrücklichen Zustimmung. Der Download läuft über Ollama."
        )
        self.pull_button.clicked.connect(self.download_model)
        buttons.addWidget(self.pull_button)
        self.remove_button = danger_button(
            "Modell entfernen …", "Löscht das Modell aus Ollama (nach Rückfrage)."
        )
        self.remove_button.clicked.connect(self.remove_model)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.load_settings()

    # ------------------------------------------------------------------
    def load_settings(self) -> None:
        s = self.service.settings
        self._loading = True
        try:
            self.enable_check.setChecked(bool(s.ai_enabled))
            idx = self.model_combo.findData(s.ai_model)
            if idx < 0:
                self.model_combo.addItem(f"{s.ai_model} (eigene Angabe)", s.ai_model)
                idx = self.model_combo.count() - 1
            self.model_combo.setCurrentIndex(idx)
        finally:
            self._loading = False
        self.update_hardware()

    def selected_model(self) -> str:
        return str(self.model_combo.currentData() or self.service.settings.ai_model)

    def update_hardware(self) -> None:
        try:
            self.hardware = self.service.hardware_info()
        except Exception as exc:  # noqa: BLE001 – Anzeige ist nur informativ
            log.info("Hardware-Info nicht verfügbar (%s)", type(exc).__name__)
            self.hardware = HardwareInfo()
        hw = self.hardware
        ram = f"{gb_text(hw.total_ram_gb)} gesamt"
        if hw.available_ram_gb is not None:
            ram += f", {gb_text(hw.available_ram_gb)} frei"
        self.hw_labels["ram"].setText(ram)
        self.hw_labels["disk"].setText(f"{gb_text(hw.free_disk_gb)} (Modellordner)")
        self.hw_labels["disk"].setToolTip(hw.models_dir)
        cpu = f"{hw.cpu_count} Kerne" if hw.cpu_count else "unbekannt"
        self.hw_labels["cpu"].setText(f"{cpu} · {hw.platform}" if hw.platform else cpu)
        self.update_model_info()

    def update_model_info(self) -> None:
        name = self.selected_model()
        entry = MODEL_CATALOG.get(name)
        lines: list[str] = []
        if entry is not None:
            lines.append(f"Download: etwa {gb_text(entry.approx_download_gb)}")
            lines.append(
                f"Arbeitsspeicher: mindestens {entry.min_ram_gb:.0f} GB, empfohlen {entry.recommended_ram_gb:.0f} GB"
            )
            lines.append(f"Lizenz: {entry.license_hint}")
            if not entry.verified:
                lines.append(f"⚠ {UNVERIFIED_NOTE}")
        else:
            lines.append("Für dieses Modell liegen keine Angaben vor.")
        if self.hardware is not None:
            for hint in check_model_requirements(name, self.hardware):
                if hint != UNVERIFIED_NOTE and (entry is None or hint != entry.note):
                    lines.append(f"⚠ {hint}")
        self.model_info.setText("\n".join(lines))

    # ------------------------------------------------------------------
    def _on_enabled(self, checked: bool) -> None:
        if self._loading:
            return
        try:
            self.service.update_settings(ai_enabled=bool(checked))
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Einstellung nicht gespeichert", user_message(exc))
            return
        if self._on_settings_changed:
            self._on_settings_changed()
        self.check_status()

    def _on_model(self) -> None:
        self.update_model_info()
        if self._loading:
            return
        try:
            self.service.update_settings(ai_model=self.selected_model())
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Einstellung nicht gespeichert", user_message(exc))
            return
        self.check_status()

    # ------------------------------------------------------------------
    def check_status(self) -> None:
        """Status im Hintergrund ermitteln (nur Loopback, wirft nie)."""
        if self.checking or self.busy:  # während eines Downloads bleibt „Modell wird eingerichtet“ stehen
            return
        self.checking = True
        self.state_label.setText("◐ Status wird geprüft …")
        self._update_buttons()

        def done(status: LocalAiStatus) -> None:
            if not isValid(self):
                return
            self.checking = False
            self._status_task = None
            self.show_status(status)

        def failed(exc: BaseException) -> None:
            if not isValid(self):
                return
            self.checking = False
            self._status_task = None
            self.show_status(
                LocalAiStatus(
                    state=LocalAiState.ERROR, message=user_message(exc), model_name=self.selected_model()
                )
            )

        self._status_task = run_task(
            self,
            "Lokale KI wird geprüft …",
            lambda _p, _c: self.service.local_ai_status(),
            done,
            failed,
            show_progress=False,
            cancellable=False,
        )

    def show_status(self, status: LocalAiStatus) -> None:
        self.status = status
        sym, color = STATE_STYLE[status.state]
        text = f"{sym} {status.state.value}"
        if status.ollama_version:
            text += f"  ·  Ollama {status.ollama_version}"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"font-weight: 700; color: {color};")
        self.state_label.setAccessibleDescription(status.message)
        self.message_label.setText(status.message)
        self.not_installed_box.setVisible(not status.ollama_found and not status.ollama_running)
        self.docs_button.setEnabled(self._docs_path().is_file())
        self._update_buttons()

    def _update_buttons(self) -> None:
        running = bool(self.status and self.status.ollama_running)
        idle = not self.busy and not self.checking
        self.test_button.setEnabled(idle)
        self.pull_button.setEnabled(idle and running)
        self.remove_button.setEnabled(idle and running)
        self.enable_check.setEnabled(not self.busy)
        self.model_combo.setEnabled(not self.busy)
        tip = "" if running else " – Ollama läuft nicht oder wurde nicht gefunden."
        self.pull_button.setToolTip(
            "Startet erst nach Ihrer ausdrücklichen Zustimmung. Der Download läuft über Ollama." + tip
        )

    def _docs_path(self) -> Path:
        return Path(self.service.paths.resources) / "docs" / "LOKALE_KI.md"

    def _open_docs(self) -> None:
        path = self._docs_path()
        if path.is_file():
            dialogs.open_path(path)
        else:
            dialogs.show_info(
                self, "Anleitung", "Die Anleitung liegt im Programmordner unter „docs/LOKALE_KI.md“."
            )

    # ------------------------------------------------------------------
    def download_model(self) -> None:
        """Download nur nach ausdrücklicher Zustimmung im Dialog."""
        if self.busy:
            return
        name = self.selected_model()
        entry = MODEL_CATALOG.get(name)
        hw = self.hardware or HardwareInfo()
        info = consent_dialog.DownloadInfo(
            model=name,
            label=entry.label if entry else "",
            approx_gb=entry.approx_download_gb if entry else None,
            free_disk_gb=hw.free_disk_gb,
            license_hint=entry.license_hint if entry else "",
            verified=bool(entry and entry.verified),
            hints=check_model_requirements(name, hw),
        )
        if not consent_dialog.ask_download_consent(self, info):
            log.info("Modell-Download ohne Zustimmung nicht gestartet")
            return
        url = self.service.settings.ollama_url

        def pull(progress: Callable[[int, int, str], None], cancel: threading.Event) -> str:
            client = OllamaClient(url, timeout=10.0)

            def cb(status: str, completed: int | None, total: int | None) -> None:
                text = pull_status_text(status)
                if total:
                    done = min(max(int((completed or 0) * 1000 / total), 0), 1000)
                    progress(done, 1000, f"{text} – {done // 10} %")
                else:
                    progress(0, 1000, text)

            client.pull_model(name, user_consented=True, progress=cb, cancel=cancel)
            return name

        self._set_busy(True)
        self.state_label.setText(state_text(LocalAiState.SETTING_UP))
        self.message_label.setText(f"Modell „{name}“ wird eingerichtet. Das kann je nach Verbindung dauern.")
        self.task = run_task(
            self, f"Modell „{name}“ wird heruntergeladen …", pull, self._pull_done, self._pull_failed
        )

    def _pull_done(self, name: str) -> None:
        if not isValid(self):
            return
        self.task = None
        self._set_busy(False)
        dialogs.show_info(self, "Modell bereit", f"Der Download von „{name}“ ist abgeschlossen.")
        self.check_status()

    def _pull_failed(self, exc: BaseException) -> None:
        if not isValid(self):
            return
        self.task = None
        self._set_busy(False)
        if isinstance(exc, PullCancelledError):
            dialogs.show_info(self, "Download abgebrochen", CANCELLED_TEXT)
        elif isinstance(exc, OllamaError):
            dialogs.show_error(self, "Download fehlgeschlagen", str(exc))
        else:
            dialogs.show_error(self, "Download fehlgeschlagen", user_message(exc))
        self.check_status()

    def remove_model(self) -> None:
        if self.busy:
            return
        name = self.selected_model()
        if not dialogs.ask_confirm(
            self,
            "Modell entfernen?",
            f"Das Modell „{name}“ wird aus Ollama auf diesem Rechner gelöscht. Vorgänge und Dokumente bleiben "
            "erhalten. Für eine spätere Nutzung muss das Modell erneut heruntergeladen werden.",
            ok_text="Modell entfernen",
            danger=True,
        ):
            return
        url = self.service.settings.ollama_url

        def delete(_progress, _cancel) -> str:
            OllamaClient(url, timeout=30.0).delete_model(name, confirmed=True)
            return name

        def done(_name: str) -> None:
            if not isValid(self):
                return
            self.task = None
            self._set_busy(False)
            dialogs.show_info(self, "Modell entfernt", f"Das Modell „{name}“ wurde entfernt.")
            self.check_status()

        def failed(exc: BaseException) -> None:
            if not isValid(self):
                return
            self.task = None
            self._set_busy(False)
            msg = str(exc) if isinstance(exc, OllamaError) else user_message(exc)
            dialogs.show_error(self, "Entfernen fehlgeschlagen", msg)
            self.check_status()

        self._set_busy(True)
        self.task = run_task(self, "Modell wird entfernt …", delete, done, failed, cancellable=False)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._update_buttons()
