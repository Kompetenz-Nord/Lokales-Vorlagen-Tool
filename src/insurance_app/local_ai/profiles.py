"""Hardwareprofile für die optionale lokale KI.

Ein Profil legt fest, welche lokalen Modelle (Ollama) die App verwendet:

| Profil   | aktiv   | Ausweichmodell | Gegenprüfung | Download (laut Registry) |
|----------|---------|----------------|--------------|--------------------------|
| kompakt  | glm-ocr | –              | –            | 2,66 GB |
| standard | glm-ocr | qwen3-vl:4b    | –            | 6,00 GB |
| maximal  | glm-ocr | qwen3-vl:4b    | gemma4:e4b   | 12,58 GB |

Das Profil ist nur eine **Empfehlung**: Der Anwender wählt in den Einstellungen selbst, welches
Modell aktiv läuft und welches als Ausweichmodell einspringt.

Die Hardwareangaben sind **Richtwerte** (nicht auf Zielgeräten gemessen). Modelle werden
nacheinander geladen; mehr Arbeitsspeicher bzw. eine Grafikkarte machen den Ablauf flüssiger.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .status import MODEL_CATALOG, HardwareInfo


class HardwareRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mac: str
    windows: str
    min_ram_gb: float
    recommended_ram_gb: float
    disk_gb: float


class AiProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    description: str
    active_model: str
    fallback_model: str | None
    crosscheck_model: str | None
    requirement: HardwareRequirement

    @property
    def models(self) -> list[str]:
        return [m for m in (self.active_model, self.fallback_model, self.crosscheck_model) if m]

    @property
    def approx_download_gb(self) -> float:
        return round(sum(MODEL_CATALOG[m].approx_download_gb for m in self.models if m in MODEL_CATALOG), 1)


PROFILES: dict[str, AiProfile] = {
    "kompakt": AiProfile(
        key="kompakt",
        label="Kompakt – nur KI-Texterkennung",
        description=(
            "GLM-OCR liest schwierige Scans, Fotos und Tabellen. Die Feldzuordnung übernehmen weiterhin "
            "die geprüften Regeln der App. Geringster Ressourcenbedarf."
        ),
        active_model="glm-ocr",
        fallback_model=None,
        crosscheck_model=None,
        requirement=HardwareRequirement(
            mac="Apple Silicon (M1 oder neuer) mit mindestens 8 GB gemeinsamem Arbeitsspeicher",
            windows="64-Bit-Windows mit mindestens 8 GB RAM; Grafikkarte nicht erforderlich",
            min_ram_gb=8.0,
            recommended_ram_gb=16.0,
            disk_gb=4.0,
        ),
    ),
    "standard": AiProfile(
        key="standard",
        label="Standard – GLM-OCR mit Qwen3-VL als Ausweichmodell (empfohlen)",
        description=(
            "GLM-OCR arbeitet als aktives Modell. Fehlt es oder kann eine Seite nicht lesen, springt Qwen3-VL ein "
            "und liest die Felder direkt aus dem Seitenbild – jeder Wert mit wörtlichem Zitat als Beleg."
        ),
        active_model="glm-ocr",
        fallback_model="qwen3-vl:4b",
        crosscheck_model=None,
        requirement=HardwareRequirement(
            mac="Apple Silicon (M1 oder neuer) mit 16 GB gemeinsamem Arbeitsspeicher",
            windows="16 GB RAM; empfohlen: NVIDIA-Grafikkarte mit mindestens 6–8 GB Grafikspeicher (ohne GPU deutlich langsamer)",
            min_ram_gb=16.0,
            recommended_ram_gb=16.0,
            disk_gb=8.0,
        ),
    ),
    "maximal": AiProfile(
        key="maximal",
        label="Maximal – mit Gegenprüfung durch zweites Modell",
        description=(
            "Zusätzlich prüft Gemma 4 unabhängig nach. Weichen die Modelle ab, entscheidet der Mensch. "
            "Höchste Absicherung, aber größter Speicher- und Zeitbedarf."
        ),
        active_model="glm-ocr",
        fallback_model="qwen3-vl:4b",
        crosscheck_model="gemma4:e4b",
        requirement=HardwareRequirement(
            mac="Apple Silicon mit 32 GB gemeinsamem Arbeitsspeicher (z. B. M-Pro/Max); 16 GB möglich, aber langsamer",
            windows="32 GB RAM und NVIDIA-Grafikkarte mit mindestens 12 GB Grafikspeicher empfohlen",
            min_ram_gb=16.0,
            recommended_ram_gb=32.0,
            disk_gb=16.0,
        ),
    ),
}

DEFAULT_PROFILE = "standard"


def get_profile(key: str | None) -> AiProfile:
    return PROFILES.get(key or DEFAULT_PROFILE, PROFILES[DEFAULT_PROFILE])


def recommend_profile(hw: HardwareInfo) -> tuple[str | None, str]:
    """Profilvorschlag anhand von Arbeitsspeicher und freiem Speicherplatz.

    Gibt ``(profil_key | None, Begründung)`` zurück; ``None`` = lokale KI nicht empfohlen.
    """
    ram = hw.total_ram_gb
    disk = hw.free_disk_gb
    if ram is None:
        return (
            DEFAULT_PROFILE,
            "Arbeitsspeicher konnte nicht ermittelt werden – Standard vorgeschlagen, bitte selbst prüfen.",
        )
    if ram < 8:
        return (
            None,
            f"{ram:.0f} GB Arbeitsspeicher reichen für die lokale KI nicht flüssig aus. Die App arbeitet ohne KI.",
        )
    if ram < 16:
        key = "kompakt"
    elif ram < 32:
        key = "standard"
    else:
        key = "maximal"
    reason = f"{ram:.0f} GB Arbeitsspeicher erkannt."
    need = PROFILES[key].requirement.disk_gb
    if disk is not None and disk < need:
        order = ["kompakt", "standard", "maximal"]
        while key != "kompakt" and disk < PROFILES[key].requirement.disk_gb:
            key = order[order.index(key) - 1]
        reason += f" Freier Speicherplatz ({disk:.0f} GB) begrenzt die Modellauswahl."
        if disk < PROFILES["kompakt"].requirement.disk_gb:
            return None, reason + " Für die Modelle fehlt Speicherplatz."
    return key, reason


def settings_for_profile(key: str) -> dict[str, object]:
    """Einstellungswerte, die ein Profil setzt (für ``AppService.update_settings``)."""
    prof = get_profile(key)
    return {
        "ai_profile": prof.key,
        "ai_model": prof.active_model,
        "ai_fallback_model": prof.fallback_model,
        "ai_crosscheck_model": prof.crosscheck_model,
    }
