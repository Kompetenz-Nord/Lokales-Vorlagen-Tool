"""SQLite-Datenbank: Migrationen, WAL, Fremdschlüssel, Transaktionen, Fehlerfälle."""

from __future__ import annotations

import os
import sqlite3
import stat
import threading

import pytest

from insurance_app.storage.db import LATEST_SCHEMA_VERSION, Database, StorageError

TABLES = {"cases", "documents", "history", "export_log", "template_versions", "schema_migrations"}


def test_schema_v1_created(tmp_path):
    with Database(tmp_path / "db" / "test.sqlite3") as db:
        names = {r["name"] for r in db.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert names >= TABLES
        # Version 2 (SVC-04/05): Spalte cases.revision für die optimistische Sperre
        assert (
            db.schema_version() == LATEST_SCHEMA_VERSION == 3
        )  # v3: Kundenmappen (bundles, cases.bundle_id)
        cols = {r["name"] for r in db.query_all("PRAGMA table_info(cases)")}
        assert {
            "id",
            "title",
            "created_at",
            "updated_at",
            "stage",
            "template_id",
            "archived",
            "data_json",
            "search_text",
            "revision",
        } <= cols
        hist = {r["name"] for r in db.query_all("PRAGMA table_info(history)")}
        assert {
            "id",
            "case_id",
            "timestamp",
            "actor",
            "action",
            "field_key",
            "old_status",
            "new_status",
            "old_value",
            "new_value",
            "source",
            "detail",
        } == hist
        exp = {r["name"] for r in db.query_all("PRAGMA table_info(export_log)")}
        assert {
            "id",
            "case_id",
            "timestamp",
            "template_id",
            "template_version",
            "file_name",
            "flattened",
            "qa_passed",
            "sha256",
        } == exp
        tv = {r["name"] for r in db.query_all("PRAGMA table_info(template_versions)")}
        assert {"catalog_id", "version", "pdf_path", "sha256", "imported_at", "confirmed"} == tv


def test_wal_and_foreign_keys(tmp_path):
    with Database(tmp_path / "t.sqlite3") as db:
        assert db.query_one("PRAGMA journal_mode")[0].lower() == "wal"
        assert db.query_one("PRAGMA foreign_keys")[0] == 1
        with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
            conn.execute(
                "INSERT INTO documents(id, case_id, sha256, original_name, stored_path, added_at) "
                "VALUES ('D-1', 'gibt-es-nicht', 'x', 'x', 'x', 'x')"
            )


def test_cascade_delete(tmp_path):
    with Database(tmp_path / "t.sqlite3") as db:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO cases(id, created_at, updated_at, data_json) VALUES ('V1', 'a', 'a', '{}')"
            )
            conn.execute(
                "INSERT INTO documents(id, case_id, sha256, original_name, stored_path, added_at) "
                "VALUES ('D-1', 'V1', 'x', 'x', 'x', 'x')"
            )
            conn.execute(
                "INSERT INTO history(case_id, timestamp, actor, action) VALUES ('V1', 't', 'system', 'x')"
            )
        db.execute("DELETE FROM cases WHERE id = 'V1'")
        assert db.query_one("SELECT COUNT(*) FROM documents")[0] == 0
        assert db.query_one("SELECT COUNT(*) FROM history")[0] == 0


def test_transaction_rollback(tmp_path):
    with Database(tmp_path / "t.sqlite3") as db:
        with pytest.raises(RuntimeError), db.transaction() as conn:
            conn.execute(
                "INSERT INTO cases(id, created_at, updated_at, data_json) VALUES ('V1', 'a', 'a', '{}')"
            )
            raise RuntimeError("Abbruch")
        assert db.query_one("SELECT COUNT(*) FROM cases")[0] == 0
        with db.transaction() as outer, db.transaction() as inner:  # verschachtelt -> eine Transaktion
            assert outer is inner
            inner.execute(
                "INSERT INTO cases(id, created_at, updated_at, data_json) VALUES ('V2', 'a', 'a', '{}')"
            )
        assert db.query_one("SELECT COUNT(*) FROM cases")[0] == 1


def test_reopen_keeps_data_and_version(tmp_path):
    path = tmp_path / "t.sqlite3"
    with Database(path) as db:
        db.execute("INSERT INTO cases(id, created_at, updated_at, data_json) VALUES ('V1', 'a', 'a', '{}')")
    with Database(path) as db:
        assert db.schema_version() == LATEST_SCHEMA_VERSION
        assert db.query_one("SELECT COUNT(*) FROM cases")[0] == 1
        assert db.query_one("SELECT COUNT(*) FROM schema_migrations")[0] == LATEST_SCHEMA_VERSION


def test_newer_schema_is_rejected(tmp_path):
    path = tmp_path / "t.sqlite3"
    with Database(path) as db:
        db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (99, 'x')")
    with pytest.raises(StorageError) as info:
        Database(path)
    assert "neueren Programmversion" in str(info.value)


def test_corrupt_file_raises_storage_error(tmp_path):
    path = tmp_path / "kaputt.sqlite3"
    path.write_bytes(b"Das ist keine Datenbank. " * 200)
    with pytest.raises(StorageError) as info:
        Database(path)
    assert "beschädigt" in str(info.value)


def test_closed_database(tmp_path):
    db = Database(tmp_path / "t.sqlite3")
    db.close()
    db.close()  # idempotent
    assert db.closed
    with pytest.raises(StorageError):
        db.query_all("SELECT 1")


def test_concurrent_writes_are_serialized(tmp_path):
    with Database(tmp_path / "t.sqlite3") as db:
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                for i in range(25):
                    with db.transaction() as conn:
                        conn.execute(
                            "INSERT INTO cases(id, created_at, updated_at, data_json) VALUES (?, 'a', 'a', '{}')",
                            (f"V{n}-{i}",),
                        )
            except BaseException as exc:  # pragma: no cover - nur bei Fehlern
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert db.query_one("SELECT COUNT(*) FROM cases")[0] == 100
        assert db.size_bytes() > 0
        db.checkpoint()


# ---------------------------------------------------------------------------
# Regressionen
# ---------------------------------------------------------------------------


def test_svc_04_migration_from_v1_sets_revision_one(tmp_path):
    from insurance_app.storage.db import MIGRATIONS

    path = tmp_path / "alt.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for statement in MIGRATIONS[1]:
        conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'x')")
    conn.execute("INSERT INTO cases(id, created_at, updated_at, data_json) VALUES ('V1', 'a', 'a', '{}')")
    conn.commit()
    conn.close()
    with Database(path) as db:
        assert db.schema_version() == LATEST_SCHEMA_VERSION
        assert db.query_one("SELECT revision FROM cases WHERE id = 'V1'")[0] == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX-Dateirechte")
def test_priv_01_wal_and_shm_are_private_from_first_session(tmp_path):
    old = os.umask(0o022)
    try:
        path = tmp_path / "daten" / "vorgaenge.sqlite3"
        db = Database(path)
        try:
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO cases(id, created_at, updated_at, data_json) VALUES ('V1', 'a', 'a', '{}')"
                )
            for suffix in ("", "-wal", "-shm"):
                p = path.with_name(path.name + suffix)
                assert p.exists(), suffix
                assert stat.S_IMODE(p.stat().st_mode) == 0o600, suffix
        finally:
            db.close()
    finally:
        os.umask(old)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-Dateirechte")
def test_priv_01_existing_world_readable_files_are_repaired(tmp_path):
    path = tmp_path / "t.sqlite3"
    with Database(path) as db:
        db.execute("INSERT INTO cases(id, created_at, updated_at, data_json) VALUES ('V1', 'a', 'a', '{}')")
    os.chmod(path, 0o644)
    db = Database(path)
    try:
        for suffix in ("", "-wal", "-shm"):
            p = path.with_name(path.name + suffix)
            if p.exists():
                assert stat.S_IMODE(p.stat().st_mode) == 0o600, suffix
    finally:
        db.close()
