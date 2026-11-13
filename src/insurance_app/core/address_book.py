"""Lokales Adressbuch für Briefempfänger (z. B. Anschriften von Versicherern).

Die App erfindet keine Anschriften. Sie merkt sich nur, was der Anwender selbst in einem
Brief verwendet hat, und schlägt es beim nächsten Brief an denselben Empfänger wieder vor.
Die Datei liegt im lokalen Datenordner und enthält keine Kundendaten.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path

__all__ = ["AddressBook"]

log = logging.getLogger(__name__)
MAX_ENTRIES = 500
MAX_LINES = 6


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", " ", name.lower()).strip()


class AddressBook:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._entries: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            log.warning("Adressbuch nicht lesbar – es wird leer gestartet")
            return
        if not isinstance(data, dict):
            return
        for key, lines in data.items():
            if isinstance(key, str) and isinstance(lines, list) and all(isinstance(x, str) for x in lines):
                self._entries[key] = [x for x in lines if x.strip()][:MAX_LINES]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._entries, ensure_ascii=False, indent=1), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def entries(self) -> list[list[str]]:
        """Alle gemerkten Empfänger (je eine Liste von Anschriftzeilen), alphabetisch."""
        return [self._entries[k] for k in sorted(self._entries)]

    def lookup(self, name: str) -> list[str] | None:
        """Gemerkte Anschrift zu einem Namen; auch wenn der Name nur den Anfang trifft."""
        key = _key(name)
        if not key:
            return None
        if key in self._entries:
            return list(self._entries[key])
        for known, lines in self._entries.items():
            if known.startswith(key) or key.startswith(known):
                return list(lines)
        return None

    def remember(self, lines: list[str]) -> bool:
        """Empfänger merken (erste Zeile = Name). Rückgabe: wurde etwas gespeichert?"""
        clean = [" ".join(x.split()) for x in lines if x and x.strip()][:MAX_LINES]
        if len(clean) < 2:
            return False
        key = _key(clean[0])
        if not key or self._entries.get(key) == clean:
            return False
        if key not in self._entries and len(self._entries) >= MAX_ENTRIES:
            return False
        self._entries[key] = clean
        try:
            self._save()
        except OSError:
            log.warning("Adressbuch konnte nicht gespeichert werden")
            return False
        return True

    def forget(self, name: str) -> bool:
        if self._entries.pop(_key(name), None) is None:
            return False
        with contextlib.suppress(OSError):
            self._save()
        return True
