"""Hauptfenster: Seitenleiste (4 Bereiche) + Inhaltsbereich.

Bereiche
1. Dashboard / Vorgänge   -> ``pages.dashboard.DashboardPage``  (+ ``pages.case_view.CaseView`` für einen geöffneten Vorgang)
2. Vorlagen & Formulare   -> ``pages.catalog.CatalogPage``
3. Vorlagen-Studio        -> ``pages.studio.StudioPage``
4. Einstellungen          -> ``pages.settings.SettingsPage`` (Datenschutz, lokale Ablage, OCR, lokale KI)

Verträge zwischen Seiten (Konstruktoren)
* ``DashboardPage(service, open_case: Callable[[str], None])``
* ``CaseView(service, show_catalog: Callable[[], None])`` mit ``open_case(case_id)`` und Signal ``case_changed(str)``
* ``CatalogPage(service, current_case_id: Callable[[], str | None], use_template: Callable[[str], None])``
* ``StudioPage(service)``; ``SettingsPage(service)``
Jede Seite bietet ``refresh()`` (wird beim Anzeigen aufgerufen).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..core.service import AppService
from .theme import LOCAL_ONLY_TEXT


class MainWindow(QMainWindow):
    NAV = (
        ("dashboard", "🗂  Dashboard / Vorgänge"),
        ("catalog", "📄  Vorlagen & Formulare"),
        ("studio", "🛠  Vorlagen-Studio"),
        ("settings", "⚙  Einstellungen & Datenschutz"),
    )

    def __init__(self, service: AppService) -> None:
        super().__init__()
        from .pages.case_view import CaseView
        from .pages.catalog import CatalogPage
        from .pages.dashboard import DashboardPage
        from .pages.settings import SettingsPage
        from .pages.studio import StudioPage

        self.service = service
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 860)
        self.setMinimumSize(1100, 700)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget(objectName="Sidebar")
        sidebar.setFixedWidth(270)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(0, 0, 0, 12)
        sb.addWidget(QLabel(APP_NAME, objectName="AppTitle"))
        sb.addWidget(
            QLabel("Versicherungsdokumente lokal prüfen\nund Vorlagen befüllen", objectName="AppSubtitle")
        )
        self._nav_group = QButtonGroup(self)
        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label in self.NAV:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAccessibleName(label.split("  ", 1)[-1])
            btn.clicked.connect(lambda _=False, k=key: self.show_section(k))
            self._nav_group.addButton(btn)
            self._nav_buttons[key] = btn
            sb.addWidget(btn)
        sb.addStretch(1)
        ver = QLabel(f"Version {__version__}", objectName="AppSubtitle")
        sb.addWidget(ver)
        layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.case_view = CaseView(service, show_catalog=lambda: self.show_section("catalog"))
        self.pages: dict[str, QWidget] = {
            "dashboard": DashboardPage(service, open_case=self.open_case),
            "case": self.case_view,
            "catalog": CatalogPage(
                service, current_case_id=self.current_case_id, use_template=self.use_template
            ),
            "studio": StudioPage(service),
            "settings": SettingsPage(service),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        status = QStatusBar()
        badge = QLabel(LOCAL_ONLY_TEXT, objectName="LocalBadge")
        badge.setToolTip(
            "Alle Dokumente, erkannten Texte und Ergebnisse bleiben auf diesem Gerät. "
            "Es gibt keine Cloud-KI, keine Telemetrie und keine automatische Übermittlung an Dritte."
        )
        status.addPermanentWidget(badge)
        self.setStatusBar(status)
        self.show_section("dashboard")

    # ------------------------------------------------------------------
    def show_section(self, key: str) -> None:
        page = self.pages[key]
        if key in self._nav_buttons:
            self._nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(page)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    def open_case(self, case_id: str) -> None:
        self.case_view.open_case(case_id)
        self._nav_buttons["dashboard"].setChecked(True)
        self.stack.setCurrentWidget(self.case_view)

    def current_case_id(self) -> str | None:
        return self.case_view.current_case_id()

    def use_template(self, catalog_id: str) -> None:
        self.case_view.apply_template(catalog_id)
        self.stack.setCurrentWidget(self.case_view)
        self._nav_buttons["dashboard"].setChecked(True)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        from .workers import wait_for_all

        wait_for_all(5_000)
        super().closeEvent(event)
