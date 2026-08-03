"""High-level semantic indexing/search service used by the Anki integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading
from typing import Any

from .errors import SemanticRuntimeError, SemanticWorkerError
from .model_manager import ModelManager
from .vector_index import VectorIndex
from .worker_client import SemanticWorkerClient


_HASH_LOOKUP_BATCH_SIZE = 900
_SHARED_RUNTIME_LOCK = threading.Lock()


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
        self._worker = SemanticWorkerClient(self.manager, Path(bundle_root))
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
                # Native files live only in the helper, so a repair is safe as
                # soon as that disposable process has been terminated.
                self._worker.terminate_and_wait()
                self.manager.install_all(
                    progress,
                    repair_runtime=repair,
                    cancel_check=cancel_check,
                )
            self._last_error = None
            self._last_error_kind = None
        except Exception as error:
            if cancel_check is not None:
                cancel_check()
            self._last_error = str(error)
            self._last_error_kind = "model"
            raise
        return self.status()

    def install_runtime(
        self,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> SemanticStatus:
        """Install only bundled local worker files during a version upgrade."""

        try:
            with _shared_runtime_access(cancel_check):
                self._worker.terminate_and_wait()
                self.manager.install_runtime(cancel_check=cancel_check)
            self._last_error = None
            self._last_error_kind = None
        except Exception as error:
            if cancel_check is not None:
                cancel_check()
            self._last_error = str(error)
            self._last_error_kind = "model"
            raise
        return self.status()

    @property
    def restart_required(self) -> bool:
        """Worker repairs never require restarting the Anki interpreter."""

        return False

    @property
    def model_loaded(self) -> bool:
        """Whether this service currently owns a live helper process."""

        return self._worker.running

    @property
    def last_error_kind(self) -> str | None:
        """Return the latest cheap recovery hint without touching disk."""

        return self._last_error_kind

    def unload(self) -> bool:
        """Terminate the helper so macOS reclaims all native model memory."""

        return self._worker.terminate_now()

    abort_now = unload

    def close(self) -> bool:
        """Synchronously reap the helper before profile files are retired."""

        return self._worker.terminate_and_wait()

    def warmup(self) -> None:
        """Load the local runtime and run one tiny inference off the UI thread."""

        try:
            with _shared_runtime_access():
                self._worker.warmup()
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
            with _shared_runtime_access(cancel_check):
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
                        pending,
                        cancel_check=cancel_check,
                    )
                    pending.clear()
                    if progress:
                        progress(indexed, stale_count)
                if pending:
                    indexed += self._index_batch(
                        pending,
                        cancel_check=cancel_check,
                    )
                    if progress:
                        progress(indexed, stale_count)
            self._last_error = None
            self._last_error_kind = None
            return indexed
        except SemanticWorkerError:
            # A killed/crashed helper is disposable. Reviewer cancellation is
            # re-raised by the caller's checkpoint; any other transient child
            # failure can be retried without falsely declaring the verified
            # model or index corrupt.
            if cancel_check is not None:
                cancel_check()
            self._last_error = None
            self._last_error_kind = None
            raise
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
        documents: Sequence[SemanticDocument],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> int:
        vectors = self._worker.embed(
            [document.text for document in documents],
            background=True,
            cancel_check=cancel_check,
        )
        if cancel_check is not None:
            cancel_check()
        self.manager.activate_vector_runtime()
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
            with _shared_runtime_access(cancel_check):
                vector = self._worker.embed(
                    [query],
                    background=False,
                    cancel_check=cancel_check,
                )[0]
            self.manager.activate_vector_runtime()
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
        except SemanticWorkerError:
            # A child crash, lifecycle termination, or protocol fault does not
            # imply that the verified local assets are corrupt. Surface one
            # transient query warning and let the next request start cleanly.
            if cancel_check is not None:
                cancel_check()
            self._last_error = None
            self._last_error_kind = None
            raise
        except SemanticRuntimeError as error:
            if cancel_check is not None:
                cancel_check()
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
