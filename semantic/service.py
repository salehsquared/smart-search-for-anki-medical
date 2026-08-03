"""High-level semantic indexing/search service used by the Anki integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sys
import threading
from typing import Any

from .embedder import OnnxMedicalEmbedder, SemanticRuntimeError
from .model_manager import ModelManager
from .vector_index import VectorIndex


_HASH_LOOKUP_BATCH_SIZE = 900
_SHARED_RUNTIME_LOCK = threading.Lock()
_RUNTIME_RESTART_REQUIRED = threading.Event()


@contextmanager
def _shared_runtime_access(
    cancel_check: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Serialize shared runtime installation and inference across profiles."""

    while not _SHARED_RUNTIME_LOCK.acquire(timeout=0.1):
        if cancel_check is not None:
            cancel_check()
    try:
        if cancel_check is not None:
            cancel_check()
        yield
    finally:
        _SHARED_RUNTIME_LOCK.release()


def semantic_text_hash(text: str) -> str:
    """Return the stable hash used to decide whether a note needs embedding.

    This hash intentionally covers only the text supplied to the semantic
    model.  Tags, decks, scheduling state, and other lexical metadata should
    not make an otherwise unchanged vector stale.
    """

    normalized = str(text or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
            content_hash=semantic_text_hash(normalized),
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
        self._embedder_lock = threading.RLock()
        self._embedder_condition = threading.Condition(self._embedder_lock)
        self._embedder_users = 0
        self._unload_when_idle = False
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
        cancel_check: Callable[[], None] | None = None,
    ) -> SemanticStatus:
        try:
            with _shared_runtime_access(cancel_check):
                runtime_was_imported = any(
                    name == "onnxruntime" or name.startswith("onnxruntime.")
                    for name in sys.modules
                )
                if repair and runtime_was_imported:
                    # Repair may replace native files before a later model
                    # download fails or is cancelled. Fail closed before any
                    # mutation, and retain the guard across profile switches.
                    _RUNTIME_RESTART_REQUIRED.set()
                # Do not retain this condition during extraction/download.
                # Cleanup/unload can then detach an old profile immediately,
                # while the process-wide lock keeps shared assets single-writer.
                with self._embedder_condition:
                    self._embedder = None
                    self._unload_when_idle = False
                self.manager.install_all(
                    progress,
                    repair_runtime=repair,
                    cancel_check=cancel_check,
                )
            self._last_error = None
            self._last_error_kind = None
        except Exception as error:
            self._last_error = str(error)
            self._last_error_kind = "model"
            raise
        return self.status()

    @property
    def restart_required(self) -> bool:
        """Whether repaired native files require a clean interpreter load."""

        return _RUNTIME_RESTART_REQUIRED.is_set()

    def _get_embedder(self) -> OnnxMedicalEmbedder:
        with self._embedder_lock:
            if self._embedder is None:
                self._embedder = OnnxMedicalEmbedder(self.manager)
            return self._embedder

    @contextmanager
    def _use_embedder(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> Iterator[OnnxMedicalEmbedder]:
        """Hold a logical lease so unload cannot disrupt active inference."""

        with _shared_runtime_access(cancel_check):
            with self._embedder_lock:
                embedder = self._get_embedder()
                self._embedder_users += 1
            try:
                yield embedder
            finally:
                with self._embedder_lock:
                    self._embedder_users = max(0, self._embedder_users - 1)
                    if self._embedder_users == 0 and self._unload_when_idle:
                        self._embedder = None
                        self._unload_when_idle = False
                    self._embedder_condition.notify_all()

    @property
    def model_loaded(self) -> bool:
        """Whether this service currently retains an embedding model."""

        with self._embedder_lock:
            return self._embedder is not None

    def unload(self) -> bool:
        """Best-effort release of the optional native model and tokenizer.

        ONNX Runtime does not expose a portable force-close API.  Dropping the
        final Python reference is therefore the safest supported release
        mechanism.  If inference is in flight, release is deferred until its
        lease exits instead of invalidating a live native session.

        Returns ``True`` when a loaded model existed (including one whose
        release was deferred), and ``False`` when there was nothing to release.
        """

        with self._embedder_lock:
            if self._embedder is None:
                return False
            if self._embedder_users:
                self._unload_when_idle = True
            else:
                self._embedder = None
                self._unload_when_idle = False
            return True

    def warmup(self) -> None:
        """Load the local runtime and run one tiny inference off the UI thread."""

        try:
            with self._use_embedder() as embedder:
                embedder.embed(["medical semantic search"])
            self._last_error = None
            self._last_error_kind = None
        except Exception as error:
            self._last_error = str(error)
            self._last_error_kind = "model"
            raise

    def iter_document_freshness(
        self,
        documents: Iterable[SemanticDocument],
        *,
        batch_size: int = _HASH_LOOKUP_BATCH_SIZE,
        cancel_check: Callable[[], None] | None = None,
    ) -> Iterator[tuple[SemanticDocument, bool]]:
        """Yield documents with per-note vector freshness in bounded batches."""

        for batch in _document_batches(documents, batch_size):
            if cancel_check is not None:
                cancel_check()
            known = self.index.known_hashes(
                [document.note_id for document in batch],
                cancel_check=cancel_check,
            )
            for document in batch:
                yield document, (
                    known.get(document.note_id) == document.content_hash
                )
        if cancel_check is not None:
            cancel_check()

    def stale_documents(
        self,
        documents: Iterable[SemanticDocument],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[SemanticDocument]:
        return [
            document
            for document, current in self.iter_document_freshness(
                documents,
                cancel_check=cancel_check,
            )
            if not current
        ]

    def index_documents(
        self,
        documents: Sequence[SemanticDocument],
        *,
        batch_size: int = 16,
        progress: Callable[[int, int], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> int:
        try:
            # Determine the exact progress denominator without retaining a
            # second full-size list of SemanticDocument objects.  Documents is
            # a Sequence, so the inexpensive hash pass can be repeated safely.
            stale_count = sum(
                1
                for _document, current in self.iter_document_freshness(
                    documents,
                    cancel_check=cancel_check,
                )
                if not current
            )
            if not stale_count:
                self._last_error = None
                self._last_error_kind = None
                return 0

            embed_batch_size = max(1, int(batch_size))
            indexed = 0
            pending: list[SemanticDocument] = []
            with self._use_embedder(cancel_check) as embedder:
                for document, current in self.iter_document_freshness(
                    documents,
                    cancel_check=cancel_check,
                ):
                    if current:
                        continue
                    pending.append(document)
                    if len(pending) < embed_batch_size:
                        continue
                    indexed += self._index_batch(
                        embedder,
                        pending,
                        cancel_check=cancel_check,
                    )
                    pending.clear()
                    if progress:
                        progress(indexed, stale_count)
                if pending:
                    indexed += self._index_batch(
                        embedder,
                        pending,
                        cancel_check=cancel_check,
                    )
                    if progress:
                        progress(indexed, stale_count)
            self._last_error = None
            self._last_error_kind = None
            return indexed
        except Exception as error:
            # The caller owns the cancellation exception type. Re-run its
            # checkpoint so an expected reviewer/profile cancellation is not
            # persisted as model/index damage.
            if cancel_check is not None:
                cancel_check()
            self._last_error = str(error)
            self._last_error_kind = (
                "model" if isinstance(error, SemanticRuntimeError) else "index"
            )
            raise

    def _index_batch(
        self,
        embedder: OnnxMedicalEmbedder,
        documents: Sequence[SemanticDocument],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> int:
        vectors = embedder.embed(
            [document.text for document in documents],
            cancel_check=cancel_check,
        )
        if cancel_check is not None:
            cancel_check()
        self.index.upsert_many(
            [document.note_id for document in documents],
            [document.content_hash for document in documents],
            vectors,
        )
        if cancel_check is not None:
            cancel_check()
        return len(documents)

    def remove_notes(
        self,
        note_ids: Sequence[int],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        try:
            self.index.delete(note_ids, cancel_check=cancel_check)
        except Exception as error:
            if cancel_check is not None:
                cancel_check()
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
            with self._use_embedder(cancel_check) as embedder:
                vector = embedder.embed(
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


def _document_batches(
    documents: Iterable[SemanticDocument], batch_size: int
) -> Iterator[list[SemanticDocument]]:
    size = max(1, min(_HASH_LOOKUP_BATCH_SIZE, int(batch_size)))
    batch: list[SemanticDocument] = []
    for document in documents:
        batch.append(document)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
