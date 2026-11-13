"""Bildschirmfotos von Dashboard und Vorgangsansicht mit dem synthetischen Musterfall.

Ist ``LVT_SCREENSHOTS`` gesetzt (Ordnerpfad), werden PNG-Dateien gespeichert:
``ui_dashboard.png``, ``ui_case_documents.png``, ``ui_case_review.png``, ``ui_case_preview.png``.
Ohne die Variable werden die Seiten nur gerendert (kein Schreiben).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from uia_helpers import (
    SL13065,
    WINDOW_SIZE,
    DialogRecorder,
    flush_tasks,
    make_service,
    release_freigabe_contract_data,
    save_grab,
    style_window,
)

from insurance_app.ui.pages.case_view import TAB_DOCUMENTS, TAB_PREVIEW, CaseView
from insurance_app.ui.pages.dashboard import DashboardPage

pytestmark = pytest.mark.ui


@pytest.fixture()
def svc(tmp_path: Path):
    service = make_service(tmp_path)
    yield service
    flush_tasks()
    service.close()


def build_windows(qtbot, svc) -> tuple[QWidget, QWidget, DashboardPage, CaseView]:
    """Hauptfenster (wenn alle Seiten vorhanden sind), sonst die beiden Seiten einzeln."""
    try:
        from insurance_app.ui.main_window import MainWindow

        win = style_window(MainWindow(svc))
    except Exception:  # noqa: BLE001 – andere Seiten noch nicht verfügbar: Seiten einzeln zeigen
        case_view = style_window(CaseView(svc, show_catalog=lambda: None))
        dashboard = style_window(DashboardPage(svc, open_case=case_view.open_case))
        for w in (case_view, dashboard):
            qtbot.addWidget(w)
            w.show()
        return dashboard, case_view, dashboard, case_view
    qtbot.addWidget(win)
    win.show()
    return win, win, win.pages["dashboard"], win.case_view


def test_screenshots_dashboard_and_case(qtbot, svc, monkeypatch):
    DialogRecorder(monkeypatch)
    sample = svc.create_sample_case()
    dash_win, case_win, dashboard, case_view = build_windows(qtbot, svc)
    assert (dash_win.width(), dash_win.height()) == WINDOW_SIZE
    dashboard.refresh()
    assert dashboard.table.rowCount() == 1
    save_grab(dash_win, "ui_dashboard.png")

    if case_win is dash_win:
        case_win.open_case(sample.id)  # Hauptfenster: wechselt zur Vorgangsansicht
    else:
        case_view.open_case(sample.id)
    case_view.show_tab(TAB_DOCUMENTS)
    QApplication.processEvents()
    save_grab(case_win, "ui_case_documents.png")

    case_view.apply_template(SL13065)
    assert case_view.review.table.select_key("insured.street")  # Konfliktzeile
    QApplication.processEvents()
    assert case_view.review.evidence.canvas.rects  # markierte Fundstelle
    save_grab(case_win, "ui_case_review.png")

    # Daten wie ein Mensch freigeben, dann Vorschau erzeugen
    case = case_view.controller.case
    release_freigabe_contract_data(svc, case)
    svc.set_choice(case, "order.severance_consent", False)
    svc.set_choice(case, "order.waive_refund", False)
    case_view.refresh()
    case_view.show_tab(TAB_PREVIEW)
    case_view.preview.preview_btn.click()
    qtbot.waitUntil(
        lambda: case_view.preview.preview is not None and not case_view.controller.busy, timeout=30_000
    )
    assert case_view.preview.generate_btn.isEnabled()
    QApplication.processEvents()
    save_grab(case_win, "ui_case_preview.png")
