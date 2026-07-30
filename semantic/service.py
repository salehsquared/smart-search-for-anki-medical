"""High-level semantic indexing/search service used by the Anki integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading
from typing import Any

from .embedder import OnnxMedicalEmbedder, SemanticRuntimeError
from .model_manager import ModelManager
from .vector_index import VectorIndex


@dataclass(frozen=True)
class SemanticDocument:
    note_id: int
    text: str
    content_hash: str

    @classmethod
    def from_text(cls, note_id: int, text: str) -> "SemanticDocument":
        normalized = str(text or "")
        return cls(
            note_id=int(note_id),
            text=normalized,
            content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class SemanticHit:
    note_id: int
    score: float
    reason: str = "Semantic match"


@dataclass(frozen=True)
class SemanticStatus:
    supported: bool
    runtime_ready: bool
    model_ready: bool
    index_count: int
    error: str | None = None
    error_kind: str | None = None

    @property
    def ready(self) -> bool:
        return self.runtime_ready and self.model_ready and self.error is None


class SemanticService:
    def __init__(
        self,
        data_root: Path,
        bundle_root: Path,
        profile_key: str,
    ) -> None:
        self.manager = ModelManager(data_root=Path(data_root), bundle_root=Path(bundle_root))
        self.index = VectorIndex(Path(data_root) / "profiles" / profile_key / "vectors")
        self._embedder: OnnxMedicalEmbedder | None = None
        self._embedder_lock = threading.Lock()
        self._last_error: str | None = None
        self._last_error_kind: str | None = None

    def status(self) -> SemanticStatus:
        return SemanticStatus(
            supported=self.manager.runtime_supported(),
            runtime_ready=self.manager.runtime_ready(),
            model_ready=self.manager.model_ready(),
            index_count=self.index.count(),
            error=self._last_error,
            error_kind=self._last_error_kind,
        )

    def install(
        self,
        progress: Callable[[str, int, int], None] | None = None,
        *,
        repair: bool = False,
    ) -> SemanticStatus:
        try:
            with self._embedder_lock:
                self.manager.install_all(progress, repair_runtime=repair)
                self._embedder = None
            self._last_error = None
            self._last_error_kind = None
        except Exception as error:
            self._last_error = str(error)
            self._last_error_kind = "model"
            raise
        return self.status()

    def _get_embedder(self) -> OnnxMedicalEmbedder:
        with self._embedder_lock:
            if self._embedder is None:
                self._embedder = OnnxMedicalEmbedder(self.manager)
            return self._embedder

    def warmup(self) -> None:
        """Load the local runtime and run one tiny inference off the UI thread."""

        try:
            self._get_embedder().embed(["medical semantic search"])
            self._last_error = None
            self._last_error_kind = None
        except Exception as error:
            self._last_error = str(error)
            self._last_error_kind = "model"
            raise

    def stale_documents(
        self, documents: Iterable[SemanticDocument]
    ) -> list[SemanticDocument]:
        docs = list(documents)
        known = self.index.known_hashes([document.note_id for document in docs])
        return [
            document
            for document in docs
            if known.get(document.note_id) != document.content_hash
        ]

    def index_documents(
        self,
        documents: Sequence[SemanticDocument],
        *,
        batch_size: int = 16,
        progress: Callable[[int, int], None] | None = None,
    ) -> int:
        try:
            stale = self.stale_documents(documents)
            if not stale:
                self._last_error = None
                self._last_error_kind = None
                return 0
            embedder = self._get_embedder()
            total = len(stale)
            indexed = 0
            for start in range(0, total, max(1, batch_size)):
                batch = stale[start : start + batch_size]
                vectors = embedder.embed([document.text for document in batch])
                self.index.upsert_many(
                    [document.note_id for document in batch],
                    [document.content_hash for document in batch],
                    vectors,
                )
                indexed += len(batch)
                if progress:
                    progress(indexed, total)
            self._last_error = None
            self._last_error_kind = None
            return indexed
        except Exception as error:
            self._last_error = str(error)
            self._last_error_kind = (
                "model" if isinstance(error, SemanticRuntimeError) else "index"
            )
            raise

    def remove_notes(self, note_ids: Sequence[int]) -> None:
        try:
            self.index.delete(note_ids)
        except Exception as error:
            self._last_error = str(error)
            self._last_error_kind = "index"
            raise

    def search(
        self,
        query: str,
        *,
        limit: int = 50,
        allowed_note_ids: set[int] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[SemanticHit]:
        # Do not initialize the 100+ MB native runtime on ordinary Smart
        # searches until at least one note has actually been embedded.
        if not query.strip() or self.index.count() == 0:
            return []
        try:
            if cancel_check is not None:
                cancel_check()
            vector = self._get_embedder().embed(
                [query],
                cancel_check=cancel_check,
            )[0]
            hits = self.index.search(
                vector,
                limit=limit,
                allowed_note_ids=allowed_note_ids,
                cancel_check=cancel_check,
            )
            self._last_error = None
            self._last_error_kind = None
            return [
                SemanticHit(note_id=hit.note_id, score=hit.score)
                for hit in hits
            ]
        except SemanticRuntimeError as error:
            self._last_error = str(error)
            self._last_error_kind = "model"
            return []
        except Exception as error:
            # Cancellation uses the caller's private exception type.  Re-run
            # its checkpoint so it propagates instead of being mislabeled as a
            # damaged semantic index.
            if cancel_check is not None:
                cancel_check()
            self._last_error = str(error)
            self._last_error_kind = "index"
            return []
