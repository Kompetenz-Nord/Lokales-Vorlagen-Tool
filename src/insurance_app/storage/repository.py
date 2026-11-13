"""Vorgangs-Repository: Vorgänge, Dokumentkopien, Verlauf, Exportprotokoll.

Grundsätze
----------
* Alles bleibt lokal (SQLite + Vorgangsordner ``<data>/cases/<id>/documents``).
* Hochgeladene Dateien werden nur **kopiert** – das Original wird nie verändert
  oder verschoben. Die Kopie heißt ``<Dokument-ID><Endung>`` (keine Namen im
  Dateinamen) und ist schreibgeschützt.
* Der Verlauf wird nur ergänzt, nie überschrieben.
* Optimistische Sperre: Jeder Speichervorgang erhöht ``Case.revision``. Ein
  veralteter Stand (anderswo inzwischen gespeichert) wird abgewiesen
  (:class:`StaleCaseError`); ein gelöschter Vorgang wird nie wieder angelegt
  (:class:`CaseNotFoundError`).
* Logs enthalten nur IDs und Zähler – keine Inhalte, Namen oder Dateinamen.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from insurance_app.core.case import Bundle, Case, CaseSummary, PipelineStage, SourceDocument, new_document_id
from insurance_app.core.models import FieldStatus, HistoryEntry
from insurance_app.core.paths import AppPaths
from insurance_app.core.textutil import fold
from insurance_app.security.tempfiles import force_remove, force_rmtree

from .db import Database, StorageError

__all__ = [
    "SEARCH_FIELD_KEYS",
    "CaseNotFoundError",
    "CaseRepository",
    "DocumentImportError",
    "DocumentNotFoundError",
    "StaleCaseError",
    "count_open_issues",
    "file_sha256",
]

log = logging.getLogger(__name__)

# Felder, die (lokal) durchsuchbar sind: Vertragsnummern und Namen
SEARCH_FIELD_KEYS: tuple[str, ...] = (
    "contract.number",
    "contract.policy_number",
    "contract.application_number",
    "policyholder.name",
    "policyholder.first_name",
    "insured.last_name",
    "insured.first_name",
    "insurer.name",
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SAFE_EXT = re.compile(r"^\.[A-Za-z0-9]{1,10}$")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_CHUNK = 1024 * 1024


class CaseNotFoundError(StorageError, LookupError):
    """Vorgang existiert nicht (mehr)."""

    def __init__(self, case_id: str) -> None:
        super().__init__(f"Der Vorgang {case_id} wurde nicht gefunden.")
        self.case_id = case_id


class StaleCaseError(StorageError):
    """Der Vorgang wurde inzwischen an anderer Stelle gespeichert (veralteter Stand)."""

    def __init__(self, case_id: str) -> None:
        super().__init__("Der Vorgang wurde inzwischen an anderer Stelle geändert. Bitte neu laden.")
        self.case_id = case_id


class DocumentNotFoundError(StorageError, LookupError):
    """Dokument gehört nicht (mehr) zum Vorgang."""

    def __init__(self, document_id: str) -> None:
        super().__init__(f"Das Dokument {document_id} gehört nicht zu diesem Vorgang.")
        self.document_id = document_id


class DocumentImportError(StorageError):
    """Datei konnte nicht übernommen werden."""


def file_sha256(path: Path) -> str:
    """SHA-256 einer Datei (blockweise gelesen)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def count_open_issues(case: Case) -> int:
    """Offene Punkte: Konflikte, unklare Felder und erkannte, noch nicht bestätigte Werte."""
    open_states = (FieldStatus.CONFLICT, FieldStatus.UNCLEAR, FieldStatus.EXTRACTED)
    return sum(1 for fv in case.all_fields().values() if fv.status in open_states)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _compact(text: str) -> str:
    return _NON_ALNUM.sub("", fold(text))


def _search_text(case: Case) -> str:
    parts: list[str] = [case.id, case.title, case.template_id or ""]
    for key in SEARCH_FIELD_KEYS:
        fv = case.get(key)
        if fv is None:
            continue
        for v in (fv.value, fv.normalized_value):
            if v:
                parts.append(v)
    words = " ".join(fold(p) for p in parts if p)
    compact = " ".join(_compact(p) for p in parts if p and _compact(p))
    return f"{words} | {compact}"


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _check_id(case_id: str) -> str:
    if not isinstance(case_id, str) or not _SAFE_ID.match(case_id):
        raise ValueError("Ungültige Vorgangs-ID.")
    return case_id


def _dir_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


class CaseRepository:
    """Speichern, Laden und Verwalten von Vorgängen (lokal)."""

    def __init__(self, db: Database, paths: AppPaths) -> None:
        self.db = db
        self.paths = paths

    # ------------------------------------------------------------------
    # Pfade
    # ------------------------------------------------------------------
    def case_dir(self, case_id: str) -> Path:
        return self.paths.cases_dir / _check_id(case_id)

    def documents_dir(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "documents"

    def document_path(self, case: Case, document_id: str) -> Path:
        """Pfad der Arbeitskopie; findet die Datei auch nach Verschieben des Datenordners."""
        doc = case.document(document_id)
        if doc is None:
            raise DocumentNotFoundError(document_id)
        stored = Path(doc.stored_path)
        if stored.exists():
            return stored
        fallback = self.documents_dir(case.id) / stored.name
        return fallback if fallback.exists() else stored

    def _ensure_case_dirs(self, case_id: str) -> Path:
        docs = self.documents_dir(case_id)
        docs.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            for p in (docs.parent, docs):
                with contextlib.suppress(OSError):
                    os.chmod(p, 0o700)
        return docs

    # ------------------------------------------------------------------
    # Vorgänge
    # ------------------------------------------------------------------
    def create_case(self, title: str = "") -> Case:
        """Neuen Vorgang anlegen (inkl. Ordner) und speichern."""
        case = Case(title=title.strip())
        while self.exists(case.id):  # pragma: no cover - Kollision praktisch ausgeschlossen
            case = Case(title=title.strip())
        self._ensure_case_dirs(case.id)
        case.add_history(HistoryEntry(actor="system", action="case_created", detail="Vorgang angelegt"))
        self.save_case(case)
        log.info("Vorgang angelegt: %s", case.id)
        return case

    def exists(self, case_id: str) -> bool:
        try:
            _check_id(case_id)
        except ValueError:
            return False
        return self.db.query_one("SELECT 1 FROM cases WHERE id = ?", (case_id,)) is not None

    def save_case(self, case: Case) -> None:
        """Vorgang speichern. Setzt ``updated_at`` und erhöht ``case.revision``.

        * ``revision == 0`` (noch nie gespeichert): neuer Datensatz. Gibt es die ID
          schon, wird nichts überschrieben (:class:`StaleCaseError`).
        * ``revision > 0``: Aktualisierung nur, wenn der gespeicherte Stand noch
          ``case.revision`` ist – sonst :class:`StaleCaseError` (anderswo geändert)
          bzw. :class:`CaseNotFoundError` (inzwischen gelöscht, wird nicht neu angelegt).
        * Dokumenttabelle wird mit ``case.documents`` abgeglichen.
        * Verlauf: Nur neue Einträge (über die bereits gespeicherte Anzahl hinaus) werden angehängt.
          Dank der Standprüfung stammt der Verlauf immer vom zuletzt gespeicherten Stand ab.
        """
        _check_id(case.id)
        expected = int(case.revision)
        new_revision = expected + 1
        case.touch()
        # JSON mit dem neuen Stand speichern; das Objekt erst nach Erfolg anpassen
        data_json = case.model_copy(update={"revision": new_revision}).model_dump_json()
        values = (
            case.title,
            _iso(case.updated_at),
            int(case.stage),
            case.template_id,
            int(case.archived),
            data_json,
            _search_text(case),
            len(case.documents),
            count_open_issues(case),
            new_revision,
            case.bundle_id,
        )
        try:
            with self.db.transaction() as conn:
                if expected <= 0:
                    if conn.execute("SELECT 1 FROM cases WHERE id = ?", (case.id,)).fetchone():
                        raise StaleCaseError(case.id)
                    conn.execute(
                        """
                        INSERT INTO cases(title, updated_at, stage, template_id, archived, data_json,
                                          search_text, document_count, open_issues, revision, bundle_id,
                                          id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (*values, case.id, _iso(case.created_at)),
                    )
                else:
                    cur = conn.execute(
                        """
                        UPDATE cases SET title = ?, updated_at = ?, stage = ?, template_id = ?, archived = ?,
                                         data_json = ?, search_text = ?, document_count = ?,
                                         open_issues = ?, revision = ?, bundle_id = ?
                        WHERE id = ? AND revision = ?
                        """,
                        (*values, case.id, expected),
                    )
                    if cur.rowcount == 0:
                        if conn.execute("SELECT 1 FROM cases WHERE id = ?", (case.id,)).fetchone() is None:
                            raise CaseNotFoundError(case.id)
                        raise StaleCaseError(case.id)
                self._sync_documents(conn, case)
                self._append_history(conn, case)
        except StaleCaseError:
            log.warning("Vorgang %s nicht gespeichert: veralteter Stand", case.id)
            raise
        except CaseNotFoundError:
            log.warning("Vorgang %s nicht gespeichert: inzwischen gelöscht", case.id)
            raise
        except sqlite3.Error as exc:
            log.error("Vorgang %s konnte nicht gespeichert werden (%s)", case.id, type(exc).__name__)
            raise StorageError("Der Vorgang konnte nicht gespeichert werden.") from exc
        case.revision = new_revision

    def current_revision(self, case_id: str) -> int | None:
        """Gespeicherter Stand eines Vorgangs (``None``, wenn es ihn nicht gibt)."""
        try:
            _check_id(case_id)
        except ValueError:
            return None
        row = self.db.query_one("SELECT revision FROM cases WHERE id = ?", (case_id,))
        return int(row["revision"]) if row is not None else None

    def _sync_documents(self, conn: sqlite3.Connection, case: Case) -> None:
        ids = [d.id for d in case.documents]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"DELETE FROM documents WHERE case_id = ? AND id NOT IN ({placeholders})",  # noqa: S608
                (case.id, *ids),
            )
        else:
            conn.execute("DELETE FROM documents WHERE case_id = ?", (case.id,))
        conn.executemany(
            """
            INSERT INTO documents(id, case_id, sha256, original_name, stored_path, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id, id) DO UPDATE SET
                sha256 = excluded.sha256,
                original_name = excluded.original_name,
                stored_path = excluded.stored_path,
                added_at = excluded.added_at
            """,
            [
                (d.id, case.id, d.sha256, d.original_name, d.stored_path, _iso(d.added_at))
                for d in case.documents
            ],
        )

    def _append_history(self, conn: sqlite3.Connection, case: Case) -> None:
        stored = int(conn.execute("SELECT COUNT(*) FROM history WHERE case_id = ?", (case.id,)).fetchone()[0])
        new_entries = case.history[stored:]
        if not new_entries:
            return
        conn.executemany(
            """
            INSERT INTO history(case_id, timestamp, actor, action, field_key, old_status, new_status,
                                old_value, new_value, source, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    case.id,
                    _iso(e.timestamp),
                    e.actor,
                    e.action,
                    e.field_key,
                    e.old_status.value if e.old_status else None,
                    e.new_status.value if e.new_status else None,
                    e.old_value,
                    e.new_value,
                    e.source,
                    e.detail,
                )
                for e in new_entries
            ],
        )

    def load_case(self, case_id: str) -> Case:
        """Vorgang laden. Löst :class:`CaseNotFoundError` aus, wenn er nicht existiert."""
        try:
            _check_id(case_id)
        except ValueError:
            raise CaseNotFoundError(str(case_id)[:64]) from None
        row = self.db.query_one("SELECT data_json, revision FROM cases WHERE id = ?", (case_id,))
        if row is None:
            raise CaseNotFoundError(case_id)
        try:
            case = Case.model_validate_json(row["data_json"])
            case.revision = int(row["revision"])  # die Spalte ist maßgeblich
            return case
        except ValidationError as exc:
            log.error("Vorgang %s ist nicht lesbar (%d Validierungsfehler)", case_id, exc.error_count())
            raise StorageError(
                f"Der Vorgang {case_id} ist beschädigt oder stammt aus einer neueren Programmversion."
            ) from exc

    def list_cases(
        self,
        search: str | None = None,
        include_archived: bool = False,
        limit: int = 200,
        bundle_id: str | None = None,
    ) -> list[CaseSummary]:
        """Vorgänge für das Dashboard (neueste zuerst).

        Die Suche läuft nur lokal über ID, Titel, Vorlage, Vertragsnummern und Namen.
        Mehrere Suchwörter müssen alle vorkommen.
        """
        where: list[str] = []
        params: list[Any] = []
        if not include_archived:
            where.append("archived = 0")
        if bundle_id is not None:
            where.append("bundle_id = ?")
            params.append(bundle_id)
        for term in (search or "").split():
            folded = fold(term)
            compact = _compact(term)
            clause = "(search_text LIKE ? ESCAPE '\\'"
            params.append(f"%{_escape_like(folded)}%")
            if compact and compact != folded:
                clause += " OR search_text LIKE ? ESCAPE '\\'"
                params.append(f"%{_escape_like(compact)}%")
            where.append(clause + ")")
        sql = (
            "SELECT id, title, created_at, updated_at, stage, template_id, archived, document_count, "
            "open_issues, bundle_id FROM cases"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        rows = self.db.query_all(sql, params)
        out: list[CaseSummary] = []
        for r in rows:
            try:
                stage = PipelineStage(int(r["stage"]))
            except ValueError:
                stage = PipelineStage.ABLAGE
            out.append(
                CaseSummary(
                    id=r["id"],
                    title=r["title"],
                    created_at=_parse_dt(r["created_at"]),
                    updated_at=_parse_dt(r["updated_at"]),
                    stage=stage,
                    template_id=r["template_id"],
                    document_count=int(r["document_count"]),
                    archived=bool(r["archived"]),
                    open_issues=int(r["open_issues"]),
                    bundle_id=r["bundle_id"],
                )
            )
        return out

    # ------------------------------------------------------------------
    # Kundenmappen
    # ------------------------------------------------------------------
    def create_bundle(self, title: str) -> Bundle:
        bundle = Bundle(title=(title or "").strip() or "Kundenmappe")
        self.db.execute(
            "INSERT INTO bundles(id, title, created_at) VALUES (?, ?, ?)",
            (bundle.id, bundle.title, _iso(bundle.created_at)),
        )
        return bundle

    def list_bundles(self) -> list[Bundle]:
        rows = self.db.query_all(
            "SELECT b.id, b.title, b.created_at, COUNT(c.id) AS n FROM bundles b "
            "LEFT JOIN cases c ON c.bundle_id = b.id GROUP BY b.id ORDER BY b.created_at DESC"
        )
        return [
            Bundle(
                id=r["id"], title=r["title"], created_at=_parse_dt(r["created_at"]), case_count=int(r["n"])
            )
            for r in rows
        ]

    def get_bundle(self, bundle_id: str) -> Bundle:
        r = self.db.query_one(
            "SELECT b.id, b.title, b.created_at, (SELECT COUNT(*) FROM cases c WHERE c.bundle_id = b.id) AS n "
            "FROM bundles b WHERE b.id = ?",
            (bundle_id,),
        )
        if r is None:
            raise StorageError("Kundenmappe nicht gefunden.")
        return Bundle(
            id=r["id"], title=r["title"], created_at=_parse_dt(r["created_at"]), case_count=int(r["n"])
        )

    def rename_bundle(self, bundle_id: str, title: str) -> None:
        self.db.execute(
            "UPDATE bundles SET title = ? WHERE id = ?", ((title or "").strip() or "Kundenmappe", bundle_id)
        )

    def delete_bundle(self, bundle_id: str, delete_cases: bool = False) -> None:
        """Mappe löschen. Vorgänge werden nur mit ``delete_cases=True`` mitgelöscht, sonst aus der Mappe gelöst."""
        case_ids = [
            r["id"] for r in self.db.query_all("SELECT id FROM cases WHERE bundle_id = ?", (bundle_id,))
        ]
        for cid in case_ids:
            if delete_cases:
                self.delete_case(cid)
            else:
                case = self.load_case(cid)
                case.bundle_id = None
                self.save_case(case)
        self.db.execute("DELETE FROM bundles WHERE id = ?", (bundle_id,))

    def list_history(self, case_id: str) -> list[HistoryEntry]:
        """Gespeicherter Verlauf eines Vorgangs (älteste zuerst)."""
        _check_id(case_id)
        rows = self.db.query_all(
            "SELECT timestamp, actor, action, field_key, old_status, new_status, old_value, new_value, "
            "source, detail FROM history WHERE case_id = ? ORDER BY id",
            (case_id,),
        )
        return [
            HistoryEntry(
                timestamp=_parse_dt(r["timestamp"]),
                actor=r["actor"],
                action=r["action"],
                field_key=r["field_key"],
                old_status=FieldStatus(r["old_status"]) if r["old_status"] else None,
                new_status=FieldStatus(r["new_status"]) if r["new_status"] else None,
                old_value=r["old_value"],
                new_value=r["new_value"],
                source=r["source"],
                detail=r["detail"],
            )
            for r in rows
        ]

    def archive_case(self, case_id: str, archived: bool = True) -> None:
        """Vorgang archivieren bzw. wiederherstellen.

        Auch ein Vorgang, dessen gespeicherte Daten nicht mehr lesbar sind
        (beschädigt oder aus einer neueren Programmversion), lässt sich
        archivieren – dann nur über die Listenspalte, ohne die Daten zu laden.
        """
        try:
            case = self.load_case(case_id)
        except CaseNotFoundError:
            raise
        except StorageError:
            self._archive_unreadable(case_id, archived)
            return
        if case.archived == archived:
            return
        case.archived = archived
        case.add_history(
            HistoryEntry(
                actor="user",
                action="archived" if archived else "unarchived",
                detail="Vorgang archiviert" if archived else "Vorgang wiederhergestellt",
            )
        )
        self.save_case(case)
        log.info("Vorgang %s %s", case_id, "archiviert" if archived else "wiederhergestellt")

    def _archive_unreadable(self, case_id: str, archived: bool) -> None:
        """Archiv-Kennzeichen eines nicht lesbaren Vorgangs setzen (ohne Modellprüfung)."""
        try:
            with self.db.transaction() as conn:
                cur = conn.execute(
                    "UPDATE cases SET archived = ?, updated_at = ?, revision = revision + 1 "
                    "WHERE id = ? AND archived != ?",
                    (int(archived), _iso(datetime.now(UTC)), case_id, int(archived)),
                )
                if cur.rowcount == 0:
                    return  # bereits im gewünschten Zustand
                conn.execute(
                    "INSERT INTO history(case_id, timestamp, actor, action, detail) VALUES (?, ?, ?, ?, ?)",
                    (
                        case_id,
                        _iso(datetime.now(UTC)),
                        "user",
                        "archived" if archived else "unarchived",
                        ("Vorgang archiviert" if archived else "Vorgang wiederhergestellt")
                        + " (gespeicherte Daten nicht lesbar)",
                    ),
                )
        except sqlite3.Error as exc:
            log.error("Vorgang %s: Archivieren fehlgeschlagen (%s)", case_id, type(exc).__name__)
            raise StorageError("Der Vorgang konnte nicht archiviert werden.") from exc
        log.info("Nicht lesbarer Vorgang %s %s", case_id, "archiviert" if archived else "wiederhergestellt")

    def document_ids(self, case_id: str) -> list[str]:
        """Dokument-IDs eines Vorgangs aus der Dokumenttabelle (ohne die Vorgangsdaten zu laden)."""
        _check_id(case_id)
        return [r["id"] for r in self.db.query_all("SELECT id FROM documents WHERE case_id = ?", (case_id,))]

    def delete_case(self, case_id: str) -> None:
        """Vorgang endgültig löschen: Datenbankeinträge und Vorgangsordner (inkl. Dokumentkopien).

        Die gespeicherten Daten werden dafür nicht geladen – auch beschädigte
        Vorgänge lassen sich so vollständig löschen.
        """
        _check_id(case_id)
        folder = self.case_dir(case_id)
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
            existed = cur.rowcount > 0
            conn.execute("DELETE FROM export_log WHERE case_id = ?", (case_id,))
        if existed:
            # WAL-Inhalt sofort zurückschreiben, damit gelöschte Daten nicht im WAL verbleiben
            with contextlib.suppress(sqlite3.Error, StorageError):
                self.db.checkpoint()
        folder_existed = folder.exists()
        if folder_existed:
            try:
                force_rmtree(folder)
            except OSError as exc:
                log.error("Vorgangsordner %s nicht vollständig gelöscht (%s)", case_id, type(exc).__name__)
                raise StorageError(
                    "Der Vorgang wurde gelöscht, aber einige Dateien im Vorgangsordner konnten nicht "
                    "entfernt werden. Bitte die App schließen und erneut versuchen."
                ) from exc
        if not existed and not folder_existed:
            raise CaseNotFoundError(case_id)
        log.info("Vorgang gelöscht: %s", case_id)

    # ------------------------------------------------------------------
    # Dokumente
    # ------------------------------------------------------------------
    def add_document(self, case: Case, src_path: Path) -> SourceDocument:
        """Datei als schreibgeschützte Kopie in den Vorgangsordner übernehmen und speichern.

        Das Original wird nur gelesen. Dubletten (gleiche SHA-256 im Vorgang) werden
        übernommen, aber als ``excluded`` mit ``duplicate_of`` markiert.
        """
        src = Path(src_path)
        if not src.exists():
            raise DocumentImportError("Die Datei wurde nicht gefunden.")
        if not src.is_file():
            raise DocumentImportError("Bitte eine Datei auswählen (kein Ordner).")
        docs_dir = self._ensure_case_dirs(case.id)
        existing_ids = {d.id for d in case.documents}
        doc_id = new_document_id()
        while doc_id in existing_ids:  # pragma: no cover - Kollision praktisch ausgeschlossen
            doc_id = new_document_id()
        ext = src.suffix.lower() if _SAFE_EXT.match(src.suffix) else ""
        dest = docs_dir / f"{doc_id}{ext}"
        try:
            src_size = src.stat().st_size
            shutil.copy2(src, dest)
            if dest.stat().st_size != src_size:
                raise OSError("Größe der Kopie weicht ab")
            sha = file_sha256(dest)
            os.chmod(dest, stat.S_IREAD)
        except OSError as exc:
            self._discard_copy(dest)
            log.error("Dokument konnte nicht übernommen werden (%s)", type(exc).__name__)
            raise DocumentImportError(
                "Die Datei konnte nicht in den Vorgang kopiert werden. Bitte Lesbarkeit und freien "
                "Speicherplatz prüfen."
            ) from exc

        original = next((d for d in case.documents if d.sha256 == sha and d.duplicate_of is None), None)
        if original is None:
            original = next((d for d in case.documents if d.sha256 == sha), None)
        doc = SourceDocument(
            id=doc_id,
            original_name=src.name,
            stored_path=str(dest.resolve()),
            sha256=sha,
            excluded=original is not None,
            duplicate_of=(original.duplicate_of or original.id) if original is not None else None,
        )
        case.documents.append(doc)
        case.add_history(
            HistoryEntry(
                actor="user",
                action="document_added",
                source=src.name,
                detail=(
                    f"Dokument {doc_id} übernommen (Dublette von {doc.duplicate_of})"
                    if doc.duplicate_of
                    else f"Dokument {doc_id} übernommen"
                ),
            )
        )
        try:
            self.save_case(case)
        except StorageError:
            case.documents.remove(doc)
            case.history.pop()
            self._discard_copy(dest)
            raise
        log.info(
            "Dokument %s zu Vorgang %s übernommen (Dublette: %s)",
            doc_id,
            case.id,
            "ja" if doc.excluded else "nein",
        )
        return doc

    @staticmethod
    def _discard_copy(dest: Path) -> None:
        try:
            force_remove(dest)
        except OSError:
            log.warning("Unvollständige Kopie konnte nicht entfernt werden")

    def remove_document(self, case: Case, document_id: str) -> None:
        """Dokument aus dem Vorgang entfernen und seine Arbeitskopie löschen.

        War das Dokument Vorlage für Dubletten, wird die erste Dublette zum neuen Original.
        Feldwerte, die sich auf das Dokument beziehen, bleiben unverändert (Neuauswertung
        ist Aufgabe der Orchestrierung).
        """
        doc = case.document(document_id)
        if doc is None:
            raise DocumentNotFoundError(document_id)
        path = self.document_path(case, document_id)
        case.documents.remove(doc)
        dups = [d for d in case.documents if d.duplicate_of == document_id]
        if dups:
            new_original = dups[0]
            new_original.duplicate_of = None
            new_original.excluded = False
            for d in dups[1:]:
                d.duplicate_of = new_original.id
        case.add_history(
            HistoryEntry(actor="user", action="document_removed", detail=f"Dokument {document_id} entfernt")
        )
        self.save_case(case)
        docs_dir = self.documents_dir(case.id).resolve()
        try:
            if path.resolve().parent == docs_dir:  # nie Dateien außerhalb des Vorgangsordners löschen
                force_remove(path)
        except OSError as exc:
            log.warning("Arbeitskopie %s nicht gelöscht (%s)", document_id, type(exc).__name__)
        log.info("Dokument %s aus Vorgang %s entfernt", document_id, case.id)

    # ------------------------------------------------------------------
    # Exportprotokoll
    # ------------------------------------------------------------------
    def log_export(
        self,
        case_id: str,
        file_name: str,
        template_id: str,
        template_version: str,
        flattened: bool,
        qa_passed: bool,
        sha256: str | None,
    ) -> int:
        """Export protokollieren (nur Dateiname ohne Pfad). Gibt die Protokoll-ID zurück."""
        _check_id(case_id)
        name = Path(str(file_name).replace("\\", "/")).name
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO export_log(case_id, timestamp, template_id, template_version, file_name,
                                       flattened, qa_passed, sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    _iso(datetime.now(UTC)),
                    template_id,
                    template_version,
                    name,
                    int(bool(flattened)),
                    int(bool(qa_passed)),
                    sha256,
                ),
            )
            export_id = int(cur.lastrowid or 0)
        log.info("Export protokolliert: Vorgang %s, Protokoll-Nr. %d", case_id, export_id)
        return export_id

    def list_exports(self, case_id: str | None = None) -> list[dict[str, Any]]:
        """Exportprotokoll (neueste zuerst). ``timestamp`` als ``datetime`` (UTC)."""
        sql = (
            "SELECT id, case_id, timestamp, template_id, template_version, file_name, flattened, "
            "qa_passed, sha256 FROM export_log"
        )
        params: tuple[Any, ...] = ()
        if case_id is not None:
            _check_id(case_id)
            sql += " WHERE case_id = ?"
            params = (case_id,)
        sql += " ORDER BY id DESC"
        return [
            {
                "id": int(r["id"]),
                "case_id": r["case_id"],
                "timestamp": _parse_dt(r["timestamp"]),
                "template_id": r["template_id"],
                "template_version": r["template_version"],
                "file_name": r["file_name"],
                "flattened": bool(r["flattened"]),
                "qa_passed": bool(r["qa_passed"]),
                "sha256": r["sha256"],
            }
            for r in self.db.query_all(sql, params)
        ]

    # ------------------------------------------------------------------
    # Vorlagenversionen
    # ------------------------------------------------------------------
    def record_template_version(
        self, catalog_id: str, version: str, pdf_path: Path | str, sha256: str, confirmed: bool = False
    ) -> None:
        """Vorlagenversion eintragen oder aktualisieren (z. B. nach Import im Vorlagen-Studio)."""
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO template_versions(catalog_id, version, pdf_path, sha256, imported_at, confirmed)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(catalog_id, version) DO UPDATE SET
                    pdf_path = excluded.pdf_path,
                    sha256 = excluded.sha256,
                    confirmed = excluded.confirmed
                """,
                (catalog_id, version, str(pdf_path), sha256, _iso(datetime.now(UTC)), int(bool(confirmed))),
            )

    def set_template_confirmed(self, catalog_id: str, version: str, confirmed: bool = True) -> None:
        """Zuordnung einer Vorlagenversion als bestätigt markieren (oder zurücknehmen)."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                "UPDATE template_versions SET confirmed = ? WHERE catalog_id = ? AND version = ?",
                (int(bool(confirmed)), catalog_id, version),
            )
            if cur.rowcount == 0:
                raise StorageError("Diese Vorlagenversion ist nicht eingetragen.")

    def list_template_versions(self, catalog_id: str | None = None) -> list[dict[str, Any]]:
        """Eingetragene Vorlagenversionen (neueste Einträge zuerst)."""
        sql = "SELECT catalog_id, version, pdf_path, sha256, imported_at, confirmed FROM template_versions"
        params: tuple[Any, ...] = ()
        if catalog_id is not None:
            sql += " WHERE catalog_id = ?"
            params = (catalog_id,)
        sql += " ORDER BY imported_at DESC, version DESC"
        return [
            {
                "catalog_id": r["catalog_id"],
                "version": r["version"],
                "pdf_path": r["pdf_path"],
                "sha256": r["sha256"],
                "imported_at": _parse_dt(r["imported_at"]),
                "confirmed": bool(r["confirmed"]),
            }
            for r in self.db.query_all(sql, params)
        ]

    # ------------------------------------------------------------------
    # Übersicht "Wo liegen meine Daten?"
    # ------------------------------------------------------------------
    def storage_overview(self, export_dir: Path | None = None) -> dict[str, Any]:
        """Speicherorte und Größen für die Einstellungsseite."""
        counts = self.db.query_one(
            "SELECT COUNT(*) AS total, COALESCE(SUM(archived), 0) AS archived, "
            "COALESCE(SUM(document_count), 0) AS docs FROM cases"
        )
        exports = Path(export_dir) if export_dir is not None else self.paths.exports
        export_row = self.db.query_one("SELECT COUNT(*) AS n FROM export_log")
        export_count = int(export_row["n"]) if export_row else 0
        return {
            "data_dir": str(self.paths.data),
            "export_dir": str(exports),
            "database_path": str(self.db.path),
            "database_size_bytes": self.db.size_bytes(),
            "cases_dir": str(self.paths.cases_dir),
            "logs_dir": str(self.paths.logs_dir),
            "temp_dir": str(self.paths.temp_dir),
            "case_count": int(counts["total"]) if counts else 0,
            "archived_case_count": int(counts["archived"]) if counts else 0,
            "document_count": int(counts["docs"]) if counts else 0,
            "documents_size_bytes": _dir_size(self.paths.cases_dir),
            "export_count": export_count,
            "description": (
                "Alle Daten bleiben auf diesem Rechner. Vorgänge, Dokumentkopien und Verlauf liegen im "
                "Datenordner, fertige PDFs im Exportordner."
            ),
        }
