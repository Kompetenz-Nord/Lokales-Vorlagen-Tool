"""Statusanzeige und Hardware-Hinweise für die optionale lokale KI."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from insurance_app.core.settings import Settings
from insurance_app.security.network_guard import NetworkBlockedError

from .ollama_client import InvalidModelNameError, OllamaClient, OllamaError, validate_model_name

__all__ = [
    "MODEL_CATALOG",
    "UNVERIFIED_NOTE",
    "HardwareInfo",
    "LocalAiState",
    "LocalAiStatus",
    "ModelCatalogEntry",
    "check_model_requirements",
    "detect_status",
    "find_ollama_binary",
    "hardware_info",
]

log = logging.getLogger(__name__)


class LocalAiState(StrEnum):
    NOT_INSTALLED = "Lokale KI nicht installiert"
    SETTING_UP = "Modell wird eingerichtet"
    READY = "Bereit"
    ERROR = "Fehler"
    DISABLED = "Deaktiviert"


class LocalAiStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: LocalAiState
    message: str  # verständliche deutsche Erklärung für die Oberfläche
    ollama_found: bool = False
    ollama_running: bool = False
    ollama_version: str | None = None
    model_name: str = ""
    model_present: bool = False


# ---------------------------------------------------------------------------
# Modellkatalog (Angaben ungeprüft!)
# ---------------------------------------------------------------------------

UNVERIFIED_NOTE = "Angaben ungeprüft – vor dem Download in der Ollama-Bibliothek prüfen (Größe, Lizenz)"


class ModelCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str
    approx_download_gb: float  # ungefähre Downloadgröße (laut Recherche, ungeprüft)
    min_ram_gb: float  # Mindestens empfohlener Arbeitsspeicher (Richtwert)
    recommended_ram_gb: float  # für flüssigen Betrieb (Richtwert)
    license_hint: str
    verified: bool = False  # Größe/Lizenz NICHT geprüft (kein Zugriff auf die Ollama-Bibliothek)
    note: str = UNVERIFIED_NOTE
    role: str = ""  # Aufgabe in der App
    advantages: str = ""  # Vorteil gegenüber den anderen Modellen
    num_ctx: int = 16384  # Kontextfenster für Bildanfragen (Ollama-Standard 4096 reicht für Bilder nicht)
    kind: str = "vlm"  # "ocr" (liefert Text -> Regel-Extraktion) | "vlm" (liefert Felder als JSON mit Zitat)
    sources: tuple[str, ...] = ()  # Quellen der Größenangaben (nur Information)


MODEL_CATALOG: dict[str, ModelCatalogEntry] = {
    "glm-ocr": ModelCatalogEntry(
        name="glm-ocr",
        label="GLM-OCR (Dokumenten- und Tabellen-Texterkennung)",
        kind="ocr",
        approx_download_gb=2.66,
        verified=True,
        note="Größe laut Ollama-Registry-Manifest (abgerufen am 13.11.2026). Lizenz ist im Manifest nicht angegeben – vor dem Einsatz beim Anbieter (Z.ai) prüfen.",
        min_ram_gb=8.0,
        recommended_ram_gb=16.0,
        license_hint="keine Lizenzangabe im Ollama-Manifest – beim Anbieter Z.ai prüfen",
        role="Texterkennung schwieriger Seiten (Scans, Fotos, Tabellen) – liefert Text, keine Feldzuordnung",
        advantages=(
            "Sehr klein (ca. 0,9 Mrd. Parameter) und spezialisiert auf Dokumente und Tabellen: "
            "liest Scans und Tabellen meist besser als Tesseract und läuft auch ohne Grafikkarte."
        ),
        sources=(
            "https://docs.z.ai/guides/vlm/glm-ocr",
            "https://huggingface.co/docs/transformers/main/en/model_doc/glm_ocr",
        ),
    ),
    "qwen3-vl:4b": ModelCatalogEntry(
        name="qwen3-vl:4b",
        label="Qwen3-VL 4B (präzise visuelle PDF- und Bildanalyse)",
        approx_download_gb=3.34,
        verified=True,
        note="Größe laut Ollama-Registry-Manifest (abgerufen am 13.11.2026).",
        min_ram_gb=8.0,
        recommended_ram_gb=16.0,
        license_hint="Apache 2.0 (laut Ollama-Manifest)",
        role="Feldextraktion aus Seitenbildern als strukturiertes JSON mit wörtlichem Zitat als Beleg",
        advantages=(
            "Versteht Layout und Bildinhalt gemeinsam und ordnet Werte direkt den Feldern zu; "
            "guter Kompromiss aus Genauigkeit und Ressourcenbedarf."
        ),
        sources=("https://codersera.com/blog/qwen3-vl-4b-vs-qwen3-vl-8b-benchmarks-vram-guide/",),
    ),
    "gemma4:e4b": ModelCatalogEntry(
        name="gemma4:e4b",
        label="Gemma 4 E4B (multimodales Allround-Modell)",
        approx_download_gb=6.58,
        verified=True,
        min_ram_gb=16.0,
        recommended_ram_gb=32.0,
        license_hint="Apache 2.0 (laut Ollama-Manifest)",
        role="Unabhängige Gegenprüfung der Feldextraktion (zweites Modell, andere Modellfamilie)",
        advantages=(
            "Zweite, unabhängige Einschätzung: Weichen Qwen3-VL und Gemma 4 voneinander ab, "
            "markiert die App das Feld als Konflikt – Fehler einzelner Modelle fallen so eher auf."
        ),
        note="Größe laut Ollama-Registry-Manifest (abgerufen am 13.11.2026).",
        sources=(
            "https://www.itechguides.com/?p=331053",
            "https://www.developersdigest.tech/local-models/gemma-4-e4b",
        ),
    ),
    "qwen3-vl:8b": ModelCatalogEntry(
        name="qwen3-vl:8b",
        label="Qwen3-VL 8B (genauer, langsamer)",
        approx_download_gb=6.1,
        min_ram_gb=16.0,
        recommended_ram_gb=32.0,
        license_hint="vermutlich Apache 2.0 – bitte prüfen",
        role="Alternative Feldextraktion mit größerem Modell",
        advantages="Etwas genauer als die 4B-Variante, benötigt aber deutlich mehr Arbeitsspeicher.",
    ),
}


# ---------------------------------------------------------------------------
# Ollama-Programm finden
# ---------------------------------------------------------------------------


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    if sys.platform == "darwin":
        paths.append(Path("/Applications/Ollama.app/Contents/Resources/ollama"))
        paths.append(Path.home() / "Applications" / "Ollama.app" / "Contents" / "Resources" / "ollama")
        paths.append(Path("/usr/local/bin/ollama"))
        paths.append(Path("/opt/homebrew/bin/ollama"))
    elif sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
        program_files = os.environ.get("PROGRAMFILES")
        if program_files:
            paths.append(Path(program_files) / "Ollama" / "ollama.exe")
    else:
        paths.append(Path("/usr/local/bin/ollama"))
        paths.append(Path("/usr/bin/ollama"))
    return paths


def find_ollama_binary() -> Path | None:
    """Pfad des Ollama-Programms (PATH oder typische Installationsorte) oder ``None``."""
    found = shutil.which("ollama")
    if found:
        return Path(found)
    for candidate in _candidate_paths():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def detect_status(
    settings: Settings, client: OllamaClient | None = None, pulling: bool = False
) -> LocalAiStatus:
    """Aktuellen Zustand der lokalen KI ermitteln (wirft nie).

    Kontaktiert ausschließlich den lokalen Ollama-Dienst (Loopback).
    """
    model = settings.ai_model
    found = find_ollama_binary() is not None
    base = {"ollama_found": found, "model_name": model}

    if client is None:
        try:
            client = OllamaClient(settings.ollama_url, timeout=3.0)
        except (NetworkBlockedError, ValueError):
            if not settings.ai_enabled:
                return LocalAiStatus(
                    state=LocalAiState.DISABLED,
                    message="Die lokale KI ist ausgeschaltet. Die App funktioniert vollständig ohne sie.",
                    **base,
                )
            return LocalAiStatus(
                state=LocalAiState.ERROR,
                message="Die eingestellte Ollama-Adresse zeigt nicht auf diesen Rechner. Erlaubt ist nur "
                "eine lokale Adresse wie http://127.0.0.1:11434.",
                **base,
            )

    running = client.is_running()
    version = client.version() if running else None
    found = found or running
    base = {"ollama_found": found, "ollama_running": running, "ollama_version": version, "model_name": model}

    if not settings.ai_enabled:
        return LocalAiStatus(
            state=LocalAiState.DISABLED,
            message="Die lokale KI ist ausgeschaltet. Die App funktioniert vollständig ohne sie.",
            **base,
        )
    if not found:
        return LocalAiStatus(
            state=LocalAiState.NOT_INSTALLED,
            message="Lokale KI nicht installiert: Ollama wurde auf diesem Rechner nicht gefunden. "
            "Die App funktioniert auch ohne lokale KI.",
            **base,
        )
    if not running:
        return LocalAiStatus(
            state=LocalAiState.ERROR,
            message="Ollama ist installiert, läuft aber nicht. Bitte Ollama starten und erneut prüfen.",
            **base,
        )
    try:
        validate_model_name(model)
    except InvalidModelNameError as exc:
        return LocalAiStatus(state=LocalAiState.ERROR, message=str(exc), **base)
    try:
        present = client.has_model(model)
    except OllamaError as exc:
        log.info("Modellliste nicht lesbar (%s)", type(exc).__name__)
        return LocalAiStatus(state=LocalAiState.ERROR, message=str(exc), **base)
    if pulling:
        return LocalAiStatus(
            state=LocalAiState.SETTING_UP,
            message=f"Modell „{model}“ wird eingerichtet. Das kann je nach Verbindung dauern.",
            model_present=present,
            **base,
        )
    if present:
        return LocalAiStatus(
            state=LocalAiState.READY,
            message=f"Bereit: Modell „{model}“ ist lokal vorhanden.",
            model_present=True,
            **base,
        )
    return LocalAiStatus(
        state=LocalAiState.NOT_INSTALLED,
        message=f"Ollama läuft, aber das Modell „{model}“ ist noch nicht geladen. Der Download startet "
        "nur nach Ihrer Zustimmung.",
        model_present=False,
        **base,
    )


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------


class HardwareInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_ram_gb: float | None = None
    available_ram_gb: float | None = None
    free_disk_gb: float | None = None  # freier Platz am Modellordner
    cpu_count: int | None = None
    platform: str = ""
    models_dir: str = ""


_GB = 1024**3


def _ram_info() -> tuple[float | None, float | None]:
    try:
        import psutil  # optional
    except ImportError:
        psutil = None  # type: ignore[assignment]
    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            return round(vm.total / _GB, 1), round(vm.available / _GB, 1)
        except Exception as exc:  # psutil kann auf exotischen Systemen scheitern
            log.debug("psutil nicht nutzbar (%s)", type(exc).__name__)
    if hasattr(os, "sysconf"):
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            total = os.sysconf("SC_PHYS_PAGES") * page
            avail: float | None = None
            if "SC_AVPHYS_PAGES" in os.sysconf_names:
                avail = round(os.sysconf("SC_AVPHYS_PAGES") * page / _GB, 1)
            return round(total / _GB, 1), avail
        except (OSError, ValueError):
            pass
    return None, None


def _default_models_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".ollama" / "models"


def _free_disk_gb(path: Path) -> float | None:
    probe = path
    for _ in range(64):
        if probe.exists():
            break
        if probe.parent == probe:
            break
        probe = probe.parent
    try:
        return round(shutil.disk_usage(probe).free / _GB, 1)
    except OSError:
        return None


def hardware_info(models_dir: Path | None = None) -> HardwareInfo:
    """Arbeitsspeicher, freier Speicherplatz am Modellordner, CPU-Kerne, Plattform."""
    target = Path(models_dir) if models_dir is not None else _default_models_dir()
    total, available = _ram_info()
    return HardwareInfo(
        total_ram_gb=total,
        available_ram_gb=available,
        free_disk_gb=_free_disk_gb(target),
        cpu_count=os.cpu_count(),
        platform=f"{platform.system()} {platform.machine()}".strip(),
        models_dir=str(target),
    )


def check_model_requirements(model: str, hw: HardwareInfo) -> list[str]:
    """Deutsche Hinweise, falls Arbeitsspeicher oder Speicherplatz knapp sind (leer = passt)."""
    entry = MODEL_CATALOG.get(model)
    if entry is None:
        return [f"Für „{model}“ liegen keine Größenangaben vor. Bitte in der Ollama-Bibliothek prüfen."]
    hints: list[str] = []
    if hw.total_ram_gb is not None and hw.total_ram_gb < entry.min_ram_gb:
        hints.append(
            f"Arbeitsspeicher knapp: {hw.total_ram_gb:.0f} GB vorhanden, empfohlen sind mindestens "
            f"{entry.min_ram_gb:.0f} GB."
        )
    if hw.free_disk_gb is not None and hw.free_disk_gb < entry.approx_download_gb * 1.5:
        hints.append(
            f"Speicherplatz knapp: {hw.free_disk_gb:.1f} GB frei, benötigt werden etwa "
            f"{entry.approx_download_gb * 1.5:.1f} GB."
        )
    if not entry.verified:
        hints.append(entry.note)
    return hints
