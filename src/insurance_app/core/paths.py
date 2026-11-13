"""Plattformneutrale Pfade für Programmdateien und lokale Laufzeitdaten.

* Keine absoluten, plattformabhängigen Pfade im Code.
* Aus dem Quellcode gestartet ("Entwicklermodus") liegen Laufzeitdaten in
  ``<Projekt>/user_data`` und Exporte in ``<Projekt>/exports`` (beide per
  .gitignore ausgeschlossen).
* Als gebaute App (PyInstaller) liegen sie im Benutzerprofil:
  - Windows: ``%LOCALAPPDATA%\\LokalesVorlagenTool``
  - macOS:   ``~/Library/Application Support/LokalesVorlagenTool``
  - Linux:   ``~/.local/share/lokales-vorlagen-tool``
  Exporte standardmäßig in ``~/Documents/Vorlagen-Exporte``.
* Überschreibbar per Umgebungsvariablen ``VORLAGEN_TOOL_DATA_DIR`` und
  ``VORLAGEN_TOOL_EXPORT_DIR`` oder in den Einstellungen (Exportordner).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "LokalesVorlagenTool"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Wurzel der mitgelieferten Ressourcen (assets/, config/)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # src/insurance_app/core/paths.py -> Projektwurzel
    return Path(__file__).resolve().parents[3]


def _platform_data_dir() -> Path:
    home = Path.home()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / APP_DIR_NAME
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / "lokales-vorlagen-tool"


def _platform_export_dir() -> Path:
    return Path.home() / "Documents" / "Vorlagen-Exporte"


@dataclass(frozen=True)
class AppPaths:
    resources: Path  # mitgelieferte Dateien (nur lesen)
    data: Path  # lokale Laufzeitdaten (DB, Vorgänge, importierte Vorlagen)
    exports: Path  # finale PDF-Ausgaben

    # --- abgeleitete Pfade -------------------------------------------------
    @property
    def config_dir(self) -> Path:
        return self.resources / "config"

    @property
    def assets_dir(self) -> Path:
        return self.resources / "assets"

    @property
    def original_templates_dir(self) -> Path:
        return self.assets_dir / "templates" / "originals"

    @property
    def fonts_dir(self) -> Path:
        return self.assets_dir / "fonts"

    @property
    def database(self) -> Path:
        return self.data / "vorgaenge.sqlite3"

    @property
    def cases_dir(self) -> Path:
        return self.data / "cases"

    @property
    def user_templates_dir(self) -> Path:
        return self.data / "templates"

    @property
    def temp_dir(self) -> Path:
        return self.data / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.data / "logs"

    @property
    def settings_file(self) -> Path:
        return self.data / "settings.json"

    def ensure(self) -> AppPaths:
        for p in (
            self.data,
            self.exports,
            self.cases_dir,
            self.user_templates_dir,
            self.temp_dir,
            self.logs_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def default(cls) -> AppPaths:
        res = resource_root()
        env_data = os.environ.get("VORLAGEN_TOOL_DATA_DIR")
        env_exp = os.environ.get("VORLAGEN_TOOL_EXPORT_DIR")
        if is_frozen():
            data = Path(env_data) if env_data else _platform_data_dir()
            exports = Path(env_exp) if env_exp else _platform_export_dir()
        else:
            data = Path(env_data) if env_data else res / "user_data"
            exports = Path(env_exp) if env_exp else res / "exports"
        return cls(resources=res, data=data, exports=exports)

    @classmethod
    def for_testing(cls, tmp: Path) -> AppPaths:
        """Isolierte Pfade für Tests (Ressourcen aus dem Projekt, Daten im tmp-Ordner)."""
        return cls(resources=resource_root(), data=tmp / "user_data", exports=tmp / "exports").ensure()
