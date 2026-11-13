"""Vorlagen-Studio: Import, Zuordnung, Bestätigung, Pakete, Testbefüllung, Koordinateneditor.

Alle PDFs sind synthetisch („MUSTER – KEINE ECHTDATEN“).
"""

from __future__ import annotations

import hashlib
import stat
import zipfile

import pytest
from PySide6.QtCore import QRectF, Qt
from uib_helpers import (  # noqa: F401 – Fixture wird durch Import aktiv
    _uib_wait_tasks,
    apply_app_style,
    build_host,
    dispose,
    make_acroform_pdf,
    make_plain_pdf,
    make_service,
    patch_dialogs,
    screenshot_dir,
)

from insurance_app.templates.schema import MappingKind, TemplateStatus
from insurance_app.ui.pages.studio import StudioPage
from insurance_app.ui.studio import coordinate_editor, mapping_editor
from insurance_app.ui.studio.coordinate_editor import SCALE, OverlaySpec
from insurance_app.ui.studio.mapping_editor import ChoiceMapDialog

pytestmark = pytest.mark.ui

TECIS = "tecis_haftungsfreistellung_ak"
SL13065 = "swisslife_sl13065_freigabe"


def _studio(qtbot, svc) -> StudioPage:
    page = StudioPage(svc)
    qtbot.addWidget(page)
    return page


def _import_acroform(qtbot, page: StudioPage, rec, tmp_path):
    pdf = make_acroform_pdf(tmp_path / "muster_formular.pdf")
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    rec.open_file = pdf
    page.select_entry(TECIS)
    assert page.import_button.isEnabled()
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    assert not rec.errors, rec.error_text()
    assert "schreibgeschützte Kopie" in rec.info_text()
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before  # Originaldatei unverändert
    return pdf


def _set_source(editor, pdf_field: str, key: str) -> None:
    combo = editor.widgets_for(pdf_field).source
    idx = combo.findData(key, Qt.ItemDataRole.UserRole)
    assert idx >= 0
    combo.setCurrentIndex(idx)


def test_studio_lists_entries_and_shows_shipped_mapping_read_only(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    assert page.table.rowCount() == 9
    assert page.status_text(SL13065) == "✔ Aktiv"
    assert page.status_text(TECIS) == "○ Originalvorlage fehlt"
    page.select_entry(SL13065)
    editor = page.editor
    assert editor.read_only
    assert editor.edit_button.isVisibleTo(page)
    assert not editor.confirm_button.isEnabled()
    assert len(editor.rows) == 23  # 22 Zuordnungen + Schaltfläche „Formular zurücksetzen“
    sig = editor.rows[editor.row_index("Unterschrift")]
    assert sig.kind == MappingKind.SIGNATURE and sig.locked
    assert not editor.widgets_for("Versicherung Nummer").source.isEnabled()
    assert page.versions_table.rowCount() == 1
    assert "mitgeliefert" in page.versions_table.item(0, 1).text()
    assert page.sample_button.isEnabled()
    assert not page.coord_button.isEnabled()  # PDF hat Formularfelder
    assert not rec.errors


def test_import_map_confirm_makes_template_active(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    _import_acroform(qtbot, page, rec, tmp_path)
    assert page.status_text(TECIS) == "◐ Zu prüfen"
    v = page.current_version
    assert v is not None and v.source == "importiert" and not v.confirmed
    mode = v.pdf_path.stat().st_mode
    assert not mode & stat.S_IWUSR  # Kopie ist schreibgeschützt
    editor = page.editor
    assert not editor.read_only
    # Automatisch zugeordnet: Vertragsnummer; „Name“ nur als unsicherer Vorschlag
    auto = editor.rows[editor.row_index("Vertragsnummer")]
    assert auto.kind == MappingKind.TEXT and auto.source == "contract.number"
    name_row = editor.rows[editor.row_index("Name")]
    assert name_row.kind is None and name_row.suggestion
    assert "Vorschlag" in (name_row.hint or "")
    checkbox = editor.rows[editor.row_index("Einverstanden")]
    assert checkbox.kind is None  # Ankreuzfelder nie automatisch

    # ein Feld zuordnen und bestätigen
    _set_source(editor, "Name", "insured.last_name")
    assert name_row.kind == MappingKind.TEXT and name_row.source == "insured.last_name"
    assert editor.dirty
    rec.infos.clear()
    qtbot.mouseClick(editor.confirm_button, Qt.MouseButton.LeftButton)
    assert not rec.errors, rec.error_text()
    assert rec.confirms, "Bestätigung muss nachgefragt werden"
    assert "✔ Aktiv" in rec.info_text()
    assert page.status_text(TECIS) == "✔ Aktiv"
    choice = next(c for c in svc.template_choices() if c.catalog_id == TECIS)
    assert choice.available and choice.status == TemplateStatus.ACTIVE
    definition = svc.registry.get_definition(TECIS)
    assert definition.mapping_confirmed
    assert {m.pdf_field: m.source for m in definition.fields}["Name"] == "insured.last_name"


def test_incomplete_mapping_shows_german_hints(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    _import_acroform(qtbot, page, rec, tmp_path)
    editor = page.editor
    kind_combo = editor.widgets_for("Kundennummer").kind
    kind_combo.setCurrentIndex(kind_combo.findData(MappingKind.DATE_PART.value))
    qtbot.mouseClick(editor.save_button, Qt.MouseButton.LeftButton)
    assert rec.errors
    text = rec.error_text()
    assert "Kundennummer" in text and "Quelle fehlt" in text
    assert page.status_text(TECIS) == "◐ Zu prüfen"  # nichts gespeichert


def test_package_export_import_roundtrip(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc_a = make_service(tmp_path / "a")
    page_a = _studio(qtbot, svc_a)
    _import_acroform(qtbot, page_a, rec, tmp_path)
    _set_source(page_a.editor, "Name", "insured.last_name")
    assert page_a.editor.confirm()
    assert page_a.status_text(TECIS) == "✔ Aktiv"

    rec.save_file = tmp_path / "vorlagenpaket"  # Endung wird ergänzt
    qtbot.mouseClick(page_a.export_pkg_button, Qt.MouseButton.LeftButton)
    package = tmp_path / "vorlagenpaket.zip"
    assert package.is_file(), rec.error_text()
    assert sorted(zipfile.ZipFile(package).namelist()) == ["manifest.json", "mapping.json", "original.pdf"]

    svc_b = make_service(tmp_path / "b")
    page_b = _studio(qtbot, svc_b)
    assert page_b.status_text(TECIS) == "○ Originalvorlage fehlt"
    rec.open_file = package
    rec.infos.clear()
    qtbot.mouseClick(page_b.import_pkg_button, Qt.MouseButton.LeftButton)
    assert not rec.errors, rec.error_text()
    assert "bestätigen" in rec.info_text()
    assert page_b.status_text(TECIS) == "◐ Zu prüfen"  # importierte Pakete müssen bestätigt werden
    assert page_b.catalog_id == TECIS
    assert page_b.editor.rows[page_b.editor.row_index("Name")].source == "insured.last_name"
    assert page_b.editor.confirm()
    assert page_b.status_text(TECIS) == "✔ Aktiv"


def test_sample_fill_shows_pages_and_removes_temp_files(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    page.select_entry(SL13065)
    assert page.sample_button.isEnabled()
    qtbot.mouseClick(page.sample_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.preview_dialog is not None, timeout=20_000)
    assert not rec.errors, rec.error_text()
    dlg = page.preview_dialog
    assert len(dlg.page_labels) == 1
    assert "Versicherung Nummer" in dlg.result.filled_fields
    assert "Unterschrift" not in dlg.result.filled_fields  # Unterschriften bleiben leer
    leftovers = [p for p in svc.paths.temp_dir.iterdir() if p.name.startswith("testbefuellung")]
    assert leftovers == []
    dlg.close()


def test_coordinate_editor_creates_overlay_field(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    rec.open_file = make_plain_pdf(tmp_path / "ohne_felder.pdf")
    page.select_entry(TECIS)
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    assert page.current_inspection is not None and not page.current_inspection.fields
    assert page.coord_button.isEnabled()
    qtbot.mouseClick(page.coord_button, Qt.MouseButton.LeftButton)
    dlg = page.coord_dialog  # Kind der Seite – wird mit ihr geschlossen
    assert dlg is not None
    assert dlg.page_count == 2

    asked: list[OverlaySpec | None] = []

    def fake_ask(parent, spec=None):
        asked.append(spec)
        return OverlaySpec(
            label="Vertragsnummer", source="contract.number", kind=MappingKind.TEXT, font_size=10
        )

    monkeypatch.setattr(coordinate_editor, "ask_overlay_field", fake_ask)
    # Rechteck „aufziehen“ (Szene-Koordinaten) -> Feld in PDF-Punkten
    dlg.view.rect_drawn.emit(QRectF(200 * SCALE, 90 * SCALE, 180 * SCALE, 16 * SCALE))
    assert len(asked) == 1
    assert len(dlg.fields) == 1
    field = dlg.fields[0]
    assert field.page == 1
    assert field.rect == pytest.approx([200, 90, 380, 106], abs=0.01)
    assert dlg.selected_field() is field
    # Pfeiltaste verschiebt um 1 Punkt, Umschalt+Pfeil ändert die Größe
    qtbot.keyClick(dlg.view, Qt.Key.Key_Right)
    qtbot.keyClick(dlg.view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert field.rect == pytest.approx([201, 90, 381, 107], abs=0.01)
    dlg.spins["w"].setValue(150)
    assert field.rect[2] == pytest.approx(351, abs=0.01)
    assert field.item is not None
    assert field.item.pos().x() == pytest.approx(201 * SCALE, abs=0.01)

    # zweites Feld anlegen und mit Entf löschen
    dlg.add_field([60, 300, 200, 316], OverlaySpec(label="Ort", source="insured.city"))
    assert len(dlg.fields) == 2
    qtbot.keyClick(dlg.view, Qt.Key.Key_Delete)
    assert [f.spec.label for f in dlg.fields] == ["Vertragsnummer"]

    assert dlg.save(confirmed=True)
    assert not rec.errors, rec.error_text()
    assert page.status_text(TECIS) == "✔ Aktiv"
    definition = svc.registry.get_definition(TECIS)
    overlay = [m for m in definition.fields if m.overlay_rect]
    assert len(overlay) == 1
    assert overlay[0].source == "contract.number"
    assert overlay[0].overlay_rect == pytest.approx([201, 90, 351, 107], abs=0.11)
    dlg.close()

    # Testbefüllung funktioniert auch mit Koordinatenfeldern
    qtbot.mouseClick(page.sample_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.preview_dialog is not None, timeout=20_000)
    assert overlay[0].pdf_field in page.preview_dialog.result.filled_fields
    assert len(page.preview_dialog.page_labels) == 2
    page.preview_dialog.close()


def test_coordinate_editor_mouse_drawing(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    rec.open_file = make_plain_pdf(tmp_path / "ohne_felder.pdf", pages=1)
    page.select_entry(TECIS)
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    dlg = page.open_coordinate_editor()  # Kind der Seite – wird mit ihr geschlossen
    dlg.show()
    qtbot.waitExposed(dlg)
    monkeypatch.setattr(
        coordinate_editor,
        "ask_overlay_field",
        lambda parent, spec=None: OverlaySpec(label="Name", source="insured.last_name"),
    )
    viewport = dlg.view.viewport()
    start = dlg.view.mapFromScene(100 * SCALE, 200 * SCALE)
    end = dlg.view.mapFromScene(260 * SCALE, 220 * SCALE)
    with qtbot.waitSignal(dlg.view.rect_drawn, timeout=2000):
        qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseMove(viewport, end)
        qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
    assert len(dlg.fields) == 1
    x0, y0, x1, y1 = dlg.fields[0].rect
    assert x0 == pytest.approx(100, abs=1.5) and y0 == pytest.approx(200, abs=1.5)
    assert x1 == pytest.approx(260, abs=1.5) and y1 == pytest.approx(220, abs=1.5)
    # zu kleines Rechteck (bloßer Klick) legt kein Feld an
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=dlg.view.mapFromScene(50 * SCALE, 500 * SCALE))
    assert len(dlg.fields) == 1
    assert dlg.save(confirmed=False)
    assert page.status_text(TECIS) == "◐ Zu prüfen"
    dlg.close()


def test_shipped_mapping_edit_creates_user_copy(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    shipped = svc.registry.list_versions(SL13065)[0]
    shipped_hash = hashlib.sha256(shipped.mapping_path.read_bytes()).hexdigest()
    page = _studio(qtbot, svc)
    page.select_entry(SL13065)
    editor = page.editor
    qtbot.mouseClick(editor.edit_button, Qt.MouseButton.LeftButton)
    assert not editor.read_only
    assert editor.widgets_for("Ort").source.isEnabled()
    assert not editor.widgets_for("Unterschrift").kind.isEnabled()  # Unterschrift bleibt gesperrt
    qtbot.mouseClick(editor.save_button, Qt.MouseButton.LeftButton)
    assert not rec.errors, rec.error_text()
    versions = svc.registry.list_versions(SL13065)
    assert [v.source for v in versions] == ["importiert", "mitgeliefert"]
    assert hashlib.sha256(shipped.mapping_path.read_bytes()).hexdigest() == shipped_hash
    assert page.versions_table.rowCount() == 2


def test_import_for_letter_entry_asks_first(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    rec.open_file = make_acroform_pdf(tmp_path / "eigene_vorlage.pdf")
    page.select_entry("hdi_beitragsfreistellung")
    rec.confirm_answer = False
    qtbot.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    assert rec.confirms and "DIN 5008" in rec.confirms[0][1]
    assert svc.registry.list_versions("hdi_beitragsfreistellung") == []  # nichts importiert
    assert page.status_text("hdi_beitragsfreistellung") == "✔ Aktiv"


def test_choice_map_dialog_maps_states(qtbot):
    dlg = ChoiceMapDialog("Beitragszahlung", ["0", "1"], "order.payment_method", None)
    qtbot.addWidget(dlg)
    combos = dlg.combos
    combos["0"].setCurrentIndex(combos["0"].findData("sepa"))
    combos["1"].setCurrentIndex(combos["1"].findData("transfer"))
    assert dlg.result_map() == {"sepa": "0", "transfer": "1"}
    combos["1"].setCurrentIndex(combos["1"].findData("sepa"))
    dlg._accept()
    assert "nur einem Zustand" in dlg.error.text()


def test_radio_mapping_uses_choice_map_editor(qtbot, tmp_path, monkeypatch):
    patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _studio(qtbot, svc)
    page.select_entry("swisslife_sl18477_uebernahme")
    editor = page.editor
    editor.set_read_only(False)
    idx = editor.row_index("Beitragszahlung")
    row = editor.rows[idx]
    assert row.kind == MappingKind.RADIO and row.choice_map == {"sepa": "0", "transfer": "1"}
    seen = {}

    def fake_edit(parent, pdf_field, states, source, current, labels=None):
        seen.update(field=pdf_field, states=states, source=source, current=current)
        return {"transfer": "0", "sepa": "1"}

    monkeypatch.setattr(mapping_editor, "edit_choice_map", fake_edit)
    editor.edit_radio(idx)
    assert seen["source"] == "order.payment_method"
    assert sorted(seen["states"]) == ["0", "1"]
    assert editor.rows[idx].choice_map == {"transfer": "0", "sepa": "1"}
    definition = editor.build_definition()
    assert definition.mapping("Beitragszahlung").choice_map == {"transfer": "0", "sepa": "1"}


def test_studio_screenshot(qtbot, tmp_path, monkeypatch):
    patch_dialogs(monkeypatch)
    apply_app_style()
    svc = make_service(tmp_path)
    svc.create_sample_case()
    host, page = build_host(svc, "studio", lambda: StudioPage(svc))
    try:
        host.show()
        qtbot.waitExposed(host)
        page.select_entry(SL13065)
        assert page.editor.table.isVisible()
        qtbot.wait(100)  # Layout nach dem Laden aktualisieren
        image = host.grab()
        assert not image.isNull()
        target = screenshot_dir()
        if target is not None:
            assert image.save(str(target / "ui_studio.png"))
    finally:
        dispose(host)
