"""Start der Desktop-Oberfläche."""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def create_application(argv: list[str] | None = None):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME
    from .theme import STYLESHEET

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("LokalesVorlagenTool")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    font = QFont()
    font.setPointSizeF(10.5)
    app.setFont(font)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    # Speicherbereinigung nur im Fenster-Thread: verhindert Abstürze durch Qt-Objekte,
    # die sonst in einem Worker-Thread freigegeben würden (siehe ui/gc_guard.py).
    from .gc_guard import install_main_thread_gc

    install_main_thread_gc(app)
    return app


def run_gui(argv: list[str] | None = None) -> int:
    app = create_application(argv)
    from PySide6.QtWidgets import QMessageBox

    from ..core.service import AppService
    from .main_window import MainWindow

    try:
        service = AppService()
    except Exception as exc:  # noqa: BLE001 – Startfehler verständlich anzeigen
        log.exception("Start fehlgeschlagen")
        QMessageBox.critical(
            None,
            "Start nicht möglich",
            "Die lokale Datenablage konnte nicht geöffnet werden.\n\n"
            f"{getattr(exc, 'user_message', type(exc).__name__)}",
        )
        return 1
    window = MainWindow(service)
    window.show()
    if not service.settings.first_run_completed:
        QMessageBox.information(
            window,
            "Willkommen",
            "Diese App arbeitet ausschließlich lokal auf diesem Gerät.\n\n"
            "• Keine Cloud-KI, keine Telemetrie, keine automatische Übermittlung.\n"
            "• Erkannte Werte müssen Sie vor dem Ausfüllen prüfen und bestätigen.\n"
            "• Unterschriften erfolgen immer handschriftlich.\n\n"
            "Zum Ausprobieren: „Musterfall laden“ auf dem Dashboard (nur synthetische Daten).\n"
            "Die optionale lokale KI können Sie später unter „Einstellungen“ einrichten.",
        )
        service.update_settings(first_run_completed=True)
    code = app.exec()
    service.close()
    return int(code)
