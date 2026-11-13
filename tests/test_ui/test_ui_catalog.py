"""Vorlagenkatalog: 9 Kacheln in 4 Gruppen, Verfügbarkeit, Rückrufe (synthetische Daten)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from uib_helpers import (  # noqa: F401 – Fixture wird durch Import aktiv
    _uib_wait_tasks,
    apply_app_style,
    build_host,
    dispose,
    make_service,
    patch_dialogs,
    screenshot_dir,
)

from insurance_app.ui.pages.catalog import CatalogPage
from insurance_app.ui.studio.template_tile import LETTER_FORM_TEXT, REASON_MISSING_ORIGINAL, REASON_NO_CASE

pytestmark = pytest.mark.ui

MISSING = "tecis_haftungsfreistellung_ak"
SL13065 = "swisslife_sl13065_freigabe"


def _page(qtbot, svc, case_id="VG-MUSTER-1", used=None, opened=None):
    used = used if used is not None else []
    page = CatalogPage(
        svc,
        current_case_id=lambda: case_id,
        use_template=used.append,
        open_studio=opened.append if opened is not None else None,
    )
    qtbot.addWidget(page)
    return page


def test_catalog_shows_nine_tiles_eight_usable(qtbot, tmp_path, monkeypatch):
    rec = patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    page = _page(qtbot, svc)
    assert page.title.text() == "Vorlagen & Formulare (9 Einträge)"
    assert len(page.tiles) == 9
    usable = sorted(cid for cid, tile in page.tiles.items() if tile.use_button.isEnabled())
    assert len(usable) == 8
    assert MISSING not in usable
    missing = page.tiles[MISSING]
    assert not missing.use_button.isEnabled()
    assert REASON_MISSING_ORIGINAL in missing.use_button.toolTip()
    assert missing.block_reason.isVisibleTo(missing)
    assert "Originalvorlage fehlt" in missing.status_badge.text()  # Symbol + Text, nicht nur Farbe
    assert missing.studio_button.isEnabled()
    assert not rec.errors


def test_tiles_show_number_form_status_and_legal_hint(qtbot, tmp_path):
    svc = make_service(tmp_path)
    page = _page(qtbot, svc)
    sl = page.tiles[SL13065]
    texts = " | ".join(lbl.text() for lbl in sl.findChildren(type(sl.status_badge)))
    assert "Nr. 1" in texts
    assert "Formular SL13065" in texts
    assert "Swiss Life" in texts
    assert "✔ Aktiv" in sl.status_badge.text()
    assert "⚖" in texts
    letter = page.tiles["kuendigung_ordentlich"]
    letter_texts = " | ".join(lbl.text() for lbl in letter.findChildren(type(letter.status_badge)))
    assert LETTER_FORM_TEXT in letter_texts
    # 4 Gruppen als Abschnittsüberschriften
    titles = [
        lbl.text() for lbl in page.findChildren(type(sl.status_badge)) if lbl.objectName() == "SectionTitle"
    ]
    assert len(titles) == 4
    assert titles[0].startswith("Swiss Life bAV")


def test_use_button_disabled_without_open_case(qtbot, tmp_path):
    svc = make_service(tmp_path)
    current: list[str | None] = [None]
    used: list[str] = []
    page = CatalogPage(svc, current_case_id=lambda: current[0], use_template=used.append)
    qtbot.addWidget(page)
    assert not any(t.use_button.isEnabled() for t in page.tiles.values())
    assert REASON_NO_CASE in page.tiles[SL13065].use_button.toolTip()
    assert "Kein Vorgang geöffnet" in page.case_hint.text()
    current[0] = "VG-MUSTER-1"
    page.refresh()
    assert page.tiles[SL13065].use_button.isEnabled()
    assert not page.tiles[MISSING].use_button.isEnabled()


def test_use_template_and_open_studio_callbacks(qtbot, tmp_path):
    svc = make_service(tmp_path)
    used: list[str] = []
    opened: list[str] = []
    page = _page(qtbot, svc, used=used, opened=opened)
    qtbot.mouseClick(page.tiles[SL13065].use_button, Qt.MouseButton.LeftButton)
    assert used == [SL13065]
    # nicht verfügbare Vorlage löst keinen Rückruf aus
    page._on_use(MISSING)
    assert used == [SL13065]
    with qtbot.waitSignal(page.studio_requested, timeout=1000) as blocker:
        qtbot.mouseClick(page.tiles[MISSING].studio_button, Qt.MouseButton.LeftButton)
    assert blocker.args == [MISSING]
    assert opened == [MISSING]


def test_studio_button_navigates_in_main_window(qtbot, tmp_path, monkeypatch):
    patch_dialogs(monkeypatch)
    svc = make_service(tmp_path)
    try:
        from insurance_app.ui.main_window import MainWindow

        win = MainWindow(svc)
    except Exception as exc:  # noqa: BLE001 – andere Seiten (UI-A) evtl. noch nicht fertig
        pytest.skip(f"Hauptfenster nicht verfügbar ({type(exc).__name__})")
    try:
        win.show_section("catalog")
        catalog = win.pages["catalog"]
        qtbot.mouseClick(catalog.tiles[MISSING].studio_button, Qt.MouseButton.LeftButton)
        studio = win.pages["studio"]
        assert win.stack.currentWidget() is studio
        assert studio.catalog_id == MISSING
    finally:
        dispose(win)


def test_catalog_screenshot(qtbot, tmp_path, monkeypatch):
    patch_dialogs(monkeypatch)
    apply_app_style()
    svc = make_service(tmp_path)
    case = svc.create_sample_case()
    host, page = build_host(
        svc,
        "catalog",
        lambda: CatalogPage(svc, current_case_id=lambda: case.id, use_template=lambda _cid: None),
    )
    try:
        if hasattr(host, "case_view"):
            host.case_view.open_case(case.id)
            host.show_section("catalog")
        host.show()
        qtbot.waitExposed(host)
        page.refresh()
        assert len(page.tiles) == 9
        assert page.tiles[SL13065].use_button.isEnabled()
        qtbot.waitUntil(lambda: page.tiles[SL13065].isVisible(), timeout=5_000)  # Anzeige erfolgt verzögert
        qtbot.wait(50)
        image = host.grab()
        assert not image.isNull()
        target = screenshot_dir()
        if target is not None:
            assert image.save(str(target / "ui_catalog.png"))
    finally:
        dispose(host)
