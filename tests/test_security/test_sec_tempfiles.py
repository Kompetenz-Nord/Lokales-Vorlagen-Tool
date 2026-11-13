"""Temporäre Arbeitsordner werden immer entfernt."""

from __future__ import annotations

import os
import stat
import time

import pytest

from insurance_app.security.tempfiles import TempWorkspace, cleanup_stale, force_rmtree


def _make_read_only_tree(root):
    sub = root / "sub"
    sub.mkdir()
    f = sub / "seite1.png"
    f.write_bytes(b"MUSTER")
    os.chmod(f, stat.S_IREAD)
    os.chmod(sub, stat.S_IREAD | stat.S_IEXEC)
    return f


def test_workspace_created_private_and_removed(tmp_path):
    with TempWorkspace(tmp_path / "tmp", "J-1A2B3C4D") as work:
        assert work.is_dir()
        assert work.parent == tmp_path / "tmp"
        assert work.name.startswith("J-1A2B3C4D-")
        if os.name == "posix":
            assert stat.S_IMODE(work.stat().st_mode) == 0o700
        (work / "datei.txt").write_text("MUSTER")
        _make_read_only_tree(work)
    assert not work.exists()
    assert list((tmp_path / "tmp").iterdir()) == []


def test_workspace_removed_on_exception(tmp_path):
    holder = {}
    with pytest.raises(RuntimeError), TempWorkspace(tmp_path, "job") as work:
        holder["path"] = work
        _make_read_only_tree(work)
        raise RuntimeError("Abbruch")
    assert not holder["path"].exists()


def test_workspaces_are_unique_and_job_id_sanitized(tmp_path):
    with TempWorkspace(tmp_path, "../böse id") as a, TempWorkspace(tmp_path, "../böse id") as b:
        assert a != b
        assert a.parent == tmp_path and b.parent == tmp_path
        assert "/" not in a.name and not a.name.startswith(".")
    with TempWorkspace(tmp_path, "...") as c:
        assert c.name.startswith("job-")


def test_path_property_outside_context(tmp_path):
    ws = TempWorkspace(tmp_path, "job")
    with pytest.raises(RuntimeError):
        _ = ws.path
    with ws as p:
        assert ws.path == p
    assert ws.cleanup() is True


def test_cleanup_stale(tmp_path):
    old_dir = tmp_path / "alt"
    old_dir.mkdir()
    _make_read_only_tree(old_dir)
    old_file = tmp_path / "alt.tmp"
    old_file.write_text("x")
    new_dir = tmp_path / "neu"
    new_dir.mkdir()
    past = time.time() - 48 * 3600
    os.utime(old_dir, (past, past))
    os.utime(old_file, (past, past))
    assert cleanup_stale(tmp_path, max_age_hours=24) == 2
    assert not old_dir.exists() and not old_file.exists()
    assert new_dir.exists()
    assert cleanup_stale(tmp_path, max_age_hours=0) == 1
    assert cleanup_stale(tmp_path / "gibt-es-nicht") == 0


def test_cleanup_stale_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "aussen"
    outside.mkdir()
    keep = outside / "wichtig.txt"
    keep.write_text("bleibt")
    base = tmp_path / "tmp"
    base.mkdir()
    link = base / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks nicht verfügbar")
    assert cleanup_stale(base, max_age_hours=0) == 1
    assert keep.exists()


def test_force_rmtree_missing_path_is_ok(tmp_path):
    force_rmtree(tmp_path / "fehlt")
    f = tmp_path / "datei"
    f.write_text("x")
    os.chmod(f, stat.S_IREAD)
    force_rmtree(f)
    assert not f.exists()


# ---------------------------------------------------------------------------
# PRIV-09: Einzelinstanz-Sperre und sofortiges Aufräumen eigener Job-Reste
# ---------------------------------------------------------------------------


def _leftovers(base):
    """Typische Reste eines Absturzes (frisch, also jünger als 24 h) – nur MUSTER-Inhalte."""
    base.mkdir(parents=True, exist_ok=True)
    made = []
    for rel in (
        "vorschau/V20261113-AB12CD/vorschau.pdf",
        "export-V20261113-AB12CD/ausgabe.pdf",
        "musterfall/muster.pdf",
        "testbefuellung-0123456789abcdef/seite.png",
        "docling-0123456789ab/seite1.png",
        "J-1A2B3C4D-0123456789ab/seite1.png",
    ):
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"MUSTER - KEINE ECHTDATEN")
        made.append(base / rel.split("/")[0])
    os.chmod(base / "export-V20261113-AB12CD" / "ausgabe.pdf", stat.S_IREAD)
    return made


def test_priv_09_exclusive_cleanup_removes_fresh_job_leftovers(tmp_path):
    from insurance_app.security.tempfiles import LOCK_FILE_NAME, acquire_instance_lock, cleanup_temp_dir

    base = tmp_path / "tmp"
    made = _leftovers(base)
    foreign = base / "fremd.txt"  # unbekannter, frischer Eintrag bleibt (nur Altersregel)
    foreign.write_text("x")
    with acquire_instance_lock(base) as lock:
        assert lock.acquired
        assert cleanup_temp_dir(base, exclusive=lock.acquired) == len(made)
        assert not any(p.exists() for p in made)
        assert foreign.exists() and (base / LOCK_FILE_NAME).exists()
        if os.name == "posix":
            assert stat.S_IMODE((base / LOCK_FILE_NAME).stat().st_mode) == 0o600


def test_priv_09_second_instance_only_removes_old_entries(tmp_path):
    from insurance_app.security.tempfiles import LOCK_FILE_NAME, acquire_instance_lock, cleanup_temp_dir

    base = tmp_path / "tmp"
    first = acquire_instance_lock(base)
    try:
        assert first.acquired
        made = _leftovers(base)  # laufende Jobs der ersten Instanz
        old = base / "export-V20200101-000000"
        old.mkdir()
        past = time.time() - 48 * 3600
        os.utime(old, (past, past))
        lock_file = base / LOCK_FILE_NAME
        os.utime(lock_file, (past, past))  # alte Sperrdatei darf trotzdem nicht gelöscht werden
        second = acquire_instance_lock(base)
        assert not second.acquired
        assert cleanup_temp_dir(base, exclusive=second.acquired) == 1
        assert not old.exists() and all(p.exists() for p in made)
        assert lock_file.exists()
        second.release()
    finally:
        first.release()
        first.release()  # idempotent
    third = acquire_instance_lock(base)
    try:
        assert third.acquired  # nach Freigabe wieder erhältlich
    finally:
        third.release()


def test_priv_09_lock_of_other_process_and_release_on_exit(tmp_path):
    import subprocess
    import sys
    import textwrap

    from insurance_app.security.tempfiles import acquire_instance_lock

    base = tmp_path / "tmp"
    script = textwrap.dedent(
        """
        import sys, time
        from pathlib import Path
        from insurance_app.security.tempfiles import acquire_instance_lock
        lock = acquire_instance_lock(Path(sys.argv[1]))
        print("ok" if lock.acquired else "nein", flush=True)
        sys.stdin.readline()
        """
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(base)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert proc.stdout.readline().strip() == "ok"
        lock = acquire_instance_lock(base)
        assert not lock.acquired  # andere, laufende Instanz hält die Sperre
    finally:
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(timeout=20)
    with acquire_instance_lock(base) as lock:
        assert lock.acquired  # Prozess beendet -> Sperre automatisch frei


def test_priv_09_cleanup_stale_keeps_lock_file(tmp_path):
    from insurance_app.security.tempfiles import LOCK_FILE_NAME

    lock_file = tmp_path / LOCK_FILE_NAME
    lock_file.write_text("")
    past = time.time() - 48 * 3600
    os.utime(lock_file, (past, past))
    assert cleanup_stale(tmp_path, max_age_hours=0) == 0
    assert lock_file.exists()
