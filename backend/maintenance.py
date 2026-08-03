"""Durable, profile-local maintenance state for derived search indexes.

The journal is deliberately independent of Anki's collection database.  It
records only enough state to resume targeted maintenance after a restart:

* note IDs that need derived-index work,
* a race-safe request for a compact collection audit, and
* a compact manifest used to identify additions, changes, and deletions.

Callers should keep one :class:`MaintenanceJournal` per Anki profile and close
it when the profile closes.  SQLite's WAL and FULL synchronous mode make an
enqueue durable before the method returns without requiring any collection
access from this module.
"""

from __future__ import annotations

from contextlib import contextmanager
from enum import IntFlag
from pathlib import Path
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from .compat import dataclass


SCHEMA_VERSION = 4
_UNSAFE_DATABASE_NAMES = frozenset(
    {"collection.anki2", "collection.anki21", "collection.media"}
)


class IncompatibleMaintenanceJournal(RuntimeError):
    """Raised when durable maintenance state needs an explicit migration."""


class UnsafeMaintenancePath(ValueError):
    """Raised when a caller points the journal at an Anki collection path."""


class DirtyReason(IntFlag):
    """Independent kinds of derived state that a note change can invalidate."""

    LEXICAL = 1 << 0
    SEMANTIC = 1 << 1
    VOCABULARY = 1 << 2
    CARD_METADATA = 1 << 3
    DELETED = 1 << 4

    NOTE_CONTENT = LEXICAL | SEMANTIC | VOCABULARY


@dataclass(frozen=True, slots=True)
class DirtyNote:
    note_id: int
    reasons: DirtyReason
    sequence: int
    queued_at: float
    attempts: int


@dataclass(frozen=True, slots=True)
class AuditRequest:
    sequence: int
    requested_at: float
    reason: str


@dataclass(frozen=True, slots=True)
class NoteManifest:
    """A small collection-row fingerprint, not a copy of note content.

    ``usn`` may legitimately be ``-1`` for unsynced local changes.  The
    checksum is intentionally paired with lengths and Anki's other revision
    fields; none of these values is treated as a cryptographic proof that two
    notes are equal.
    """

    note_id: int
    modified_seconds: int
    usn: int
    note_type_id: int
    fields_length: int
    tags_length: int
    field_checksum: int
    fields_digest: bytes = b""
    tags_digest: bytes = b""


@dataclass(frozen=True, slots=True)
class ManifestDiff:
    added: tuple[int, ...]
    changed: tuple[int, ...]
    deleted: tuple[int, ...]
    unchanged: int

    @property
    def candidate_note_ids(self) -> tuple[int, ...]:
        """Return live notes that need hydration, in stable ID order."""

        return tuple(sorted((*self.added, *self.changed)))


@dataclass(frozen=True, slots=True)
class CardManifest:
    """Stable card ownership used by deck/card facets.

    ``home_deck_id`` is the card's canonical home deck (``odid`` while the
    card is in a filtered deck, otherwise ``did``).  Transient scheduling
    fields intentionally do not belong in this manifest.
    """

    card_id: int
    note_id: int
    home_deck_id: int


@dataclass(frozen=True, slots=True)
class CardManifestDiff:
    """Changes to card identity, ownership, or canonical home deck.

    The per-kind note ID sets make it possible to refresh only the affected
    note facets.  For an ownership change, ``changed_note_ids`` contains both
    the previously saved owner and the current owner.
    """

    added: tuple[int, ...]
    changed: tuple[int, ...]
    deleted: tuple[int, ...]
    added_note_ids: tuple[int, ...]
    changed_note_ids: tuple[int, ...]
    deleted_note_ids: tuple[int, ...]
    unchanged: int

    @property
    def affected_note_ids(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    *self.added_note_ids,
                    *self.changed_note_ids,
                    *self.deleted_note_ids,
                }
            )
        )


@dataclass(frozen=True, slots=True)
class FacetManifestKey:
    """Identity of one public metadata object, such as a deck or notetype."""

    facet: str
    object_id: int


@dataclass(frozen=True, slots=True)
class FacetManifest:
    """Compact signature for public metadata that can affect search facets.

    Callers are responsible for producing a stable signature from the public
    fields they care about.  Typical namespaces are ``deck`` (deck name) and
    ``notetype`` (name plus field schema).
    """

    facet: str
    object_id: int
    signature: str

    @property
    def key(self) -> FacetManifestKey:
        return FacetManifestKey(self.facet, self.object_id)


@dataclass(frozen=True, slots=True)
class FacetManifestDiff:
    added: tuple[FacetManifestKey, ...]
    changed: tuple[FacetManifestKey, ...]
    deleted: tuple[FacetManifestKey, ...]
    unchanged: int


class MaintenanceJournal:
    """Own durable maintenance state for one profile.

    The class uses one connection guarded by an ``RLock``.  Methods can safely
    be called from Anki's GUI and background threads, although collection reads
    and writes remain the caller's responsibility.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.name.casefold() in _UNSAFE_DATABASE_NAMES:
            raise UnsafeMaintenancePath(
                "Maintenance state must never use an Anki collection database."
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        try:
            self._connect()
        except Exception:
            self.close()
            raise

    def _connect(self) -> None:
        connection = sqlite3.connect(
            str(self.path),
            timeout=8.0,
            isolation_level=None,
            check_same_thread=False,
        )
        # Take ownership immediately. A corrupt SQLite file can raise from an
        # early PRAGMA, before schema initialization begins; assigning first
        # lets the constructor's recovery path close that handle reliably.
        self._connection = connection
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=8000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            self._initialize_schema()
        except Exception:
            self.close()
            raise

    @property
    def connection(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise RuntimeError("The maintenance journal is closed.")
        return connection

    @property
    def closed(self) -> bool:
        return self._connection is None

    def _initialize_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        row = self.connection.execute(
            "SELECT value FROM journal_meta WHERE key = 'schema_version'"
        ).fetchone()
        try:
            existing_version = int(row["value"]) if row is not None else None
        except (TypeError, ValueError) as exc:
            raise IncompatibleMaintenanceJournal(
                "Maintenance schema version is not a valid integer."
            ) from exc
        if existing_version not in (None, 1, 2, 3, SCHEMA_VERSION):
            raise IncompatibleMaintenanceJournal(
                f"Maintenance schema {existing_version} is incompatible with "
                f"{SCHEMA_VERSION}."
            )

        with self._transaction():
            manifest_layout_changed = False
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dirty_notes (
                    note_id INTEGER PRIMARY KEY CHECK (note_id > 0),
                    reasons INTEGER NOT NULL CHECK (reasons > 0),
                    sequence INTEGER NOT NULL CHECK (sequence > 0),
                    queued_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
                )
                """
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS dirty_notes_sequence_idx
                    ON dirty_notes(sequence, note_id)
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS note_manifest (
                    note_id INTEGER PRIMARY KEY CHECK (note_id > 0),
                    modified_seconds INTEGER NOT NULL,
                    usn INTEGER NOT NULL,
                    note_type_id INTEGER NOT NULL,
                    fields_length INTEGER NOT NULL CHECK (fields_length >= 0),
                    tags_length INTEGER NOT NULL CHECK (tags_length >= 0),
                    field_checksum INTEGER NOT NULL,
                    fields_digest BLOB,
                    tags_digest BLOB
                )
                """
            )
            manifest_columns = {
                str(column[1])
                for column in self.connection.execute(
                    "PRAGMA table_info(note_manifest)"
                )
            }
            if "fields_digest" not in manifest_columns:
                self.connection.execute(
                    "ALTER TABLE note_manifest ADD COLUMN fields_digest BLOB"
                )
                manifest_layout_changed = True
            if "tags_digest" not in manifest_columns:
                self.connection.execute(
                    "ALTER TABLE note_manifest ADD COLUMN tags_digest BLOB"
                )
                manifest_layout_changed = True
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS card_manifest (
                    card_id INTEGER PRIMARY KEY CHECK (card_id > 0),
                    note_id INTEGER NOT NULL CHECK (note_id > 0),
                    home_deck_id INTEGER NOT NULL CHECK (home_deck_id > 0)
                )
                """
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS card_manifest_note_id_idx
                    ON card_manifest(note_id, card_id)
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS facet_manifest (
                    facet TEXT NOT NULL CHECK (length(facet) > 0),
                    object_id INTEGER NOT NULL CHECK (object_id > 0),
                    signature TEXT NOT NULL,
                    PRIMARY KEY (facet, object_id)
                )
                """
            )
            defaults = {
                "next_sequence": "0",
                "audit_needed": "0",
                "manifest_initialized": "0",
            }
            self.connection.executemany(
                "INSERT OR IGNORE INTO journal_meta(key, value) VALUES (?, ?)",
                defaults.items(),
            )
            if manifest_layout_changed:
                # NULL legacy digests must never be trusted as a complete
                # manifest. The next bounded audit seeds both digests from the
                # live collection before incremental comparisons resume.
                self.connection.execute(
                    "INSERT OR REPLACE INTO journal_meta(key, value) "
                    "VALUES ('manifest_initialized', '0')"
                )
            self.connection.execute(
                """
                INSERT INTO journal_meta(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def _executemany_batched(
        self,
        sql: str,
        rows: Iterable[tuple[Any, ...]],
        *,
        cancel_check: Callable[[], None] | None = None,
        batch_size: int = 500,
    ) -> None:
        """Execute bounded write batches inside the caller's transaction."""

        batch: list[tuple[Any, ...]] = []
        size = max(1, int(batch_size))
        for row in rows:
            if not batch and cancel_check is not None:
                cancel_check()
            batch.append(row)
            if len(batch) >= size:
                self.connection.executemany(sql, batch)
                batch.clear()
        if batch:
            self.connection.executemany(sql, batch)
        if cancel_check is not None:
            cancel_check()

    def _fetchall_cancellable(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[sqlite3.Row]:
        """Run a manifest join with cooperative SQLite interruption."""

        if cancel_check is None:
            return self.connection.execute(sql, parameters).fetchall()
        cancellation_errors: list[Exception] = []

        def progress_handler() -> int:
            try:
                cancel_check()
            except Exception as error:
                cancellation_errors.append(error)
                return 1
            return 0

        cancel_check()
        self.connection.set_progress_handler(progress_handler, 500)
        try:
            rows = self.connection.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError:
            if cancellation_errors:
                raise cancellation_errors[0]
            raise
        finally:
            self.connection.set_progress_handler(None, 0)
        cancel_check()
        return rows

    def _next_sequence(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM journal_meta WHERE key = 'next_sequence'"
        ).fetchone()
        sequence = int(row["value"] if row else 0) + 1
        self.connection.execute(
            "INSERT OR REPLACE INTO journal_meta(key, value) VALUES (?, ?)",
            ("next_sequence", str(sequence)),
        )
        return sequence

    @staticmethod
    def _normalize_note_ids(note_ids: Iterable[int]) -> tuple[int, ...]:
        normalized = tuple(sorted({int(note_id) for note_id in note_ids}))
        if any(note_id <= 0 for note_id in normalized):
            raise ValueError("note IDs must be positive integers")
        return normalized

    @staticmethod
    def _normalize_reasons(reasons: DirtyReason | int) -> DirtyReason:
        normalized = DirtyReason(int(reasons))
        if int(normalized) <= 0:
            raise ValueError("at least one dirty reason is required")
        return normalized

    def enqueue(
        self,
        note_ids: Iterable[int],
        reasons: DirtyReason | int,
        *,
        queued_at: float | None = None,
    ) -> int | None:
        """Merge reasons into pending notes and return the enqueue sequence.

        All note IDs in one call receive the same sequence.  A later enqueue
        always advances that sequence, which lets a worker prove that the row
        it processed has not been superseded before acknowledging it.
        """

        ids = self._normalize_note_ids(note_ids)
        if not ids:
            return None
        flags = self._normalize_reasons(reasons)
        timestamp = time.time() if queued_at is None else float(queued_at)
        with self._lock, self._transaction():
            sequence = self._next_sequence()
            self.connection.executemany(
                """
                INSERT INTO dirty_notes(
                    note_id, reasons, sequence, queued_at, attempts
                ) VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(note_id) DO UPDATE SET
                    reasons = dirty_notes.reasons | excluded.reasons,
                    sequence = excluded.sequence,
                    queued_at = excluded.queued_at,
                    attempts = 0
                """,
                (
                    (note_id, int(flags), sequence, timestamp)
                    for note_id in ids
                ),
            )
        return sequence

    def pending(self, *, limit: int | None = None) -> tuple[DirtyNote, ...]:
        """Return a stable snapshot of pending work without claiming it."""

        if limit is not None and int(limit) < 0:
            raise ValueError("limit must be non-negative")
        sql = (
            "SELECT note_id, reasons, sequence, queued_at, attempts "
            "FROM dirty_notes ORDER BY sequence, note_id"
        )
        parameters: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (int(limit),)
        with self._lock:
            rows = self.connection.execute(sql, parameters).fetchall()
        return tuple(
            DirtyNote(
                note_id=int(row["note_id"]),
                reasons=DirtyReason(int(row["reasons"])),
                sequence=int(row["sequence"]),
                queued_at=float(row["queued_at"]),
                attempts=int(row["attempts"]),
            )
            for row in rows
        )

    def pending_count(self) -> int:
        with self._lock:
            row = self.connection.execute(
                "SELECT count(*) AS count FROM dirty_notes"
            ).fetchone()
        return int(row["count"])

    def try_pending(
        self, *, limit: int | None = None
    ) -> tuple[DirtyNote, ...] | None:
        """Return pending work without waiting behind a background writer."""

        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self.pending(limit=limit)
        finally:
            self._lock.release()

    def try_audit_request(self) -> tuple[bool, AuditRequest | None]:
        """Return ``(available, request)`` without blocking a GUI callback."""

        if not self._lock.acquire(blocking=False):
            return False, None
        try:
            return True, self.audit_request()
        finally:
            self._lock.release()

    def try_work_state(self) -> tuple[int, bool] | None:
        """Return queue size/audit state, or ``None`` while a writer owns it."""

        if not self._lock.acquire(blocking=False):
            return None
        try:
            row = self.connection.execute(
                "SELECT count(*) AS count FROM dirty_notes"
            ).fetchone()
            return int(row["count"]), self.audit_request() is not None
        finally:
            self._lock.release()

    def mark_attempted(self, note_id: int, sequence: int) -> bool:
        """Increment retries only if this is still the worker's queue row."""

        with self._lock, self._transaction():
            cursor = self.connection.execute(
                """
                UPDATE dirty_notes
                SET attempts = attempts + 1
                WHERE note_id = ? AND sequence = ?
                """,
                (int(note_id), int(sequence)),
            )
        return cursor.rowcount == 1

    def acknowledge(
        self,
        note_id: int,
        sequence: int,
        *,
        reasons: DirtyReason | int | None = None,
    ) -> bool:
        """Acknowledge work only if no newer enqueue superseded it.

        When ``reasons`` is omitted, the whole queue row is removed.  When it
        is supplied, only those reason bits are cleared, which allows lexical
        and semantic workers to finish independently.  A mismatched sequence
        never modifies the newer row.
        """

        processed = None if reasons is None else self._normalize_reasons(reasons)
        with self._lock, self._transaction():
            row = self.connection.execute(
                "SELECT reasons, sequence FROM dirty_notes WHERE note_id = ?",
                (int(note_id),),
            ).fetchone()
            if row is None or int(row["sequence"]) != int(sequence):
                return False
            remaining = 0
            if processed is not None:
                remaining = int(row["reasons"]) & ~int(processed)
            if remaining:
                self.connection.execute(
                    "UPDATE dirty_notes SET reasons = ? WHERE note_id = ?",
                    (remaining, int(note_id)),
                )
            else:
                self.connection.execute(
                    "DELETE FROM dirty_notes WHERE note_id = ?",
                    (int(note_id),),
                )
        return True

    def acknowledge_many(
        self,
        entries: Iterable[tuple[int, int]],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> int:
        """Acknowledge exact queue generations in one durable transaction.

        A newer enqueue retains its row because every delete is guarded by
        both note ID and sequence. Cancellation rolls the whole batch back, so
        a reviewer transition cannot leave a half-acknowledged batch.
        """

        normalized = tuple(
            dict.fromkeys(
                (int(note_id), int(sequence))
                for note_id, sequence in entries
            )
        )
        if any(
            note_id <= 0 or sequence <= 0
            for note_id, sequence in normalized
        ):
            raise ValueError("note IDs and sequences must be positive integers")
        if not normalized:
            return 0
        acknowledged = 0
        with self._lock, self._transaction():
            for index, (note_id, sequence) in enumerate(normalized, start=1):
                if cancel_check is not None and (
                    index == 1 or index % 32 == 0
                ):
                    cancel_check()
                cursor = self.connection.execute(
                    "DELETE FROM dirty_notes "
                    "WHERE note_id = ? AND sequence = ?",
                    (note_id, sequence),
                )
                acknowledged += int(cursor.rowcount == 1)
            if cancel_check is not None:
                cancel_check()
        return acknowledged

    def request_audit(self, reason: str = "unspecified") -> int:
        """Persist an audit request and return its race-safe sequence."""

        timestamp = time.time()
        with self._lock, self._transaction():
            sequence = self._next_sequence()
            values = {
                "audit_needed": "1",
                "audit_sequence": str(sequence),
                "audit_requested_at": repr(timestamp),
                "audit_reason": str(reason),
            }
            self.connection.executemany(
                "INSERT OR REPLACE INTO journal_meta(key, value) VALUES (?, ?)",
                values.items(),
            )
        return sequence

    @property
    def audit_needed(self) -> bool:
        return self.audit_request() is not None

    def audit_request(self) -> AuditRequest | None:
        with self._lock:
            rows = dict(
                self.connection.execute(
                    """
                    SELECT key, value FROM journal_meta
                    WHERE key IN (
                        'audit_needed', 'audit_sequence',
                        'audit_requested_at', 'audit_reason'
                    )
                    """
                )
            )
        if rows.get("audit_needed") != "1":
            return None
        return AuditRequest(
            sequence=int(rows["audit_sequence"]),
            requested_at=float(rows["audit_requested_at"]),
            reason=rows.get("audit_reason", ""),
        )

    def complete_audit(
        self,
        sequence: int,
        *,
        clear_meta_keys: Iterable[str] = (),
    ) -> bool:
        """Publish one exact audit completion atomically.

        ``clear_meta_keys`` is removed in the same transaction only when the
        requested sequence is still current. This keeps recovery guards
        attached to any newer audit that races a worker finishing an older one.
        """

        keys = tuple(dict.fromkeys(str(key) for key in clear_meta_keys))
        if any(not key for key in keys):
            raise ValueError("metadata keys must not be empty")

        with self._lock, self._transaction():
            rows = dict(
                self.connection.execute(
                    """
                    SELECT key, value FROM journal_meta
                    WHERE key IN ('audit_needed', 'audit_sequence')
                    """
                )
            )
            if (
                rows.get("audit_needed") != "1"
                or int(rows.get("audit_sequence", "0")) != int(sequence)
            ):
                return False
            self.connection.executemany(
                "INSERT OR REPLACE INTO journal_meta(key, value) VALUES (?, ?)",
                (
                    ("audit_needed", "0"),
                    ("audit_completed_sequence", str(int(sequence))),
                    ("audit_completed_at", repr(time.time())),
                ),
            )
            if keys:
                self.connection.executemany(
                    "DELETE FROM journal_meta WHERE key = ?",
                    ((key,) for key in keys),
                )
        return True

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT value FROM journal_meta WHERE key = ?", (str(key),)
            ).fetchone()
        return str(row["value"]) if row is not None else default

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock, self._transaction():
            self.connection.execute(
                "INSERT OR REPLACE INTO journal_meta(key, value) VALUES (?, ?)",
                (str(key), str(value)),
            )

    def delete_meta(self, key: str) -> bool:
        with self._lock, self._transaction():
            cursor = self.connection.execute(
                "DELETE FROM journal_meta WHERE key = ?", (str(key),)
            )
        return cursor.rowcount == 1

    @staticmethod
    def _manifest_values(row: NoteManifest) -> tuple[Any, ...]:
        values = (
            int(row.note_id),
            int(row.modified_seconds),
            int(row.usn),
            int(row.note_type_id),
            int(row.fields_length),
            int(row.tags_length),
            int(row.field_checksum),
            bytes(row.fields_digest),
            bytes(row.tags_digest),
        )
        if values[0] <= 0:
            raise ValueError("manifest note IDs must be positive")
        if values[4] < 0 or values[5] < 0:
            raise ValueError("manifest lengths must be non-negative")
        return values

    @staticmethod
    def _manifest_insert_sql(table: str) -> str:
        if table not in {"note_manifest", "current_note_manifest"}:
            raise ValueError("unexpected manifest table")
        return f"""
            INSERT INTO {table}(
                note_id, modified_seconds, usn, note_type_id,
                fields_length, tags_length, field_checksum,
                fields_digest, tags_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

    def replace_manifest(
        self,
        rows: Iterable[NoteManifest],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> int:
        """Atomically replace the saved manifest after a successful audit."""

        with self._lock, self._transaction():
            self.connection.execute("DELETE FROM note_manifest")
            batch: list[tuple[Any, ...]] = []
            for row in rows:
                if cancel_check is not None and not batch:
                    cancel_check()
                batch.append(self._manifest_values(row))
                if len(batch) >= 500:
                    self.connection.executemany(
                        self._manifest_insert_sql("note_manifest"), batch
                    )
                    batch.clear()
            if batch:
                self.connection.executemany(
                    self._manifest_insert_sql("note_manifest"), batch
                )
            if cancel_check is not None:
                cancel_check()
            count = int(
                self.connection.execute(
                    "SELECT count(*) FROM note_manifest"
                ).fetchone()[0]
            )
        return count

    def upsert_manifest(self, rows: Iterable[NoteManifest]) -> int:
        """Insert or replace exact manifest rows and return affected count."""

        values = tuple(self._manifest_values(row) for row in rows)
        if not values:
            return 0
        sql = self._manifest_insert_sql("note_manifest") + """
            ON CONFLICT(note_id) DO UPDATE SET
                modified_seconds = excluded.modified_seconds,
                usn = excluded.usn,
                note_type_id = excluded.note_type_id,
                fields_length = excluded.fields_length,
                tags_length = excluded.tags_length,
                field_checksum = excluded.field_checksum,
                fields_digest = excluded.fields_digest,
                tags_digest = excluded.tags_digest
        """
        with self._lock, self._transaction():
            self.connection.executemany(sql, values)
        return len(values)

    def delete_manifest(self, note_ids: Iterable[int]) -> int:
        ids = self._normalize_note_ids(note_ids)
        if not ids:
            return 0
        with self._lock, self._transaction():
            before = self.connection.total_changes
            self.connection.executemany(
                "DELETE FROM note_manifest WHERE note_id = ?",
                ((note_id,) for note_id in ids),
            )
            changed = self.connection.total_changes - before
        return int(changed)

    def manifest_count(self) -> int:
        with self._lock:
            return int(
                self.connection.execute(
                    "SELECT count(*) FROM note_manifest"
                ).fetchone()[0]
            )

    def manifest_rows(
        self,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> tuple[NoteManifest, ...]:
        """Return saved rows for diagnostics and small integration tasks."""

        with self._lock:
            rows = self._fetchall_cancellable(
                """
                SELECT note_id, modified_seconds, usn, note_type_id,
                       fields_length, tags_length, field_checksum,
                       fields_digest, tags_digest
                FROM note_manifest ORDER BY note_id
                """,
                cancel_check=cancel_check,
            )
        output: list[NoteManifest] = []
        for index, row in enumerate(rows, start=1):
            if cancel_check is not None and (
                index == 1 or index % 500 == 0
            ):
                cancel_check()
            output.append(NoteManifest(
                note_id=int(row["note_id"]),
                modified_seconds=int(row["modified_seconds"]),
                usn=int(row["usn"]),
                note_type_id=int(row["note_type_id"]),
                fields_length=int(row["fields_length"]),
                tags_length=int(row["tags_length"]),
                field_checksum=int(row["field_checksum"]),
                fields_digest=(
                    bytes(row["fields_digest"])
                    if row["fields_digest"] is not None
                    else b""
                ),
                tags_digest=(
                    bytes(row["tags_digest"])
                    if row["tags_digest"] is not None
                    else b""
                ),
            ))
        if cancel_check is not None:
            cancel_check()
        return tuple(output)

    def diff_manifest(
        self,
        current: Iterable[NoteManifest],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> ManifestDiff:
        """Compare a streamed compact inventory without changing saved state."""

        create_sql = """
            CREATE TEMP TABLE IF NOT EXISTS current_note_manifest (
                note_id INTEGER PRIMARY KEY,
                modified_seconds INTEGER NOT NULL,
                usn INTEGER NOT NULL,
                note_type_id INTEGER NOT NULL,
                fields_length INTEGER NOT NULL,
                tags_length INTEGER NOT NULL,
                field_checksum INTEGER NOT NULL,
                fields_digest BLOB NOT NULL,
                tags_digest BLOB NOT NULL
            )
        """
        with self._lock, self._transaction():
            self.connection.execute(create_sql)
            self.connection.execute("DELETE FROM current_note_manifest")
            self._executemany_batched(
                self._manifest_insert_sql("current_note_manifest"),
                (self._manifest_values(row) for row in current),
                cancel_check=cancel_check,
            )
            added = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    """
                    SELECT current.note_id
                    FROM current_note_manifest AS current
                    LEFT JOIN note_manifest AS saved
                        ON saved.note_id = current.note_id
                    WHERE saved.note_id IS NULL
                    ORDER BY current.note_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            changed = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    """
                    SELECT current.note_id
                    FROM current_note_manifest AS current
                    INNER JOIN note_manifest AS saved
                        ON saved.note_id = current.note_id
                    WHERE current.modified_seconds != saved.modified_seconds
                       OR current.usn != saved.usn
                       OR current.note_type_id != saved.note_type_id
                       OR current.fields_length != saved.fields_length
                       OR current.tags_length != saved.tags_length
                       OR current.field_checksum != saved.field_checksum
                       OR saved.fields_digest IS NULL
                       OR current.fields_digest != saved.fields_digest
                       OR saved.tags_digest IS NULL
                       OR current.tags_digest != saved.tags_digest
                    ORDER BY current.note_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            deleted = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    """
                    SELECT saved.note_id
                    FROM note_manifest AS saved
                    LEFT JOIN current_note_manifest AS current
                        ON current.note_id = saved.note_id
                    WHERE current.note_id IS NULL
                    ORDER BY saved.note_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            current_count = int(self._fetchall_cancellable(
                "SELECT count(*) FROM current_note_manifest",
                cancel_check=cancel_check,
            )[0][0])
            self.connection.execute("DELETE FROM current_note_manifest")
        return ManifestDiff(
            added=added,
            changed=changed,
            deleted=deleted,
            unchanged=max(0, current_count - len(added) - len(changed)),
        )

    @staticmethod
    def _card_manifest_values(row: CardManifest) -> tuple[int, int, int]:
        values = (
            int(row.card_id),
            int(row.note_id),
            int(row.home_deck_id),
        )
        if values[0] <= 0:
            raise ValueError("manifest card IDs must be positive")
        if values[1] <= 0:
            raise ValueError("manifest card note IDs must be positive")
        if values[2] <= 0:
            raise ValueError("manifest home deck IDs must be positive")
        return values

    @staticmethod
    def _card_manifest_insert_sql(table: str) -> str:
        if table not in {"card_manifest", "current_card_manifest"}:
            raise ValueError("unexpected card manifest table")
        return f"""
            INSERT INTO {table}(card_id, note_id, home_deck_id)
            VALUES (?, ?, ?)
        """

    @staticmethod
    def _normalize_card_ids(card_ids: Iterable[int]) -> tuple[int, ...]:
        normalized = tuple(sorted({int(card_id) for card_id in card_ids}))
        if any(card_id <= 0 for card_id in normalized):
            raise ValueError("card IDs must be positive integers")
        return normalized

    def replace_card_manifest(
        self,
        rows: Iterable[CardManifest],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> int:
        """Atomically replace canonical card ownership after an audit."""

        with self._lock, self._transaction():
            self.connection.execute("DELETE FROM card_manifest")
            batch: list[tuple[int, int, int]] = []
            for row in rows:
                if cancel_check is not None and not batch:
                    cancel_check()
                batch.append(self._card_manifest_values(row))
                if len(batch) >= 500:
                    self.connection.executemany(
                        self._card_manifest_insert_sql("card_manifest"), batch
                    )
                    batch.clear()
            if batch:
                self.connection.executemany(
                    self._card_manifest_insert_sql("card_manifest"), batch
                )
            if cancel_check is not None:
                cancel_check()
            count = int(
                self.connection.execute(
                    "SELECT count(*) FROM card_manifest"
                ).fetchone()[0]
            )
        return count

    def upsert_card_manifest(self, rows: Iterable[CardManifest]) -> int:
        values = tuple(self._card_manifest_values(row) for row in rows)
        if not values:
            return 0
        sql = self._card_manifest_insert_sql("card_manifest") + """
            ON CONFLICT(card_id) DO UPDATE SET
                note_id = excluded.note_id,
                home_deck_id = excluded.home_deck_id
        """
        with self._lock, self._transaction():
            self.connection.executemany(sql, values)
        return len(values)

    def delete_card_manifest(self, card_ids: Iterable[int]) -> int:
        ids = self._normalize_card_ids(card_ids)
        if not ids:
            return 0
        with self._lock, self._transaction():
            before = self.connection.total_changes
            self.connection.executemany(
                "DELETE FROM card_manifest WHERE card_id = ?",
                ((card_id,) for card_id in ids),
            )
            changed = self.connection.total_changes - before
        return int(changed)

    def replace_card_manifest_for_notes(
        self,
        note_ids: Iterable[int],
        rows: Iterable[CardManifest],
    ) -> int:
        """Atomically refresh card scope for a bounded set of note owners.

        Targeted note maintenance calls this after hydrating the same notes.
        Deleting the previous rows first is important: a removed card has no
        live row to upsert, but must still disappear from the saved manifest.
        """

        owners = tuple(sorted({int(note_id) for note_id in note_ids}))
        if any(note_id <= 0 for note_id in owners):
            raise ValueError("note IDs must be positive integers")
        values = tuple(self._card_manifest_values(row) for row in rows)
        if any(value[1] not in owners for value in values):
            raise ValueError("card manifest rows must belong to requested notes")
        if not owners:
            if values:
                raise ValueError("card manifest rows require note owners")
            return 0
        with self._lock, self._transaction():
            for offset in range(0, len(owners), 500):
                chunk = owners[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                self.connection.execute(
                    f"DELETE FROM card_manifest WHERE note_id IN ({placeholders})",
                    chunk,
                )
            self.connection.executemany(
                self._card_manifest_insert_sql("card_manifest"),
                values,
            )
        return len(values)

    def card_manifest_count(self) -> int:
        with self._lock:
            return int(
                self.connection.execute(
                    "SELECT count(*) FROM card_manifest"
                ).fetchone()[0]
            )

    def card_manifest_rows(
        self,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> tuple[CardManifest, ...]:
        with self._lock:
            rows = self._fetchall_cancellable(
                """
                SELECT card_id, note_id, home_deck_id
                FROM card_manifest ORDER BY card_id
                """,
                cancel_check=cancel_check,
            )
        output: list[CardManifest] = []
        for index, row in enumerate(rows, start=1):
            if cancel_check is not None and (
                index == 1 or index % 500 == 0
            ):
                cancel_check()
            output.append(CardManifest(
                card_id=int(row["card_id"]),
                note_id=int(row["note_id"]),
                home_deck_id=int(row["home_deck_id"]),
            ))
        if cancel_check is not None:
            cancel_check()
        return tuple(output)

    def diff_card_manifest(
        self,
        current: Iterable[CardManifest],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> CardManifestDiff:
        """Compare canonical card facets and identify every affected owner."""

        create_sql = """
            CREATE TEMP TABLE IF NOT EXISTS current_card_manifest (
                card_id INTEGER PRIMARY KEY,
                note_id INTEGER NOT NULL,
                home_deck_id INTEGER NOT NULL
            )
        """
        changed_join = """
            FROM current_card_manifest AS current
            INNER JOIN card_manifest AS saved
                ON saved.card_id = current.card_id
            WHERE current.note_id != saved.note_id
               OR current.home_deck_id != saved.home_deck_id
        """
        with self._lock, self._transaction():
            self.connection.execute(create_sql)
            self.connection.execute("DELETE FROM current_card_manifest")
            self._executemany_batched(
                self._card_manifest_insert_sql("current_card_manifest"),
                (self._card_manifest_values(row) for row in current),
                cancel_check=cancel_check,
            )
            added = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    """
                    SELECT current.card_id
                    FROM current_card_manifest AS current
                    LEFT JOIN card_manifest AS saved
                        ON saved.card_id = current.card_id
                    WHERE saved.card_id IS NULL
                    ORDER BY current.card_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            added_note_ids = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    """
                    SELECT DISTINCT current.note_id
                    FROM current_card_manifest AS current
                    LEFT JOIN card_manifest AS saved
                        ON saved.card_id = current.card_id
                    WHERE saved.card_id IS NULL
                    ORDER BY current.note_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            changed = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    "SELECT current.card_id "
                    + changed_join
                    + " ORDER BY current.card_id",
                    cancel_check=cancel_check,
                )
            )
            changed_note_ids = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    """
                    SELECT note_id FROM (
                        SELECT current.note_id AS note_id
                    """
                    + changed_join
                    + """
                        UNION
                        SELECT saved.note_id AS note_id
                    """
                    + changed_join
                    + """
                    ) ORDER BY note_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            deleted = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    """
                    SELECT saved.card_id
                    FROM card_manifest AS saved
                    LEFT JOIN current_card_manifest AS current
                        ON current.card_id = saved.card_id
                    WHERE current.card_id IS NULL
                    ORDER BY saved.card_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            deleted_note_ids = tuple(
                int(row[0])
                for row in self._fetchall_cancellable(
                    """
                    SELECT DISTINCT saved.note_id
                    FROM card_manifest AS saved
                    LEFT JOIN current_card_manifest AS current
                        ON current.card_id = saved.card_id
                    WHERE current.card_id IS NULL
                    ORDER BY saved.note_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            current_count = int(self._fetchall_cancellable(
                "SELECT count(*) FROM current_card_manifest",
                cancel_check=cancel_check,
            )[0][0])
            self.connection.execute("DELETE FROM current_card_manifest")
        return CardManifestDiff(
            added=added,
            changed=changed,
            deleted=deleted,
            added_note_ids=added_note_ids,
            changed_note_ids=changed_note_ids,
            deleted_note_ids=deleted_note_ids,
            unchanged=max(0, current_count - len(added) - len(changed)),
        )

    @staticmethod
    def _normalize_facet_name(facet: str) -> str:
        normalized = str(facet).strip().casefold()
        if not normalized:
            raise ValueError("facet namespace must not be empty")
        return normalized

    @classmethod
    def _facet_manifest_values(
        cls, row: FacetManifest
    ) -> tuple[str, int, str]:
        values = (
            cls._normalize_facet_name(row.facet),
            int(row.object_id),
            str(row.signature),
        )
        if values[1] <= 0:
            raise ValueError("facet object IDs must be positive")
        return values

    @classmethod
    def _facet_key_values(
        cls, key: FacetManifestKey
    ) -> tuple[str, int]:
        values = (
            cls._normalize_facet_name(key.facet),
            int(key.object_id),
        )
        if values[1] <= 0:
            raise ValueError("facet object IDs must be positive")
        return values

    @staticmethod
    def _facet_manifest_insert_sql(table: str) -> str:
        if table not in {"facet_manifest", "current_facet_manifest"}:
            raise ValueError("unexpected facet manifest table")
        return f"""
            INSERT INTO {table}(facet, object_id, signature)
            VALUES (?, ?, ?)
        """

    def replace_facet_manifest(
        self,
        rows: Iterable[FacetManifest],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> int:
        """Atomically replace all saved public-metadata signatures."""

        with self._lock, self._transaction():
            self.connection.execute("DELETE FROM facet_manifest")
            values = tuple(self._facet_manifest_values(row) for row in rows)
            if cancel_check is not None:
                cancel_check()
            self.connection.executemany(
                self._facet_manifest_insert_sql("facet_manifest"), values
            )
            if cancel_check is not None:
                cancel_check()
            count = int(
                self.connection.execute(
                    "SELECT count(*) FROM facet_manifest"
                ).fetchone()[0]
            )
        return count

    def upsert_facet_manifest(self, rows: Iterable[FacetManifest]) -> int:
        values = tuple(self._facet_manifest_values(row) for row in rows)
        if not values:
            return 0
        sql = self._facet_manifest_insert_sql("facet_manifest") + """
            ON CONFLICT(facet, object_id) DO UPDATE SET
                signature = excluded.signature
        """
        with self._lock, self._transaction():
            self.connection.executemany(sql, values)
        return len(values)

    def delete_facet_manifest(
        self, keys: Iterable[FacetManifestKey]
    ) -> int:
        values = tuple(sorted({self._facet_key_values(key) for key in keys}))
        if not values:
            return 0
        with self._lock, self._transaction():
            before = self.connection.total_changes
            self.connection.executemany(
                "DELETE FROM facet_manifest WHERE facet = ? AND object_id = ?",
                values,
            )
            changed = self.connection.total_changes - before
        return int(changed)

    def facet_manifest_count(self, *, facet: str | None = None) -> int:
        with self._lock:
            if facet is None:
                row = self.connection.execute(
                    "SELECT count(*) FROM facet_manifest"
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT count(*) FROM facet_manifest WHERE facet = ?",
                    (self._normalize_facet_name(facet),),
                ).fetchone()
        return int(row[0])

    def facet_manifest_rows(
        self, *, facet: str | None = None
    ) -> tuple[FacetManifest, ...]:
        sql = """
            SELECT facet, object_id, signature
            FROM facet_manifest
        """
        parameters: tuple[Any, ...] = ()
        if facet is not None:
            sql += " WHERE facet = ?"
            parameters = (self._normalize_facet_name(facet),)
        sql += " ORDER BY facet, object_id"
        with self._lock:
            rows = self.connection.execute(sql, parameters).fetchall()
        return tuple(
            FacetManifest(
                facet=str(row["facet"]),
                object_id=int(row["object_id"]),
                signature=str(row["signature"]),
            )
            for row in rows
        )

    def diff_facet_manifest(
        self,
        current: Iterable[FacetManifest],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> FacetManifestDiff:
        """Compare deck/notetype signatures without changing saved state."""

        create_sql = """
            CREATE TEMP TABLE IF NOT EXISTS current_facet_manifest (
                facet TEXT NOT NULL,
                object_id INTEGER NOT NULL,
                signature TEXT NOT NULL,
                PRIMARY KEY (facet, object_id)
            )
        """

        def keys(rows: Iterable[sqlite3.Row]) -> tuple[FacetManifestKey, ...]:
            return tuple(
                FacetManifestKey(str(row[0]), int(row[1])) for row in rows
            )

        with self._lock, self._transaction():
            self.connection.execute(create_sql)
            self.connection.execute("DELETE FROM current_facet_manifest")
            self._executemany_batched(
                self._facet_manifest_insert_sql("current_facet_manifest"),
                (self._facet_manifest_values(row) for row in current),
                cancel_check=cancel_check,
            )
            added = keys(
                self._fetchall_cancellable(
                    """
                    SELECT current.facet, current.object_id
                    FROM current_facet_manifest AS current
                    LEFT JOIN facet_manifest AS saved
                        ON saved.facet = current.facet
                       AND saved.object_id = current.object_id
                    WHERE saved.object_id IS NULL
                    ORDER BY current.facet, current.object_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            changed = keys(
                self._fetchall_cancellable(
                    """
                    SELECT current.facet, current.object_id
                    FROM current_facet_manifest AS current
                    INNER JOIN facet_manifest AS saved
                        ON saved.facet = current.facet
                       AND saved.object_id = current.object_id
                    WHERE current.signature != saved.signature
                    ORDER BY current.facet, current.object_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            deleted = keys(
                self._fetchall_cancellable(
                    """
                    SELECT saved.facet, saved.object_id
                    FROM facet_manifest AS saved
                    LEFT JOIN current_facet_manifest AS current
                        ON current.facet = saved.facet
                       AND current.object_id = saved.object_id
                    WHERE current.object_id IS NULL
                    ORDER BY saved.facet, saved.object_id
                    """,
                    cancel_check=cancel_check,
                )
            )
            current_count = int(self._fetchall_cancellable(
                "SELECT count(*) FROM current_facet_manifest",
                cancel_check=cancel_check,
            )[0][0])
            self.connection.execute("DELETE FROM current_facet_manifest")
        return FacetManifestDiff(
            added=added,
            changed=changed,
            deleted=deleted,
            unchanged=max(0, current_count - len(added) - len(changed)),
        )

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()

    def __enter__(self) -> "MaintenanceJournal":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


__all__ = [
    "AuditRequest",
    "CardManifest",
    "CardManifestDiff",
    "DirtyNote",
    "DirtyReason",
    "FacetManifest",
    "FacetManifestDiff",
    "FacetManifestKey",
    "IncompatibleMaintenanceJournal",
    "MaintenanceJournal",
    "ManifestDiff",
    "NoteManifest",
    "SCHEMA_VERSION",
    "UnsafeMaintenancePath",
]
