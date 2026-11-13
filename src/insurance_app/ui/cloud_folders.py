"""Erkennen, ob ein Ordner in einem typischen Cloud-Sync-Ordner liegt.

Reine Textprüfung des Pfads (kein Dateizugriff, kein Netzwerk). Die App selbst sendet nie
etwas; liegt der Exportordner aber z. B. in „OneDrive“, lädt das Betriebssystem bzw. das
Sync-Programm die fertigen PDFs zu diesem Anbieter hoch. Darauf weist die Oberfläche hin.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePath

__all__ = ["cloud_sync_provider", "cloud_sync_warning"]

# (Anbieter, Muster für EINEN Ordnernamen, bereits kleingeschrieben)
_COMPONENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("iCloud Drive", re.compile(r"^(icloud ?drive|iclouddrive|icloud~.*|com~apple~clouddocs)$")),
    ("OneDrive", re.compile(r"^onedrive( ?[-–] ?.+)?$")),
    ("Dropbox", re.compile(r"^dropbox( ?\(.+\))?$")),
    (
        "Google Drive",
        re.compile(r"^(google ?drive(-.+)?|my drive|meine ablage|googledrive-.+|google drive file stream)$"),
    ),
    ("Nextcloud", re.compile(r"^nextcloud\d*$")),
    ("pCloud", re.compile(r"^pcloud ?(drive|sync)?$")),
    ("Box", re.compile(r"^(box|box sync|box-box)$")),
)
# macOS „Dateianbieter“: ~/Library/CloudStorage/<Anbieter>-<Konto>
_CLOUD_STORAGE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("onedrive", "OneDrive"),
    ("dropbox", "Dropbox"),
    ("googledrive", "Google Drive"),
    ("nextcloud", "Nextcloud"),
    ("pcloud", "pCloud"),
    ("box", "Box"),
    ("icloud", "iCloud Drive"),
)


def _components(path: str | PurePath) -> list[str]:
    """Pfad in kleingeschriebene Ordnernamen zerlegen (Windows- und POSIX-Schreibweise)."""
    text = str(path).replace("\\", "/")
    return [part.strip().lower() for part in text.split("/") if part.strip()]


def cloud_sync_provider(path: str | PurePath | None) -> str | None:
    """Name des Cloud-Anbieters, in dessen Sync-Ordner ``path`` vermutlich liegt – sonst ``None``.

    Groß-/Kleinschreibung ist egal; geprüft werden ganze Ordnernamen (ein Ordner
    „Dropbox-Rechnungen“ oder „Boxen“ löst keine Warnung aus).
    """
    if path is None or not str(path).strip():
        return None
    parts = _components(path)
    for idx, part in enumerate(parts):
        prev = parts[idx - 1] if idx else ""
        if part == "mobile documents" and prev == "library":
            return "iCloud Drive"
        if prev == "cloudstorage" and idx >= 2 and parts[idx - 2] == "library":
            for prefix, provider in _CLOUD_STORAGE_PREFIXES:
                if part.startswith(prefix):
                    return provider
        for provider, pattern in _COMPONENT_RULES:
            if pattern.match(part):
                return provider
    return None


def cloud_sync_warning(path: str | Path | None) -> str:
    """Warntext (Symbol + Text) für die Oberfläche; leer, wenn kein Cloud-Ordner erkannt wurde."""
    provider = cloud_sync_provider(path)
    if provider is None:
        return ""
    return (
        f"⚠ Achtung: Dieser Ordner liegt vermutlich in einem Cloud-Ordner ({provider}). "
        f"Ihr Betriebssystem bzw. das Sync-Programm lädt dort gespeicherte Dateien automatisch zu "
        f"{provider} hoch. Die App selbst sendet nichts – für einen rein lokalen Ablauf wählen Sie "
        "bitte einen Ordner außerhalb von Cloud-Ordnern."
    )
