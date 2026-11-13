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
