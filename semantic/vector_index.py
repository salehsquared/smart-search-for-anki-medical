"""A compact, disposable float16 vector index keyed by Anki note ID."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from typing import Any

from .manifest import MODEL_DIMENSION, MODEL_NAME


SCHEMA_VERSION = 1
GROWTH_SLOTS = 1024
_SOURCE_GENERATION_KEY = "source_generation"


@dataclass(frozen=True)
class VectorHit:
    note_id: int
    score: float


class VectorIndex:
    def __init__(self, root: Path, dimension: int = MODEL_DIMENSION) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "semantic.sqlite3"
        self.vector_path = self.root / "vectors.f16"
        self.dimension = dimension
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterable[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=NORMAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        capacity = 0
        with self._connect() as connection:
            # WAL is persistent database metadata.  Set it once while opening
            # the disposable index instead of renegotiating journal mode on
            # every read-only status/count connection.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vectors (
                    note_id INTEGER PRIMARY KEY,
                    slot INTEGER NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS free_slots (
                    slot INTEGER PRIMARY KEY
                );
                """
            )
            existing = dict(connection.execute("SELECT key, value FROM meta"))
            expected = {
                "schema_version": str(SCHEMA_VERSION),
                "dimension": str(self.dimension),
                "model_name": MODEL_NAME,
            }
            if existing and any(existing.get(key) != value for key, value in expected.items()):
                raise RuntimeError(
                    "The semantic vector index is incompatible and should be rebuilt."
                )
            for key, value in expected.items():
                connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value)
                )
            connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('capacity', '0')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('next_slot', '0')"
            )
            capacity_row = connection.execute(
                "SELECT value FROM meta WHERE key = 'capacity'"
            ).fetchone()
            capacity = _nonnegative_metadata_int(
                "capacity", capacity_row[0] if capacity_row else None
            )
            next_slot_row = connection.execute(
                "SELECT value FROM meta WHERE key = 'next_slot'"
            ).fetchone()
            next_slot = _nonnegative_metadata_int(
                "next_slot", next_slot_row[0] if next_slot_row else None
            )
            if next_slot > capacity:
                raise RuntimeError(
                    "The semantic vector index has invalid next_slot metadata "
                    "and should be rebuilt."
                )
            source_row = connection.execute(
                "SELECT value FROM meta WHERE key = ?", (_SOURCE_GENERATION_KEY,)
            ).fetchone()
            if source_row is not None:
                _nonnegative_metadata_int(_SOURCE_GENERATION_KEY, source_row[0])
            minimum_slot, maximum_slot = connection.execute(
                "SELECT min(slot), max(slot) FROM vectors"
            ).fetchone()
            if (
                minimum_slot is not None
                and (
                    int(minimum_slot) < 0
                    or int(maximum_slot) >= next_slot
                )
            ):
                raise RuntimeError(
                    "The semantic vector index has invalid occupied slots "
                    "and should be rebuilt."
                )
            minimum_free, maximum_free = connection.execute(
                "SELECT min(slot), max(slot) FROM free_slots"
            ).fetchone()
            if (
                minimum_free is not None
                and (
                    int(minimum_free) < 0
                    or int(maximum_free) >= next_slot
                )
            ):
                raise RuntimeError(
                    "The semantic vector index has invalid free slots "
                    "and should be rebuilt."
                )
            overlap = connection.execute(
                """
                SELECT 1
                FROM free_slots AS free
                INNER JOIN vectors AS occupied ON occupied.slot = free.slot
                LIMIT 1
                """
            ).fetchone()
            if overlap is not None:
                raise RuntimeError(
                    "The semantic vector index has overlapping occupied and free slots "
                    "and should be rebuilt."
                )

        expected_bytes = capacity * self.dimension * 2
        if not self.vector_path.exists():
            if capacity:
                raise RuntimeError(
                    "The semantic vector file is missing and should be rebuilt."
                )
            self.vector_path.touch()
        actual_bytes = self.vector_path.stat().st_size
        if actual_bytes != expected_bytes:
            raise RuntimeError(
                "The semantic vector file size does not match its capacity "
                "and should be rebuilt."
            )

    @staticmethod
    def _np() -> Any:
        try:
            import numpy as np
        except Exception as error:
            raise RuntimeError("NumPy is required for semantic vector search.") from error
        return np

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT count(*) FROM vectors").fetchone()[0])

    @property
    def source_generation(self) -> int | None:
        """Return the lexical-index generation this vector set fully represents.

        Absence is intentional: it means a semantic pass has never completed,
        or a later vector mutation began but did not finish.  Keeping this
        completion marker in the semantic SQLite database makes that dirty
        state survive an Anki restart.
        """

        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = ?", (_SOURCE_GENERATION_KEY,)
            ).fetchone()
        return (
            _nonnegative_metadata_int(_SOURCE_GENERATION_KEY, row[0])
            if row
            else None
        )

    def clear_source_generation(self) -> None:
        """Persistently mark the vector set incomplete before changing it."""

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM meta WHERE key = ?", (_SOURCE_GENERATION_KEY,)
            )

    def mark_source_generation(self, generation: int) -> None:
        """Record a successfully completed lexical source generation."""

        normalized = int(generation)
        if normalized < 0:
            raise ValueError("source generation must be non-negative")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                (_SOURCE_GENERATION_KEY, str(normalized)),
            )

    def content_hash(self, note_id: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content_hash FROM vectors WHERE note_id = ?", (note_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def known_hashes(self, note_ids: Sequence[int] | None = None) -> dict[int, str]:
        with self._connect() as connection:
            if not note_ids:
                rows = connection.execute("SELECT note_id, content_hash FROM vectors")
                return {int(row[0]): str(row[1]) for row in rows}

            # A full collection can exceed SQLite's conservative host
            # parameter limit, so resolve IDs in portable chunks.
            output: dict[int, str] = {}
            normalized = tuple(dict.fromkeys(int(note_id) for note_id in note_ids))
            for start in range(0, len(normalized), 900):
                chunk = normalized[start : start + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT note_id, content_hash FROM vectors "
                    f"WHERE note_id IN ({placeholders})",
                    chunk,
                )
                output.update((int(row[0]), str(row[1])) for row in rows)
            return output

    def upsert_many(
        self,
        note_ids: Sequence[int],
        content_hashes: Sequence[str],
        embeddings: Any,
    ) -> None:
        np = self._np()
        matrix = np.asarray(embeddings, dtype=np.float32)
        if matrix.shape != (len(note_ids), self.dimension):
            raise ValueError(
                f"Expected embeddings shaped ({len(note_ids)}, {self.dimension}), "
                f"got {matrix.shape}"
            )
        if len(note_ids) != len(content_hashes):
            raise ValueError("note_ids and content_hashes must have equal lengths")
        if not note_ids:
            return

        # Commit the dirty marker separately before any mmap or metadata
        # mutation.  If a process exits or an embedding/write fails midway,
        # reopening the index must not mistake the old completion marker for a
        # fully refreshed vector set.
        self.clear_source_generation()
        with self._connect() as connection:
            capacity = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'capacity'"
                ).fetchone()[0]
            )
            next_slot = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'next_slot'"
                ).fetchone()[0]
            )
            existing = {
                int(row["note_id"]): int(row["slot"])
                for row in connection.execute(
                    "SELECT note_id, slot FROM vectors WHERE note_id IN ({})".format(
                        ",".join("?" for _ in note_ids)
                    ),
                    tuple(int(note_id) for note_id in note_ids),
                )
            }
            free = [
                int(row[0])
                for row in connection.execute(
                    "SELECT slot FROM free_slots ORDER BY slot LIMIT ?", (len(note_ids),)
                )
            ]
            assigned: list[int] = []
            for note_id in note_ids:
                note_id = int(note_id)
                if note_id in existing:
                    assigned.append(existing[note_id])
                elif free:
                    slot = free.pop(0)
                    connection.execute("DELETE FROM free_slots WHERE slot = ?", (slot,))
                    assigned.append(slot)
                else:
                    assigned.append(next_slot)
                    next_slot += 1

            required = max(assigned) + 1
            if required > capacity:
                capacity = ((required + GROWTH_SLOTS - 1) // GROWTH_SLOTS) * GROWTH_SLOTS
                with self.vector_path.open("r+b") as handle:
                    handle.truncate(capacity * self.dimension * 2)
                connection.execute(
                    "UPDATE meta SET value = ? WHERE key = 'capacity'", (str(capacity),)
                )
            connection.execute(
                "UPDATE meta SET value = ? WHERE key = 'next_slot'", (str(next_slot),)
            )

            mmap = np.memmap(
                self.vector_path,
                dtype="<f2",
                mode="r+",
                shape=(capacity, self.dimension),
            )
            now = int(time.time())
            for row_index, (note_id, content_hash, slot) in enumerate(
                zip(note_ids, content_hashes, assigned)
            ):
                vector = matrix[row_index]
                norm = float(np.linalg.norm(vector))
                if norm <= 1e-12:
                    raise ValueError(f"Embedding for note {note_id} has zero norm")
                mmap[slot] = (vector / norm).astype(np.float16)
                connection.execute(
                    """
                    INSERT INTO vectors(note_id, slot, content_hash, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(note_id) DO UPDATE SET
                        slot=excluded.slot,
                        content_hash=excluded.content_hash,
                        updated_at=excluded.updated_at
                    """,
                    (int(note_id), int(slot), str(content_hash), now),
                )
            mmap.flush()
            del mmap

    def delete(self, note_ids: Sequence[int]) -> None:
        if not note_ids:
            return
        self.clear_source_generation()
        with self._connect() as connection:
            normalized = tuple(dict.fromkeys(int(note_id) for note_id in note_ids))
            for start in range(0, len(normalized), 900):
                chunk = normalized[start : start + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT slot FROM vectors WHERE note_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                connection.execute(
                    f"DELETE FROM vectors WHERE note_id IN ({placeholders})",
                    chunk,
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO free_slots(slot) VALUES (?)",
                    [(int(row[0]),) for row in rows],
                )

    def search(
        self,
        query_vector: Any,
        *,
        limit: int = 50,
        allowed_note_ids: set[int] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[VectorHit]:
        np = self._np()
        if cancel_check is not None:
            cancel_check()
        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if query.shape != (self.dimension,):
            raise ValueError(
                f"Expected a {self.dimension}-element query vector, got {query.shape}"
            )
        norm = float(np.linalg.norm(query))
        if norm <= 1e-12:
            return []
        query = query / norm

        with self._connect() as connection:
            capacity = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key = 'capacity'"
                ).fetchone()[0]
            )
            rows = connection.execute("SELECT note_id, slot FROM vectors").fetchall()
        if not rows or capacity <= 0:
            return []

        selected = [
            (int(row["note_id"]), int(row["slot"]))
            for row in rows
            if allowed_note_ids is None or int(row["note_id"]) in allowed_note_ids
        ]
        if not selected:
            return []

        mmap = np.memmap(
            self.vector_path,
            dtype="<f2",
            mode="r",
            shape=(capacity, self.dimension),
        )
        heap_note_ids: list[int] = []
        heap_scores: list[float] = []
        for start in range(0, len(selected), 4096):
            if cancel_check is not None:
                cancel_check()
            chunk = selected[start : start + 4096]
            slots = np.asarray([slot for _, slot in chunk], dtype=np.int64)
            matrix = np.asarray(mmap[slots], dtype=np.float32)
            scores = matrix @ query
            heap_note_ids.extend(note_id for note_id, _ in chunk)
            heap_scores.extend(float(score) for score in scores)
        del mmap

        count = min(max(0, int(limit)), len(heap_scores))
        if count == 0:
            return []
        scores_array = np.asarray(heap_scores, dtype=np.float32)
        note_ids_array = np.asarray(heap_note_ids, dtype=np.int64)
        if count < len(heap_scores):
            selected_indices = np.argpartition(scores_array, -count)[-count:]
        else:
            selected_indices = np.arange(len(heap_scores), dtype=np.int64)
        # Sort only the retained candidates.  Lexsort makes ties deterministic
        # by note ID and avoids a Python O(n log n) sort over the whole index.
        local_order = np.lexsort(
            (
                note_ids_array[selected_indices],
                -scores_array[selected_indices],
            )
        )
        order = selected_indices[local_order]
        if cancel_check is not None:
            cancel_check()
        return [
            VectorHit(
                note_id=int(note_ids_array[index]),
                score=float(scores_array[index]),
            )
            for index in order
        ]


def _nonnegative_metadata_int(key: str, value: object) -> int:
    try:
        normalized = int(str(value))
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            f"The semantic vector index has invalid {key} metadata "
            "and should be rebuilt."
        ) from error
    if normalized < 0:
        raise RuntimeError(
            f"The semantic vector index has invalid {key} metadata "
            "and should be rebuilt."
        )
    return normalized
