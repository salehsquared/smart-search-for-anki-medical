"""Disposable external SQLite/FTS5 index.

This module never opens, attaches, or writes an Anki collection database.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

from .compat import dataclass
from .medical_vocab import load_alias_resource
from .models import IndexStats, IndexedNote
from .text import (
    join_searchable_text,
    normalize_text,
    strip_html_and_cloze,
    tokenize,
    truncate_text,
)


SCHEMA_VERSION = 2
_PAYLOAD_VERSION = 1
_UNSAFE_COLLECTION_NAMES = frozenset(
    {"collection.anki2", "collection.anki21", "collection.media"}
)


class FTS5Unavailable(RuntimeError):
    pass


class UnsafeIndexPath(ValueError):
    pass


class IncompatibleIndex(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    note_id: int
    card_ids: tuple[int, ...]
    title: str
    plain_text: str
    normalized_text: str
    fields: Mapping[str, str]
    tags: tuple[str, ...]
    decks: tuple[str, ...]
    note_type: str


@dataclass(frozen=True, slots=True)
class FTSDocumentHit:
    document: IndexedDocument
    rank: int
    bm25: float


@dataclass(frozen=True, slots=True)
class NoteChangePlan:
    """Cheaply classified note changes, prepared outside the search lock."""

    changed: tuple[tuple[IndexedNote, bytes], ...]
    unchanged: int
    existing_note_ids: frozenset[int]


class SmartSearchIndex:
    """Own an add-on-local FTS database with atomic rebuilds."""

    def __init__(self, path: str | Path) -> None:
        self._memory = str(path) == ":memory:"
        self.path = Path(path) if not self._memory else Path(":memory:")
        _validate_index_path(self.path, self._memory)
        if not self._memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        try:
            self._connect()
        except IncompatibleIndex:
            aliases, alias_metadata = self._recoverable_alias_state()
            self.close()
            if self._memory:
                raise
            archived = self._archive_incompatible_index()
            self._connect()
            with self.transaction():
                self._restore_alias_rows(aliases)
                for key, value in alias_metadata.items():
                    self._set_metadata(key, value)
                self._set_metadata("superseded_index_path", str(archived))

    def _connect(self) -> None:
        target = ":memory:" if self._memory else str(self.path)
        connection = sqlite3.connect(
            target,
            timeout=8.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=8000")
        connection.execute("PRAGMA temp_store=MEMORY")
        if not self._memory:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        self._connection = connection
        try:
            self._initialize_schema()
        except IncompatibleIndex:
            # The constructor reads recoverable alias state before archiving
            # this disposable index, then closes the connection.
            raise
        except Exception:
            connection.close()
            self._connection = None
            raise

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("The search index is closed.")
        return self._connection

    def _initialize_schema(self) -> None:
        existing_version = self._existing_schema_version()
        if existing_version is not None and existing_version != SCHEMA_VERSION:
            raise IncompatibleIndex(
                f"Index schema {existing_version} is incompatible with "
                f"{SCHEMA_VERSION}; the derived index will be rebuilt."
            )
        try:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes (
                    note_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    content_hash BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vocabulary (
                    term TEXT PRIMARY KEY,
                    frequency INTEGER NOT NULL CHECK (frequency > 0)
                );
                CREATE TABLE IF NOT EXISTS field_names (
                    normalized_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    note_count INTEGER NOT NULL CHECK (note_count > 0),
                    PRIMARY KEY (normalized_name)
                );
                CREATE TABLE IF NOT EXISTS aliases (
                    alias TEXT NOT NULL,
                    canonical TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    source TEXT NOT NULL DEFAULT 'user',
                    PRIMARY KEY (alias, canonical, source)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                    title,
                    body,
                    tags,
                    decks,
                    note_type,
                    tokenize='unicode61 remove_diacritics 2',
                    content=''
                );
                """
            )
        except sqlite3.OperationalError as error:
            if "fts5" in str(error).casefold():
                raise FTS5Unavailable(
                    "This Python/Anki build does not provide SQLite FTS5."
                ) from error
            raise
        with self.transaction():
            self._set_metadata("schema_version", str(SCHEMA_VERSION))
            if self._metadata("generation") is None:
                self._set_metadata("generation", "0")

    def _existing_schema_version(self) -> int | None:
        metadata_exists = self.connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='metadata'
            """
        ).fetchone()
        if not metadata_exists:
            return None
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row else None

    def _archive_incompatible_index(self) -> Path:
        suffix = f".schema-backup-{int(time.time())}"
        archived = Path(str(self.path) + suffix)
        counter = 1
        while archived.exists():
            archived = Path(str(self.path) + suffix + f"-{counter}")
            counter += 1
        os.replace(self.path, archived)
        for sidecar in ("-wal", "-shm"):
            source = Path(str(self.path) + sidecar)
            if source.exists():
                os.replace(source, Path(str(archived) + sidecar))
        return archived

    def _recoverable_alias_state(
        self,
    ) -> tuple[list[tuple[str, str, float, str]], dict[str, str]]:
        if self._connection is None:
            return [], {}
        table = self.connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='aliases'
            """
        ).fetchone()
        aliases: list[tuple[str, str, float, str]] = []
        if table:
            aliases = self._alias_rows()
        metadata = {
            str(row["key"]): str(row["value"])
            for row in self.connection.execute(
                """
                SELECT key, value FROM metadata
                WHERE key LIKE 'alias_resource_sha256:%'
                """
            )
        }
        return aliases, metadata

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
            else:
                self.connection.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            if self._connection is None:
                return
            if not self._memory:
                try:
                    self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "SmartSearchIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _metadata(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return str(row["value"]) if row else None

    def _set_metadata(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    @property
    def generation(self) -> int:
        with self._lock:
            return int(self._metadata("generation") or 0)

    def _increment_generation(self) -> None:
        self._set_metadata("generation", str(int(self._metadata("generation") or 0) + 1))

    def rebuild(
        self,
        notes: Iterable[IndexedNote],
        *,
        progress: Callable[[int], None] | None = None,
    ) -> int:
        """Build a complete replacement and atomically swap it into place."""

        if self._memory:
            with self.transaction():
                aliases = self._alias_rows()
                self._clear_note_data()
                count = self._bulk_insert(notes, progress=progress)
                self._restore_alias_rows(aliases)
                self._increment_generation()
            return count

        aliases = self._alias_rows()
        superseded_path = self._metadata("superseded_index_path")
        next_generation = self.generation + 1
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.rebuild-",
            suffix=".sqlite3",
            dir=self.path.parent,
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        temporary: SmartSearchIndex | None = None
        try:
            temporary = SmartSearchIndex(temporary_path)
            with temporary.transaction():
                temporary._clear_note_data()
                count = temporary._bulk_insert(notes, progress=progress)
                temporary._restore_alias_rows(aliases)
                temporary._set_metadata("generation", str(next_generation))
            temporary.close()
            temporary = None
            self.close()
            _remove_sqlite_sidecars(self.path)
            os.replace(temporary_path, self.path)
            self._connect()
            if superseded_path:
                self._remove_superseded_index(Path(superseded_path))
            return count
        except Exception:
            if temporary is not None:
                temporary.close()
            temporary_path.unlink(missing_ok=True)
            _remove_sqlite_sidecars(temporary_path)
            if self._connection is None:
                self._connect()
            raise

    def _remove_superseded_index(self, path: Path) -> None:
        """Remove only the recoverable old index after replacement succeeds."""

        expected_prefix = self.path.name + ".schema-backup-"
        if (
            path.parent.resolve() != self.path.parent.resolve()
            or not path.name.startswith(expected_prefix)
        ):
            return
        path.unlink(missing_ok=True)
        _remove_sqlite_sidecars(path)

    def _clear_note_data(self) -> None:
        self.connection.execute("INSERT INTO notes_fts(notes_fts) VALUES('delete-all')")
        self.connection.execute("DELETE FROM field_names")
        self.connection.execute("DELETE FROM vocabulary")
        self.connection.execute("DELETE FROM notes")

    def _bulk_insert(
        self,
        notes: Iterable[IndexedNote],
        *,
        progress: Callable[[int], None] | None = None,
    ) -> int:
        vocabulary: Counter[str] = Counter()
        field_counts: Counter[str] = Counter()
        field_display_names: dict[str, str] = {}
        count = 0
        for count, note in enumerate(notes, start=1):
            prepared = _prepare_note(note)
            term_counts = self._insert_note(
                note,
                prepared=prepared,
                update_field_names=False,
            )
            vocabulary.update(term_counts)
            for normalized_name, display_name in prepared["field_names"].items():
                field_counts[normalized_name] += 1
                field_display_names.setdefault(normalized_name, display_name)
            if progress and (count == 1 or count % 250 == 0):
                progress(count)
        self.connection.executemany(
            "INSERT INTO vocabulary(term, frequency) VALUES(?, ?)",
            sorted(vocabulary.items()),
        )
        self.connection.executemany(
            """
            INSERT INTO field_names(normalized_name, display_name, note_count)
            VALUES(?, ?, ?)
            """,
            (
                (normalized_name, field_display_names[normalized_name], frequency)
                for normalized_name, frequency in sorted(field_counts.items())
            ),
        )
        if progress:
            progress(count)
        return count

    def plan_upsert(
        self,
        notes: Iterable[IndexedNote],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> NoteChangePlan:
        """Classify changes without normalizing or locking the index for long.

        Reconciliation commonly receives a full profile snapshot after only
        one note changed.  Hashing the raw immutable snapshot first avoids
        HTML parsing, tokenization, compression, and write-lock contention for
        every unchanged note.
        """

        with self._lock:
            existing = {
                int(row["note_id"]): bytes(row["content_hash"])
                for row in self.connection.execute(
                    "SELECT note_id, content_hash FROM notes"
                )
            }
        changed: list[tuple[IndexedNote, bytes]] = []
        unchanged = 0
        for index, note in enumerate(notes, start=1):
            if cancel_check is not None and (index == 1 or index % 128 == 0):
                cancel_check()
            content_hash = _content_hash(note)
            if existing.get(note.note_id) == content_hash:
                unchanged += 1
            else:
                changed.append((note, content_hash))
        if cancel_check is not None:
            cancel_check()
        return NoteChangePlan(
            changed=tuple(changed),
            unchanged=unchanged,
            existing_note_ids=frozenset(existing),
        )

    def plan_targeted_upsert(
        self,
        notes: Iterable[IndexedNote],
        *,
        candidate_note_ids: Iterable[int] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> NoteChangePlan:
        """Classify a small requested delta without scanning every index row."""

        snapshots = tuple(notes)
        requested_ids = {note.note_id for note in snapshots}
        if candidate_note_ids is not None:
            requested_ids.update(
                int(note_id)
                for note_id in candidate_note_ids
                if int(note_id) > 0
            )
        with self._lock:
            if requested_ids:
                self._prepare_allowed_ids(requested_ids)
                existing = {
                    int(row["note_id"]): bytes(row["content_hash"])
                    for row in self.connection.execute(
                        """
                        SELECT n.note_id, n.content_hash
                        FROM notes n
                        JOIN temp.smart_search_allowed a
                          ON a.note_id=n.note_id
                        """
                    )
                }
            else:
                existing = {}
        changed: list[tuple[IndexedNote, bytes]] = []
        unchanged = 0
        for index, note in enumerate(snapshots, start=1):
            if cancel_check is not None and (index == 1 or index % 128 == 0):
                cancel_check()
            content_hash = _content_hash(note)
            if existing.get(note.note_id) == content_hash:
                unchanged += 1
            else:
                changed.append((note, content_hash))
        if cancel_check is not None:
            cancel_check()
        return NoteChangePlan(
            changed=tuple(changed),
            unchanged=unchanged,
            existing_note_ids=frozenset(existing),
        )

    def apply_upsert(
        self,
        plan: NoteChangePlan,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> int:
        """Apply a staged delta as one short atomic index transaction."""

        changed = 0
        with self.transaction():
            for index, (note, content_hash) in enumerate(plan.changed, start=1):
                if cancel_check is not None and (index == 1 or index % 32 == 0):
                    cancel_check()
                row = self.connection.execute(
                    "SELECT content_hash FROM notes WHERE note_id=?", (note.note_id,)
                ).fetchone()
                if row and bytes(row["content_hash"]) == content_hash:
                    continue
                prepared = _prepare_note(note, content_hash=content_hash)
                self._remove_note(note.note_id, update_vocabulary=True)
                counts = self._insert_note(note, prepared=prepared)
                self._increase_vocabulary(counts)
                changed += 1
            if changed:
                self._increment_generation()
        if cancel_check is not None:
            cancel_check()
        return changed

    def upsert_notes(
        self,
        notes: Iterable[IndexedNote],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> tuple[int, int]:
        """Incrementally add/update snapshots; return ``(changed, unchanged)``."""

        plan = self.plan_upsert(notes, cancel_check=cancel_check)
        changed = self.apply_upsert(plan, cancel_check=cancel_check)
        return changed, plan.unchanged + (len(plan.changed) - changed)

    def delete_notes(self, note_ids: Iterable[int]) -> int:
        deleted = 0
        with self.transaction():
            for note_id in dict.fromkeys(int(value) for value in note_ids):
                exists = self.connection.execute(
                    "SELECT 1 FROM notes WHERE note_id=?", (note_id,)
                ).fetchone()
                if not exists:
                    continue
                self._remove_note(note_id, update_vocabulary=True)
                deleted += 1
            if deleted:
                self._increment_generation()
        return deleted

    def _insert_note(
        self,
        note: IndexedNote,
        *,
        prepared: dict[str, Any] | None = None,
        update_field_names: bool = True,
    ) -> Counter[str]:
        data = prepared or _prepare_note(note)
        self.connection.execute(
            """
            INSERT INTO notes(note_id, title, payload, content_hash)
            VALUES(?, ?, ?, ?)
            """,
            (
                note.note_id,
                data["title"],
                data["payload"],
                data["content_hash"],
            ),
        )
        fts_values = _fts_values(data["title"], data["payload_data"])
        self.connection.execute(
            """
            INSERT INTO notes_fts(rowid, title, body, tags, decks, note_type)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                note.note_id,
                *fts_values,
            ),
        )
        if update_field_names:
            self._increase_field_names(data["field_names"])
        term_counts = Counter(tokenize(data["vocabulary_text"]))
        return term_counts

    def _remove_note(self, note_id: int, *, update_vocabulary: bool) -> None:
        row = self.connection.execute(
            "SELECT title, payload FROM notes WHERE note_id=?", (note_id,)
        ).fetchone()
        if row is None:
            return
        payload_data = _unpack_payload(row["payload"])
        if update_vocabulary:
            self._decrease_vocabulary(_vocabulary_counts(payload_data))
        self._decrease_field_names(payload_data["f"])
        self.connection.execute(
            """
            INSERT INTO notes_fts(
                notes_fts, rowid, title, body, tags, decks, note_type
            ) VALUES('delete', ?, ?, ?, ?, ?, ?)
            """,
            (note_id, *_fts_values(str(row["title"]), payload_data)),
        )
        self.connection.execute("DELETE FROM notes WHERE note_id=?", (note_id,))

    def _increase_vocabulary(self, counts: Mapping[str, int]) -> None:
        self.connection.executemany(
            """
            INSERT INTO vocabulary(term, frequency) VALUES(?, ?)
            ON CONFLICT(term) DO UPDATE SET
                frequency=vocabulary.frequency+excluded.frequency
            """,
            sorted(counts.items()),
        )

    def _decrease_vocabulary(self, counts: Mapping[str, int]) -> None:
        for term, frequency in counts.items():
            self.connection.execute(
                "DELETE FROM vocabulary WHERE term=? AND frequency<=?",
                (term, frequency),
            )
            self.connection.execute(
                """
                UPDATE vocabulary SET frequency=frequency-?
                WHERE term=? AND frequency>?
                """,
                (frequency, term, frequency),
            )

    def _increase_field_names(self, field_names: Mapping[str, str]) -> None:
        self.connection.executemany(
            """
            INSERT INTO field_names(normalized_name, display_name, note_count)
            VALUES(?, ?, 1)
            ON CONFLICT(normalized_name) DO UPDATE SET
                note_count=field_names.note_count+1
            """,
            sorted(field_names.items()),
        )

    def _decrease_field_names(self, fields: Mapping[str, str]) -> None:
        normalized_names = {
            normalize_text(field_name) for field_name in fields if field_name
        }
        for normalized_name in normalized_names:
            self.connection.execute(
                "DELETE FROM field_names WHERE normalized_name=? AND note_count<=1",
                (normalized_name,),
            )
            self.connection.execute(
                """
                UPDATE field_names SET note_count=note_count-1
                WHERE normalized_name=? AND note_count>1
                """,
                (normalized_name,),
            )

    def set_aliases(
        self,
        aliases: Mapping[str, Iterable[str]],
        *,
        source: str = "user",
        replace_source: bool = False,
    ) -> int:
        rows: set[tuple[str, str, float, str]] = set()
        for alias, canonical_values in aliases.items():
            normalized_alias = normalize_text(alias)
            for canonical in canonical_values:
                normalized_canonical = normalize_text(canonical)
                if (
                    normalized_alias
                    and normalized_canonical
                    and normalized_alias != normalized_canonical
                ):
                    rows.add((normalized_alias, normalized_canonical, 1.0, source))
        with self.transaction():
            if replace_source:
                self.connection.execute("DELETE FROM aliases WHERE source=?", (source,))
            self.connection.executemany(
                """
                INSERT INTO aliases(alias, canonical, weight, source)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(alias, canonical, source) DO UPDATE SET
                    weight=excluded.weight
                """,
                sorted(rows),
            )
            if rows or replace_source:
                self._increment_generation()
        return len(rows)

    def load_alias_resource(
        self,
        path: str | Path,
        *,
        source: str = "rxterms",
        replace_source: bool = True,
    ) -> int:
        resource = Path(path)
        digest = hashlib.sha256(resource.read_bytes()).hexdigest()
        metadata_key = f"alias_resource_sha256:{source}"
        with self._lock:
            if self._metadata(metadata_key) == digest:
                return 0
        count = self.set_aliases(
            load_alias_resource(resource),
            source=source,
            replace_source=replace_source,
        )
        with self.transaction():
            self._set_metadata(metadata_key, digest)
        return count

    def vocabulary_entries(self) -> tuple[tuple[str, int], ...]:
        with self._lock:
            return tuple(
                (str(row["term"]), int(row["frequency"]))
                for row in self.connection.execute(
                    "SELECT term, frequency FROM vocabulary ORDER BY term"
                )
            )

    def alias_map(self) -> dict[str, set[str]]:
        with self._lock:
            aliases: dict[str, set[str]] = {}
            for row in self.connection.execute(
                "SELECT alias, canonical FROM aliases ORDER BY alias, canonical"
            ):
                aliases.setdefault(str(row["alias"]), set()).add(
                    str(row["canonical"])
                )
            return aliases

    def _alias_rows(self) -> list[tuple[str, str, float, str]]:
        with self._lock:
            return [
                (
                    str(row["alias"]),
                    str(row["canonical"]),
                    float(row["weight"]),
                    str(row["source"]),
                )
                for row in self.connection.execute(
                    "SELECT alias, canonical, weight, source FROM aliases"
                )
            ]

    def _restore_alias_rows(self, rows: Sequence[tuple[str, str, float, str]]) -> None:
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO aliases(alias, canonical, weight, source)
            VALUES(?, ?, ?, ?)
            """,
            rows,
        )

    def field_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                str(row["display_name"])
                for row in self.connection.execute(
                    """
                    SELECT display_name FROM field_names
                    ORDER BY display_name COLLATE NOCASE
                    """
                )
            )

    def search_fts(
        self,
        expression: str,
        *,
        limit: int = 250,
        allowed_note_ids: set[int] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> tuple[FTSDocumentHit, ...]:
        if not expression.strip() or limit <= 0:
            return ()
        with self._lock:
            if cancel_check is not None:
                cancel_check()
            filter_join = ""
            parameters: list[Any] = [expression]
            if allowed_note_ids is not None:
                if not allowed_note_ids:
                    return ()
                self._prepare_allowed_ids(allowed_note_ids)
                filter_join = (
                    " JOIN temp.smart_search_allowed a ON a.note_id=n.note_id "
                )
            sql = f"""
                SELECT n.*,
                    bm25(notes_fts, 8.0, 3.0, 4.0, 2.5, 2.5) AS score
                FROM notes_fts
                JOIN notes n ON n.note_id=notes_fts.rowid
                {filter_join}
                WHERE notes_fts MATCH ?
                ORDER BY score ASC, n.note_id ASC
                LIMIT ?
            """
            parameters.append(int(limit))
            cancellation_errors: list[Exception] = []

            def progress_handler() -> int:
                if cancel_check is None:
                    return 0
                try:
                    cancel_check()
                except Exception as error:
                    cancellation_errors.append(error)
                    return 1
                return 0

            if cancel_check is not None:
                self.connection.set_progress_handler(progress_handler, 500)
            try:
                rows = self.connection.execute(sql, parameters).fetchall()
            except sqlite3.OperationalError:
                if cancellation_errors:
                    raise cancellation_errors[0]
                raise
            finally:
                if cancel_check is not None:
                    self.connection.set_progress_handler(None, 0)
            if cancel_check is not None:
                cancel_check()
            return tuple(
                FTSDocumentHit(
                    document=_row_to_document(row),
                    rank=rank,
                    bm25=float(row["score"]),
                )
                for rank, row in enumerate(rows, start=1)
            )

    def documents(
        self,
        note_ids: Iterable[int] | None = None,
        *,
        limit: int | None = None,
    ) -> tuple[IndexedDocument, ...]:
        with self._lock:
            params: list[Any] = []
            join = ""
            if note_ids is not None:
                allowed = set(int(note_id) for note_id in note_ids)
                if not allowed:
                    return ()
                self._prepare_allowed_ids(allowed)
                join = " JOIN temp.smart_search_allowed a ON a.note_id=n.note_id "
            limit_sql = ""
            if limit is not None:
                limit_sql = " LIMIT ?"
                params.append(max(0, int(limit)))
            rows = self.connection.execute(
                f"SELECT n.* FROM notes n {join} ORDER BY n.note_id ASC{limit_sql}",
                params,
            ).fetchall()
            return tuple(_row_to_document(row) for row in rows)

    def note_ids(self) -> tuple[int, ...]:
        """Return indexed IDs cheaply so callers can process bounded batches."""

        with self._lock:
            return tuple(
                int(row["note_id"])
                for row in self.connection.execute(
                    "SELECT note_id FROM notes ORDER BY note_id"
                )
            )

    def get_documents(self, note_ids: Iterable[int]) -> dict[int, IndexedDocument]:
        return {document.note_id: document for document in self.documents(note_ids)}

    def count_documents(self, note_ids: Iterable[int] | None = None) -> int:
        with self._lock:
            if note_ids is None:
                return int(self.connection.execute("SELECT count(*) FROM notes").fetchone()[0])
            allowed = set(int(note_id) for note_id in note_ids)
            if not allowed:
                return 0
            self._prepare_allowed_ids(allowed)
            return int(
                self.connection.execute(
                    """
                    SELECT count(*) FROM notes n
                    JOIN temp.smart_search_allowed a ON a.note_id=n.note_id
                    """
                ).fetchone()[0]
            )

    def _prepare_allowed_ids(self, allowed_note_ids: set[int]) -> None:
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS smart_search_allowed("
            "note_id INTEGER PRIMARY KEY)"
        )
        self.connection.execute("DELETE FROM temp.smart_search_allowed")
        self.connection.executemany(
            "INSERT INTO temp.smart_search_allowed(note_id) VALUES(?)",
            ((note_id,) for note_id in allowed_note_ids),
        )

    def stats(self) -> IndexStats:
        with self._lock:
            note_count = int(
                self.connection.execute("SELECT count(*) FROM notes").fetchone()[0]
            )
            vocabulary_size = int(
                self.connection.execute("SELECT count(*) FROM vocabulary").fetchone()[0]
            )
            alias_count = int(
                self.connection.execute("SELECT count(*) FROM aliases").fetchone()[0]
            )
            size = 0
            if not self._memory:
                for candidate in (
                    self.path,
                    Path(str(self.path) + "-wal"),
                    Path(str(self.path) + "-shm"),
                ):
                    if candidate.exists():
                        size += candidate.stat().st_size
            return IndexStats(
                note_count=note_count,
                vocabulary_size=vocabulary_size,
                alias_count=alias_count,
                database_bytes=size,
                schema_version=SCHEMA_VERSION,
                generation=self.generation,
            )


def _content_hash(note: IndexedNote) -> bytes:
    content_payload = {
        "note_id": note.note_id,
        "fields": dict(note.fields),
        "tags": note.tags,
        "decks": note.decks,
        "note_type": note.note_type,
        "card_ids": note.card_ids,
        "modified_seconds": note.modified_seconds,
        "guid": note.guid,
        "title": note.title,
    }
    return hashlib.sha256(
        json.dumps(
            content_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).digest()


def _prepare_note(
    note: IndexedNote,
    *,
    content_hash: bytes | None = None,
) -> dict[str, Any]:
    plain_fields = {
        name: strip_html_and_cloze(value) for name, value in note.fields.items()
    }
    nonempty_values = [value for value in plain_fields.values() if value]
    title = strip_html_and_cloze(note.title) if note.title else ""
    if not title:
        title = truncate_text(nonempty_values[0], 180) if nonempty_values else f"Note {note.note_id}"
    plain_text = "\n".join(nonempty_values)
    normalized_text = join_searchable_text(
        [
            title,
            plain_text,
            " ".join(note.tags),
            " ".join(note.decks),
            note.note_type,
        ]
    )
    vocabulary_text = join_searchable_text(
        [plain_text, " ".join(note.tags), " ".join(note.decks), note.note_type]
    )
    field_names: dict[str, str] = {}
    for field_name in note.fields:
        normalized_name = normalize_text(field_name)
        if normalized_name:
            field_names.setdefault(normalized_name, field_name)
    payload_data = {
        "v": _PAYLOAD_VERSION,
        "c": list(note.card_ids),
        "f": dict(note.fields),
        "t": list(note.tags),
        "d": list(note.decks),
        "n": note.note_type,
        "p": plain_text,
        "x": normalized_text,
    }
    return {
        "title": title,
        "plain_text": plain_text,
        "normalized_text": normalized_text,
        "vocabulary_text": vocabulary_text,
        "field_names": field_names,
        "payload_data": payload_data,
        "payload": _pack_payload(payload_data),
        "content_hash": content_hash if content_hash is not None else _content_hash(note),
    }


def _row_to_document(row: sqlite3.Row) -> IndexedDocument:
    payload = _unpack_payload(row["payload"])
    return IndexedDocument(
        note_id=int(row["note_id"]),
        card_ids=tuple(int(value) for value in payload["c"]),
        title=str(row["title"]),
        plain_text=str(payload["p"]),
        normalized_text=str(payload["x"]),
        fields={str(key): str(value) for key, value in payload["f"].items()},
        tags=tuple(str(value) for value in payload["t"]),
        decks=tuple(str(value) for value in payload["d"]),
        note_type=str(payload["n"]),
    )


def _pack_payload(payload: Mapping[str, Any]) -> bytes:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return zlib.compress(serialized, level=6)


def _unpack_payload(value: bytes | memoryview) -> dict[str, Any]:
    raw = value.tobytes() if isinstance(value, memoryview) else bytes(value)
    payload = json.loads(zlib.decompress(raw).decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("v") != _PAYLOAD_VERSION:
        raise IncompatibleIndex("The note payload version is not supported.")
    return payload


def _fts_values(
    title: str,
    payload: Mapping[str, Any],
) -> tuple[str, str, str, str, str]:
    return (
        normalize_text(title),
        normalize_text(str(payload["p"])),
        normalize_text(" ".join(str(value) for value in payload["t"])),
        normalize_text(" ".join(str(value) for value in payload["d"])),
        normalize_text(str(payload["n"])),
    )


def _vocabulary_counts(payload: Mapping[str, Any]) -> Counter[str]:
    vocabulary_text = join_searchable_text(
        [
            str(payload["p"]),
            " ".join(str(value) for value in payload["t"]),
            " ".join(str(value) for value in payload["d"]),
            str(payload["n"]),
        ]
    )
    return Counter(tokenize(vocabulary_text))


def _validate_index_path(path: Path, memory: bool) -> None:
    if memory:
        return
    if path.name.casefold() in _UNSAFE_COLLECTION_NAMES:
        raise UnsafeIndexPath("The search index may not use an Anki collection path.")
    if path.suffix.casefold() in {".anki2", ".anki21"}:
        raise UnsafeIndexPath("The search index must be separate from the Anki collection.")
    if path.exists() and path.is_dir():
        raise UnsafeIndexPath("The search index path points to a directory.")


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
