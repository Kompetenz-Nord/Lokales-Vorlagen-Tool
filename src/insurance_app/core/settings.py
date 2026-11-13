"""Lokale Einstellungen (JSON im Datenordner, keine Cloud-Synchronisation)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    # Datenschutz: In dieser Version unveränderlich LOCAL ONLY.
    local_only: bool = True

    # OCR
    ocr_backend: str = "tesseract"  # "tesseract" | "rapidocr" | "docling"
    ocr_languages: str = "deu+eng"
    ocr_dpi: int = 300
    min_text_chars_per_page: int = 40  # darunter gilt eine Seite als "ohne brauchbaren Text"
    ocr_low_confidence: float = 0.70  # mittlere Wortkonfidenz darunter -> Warnung + manuelle Prüfung
    tesseract_cmd: str | None = None  # optional expliziter Pfad zu tesseract(.exe)
    ocr_timeout_s: int = 240  # maximale Laufzeit je Seite
    docling_artifacts_path: str | None = None  # lokaler Modellordner für Docling (optional)

    # Lokale KI (optional, standardmäßig AUS)
    ai_enabled: bool = False
    ai_profile: str = "standard"  # Hardwareprofil (nur Empfehlung, siehe local_ai.profiles)
    ai_model: str = "glm-ocr"  # AKTIVES Modell – der Anwender entscheidet (Standard: glm-ocr)
    ai_fallback_model: str | None = "qwen3-vl:4b"  # springt ein, wenn das aktive Modell fehlt/scheitert
    ai_crosscheck_model: str | None = None  # optionale Gegenprüfung (z. B. gemma4:e4b)
    ollama_url: str = DEFAULT_OLLAMA_URL
    ai_timeout_s: int = 600  # je Seite; auf reinen CPU-Rechnern brauchen Bildmodelle oft mehrere Minuten
    ai_max_tokens: int = 2048

    # Export
    export_dir: str | None = None  # None -> AppPaths.exports
    flatten_default: bool = False  # finale PDF standardmäßig ausfüllbar lassen
    letter_form: str = "B"  # DIN 5008 Form A oder B

    # Oberfläche
    first_run_completed: bool = False

    @field_validator("local_only")
    @classmethod
    def _force_local(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Diese Version unterstützt ausschließlich den Modus LOCAL ONLY.")
        return v

    @field_validator("letter_form")
    @classmethod
    def _letter_form(cls, v: str) -> str:
        if v not in {"A", "B"}:
            raise ValueError("Briefformat muss DIN 5008 Form A oder B sein.")
        return v

    @field_validator("ocr_languages")
    @classmethod
    def _languages(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-z_]{3,8}(\+[a-z_]{3,8})*", v or ""):
            raise ValueError("Ungültige OCR-Sprachangabe (Beispiel: deu+eng).")
        return v

    @field_validator("ocr_backend")
    @classmethod
    def _backend(cls, v: str) -> str:
        if v not in {"tesseract", "rapidocr", "docling"}:
            raise ValueError(f"Unbekanntes OCR-Backend: {v}")
        return v

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> Settings:
        if not path.exists():
            return cls()
        try:
            return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            log.warning(
                "Einstellungen konnten nicht gelesen werden (%s) – Standardwerte aktiv.", type(exc).__name__
            )
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)


class SettingsChange(BaseModel):
    """Hilfsmodell für die Oberfläche (nur geänderte Werte)."""

    values: dict[str, object] = Field(default_factory=dict)
