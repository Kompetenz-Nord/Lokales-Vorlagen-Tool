"""UI-Feinschliff: Katalog → Studio-Import, importierte Version entfernen, Importtext,
Cloud-Ordner-Warnung, Zeitgeber mit Kontextobjekt. Alle PDFs sind synthetisch („MUSTER“)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from uib_helpers import (  # noqa: F401 – Fixture wird durch Import aktiv
    _uib_wait_tasks,
    dispose,
    make_acroform_pdf,
    make_service,
    patch_dialogs,
)

import insurance_app.ui as ui_package
from insurance_app.core.service import REMOVED_VERSION_SUFFIX
from insurance_app.templates.schema import TemplateStatus
from insurance_app.ui.main_window import MainWindow
from insurance_app.ui.pages.catalog import CatalogPage
from insurance_app.ui.pages.settings import SettingsPage
from insurance_app.ui.pages.studio import IMPORT_EXPLANATION, REMOVE_BUTTON_TEXT, StudioPage
from insurance_app.ui.studio.template_tile import IMPORT_BUTTON_TEXT

pytestmark = pytest.mark.ui

MISSING = "tecis_haftungsfreistellung_ak"
LETTER = "hdi_beitragsfreistellung"
SL13065 = "swisslife_sl13065_freigabe"


def _studio(qtbot, svc) -> StudioPage:
    page = StudioPage(svc)
    qtbot.addWidget(page)
    return page


# ---------------------------------------------------------------------------
# 1. Katalog → Studio
# ---------------------------------------------------------------------------


def test_only_missing_original_tiles_offer_import_button(qtbot, tmp_path, monkeypatch):
    patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    requested: list[str] = []
    page = CatalogPage(
        svc, current_case_id=lambda: None, use_template=lambda _id: None, import_original=requested.append
    )
    qtbot.addWidget(page)
    with_button = sorted(cid for cid, tile in page.tiles.items() if tile.import_button is not None)
    missing = sorted(c.catalog_id for c in page.choices if c.status == TemplateStatus.MISSING_ORIGINAL)
    assert with_button == missing == [MISSING]
    button = page.tiles[MISSING].import_button
    assert button.text() == IMPORT_BUTTON_TEXT == "Originalvorlage importieren …"
    assert button.isEnabled()  # auch ohne geöffneten Vorgang
    with qtbot.waitSignal(page.import_requested) as sig:
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert sig.args == [MISSING] and requested == [MISSING]


def test_main_window_import_button_opens_studio_with_entry_preselected(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    win = MainWindow(svc)  # wird am Ende über ``dispose`` gelöscht
    try:
        win.show_section("catalog")
        catalog, studio = win.pages["catalog"], win.pages["studio"]
        assert studio.catalog_id is None and not studio.import_button.isEnabled()
        qtbot.mouseClick(catalog.tiles[MISSING].import_button, Qt.MouseButton.LeftButton)
        assert win.stack.currentWidget() is studio
        assert win._nav_buttons["studio"].isChecked()
        assert studio.catalog_id == MISSING
        assert studio.import_button.isEnabled()
        assert studio.import_hint.isVisibleTo(studio)
        assert "ist für den Import ausgewählt" in studio.import_hint.text()
        assert not rec.errors, rec.error_text()

        # Import direkt von dort: der vorgewählte Eintrag erhält die Version
        rec.open_file = make_acroform_pdf(tmp_path / "muster_formular.pdf")
        qtbot.mouseClick(studio.import_button, Qt.MouseButton.LeftButton)
        assert [v.source for v in svc.registry.list_versions(MISSING)] == ["importiert"]
        assert not studio.import_hint.isVisibleTo(studio)

        # „Im Studio öffnen“ läuft ebenfalls über das Hauptfenster
        win.show_section("catalog")
        qtbot.mouseClick(win.pages["catalog"].tiles[SL13065].studio_button, Qt.MouseButton.LeftButton)
        assert win.stack.currentWidget() is studio and studio.catalog_id == SL13065
    finally:
        dispose(win)


# ---------------------------------------------------------------------------
# 5. Text des Import-Dialogs
# ---------------------------------------------------------------------------


def test_import_dialog_explains_what_happens(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    rec.open_file = make_acroform_pdf(tmp_path / "muster_formular.pdf")
    page.select_entry(MISSING)
    rec.confirm_answer = False
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    assert len(rec.confirms) == 1
    title, text = rec.confirms[0]
    assert title == "Originalvorlage importieren"
    assert IMPORT_EXPLANATION in text
    for needle in (
        "Original-PDF bleibt unverändert",
        "schreibgeschützte Kopie",
        "Formularfelder",
        "prüfen und bestätigen",
        "Nichts verlässt dieses Gerät",
        "Berechtigungen der PDF werden beachtet",
    ):
        assert needle in text, needle
    assert "DIN 5008" not in text  # Zusatz nur bei Einträgen mit Brief
    assert svc.registry.list_versions(MISSING) == []  # abgebrochen: nichts importiert
    assert not rec.infos

    page.select_entry(LETTER)
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    assert IMPORT_EXPLANATION in rec.confirms[1][1] and "DIN 5008" in rec.confirms[1][1]


# ---------------------------------------------------------------------------
# 2. Importierte Version entfernen
# ---------------------------------------------------------------------------


def test_remove_imported_version_button_with_danger_confirmation(qtbot, tmp_path, monkeypatch):
    from insurance_app.ui import dialogs

    rec = patch_dialogs(monkeypatch)
    kwargs: list[dict] = []
    plain_confirm = dialogs.ask_confirm

    def recording_confirm(parent, title, text, **kw):
        kwargs.append({"title": title, **kw})
        return plain_confirm(parent, title, text, **kw)

    monkeypatch.setattr(dialogs, "ask_confirm", recording_confirm)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    rec.open_file = make_acroform_pdf(tmp_path / "muster_formular.pdf")
    page.select_entry(MISSING)
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    version = page.current_version
    assert version is not None and version.source == "importiert"
    button = page.remove_version_button
    assert button.text() == REMOVE_BUTTON_TEXT == "Importierte Version entfernen"
    assert button.property("danger") is True and button.isEnabled()

    # Abbrechen: nichts wird gelöscht
    rec.confirms.clear()
    kwargs.clear()
    rec.confirm_answer = False
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert kwargs[-1]["danger"] is True and kwargs[-1]["ok_text"] == "Endgültig entfernen"
    assert "Bereits erzeugte PDFs" in rec.confirms[-1][1]
    assert version.pdf_path.is_file()
    assert len(svc.registry.list_versions(MISSING)) == 1

    # Bestätigen: Version weg, Status wieder „Originalvorlage fehlt“
    rec.confirm_answer = True
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not rec.errors, rec.error_text()
    assert svc.registry.list_versions(MISSING) == []
    assert not version.pdf_path.exists()
    assert page.status_text(MISSING) == "○ Originalvorlage fehlt"
    assert page.versions_table.rowCount() == 0
    assert page.current_version is None and not button.isEnabled()
    assert "wurde entfernt" in rec.info_text()


def test_shipped_version_cannot_be_removed_and_cases_are_warned(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    page.select_entry(SL13065)
    shipped = page.current_version
    assert shipped is not None and shipped.source == "mitgeliefert"
    assert not page.remove_version_button.isEnabled()
    assert "können nicht entfernt werden" in page.remove_hint.text()
    page.remove_version()  # auch ein direkter Aufruf löscht nichts
    assert shipped.pdf_path.is_file() and not rec.confirms

    # eigene Kopie derselben Version, von einem Vorgang verwendet
    svc.registry.save_mapping(SL13065, svc.registry.get_definition(SL13065), confirmed=True)
    case = svc.create_case("MUSTER Vorgang")
    svc.select_template(case, SL13065)
    page.refresh()
    page.select_entry(SL13065, version=shipped.version, source="importiert")
    assert page.current_version.source == "importiert"
    assert page.remove_version_button.isEnabled()
    qtbot.mouseClick(page.remove_version_button, Qt.MouseButton.LeftButton)
    assert "1 Vorgang/Vorgänge verwenden diese Version" in rec.confirms[-1][1]
    assert not rec.errors, rec.error_text()
    assert [v.source for v in svc.registry.list_versions(SL13065)] == ["mitgeliefert"]
    assert shipped.pdf_path.is_file()  # mitgeliefertes Original bleibt
    assert page.status_text(SL13065) == "✔ Aktiv"
    reloaded = svc.load_case(case.id)
    assert reloaded.template_version == shipped.version + REMOVED_VERSION_SUFFIX
    assert svc.review_status(reloaded).blocking_items[0].code == "TEMPLATE_VERSION_CHANGED"


# ---------------------------------------------------------------------------
# 6. Cloud-Ordner-Warnung in den Einstellungen
# ---------------------------------------------------------------------------


def test_export_dir_in_cloud_folder_warns_and_asks(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = SettingsPage(svc)
    qtbot.addWidget(page)
    assert not page.cloud_warning.isVisibleTo(page)
    cloud = tmp_path / "OneDrive - Muster GmbH" / "Exporte"
    rec.directory = cloud

    rec.confirm_answer = False  # „Anderen Ordner wählen“
    qtbot.mouseClick(page.export_dir_button, Qt.MouseButton.LeftButton)
    title, text = rec.confirms[-1]
    assert title == "Ordner liegt in einem Cloud-Ordner"
    assert "OneDrive" in text and "Die App selbst sendet nichts" in text
    assert svc.settings.export_dir is None and not cloud.exists()
    assert not page.cloud_warning.isVisibleTo(page)

    rec.confirm_answer = True  # bewusst trotzdem verwenden
    qtbot.mouseClick(page.export_dir_button, Qt.MouseButton.LeftButton)
    assert svc.settings.export_dir == str(cloud)
    assert page.cloud_warning.isVisibleTo(page)
    warning = page.cloud_warning.text()
    assert warning.startswith("⚠") and "Exportordner" in warning and "OneDrive" in warning

    qtbot.mouseClick(page.export_dir_reset, Qt.MouseButton.LeftButton)
    assert not page.cloud_warning.isVisibleTo(page)

    # lokaler Ordner: keine Rückfrage
    before = len(rec.confirms)
    rec.directory = tmp_path / "lokal"
    qtbot.mouseClick(page.export_dir_button, Qt.MouseButton.LeftButton)
    assert len(rec.confirms) == before and svc.settings.export_dir == str(tmp_path / "lokal")
    assert not page.cloud_warning.isVisibleTo(page)


# ---------------------------------------------------------------------------
# 3. Zeitgeber nur mit Kontextobjekt
# ---------------------------------------------------------------------------

_SINGLE_SHOT = re.compile(r"QTimer\.singleShot\(([^()]*(?:\([^()]*\)[^()]*)*)\)")


def test_single_shot_timers_always_have_a_context_object():
    """``QTimer.singleShot(ms, callable)`` ohne Kontext kann nach dem Löschen des Widgets feuern."""
    offenders = []
    calls = 0
    for path in sorted(Path(ui_package.__file__).parent.rglob("*.py")):
        for match in _SINGLE_SHOT.finditer(path.read_text("utf-8")):
            calls += 1
            args = [a.strip() for a in match.group(1).split(",")]
            if len(args) < 3 or args[1] != "self":
                offenders.append(f"{path.name}: {match.group(0)}")
    assert calls >= 7
    assert not offenders, offenders


def test_pending_row_fit_does_not_fire_after_table_is_deleted(qtbot, tmp_path, monkeypatch):
    from insurance_app.ui.case.controller import CaseController
    from insurance_app.ui.case.documents_panel import DocumentsPanel

    patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    fired: list[int] = []
    controller = CaseController(svc)
    panel = DocumentsPanel(svc, controller, start_processing=lambda: None, goto_review=lambda: None)
    QApplication.processEvents()  # Zeitgeber aus dem Aufbau abarbeiten
    panel._fit_rows = lambda: fired.append(1)
    panel._rows_pending = False
    panel._schedule_row_heights()
    shiboken6.delete(panel)
    QApplication.processEvents()
    assert fired == []

    # Gegenprobe: solange das Widget lebt, läuft der Aufruf
    alive = DocumentsPanel(svc, controller, start_processing=lambda: None, goto_review=lambda: None)
    qtbot.addWidget(alive)
    QApplication.processEvents()
    alive._fit_rows = lambda: fired.append(1)
    alive._rows_pending = False
    alive._schedule_row_heights()
    QApplication.processEvents()
    assert fired == [1]
