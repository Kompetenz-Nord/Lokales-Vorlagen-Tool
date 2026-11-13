"""Seite „Einstellungen & Datenschutz“.

Karten: Datenschutz & lokale Ablage · Texterkennung (OCR) · Lokale KI (optional) ·
Export · Exportprotokoll. Alles bleibt auf diesem Rechner. Die Seite löst keine
Netzwerkzugriffe aus – außer der Statusprüfung des lokalen Ollama-Dienstes
(nur 127.0.0.1) und einem Modell-Download nach ausdrücklicher Zustimmung.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
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
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.service import AppService
from .. import dialogs
from ..studio.common import format_dt, human_size
from ..studio.model_panel import LocalAiPanel
from ..theme import ERROR, LOCAL_ONLY_TEXT, OK, TEXT_MUTED
from ..widgets.common import card, muted, page_title, primary_button, section_title
from ..workers import user_message

log = logging.getLogger(__name__)

PRIVACY_TEXT = (
    "Diese App arbeitet ausschließlich auf diesem Rechner:\n"
    "• Keine Cloud, keine Telemetrie (keine Nutzungsdaten), keine automatische Übermittlung an Dritte.\n"
    "• Dokumente, erkannte Texte und Ergebnisse liegen nur im Datenordner und im Exportordner.\n"
    "• Protokolle (Logs) enthalten keine Personendaten – nur technische Angaben wie Zeit und Status.\n"
    "• Temporäre Dateien (z. B. Vorschauen) werden nach Gebrauch gelöscht; Reste räumt die App beim Start auf."
)
RESPONSIBILITY_TEXT = (
    "Die App schützt die Daten technisch (lokal, ohne Übertragung). Die Verantwortung für den "
    "datenschutzkonformen Einsatz (z. B. Verarbeitungsverzeichnis, Löschfristen) liegt beim Betreiber."
)

OCR_LABELS = {
    "tesseract": "Tesseract (Standard)",
    "rapidocr": "RapidOCR (optional)",
    "docling": "Docling (optional)",
}
LANGUAGE_NAMES = {"deu": "Deutsch", "eng": "Englisch", "fra": "Französisch", "ita": "Italienisch"}

EXPORT_LOG_COLUMNS = ("Zeit", "Vorgang", "Vorlage", "Version", "Datei", "Fixiert", "QA")


class SettingsPage(QWidget):
    def __init__(self, service: AppService) -> None:
        super().__init__()
        self.service = service
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(page_title("Einstellungen & Datenschutz"), 1)
        local = QLabel(LOCAL_ONLY_TEXT, objectName="LocalBadge")
        local.setToolTip("Alle Daten bleiben auf diesem Gerät.")
        local.setAccessibleName(LOCAL_ONLY_TEXT)
        head.addWidget(local)
        outer.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(objectName="SettingsContent")
        self.scroll.viewport().setObjectName("SettingsViewport")
        self.scroll.setStyleSheet("#SettingsViewport, #SettingsContent { background: transparent; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 8, 12)
        self.content_layout.setSpacing(14)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)

        self._build_privacy()
        self._build_ocr()
        self._build_ai()
        self._build_export()
        self._build_export_log()
        self.content_layout.addStretch(1)

        self.refresh_local()  # Status der lokalen KI erst beim Anzeigen (refresh) prüfen

    # ------------------------------------------------------------------
    # Datenschutz & Ablage
    # ------------------------------------------------------------------
    def _build_privacy(self) -> None:
        frame, lay = card()
        frame.setAccessibleName("Datenschutz und lokale Ablage")
        lay.addWidget(section_title("Datenschutz & lokale Ablage"))
        self.local_only_label = QLabel(LOCAL_ONLY_TEXT)
        self.local_only_label.setStyleSheet(f"color: {OK}; font-weight: 600;")
        lay.addWidget(self.local_only_label)
        self.privacy_label = QLabel(PRIVACY_TEXT)
        self.privacy_label.setWordWrap(True)
        lay.addWidget(self.privacy_label)
        self.responsibility_label = muted(RESPONSIBILITY_TEXT)
        lay.addWidget(self.responsibility_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.storage_labels: dict[str, QLabel] = {}
        rows = (
            ("data_dir", "Datenordner", "Vorgänge, Dokumentkopien, Datenbank, Protokolle."),
            ("export_dir", "Exportordner", "Hier landen die fertigen PDFs."),
            ("db", "Datenbank", "Lokale SQLite-Datei mit Vorgängen und Verlauf (Größe)."),
            ("counts", "Bestand", "Anzahl Vorgänge, Dokumente und Exporte."),
        )
        for idx, (key, title, tip) in enumerate(rows):
            t = QLabel(title)
            t.setToolTip(tip)
            t.setStyleSheet("font-weight: 600;")
            v = QLabel("–")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setAccessibleName(title)
            grid.addWidget(t, idx, 0)
            grid.addWidget(v, idx, 1)
            self.storage_labels[key] = v
        self.open_data_button = QPushButton("Ordner öffnen")
        self.open_data_button.setAccessibleName("Datenordner öffnen")
        self.open_data_button.clicked.connect(lambda: self._open_dir("data_dir"))
        grid.addWidget(self.open_data_button, 0, 2)
        self.open_export_button = QPushButton("Ordner öffnen")
        self.open_export_button.setAccessibleName("Exportordner öffnen")
        self.open_export_button.clicked.connect(lambda: self._open_dir("export_dir"))
        grid.addWidget(self.open_export_button, 1, 2)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        row = QHBoxLayout()
        self.export_dir_button = QPushButton("Exportordner ändern …")
        self.export_dir_button.setToolTip("Einen anderen lokalen Ordner für fertige PDFs wählen.")
        self.export_dir_button.clicked.connect(self.change_export_dir)
        row.addWidget(self.export_dir_button)
        self.export_dir_reset = QPushButton("Standardordner verwenden")
        self.export_dir_reset.setToolTip("Exportordner auf den Standard zurücksetzen.")
        self.export_dir_reset.clicked.connect(self.reset_export_dir)
        row.addWidget(self.export_dir_reset)
        row.addStretch(1)
        lay.addLayout(row)
        self._storage: dict = {}
        self.content_layout.addWidget(frame)

    def refresh_storage(self) -> None:
        try:
            info = self.service.storage_overview()
        except Exception as exc:  # noqa: BLE001
            log.warning("Speicherübersicht nicht verfügbar (%s)", type(exc).__name__)
            info = {}
        self._storage = info
        self.storage_labels["data_dir"].setText(str(info.get("data_dir", "–")))
        self.storage_labels["export_dir"].setText(str(info.get("export_dir", "–")))
        self.storage_labels["db"].setText(
            f"{human_size(info.get('database_size_bytes'))} · Dokumentkopien: "
            f"{human_size(info.get('documents_size_bytes'))}"
        )
        cases = int(info.get("case_count", 0))
        docs = int(info.get("document_count", 0))
        exports = int(info.get("export_count", 0))
        self.storage_labels["counts"].setText(
            f"{cases} {'Vorgang' if cases == 1 else 'Vorgänge'} "
            f"(davon {info.get('archived_case_count', 0)} archiviert) · "
            f"{docs} {'Dokument' if docs == 1 else 'Dokumente'} · {exports} {'Export' if exports == 1 else 'Exporte'}"
        )
        self.export_dir_reset.setEnabled(bool(self.service.settings.export_dir))

    def _open_dir(self, key: str) -> None:
        value = self._storage.get(key)
        if not value:
            return
        path = Path(value)
        path.mkdir(parents=True, exist_ok=True)
        dialogs.open_path(path)

    def change_export_dir(self) -> None:
        start = self.service.export_dir
        path = dialogs.pick_directory(self, "Exportordner wählen", start)
        if path is None:
            return
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".schreibtest"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            dialogs.show_error(
                self,
                "Exportordner",
                "In diesen Ordner kann nicht geschrieben werden. Bitte einen anderen wählen.",
            )
            return
        try:
            self.service.update_settings(export_dir=str(path))
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Exportordner", user_message(exc))
            return
        self.refresh_storage()

    def reset_export_dir(self) -> None:
        self.service.update_settings(export_dir=None)
        self.refresh_storage()

    # ------------------------------------------------------------------
    # OCR
    # ------------------------------------------------------------------
    def _build_ocr(self) -> None:
        frame, lay = card()
        frame.setAccessibleName("Texterkennung")
        lay.addWidget(section_title("Texterkennung (OCR)"))
        lay.addWidget(
            muted(
                "OCR (optische Zeichenerkennung) liest Text aus gescannten Seiten. Seiten mit eigener Textebene "
                "werden direkt gelesen – OCR ist nur für Scans nötig."
            )
        )
        self.ocr_status_labels: dict[str, QLabel] = {}
        for name in OCR_LABELS:
            lbl = QLabel("")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
            self.ocr_status_labels[name] = lbl

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.addWidget(QLabel("Verfahren"), 0, 0)
        self.ocr_backend_combo = QComboBox()
        self.ocr_backend_combo.setAccessibleName("OCR-Verfahren")
        self.ocr_backend_combo.setMaximumWidth(420)
        for name, label in OCR_LABELS.items():
            self.ocr_backend_combo.addItem(label, name)
        grid.addWidget(self.ocr_backend_combo, 0, 1)

        dpi_label = QLabel("Auflösung")
        dpi_label.setToolTip(
            "dpi = Bildpunkte je Zoll. 300 dpi ist ein guter Standard; höher ist genauer, aber langsamer."
        )
        grid.addWidget(dpi_label, 1, 0)
        self.ocr_dpi_spin = QSpinBox()
        self.ocr_dpi_spin.setRange(150, 600)
        self.ocr_dpi_spin.setSingleStep(50)
        self.ocr_dpi_spin.setSuffix(" dpi")
        self.ocr_dpi_spin.setAccessibleName("OCR-Auflösung in dpi")
        self.ocr_dpi_spin.setMaximumWidth(160)
        self.ocr_dpi_spin.setToolTip(dpi_label.toolTip())
        grid.addWidget(self.ocr_dpi_spin, 1, 1)

        grid.addWidget(QLabel("Tesseract-Programm"), 2, 0)
        tess_row = QHBoxLayout()
        self.tesseract_label = QLabel("Automatisch")
        self.tesseract_label.setAccessibleName("Pfad zum Tesseract-Programm")
        self.tesseract_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tess_row.addWidget(self.tesseract_label, 1)
        self.tesseract_pick = QPushButton("Datei wählen …")
        self.tesseract_pick.setToolTip("Nur nötig, wenn Tesseract nicht automatisch gefunden wird.")
        self.tesseract_pick.clicked.connect(self.pick_tesseract)
        tess_row.addWidget(self.tesseract_pick)
        self.tesseract_auto = QPushButton("Automatisch")
        self.tesseract_auto.setToolTip("Tesseract automatisch suchen (Standard).")
        self.tesseract_auto.clicked.connect(lambda: self._set_tesseract(None))
        tess_row.addWidget(self.tesseract_auto)
        grid.addLayout(tess_row, 2, 1)

        grid.addWidget(QLabel("Sprachen"), 3, 0)
        self.languages_label = QLabel("")
        self.languages_label.setToolTip("Sprachpakete für die Texterkennung (in Tesseract installiert).")
        grid.addWidget(self.languages_label, 3, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        row = QHBoxLayout()
        self.ocr_apply = primary_button("Übernehmen", "OCR-Einstellungen speichern.")
        self.ocr_apply.clicked.connect(self.apply_ocr)
        row.addWidget(self.ocr_apply)
        self.ocr_feedback = muted("")
        row.addWidget(self.ocr_feedback, 1)
        lay.addLayout(row)
        self._tesseract_cmd: str | None = None
        self.content_layout.addWidget(frame)

    def refresh_ocr(self) -> None:
        try:
            status = self.service.ocr_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("OCR-Status nicht verfügbar (%s)", type(exc).__name__)
            status = {}
        for name, label in OCR_LABELS.items():
            ok, reason = status.get(name, (False, "Status unbekannt."))
            sym = "✔" if ok else "○"
            lbl = self.ocr_status_labels[name]
            lbl.setText(f"{sym} {label}: {reason}")
            lbl.setStyleSheet(f"color: {OK if ok else TEXT_MUTED};")
            lbl.setAccessibleName(f"{label}: {'bereit' if ok else 'nicht verfügbar'}")
        s = self.service.settings
        model = self.ocr_backend_combo.model()
        for i in range(self.ocr_backend_combo.count()):
            name = self.ocr_backend_combo.itemData(i)
            ok = status.get(name, (False, ""))[0]
            item = model.item(i) if hasattr(model, "item") else None
            if item is not None:
                selectable = ok or name == s.ocr_backend
                item.setEnabled(selectable)
                suffix = "" if ok else " – nicht verfügbar"
                item.setText(OCR_LABELS[name] + suffix)
        self.ocr_backend_combo.setCurrentIndex(max(self.ocr_backend_combo.findData(s.ocr_backend), 0))
        self.ocr_dpi_spin.setValue(int(s.ocr_dpi))
        self._set_tesseract(s.tesseract_cmd, apply=False)
        langs = [LANGUAGE_NAMES.get(code, code) for code in s.ocr_languages.split("+") if code]
        self.languages_label.setText(" + ".join(langs) + f"  ({s.ocr_languages})")

    def _set_tesseract(self, cmd: str | None, apply: bool = False) -> None:
        self._tesseract_cmd = cmd
        if cmd:
            self.tesseract_label.setText(cmd)
        else:
            found = getattr(self.service, "tesseract_cmd", None)
            self.tesseract_label.setText(
                f"Automatisch ({found})" if found else "Automatisch (nicht gefunden)"
            )
        self.tesseract_auto.setEnabled(bool(cmd))

    def pick_tesseract(self) -> None:
        path = dialogs.pick_open_file(self, "Tesseract-Programm wählen", "Programme (*)")
        if path is None:
            return
        self._set_tesseract(str(path))
        self.ocr_feedback.setText("ⓘ Noch nicht gespeichert – bitte „Übernehmen“ wählen.")

    def apply_ocr(self) -> None:
        try:
            self.service.update_settings(
                ocr_backend=str(self.ocr_backend_combo.currentData()),
                ocr_dpi=int(self.ocr_dpi_spin.value()),
                tesseract_cmd=self._tesseract_cmd,
            )
        except Exception as exc:  # noqa: BLE001
            self.ocr_feedback.setStyleSheet(f"color: {ERROR};")
            self.ocr_feedback.setText(f"✖ {user_message(exc)}")
            return
        self.ocr_feedback.setStyleSheet(f"color: {OK};")
        self.ocr_feedback.setText("✔ Gespeichert.")
        self.refresh_ocr()

    # ------------------------------------------------------------------
    # Lokale KI
    # ------------------------------------------------------------------
    def _build_ai(self) -> None:
        frame, lay = card()
        frame.setAccessibleName("Lokale KI")
        lay.addWidget(section_title("Lokale KI (optional)"))
        self.ai_panel = LocalAiPanel(self.service)
        lay.addWidget(self.ai_panel)
        self.content_layout.addWidget(frame)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _build_export(self) -> None:
        frame, lay = card()
        frame.setAccessibleName("Export")
        lay.addWidget(section_title("Export"))
        self.flatten_check = QCheckBox("Formularfelder standardmäßig fixieren (wenn die Vorlage es erlaubt)")
        self.flatten_check.setToolTip(
            "Fixieren heißt: Die eingetragenen Werte werden fester Teil der Seite und sind danach nicht mehr "
            "änderbar. Die Swiss-Life-Vorlagen erlauben das nicht – sie bleiben ausfüllbar."
        )
        self.flatten_check.toggled.connect(self._on_flatten)
        lay.addWidget(self.flatten_check)
        row = QHBoxLayout()
        lbl = QLabel("Briefaufbau nach DIN 5008")
        lbl.setToolTip(
            "DIN 5008 regelt den Aufbau von Geschäftsbriefen. Form A hat einen kleineren, Form B einen "
            "größeren oberen Rand (Briefkopf)."
        )
        row.addWidget(lbl)
        self.letter_form_combo = QComboBox()
        self.letter_form_combo.addItem("Form A (kleiner Briefkopf)", "A")
        self.letter_form_combo.addItem("Form B (großer Briefkopf, Standard)", "B")
        self.letter_form_combo.setAccessibleName("Briefaufbau nach DIN 5008")
        self.letter_form_combo.currentIndexChanged.connect(self._on_letter_form)
        row.addWidget(self.letter_form_combo)
        row.addStretch(1)
        lay.addLayout(row)
        self.content_layout.addWidget(frame)

    def refresh_export(self) -> None:
        s = self.service.settings
        self._loading = True
        try:
            self.flatten_check.setChecked(bool(s.flatten_default))
            self.letter_form_combo.setCurrentIndex(max(self.letter_form_combo.findData(s.letter_form), 0))
        finally:
            self._loading = False

    def _on_flatten(self, checked: bool) -> None:
        if not self._loading:
            self._update(flatten_default=bool(checked))

    def _on_letter_form(self) -> None:
        if not self._loading:
            self._update(letter_form=str(self.letter_form_combo.currentData()))

    def _update(self, **changes: object) -> None:
        try:
            self.service.update_settings(**changes)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Einstellung nicht gespeichert", user_message(exc))

    # ------------------------------------------------------------------
    # Exportprotokoll
    # ------------------------------------------------------------------
    def _build_export_log(self) -> None:
        frame, lay = card()
        frame.setAccessibleName("Exportprotokoll")
        lay.addWidget(section_title("Exportprotokoll"))
        lay.addWidget(
            muted(
                "Jeder Export wird hier vermerkt – ohne Personendaten (nur Zeit, Vorgangsnummer, Vorlage, "
                "Version, Dateiname und Prüfergebnis)."
            )
        )
        self.export_table = QTableWidget(0, len(EXPORT_LOG_COLUMNS))
        self.export_table.setHorizontalHeaderLabels(list(EXPORT_LOG_COLUMNS))
        self.export_table.setAccessibleName("Exportprotokoll")
        self.export_table.verticalHeader().setVisible(False)
        self.export_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.export_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.export_table.setMinimumHeight(160)
        header = self.export_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        qa_item = self.export_table.horizontalHeaderItem(6)
        if qa_item is not None:
            qa_item.setToolTip(
                "QA = Qualitätskontrolle der fertigen PDF (Felder, Darstellung, Unterschriften leer)."
            )
        lay.addWidget(self.export_table)
        self.export_empty = muted("Noch keine Exporte.")
        lay.addWidget(self.export_empty)
        self.content_layout.addWidget(frame)

    def refresh_export_log(self) -> None:
        try:
            entries = list(reversed(self.service.export_log()))
        except Exception as exc:  # noqa: BLE001
            log.warning("Exportprotokoll nicht lesbar (%s)", type(exc).__name__)
            entries = []
        self.export_table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            values = (
                format_dt(e.timestamp),
                e.case_id,
                e.template_id,
                e.template_version,
                e.file_name,
                "✔ ja" if e.flattened else "– nein",
                "✔ bestanden" if e.qa_passed else "✖ nicht bestanden",
            )
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 6:
                    item.setForeground(_color(OK if e.qa_passed else ERROR))
                self.export_table.setItem(row, col, item)
        self.export_empty.setVisible(not entries)

    # ------------------------------------------------------------------
    def refresh_local(self) -> None:
        """Alles, was nur lokale Dateien liest (kein Netzwerk, auch kein Loopback)."""
        self.refresh_storage()
        self.refresh_ocr()
        self.refresh_export()
        self.refresh_export_log()
        self.ai_panel.load_settings()

    def refresh(self) -> None:
        self.refresh_local()
        self.ai_panel.check_status()  # nur der lokale Ollama-Dienst (127.0.0.1), im Hintergrund


def _color(value: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(value))
