"""SQLite-Datenbank der App (Standardbibliothek, WAL, Fremdschlüssel, Migrationen).

Die Datenbank liegt ausschließlich lokal im Datenordner. Vorgänge werden als
validiertes JSON (``cases.data_json``) plus Index-Spalten gespeichert.

Schema-Version 1
----------------
* ``cases``             – ein Datensatz je Vorgang (JSON + Such-/Listenspalten)
* ``documents``         – Quelldokumente je Vorgang (Kopie im Vorgangsordner)
* ``history``           – Änderungsverlauf (nur lokal, nie in Logs)
* ``export_log``        – Exportprotokoll (nur Dateiname, keine Personendaten)
* ``template_versions`` – importierte/geprüfte Vorlagenversionen
* ``schema_migrations`` – angewendete Migrationen

Schema-Version 2
----------------
* ``cases.revision``    – Speicherstand des Vorgangs (optimistische Sperre: ein
  veralteter Stand darf einen neueren nicht überschreiben)

Rechte: Datenbank, WAL- und SHM-Datei sind auf POSIX-Systemen nur für den
eigenen Benutzer lesbar (``0600``).
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

__all__ = ["LATEST_SCHEMA_VERSION", "MIGRATIONS", "Database", "StorageError"]

log = logging.getLogger(__name__)


class StorageError(Exception):
    """Fehler beim lokalen Speichern oder Lesen (deutsche Meldung für die Oberfläche)."""


_V1: tuple[str, ...] = (
    """
    CREATE TABLE cases (
        id              TEXT PRIMARY KEY,
        title           TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        stage           INTEGER NOT NULL DEFAULT 1,
        template_id     TEXT,
        archived        INTEGER NOT NULL DEFAULT 0,
        data_json       TEXT NOT NULL,
        search_text     TEXT NOT NULL DEFAULT '',
        document_count  INTEGER NOT NULL DEFAULT 0,
        open_issues     INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX idx_cases_updated ON cases(archived, updated_at DESC)",
    """
    CREATE TABLE documents (
        id              TEXT NOT NULL,
        case_id         TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        sha256          TEXT NOT NULL,
        original_name   TEXT NOT NULL,
        stored_path     TEXT NOT NULL,
        added_at        TEXT NOT NULL,
        PRIMARY KEY (case_id, id)
    )
    """,
    "CREATE INDEX idx_documents_sha ON documents(case_id, sha256)",
    """
    CREATE TABLE history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id         TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        timestamp       TEXT NOT NULL,
        actor           TEXT NOT NULL,
        action          TEXT NOT NULL,
        field_key       TEXT,
        old_status      TEXT,
        new_status      TEXT,
        old_value       TEXT,
        new_value       TEXT,
        source          TEXT,
        detail          TEXT
    )
    """,
    "CREATE INDEX idx_history_case ON history(case_id, id)",
    """
    CREATE TABLE export_log (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id          TEXT NOT NULL,
        timestamp        TEXT NOT NULL,
        template_id      TEXT NOT NULL,
        template_version TEXT NOT NULL,
        file_name        TEXT NOT NULL,
        flattened        INTEGER NOT NULL DEFAULT 0,
        qa_passed        INTEGER NOT NULL DEFAULT 0,
        sha256           TEXT
    )
    """,
    "CREATE INDEX idx_export_case ON export_log(case_id, id)",
    """
    CREATE TABLE template_versions (
        catalog_id      TEXT NOT NULL,
        version         TEXT NOT NULL,
        pdf_path        TEXT NOT NULL,
        sha256          TEXT NOT NULL,
        imported_at     TEXT NOT NULL,
        confirmed       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (catalog_id, version)
    )
    """,
)

_V2: tuple[str, ...] = (
    # Bestehende Vorgänge erhalten Stand 1 (0 bedeutet "noch nie gespeichert").
    "ALTER TABLE cases ADD COLUMN revision INTEGER NOT NULL DEFAULT 1",
)

_V3: tuple[str, ...] = (
    # Kundenmappen (Stapelverarbeitung mehrerer Verträge einer Kundin/eines Kunden)
    """
    CREATE TABLE bundles (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "ALTER TABLE cases ADD COLUMN bundle_id TEXT",
    "CREATE INDEX idx_cases_bundle ON cases(bundle_id)",
)

# Versionsnummer -> SQL-Anweisungen. Neue Versionen nur anhängen, nie ändern.
MIGRATIONS: dict[int, tuple[str, ...]] = {1: _V1, 2: _V2, 3: _V3}
LATEST_SCHEMA_VERSION = max(MIGRATIONS)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """Thread-sichere SQLite-Verbindung mit Migrationen.

    Alle Zugriffe laufen über eine gemeinsame Verbindung und eine Sperre
    (``check_same_thread=False``). Transaktionen über :meth:`transaction`.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._closed = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not self.path.exists()
            if new_file:
                self._precreate_private()
            self._conn = sqlite3.connect(
                str(self.path),
                timeout=10.0,
                isolation_level=None,  # Autocommit; Transaktionen explizit
                check_same_thread=False,
            )
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(
                "Die lokale Datenbank konnte nicht geöffnet werden. Bitte Speicherort und Rechte prüfen."
            ) from exc
        self._conn.row_factory = sqlite3.Row
        try:
            self._configure()
            self._restrict_permissions()
            self._migrate()
        except sqlite3.DatabaseError as exc:
            self._conn.close()
            self._closed = True
            raise StorageError(
                "Die lokale Datenbank ist beschädigt oder keine gültige Datenbankdatei."
            ) from exc
        except StorageError:
            self._conn.close()
            self._closed = True
            raise

    # ------------------------------------------------------------------
    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 10000")
            # Gelöschte Inhalte überschreiben (Datenschutz beim Löschen von Vorgängen)
            self._conn.execute("PRAGMA secure_delete = ON")
            mode = self._conn.execute("PRAGMA journal_mode = WAL").fetchone()
            if mode is None or str(mode[0]).lower() != "wal":
                log.warning("SQLite-WAL-Modus nicht verfügbar – Standardmodus aktiv")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            # Datenbank als SQLite-Datei prüfen (löst DatabaseError bei Fremddateien aus)
            self._conn.execute("SELECT count(*) FROM sqlite_master").fetchone()

    def _precreate_private(self) -> None:
        """Neue Datenbankdatei vorab mit ``0600`` anlegen.

        SQLite übernimmt beim Anlegen von ``-wal``/``-shm`` die Rechte der
        Hauptdatei. Wird sie erst nach dem ersten Zugriff eingeschränkt, entstehen
        WAL und SHM der ersten Sitzung mit den Standardrechten (z. B. ``0644``).
        """
        if os.name != "posix":
            return
        with contextlib.suppress(FileExistsError):
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))

    def _restrict_permissions(self) -> None:
        """Datenbank, WAL und SHM nur für den eigenen Benutzer (repariert auch ältere Installationen)."""
        if os.name != "posix":
            return
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            with contextlib.suppress(OSError):
                if p.exists() and (p.stat().st_mode & 0o077):
                    os.chmod(p, 0o600)

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            current = self.schema_version()
            if current > LATEST_SCHEMA_VERSION:
                raise StorageError(
                    "Die Datenbank stammt aus einer neueren Programmversion. Bitte die App aktualisieren."
                )
            for version in sorted(v for v in MIGRATIONS if v > current):
                with self.transaction() as conn:
                    for statement in MIGRATIONS[version]:
                        conn.execute(statement)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, _now_iso()),
                    )
                log.info("Datenbank-Migration angewendet: Version %d", version)

    # ------------------------------------------------------------------
    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise StorageError("Die Datenbank ist bereits geschlossen.")

    def schema_version(self) -> int:
        with self._lock:
            self._check_open()
            row = self._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Schreibtransaktion (``BEGIN IMMEDIATE``). Verschachtelte Aufrufe laufen in der äußeren."""
        with self._lock:
            self._check_open()
            if self._conn.in_transaction:
                yield self._conn
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            self._check_open()
            return self._conn.execute(sql, tuple(params))

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            self._check_open()
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            self._check_open()
            return self._conn.execute(sql, tuple(params)).fetchone()

    def size_bytes(self) -> int:
        """Größe der Datenbank inkl. WAL-/SHM-Datei in Bytes."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            try:
                total += p.stat().st_size
            except OSError:
                continue
        return total

    def checkpoint(self) -> None:
        """WAL-Inhalt in die Hauptdatei übernehmen (z. B. vor einer Sicherung)."""
        with self._lock:
            self._check_open()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            with contextlib.suppress(sqlite3.Error):
                self._conn.execute("PRAGMA optimize")
            self._conn.close()
            self._closed = True

    def __enter__(self) -> Database:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
