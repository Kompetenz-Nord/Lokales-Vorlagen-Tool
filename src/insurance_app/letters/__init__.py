"""Generierte Kundenschreiben nach DIN 5008 (Kündigung, Widerruf, Beitragsänderung, Betreuerwechsel)."""

from __future__ import annotations

from pathlib import Path

from .schema import LetterTemplate


def load_letter_template(path: Path) -> LetterTemplate:
    return LetterTemplate.model_validate_json(path.read_text(encoding="utf-8"))


def letter_templates_dir(config_dir: Path) -> Path:
    return config_dir / "letter_templates"
