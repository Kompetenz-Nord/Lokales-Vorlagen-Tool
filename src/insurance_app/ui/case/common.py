"""Gemeinsame Hilfen für die Vorgangsansicht (Fehlertexte, Bilder, kleine Anzeige-Bausteine)."""

from __future__ import annotations

import html
from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QLayout, QWidget

from ...core.models import Severity, ValidationMessage
from .. import workers
from .formatting import severity_color, severity_symbol


def error_text(exc: BaseException) -> str:
    """Verständliche deutsche Fehlermeldung (Fachfehler der App zeigen ihren eigenen Text)."""
    msg = getattr(exc, "user_message", None)
    if msg:
        return str(msg)
    module = type(exc).__module__ or ""
    if module.startswith("insurance_app") and str(exc):
        return str(exc)  # eigene Fehlerklassen tragen bereits deutsche Meldungen
    return workers.user_message(exc)


def pil_to_qimage(img) -> QImage:
    """PIL-Bild (beliebiger Modus) -> eigenständige QImage-Kopie."""
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, 3 * rgb.width, QImage.Format.Format_RGB888)
    return qimg.copy()  # Daten von ``data`` lösen


def clear_layout(layout: QLayout) -> None:
    """Alle Elemente eines Layouts entfernen (Widgets werden gelöscht)."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def message_html(messages: Iterable[ValidationMessage], limit: int | None = None) -> str:
    """Meldungen als kurze HTML-Liste mit Symbol und Farbe (nie nur Farbe)."""
    rows = []
    msgs = list(messages)
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    msgs.sort(key=lambda m: order.get(m.severity, 3))
    shown = msgs if limit is None else msgs[:limit]
    for m in shown:
        rows.append(
            f'<div style="color:{severity_color(m.severity)}">{severity_symbol(m.severity)}&nbsp;'
            f"{html.escape(m.message)}</div>"
        )
    if limit is not None and len(msgs) > limit:
        rows.append(f"<div>… und {len(msgs) - limit} weitere Meldungen</div>")
    return "".join(rows)


def wrap_label(text: str = "", *, rich: bool = False, selectable: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def set_dynamic_property(widget: QWidget, name: str, value: object) -> None:
    """Dynamische Eigenschaft setzen und Stylesheet neu anwenden (z. B. ``dragActive``)."""
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
