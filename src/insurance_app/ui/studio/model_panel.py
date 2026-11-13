"""Bereich „Lokale KI (optional)“ der Einstellungen.

Aufbau
1. Statuszeile aus ``service.ai_models_status()`` (Symbol + verständlicher Text) und Rollen-Übersicht.
2. „Lokale KI aktivieren“.
3. Dieses Gerät: Arbeitsspeicher, freier Speicherplatz, Prozessor + Empfehlung (``recommend_profile``).
4. Drei Profile (Kompakt / Standard / Maximal) mit Modellen, Downloadgröße und Anforderungen
   für macOS und Windows – ein Klick übernimmt das Profil (``apply_ai_profile``).
5. Eigene Modellwahl: aktives Modell, Ausweichmodell, Gegenprüfung (``set_ai_models``).
   Der Anwender entscheidet, welches Modell aktiv läuft.
6. Modelltabelle mit Aufgabe, Vorteil, Download, Lizenz, Installationsstatus und Aktion
   („Herunterladen …“ nur nach ausdrücklicher Zustimmung, „Entfernen …“ nur nach Rückfrage).

Netzwerk: Die Statusprüfung fragt nur den lokalen Ollama-Dienst (127.0.0.1). Download und
Entfernen starten ausschließlich nach Klick. Cloud-Modelle (``…:cloud``) werden nicht unterstützt.
Die App installiert keine Software; Ollama richtet die Anwenderin/der Anwender selbst ein.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ...local_ai.ollama_client import OllamaClient, OllamaError, PullCancelledError
from ...local_ai.profiles import PROFILES, AiProfile, recommend_profile
from ...local_ai.status import (
    MODEL_CATALOG,
    HardwareInfo,
    check_model_requirements,
    find_ollama_binary,
)
from .. import dialogs
from ..theme import ERROR, OK, SURFACE, TEXT_MUTED, WARN
from ..widgets.common import danger_button, muted, section_title
from ..workers import Task, run_task, user_message
from . import consent_dialog
from .common import gb_text

log = logging.getLogger(__name__)

CANCELLED_TEXT = (
    "Der Download wurde abgebrochen. Er kann später fortgesetzt werden – "
    "bereits geladene Teile behält Ollama."
)

LOCAL_ONLY_HINT = (
    "ⓘ Die App verwendet ausschließlich lokale Modelle auf diesem Rechner. Cloud-Modelle (Namen, die auf "
    "„:cloud“ enden, z. B. „gemma4:cloud“) werden nicht unterstützt und abgelehnt. Empfehlung: Ollama im "
    "reinen Lokalbetrieb betreiben – Umgebungsvariable OLLAMA_NO_CLOUD=1 setzen oder in "
    '~/.ollama/server.json den Eintrag {"disable_ollama_cloud": true} hinterlegen. '
    "Download und Entfernen startet die App nur nach Klick auf die jeweilige Schaltfläche; die Statusprüfung "
    "fragt nur den lokalen Ollama-Dienst (127.0.0.1)."
)

ROLE_LABELS = {"active": "aktiv", "fallback": "Ausweichmodell", "crosscheck": "Gegenprüfung"}
MODEL_COLUMNS = ("Modell", "Aufgabe", "Vorteil", "Download", "Lizenz", "Installiert", "Aktion")
NO_FALLBACK_TEXT = "– keines –"
NO_CROSSCHECK_TEXT = "– keine –"
INSTALLED_TEXT = "✔ installiert"
MISSING_TEXT = "○ nicht installiert"
UNKNOWN_TEXT = "? unbekannt"

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


def _tagged(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def ai_state_style(status: dict, enabled: bool) -> tuple[str, str]:
    """Symbol und Farbe zur Statusmeldung (nie nur Farbe)."""
    if not enabled:
        return "○", TEXT_MUTED
    if status.get("ready"):
        roles = status.get("roles") or {}
        if status.get("will_run") == roles.get("active"):
            return "✔", OK
        return "◐", WARN  # Ausweichmodell springt ein
    if not status.get("running"):
        return "✖", ERROR
    return "⚠", WARN


def profile_short_name(profile: AiProfile) -> str:
    return profile.label.split(" – ", 1)[0]


def profile_models_text(profile: AiProfile) -> str:
    parts = [f"{profile.active_model} (aktiv)"]
    if profile.fallback_model:
        parts.append(f"{profile.fallback_model} (Ausweichmodell)")
    if profile.crosscheck_model:
        parts.append(f"{profile.crosscheck_model} (Gegenprüfung)")
    return "Modelle: " + " · ".join(parts)


def recommendation_text(key: str | None, reason: str) -> str:
    reason = reason.strip().rstrip(".")
    if key is None:
        return f"Empfehlung für dieses Gerät: ohne lokale KI arbeiten ({reason})."
    label = PROFILES[key].label.replace(" (empfohlen)", "")
    return f"Empfehlung für dieses Gerät: {label} ({reason})."


class LocalAiPanel(QWidget):
    """Status, Ein/Aus, Hardware + Empfehlung, Profile, Modellwahl, Modelltabelle mit Download/Entfernen."""

    def __init__(self, service, on_settings_changed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.service = service
        self._on_settings_changed = on_settings_changed
        self.status: dict | None = None
        self.installed: dict[str, bool] = {}
        self.ollama_found: bool | None = None
        self.hardware: HardwareInfo | None = None
        self.recommendation: tuple[str | None, str] | None = None
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
                "Optional: Lokale KI-Modelle lesen schwer lesbare Seiten zusätzlich. Sie laufen vollständig auf "
                "diesem Rechner über das Programm Ollama. Die App funktioniert auch ganz ohne KI. "
                "KI-Vorschläge müssen Sie immer selbst prüfen – Abweichungen erscheinen als Konflikt."
            )
        )
        self._build_status(layout)
        self._build_hardware(layout)
        self._build_profiles(layout)
        self._build_model_choice(layout)
        self._build_model_table(layout)
        self.cloud_hint = muted(LOCAL_ONLY_HINT)
        self.cloud_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cloud_hint.setAccessibleName("Hinweis: nur lokale Modelle")
        layout.addWidget(self.cloud_hint)

        self.load_settings()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def _build_status(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self.state_label = QLabel("○ Status noch nicht geprüft")
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("font-weight: 700;")
        self.state_label.setAccessibleName("Status der lokalen KI")
        row.addWidget(self.state_label, 1)
        self.test_button = QPushButton("Status prüfen")
        self.test_button.setToolTip(
            "Fragt den lokalen Ollama-Dienst (127.0.0.1), ob er läuft und welche Modelle vorhanden sind."
        )
        self.test_button.clicked.connect(self.check_status)
        row.addWidget(self.test_button)
        layout.addLayout(row)
        self.detail_label = muted("")
        self.detail_label.setAccessibleName("Rollen der Modelle")
        layout.addWidget(self.detail_label)

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

    def _build_hardware(self, layout: QVBoxLayout) -> None:
        box = QFrame(objectName="AiHardwareBox")
        box.setStyleSheet(
            "#AiHardwareBox { background: #F3F6FA; border: 1px solid #D5DBE3; border-radius: 8px; }"
            "#AiHardwareBox QLabel { border: none; background: transparent; }"
        )
        bl = QVBoxLayout(box)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(6)
        title = QLabel("Dieses Gerät")
        title.setStyleSheet("font-weight: 700;")
        bl.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.hw_labels: dict[str, QLabel] = {}
        for idx, (key, name, tip) in enumerate(
            (
                ("ram", "Arbeitsspeicher (RAM)", "Arbeitsspeicher des Rechners (gesamt / frei)."),
                ("disk", "Freier Speicherplatz", "Freier Platz am Ordner, in dem Ollama Modelle ablegt."),
                ("cpu", "Prozessor", "Anzahl der Prozessorkerne und Plattform."),
            )
        ):
            t = QLabel(name)
            t.setToolTip(tip)
            v = QLabel("–")
            v.setAccessibleName(name)
            grid.addWidget(t, idx, 0)
            grid.addWidget(v, idx, 1)
            self.hw_labels[key] = v
        grid.setColumnStretch(1, 1)
        bl.addLayout(grid)
        self.recommendation_label = QLabel("")
        self.recommendation_label.setWordWrap(True)
        self.recommendation_label.setStyleSheet("font-weight: 600;")
        self.recommendation_label.setAccessibleName("Empfehlung für dieses Gerät")
        bl.addWidget(self.recommendation_label)
        bl.addWidget(
            muted(
                "Richtwerte, nicht auf jedem Gerät gemessen. Eine Grafikkarte (bzw. Apple Silicon) macht die "
                "Auswertung deutlich schneller; ohne sie laufen große Modelle langsam."
            )
        )
        layout.addWidget(box)

    def _build_profiles(self, layout: QVBoxLayout) -> None:
        layout.addWidget(section_title("Profile"))
        layout.addWidget(
            muted(
                "Ein Profil setzt aktives Modell, Ausweichmodell und Gegenprüfung auf einmal. "
                "Sie können die Auswahl danach unter „Modellauswahl“ frei ändern."
            )
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        self.profile_buttons: dict[str, QPushButton] = {}
        self.profile_badges: dict[str, tuple[QLabel, QLabel]] = {}
        for key, prof in PROFILES.items():
            row.addWidget(self._profile_card(key, prof), 1)
        layout.addLayout(row)

    def _profile_card(self, key: str, prof: AiProfile) -> QFrame:
        frame = QFrame(objectName="ProfileCard")
        frame.setStyleSheet(
            f"#ProfileCard {{ background: {SURFACE}; border: 1px solid #D5DBE3; border-radius: 8px; }}"
            "#ProfileCard QLabel { border: none; background: transparent; }"
        )
        frame.setAccessibleName(f"Profil {profile_short_name(prof)}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(5)
        title = QLabel(prof.label)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 700;")
        lay.addWidget(title)
        rec = QLabel("★ Empfehlung für dieses Gerät")
        rec.setStyleSheet(f"color: {OK}; font-weight: 600;")
        rec.setVisible(False)
        lay.addWidget(rec)
        cur = QLabel("✔ entspricht der aktuellen Auswahl")
        cur.setStyleSheet(f"color: {OK};")
        cur.setVisible(False)
        lay.addWidget(cur)
        self.profile_badges[key] = (rec, cur)
        lay.addWidget(muted(prof.description))
        req = prof.requirement
        for text in (
            profile_models_text(prof),
            f"Download gesamt: etwa {gb_text(prof.approx_download_gb)}",
            f"macOS: {req.mac}",
            f"Windows: {req.windows}",
            f"Speicherplatz: mindestens {gb_text(req.disk_gb)} frei",
        ):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
        lay.addStretch(1)
        btn = QPushButton(f"Profil „{profile_short_name(prof)}“ übernehmen")
        btn.setToolTip(
            "Setzt aktives Modell, Ausweichmodell und Gegenprüfung dieses Profils (kein Download)."
        )
        btn.clicked.connect(lambda _=False, k=key: self.apply_profile(k))
        lay.addWidget(btn)
        self.profile_buttons[key] = btn
        return frame

    def _model_combo(self, empty_text: str | None, name: str) -> QComboBox:
        combo = QComboBox()
        combo.setAccessibleName(name)
        if empty_text is not None:
            combo.addItem(empty_text, None)
        for model, entry in MODEL_CATALOG.items():
            combo.addItem(f"{model} – {entry.label}", model)
            combo.setItemData(combo.count() - 1, entry.role, Qt.ItemDataRole.ToolTipRole)
        combo.currentIndexChanged.connect(self._on_models_changed)
        return combo

    def _build_model_choice(self, layout: QVBoxLayout) -> None:
        layout.addWidget(section_title("Modellauswahl"))
        layout.addWidget(
            muted(
                "Sie entscheiden, welches Modell aktiv läuft. Fehlt das aktive Modell oder scheitert es an einer "
                "Seite, springt das Ausweichmodell ein. Die Gegenprüfung liest zusätzlich unabhängig – weichen "
                "die Ergebnisse ab, markiert die App das Feld als Konflikt."
            )
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        self.active_combo = self._model_combo(None, "Aktives Modell")
        self.fallback_combo = self._model_combo(NO_FALLBACK_TEXT, "Ausweichmodell")
        self.crosscheck_combo = self._model_combo(NO_CROSSCHECK_TEXT, "Gegenprüfung")
        for idx, (text, combo, tip) in enumerate(
            (
                ("Aktives Modell", self.active_combo, "Läuft zuerst (Standard: glm-ocr)."),
                (
                    "Ausweichmodell",
                    self.fallback_combo,
                    "Springt ein, wenn das aktive Modell fehlt oder scheitert.",
                ),
                (
                    "Gegenprüfung",
                    self.crosscheck_combo,
                    "Optionales zweites Modell für eine unabhängige Prüfung.",
                ),
            )
        ):
            lbl = QLabel(text)
            lbl.setToolTip(tip)
            combo.setToolTip(tip)
            combo.setMaximumWidth(560)
            grid.addWidget(lbl, idx, 0)
            grid.addWidget(combo, idx, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.models_feedback = muted("")
        self.models_feedback.setAccessibleName("Rückmeldung zur Modellauswahl")
        layout.addWidget(self.models_feedback)

    def _build_model_table(self, layout: QVBoxLayout) -> None:
        layout.addWidget(section_title("Modelle"))
        table = QTableWidget(len(MODEL_CATALOG), len(MODEL_COLUMNS))
        table.setHorizontalHeaderLabels(list(MODEL_COLUMNS))
        table.setAccessibleName("Lokale KI-Modelle")
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setWordWrap(True)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for col, width in ((0, 140), (3, 115), (4, 135), (5, 115), (6, 150)):
            table.setColumnWidth(col, width)
        table.horizontalHeaderItem(3).setToolTip(
            "Ungefähre Downloadgröße; „laut Ollama-Registry“ = aus dem Registry-Manifest übernommen"
        )
        self.model_table = table
        hdr.sectionResized.connect(lambda *_: QTimer.singleShot(0, self._fit_table))
        self.pull_buttons: dict[str, QPushButton] = {}
        self.remove_buttons: dict[str, QPushButton] = {}
        for row, (name, entry) in enumerate(MODEL_CATALOG.items()):
            item = QTableWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(entry.label)
            table.setItem(row, 0, item)
            table.setItem(row, 1, QTableWidgetItem(entry.role))
            table.setItem(row, 2, QTableWidgetItem(entry.advantages))
            size = f"etwa {gb_text(entry.approx_download_gb)}\n" + (
                "laut Ollama-Registry" if entry.verified else "⚠ ungeprüft"
            )
            size_item = QTableWidgetItem(size)
            size_item.setToolTip(entry.note)
            table.setItem(row, 3, size_item)
            table.setItem(row, 4, QTableWidgetItem(entry.license_hint))
            table.setItem(row, 5, QTableWidgetItem(UNKNOWN_TEXT))
            cell = QWidget()
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(4, 4, 4, 4)
            pull = QPushButton("Herunterladen …")
            pull.setAccessibleName(f"{name} herunterladen")
            pull.clicked.connect(lambda _=False, n=name: self.download_model(n))
            remove = danger_button(
                "Entfernen …", f"„{name}“ aus Ollama auf diesem Rechner löschen (nach Rückfrage)"
            )
            remove.setAccessibleName(f"{name} entfernen")
            remove.clicked.connect(lambda _=False, n=name: self.remove_model(n))
            cl.addWidget(pull)
            cl.addWidget(remove)
            cl.addStretch(1)
            table.setCellWidget(row, 6, cell)
            self.pull_buttons[name] = pull
            self.remove_buttons[name] = remove
        layout.addWidget(table)
        QTimer.singleShot(0, self._fit_table)

    def _fit_table(self) -> None:
        if not isValid(self):
            return
        table = self.model_table
        table.resizeRowsToContents()
        height = table.horizontalHeader().height() + 2 * table.frameWidth() + 2
        for row in range(table.rowCount()):
            table.setRowHeight(row, max(table.rowHeight(row) + 8, 78))
            height += table.rowHeight(row)
        table.setFixedHeight(height)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_table)

    # ------------------------------------------------------------------
    # Einstellungen lesen
    # ------------------------------------------------------------------
    @staticmethod
    def _select(combo: QComboBox, value: str | None) -> None:
        idx = combo.findData(value)
        if idx < 0 and value:
            combo.addItem(f"{value} (eigene Angabe)", value)
            idx = combo.count() - 1
        combo.setCurrentIndex(max(idx, 0))

    def load_settings(self) -> None:
        s = self.service.settings
        self._loading = True
        try:
            self.enable_check.setChecked(bool(s.ai_enabled))
            self._select(self.active_combo, s.ai_model)
            self._select(self.fallback_combo, s.ai_fallback_model)
            self._select(self.crosscheck_combo, s.ai_crosscheck_model)
        finally:
            self._loading = False
        self.update_hardware()
        self._update_profile_badges()
        self._render_table()

    def selected_models(self) -> tuple[str, str | None, str | None]:
        active = str(self.active_combo.currentData() or self.service.settings.ai_model)
        fallback = self.fallback_combo.currentData()
        cross = self.crosscheck_combo.currentData()
        return active, (str(fallback) if fallback else None), (str(cross) if cross else None)

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
        self.hw_labels["disk"].setText(f"{gb_text(hw.free_disk_gb)} frei (Modellordner)")
        self.hw_labels["disk"].setToolTip(hw.models_dir)
        cpu = f"{hw.cpu_count} Kerne" if hw.cpu_count else "unbekannt"
        self.hw_labels["cpu"].setText(f"{cpu} · {hw.platform}" if hw.platform else cpu)
        try:
            self.recommendation = recommend_profile(hw)
        except Exception as exc:  # noqa: BLE001
            log.info("Empfehlung nicht möglich (%s)", type(exc).__name__)
            self.recommendation = (None, "Hardware konnte nicht bewertet werden")
        key, reason = self.recommendation
        self.recommendation_label.setText(recommendation_text(key, reason))
        self.recommendation_label.setStyleSheet(f"font-weight: 600; color: {OK if key else WARN};")
        self._update_profile_badges()

    def _update_profile_badges(self) -> None:
        if not hasattr(self, "profile_badges"):
            return
        s = self.service.settings
        current = (s.ai_model, s.ai_fallback_model, s.ai_crosscheck_model)
        rec_key = self.recommendation[0] if self.recommendation else None
        for key, (rec, cur) in self.profile_badges.items():
            prof = PROFILES[key]
            rec.setVisible(key == rec_key)
            cur.setVisible(current == (prof.active_model, prof.fallback_model, prof.crosscheck_model))

    # ------------------------------------------------------------------
    # Änderungen
    # ------------------------------------------------------------------
    def _settings_changed(self) -> None:
        if self._on_settings_changed:
            self._on_settings_changed()

    def _on_enabled(self, checked: bool) -> None:
        if self._loading:
            return
        try:
            self.service.update_settings(ai_enabled=bool(checked))
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Einstellung nicht gespeichert", user_message(exc))
            return
        self._settings_changed()
        self.check_status()

    def apply_profile(self, key: str) -> None:
        if self.busy:
            return
        try:
            self.service.apply_ai_profile(key)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Profil nicht übernommen", user_message(exc))
            return
        self.load_settings()
        self.models_feedback.setStyleSheet(f"color: {OK};")
        self.models_feedback.setText(
            f"✔ Profil „{profile_short_name(PROFILES[key])}“ übernommen: {self._models_summary()}"
        )
        self._settings_changed()
        self.check_status()

    def _models_summary(self) -> str:
        s = self.service.settings
        return (
            f"aktiv {s.ai_model}, Ausweichmodell {s.ai_fallback_model or '–'}, "
            f"Gegenprüfung {s.ai_crosscheck_model or '–'}."
        )

    def _on_models_changed(self) -> None:
        if self._loading:
            return
        active, fallback, cross = self.selected_models()
        try:
            self.service.set_ai_models(active, fallback, cross)
        except Exception as exc:  # noqa: BLE001
            self.models_feedback.setStyleSheet(f"color: {ERROR};")
            self.models_feedback.setText(f"✖ {user_message(exc)}")
            self.load_settings()
            return
        note = ""
        if fallback and fallback == active:
            note = " Das Ausweichmodell entsprach dem aktiven Modell und wurde auf „keines“ gesetzt."
        self.load_settings()
        self.models_feedback.setStyleSheet(f"color: {OK};")
        self.models_feedback.setText(f"✔ Gespeichert: {self._models_summary()}{note}")
        self._settings_changed()
        self.check_status()

    # ------------------------------------------------------------------
    # Status (nur Loopback, im Hintergrund)
    # ------------------------------------------------------------------
    def check_status(self) -> None:
        if self.checking or self.busy:
            return
        self.checking = True
        self.state_label.setText("◐ Status wird geprüft …")
        self.state_label.setStyleSheet(f"font-weight: 700; color: {TEXT_MUTED};")
        self._update_buttons()
        service = self.service
        url = service.settings.ollama_url

        def work(_progress, _cancel) -> tuple[dict, dict[str, bool], bool]:
            status = service.ai_models_status()
            installed: dict[str, bool] = {}
            if status.get("running"):
                try:
                    names = {_tagged(m.name) for m in OllamaClient(url, timeout=3.0).list_models()}
                    installed = {name: _tagged(name) in names for name in MODEL_CATALOG}
                except (OllamaError, ValueError) as exc:
                    log.info("Modellliste nicht lesbar (%s)", type(exc).__name__)
            installed.update({k: bool(v) for k, v in (status.get("models") or {}).items()})
            found = bool(status.get("running")) or find_ollama_binary() is not None
            return status, installed, found

        def done(result: tuple[dict, dict[str, bool], bool]) -> None:
            if not isValid(self):
                return
            self.checking = False
            self._status_task = None
            self.show_status(*result)

        def failed(exc: BaseException) -> None:
            if not isValid(self):
                return
            self.checking = False
            self._status_task = None
            self.show_status(
                {"running": False, "ready": False, "models": {}, "roles": {}, "message": user_message(exc)},
                {},
                bool(find_ollama_binary()),
            )

        self._status_task = run_task(
            self, "Lokale KI wird geprüft …", work, done, failed, show_progress=False, cancellable=False
        )

    def show_status(self, status: dict, installed: dict[str, bool] | None = None, found: bool = True) -> None:
        self.status = status
        self.installed = dict(installed or {})
        self.ollama_found = found
        enabled = bool(self.service.settings.ai_enabled)
        sym, color = ai_state_style(status, enabled)
        message = str(status.get("message") or "")
        self.state_label.setText(f"{sym} {message}")
        self.state_label.setStyleSheet(f"font-weight: 700; color: {color};")
        self.state_label.setAccessibleDescription(message)
        self.detail_label.setText(self._roles_text(status))
        self.not_installed_box.setVisible(not found and not status.get("running"))
        self.docs_button.setEnabled(self._docs_path().is_file())
        self._render_table()

    def _roles_text(self, status: dict) -> str:
        running = bool(status.get("running"))
        models = status.get("models") or {}
        s = self.service.settings
        parts = [f"Ollama: {'✔ läuft' if running else '✖ nicht erreichbar'} (nur lokal)"]
        for role, name in (
            ("active", s.ai_model),
            ("fallback", s.ai_fallback_model),
            ("crosscheck", s.ai_crosscheck_model),
        ):
            label = ROLE_LABELS[role].capitalize() if role == "active" else ROLE_LABELS[role]
            if not name:
                parts.append(f"{label}: –")
            elif running:
                mark = "✔ vorhanden" if models.get(name) else "○ fehlt"
                parts.append(f"{label}: {name} ({mark})")
            else:
                parts.append(f"{label}: {name}")
        return " · ".join(parts)

    def _render_table(self) -> None:
        if not hasattr(self, "model_table"):
            return
        s = self.service.settings
        roles = {
            s.ai_model: "aktiv",
            s.ai_fallback_model: "Ausweichmodell",
            s.ai_crosscheck_model: "Gegenprüfung",
        }
        running = bool(self.status and self.status.get("running"))
        for row in range(self.model_table.rowCount()):
            name = self.model_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            role = roles.get(name)
            self.model_table.item(row, 0).setText(f"{name}\n({role})" if role else name)
            if not running or name not in self.installed:
                text, color = UNKNOWN_TEXT, TEXT_MUTED
                if self.status is not None and not running:
                    text = "? Ollama läuft nicht"
            elif self.installed[name]:
                text, color = INSTALLED_TEXT, OK
            else:
                text, color = MISSING_TEXT, TEXT_MUTED
            item = self.model_table.item(row, 5)
            item.setText(text)
            item.setForeground(QBrush(QColor(color)))
        self._update_buttons()
        QTimer.singleShot(0, self._fit_table)

    def is_installed(self, name: str) -> bool | None:
        if not (self.status and self.status.get("running")) or name not in self.installed:
            return None
        return self.installed[name]

    def _update_buttons(self) -> None:
        running = bool(self.status and self.status.get("running"))
        idle = not self.busy and not self.checking
        self.test_button.setEnabled(idle)
        self.enable_check.setEnabled(not self.busy)
        for combo in (self.active_combo, self.fallback_combo, self.crosscheck_combo):
            combo.setEnabled(not self.busy)
        for btn in self.profile_buttons.values():
            btn.setEnabled(not self.busy)
        why = "" if running else " – Ollama läuft nicht oder wurde nicht gefunden."
        for name, pull in self.pull_buttons.items():
            installed = self.is_installed(name)
            remove = self.remove_buttons[name]
            pull.setVisible(installed is not True)
            remove.setVisible(installed is True)
            pull.setEnabled(idle and running and installed is False)
            remove.setEnabled(idle and running and installed is True)
            pull.setToolTip(
                "Startet erst nach Ihrer ausdrücklichen Zustimmung. Den Download führt der lokale "
                "Ollama-Dienst aus der Ollama-Bibliothek aus." + why
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
    # Download / Entfernen (nur nach Klick)
    # ------------------------------------------------------------------
    def download_model(self, name: str) -> None:
        """Download nur nach Platzprüfung und ausdrücklicher Zustimmung im Dialog."""
        if self.busy:
            return
        entry = MODEL_CATALOG.get(name)
        hw = self.hardware or HardwareInfo()
        if entry is not None and hw.free_disk_gb is not None and hw.free_disk_gb < entry.approx_download_gb:
            dialogs.show_error(
                self,
                "Zu wenig Speicherplatz",
                f"Für „{name}“ werden etwa {gb_text(entry.approx_download_gb)} benötigt, am Modellordner sind "
                f"nur {gb_text(hw.free_disk_gb)} frei. Bitte zuerst Platz schaffen.",
            )
            return
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
        self.state_label.setText(f"◐ Modell „{name}“ wird eingerichtet – das kann je nach Verbindung dauern.")
        self.state_label.setStyleSheet(f"font-weight: 700; color: {WARN};")
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

    def remove_model(self, name: str) -> None:
        if self.busy:
            return
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
