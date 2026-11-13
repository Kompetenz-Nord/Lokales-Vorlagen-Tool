"""Zentrale Dialog-Helfer.

Alle Seiten rufen modale Dialoge NUR über diese Funktionen auf
(``from .. import dialogs`` und dann ``dialogs.ask_confirm(...)``),
damit Tests sie per ``monkeypatch`` ersetzen können und die Texte einheitlich bleiben.
Passwörter werden nie gespeichert oder protokolliert.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QWidget

DOCUMENT_FILTER = (
    "Dokumente (*.pdf *.png *.jpg *.jpeg *.tif *.tiff);;PDF (*.pdf);;Bilder (*.png *.jpg *.jpeg *.tif *.tiff)"
)


def ask_confirm(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    ok_text: str = "Ja",
    cancel_text: str = "Abbrechen",
    danger: bool = False,
) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(
        box.buttons()[-1] if danger else ok
    )  # bei gefährlichen Aktionen ist "Abbrechen" Standard
    box.exec()
    return box.clickedButton() is ok


def show_info(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def show_error(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def ask_password(parent: QWidget | None, file_name: str, wrong: bool = False) -> str | None:
    """Passwort für ein geschütztes PDF abfragen (Eingabe verdeckt, nichts wird gespeichert)."""
    prompt = (
        "Das Passwort war falsch.\n\n" if wrong else ""
    ) + f"„{file_name}“ ist passwortgeschützt.\nBitte das Passwort eingeben (es wird nicht gespeichert):"
    text, ok = QInputDialog.getText(parent, "Passwort erforderlich", prompt, QLineEdit.EchoMode.Password)
    return text if ok and text else None


def ask_password_again(parent: QWidget | None, file_name: str, wrong: bool = False) -> str | None:
    """Passwort für ein bereits übernommenes, geschütztes PDF erneut abfragen (z. B. nach Neustart)."""
    prompt = ("✖ Das Passwort war falsch. Bitte erneut eingeben.\n\n" if wrong else "") + (
        f"🔒 „{file_name}“ ist passwortgeschützt.\n\n"
        "Aus Sicherheitsgründen speichert die App Passwörter nie. Nach einem Neustart "
        "muss das Passwort deshalb erneut eingegeben werden, damit die Datei wieder "
        "gelesen oder angezeigt werden kann.\n\n"
        "Bitte das Passwort eingeben (es bleibt nur bis zum Beenden der App im Arbeitsspeicher):"
    )
    text, ok = QInputDialog.getText(
        parent, "Passwort erneut erforderlich", prompt, QLineEdit.EchoMode.Password
    )
    return text if ok and text else None


def ask_text(parent: QWidget | None, title: str, label: str, value: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, value)
    return text if ok else None


def pick_open_files(
    parent: QWidget | None, title: str = "Dokumente auswählen", file_filter: str = DOCUMENT_FILTER
) -> list[Path]:
    files, _ = QFileDialog.getOpenFileNames(parent, title, str(Path.home()), file_filter)
    return [Path(f) for f in files]


def pick_open_file(parent: QWidget | None, title: str, file_filter: str) -> Path | None:
    file, _ = QFileDialog.getOpenFileName(parent, title, str(Path.home()), file_filter)
    return Path(file) if file else None


def pick_save_file(parent: QWidget | None, title: str, suggested: str, file_filter: str) -> Path | None:
    file, _ = QFileDialog.getSaveFileName(parent, title, str(Path.home() / suggested), file_filter)
    return Path(file) if file else None


def pick_directory(parent: QWidget | None, title: str, start: Path | None = None) -> Path | None:
    d = QFileDialog.getExistingDirectory(parent, title, str(start or Path.home()))
    return Path(d) if d else None


def open_path(path: Path) -> None:
    """Datei oder Ordner mit dem Standardprogramm des Systems öffnen (lokal)."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
