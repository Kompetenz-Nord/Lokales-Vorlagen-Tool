"""Seite „Vorlagen & Formulare“: alle Katalogeinträge, gruppiert, mit Status.

Eine Vorlage lässt sich nur verwenden, wenn sie verfügbar ist (Status „Aktiv“)
UND ein Vorgang geöffnet ist. Sonst ist die Schaltfläche deaktiviert und der
Grund steht als Text daneben (nicht nur als Tooltip).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import SIGNAL, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.service import AppService, TemplateChoice
from .. import dialogs
from ..studio.template_tile import TemplateTile
from ..widgets.common import muted, page_title, section_title
from ..workers import user_message

log = logging.getLogger(__name__)


class CatalogPage(QWidget):
    """Vorlagenkatalog (9 Einträge in 4 Gruppen)."""

    #: Wunsch, einen Eintrag im Vorlagen-Studio zu öffnen (Katalog-ID)
    studio_requested = Signal(str)
    #: Wunsch, für einen Eintrag ohne Originalvorlage den Import im Studio zu starten (Katalog-ID)
    import_requested = Signal(str)

    def __init__(
        self,
        service: AppService,
        current_case_id: Callable[[], str | None],
        use_template: Callable[[str], None],
        open_studio: Callable[[str], None] | None = None,
        import_original: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self._current_case_id = current_case_id
        self._use_template = use_template
        self._open_studio = open_studio
        self._import_original = import_original
        self.choices: list[TemplateChoice] = []
        self.tiles: dict[str, TemplateTile] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 12)
        outer.setSpacing(10)

        head = QHBoxLayout()
        self.title = page_title("Vorlagen & Formulare")
        head.addWidget(self.title, 1)
        self.recheck_button = QPushButton("Vorlagen neu prüfen")
        self.recheck_button.setToolTip(
            "Prüft alle Originalvorlagen erneut (Prüfsumme, Formularfelder, Berechtigungen)."
        )
        self.recheck_button.clicked.connect(lambda: self.refresh(recheck=True))
        head.addWidget(self.recheck_button)
        outer.addLayout(head)

        self.intro = muted(
            "Hier sehen Sie alle Vorlagen. „Aktiv“ heißt: Die Originalvorlage ist geprüft und die "
            "Feldzuordnung bestätigt – oder die App erzeugt ein Kundenschreiben nach DIN 5008. "
            "Wählen Sie eine Vorlage für den gerade geöffneten Vorgang."
        )
        outer.addWidget(self.intro)
        self.case_hint = muted("")
        outer.addWidget(self.case_hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget(objectName="CatalogContent")
        self.scroll.viewport().setObjectName("CatalogViewport")
        self.scroll.setStyleSheet("#CatalogViewport, #CatalogContent { background: transparent; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 8, 0)
        self._content_layout.setSpacing(10)
        self.scroll.setWidget(self._content)
        outer.addWidget(self.scroll, 1)

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self, recheck: bool = False) -> None:
        try:
            self.choices = self.service.template_choices(refresh=recheck)
        except Exception as exc:  # noqa: BLE001 – Seite muss trotzdem erscheinen
            log.warning("Vorlagenkatalog nicht lesbar (%s)", type(exc).__name__)
            self.choices = []
            dialogs.show_error(self, "Vorlagen", user_message(exc))
        self._rebuild()
        self.update_case_state()

    def _groups(self) -> list[str]:
        try:
            groups = list(self.service.registry.catalog().groups)
        except Exception:  # noqa: BLE001
            groups = []
        for c in self.choices:
            if c.group not in groups:
                groups.append(c.group)
        return groups

    def _rebuild(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.tiles.clear()
        count = len(self.choices)
        self.title.setText(f"Vorlagen & Formulare ({count} Einträge)")
        self.title.setAccessibleName(self.title.text())
        for group in self._groups():
            members = [c for c in self.choices if c.group == group]
            if not members:
                continue
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 6, 0, 0)
            box_layout.setSpacing(6)
            usable = sum(1 for c in members if c.available)
            title = section_title(f"{group}  ·  {usable} von {len(members)} verfügbar")
            title.setAccessibleName(f"Gruppe {group}")
            box_layout.addWidget(title)
            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(12)
            for idx, choice in enumerate(members):
                tile = TemplateTile(choice, self._on_use, self._on_studio, self._on_import)
                self.tiles[choice.catalog_id] = tile
                grid.addWidget(tile, idx // 2, idx % 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            box_layout.addLayout(grid)
            self._content_layout.addWidget(box)
        self._content_layout.addStretch(1)

    def update_case_state(self) -> None:
        case_id = self._case_id()
        for tile in self.tiles.values():
            tile.update_case_state(case_id is not None)
        if case_id is None:
            self.case_hint.setText(
                "ℹ Kein Vorgang geöffnet. Öffnen Sie zuerst einen Vorgang im Dashboard, "
                "um eine Vorlage auszuwählen."
            )
        else:
            self.case_hint.setText("✔ Ein Vorgang ist geöffnet. Verfügbare Vorlagen können Sie jetzt wählen.")

    def _case_id(self) -> str | None:
        try:
            return self._current_case_id()
        except Exception:  # noqa: BLE001 – Rückruf der Hauptseite darf die Seite nicht stören
            return None

    def showEvent(self, event) -> None:  # noqa: N802 (Qt-API)
        self.update_case_state()
        super().showEvent(event)

    # ------------------------------------------------------------------
    def _on_use(self, catalog_id: str) -> None:
        choice = next((c for c in self.choices if c.catalog_id == catalog_id), None)
        if choice is None or not choice.available or self._case_id() is None:
            self.update_case_state()
            return
        try:
            self._use_template(catalog_id)
        except Exception as exc:  # noqa: BLE001
            dialogs.show_error(self, "Vorlage verwenden", user_message(exc))

    def _on_studio(self, catalog_id: str) -> None:
        self.studio_requested.emit(catalog_id)
        if self._open_studio is not None:
            self._open_studio(catalog_id)
            return
        # Ersatzweg, solange das Hauptfenster das Signal nicht verbindet
        if self.receivers(SIGNAL("studio_requested(QString)")) > 0:
            return
        win = self.window()
        show_section = getattr(win, "show_section", None)
        pages = getattr(win, "pages", None)
        if callable(show_section) and isinstance(pages, dict) and "studio" in pages:
            show_section("studio")
            select = getattr(pages["studio"], "select_entry", None)
            if callable(select):
                select(catalog_id)

    def _on_import(self, catalog_id: str) -> None:
        """„Originalvorlage importieren …“: ins Studio wechseln, Eintrag für den Import vorwählen."""
        self.import_requested.emit(catalog_id)
        if self._import_original is not None:
            self._import_original(catalog_id)
            return
        if self.receivers(SIGNAL("import_requested(QString)")) > 0:
            return
        self._on_studio(catalog_id)  # Ersatzweg: Eintrag wenigstens im Studio öffnen
