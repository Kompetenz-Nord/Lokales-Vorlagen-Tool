"""Vorgangs-Repository mit synthetischen MUSTER-Daten."""

from __future__ import annotations

import hashlib
import os
import stat

import pytest

from insurance_app.core.case import Case, PipelineStage
from insurance_app.core.models import (
    Candidate,
    Evidence,
    ExtractionMethod,
    FieldStatus,
    FieldValue,
    HistoryEntry,
)
from insurance_app.storage import (
    CaseNotFoundError,
    CaseRepository,
    Database,
    DocumentImportError,
    DocumentNotFoundError,
    StaleCaseError,
    StorageError,
    count_open_issues,
)


@pytest.fixture()
def repo(app_paths):
    db = Database(app_paths.database)
    try:
        yield CaseRepository(db, app_paths)
    finally:
        db.close()


@pytest.fixture()
def muster_file(tmp_path):
    src = tmp_path / "eingang" / "MUSTER Police Erika Musterfrau.pdf"
    src.parent.mkdir()
    src.write_bytes(b"%PDF-1.4\n% MUSTER - KEINE ECHTDATEN\n" + bytes(range(256)) * 20)
    return src


def _field(key: str, value: str, status: FieldStatus) -> FieldValue:
    return FieldValue(
        key=key,
        value=value,
        normalized_value=value,
        status=status,
        candidates=[
            Candidate(
                field_key=key,
                value=value,
                normalized=value,
                evidence=Evidence(
                    document_id="D-0000MUST", file_name="MUSTER.pdf", page=1, method=ExtractionMethod.PDF_TEXT
                ),
            )
        ],
    )


def _fill(case: Case) -> None:
    case.set_field(_field("contract.number", "8815201-7", FieldStatus.CONFIRMED))
    case.set_field(_field("insured.last_name", "Müller-Musterfrau", FieldStatus.EXTRACTED))
    case.set_field(_field("insured.first_name", "Erika", FieldStatus.UNCLEAR))
    case.set_field(_field("contract.start_date", "2019-01-01", FieldStatus.CONFLICT))
    case.set_field(
        FieldValue(key="order.severance_consent", normalized_value="true", status=FieldStatus.MANUAL)
    )
    case.template_id = "swisslife_sl13065_2024-09"
    case.stage = PipelineStage.DATENFREIGABE


# ---------------------------------------------------------------------------
# Anlegen, Speichern, Laden
# ---------------------------------------------------------------------------


def test_create_case_creates_folder_and_row(repo, app_paths):
    case = repo.create_case("  MUSTER Vorgang  ")
    assert case.title == "MUSTER Vorgang"
    assert (app_paths.cases_dir / case.id / "documents").is_dir()
    assert repo.exists(case.id)
    loaded = repo.load_case(case.id)
    assert loaded.id == case.id
    assert [h.action for h in loaded.history] == ["case_created"]


def test_roundtrip_with_fields_history_and_exports(repo):
    case = repo.create_case("MUSTER")
    _fill(case)
    case.add_history(
        HistoryEntry(
            actor="user",
            action="confirmed",
            field_key="contract.number",
            old_status=FieldStatus.EXTRACTED,
            new_status=FieldStatus.CONFIRMED,
            old_value="8815201-7",
            new_value="8815201-7",
            source="MUSTER.pdf, S. 1, PDF_TEXT",
        )
    )
    repo.save_case(case)
    loaded = repo.load_case(case.id)
    assert loaded.contract_fields == case.contract_fields
    assert loaded.order_fields == case.order_fields
    assert loaded.stage == PipelineStage.DATENFREIGABE
    assert loaded.template_id == "swisslife_sl13065_2024-09"
    assert len(loaded.history) == 2
    history = repo.list_history(case.id)
    assert [h.action for h in history] == ["case_created", "confirmed"]
    assert history[1].old_status == FieldStatus.EXTRACTED and history[1].new_status == FieldStatus.CONFIRMED
    assert history[1].source == "MUSTER.pdf, S. 1, PDF_TEXT"


def test_save_appends_only_new_history(repo):
    case = repo.create_case()
    repo.save_case(case)
    repo.save_case(case)
    assert len(repo.list_history(case.id)) == 1
    case.add_history(HistoryEntry(action="edited", field_key="contract.number"))
    repo.save_case(case)
    repo.save_case(case)
    assert [h.action for h in repo.list_history(case.id)] == ["case_created", "edited"]


def test_save_case_updates_timestamp_and_summary(repo):
    case = repo.create_case("MUSTER")
    before = case.updated_at
    _fill(case)
    repo.save_case(case)
    assert case.updated_at >= before
    summary = repo.list_cases()[0]
    assert summary.open_issues == count_open_issues(case) == 3
    assert summary.stage == PipelineStage.DATENFREIGABE
    assert summary.template_id == "swisslife_sl13065_2024-09"


def test_load_missing_case(repo):
    with pytest.raises(CaseNotFoundError) as info:
        repo.load_case("V20990101-ABCDEF")
    assert isinstance(info.value, StorageError) and isinstance(info.value, LookupError)
    assert "nicht gefunden" in str(info.value)
    with pytest.raises(CaseNotFoundError):
        repo.load_case("../../etc/passwd")


def test_corrupt_case_json(repo):
    case = repo.create_case()
    repo.db.execute("UPDATE cases SET data_json = '{\"id\": 5}' WHERE id = ?", (case.id,))
    with pytest.raises(StorageError) as info:
        repo.load_case(case.id)
    assert "beschädigt" in str(info.value)


def test_unsafe_case_ids_rejected(repo):
    bad = Case(id="../ausbruch")
    with pytest.raises(ValueError):
        repo.save_case(bad)
    with pytest.raises(ValueError):
        repo.delete_case("../ausbruch")
    assert not repo.exists("../ausbruch")


# ---------------------------------------------------------------------------
# Liste, Suche, Archiv, Löschen
# ---------------------------------------------------------------------------


def test_list_newest_first_and_limit(repo):
    ids = [repo.create_case(f"MUSTER {i}").id for i in range(5)]
    listed = [s.id for s in repo.list_cases()]
    assert listed == list(reversed(ids))
    assert len(repo.list_cases(limit=2)) == 2


def test_search_by_title_number_and_names(repo):
    a = repo.create_case("Freigabe MUSTER Bäckerei")
    _fill(a)
    repo.save_case(a)
    b = repo.create_case("Anderer MUSTER-Fall")
    assert [s.id for s in repo.list_cases("8815201-7")] == [a.id]
    assert [s.id for s in repo.list_cases("88152017")] == [a.id]
    assert [s.id for s in repo.list_cases("mueller")] == [a.id]
    assert [s.id for s in repo.list_cases("MÜLLER erika")] == [a.id]
    assert [s.id for s in repo.list_cases("Bäckerei")] == [a.id]
    assert [s.id for s in repo.list_cases("sl13065")] == [a.id]
    assert [s.id for s in repo.list_cases(b.id)] == [b.id]
    assert {s.id for s in repo.list_cases("muster")} == {a.id, b.id}
    assert repo.list_cases("gibtsnicht") == []
    assert repo.list_cases("%") == []
    # "_" ist kein Platzhalter: trifft nur die Vorlagen-ID mit echtem Unterstrich
    assert [s.id for s in repo.list_cases("_")] == [a.id]


def test_archive_and_restore(repo):
    case = repo.create_case("MUSTER")
    repo.archive_case(case.id)
    assert repo.list_cases() == []
    archived = repo.list_cases(include_archived=True)
    assert archived[0].archived is True
    assert repo.load_case(case.id).archived is True
    repo.archive_case(case.id, archived=False)
    assert [s.id for s in repo.list_cases()] == [case.id]
    actions = [h.action for h in repo.list_history(case.id)]
    assert actions == ["case_created", "archived", "unarchived"]
    with pytest.raises(CaseNotFoundError):
        repo.archive_case("V20990101-ABCDEF")


def test_delete_case_removes_rows_and_folder(repo, app_paths, muster_file):
    case = repo.create_case("MUSTER")
    doc = repo.add_document(case, muster_file)
    repo.log_export(case.id, "2026-11-13_SWISSLIFE_SL13065_Freigabe_X.pdf", "t", "2024-09", False, True, "ab")
    folder = app_paths.cases_dir / case.id
    assert not os.access(doc.stored_path, os.W_OK) or os.geteuid() == 0
    repo.delete_case(case.id)
    assert not folder.exists()
    assert not repo.exists(case.id)
    for table in ("documents", "history", "export_log"):
        assert repo.db.query_one(f"SELECT COUNT(*) FROM {table} WHERE case_id = ?", (case.id,))[0] == 0  # noqa: S608
    assert muster_file.exists()  # Original unberührt
    with pytest.raises(CaseNotFoundError):
        repo.delete_case(case.id)


# ---------------------------------------------------------------------------
# Dokumente
# ---------------------------------------------------------------------------


def test_add_document_copies_read_only_and_keeps_source(repo, app_paths, muster_file):
    sha_before = hashlib.sha256(muster_file.read_bytes()).hexdigest()
    st_before = muster_file.stat()
    case = repo.create_case()
    doc = repo.add_document(case, muster_file)
    st_after = muster_file.stat()
    assert hashlib.sha256(muster_file.read_bytes()).hexdigest() == sha_before
    assert st_after.st_mtime_ns == st_before.st_mtime_ns
    assert st_after.st_mode == st_before.st_mode
    assert muster_file.exists()

    stored = repo.document_path(case, doc.id)
    assert stored.parent == app_paths.cases_dir / case.id / "documents"
    assert stored.name == f"{doc.id}.pdf"
    assert "Musterfrau" not in str(stored) and "Erika" not in str(stored)
    assert doc.original_name == muster_file.name
    assert doc.sha256 == sha_before
    assert stored.read_bytes() == muster_file.read_bytes()
    assert not (stat.S_IMODE(stored.stat().st_mode) & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    assert doc.excluded is False and doc.duplicate_of is None

    loaded = repo.load_case(case.id)
    assert [d.id for d in loaded.documents] == [doc.id]
    row = repo.db.query_one("SELECT sha256, stored_path FROM documents WHERE id = ?", (doc.id,))
    assert row["sha256"] == sha_before and row["stored_path"] == str(stored)
    assert repo.list_cases()[0].document_count == 1


def test_duplicate_detection(repo, muster_file, tmp_path):
    case = repo.create_case()
    first = repo.add_document(case, muster_file)
    copy = tmp_path / "kopie.PDF"
    copy.write_bytes(muster_file.read_bytes())
    dup = repo.add_document(case, copy)
    dup2 = repo.add_document(case, muster_file)
    assert dup.excluded and dup.duplicate_of == first.id
    assert dup2.excluded and dup2.duplicate_of == first.id
    assert repo.document_path(case, dup.id).suffix == ".pdf"
    other = tmp_path / "anders.png"
    other.write_bytes(b"\x89PNG MUSTER")
    distinct = repo.add_document(case, other)
    assert not distinct.excluded
    assert [d.id for d in case.active_documents()] == [first.id, distinct.id]
    # Dublette in einem anderen Vorgang ist keine Dublette
    case2 = repo.create_case()
    assert repo.add_document(case2, muster_file).excluded is False


def test_add_document_errors(repo, tmp_path):
    case = repo.create_case()
    with pytest.raises(DocumentImportError):
        repo.add_document(case, tmp_path / "fehlt.pdf")
    with pytest.raises(DocumentImportError):
        repo.add_document(case, tmp_path)
    assert repo.load_case(case.id).documents == []


def test_unsafe_extension_is_dropped(repo, tmp_path):
    src = tmp_path / "MUSTER.p d f"
    src.write_bytes(b"x")
    case = repo.create_case()
    doc = repo.add_document(case, src)
    assert repo.document_path(case, doc.id).name == doc.id


def test_remove_document_promotes_duplicate(repo, muster_file):
    case = repo.create_case()
    first = repo.add_document(case, muster_file)
    dup_a = repo.add_document(case, muster_file)
    dup_b = repo.add_document(case, muster_file)
    first_path = repo.document_path(case, first.id)
    repo.remove_document(case, first.id)
    assert not first_path.exists()
    loaded = repo.load_case(case.id)
    a = loaded.document(dup_a.id)
    b = loaded.document(dup_b.id)
    assert a.excluded is False and a.duplicate_of is None
    assert b.excluded is True and b.duplicate_of == dup_a.id
    assert repo.db.query_one("SELECT COUNT(*) FROM documents WHERE case_id = ?", (case.id,))[0] == 2
    assert muster_file.exists()
    with pytest.raises(DocumentNotFoundError):
        repo.remove_document(case, first.id)


def test_document_path_after_data_folder_move(repo, muster_file):
    case = repo.create_case()
    doc = repo.add_document(case, muster_file)
    doc.stored_path = "/verschoben/alt/" + os.path.basename(doc.stored_path)
    assert repo.document_path(case, doc.id).exists()
    with pytest.raises(DocumentNotFoundError):
        repo.document_path(case, "D-FFFFFFFF")


# ---------------------------------------------------------------------------
# Neustart, Exportprotokoll, Vorlagenversionen, Übersicht
# ---------------------------------------------------------------------------


def test_resume_after_restart(app_paths, muster_file):
    db = Database(app_paths.database)
    repo = CaseRepository(db, app_paths)
    case = repo.create_case("MUSTER Wiederaufnahme")
    _fill(case)
    repo.add_document(case, muster_file)
    repo.log_export(case.id, "datei.pdf", "tpl", "2024-09", False, True, "abc")
    db.close()

    db2 = Database(app_paths.database)
    try:
        repo2 = CaseRepository(db2, app_paths)
        loaded = repo2.load_case(case.id)
        assert loaded.title == "MUSTER Wiederaufnahme"
        assert loaded.contract_fields == case.contract_fields
        assert len(loaded.documents) == 1
        assert repo2.document_path(loaded, loaded.documents[0].id).exists()
        assert [s.id for s in repo2.list_cases("Wiederaufnahme")] == [case.id]
        assert len(repo2.list_exports(case.id)) == 1
        loaded.add_history(HistoryEntry(action="edited"))
        repo2.save_case(loaded)
        assert [h.action for h in repo2.list_history(case.id)][-1] == "edited"
        assert len(repo2.list_history(case.id)) == len(loaded.history)
    finally:
        db2.close()


def test_export_log(repo):
    case = repo.create_case()
    first = repo.log_export(
        case.id,
        "/geheim/pfad/2026-11-13_SWISSLIFE_SL13065_Freigabe.pdf",
        "swisslife_sl13065_2024-09",
        "2024-09",
        False,
        True,
        "a" * 64,
    )
    second = repo.log_export(case.id, "C:\\Users\\x\\b.pdf", "t2", "v2", True, False, None)
    other = repo.create_case()
    repo.log_export(other.id, "c.pdf", "t3", "v3", False, True, None)
    exports = repo.list_exports(case.id)
    assert [e["id"] for e in exports] == [second, first]
    assert exports[1]["file_name"] == "2026-11-13_SWISSLIFE_SL13065_Freigabe.pdf"
    assert exports[0]["file_name"] == "b.pdf"
    assert exports[1]["flattened"] is False and exports[1]["qa_passed"] is True
    assert exports[0]["flattened"] is True and exports[0]["qa_passed"] is False
    assert exports[1]["timestamp"].tzinfo is not None
    assert len(repo.list_exports()) == 3


def test_template_versions(repo, tmp_path):
    repo.record_template_version("swisslife_sl13065_freigabe", "2024-09", tmp_path / "o.pdf", "ab" * 32)
    repo.record_template_version("swisslife_sl13065_freigabe", "2025-01", tmp_path / "n.pdf", "cd" * 32, True)
    versions = repo.list_template_versions("swisslife_sl13065_freigabe")
    assert {v["version"]: v["confirmed"] for v in versions} == {"2024-09": False, "2025-01": True}
    repo.set_template_confirmed("swisslife_sl13065_freigabe", "2024-09")
    assert all(v["confirmed"] for v in repo.list_template_versions())
    repo.record_template_version("swisslife_sl13065_freigabe", "2024-09", tmp_path / "o2.pdf", "ef" * 32)
    updated = [v for v in repo.list_template_versions() if v["version"] == "2024-09"][0]
    assert updated["sha256"] == "ef" * 32 and updated["confirmed"] is False
    with pytest.raises(StorageError):
        repo.set_template_confirmed("gibt", "es-nicht")


def test_storage_overview(repo, app_paths, muster_file, tmp_path):
    case = repo.create_case()
    repo.add_document(case, muster_file)
    repo.archive_case(repo.create_case().id)
    overview = repo.storage_overview()
    assert overview["data_dir"] == str(app_paths.data)
    assert overview["export_dir"] == str(app_paths.exports)
    assert overview["database_path"] == str(app_paths.database)
    assert overview["database_size_bytes"] > 0
    assert overview["case_count"] == 2
    assert overview["archived_case_count"] == 1
    assert overview["document_count"] == 1
    assert overview["documents_size_bytes"] == muster_file.stat().st_size
    assert overview["export_count"] == 0
    assert "Rechner" in overview["description"]
    custom = repo.storage_overview(export_dir=tmp_path / "eigene-exporte")
    assert custom["export_dir"] == str(tmp_path / "eigene-exporte")


def test_deleted_case_leaves_no_plain_text_in_database_files(app_paths):
    marker = "MUSTERGEHEIMXYZ"
    db = Database(app_paths.database)
    repo = CaseRepository(db, app_paths)
    case = repo.create_case(f"Titel {marker}")
    case.set_field(_field("insured.last_name", marker, FieldStatus.EXTRACTED))
    repo.save_case(case)
    keep = repo.create_case("MUSTER bleibt")
    repo.delete_case(case.id)
    db.close()
    raw = b""
    for suffix in ("", "-wal", "-shm"):
        path = app_paths.database.with_name(app_paths.database.name + suffix)
        if path.exists():
            raw += path.read_bytes()
    assert marker.encode() not in raw
    assert marker.lower().encode() not in raw
    db2 = Database(app_paths.database)
    try:
        assert [s.id for s in CaseRepository(db2, app_paths).list_cases()] == [keep.id]
    finally:
        db2.close()


# ---------------------------------------------------------------------------
# Regressionen: optimistische Sperre, gelöschte und beschädigte Vorgänge
# ---------------------------------------------------------------------------


def test_svc_04_revision_increments_and_create_saves_revision_one(repo):
    case = repo.create_case("MUSTER")
    assert case.revision == 1 and repo.current_revision(case.id) == 1
    assert repo.load_case(case.id).revision == 1
    repo.save_case(case)
    assert case.revision == 2 and repo.current_revision(case.id) == 2
    assert repo.load_case(case.id).revision == 2
    assert repo.current_revision("V20990101-ABCDEF") is None
    assert repo.current_revision("../x") is None


def test_svc_04_saving_stale_copy_of_deleted_case_does_not_resurrect_it(repo, app_paths, muster_file):
    case = repo.create_case("MUSTER")
    _fill(case)
    repo.add_document(case, muster_file)
    work = repo.load_case(case.id)  # z. B. Kopie eines Hintergrundjobs
    repo.delete_case(case.id)
    work.add_history(HistoryEntry(action="edited", field_key="contract.number"))
    with pytest.raises(CaseNotFoundError):
        repo.save_case(work)
    assert not repo.exists(case.id)
    assert repo.list_cases(include_archived=True) == []
    for table in ("documents", "history"):
        assert repo.db.query_one(f"SELECT COUNT(*) FROM {table} WHERE case_id = ?", (case.id,))[0] == 0  # noqa: S608
    assert not (app_paths.cases_dir / case.id).exists()


def test_svc_05_stale_copy_cannot_overwrite_newer_state(repo):
    case = repo.create_case("MUSTER")
    view = repo.load_case(case.id)  # Vorgangsansicht
    repo.archive_case(case.id)  # Dashboard archiviert (frische Kopie)
    view.add_history(HistoryEntry(action="confirmed", field_key="contract.number"))
    before = view.revision
    with pytest.raises(StaleCaseError) as info:
        repo.save_case(view)
    assert str(info.value) == "Der Vorgang wurde inzwischen an anderer Stelle geändert. Bitte neu laden."
    assert isinstance(info.value, StorageError)
    assert view.revision == before  # unverändert, wenn nicht gespeichert
    loaded = repo.load_case(case.id)
    assert loaded.archived is True
    assert [h.action for h in repo.list_history(case.id)] == ["case_created", "archived"]
    # nach dem Neuladen funktioniert das Speichern wieder, Verlauf bleibt vollständig
    loaded.add_history(HistoryEntry(action="confirmed", field_key="contract.number"))
    repo.save_case(loaded)
    assert [h.action for h in repo.list_history(case.id)] == ["case_created", "archived", "confirmed"]
    assert len(repo.load_case(case.id).history) == 3


def test_svc_05_unsaved_new_case_with_existing_id_is_rejected(repo):
    case = repo.create_case("MUSTER")
    clone = Case(id=case.id, title="Überschreiben?")
    with pytest.raises(StaleCaseError):
        repo.save_case(clone)
    assert repo.load_case(case.id).title == "MUSTER"


def _corrupt(repo, case_id: str) -> None:
    row = repo.db.query_one("SELECT data_json FROM cases WHERE id = ?", (case_id,))
    data = row["data_json"][:-1] + ', "neues_feld": 1}'  # wie eine neuere Programmversion
    repo.db.execute("UPDATE cases SET data_json = ? WHERE id = ?", (data, case_id))


def test_svc_09_unreadable_case_can_be_archived_and_restored(repo):
    case = repo.create_case("MUSTER")
    _corrupt(repo, case.id)
    with pytest.raises(StorageError):
        repo.load_case(case.id)
    repo.archive_case(case.id)
    assert repo.list_cases() == []
    assert repo.list_cases(include_archived=True)[0].archived is True
    repo.archive_case(case.id)  # bereits archiviert: keine weitere Verlaufszeile
    repo.archive_case(case.id, archived=False)
    assert [s.id for s in repo.list_cases()] == [case.id]
    assert [h.action for h in repo.list_history(case.id)] == ["case_created", "archived", "unarchived"]
    with pytest.raises(CaseNotFoundError):
        repo.archive_case("V20990101-ABCDEF")


def test_priv_02_unreadable_case_can_be_deleted_with_documents(repo, app_paths, muster_file):
    case = repo.create_case("MUSTER")
    doc = repo.add_document(case, muster_file)
    _corrupt(repo, case.id)
    assert repo.document_ids(case.id) == [doc.id]
    repo.delete_case(case.id)
    assert not repo.exists(case.id) and not (app_paths.cases_dir / case.id).exists()
    assert repo.document_ids(case.id) == []
