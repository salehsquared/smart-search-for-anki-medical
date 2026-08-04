"""Safe host integration for Smart Search inside Anki 26.5.

The module has two deliberately separate layers:

``AnkiSearchBackend``
    Implements the UI's asynchronous ``SearchBackend`` protocol.  It owns the
    profile-scoped, disposable FTS/vector indexes and is the only object that
    schedules collection reads through :class:`aqt.operations.QueryOp`.

``SmartSearchAddonController``
    Owns Anki hooks, the search dialog, and the small set of user-requested
    collection actions. Importing this module outside Anki is safe; all
    ``aqt``/Qt imports are delayed until a host action is used.

Search/index access remains read-only. Flag, suspension, and tag changes are
delegated to :mod:`anki_actions`, which uses one official undoable
``CollectionOp`` per click and never writes SQLite directly. All index writes
target files below this add-on's ``user_files`` directory.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any

from .backend.compat import dataclass, optional_hook

from .anki_actions import (
    ActionKind,
    CollectionAction,
    UndoToken,
    UndoUnavailable,
    format_action_message,
    result_ids,
    start_collection_action,
    start_guarded_undo,
)
from .anki_adapter import (
    AnkiCollectionReader,
    open_card_ids_in_browser,
    open_native_query_in_browser,
    open_note_ids_in_browser,
    preview_card_ids,
    profile_key,
)
from .inline_preview import create_inline_result_inspector
from .backend.anki_reader import iter_collection_notes
from .backend.index import (
    FTS5Unavailable,
    SmartSearchIndex,
)
from .backend.maintenance import (
    AuditRequest,
    DirtyNote,
    DirtyReason,
    FacetManifest,
    MaintenanceJournal,
    ManifestDiff,
)
from .backend.models import (
    AliasExpansion as BackendAliasExpansion,
    Correction as BackendCorrection,
    IndexedNote,
    SearchResponse as BackendSearchResponse,
)
from .backend.query import (
    DEFAULT_FILTER_KEYS,
    QueryParser,
    strip_incomplete_filter_tokens,
)
from .backend.search import SearchEngine
from .backend.text import normalize_text, strip_html_and_cloze, tokenize
from .semantic.service import SemanticDocument, SemanticService
from .ui.contracts import (
    AboutInfo,
    CardState,
    Correction,
    DeckCatalog,
    DeckCatalogCallback,
    FilterChip,
    HighlightSpan,
    IndexState,
    IndexStatus,
    MatchKind,
    PreviewDefault,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SemanticRecovery,
    SemanticState,
    SemanticStatus,
    UISettings,
)


ProgressCallback = Callable[[float, str], None]
StatusCallback = Callable[[IndexStatus], None]
ErrorCallback = Callable[[str], None]
VoidCallback = Callable[[], None]

_DEFAULT_RESULT_LIMIT = 50
_DEFAULT_DEBOUNCE_MS = 1200
_DEFAULT_SEMANTIC_AUTOSTART_DELAY_MS = 20_000
_SEMANTIC_AUTOSTART_RETRY_MS = 2_000
_POST_REVIEW_MAINTENANCE_DELAY_MS = 5_000
# Keep the isolated worker warm across an ordinary burst of searches. It lives
# outside Anki and is still terminated immediately when the search window
# closes, review begins, or the profile is retired.
_SEMANTIC_IDLE_UNLOAD_MS = 90_000
_SEMANTIC_DELTA_BATCH_SIZE = 250
_RXTERMS_RESOURCE = Path("resources") / "medical_vocab" / "rxterms_202607.json.gz"
_DIALOG_MANAGER_NAME = "SmartSearchMedical"
_NOTETYPE_FILTER = re.compile(
    r"(?<![\w\"])(?P<neg>-?)notetype:(?P<value>\"(?:\\.|[^\"])*\"|\S+)",
    flags=re.IGNORECASE,
)


class _CancelledOperation(RuntimeError):
    pass


class _BundleUpdatePending(RuntimeError):
    pass


@dataclass(slots=True)
class _ProfileContext:
    token: int
    key: str
    profile_name: str
    collection_path: str
    index: SmartSearchIndex
    engine: SearchEngine
    semantic: SemanticService | None
    maintenance: MaintenanceJournal | None = None
    note_count: int = 0
    semantic_needs_reconcile: bool = False
    semantic_error: str | None = None
    semantic_error_recovery: SemanticRecovery | None = None
    semantic_snapshot: Any | None = None
    semantic_source_generation: int | None = None
    fingerprint: str | None = None
    lexical_generation: int = 0
    field_names: tuple[str, ...] = ()
    semantic_pending_note_ids: set[int] = field(default_factory=set)
    semantic_pending_deleted_ids: set[int] = field(default_factory=set)
    # Exact note IDs can bridge several successive lexical generations, but
    # only when the chain began from a vector generation known to be current.
    # Unknown/bulk changes deliberately invalidate that chain and request one
    # canonical full refresh after lexical maintenance drains.
    semantic_exact_delta_chain: bool = False
    semantic_force_full_refresh: bool = False
    semantic_restart_required: bool = False


@dataclass(frozen=True, slots=True)
class _OwnedMutation:
    """Exact scope carried through Anki's operation initiator hook."""

    profile_token: int
    action: CollectionAction


@dataclass(frozen=True, slots=True)
class _OwnedUndo:
    """Undo of one exact Smart Search mutation."""

    profile_token: int
    action: CollectionAction


@dataclass(frozen=True, slots=True)
class _PendingUndo:
    """The exact native undo step currently offered by the search window."""

    profile_token: int
    token: UndoToken
    action: CollectionAction


def _noop(*_args: object, **_kwargs: object) -> None:
    return None


def _positive_ids(values: Iterable[Any]) -> tuple[int, ...]:
    output: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in seen:
            output.append(item)
            seen.add(item)
    return tuple(output)


def _object_id(value: Any) -> int:
    try:
        candidate = getattr(value, "id", 0)
        if callable(candidate):
            candidate = candidate()
        return max(0, int(candidate or 0))
    except (TypeError, ValueError):
        return 0


def _host_state_name(value: Any) -> str:
    """Normalize Anki state strings/enums across supported host versions."""

    candidate = getattr(value, "value", value)
    text = str(candidate or "").strip().casefold()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _handler_note_id(handler: object | None) -> int:
    """Best-effort committed editor target without depending on private SQL."""

    if handler is None:
        return 0
    try:
        direct = int(getattr(handler, "nid", 0) or 0)
    except (TypeError, ValueError):
        direct = 0
    if direct > 0:
        return direct
    return _object_id(getattr(handler, "note", None))


class AnkiSearchBackend:
    """Profile-aware implementation of :class:`ui.contracts.SearchBackend`.

    Call :meth:`activate_profile` from ``profile_did_open`` and
    :meth:`deactivate_profile` from ``profile_will_close``.  Searches,
    snapshots, and maintenance are asynchronous and callbacks may originate
    from a worker thread, matching the UI protocol.
    """

    def __init__(
        self,
        mw: Any,
        *,
        bundle_root: str | Path | None = None,
        addon_module: str | None = None,
        reader: AnkiCollectionReader | None = None,
    ) -> None:
        self.mw = mw
        self.bundle_root = Path(bundle_root or Path(__file__).resolve().parent)
        self.data_root = self.bundle_root / "user_files"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.addon_module = addon_module or (__package__ or __name__).split(".")[0]
        self.reader = reader or AnkiCollectionReader()

        self._context: _ProfileContext | None = None
        self._profile_token = 0
        self._state_lock = threading.RLock()
        self._engine_lock = threading.RLock()
        self._search_lock = threading.Lock()
        self._active_cancellations: set[threading.Event] = set()
        self._maintenance_cancellations: set[threading.Event] = set()
        self._semantic_cancellations: set[threading.Event] = set()
        self._journal_writes_inflight: dict[int, int] = {}
        self._background_op_count = 0
        self._bundle_update_running = False
        self._background_maintenance_paused = False
        self._maintenance_running = False
        self._vocabulary_refresh_running = False
        self._vocabulary_refresh_token: int | None = None

        self._index_state = IndexState.UNAVAILABLE
        self._index_progress: float | None = None
        self._index_detail = "Open an Anki profile to initialize Smart Search."
        self._semantic_phase: str | None = None
        self._semantic_progress: float | None = None
        self._semantic_phase_detail = ""
        self._semantic_enabled = True
        self._auto_semantic_index = True
        self._semantic_activity_callback: Callable[[bool], None] = _noop
        self._semantic_cancelled_callback: Callable[[], None] = _noop

    # ------------------------------------------------------------------
    # Profile lifecycle

    @property
    def active(self) -> bool:
        return self._context is not None

    @property
    def profile_id(self) -> str | None:
        return self._context.key if self._context else None

    @property
    def bundle_update_running(self) -> bool:
        """Whether Anki has exclusive access to replace this add-on bundle."""

        with self._state_lock:
            return self._bundle_update_running

    def set_semantic_activity_callback(
        self, callback: Callable[[bool], None] | None
    ) -> None:
        """Notify host UI when Semantic inference starts or becomes idle."""

        self._semantic_activity_callback = callback or _noop

    def set_semantic_cancelled_callback(
        self, callback: Callable[[], None] | None
    ) -> None:
        """Notify the host when canceled model maintenance has fully unwound."""

        self._semantic_cancelled_callback = callback or _noop

    def begin_bundle_update(self) -> bool:
        """Claim an exclusive, fail-closed lane for Anki's native updater.

        Every QueryOp is counted until its completion callback has fully
        returned.  Combining that count with the operation phase flags under
        one lock prevents a writer from slipping in between the idle check and
        the update request.
        """

        with self._state_lock:
            if (
                self._bundle_update_running
                or self._background_op_count
                or self._active_cancellations
                or self._maintenance_running
                or self._vocabulary_refresh_running
                or self._semantic_phase is not None
            ):
                return False
            self._bundle_update_running = True
            return True

    def finish_bundle_update(self) -> None:
        """Release a check that did not replace the on-disk bundle."""

        with self._state_lock:
            self._bundle_update_running = False

    def quiesce_for_bundle_update(
        self,
        *,
        on_ready: VoidCallback,
        on_error: ErrorCallback,
    ) -> None:
        """Close profile-owned files before Anki replaces the add-on folder."""

        with self._state_lock:
            if not self._bundle_update_running:
                on_error("The Smart Search update lane is not active.")
                return
            if self._background_op_count or self._active_cancellations:
                on_error("Smart Search is still finishing background work.")
                return
            self._profile_token += 1
            context = self._context
            self._context = None
        self._set_index_state(
            IndexState.UNAVAILABLE,
            detail="Updating Smart Search. Restart Anki when prompted.",
        )
        if context is None:
            on_ready()
            return

        self._run_query_op(
            uses_collection=False,
            op=lambda _collection: self._close_profile_context(context),
            success=lambda _result: on_ready(),
            failure=lambda error: on_error(str(error)),
            allow_during_bundle_update=True,
        )

    def activate_profile(self, *, auto_rebuild: bool = True) -> IndexStatus:
        """Open the profile's external indexes without touching collection data."""

        setup = self._begin_profile_activation()
        if setup is None:
            return self.get_status()
        token, profile_name, collection_path, key = setup
        try:
            context, alias_error = self._open_profile_context(
                token,
                profile_name,
                collection_path,
                key,
            )
        except Exception:
            self._set_index_state(
                IndexState.ERROR,
                detail="Search data could not be opened. Try refreshing it.",
            )
            return self.get_status()
        return self._publish_profile_context(
            context,
            alias_error,
            auto_rebuild=auto_rebuild,
        )

    def activate_profile_async(
        self,
        *,
        auto_rebuild: bool = True,
        on_ready: StatusCallback = _noop,
        on_error: ErrorCallback = _noop,
    ) -> IndexStatus:
        """Open disposable search databases on Anki's generic worker pool."""

        setup = self._begin_profile_activation()
        if setup is None:
            status = self.get_status()
            on_ready(status)
            return status
        token, profile_name, collection_path, key = setup
        event = self._new_cancellation(kind="maintenance")
        self._set_index_state(
            IndexState.BUILDING,
            detail="Opening search data.",
        )

        def opened(payload: tuple[_ProfileContext, str | None]) -> None:
            cancelled = event.is_set() or self.background_maintenance_paused
            self._forget_cancellation(event)
            context, alias_error = payload
            if token != self._profile_token:
                self._close_context_in_background(context)
                return
            if cancelled:
                self._close_context_in_background(context)
                self._set_index_state(
                    IndexState.UNAVAILABLE,
                    detail="Search setup will resume after reviewing.",
                )
                on_ready(self.get_status())
                return
            status = self._publish_profile_context(
                context,
                alias_error,
                auto_rebuild=auto_rebuild,
            )
            on_ready(status)

        def failed(error: Exception) -> None:
            self._forget_cancellation(event)
            if token != self._profile_token:
                return
            if isinstance(error, _CancelledOperation) or event.is_set():
                self._set_index_state(
                    IndexState.UNAVAILABLE,
                    detail="Search setup will resume after reviewing.",
                )
                on_ready(self.get_status())
                return
            self._set_index_state(
                IndexState.ERROR,
                detail="Search data could not be opened. Try refreshing it.",
            )
            on_error("Search data could not be opened. Try refreshing it.")

        self._run_query_op(
            uses_collection=False,
            op=lambda _collection: self._open_profile_context(
                token,
                profile_name,
                collection_path,
                key,
                cancel_check=lambda: _raise_if_cancelled(event),
            ),
            success=opened,
            failure=failed,
        )
        return self.get_status()

    def _begin_profile_activation(
        self,
    ) -> tuple[int, str, str, str] | None:
        with self._state_lock:
            if self._bundle_update_running:
                return None
        if self._context is not None:
            self.deactivate_profile()
        collection = getattr(self.mw, "col", None)
        if collection is None:
            self._set_index_state(
                IndexState.UNAVAILABLE,
                detail="No Anki collection is open.",
            )
            return None
        profile_name = _profile_name(self.mw)
        collection_path = _collection_path(self.mw, collection)
        key = profile_key(profile_name, collection_path)
        config = self._read_config()
        self._semantic_enabled = bool(config.get("semantic_enabled", True))
        self._auto_semantic_index = bool(config.get("auto_semantic_index", True))
        with self._state_lock:
            self._profile_token += 1
            token = self._profile_token
        return token, profile_name, collection_path, key

    def _open_profile_context(
        self,
        token: int,
        profile_name: str,
        collection_path: str,
        key: str,
        cancel_check: Callable[[], None] | None = None,
    ) -> tuple[_ProfileContext, str | None]:
        if cancel_check is not None:
            cancel_check()
        profile_root = self.data_root / "profiles" / key
        profile_root.mkdir(parents=True, exist_ok=True)
        index = self._open_or_recover_index(
            profile_root / "search.sqlite3",
            cancel_check=cancel_check,
        )
        if cancel_check is not None:
            try:
                cancel_check()
            except Exception:
                index.close()
                raise
        try:
            maintenance = self._open_or_recover_maintenance(
                profile_root / "maintenance.sqlite3"
            )
        except Exception:
            index.close()
            raise
        semantic: SemanticService | None = None

        def checkpoint() -> None:
            if cancel_check is None:
                return
            try:
                cancel_check()
            except Exception:
                maintenance.close()
                index.close()
                vector_index = getattr(semantic, "index", None)
                close = getattr(vector_index, "close", None)
                if callable(close):
                    close()
                raise

        checkpoint()
        alias_error: str | None = None
        alias_path = self.bundle_root / _RXTERMS_RESOURCE
        if alias_path.is_file():
            try:
                index.load_alias_resource(alias_path)
            except Exception:
                # RxTerms improves search, but lexical/fuzzy search must not be
                # disabled by a damaged optional resource.
                alias_error = "Some medical aliases are temporarily unavailable."
        checkpoint()

        semantic_error: str | None = None
        try:
            semantic = self._open_or_recover_semantic(key)
        except Exception as error:
            semantic = None
            semantic_error = str(error)
        checkpoint()

        engine = SearchEngine(index)
        note_count = index.stats().note_count
        semantic_snapshot = semantic.status() if semantic is not None else None
        semantic_source_generation = (
            semantic.index.source_generation if semantic is not None else None
        )
        context = _ProfileContext(
            token=token,
            key=key,
            profile_name=profile_name,
            collection_path=collection_path,
            index=index,
            engine=engine,
            maintenance=maintenance,
            semantic=semantic,
            note_count=note_count,
            semantic_error=semantic_error,
            semantic_error_recovery=(
                SemanticRecovery.REINDEX if semantic_error else None
            ),
            semantic_snapshot=semantic_snapshot,
            semantic_source_generation=semantic_source_generation,
            semantic_restart_required=bool(
                semantic is not None
                and getattr(semantic, "restart_required", False)
            ),
            fingerprint=_read_index_fingerprint(index),
            lexical_generation=index.generation,
            field_names=index.field_names(),
        )
        if semantic is not None and semantic_snapshot is not None:
            sem = semantic_snapshot
            context.semantic_needs_reconcile = bool(
                sem.runtime_ready
                and sem.model_ready
                and (
                    sem.index_count != note_count
                    or semantic_source_generation != context.lexical_generation
                )
            )
            if sem.ready and not context.semantic_needs_reconcile:
                engine.semantic_provider = semantic
        return context, alias_error

    def _publish_profile_context(
        self,
        context: _ProfileContext,
        alias_error: str | None,
        *,
        auto_rebuild: bool,
    ) -> IndexStatus:
        if context.token != self._profile_token:
            self._close_context_in_background(context)
            return self.get_status()
        self._context = context
        note_count = context.note_count
        if note_count:
            detail = f"{note_count:,} notes ready."
            if alias_error:
                detail += f" {alias_error}"
            self._set_index_state(IndexState.READY, detail=detail)
        else:
            self._set_index_state(
                IndexState.UNAVAILABLE,
                detail="This profile needs initial search setup.",
            )
            if auto_rebuild:
                self.rebuild_index(_noop, _noop, _noop)
        return self.get_status()

    def deactivate_profile(self) -> None:
        """Detach immediately, then close disposable indexes off the GUI thread."""

        with self._state_lock:
            self._profile_token += 1
            events = tuple(self._active_cancellations)
            self._active_cancellations.clear()
            self._maintenance_cancellations.clear()
            self._semantic_cancellations.clear()
            self._journal_writes_inflight.clear()
            context = self._context
            self._context = None
            self._maintenance_running = False
            self._vocabulary_refresh_running = False
            self._vocabulary_refresh_token = None
            self._semantic_phase = None
            self._semantic_progress = None
        for event in events:
            event.set()
        semantic = getattr(context, "semantic", None)
        abort = getattr(semantic, "abort_now", None)
        if callable(abort):
            try:
                abort()
            except Exception:
                pass
        self._set_index_state(
            IndexState.UNAVAILABLE,
            detail="Open an Anki profile to initialize Smart Search.",
        )
        if context is None:
            return
        self._close_context_in_background(context)

    def _close_context_in_background(self, context: _ProfileContext) -> None:
        def close_retired_context(_collection: Any) -> None:
            self._close_profile_context(context)

        try:
            self._run_query_op(
                uses_collection=False,
                op=close_retired_context,
                success=_noop,
                failure=_noop,
            )
        except Exception:
            # Host shutdown can reject newly queued work.  A daemon fallback
            # still avoids waiting on a search/rebuild from Anki's GUI thread.
            threading.Thread(
                target=lambda: close_retired_context(None),
                name="smart-search-profile-cleanup",
                daemon=True,
            ).start()

    def _close_profile_context(self, context: _ProfileContext) -> None:
        """Close every profile-owned handle under the global reader order."""

        # Semantic readers take _search_lock and lexical readers take
        # _engine_lock. Waiting for both in the shared global order avoids
        # closing SQLite beneath an in-flight reader.
        with self._search_lock, self._engine_lock:
            context.engine.semantic_provider = None
            context.index.close()
            if context.maintenance is not None:
                context.maintenance.close()
            semantic = context.semantic
            close_semantic = getattr(semantic, "close", None)
            if not callable(close_semantic):
                close_semantic = getattr(semantic, "unload", None)
            if callable(close_semantic):
                try:
                    close_semantic()
                except Exception:
                    pass
            vector_index = getattr(semantic, "index", None)
            close = getattr(vector_index, "close", None)
            if callable(close):
                close()

    def _open_or_recover_index(
        self,
        path: Path,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> SmartSearchIndex:
        try:
            return SmartSearchIndex(path, cancel_check=cancel_check)
        except FTS5Unavailable:
            raise
        except Exception:
            if cancel_check is not None:
                cancel_check()
            # The index is derived data. Preserve a recoverable copy rather
            # than deleting it, then allow a clean rebuild.
            if not path.exists():
                raise
            suffix = f".invalid-{int(time.time())}"
            os.replace(path, Path(str(path) + suffix))
            for sidecar in ("-wal", "-shm"):
                candidate = Path(str(path) + sidecar)
                if candidate.exists():
                    os.replace(candidate, Path(str(candidate) + suffix))
            return SmartSearchIndex(path, cancel_check=cancel_check)

    def _open_or_recover_maintenance(self, path: Path) -> MaintenanceJournal:
        """Preserve damaged derived journal state and resume via a new audit."""

        try:
            return MaintenanceJournal(path)
        except Exception as first_error:
            if not path.exists():
                raise
            suffix = f".invalid-{int(time.time())}"
            archived = Path(str(path) + suffix)
            counter = 1
            while archived.exists():
                archived = Path(str(path) + suffix + f"-{counter}")
                counter += 1
            try:
                os.replace(path, archived)
                for sidecar in ("-wal", "-shm"):
                    candidate = Path(str(path) + sidecar)
                    if candidate.exists():
                        os.replace(
                            candidate,
                            Path(str(archived) + sidecar),
                        )
            except Exception as archive_error:
                raise RuntimeError(
                    "Maintenance state could not be opened or preserved: "
                    f"{first_error}"
                ) from archive_error
            recovered = MaintenanceJournal(path)
            recovered.set_meta("force_canonical_hydration", "1")
            recovered.request_audit("journal-recovery")
            return recovered

    def _open_or_recover_semantic(self, profile_key_value: str) -> SemanticService:
        """Open the disposable vector index, preserving damage before retrying."""

        vector_root = (
            self.data_root / "profiles" / str(profile_key_value) / "vectors"
        )
        try:
            return SemanticService(
                data_root=self.data_root,
                bundle_root=self.bundle_root,
                profile_key=profile_key_value,
            )
        except Exception as first_error:
            if not vector_root.exists():
                raise

            suffix = f".invalid-{int(time.time())}"
            archived = Path(str(vector_root) + suffix)
            counter = 1
            while archived.exists():
                archived = Path(str(vector_root) + suffix + f"-{counter}")
                counter += 1
            try:
                os.replace(vector_root, archived)
            except Exception as archive_error:
                raise RuntimeError(
                    "The semantic vector index could not be opened or preserved "
                    f"for recovery: {first_error}"
                ) from archive_error

            try:
                return SemanticService(
                    data_root=self.data_root,
                    bundle_root=self.bundle_root,
                    profile_key=profile_key_value,
                )
            except Exception as retry_error:
                raise RuntimeError(
                    "The damaged semantic vector index was preserved at "
                    f"{archived}, but a clean index could not be opened: "
                    f"{retry_error}"
                ) from retry_error

    # ------------------------------------------------------------------
    # UI protocol: settings and status

    def load_decks(
        self,
        on_success: DeckCatalogCallback,
        on_error: ErrorCallback,
    ) -> Callable[[], None]:
        """Load the active profile's deck catalog on Anki's collection worker."""

        event = self._new_cancellation()
        context = self._context
        token = context.token if context is not None else -1
        if context is None:
            self._forget_cancellation(event)
            on_error("Open an Anki profile to choose a deck.")
            return event.set

        def op(collection: Any) -> DeckCatalog:
            _raise_if_cancelled(event)
            catalog = self.reader.deck_catalog(collection)
            _raise_if_cancelled(event)
            return catalog

        def success(catalog: DeckCatalog) -> None:
            self._forget_cancellation(event)
            if self._cancelled(event, token):
                return
            on_success(catalog)

        def failure(error: Exception) -> None:
            self._forget_cancellation(event)
            if isinstance(error, _CancelledOperation) or self._cancelled(event, token):
                return
            on_error(str(error))

        try:
            self._run_query_op(
                uses_collection=True,
                op=op,
                success=success,
                failure=failure,
            )
        except Exception:
            self._forget_cancellation(event)
            raise
        return event.set

    def load_settings(self) -> UISettings:
        config = self._read_config()
        ui = config.get("ui") if isinstance(config.get("ui"), dict) else {}
        mode_text = str(ui.get("mode", config.get("default_mode", "smart"))).casefold()
        try:
            mode = SearchMode(mode_text)
        except ValueError:
            mode = SearchMode.SMART
        preview_default = PreviewDefault.QUESTION
        for candidate in (
            ui.get("preview_default"),
            config.get("preview_default"),
        ):
            try:
                preview_default = PreviewDefault(
                    str(candidate or "").strip().casefold()
                )
                break
            except ValueError:
                continue

        filters: list[FilterChip] = []
        for value in ui.get("filters", ()):
            if not isinstance(value, dict):
                continue
            key = str(value.get("key", "")).strip()
            filter_value = str(value.get("value", "")).strip()
            if key and filter_value:
                filters.append(
                    FilterChip(
                        key=key,
                        value=filter_value,
                        label=str(value.get("label", "")),
                    )
                )
        return UISettings(
            mode=mode,
            filters=tuple(filters),
            result_limit=_bounded_int(
                ui.get("result_limit", config.get("result_limit", _DEFAULT_RESULT_LIMIT)),
                10,
                200,
                _DEFAULT_RESULT_LIMIT,
            ),
            preview_enabled=bool(
                ui.get(
                    "preview_enabled",
                    config.get("preview_enabled", True),
                )
            ),
            preview_default=preview_default,
            width=_bounded_int(ui.get("width", 1040), 760, 4000, 1040),
            height=_bounded_int(ui.get("height", 700), 520, 3000, 700),
        )

    def save_settings(self, settings: UISettings) -> None:
        config = self._read_config()
        config["result_limit"] = _bounded_int(
            settings.result_limit, 10, 200, _DEFAULT_RESULT_LIMIT
        )
        config["preview_enabled"] = bool(settings.preview_enabled)
        try:
            preview_default = PreviewDefault(settings.preview_default)
        except (TypeError, ValueError):
            preview_default = PreviewDefault.QUESTION
        config["preview_default"] = preview_default.value
        config["ui"] = {
            "mode": settings.mode.value,
            "result_limit": config["result_limit"],
            "preview_enabled": config["preview_enabled"],
            "preview_default": config["preview_default"],
            "width": _bounded_int(settings.width, 760, 4000, 1040),
            "height": _bounded_int(settings.height, 520, 3000, 700),
            "filters": [
                {"key": item.key, "value": item.value, "label": item.label}
                for item in settings.filters
            ],
        }
        manager = getattr(self.mw, "addonManager", None)
        if manager is not None:
            manager.writeConfig(self.addon_module, config)

    def _read_config(self) -> dict[str, Any]:
        manager = getattr(self.mw, "addonManager", None)
        if manager is None:
            return {}
        try:
            value = manager.getConfig(self.addon_module)
        except Exception:
            return {}
        return dict(value) if isinstance(value, dict) else {}

    def get_status(self) -> IndexStatus:
        """Return a lock-only snapshot suitable for Anki's GUI thread."""

        with self._state_lock:
            state = self._index_state
            progress = self._index_progress
            detail = self._index_detail
            context = self._context
        semantic = self._semantic_status(context)
        return IndexStatus(
            state=state,
            progress=progress,
            detail=detail,
            semantic=semantic,
        )

    def _semantic_status(
        self, context: _ProfileContext | None
    ) -> SemanticStatus | None:
        if context is None:
            return None
        if not self._semantic_enabled:
            return SemanticStatus(
                SemanticState.UNSUPPORTED,
                detail="Semantic search is disabled in the add-on configuration.",
            )
        with self._state_lock:
            phase = self._semantic_phase
            phase_progress = self._semantic_progress
            phase_detail = self._semantic_phase_detail
        if phase in {"installing", "indexing", "updating"}:
            detail = phase_detail or (
                "Setting up semantic search."
                if phase == "installing"
                else (
                    "Updating semantic search."
                    if phase == "updating"
                    else "Preparing semantic search."
                )
            )
            return SemanticStatus(
                SemanticState.INDEXING,
                detail=detail,
                indexed_notes=_semantic_count(context),
                progress=phase_progress,
            )
        if self._semantic_restart_pending(context):
            return SemanticStatus(
                SemanticState.RESTART_REQUIRED,
                detail=(
                    "Restart Anki once to load the repaired Semantic search "
                    "components. Smart and Exact remain available."
                ),
                indexed_notes=_semantic_count(context),
            )
        if context.semantic is None:
            return SemanticStatus(
                SemanticState.ERROR,
                detail="Semantic search needs attention.",
                recovery=(
                    context.semantic_error_recovery or SemanticRecovery.REINDEX
                ),
            )
        status = context.semantic_snapshot
        if status is None:
            return SemanticStatus(
                SemanticState.ERROR,
                detail="Semantic search needs attention.",
                recovery=(
                    context.semantic_error_recovery or SemanticRecovery.REINDEX
                ),
            )
        if not status.supported:
            return SemanticStatus(
                SemanticState.UNSUPPORTED,
                detail=(
                    "Semantic Search currently requires macOS 14 or later on "
                    "Apple silicon. Smart and Exact search remain available."
                ),
            )
        if not status.runtime_ready or not status.model_ready:
            return SemanticStatus(
                SemanticState.NOT_INSTALLED,
                detail="Semantic search runs privately on this computer.",
                indexed_notes=status.index_count,
            )
        if status.error or context.semantic_error:
            recovery = (
                context.semantic_error_recovery
                or (
                    SemanticRecovery.MODEL
                    if getattr(status, "error_kind", None) == "model"
                    else SemanticRecovery.REINDEX
                )
            )
            return SemanticStatus(
                SemanticState.ERROR,
                detail=(
                    "Semantic search needs repair."
                    if recovery is SemanticRecovery.MODEL
                    else "Semantic search preparation did not finish."
                ),
                indexed_notes=status.index_count,
                recovery=recovery,
            )
        if (
            context.semantic_needs_reconcile
            or status.index_count != context.note_count
            or context.semantic_source_generation != context.lexical_generation
        ):
            return SemanticStatus(
                SemanticState.MODEL_READY,
                detail=(
                    "Preparation starts automatically after startup checks. "
                    "Choose Prepare Now to start it immediately."
                    if self._auto_semantic_index
                    else "Prepare Semantic search for this profile."
                ),
                indexed_notes=status.index_count,
                auto_start=self._auto_semantic_index,
            )
        return SemanticStatus(
            SemanticState.READY,
            detail="Semantic search is ready and stays on this computer.",
            indexed_notes=status.index_count,
        )

    @staticmethod
    def _semantic_restart_pending(context: _ProfileContext | None) -> bool:
        if context is None:
            return False
        semantic = getattr(context, "semantic", None)
        return bool(
            getattr(context, "semantic_restart_required", False)
            or getattr(semantic, "restart_required", False)
        )

    def _refresh_semantic_snapshot(self, context: _ProfileContext) -> Any | None:
        """Refresh cached semantic metadata from a worker thread.

        ``get_status()`` is called by Qt timers and mode clicks, so it must not
        open SQLite or inspect model files.  Worker operations call this helper
        after semantic work and publish one cheap immutable snapshot.
        """

        semantic = context.semantic
        if semantic is None:
            return None
        status = semantic.status()
        source_generation = semantic.index.source_generation
        with self._state_lock:
            if context.token == self._profile_token:
                context.semantic_snapshot = status
                context.semantic_source_generation = source_generation
        return status

    def _set_index_state(
        self,
        state: IndexState,
        *,
        progress: float | None = None,
        detail: str = "",
    ) -> None:
        with self._state_lock:
            self._index_state = state
            self._index_progress = (
                None if progress is None else min(1.0, max(0.0, float(progress)))
            )
            self._index_detail = str(detail)

    # ------------------------------------------------------------------
    # UI protocol: searching

    def submit_search(
        self,
        request: SearchRequest,
        on_success: Callable[[SearchResponse], None],
        on_error: ErrorCallback,
    ) -> Callable[[], None]:
        event = self._new_cancellation(
            kind=(
                "semantic"
                if not request.literal and request.mode is SearchMode.SEMANTIC
                else "interactive"
            )
        )
        context = self._context
        token = context.token if context else -1
        executable_query, incomplete_filters = strip_incomplete_filter_tokens(
            request.query,
            filter_keys=DEFAULT_FILTER_KEYS | {"notetype"},
            field_names=context.field_names if context is not None else (),
        )
        effective_request = (
            replace(request, query=executable_query)
            if incomplete_filters
            else request
        )
        query = _canonical_query(executable_query)

        def deliver(response: SearchResponse) -> None:
            if incomplete_filters:
                quoted = ", ".join(
                    f"“{token}”" for token in incomplete_filters
                )
                noun = (
                    "filter was"
                    if len(incomplete_filters) == 1
                    else "filters were"
                )
                notice = (
                    f"The unfinished {noun} ignored: {quoted}. "
                    "Finish it to apply that filter."
                )
                response = replace(
                    response,
                    query=request.query,
                    warnings=tuple(
                        dict.fromkeys((*response.warnings, notice))
                    ),
                )
            on_success(response)

        if not query:
            self._forget_cancellation(event)
            deliver(
                SearchResponse(
                    request_id=request.request_id,
                    query=request.query,
                )
            )
            return event.set

        parsed = None
        if context is not None:
            try:
                parsed = QueryParser(field_names=context.field_names).parse(query)
            except Exception:
                parsed = None

        # Exact mode is the deterministic escape hatch.  Let Anki parse and
        # execute the complete expression instead of approximating Boolean
        # semantics through the hybrid parser.
        if effective_request.literal or effective_request.mode is SearchMode.EXACT:
            self._submit_native_fallback(
                effective_request,
                query,
                event,
                token,
                deliver,
                on_error,
                warning="",
                parsed=parsed,
            )
            return event.set

        if (
            context is None
            or self.get_status().state is not IndexState.READY
        ):
            self._submit_native_fallback(
                effective_request,
                query,
                event,
                token,
                deliver,
                on_error,
                warning="Smart Search was unavailable; Exact search was used.",
                parsed=parsed,
            )
            return event.set

        if parsed is None:
            self._submit_native_fallback(
                effective_request,
                query,
                event,
                token,
                deliver,
                on_error,
                warning="Anki's standard search was used for this query.",
                parsed=None,
            )
            return event.set

        if parsed.native_search_required:
            self._submit_native_fallback(
                effective_request,
                query,
                event,
                token,
                deliver,
                on_error,
                warning=(
                    "Mixed Boolean, grouped, or wildcard syntax was evaluated "
                    "directly by Anki so its meaning was preserved."
                ),
                parsed=parsed,
            )
            return event.set

        if parsed.requires_anki_filter:
            self._run_query_op(
                uses_collection=True,
                op=lambda collection: self.reader.card_ids_by_note_for_query(
                    collection, parsed.anki_filter_query
                ),
                success=lambda card_scope: self._start_external_search(
                    effective_request,
                    query,
                    card_scope,
                    event,
                    context,
                    deliver,
                    on_error,
                ),
                failure=lambda _error: self._submit_native_fallback(
                    effective_request,
                    query,
                    event,
                    token,
                    deliver,
                    on_error,
                    warning="Anki's standard search was used for this query.",
                    parsed=parsed,
                ),
            )
        else:
            self._start_external_search(
                effective_request,
                query,
                None,
                event,
                context,
                deliver,
                on_error,
            )
        return event.set

    def refresh_card_states(
        self,
        results: Sequence[SearchResult],
        on_success: Callable[[tuple[SearchResult, ...]], None],
        on_error: ErrorCallback = _noop,
    ) -> None:
        """Refresh mutable card metadata without rerunning any search index."""

        context = self._context
        token = context.token if context is not None else -1
        visible = tuple(results)
        note_ids = tuple(result.note_id for result in visible)
        if context is None or not note_ids:
            on_success(visible)
            return

        def success(
            states_by_note: dict[int, tuple[tuple[int, int, bool, bool], ...]],
        ) -> None:
            if token != self._profile_token:
                return
            on_success(_results_with_live_card_states(visible, states_by_note))

        self._run_query_op(
            uses_collection=True,
            op=lambda collection: self.reader.card_states_for_notes(
                collection,
                note_ids,
                card_ids_by_note={
                    int(result.note_id): tuple(result.card_ids)
                    for result in visible
                },
            ),
            success=success,
            failure=lambda error: on_error(str(error)),
        )

    def _start_external_search(
        self,
        request: SearchRequest,
        query: str,
        card_ids_by_note: Mapping[int, Sequence[int]] | None,
        event: threading.Event,
        context: _ProfileContext,
        on_success: Callable[[SearchResponse], None],
        on_error: ErrorCallback,
    ) -> None:
        if self._cancelled(event, context.token):
            self._forget_cancellation(event)
            return
        card_scope = (
            {
                int(note_id): tuple(
                    int(card_id)
                    for card_id in card_ids
                    if int(card_id) > 0
                )
                for note_id, card_ids in card_ids_by_note.items()
            }
            if card_ids_by_note is not None
            else None
        )
        allowed = set(card_scope) if card_scope is not None else None
        mode = SearchMode.EXACT if request.literal else request.mode

        def search(_collection: Any) -> SearchResponse:
            _raise_if_cancelled(event)
            if mode is SearchMode.SEMANTIC:
                # Semantic readers share a lock only with semantic writers.
                # Smart/Exact work must never queue behind cold model startup.
                with self._search_lock:
                    # Resolve readiness only after joining the reader/writer
                    # lock. A writer may have detached the provider while this
                    # request was waiting; never reattach that stale reader.
                    semantic_status = self._semantic_status(context)
                    semantic_provider = (
                        context.semantic
                        if context.semantic is not None
                        and semantic_status is not None
                        and semantic_status.ready
                        else None
                    )
                    if semantic_provider is not None:
                        try:
                            self._semantic_activity_callback(True)
                        except Exception:
                            pass
                    try:
                        with self._engine_lock:
                            context.engine.semantic_provider = semantic_provider
                        _raise_if_cancelled(event)
                        response = context.engine.search(
                            query,
                            limit=request.limit,
                            allowed_note_ids=allowed,
                            mode=mode.value,
                            cancel_check=lambda: _raise_if_cancelled(event),
                        )
                    finally:
                        if semantic_provider is not None:
                            # A superseded typing request may finish its one
                            # bounded inference but discard the result. Reset
                            # the lease from that completion so the replacement
                            # request can reuse the warm helper.
                            try:
                                self._semantic_activity_callback(False)
                            except Exception:
                                pass
                self._refresh_semantic_snapshot(context)
            else:
                with self._engine_lock:
                    _raise_if_cancelled(event)
                    response = context.engine.search(
                        query,
                        limit=request.limit,
                        allowed_note_ids=allowed,
                        mode=mode.value,
                        cancel_check=lambda: _raise_if_cancelled(event),
                    )
            _raise_if_cancelled(event)
            return _to_ui_response(
                request,
                response,
                card_ids_by_note=card_scope,
            )

        def success(response: SearchResponse) -> None:
            if self._cancelled(event, context.token):
                self._forget_cancellation(event)
                return
            self._hydrate_search_response(
                response,
                event=event,
                token=context.token,
                on_success=on_success,
                card_ids_by_note=card_scope,
            )

        self._run_query_op(
            uses_collection=False,
            op=search,
            success=success,
            failure=lambda error: self._external_search_failed(
                request,
                query,
                event,
                context.token,
                on_success,
                on_error,
                error,
            ),
        )

    def _hydrate_search_response(
        self,
        response: SearchResponse,
        *,
        event: threading.Event,
        token: int,
        on_success: Callable[[SearchResponse], None],
        card_ids_by_note: Mapping[int, Sequence[int]] | None = None,
    ) -> None:
        """Attach live card state without making external search hold Anki's lock."""

        note_ids = tuple(result.note_id for result in response.results)
        if not note_ids:
            self._forget_cancellation(event)
            on_success(response)
            return

        def deliver(
            states_by_note: dict[int, tuple[tuple[int, int, bool, bool], ...]],
        ) -> None:
            if self._cancelled(event, token):
                self._forget_cancellation(event)
                return
            self._forget_cancellation(event)
            on_success(_with_live_card_states(response, states_by_note))

        def unavailable(_error: Exception) -> None:
            # Card-state decoration is useful metadata, never a reason to hide
            # otherwise valid search results.
            if self._cancelled(event, token):
                self._forget_cancellation(event)
                return
            self._forget_cancellation(event)
            on_success(response)

        self._run_query_op(
            uses_collection=True,
            op=lambda collection: self.reader.card_states_for_notes(
                collection,
                note_ids,
                card_ids_by_note=card_ids_by_note,
            ),
            success=deliver,
            failure=unavailable,
        )

    def _external_search_failed(
        self,
        request: SearchRequest,
        query: str,
        event: threading.Event,
        token: int,
        on_success: Callable[[SearchResponse], None],
        on_error: ErrorCallback,
        error: Exception,
    ) -> None:
        if isinstance(error, _CancelledOperation) or self._cancelled(event, token):
            self._forget_cancellation(event)
            return
        self._submit_native_fallback(
            request,
            query,
            event,
            token,
            on_success,
            on_error,
            warning="Anki's standard search was used for this query.",
        )

    def _submit_native_fallback(
        self,
        request: SearchRequest,
        query: str,
        event: threading.Event,
        token: int,
        on_success: Callable[[SearchResponse], None],
        on_error: ErrorCallback,
        *,
        warning: str,
        parsed: Any | None = None,
    ) -> None:
        if self._cancelled(event, token):
            self._forget_cancellation(event)
            return

        def native(collection: Any) -> SearchResponse:
            _raise_if_cancelled(event)
            all_card_ids_by_note = self.reader.card_ids_by_note_for_query(
                collection,
                query,
            )
            note_ids = tuple(all_card_ids_by_note)[: request.limit]
            card_ids_by_note = {
                note_id: all_card_ids_by_note[note_id]
                for note_id in note_ids
            }
            notes = tuple(iter_collection_notes(collection, note_ids))
            _raise_if_cancelled(event)
            response = _native_ui_response(
                request,
                notes,
                warning,
                card_ids_by_note=card_ids_by_note,
                parsed=parsed,
                total_results=len(all_card_ids_by_note),
            )
            states = self.reader.card_states_for_notes(
                collection,
                note_ids,
                card_ids_by_note=card_ids_by_note,
            )
            return _with_live_card_states(response, states)

        def success(response: SearchResponse) -> None:
            if self._cancelled(event, token):
                self._forget_cancellation(event)
                return
            self._forget_cancellation(event)
            on_success(response)

        def failure(error: Exception) -> None:
            self._forget_cancellation(event)
            if not isinstance(error, _CancelledOperation) and not event.is_set():
                on_error("This search could not be completed. Try a simpler query.")

        self._run_query_op(
            uses_collection=True,
            op=native,
            success=success,
            failure=failure,
        )

    # ------------------------------------------------------------------
    # Full and incremental indexing

    def rebuild_index(
        self,
        on_progress: ProgressCallback,
        on_success: StatusCallback,
        on_error: ErrorCallback,
    ) -> Callable[[], None] | None:
        context = self._context
        if context is None:
            on_error("No Anki profile is open.")
            return None
        if not self._begin_maintenance():
            on_error("An index maintenance operation is already running.")
            return None

        event = self._new_cancellation(kind="maintenance")
        token = context.token
        previous_count = context.note_count
        self._set_index_state(IndexState.BUILDING, progress=0.0, detail="Starting snapshot.")

        def snapshot(
            collection: Any,
        ) -> tuple[
            list[IndexedNote],
            str,
            tuple[Any, ...],
            tuple[Any, ...],
            tuple[FacetManifest, ...],
        ]:
            def progress(done: int, total: int) -> None:
                _raise_if_cancelled(event)
                fraction = 0.20 * (done / max(1, total))
                self._set_index_state(
                    IndexState.BUILDING,
                    progress=fraction,
                    detail=f"Reading notes {done:,}/{total:,}.",
                )
                on_progress(fraction, f"reading notes {done:,}/{total:,}")

            notes = self.reader.snapshot(
                collection,
                progress=progress,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            note_rows, card_rows, facet_rows, _filtered_note_ids = (
                _collection_manifest_inventory(
                    self.reader,
                    collection,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
            )
            return (
                notes,
                _collection_fingerprint(collection),
                note_rows,
                card_rows,
                facet_rows,
            )

        def snapshot_success(
            payload: tuple[
                list[IndexedNote],
                str,
                tuple[Any, ...],
                tuple[Any, ...],
                tuple[FacetManifest, ...],
            ]
        ) -> None:
            notes, fingerprint, note_rows, card_rows, facet_rows = payload
            if self._cancelled(event, token):
                self._finish_cancelled_maintenance(
                    event, token, previous_count, on_success
                )
                return
            self._build_snapshot_in_background(
                context,
                notes,
                fingerprint,
                event,
                previous_count,
                on_progress,
                on_success,
                on_error,
                manifest_inventory=(note_rows, card_rows, facet_rows),
            )

        self._run_query_op(
            uses_collection=True,
            op=snapshot,
            success=snapshot_success,
            failure=lambda error: self._maintenance_failed(
                error,
                event,
                token,
                previous_count,
                on_success,
                on_error,
            ),
        )
        return event.set

    def _build_snapshot_in_background(
        self,
        context: _ProfileContext,
        notes: list[IndexedNote],
        fingerprint: str,
        event: threading.Event,
        previous_count: int,
        on_progress: ProgressCallback,
        on_success: StatusCallback,
        on_error: ErrorCallback,
        *,
        manifest_inventory: tuple[
            tuple[Any, ...],
            tuple[Any, ...],
            tuple[FacetManifest, ...],
        ]
        | None = None,
    ) -> None:
        token = context.token

        def build(_collection: Any) -> int:
            # Lexical index replacement must not race a Semantic reader that
            # later resolves note IDs back through this same external index.
            # Keep the global order search -> engine used everywhere else.
            with self._search_lock, self._engine_lock:
                _raise_if_cancelled(event)
                journal = context.maintenance
                rebuild_audit_sequence: int | None = None
                if journal is not None and manifest_inventory is not None:
                    journal.set_meta("manifest_initialized", "0")
                    rebuild_audit_sequence = journal.request_audit(
                        "rebuild-interrupted"
                    )

                # Detach vectors before any lexical mutation. A cancellation
                # can arrive immediately after the atomic SQLite swap; failing
                # closed here prevents an old semantic generation from ever
                # being reported ready against that new lexical database.
                context.engine.semantic_provider = None
                if context.semantic is not None:
                    context.semantic_needs_reconcile = True
                    context.semantic_source_generation = None
                    with self._state_lock:
                        context.semantic_exact_delta_chain = False
                        context.semantic_force_full_refresh = True
                        context.semantic_pending_note_ids.clear()
                        context.semantic_pending_deleted_ids.clear()
                    clear_generation = getattr(
                        getattr(context.semantic, "index", None),
                        "clear_source_generation",
                        None,
                    )
                    if callable(clear_generation):
                        try:
                            clear_generation()
                        except Exception:
                            # The lexical rebuild remains useful. Its new
                            # generation will also fail the persisted semantic
                            # generation check on the next profile opening.
                            pass

                def lexical_progress(done: int) -> None:
                    _raise_if_cancelled(event)
                    fraction = 0.20 + 0.60 * (done / max(1, len(notes)))
                    self._set_index_state(
                        IndexState.BUILDING,
                        progress=fraction,
                        detail=f"Preparing notes {done:,}/{len(notes):,}.",
                    )
                    on_progress(fraction, f"preparing notes {done:,}/{len(notes):,}")

                count = context.index.rebuild(
                    notes,
                    progress=lexical_progress,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )

                # ``rebuild()`` has now published a new database. Mirror that
                # generation in memory before any cancellable tail work.
                context.note_count = count
                context.field_names = context.index.field_names()
                context.lexical_generation = context.index.generation
                context.engine = SearchEngine(context.index)
                context.fingerprint = fingerprint
                _write_index_fingerprint(context.index, fingerprint)

                alias_path = self.bundle_root / _RXTERMS_RESOURCE
                if alias_path.is_file():
                    context.index.load_alias_resource(alias_path)
                    context.lexical_generation = context.index.generation
                _raise_if_cancelled(event)
                context.engine.warmup(
                    cancel_check=lambda: _raise_if_cancelled(event)
                )
                self._set_index_state(
                    IndexState.BUILDING,
                    progress=0.90,
                    detail="Finishing setup.",
                )
                on_progress(0.90, "finishing setup")

                # Semantic embedding is deliberately not performed under the
                # lexical engine lock.  Mark an existing semantic index dirty;
                # the success callback starts the normal background semantic
                # worker after the text index becomes searchable again.
                if context.semantic is not None:
                    context.semantic_needs_reconcile = True
                _raise_if_cancelled(event)
                if journal is not None and manifest_inventory is not None:
                    note_rows, card_rows, facet_rows = manifest_inventory
                    # Initialization is explicit. If cancellation/crash lands
                    # between these bounded transactions, the next audit sees
                    # state 0 and replaces every manifest instead of trusting a
                    # partial table.
                    journal.replace_manifest(
                        note_rows,
                        cancel_check=lambda: _raise_if_cancelled(event),
                    )
                    journal.replace_card_manifest(
                        card_rows,
                        cancel_check=lambda: _raise_if_cancelled(event),
                    )
                    journal.replace_facet_manifest(
                        facet_rows,
                        cancel_check=lambda: _raise_if_cancelled(event),
                    )
                    journal.set_meta("manifest_initialized", "1")
                    journal.delete_meta("pending_collection_fingerprint")
                    if rebuild_audit_sequence is not None:
                        journal.complete_audit(
                            rebuild_audit_sequence,
                            clear_meta_keys=("force_canonical_hydration",),
                        )
                _raise_if_cancelled(event)
                return count

        def success(count: int) -> None:
            if self._cancelled(event, token):
                self._finish_cancelled_maintenance(event, token, count, on_success)
                return
            self._forget_cancellation(event)
            rerun = self._end_maintenance()
            self._set_index_state(
                IndexState.READY,
                detail=f"{count:,} notes ready.",
            )
            on_progress(1.0, "ready")
            on_success(self.get_status())
            if rerun:
                self.reconcile(on_error=on_error)
            else:
                self._refresh_existing_semantic_index(context)

        self._run_query_op(
            uses_collection=False,
            op=build,
            success=success,
            failure=lambda error: self._maintenance_failed(
                error,
                event,
                token,
                previous_count,
                on_success,
                on_error,
            ),
        )

    def reconcile(
        self,
        *,
        check_fingerprint: bool = False,
        on_success: StatusCallback = _noop,
        on_error: ErrorCallback = _noop,
    ) -> bool:
        """Debounced hook target: reconcile changed/deleted notes safely."""

        context = self._context
        if context is None:
            return False
        if not self._begin_maintenance(reconcile=True):
            return False
        event = self._new_cancellation(kind="maintenance")
        token = context.token

        def snapshot_if_needed(
            collection: Any,
        ) -> tuple[str, list[IndexedNote] | None]:
            fingerprint = _collection_fingerprint(collection)
            if check_fingerprint and fingerprint == context.fingerprint:
                return fingerprint, None
            return fingerprint, self.reader.snapshot(
                collection,
                cancel_check=lambda: _raise_if_cancelled(event),
            )

        self._run_query_op(
            uses_collection=True,
            op=snapshot_if_needed,
            success=lambda payload: self._reconcile_snapshot(
                context,
                payload[0],
                payload[1],
                event,
                on_success,
                on_error,
            ),
            failure=lambda error: self._reconcile_failed(
                error, event, token, on_error
            ),
        )
        return True

    def queue_maintenance(
        self,
        *,
        note_ids: Iterable[int] = (),
        deleted_note_ids: Iterable[int] = (),
        reasons: DirtyReason | int = DirtyReason.NOTE_CONTENT,
        audit_reason: str | None = None,
        on_ready: Callable[[bool], None] = lambda _saved: None,
    ) -> bool:
        """Persist maintenance intent off Anki's GUI/collection threads."""

        context = self._context
        journal = context.maintenance if context is not None else None
        if context is None or journal is None:
            on_ready(False)
            return False
        token = context.token
        live_ids = _positive_ids(note_ids)
        deleted_ids = _positive_ids(deleted_note_ids)
        with self._state_lock:
            self._journal_writes_inflight[token] = (
                self._journal_writes_inflight.get(token, 0) + 1
            )
        finish_lock = threading.Lock()
        finish_called = False

        def persist(_collection: Any) -> None:
            journal.enqueue(live_ids, reasons)
            journal.enqueue(
                deleted_ids,
                DirtyReason.DELETED
                | DirtyReason.LEXICAL
                | DirtyReason.SEMANTIC
                | DirtyReason.VOCABULARY,
            )
            if audit_reason is not None:
                journal.request_audit(audit_reason)

        def finished(saved: bool) -> None:
            nonlocal finish_called
            with finish_lock:
                if finish_called:
                    return
                finish_called = True
            # Register the success/fallback while the semantic gate is still
            # closed.  In current Anki versions GUI callbacks are queued even
            # when requested from the GUI thread, so dropping the in-flight
            # count first creates a real stale-generation publication window.
            try:
                if token == self._profile_token:
                    on_ready(saved)
            finally:
                with self._state_lock:
                    remaining = self._journal_writes_inflight.get(token, 0) - 1
                    if remaining > 0:
                        self._journal_writes_inflight[token] = remaining
                    else:
                        self._journal_writes_inflight.pop(token, None)

        try:
            self._run_query_op(
                uses_collection=False,
                op=persist,
                success=lambda _result: finished(True),
                failure=lambda _error: finished(False),
            )
        except Exception:
            finished(False)
            return False
        return True

    def audit_manifest(
        self,
        *,
        request: AuditRequest | None = None,
        on_success: StatusCallback = _noop,
        on_error: ErrorCallback = _noop,
    ) -> bool:
        """Turn an unknown bulk change into durable, targeted note work.

        Only compact note metadata is read from Anki.  Note text is hydrated
        later in bounded batches, so sync/import/undo no longer materializes
        every field in the profile or monopolizes one large collection read.
        """

        context = self._context
        journal = context.maintenance if context is not None else None
        if request is None and journal is not None:
            request = journal.audit_request()
        if context is None or journal is None or request is None:
            if context is not None:
                on_success(self.get_status())
                return True
            return False
        if not self._begin_maintenance(reconcile=True):
            return False

        event = self._new_cancellation(kind="maintenance")
        token = context.token

        def inventory(
            collection: Any,
        ) -> tuple[
            tuple[Any, ...],
            tuple[Any, ...],
            tuple[FacetManifest, ...],
            tuple[int, ...],
            str,
            str,
        ]:
            note_rows, card_rows, facet_rows, filtered_note_ids = (
                _collection_manifest_inventory(
                    self.reader,
                    collection,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
            )
            return (
                note_rows,
                card_rows,
                facet_rows,
                filtered_note_ids,
                _collection_fingerprint(collection),
                _collection_fingerprint(collection, legacy_card_scope=True),
            )

        def inventory_ready(
            payload: tuple[
                tuple[Any, ...],
                tuple[Any, ...],
                tuple[FacetManifest, ...],
                tuple[int, ...],
                str,
                str,
            ]
        ) -> None:
            if self._cancelled(event, token):
                self._reconcile_failed(
                    _CancelledOperation(), event, token, on_error
                )
                return

            (
                note_rows,
                card_rows,
                facet_rows,
                filtered_note_ids,
                fingerprint,
                legacy_fingerprint,
            ) = payload

            def classify(_collection: Any) -> ManifestDiff:
                _raise_if_cancelled(event)
                manifests_uninitialized = (
                    journal.get_meta("manifest_initialized", "0") != "1"
                )
                force_canonical_hydration = (
                    journal.get_meta("force_canonical_hydration", "0") == "1"
                )
                seeded_from_matching_index = False
                if manifests_uninitialized:
                    current_ids = {int(row.note_id) for row in note_rows}
                    indexed_ids = set(context.index.note_ids())
                    seeded_from_matching_index = bool(
                        not force_canonical_hydration
                        and current_ids == indexed_ids
                        and context.fingerprint
                        and (
                            context.fingerprint == fingerprint
                            or context.fingerprint == legacy_fingerprint
                        )
                    )
                    if seeded_from_matching_index:
                        mismatched_ids = _manifest_seed_mismatches(
                            context.index,
                            note_rows,
                            card_rows,
                            facet_rows,
                            cancel_check=lambda: _raise_if_cancelled(event),
                        )
                        difference = ManifestDiff(
                            added=(),
                            changed=mismatched_ids,
                            deleted=(),
                            unchanged=max(
                                0,
                                len(current_ids) - len(mismatched_ids),
                            ),
                        )
                    else:
                        difference = ManifestDiff(
                            added=tuple(sorted(current_ids)),
                            changed=(),
                            deleted=tuple(sorted(indexed_ids - current_ids)),
                            unchanged=0,
                        )
                else:
                    difference = journal.diff_manifest(
                        note_rows,
                        cancel_check=lambda: _raise_if_cancelled(event),
                    )
                _raise_if_cancelled(event)

                saved_note_rows = journal.manifest_rows(
                    cancel_check=lambda: _raise_if_cancelled(event)
                )
                saved_card_rows = journal.card_manifest_rows(
                    cancel_check=lambda: _raise_if_cancelled(event)
                )
                card_difference = journal.diff_card_manifest(
                    card_rows,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
                _raise_if_cancelled(event)
                facet_difference = journal.diff_facet_manifest(
                    facet_rows,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
                _raise_if_cancelled(event)

                changed_facet_keys = {
                    *facet_difference.added,
                    *facet_difference.changed,
                    *facet_difference.deleted,
                }
                changed_deck_ids = {
                    key.object_id
                    for key in changed_facet_keys
                    if key.facet == "deck"
                }
                changed_notetype_ids = {
                    key.object_id
                    for key in changed_facet_keys
                    if key.facet == "notetype"
                }
                facet_note_ids = {
                    int(row.note_id)
                    for row in (*saved_note_rows, *note_rows)
                    if int(row.note_type_id) in changed_notetype_ids
                }
                facet_note_ids.update(
                    int(row.note_id)
                    for row in (*saved_card_rows, *card_rows)
                    if int(row.home_deck_id) in changed_deck_ids
                )
                current_note_ids = {int(row.note_id) for row in note_rows}
                affected_live_ids = set(difference.candidate_note_ids)
                if seeded_from_matching_index:
                    # An old index built while cards were in a filtered deck
                    # may contain that transient deck name. Only those notes
                    # need canonical home-deck hydration during migration.
                    affected_live_ids.update(filtered_note_ids)
                else:
                    affected_live_ids.update(card_difference.affected_note_ids)
                    affected_live_ids.update(facet_note_ids)
                affected_live_ids.intersection_update(current_note_ids)

                # Queue first, then advance the saved manifest. A crash at any
                # boundary can cause harmless duplicate work, never lost work.
                journal.enqueue(
                    affected_live_ids,
                    DirtyReason.NOTE_CONTENT,
                )
                journal.enqueue(
                    difference.deleted,
                    DirtyReason.DELETED
                    | DirtyReason.LEXICAL
                    | DirtyReason.SEMANTIC
                    | DirtyReason.VOCABULARY,
                )
                _raise_if_cancelled(event)
                journal.set_meta("manifest_initialized", "0")
                journal.replace_manifest(
                    note_rows,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
                _raise_if_cancelled(event)
                journal.replace_card_manifest(
                    card_rows,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
                _raise_if_cancelled(event)
                journal.replace_facet_manifest(
                    facet_rows,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
                journal.set_meta("manifest_initialized", "1")
                # Include work left by an earlier audit. A later inventory can
                # legitimately classify as unchanged while its predecessor's
                # durable note batches are still draining; publishing the new
                # generation early would make the fingerprint claim more than
                # the searchable index currently contains.
                has_index_work = bool(journal.pending_count())
                if has_index_work:
                    # The inventory describes the future state. Keep the
                    # index's published fingerprint on its old generation
                    # until every durable note delta has been acknowledged.
                    journal.set_meta(
                        "pending_collection_fingerprint",
                        fingerprint,
                    )
                else:
                    context.fingerprint = fingerprint
                    _write_index_fingerprint(context.index, fingerprint)
                    journal.delete_meta("pending_collection_fingerprint")
                # Only clear the recovery guard when this exact audit request
                # was published. A newer request can arrive while the audit is
                # running; in that case the next audit must retain the
                # conservative hydration requirement.
                journal.complete_audit(
                    request.sequence,
                    clear_meta_keys=("force_canonical_hydration",),
                )
                _raise_if_cancelled(event)
                return difference

            def classified(_difference: ManifestDiff) -> None:
                self._forget_cancellation(event)
                if token != self._profile_token:
                    return
                self._end_maintenance()
                on_success(self.get_status())

            self._run_query_op(
                uses_collection=False,
                op=classify,
                success=classified,
                failure=lambda error: self._reconcile_failed(
                    error, event, token, on_error
                ),
            )

        self._run_query_op(
            uses_collection=True,
            op=inventory,
            success=inventory_ready,
            failure=lambda error: self._reconcile_failed(
                error, event, token, on_error
            ),
        )
        return True

    @staticmethod
    def _promote_pending_fingerprint(
        context: _ProfileContext,
        journal: MaintenanceJournal,
    ) -> bool:
        """Publish an audited generation only after its durable queue drains."""

        if journal.pending_count() or journal.audit_request() is not None:
            return False
        fingerprint = journal.get_meta("pending_collection_fingerprint")
        if not fingerprint:
            return False
        _write_index_fingerprint(context.index, fingerprint)
        context.fingerprint = fingerprint
        journal.delete_meta("pending_collection_fingerprint")
        return True

    def reconcile_note_ids(
        self,
        note_ids: Iterable[int],
        *,
        journal_entries: Sequence[DirtyNote] = (),
        on_success: StatusCallback = _noop,
        on_error: ErrorCallback = _noop,
    ) -> bool:
        """Refresh only the requested notes after a committed Anki operation.

        The collection worker reads a handful of immutable note snapshots.
        Hashing, text preparation, FTS writes, fuzzy-vocabulary construction,
        and semantic maintenance all happen on non-collection workers.
        """

        context = self._context
        requested = _positive_ids(note_ids)
        if context is None:
            return False
        if not requested:
            on_success(self.get_status())
            return True
        if not self._begin_maintenance():
            return False

        event = self._new_cancellation(kind="maintenance")
        token = context.token

        def snapshot(
            collection: Any,
        ) -> tuple[list[IndexedNote], tuple[Any, ...], tuple[Any, ...]]:
            notes = list(
                self.reader.snapshot_note_batch(
                    collection,
                    requested,
                    max_notes=250,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
            )
            _raise_if_cancelled(event)
            manifests = (
                self.reader.note_manifest_for_ids(collection, requested)
                if journal_entries
                else ()
            )
            _raise_if_cancelled(event)
            card_manifests = (
                self.reader.card_manifest_for_note_ids(collection, requested)
                if journal_entries
                else ()
            )
            _raise_if_cancelled(event)
            return notes, manifests, card_manifests

        self._run_query_op(
            uses_collection=True,
            op=snapshot,
            success=lambda payload: self._reconcile_targeted_snapshot(
                context,
                requested,
                payload[0],
                payload[1],
                payload[2],
                tuple(journal_entries),
                event,
                on_success,
                on_error,
            ),
            failure=lambda error: self._reconcile_failed(
                error, event, token, on_error
            ),
        )
        return True

    def _reconcile_targeted_snapshot(
        self,
        context: _ProfileContext,
        requested: tuple[int, ...],
        notes: list[IndexedNote],
        manifests: Sequence[Any],
        card_manifests: Sequence[Any],
        journal_entries: Sequence[DirtyNote],
        event: threading.Event,
        on_success: StatusCallback,
        on_error: ErrorCallback,
    ) -> None:
        token = context.token

        def reconcile(
            _collection: Any,
        ) -> tuple[
            int,
            int,
            int,
            tuple[int, ...],
            tuple[int, ...],
            bool,
        ]:
            _raise_if_cancelled(event)
            previous_generation = context.lexical_generation
            plan = context.index.plan_targeted_upsert(
                notes,
                candidate_note_ids=requested,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            live_ids = {note.note_id for note in notes}
            missing_ids = set(requested) - live_ids
            deleted_candidates = plan.existing_note_ids & missing_ids
            changed_ids = tuple(note.note_id for note, _digest in plan.changed)
            has_delta = bool(changed_ids or deleted_candidates)
            semantic_delta_safe = False

            if has_delta:
                # Drain any in-flight Semantic reader and publish dirty state
                # before changing the lexical metadata it resolves against.
                with self._search_lock, self._engine_lock, self._state_lock:
                    _raise_if_cancelled(event)
                    semantic_status = context.semantic_snapshot
                    semantic_phase = self._semantic_phase
                    base_is_current = bool(
                        context.semantic is not None
                        and semantic_status is not None
                        and semantic_status.runtime_ready
                        and semantic_status.model_ready
                        and not context.semantic_needs_reconcile
                        and not context.semantic_force_full_refresh
                        and not context.semantic_pending_note_ids
                        and not context.semantic_pending_deleted_ids
                        and semantic_phase is None
                        and context.semantic_source_generation
                        == previous_generation
                        and semantic_status.index_count == context.note_count
                    )
                    continuing_exact_chain = bool(
                        context.semantic is not None
                        and semantic_status is not None
                        and semantic_status.runtime_ready
                        and semantic_status.model_ready
                        and context.semantic_exact_delta_chain
                        and not context.semantic_force_full_refresh
                        and semantic_phase in (None, "updating")
                    )
                    semantic_delta_safe = bool(
                        base_is_current or continuing_exact_chain
                    )
                    if semantic_delta_safe:
                        context.semantic_exact_delta_chain = True
                        self._coalesce_semantic_delta_locked(
                            context,
                            changed_ids,
                            deleted_candidates,
                        )
                    elif context.semantic is not None:
                        context.semantic_exact_delta_chain = False
                        context.semantic_force_full_refresh = True
                        context.semantic_pending_note_ids.clear()
                        context.semantic_pending_deleted_ids.clear()
                    context.engine.defer_vocabulary_refresh()
                    context.engine.semantic_provider = None
                    context.semantic_needs_reconcile = context.semantic is not None

            changed = context.index.apply_upsert(
                plan,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            unchanged = plan.unchanged + (len(plan.changed) - changed)
            deleted = context.index.delete_notes(
                deleted_candidates,
                cancel_check=lambda: _raise_if_cancelled(event),
            )

            added = sum(
                1 for note_id in changed_ids if note_id not in plan.existing_note_ids
            )
            context.note_count = max(0, context.note_count + added - deleted)
            context.field_names = context.index.field_names()
            context.lexical_generation = context.index.generation
            journal = context.maintenance
            if journal is not None and journal_entries:
                if journal.get_meta("manifest_initialized", "0") == "1":
                    journal.upsert_manifest(manifests)
                    journal.delete_manifest(missing_ids)
                    journal.replace_card_manifest_for_notes(
                        requested,
                        card_manifests,
                    )
                entries_by_id = {
                    int(entry.note_id): entry for entry in journal_entries
                }
                journal.acknowledge_many(
                    (
                        (entry.note_id, entry.sequence)
                        for note_id in requested
                        if (entry := entries_by_id.get(int(note_id)))
                        is not None
                    ),
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
                self._promote_pending_fingerprint(context, journal)
            _raise_if_cancelled(event)
            actual_changed_ids = changed_ids if changed else ()
            actual_deleted_ids = tuple(sorted(deleted_candidates)) if deleted else ()
            return (
                changed,
                unchanged,
                deleted,
                actual_changed_ids,
                actual_deleted_ids,
                semantic_delta_safe,
            )

        def success(
            result: tuple[
                int,
                int,
                int,
                tuple[int, ...],
                tuple[int, ...],
                bool,
            ],
        ) -> None:
            if self._cancelled(event, token):
                self._forget_cancellation(event)
                if token == self._profile_token:
                    self._end_maintenance()
                return
            (
                _changed,
                _unchanged,
                _deleted,
                changed_ids,
                deleted_ids,
                semantic_delta_safe,
            ) = result
            self._forget_cancellation(event)
            self._end_maintenance()
            self._set_index_state(
                IndexState.READY,
                detail=f"{context.note_count:,} notes ready.",
            )
            on_success(self.get_status())
            if changed_ids or deleted_ids:
                if semantic_delta_safe:
                    self._queue_semantic_delta(
                        context,
                        changed_ids,
                        deleted_ids,
                        start=not bool(journal_entries),
                    )
                elif not journal_entries:
                    self._refresh_existing_semantic_index(context)

        self._run_query_op(
            uses_collection=False,
            op=reconcile,
            success=success,
            failure=lambda error: self._reconcile_failed(
                error, event, token, on_error
            ),
        )

    def _reconcile_snapshot(
        self,
        context: _ProfileContext,
        fingerprint: str,
        notes: list[IndexedNote] | None,
        event: threading.Event,
        on_success: StatusCallback,
        on_error: ErrorCallback,
    ) -> None:
        token = context.token
        if notes is None:
            self._forget_cancellation(event)
            if token != self._profile_token:
                return
            rerun = self._end_maintenance()
            self._set_index_state(
                IndexState.READY,
                detail=f"{context.note_count:,} notes ready.",
            )
            on_success(self.get_status())
            if rerun:
                self.reconcile(on_success=on_success, on_error=on_error)
            return

        def reconcile(_collection: Any) -> tuple[int, int, int]:
            # Classify the usually-unchanged full snapshot before taking either
            # search lock.  This keeps the existing index responsive while raw
            # hashes are compared and avoids repeating expensive HTML/text
            # preparation for every unchanged note.
            _raise_if_cancelled(event)
            plan = context.index.plan_upsert(
                notes,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            current_ids = {note.note_id for note in notes}
            deleted_ids = plan.existing_note_ids - current_ids

            # A Semantic query reads vector candidates and then lexical note
            # metadata. Drain it before publishing dirty state, then let Smart
            # searches continue against the internally locked FTS index.
            has_delta = bool(plan.changed or deleted_ids)
            if has_delta:
                with self._search_lock, self._engine_lock, self._state_lock:
                    _raise_if_cancelled(event)
                    context.engine.defer_vocabulary_refresh()
                    context.engine.semantic_provider = None
                    context.semantic_needs_reconcile = context.semantic is not None
                    if context.semantic is not None:
                        context.semantic_exact_delta_chain = False
                        context.semantic_force_full_refresh = True
                        context.semantic_pending_note_ids.clear()
                        context.semantic_pending_deleted_ids.clear()

            _raise_if_cancelled(event)
            changed = context.index.apply_upsert(
                plan,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            unchanged = plan.unchanged + (len(plan.changed) - changed)
            deleted = context.index.delete_notes(
                deleted_ids,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            if changed or deleted:
                replacement = SearchEngine(context.index)
                try:
                    replacement.warmup(
                        cancel_check=lambda: _raise_if_cancelled(event)
                    )
                except Exception:
                    replacement = SearchEngine(context.index)
                with self._engine_lock:
                    context.engine = replacement

            context.field_names = context.index.field_names()
            context.lexical_generation = context.index.generation

            if context.semantic is not None:
                status = context.semantic_snapshot
                if status is not None and status.runtime_ready and status.model_ready and (
                    changed or deleted
                ):
                    context.semantic_needs_reconcile = True
                    context.engine.semantic_provider = None
            context.note_count = len(notes)
            context.fingerprint = fingerprint
            _write_index_fingerprint(context.index, fingerprint)
            _raise_if_cancelled(event)
            return changed, unchanged, deleted

        def success(result: tuple[int, int, int]) -> None:
            if self._cancelled(event, token):
                self._forget_cancellation(event)
                if token == self._profile_token:
                    self._end_maintenance()
                return
            _changed, _unchanged, _deleted = result
            self._forget_cancellation(event)
            rerun = self._end_maintenance()
            self._set_index_state(
                IndexState.READY,
                detail=f"{context.note_count:,} notes ready.",
            )
            on_success(self.get_status())
            if rerun:
                self.reconcile(on_success=on_success, on_error=on_error)
            else:
                self._refresh_existing_semantic_index(context)

        self._run_query_op(
            uses_collection=False,
            op=reconcile,
            success=success,
            failure=lambda error: self._reconcile_failed(
                error, event, token, on_error
            ),
        )

    def _reconcile_failed(
        self,
        error: Exception,
        event: threading.Event,
        token: int,
        on_error: ErrorCallback,
    ) -> None:
        self._forget_cancellation(event)
        if token != self._profile_token:
            return
        rerun = self._end_maintenance()
        if not isinstance(error, _CancelledOperation) and not event.is_set():
            on_error(
                "Smart Search could not refresh automatically. "
                "Choose Refresh Smart & Exact to try again."
            )
        if rerun and self._context is not None:
            self.reconcile(on_error=on_error)

    def _maintenance_failed(
        self,
        error: Exception,
        event: threading.Event,
        token: int,
        previous_count: int,
        on_success: StatusCallback,
        on_error: ErrorCallback,
    ) -> None:
        self._forget_cancellation(event)
        if token != self._profile_token:
            return
        self._end_maintenance()
        context = self._context
        available_count = (
            int(context.note_count)
            if context is not None and context.token == token
            else int(previous_count)
        )
        if isinstance(error, _CancelledOperation) or event.is_set():
            state = IndexState.READY if available_count else IndexState.UNAVAILABLE
            self._set_index_state(state, detail="Refresh cancelled.")
            on_success(self.get_status())
            return
        state = IndexState.READY if available_count else IndexState.ERROR
        message = (
            "Refresh failed. Your current Smart & Exact search data is still available."
            if available_count
            else "Search setup failed. Try again."
        )
        self._set_index_state(
            state,
            detail=message,
        )
        on_error(message)

    def _finish_cancelled_maintenance(
        self,
        event: threading.Event,
        token: int,
        previous_count: int,
        on_success: StatusCallback,
    ) -> None:
        self._forget_cancellation(event)
        if token != self._profile_token:
            return
        self._end_maintenance()
        state = IndexState.READY if previous_count else IndexState.UNAVAILABLE
        self._set_index_state(state, detail="Refresh cancelled.")
        on_success(self.get_status())

    def _begin_maintenance(self, *, reconcile: bool = False) -> bool:
        del reconcile
        with self._state_lock:
            if (
                self._bundle_update_running
                or self._background_maintenance_paused
                or self._maintenance_running
            ):
                return False
            self._maintenance_running = True
            return True

    def _end_maintenance(self) -> bool:
        with self._state_lock:
            self._maintenance_running = False
            return False

    # ------------------------------------------------------------------
    # Optional local semantic setup

    def _lexical_work_pending(self, context: _ProfileContext) -> bool:
        """Conservatively gate vectors until durable lexical work is drained.

        A busy journal is treated as pending.  This prevents a semantic pass
        from capturing an in-between lexical generation between targeted
        maintenance batches.
        """

        with self._state_lock:
            if self._journal_writes_inflight.get(context.token, 0) > 0:
                return True
        journal = context.maintenance
        if journal is None:
            return False
        work_state = journal.try_work_state()
        return work_state is None or bool(work_state[0] or work_state[1])

    def _refresh_existing_semantic_index(self, context: _ProfileContext) -> bool:
        """Start a non-blocking refresh for an already-created vector index.

        Initial semantic setup remains a separate product decision.  This
        helper only preserves an existing semantic index after lexical changes,
        and routes every embedding pass through :meth:`index_semantic`.
        """

        if (
            context is not self._context
            or not context.semantic_needs_reconcile
            or self.background_maintenance_paused
            or self._semantic_restart_pending(context)
            or self._lexical_work_pending(context)
        ):
            return False
        semantic = context.semantic
        if semantic is None:
            return False
        with self._state_lock:
            if self._bundle_update_running:
                return False
            if self._semantic_phase is not None:
                return False
        status = context.semantic_snapshot
        if status is None:
            return False
        if (
            not status.runtime_ready
            or not status.model_ready
            or (
                status.index_count == 0
                and context.semantic_source_generation is None
            )
        ):
            return False
        return self.index_semantic() is not None

    def _queue_semantic_delta(
        self,
        context: _ProfileContext,
        changed_note_ids: Iterable[int],
        deleted_note_ids: Iterable[int],
        *,
        start: bool = True,
    ) -> bool:
        """Coalesce an exact lexical delta into one background vector update."""

        if context is not self._context:
            return False
        semantic = context.semantic
        status = context.semantic_snapshot
        if (
            semantic is None
            or status is None
            or not status.runtime_ready
            or not status.model_ready
        ):
            with self._state_lock:
                context.semantic_needs_reconcile = semantic is not None
                if semantic is not None:
                    context.semantic_exact_delta_chain = False
                    context.semantic_force_full_refresh = True
                    context.semantic_pending_note_ids.clear()
                    context.semantic_pending_deleted_ids.clear()
            return False
        with self._state_lock:
            if self._bundle_update_running:
                return False
            if (
                context.semantic_force_full_refresh
                or not context.semantic_exact_delta_chain
            ):
                context.semantic_needs_reconcile = True
                return False
            self._coalesce_semantic_delta_locked(
                context,
                changed_note_ids,
                deleted_note_ids,
            )
            context.semantic_needs_reconcile = True
            if (
                self._semantic_phase is not None
                or self._background_maintenance_paused
                or self._semantic_restart_pending(context)
                or self._lexical_work_pending(context)
                or not start
            ):
                return True
        return self._start_semantic_delta(context)

    @staticmethod
    def _coalesce_semantic_delta_locked(
        context: _ProfileContext,
        changed_note_ids: Iterable[int],
        deleted_note_ids: Iterable[int],
    ) -> None:
        """Merge exact vector intent with the newest note state winning."""

        changed = set(_positive_ids(changed_note_ids))
        deleted = set(_positive_ids(deleted_note_ids))
        # Apply changed first and deleted second so an impossible same-batch
        # conflict fails closed as a deletion.  Across batches, the later call
        # always replaces the older state (including delete -> undo/re-add).
        context.semantic_pending_deleted_ids.difference_update(changed)
        context.semantic_pending_note_ids.update(changed)
        context.semantic_pending_note_ids.difference_update(deleted)
        context.semantic_pending_deleted_ids.update(deleted)

    def _start_semantic_delta(self, context: _ProfileContext) -> bool:
        semantic = context.semantic
        if (
            semantic is None
            or context is not self._context
            or self._semantic_restart_pending(context)
            or self._lexical_work_pending(context)
        ):
            return False
        with self._state_lock:
            # Internal tests and legacy callers may have populated the exact
            # queue directly.  Establish a chain only from a generation whose
            # vector source is still provably identical to the lexical source.
            if (
                not context.semantic_exact_delta_chain
                and not context.semantic_force_full_refresh
                and not context.semantic_needs_reconcile
                and bool(
                    context.semantic_pending_note_ids
                    or context.semantic_pending_deleted_ids
                )
                and context.semantic_source_generation
                == context.lexical_generation
            ):
                context.semantic_exact_delta_chain = True
            if (
                self._bundle_update_running
                or self._background_maintenance_paused
                or self._semantic_restart_pending(context)
                or self._lexical_work_pending(context)
                or context.semantic_force_full_refresh
                or not context.semantic_exact_delta_chain
            ):
                return False
            if self._semantic_phase is not None:
                return False
            deleted_ids = tuple(sorted(context.semantic_pending_deleted_ids))[
                :_SEMANTIC_DELTA_BATCH_SIZE
            ]
            remaining = _SEMANTIC_DELTA_BATCH_SIZE - len(deleted_ids)
            changed_ids = tuple(sorted(context.semantic_pending_note_ids))[
                :remaining
            ]
            if not changed_ids and not deleted_ids:
                return False
            context.semantic_pending_note_ids.difference_update(changed_ids)
            context.semantic_pending_deleted_ids.difference_update(deleted_ids)
            self._semantic_phase = "updating"
            self._semantic_progress = 0.0
            self._semantic_phase_detail = "Updating semantic search."

        event = self._new_cancellation(kind="semantic")
        token = context.token
        lexical_generation = context.lexical_generation

        def update(_collection: Any) -> tuple[int, bool]:
            _raise_if_cancelled(event)
            indexed_documents = context.index.documents(changed_ids)
            documents = _semantic_documents_from_index(
                indexed_documents,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            stale_documents = _stale_semantic_documents(
                semantic,
                documents,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            live_ids = {document.note_id for document in documents}
            removals = tuple(
                sorted(set(deleted_ids) | (set(changed_ids) - live_ids))
            )
            if stale_documents or removals:
                # Drain the last live Semantic reader before mutating vectors.
                # New Semantic requests already see the published updating
                # phase, while Smart/Exact remain independent.
                with self._search_lock, self._engine_lock:
                    _raise_if_cancelled(event)
                    context.engine.semantic_provider = None
                    semantic.index.clear_source_generation()
            if removals:
                semantic.remove_notes(
                    removals,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )

            def progress(done: int, total: int) -> None:
                _raise_if_cancelled(event)
                fraction = done / max(1, total)
                with self._state_lock:
                    self._semantic_progress = fraction
                    self._semantic_phase_detail = (
                        f"Updating notes {done:,}/{total:,}."
                    )

            changed = (
                semantic.index_documents(
                    stale_documents,
                    progress=progress,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
                if stale_documents
                else 0
            )
            _raise_if_cancelled(event)
            vector_count = int(semantic.status().index_count)
            with self._search_lock, self._engine_lock, self._state_lock:
                _raise_if_cancelled(event)
                pending = bool(
                    context.semantic_pending_note_ids
                    or context.semantic_pending_deleted_ids
                )
                current = (
                    not pending
                    and context.semantic_exact_delta_chain
                    and not context.semantic_force_full_refresh
                    and lexical_generation == context.lexical_generation
                    and lexical_generation == context.index.generation
                    and vector_count == context.note_count
                    and not self._maintenance_running
                    and not self._lexical_work_pending(context)
                )
                if not pending and vector_count != context.note_count:
                    context.semantic_exact_delta_chain = False
                    context.semantic_force_full_refresh = True
                if current:
                    semantic.index.mark_source_generation(lexical_generation)
                    context.semantic_exact_delta_chain = False
                context.semantic_needs_reconcile = not current
                context.engine.semantic_provider = semantic if current else None
                self._semantic_phase = None
                self._semantic_progress = None
                self._semantic_phase_detail = ""
                self._refresh_semantic_snapshot(context)
                self._forget_cancellation(event)
            return changed, current

        def success(result: tuple[int, bool]) -> None:
            if self._cancelled(event, token):
                self._semantic_finished(event, token)
                self.release_semantic_runtime()
                return
            _changed, current = result
            if self._start_semantic_delta(context):
                return
            if self._finalize_semantic_delta_chain(context):
                return
            if not current:
                if not self._refresh_existing_semantic_index(context):
                    self.release_semantic_runtime()
            else:
                self.release_semantic_runtime()

        def failure(error: Exception) -> None:
            with self._state_lock:
                if not context.semantic_force_full_refresh:
                    already_pending = (
                        context.semantic_pending_note_ids
                        | context.semantic_pending_deleted_ids
                    )
                    self._coalesce_semantic_delta_locked(
                        context,
                        (
                            note_id
                            for note_id in changed_ids
                            if note_id not in already_pending
                        ),
                        (
                            note_id
                            for note_id in deleted_ids
                            if note_id not in already_pending
                        ),
                    )
            self._semantic_failed(error, event, token, context, _noop)

        self._run_query_op(
            uses_collection=False,
            op=update,
            success=success,
            failure=failure,
        )
        return True

    def _finalize_semantic_delta_chain(self, context: _ProfileContext) -> bool:
        """Publish a drained exact chain after unrelated lexical work settles.

        A journal write can arrive while the last vector batch is finishing.
        The batch must fail closed at that moment, but if the later lexical
        maintenance proves to be a no-op, re-embedding the entire profile would
        be wasteful.  The retained exact-chain invariant lets us safely publish
        the already-updated vectors once every lexical gate is clear.
        """

        semantic = context.semantic
        if semantic is None or context is not self._context:
            return False
        with self._search_lock, self._engine_lock, self._state_lock:
            if (
                self._bundle_update_running
                or self._background_maintenance_paused
                or self._semantic_phase is not None
                or self._maintenance_running
                or self._semantic_restart_pending(context)
                or self._lexical_work_pending(context)
                or context.semantic_force_full_refresh
                or not context.semantic_exact_delta_chain
                or context.semantic_pending_note_ids
                or context.semantic_pending_deleted_ids
                or context.lexical_generation != context.index.generation
            ):
                return False
            semantic.index.mark_source_generation(context.lexical_generation)
            context.semantic_needs_reconcile = False
            context.semantic_exact_delta_chain = False
            context.engine.semantic_provider = semantic
            self._refresh_semantic_snapshot(context)
        self.release_semantic_runtime()
        return True

    def install_semantic(
        self,
        *,
        runtime_only: bool = False,
        on_progress: ProgressCallback = _noop,
        on_success: StatusCallback = _noop,
        on_error: ErrorCallback = _noop,
    ) -> Callable[[], None] | None:
        with self._state_lock:
            if self._bundle_update_running:
                on_error("Restart Anki to finish the Smart Search update.")
                return None
            if self._background_maintenance_paused:
                on_error("Finish reviewing before setting up Semantic search.")
                return None
        context = self._context
        if not bool(self._read_config().get("semantic_enabled", True)):
            on_error("Semantic search is disabled in the add-on configuration.")
            return None
        if context is None or context.semantic is None:
            if context is None:
                on_error("Semantic search is unavailable.")
                return None
            try:
                context.semantic = self._open_or_recover_semantic(context.key)
                context.semantic_restart_required = bool(
                    getattr(context.semantic, "restart_required", False)
                )
                context.semantic_error = None
                context.semantic_error_recovery = None
                self._refresh_semantic_snapshot(context)
            except Exception as error:
                context.semantic_error = str(error)
                context.semantic_error_recovery = SemanticRecovery.REINDEX
                on_error("Semantic search setup could not start. Try again.")
                return None
        if self._semantic_restart_pending(context):
            on_error("Restart Anki to finish repairing Semantic search.")
            return None
        token = context.token
        semantic_status = context.semantic_snapshot
        if semantic_status is None:
            semantic_status = self._refresh_semantic_snapshot(context)
        if semantic_status is None:
            on_error("Semantic status is unavailable.")
            return None
        repair = (
            context.semantic_error_recovery is SemanticRecovery.MODEL
            or getattr(semantic_status, "error_kind", None) == "model"
        )
        with self._state_lock:
            if self._bundle_update_running:
                on_error("Restart Anki to finish the Smart Search update.")
                return None
            if self._background_maintenance_paused:
                on_error("Finish reviewing before preparing Semantic search.")
                return None
            if self._semantic_phase is not None:
                on_error("Semantic setup is already running.")
                return None
            self._semantic_phase = "installing"
            self._semantic_progress = 0.0
            self._semantic_phase_detail = "Setting up semantic search."

        event = self._new_cancellation(kind="semantic")
        context.semantic_error = None
        context.semantic_error_recovery = None

        def progress(_name: str, completed: int, total: int) -> None:
            _raise_if_cancelled(event)
            fraction = min(1.0, max(0.0, completed / max(1, total)))
            with self._state_lock:
                self._semantic_progress = fraction
                self._semantic_phase_detail = "Setting up semantic search."
            on_progress(fraction, "setting up semantic search")

        def install(_collection: Any) -> bool:
            _raise_if_cancelled(event)
            if runtime_only:
                context.semantic.install_runtime(
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
            else:
                context.semantic.install(
                    progress,
                    repair=repair,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
            context.semantic_restart_required = bool(
                getattr(context.semantic, "restart_required", False)
            )
            self._refresh_semantic_snapshot(context)
            _raise_if_cancelled(event)
            return bool(getattr(context.semantic, "restart_required", False))

        def success(restart_required: bool) -> None:
            if self._cancelled(event, token):
                self._semantic_finished(event, token)
                self.release_semantic_runtime()
                return
            context.semantic_restart_required = bool(restart_required)
            context.semantic_needs_reconcile = not restart_required
            context.semantic_error = None
            context.semantic_error_recovery = None
            self._semantic_finished(event, token)
            on_progress(1.0, "semantic search setup complete")
            on_success(self.get_status())

        self._run_query_op(
            uses_collection=False,
            op=install,
            success=success,
            failure=lambda error: self._semantic_failed(
                error, event, token, context, on_error
            ),
        )
        return event.set

    def index_semantic(
        self,
        *,
        on_progress: ProgressCallback = _noop,
        on_success: StatusCallback = _noop,
        on_error: ErrorCallback = _noop,
    ) -> Callable[[], None] | None:
        with self._state_lock:
            if self._bundle_update_running:
                on_error("Restart Anki to finish the Smart Search update.")
                return None
        context = self._context
        if not bool(self._read_config().get("semantic_enabled", True)):
            on_error("Semantic search is disabled in the add-on configuration.")
            return None
        if context is None or context.semantic is None:
            if context is None:
                on_error("Semantic search is unavailable.")
                return None
            try:
                context.semantic = self._open_or_recover_semantic(context.key)
                context.semantic_restart_required = bool(
                    getattr(context.semantic, "restart_required", False)
                )
                context.semantic_error = None
                context.semantic_error_recovery = None
                self._refresh_semantic_snapshot(context)
            except Exception as error:
                context.semantic_error = str(error)
                context.semantic_error_recovery = SemanticRecovery.REINDEX
                on_error("Semantic search preparation could not start. Try again.")
                return None
        if self._semantic_restart_pending(context):
            on_error("Restart Anki to finish repairing Semantic search.")
            return None
        lexical_pending = self._lexical_work_pending(context)
        with self._state_lock:
            text_index_ready = (
                not self._bundle_update_running
                and not self._background_maintenance_paused
                and self._index_state is IndexState.READY
                and not self._maintenance_running
                and not lexical_pending
            )
        if not text_index_ready:
            on_error(
                "Wait for Smart & Exact setup to finish before preparing "
                "Semantic search."
            )
            return None
        semantic_status = context.semantic_snapshot
        if semantic_status is None:
            semantic_status = self._refresh_semantic_snapshot(context)
        if semantic_status is None:
            on_error("Semantic status is unavailable.")
            return None
        if not semantic_status.runtime_ready or not semantic_status.model_ready:
            on_error("Set up Semantic search first.")
            return None
        with self._state_lock:
            if self._bundle_update_running:
                claim_error = "Restart Anki to finish the Smart Search update."
            elif self._background_maintenance_paused:
                claim_error = "Finish reviewing before preparing Semantic search."
            elif (
                self._index_state is not IndexState.READY
                or self._maintenance_running
                or self._lexical_work_pending(context)
            ):
                claim_error = (
                    "Wait for Smart & Exact setup to finish before preparing "
                    "Semantic search."
                )
            elif self._semantic_phase is not None:
                claim_error = "Semantic setup is already running."
            else:
                claim_error = None
                self._semantic_phase = "indexing"
                self._semantic_progress = 0.0
                self._semantic_phase_detail = "Reading local search data."
                # A full pass supersedes every queued exact delta.  Keep the
                # full-refresh flag set until its captured lexical generation
                # is atomically proven current at publication time.
                context.semantic_force_full_refresh = True
                context.semantic_exact_delta_chain = False
                context.semantic_pending_note_ids.clear()
                context.semantic_pending_deleted_ids.clear()
        if claim_error is not None:
            on_error(claim_error)
            return None

        event = self._new_cancellation(kind="semantic")
        token = context.token
        lexical_generation = context.lexical_generation
        context.semantic_error = None
        context.semantic_error_recovery = None
        self._index_semantic_from_lexical_batches(
            context,
            event,
            token,
            lexical_generation,
            on_progress,
            on_success,
            on_error,
        )
        return event.set

    def _index_semantic_from_lexical_batches(
        self,
        context: _ProfileContext,
        event: threading.Event,
        token: int,
        lexical_generation: int,
        on_progress: ProgressCallback,
        on_success: StatusCallback,
        on_error: ErrorCallback,
        *,
        batch_size: int = 250,
    ) -> None:
        """Prepare a full vector generation without retaining all note text."""

        def index(_collection: Any) -> tuple[int, bool]:
            _raise_if_cancelled(event)
            semantic = context.semantic
            if semantic is None:
                raise RuntimeError("Semantic search is unavailable.")
            with self._search_lock, self._engine_lock:
                _raise_if_cancelled(event)
                context.engine.semantic_provider = None
                semantic.index.clear_source_generation()
                context.semantic_needs_reconcile = True

            note_ids = context.index.note_ids()
            current_ids = set(note_ids)
            known_ids = set(
                semantic.index.known_hashes(
                    cancel_check=lambda: _raise_if_cancelled(event)
                )
            )
            semantic.remove_notes(
                tuple(sorted(known_ids - current_ids)),
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            total = len(note_ids)
            changed = 0
            size = max(1, int(batch_size))
            for start in range(0, total, size):
                _raise_if_cancelled(event)
                ids = note_ids[start : start + size]
                documents = _semantic_documents_from_index(
                    context.index.documents(ids),
                    cancel_check=lambda: _raise_if_cancelled(event),
                )

                def progress(done: int, _batch_total: int) -> None:
                    _raise_if_cancelled(event)
                    completed = min(total, start + done)
                    fraction = completed / max(1, total)
                    with self._state_lock:
                        self._semantic_progress = fraction
                        self._semantic_phase_detail = (
                            f"Preparing notes {completed:,}/{total:,}."
                        )
                    on_progress(
                        fraction,
                        f"preparing notes {completed:,}/{total:,}",
                    )

                changed += semantic.index_documents(
                    documents,
                    progress=progress,
                    cancel_check=lambda: _raise_if_cancelled(event),
                )
                completed = min(total, start + len(ids))
                fraction = completed / max(1, total)
                with self._state_lock:
                    self._semantic_progress = fraction
                    self._semantic_phase_detail = (
                        f"Preparing notes {completed:,}/{total:,}."
                    )
                on_progress(
                    fraction,
                    f"preparing notes {completed:,}/{total:,}",
                )
                time.sleep(0)

            vector_count = int(semantic.status().index_count)
            with self._search_lock, self._engine_lock, self._state_lock:
                _raise_if_cancelled(event)
                current = (
                    lexical_generation == context.lexical_generation
                    and lexical_generation == context.index.generation
                    and vector_count == context.note_count
                    and not self._maintenance_running
                    and not self._lexical_work_pending(context)
                    and not context.semantic_pending_note_ids
                    and not context.semantic_pending_deleted_ids
                )
                if current:
                    semantic.index.mark_source_generation(lexical_generation)
                    context.semantic_force_full_refresh = False
                    context.semantic_exact_delta_chain = False
                    context.semantic_pending_note_ids.clear()
                    context.semantic_pending_deleted_ids.clear()
                context.semantic_needs_reconcile = not current
                context.engine.semantic_provider = semantic if current else None
                self._semantic_phase = None
                self._semantic_progress = None
                self._semantic_phase_detail = ""
                self._refresh_semantic_snapshot(context)
                self._forget_cancellation(event)
            return changed, current

        def success(result: tuple[int, bool]) -> None:
            if self._cancelled(event, token):
                self._semantic_finished(event, token)
                self.release_semantic_runtime()
                return
            _changed, current = result
            on_progress(1.0, "semantic search ready")
            on_success(self.get_status())
            if not current:
                if not self._start_semantic_delta(context):
                    if not self._refresh_existing_semantic_index(context):
                        self.release_semantic_runtime()
            else:
                self.release_semantic_runtime()

        self._run_query_op(
            uses_collection=False,
            op=index,
            success=success,
            failure=lambda error: self._semantic_failed(
                error, event, token, context, on_error
            ),
        )

    def _index_semantic_snapshot(
        self,
        context: _ProfileContext,
        notes: list[IndexedNote],
        event: threading.Event,
        token: int,
        lexical_generation: int,
        on_progress: ProgressCallback,
        on_success: StatusCallback,
        on_error: ErrorCallback,
    ) -> None:
        """Compatibility seam for tests and callers holding note snapshots."""

        self._run_query_op(
            uses_collection=False,
            op=lambda _collection: _semantic_documents(
                notes,
                cancel_check=lambda: _raise_if_cancelled(event),
            ),
            success=lambda documents: self._index_semantic_documents(
                context,
                documents,
                event,
                token,
                lexical_generation,
                on_progress,
                on_success,
                on_error,
            ),
            failure=lambda error: self._semantic_failed(
                error, event, token, context, on_error
            ),
        )

    def _index_semantic_documents(
        self,
        context: _ProfileContext,
        documents: list[SemanticDocument],
        event: threading.Event,
        token: int,
        lexical_generation: int,
        on_progress: ProgressCallback,
        on_success: StatusCallback,
        on_error: ErrorCallback,
    ) -> None:
        def index(_collection: Any) -> tuple[int, bool]:
            # Semantic indexing writes only to its own disposable vector
            # files.  Do not hold ``_engine_lock`` for this multi-minute job:
            # lexical Smart searches need that lock briefly to use the FTS
            # engine, and Semantic mode is already detached while this phase
            # is active.  The semantic phase guard prevents a second semantic
            # writer from starting concurrently.
            _raise_if_cancelled(event)
            # A search may have captured the old semantic provider immediately
            # before the phase changed.  Wait for that reader, then detach the
            # provider under the same lock order used by searches before any
            # vector file is changed.
            with self._search_lock, self._engine_lock:
                _raise_if_cancelled(event)
                semantic = context.semantic
                if semantic is None:
                    raise RuntimeError("Semantic search is unavailable.")
                context.engine.semantic_provider = None
                semantic.index.clear_source_generation()
                context.semantic_needs_reconcile = True
            known_ids = set(
                semantic.index.known_hashes(
                    cancel_check=lambda: _raise_if_cancelled(event)
                )
            )
            current_ids = {document.note_id for document in documents}
            semantic.remove_notes(
                tuple(known_ids - current_ids),
                cancel_check=lambda: _raise_if_cancelled(event),
            )

            def progress(done: int, total: int) -> None:
                _raise_if_cancelled(event)
                fraction = done / max(1, total)
                with self._state_lock:
                    self._semantic_progress = fraction
                    self._semantic_phase_detail = (
                        f"Preparing notes {done:,}/{total:,}."
                    )
                on_progress(fraction, f"preparing notes {done:,}/{total:,}")

            changed = semantic.index_documents(
                documents,
                progress=progress,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            _raise_if_cancelled(event)
            vector_count = int(semantic.status().index_count)
            # Publish the completed generation in this worker.  QueryOp
            # success callbacks run on Anki's GUI thread and must never wait
            # for a live semantic search to release these locks.
            with self._search_lock, self._engine_lock, self._state_lock:
                _raise_if_cancelled(event)
                current = (
                    lexical_generation == context.lexical_generation
                    and lexical_generation == context.index.generation
                    and vector_count == context.note_count
                    and not self._maintenance_running
                    and not self._lexical_work_pending(context)
                    and not context.semantic_pending_note_ids
                    and not context.semantic_pending_deleted_ids
                )
                if current:
                    semantic.index.mark_source_generation(lexical_generation)
                    context.semantic_force_full_refresh = False
                    context.semantic_exact_delta_chain = False
                    context.semantic_pending_note_ids.clear()
                    context.semantic_pending_deleted_ids.clear()
                context.semantic_needs_reconcile = not current
                context.engine.semantic_provider = semantic if current else None
                self._semantic_phase = None
                self._semantic_progress = None
                self._semantic_phase_detail = ""
                self._refresh_semantic_snapshot(context)
                self._forget_cancellation(event)
            return changed, current

        def success(result: tuple[int, bool]) -> None:
            if self._cancelled(event, token):
                self._semantic_finished(event, token)
                self.release_semantic_runtime()
                return
            _changed, current = result
            on_progress(1.0, "semantic search ready")
            on_success(self.get_status())
            if not current:
                if not self._start_semantic_delta(context):
                    if not self._refresh_existing_semantic_index(context):
                        self.release_semantic_runtime()
            else:
                self.release_semantic_runtime()

        self._run_query_op(
            uses_collection=False,
            op=index,
            success=success,
            failure=lambda error: self._semantic_failed(
                error, event, token, context, on_error
            ),
        )

    def _semantic_failed(
        self,
        error: Exception,
        event: threading.Event,
        token: int,
        context: _ProfileContext,
        on_error: ErrorCallback,
    ) -> None:
        if token != self._profile_token:
            self._forget_cancellation(event)
            return
        cancelled = isinstance(error, _CancelledOperation) or event.is_set()
        if not cancelled:
            context.semantic_error = str(error)
            # The cached snapshot intentionally avoids file and SQLite reads on
            # Anki's GUI thread, but it predates the operation that just failed.
            # Read the service's in-memory recovery hint first so a verified
            # runtime/model failure is not mislabeled as a reindex failure.
            service_kind = getattr(context.semantic, "last_error_kind", None)
            if service_kind is None:
                service_kind = getattr(
                    context.semantic_snapshot, "error_kind", None
                )
            with self._state_lock:
                phase = self._semantic_phase
            context.semantic_error_recovery = (
                SemanticRecovery.MODEL
                if service_kind == "model" or phase == "installing"
                else SemanticRecovery.REINDEX
            )
        self._semantic_finished(event, token)
        self.release_semantic_runtime()
        if cancelled:
            try:
                self._semantic_cancelled_callback()
            except Exception:
                pass
        else:
            recovery = (
                context.semantic_error_recovery or SemanticRecovery.REINDEX
            )
            on_error(
                "Semantic search needs repair. Try Repair Semantic Search."
                if recovery is SemanticRecovery.MODEL
                else "Semantic search preparation did not finish. Try again."
            )

    def _semantic_finished(self, event: threading.Event, token: int) -> None:
        self._forget_cancellation(event)
        if token != self._profile_token:
            return
        with self._state_lock:
            self._semantic_phase = None
            self._semantic_progress = None
            self._semantic_phase_detail = ""

    # ------------------------------------------------------------------
    # QueryOp and cancellation primitives

    def _run_query_op(
        self,
        *,
        uses_collection: bool,
        op: Callable[[Any], Any],
        success: Callable[[Any], None],
        failure: Callable[[Exception], None],
        allow_during_bundle_update: bool = False,
    ) -> None:
        """Schedule one official Anki ``QueryOp``.

        This small seam is intentionally overrideable in tests.  QueryOps that
        touch the collection remain serialized; external FTS/model work opts
        out with ``without_collection()``.
        """

        with self._state_lock:
            blocked = (
                self._bundle_update_running
                and not allow_during_bundle_update
            )
            if not blocked:
                self._background_op_count += 1
        if blocked:
            failure(
                _BundleUpdatePending(
                    "Restart Anki to finish the Smart Search update."
                )
            )
            return

        def finish() -> None:
            with self._state_lock:
                self._background_op_count = max(
                    0,
                    self._background_op_count - 1,
                )

        def succeeded(result: Any) -> None:
            try:
                success(result)
            finally:
                finish()

        def failed(error: Exception) -> None:
            try:
                failure(error)
            finally:
                finish()

        try:
            from aqt.operations import QueryOp

            operation = QueryOp(
                parent=self.mw,
                op=op,
                success=succeeded,
            ).failure(failed)
            if not uses_collection:
                operation.without_collection()
            operation.run_in_background()
        except Exception:
            finish()
            raise

    def _new_cancellation(self, *, kind: str = "interactive") -> threading.Event:
        event = threading.Event()
        with self._state_lock:
            if self._bundle_update_running or (
                kind in {"maintenance", "semantic"}
                and self._background_maintenance_paused
            ):
                event.set()
            else:
                self._active_cancellations.add(event)
                if kind == "maintenance":
                    self._maintenance_cancellations.add(event)
                elif kind == "semantic":
                    self._semantic_cancellations.add(event)
        return event

    def _forget_cancellation(self, event: threading.Event) -> None:
        with self._state_lock:
            self._active_cancellations.discard(event)
            self._maintenance_cancellations.discard(event)
            self._semantic_cancellations.discard(event)

    def set_background_maintenance_paused(self, paused: bool) -> None:
        """Apply one central host-activity gate to every background writer.

        Reviewer transitions use this before any timer callback can enqueue a
        collection read.  Smart and Exact searches remain independent, while
        Semantic inference is cancelled so its disposable worker cannot
        compete with reviews or a temporary collection close.
        """

        events: tuple[threading.Event, ...] = ()
        with self._state_lock:
            self._background_maintenance_paused = bool(paused)
            if paused:
                events = tuple(
                    self._maintenance_cancellations
                    | self._semantic_cancellations
                )
        for event in events:
            event.set()

    @property
    def background_maintenance_paused(self) -> bool:
        with self._state_lock:
            return self._background_maintenance_paused

    def release_semantic_runtime(self) -> bool:
        """Release the optional native model without touching the collection."""

        context = self._context
        semantic = context.semantic if context is not None else None
        unload = getattr(semantic, "unload", None)
        if not callable(unload):
            return False
        with self._state_lock:
            if self._semantic_phase is not None:
                return False

        def release(_collection: Any) -> None:
            with self._search_lock:
                with self._state_lock:
                    semantic_busy = self._semantic_phase is not None
                if context is self._context and not semantic_busy:
                    unload()

        try:
            self._run_query_op(
                uses_collection=False,
                op=release,
                success=_noop,
                failure=_noop,
            )
        except Exception:
            return False
        return True

    def abort_semantic_runtime_now(self) -> bool:
        """Stop inference immediately without queueing behind its worker."""

        context = self._context
        semantic = context.semantic if context is not None else None
        abort = getattr(semantic, "abort_now", None)
        if not callable(abort):
            return False
        try:
            return bool(abort())
        except Exception:
            return False

    def resume_pending_background_work(self) -> bool:
        """Resume semantic deltas retained while the reviewer gate was closed."""

        if self.background_maintenance_paused:
            return False
        context = self._context
        if context is None:
            return False
        if (
            self._semantic_restart_pending(context)
            or self._lexical_work_pending(context)
        ):
            return False
        with self._state_lock:
            force_full_refresh = context.semantic_force_full_refresh
        if force_full_refresh:
            return self._refresh_existing_semantic_index(context)
        if self._start_semantic_delta(context):
            return True
        if self._finalize_semantic_delta_chain(context):
            return True
        return self._refresh_existing_semantic_index(context)

    def _cancelled(self, event: threading.Event, token: int) -> bool:
        return event.is_set() or token != self._profile_token

    def refresh_vocabulary(
        self,
        *,
        on_success: Callable[[bool], None] = _noop,
        on_error: ErrorCallback = _noop,
    ) -> bool:
        """Build and atomically swap a fresh fuzzy engine off-thread."""

        context = self._context
        if context is None:
            return False
        with self._state_lock:
            if (
                self._bundle_update_running
                or self._background_maintenance_paused
                or self._vocabulary_refresh_running
            ):
                return False
            self._vocabulary_refresh_running = True
            self._vocabulary_refresh_token = context.token
        token = context.token
        generation = context.index.generation
        event = self._new_cancellation(kind="maintenance")

        def build(_collection: Any) -> bool:
            replacement = SearchEngine(context.index)
            replacement.warmup(
                cancel_check=lambda: _raise_if_cancelled(event)
            )
            with self._engine_lock:
                if (
                    token != self._profile_token
                    or context is not self._context
                    or generation != context.index.generation
                ):
                    return False
                replacement.semantic_provider = (
                    context.engine.semantic_provider
                )
                context.engine = replacement
            return True

        def success(current: bool) -> None:
            self._forget_cancellation(event)
            with self._state_lock:
                if self._vocabulary_refresh_token == token:
                    self._vocabulary_refresh_running = False
                    self._vocabulary_refresh_token = None
            if token == self._profile_token:
                on_success(bool(current))

        def failure(error: Exception) -> None:
            self._forget_cancellation(event)
            with self._state_lock:
                if self._vocabulary_refresh_token == token:
                    self._vocabulary_refresh_running = False
                    self._vocabulary_refresh_token = None
            if token == self._profile_token:
                on_error(str(error))

        self._run_query_op(
            uses_collection=False,
            op=build,
            success=success,
            failure=failure,
        )
        return True

    def _warm_engine(self, context: _ProfileContext) -> None:
        if self.background_maintenance_paused:
            return
        token = context.token
        event = self._new_cancellation(kind="maintenance")

        def warm(_collection: Any) -> None:
            with self._search_lock, self._engine_lock:
                if token != self._profile_token:
                    return
                context.engine.warmup(
                    cancel_check=lambda: _raise_if_cancelled(event)
                )

        def finished(_result: Any) -> None:
            self._forget_cancellation(event)

        def failed(_error: Exception) -> None:
            self._forget_cancellation(event)

        self._run_query_op(
            uses_collection=False,
            op=warm,
            # Semantic runtime startup is intentionally demand-driven.  A
            # profile open should never reserve the model's native memory.
            success=finished,
            failure=failed,
        )

    def warm_lexical_on_demand(self) -> bool:
        """Prepare typo matching only after the user opens Smart Search."""

        context = self._context
        if context is None or self.background_maintenance_paused:
            return False
        if bool(getattr(context.engine, "_vocabulary_refresh_deferred", False)):
            return self.refresh_vocabulary()
        self._warm_engine(context)
        return True

class SmartSearchAddonController:
    """Own Anki hooks, debounce reconciliation, and the reusable dialog."""

    def __init__(
        self,
        mw: Any,
        *,
        bundle_root: str | Path | None = None,
        addon_module: str | None = None,
    ) -> None:
        self.mw = mw
        self.backend = AnkiSearchBackend(
            mw,
            bundle_root=bundle_root,
            addon_module=addon_module,
        )
        self._started = False
        self._dialog: Any | None = None
        self._ui_controller: Any | None = None
        self._previewer: Any | None = None
        self._preview_open_timer: Any | None = None
        self._pending_preview_result: object | None = None
        self._preview_auto_suppressed = False
        self._pending_undo: _PendingUndo | None = None
        self._undo_in_flight = False
        self._dialog_close_in_progress = False
        self._dialog_close_callbacks: list[Callable[[], None]] = []
        self._reconcile_timer: Any | None = None
        self._reconcile_request_lock = threading.Lock()
        self._pending_reconcile_note_ids: set[int] = set()
        self._pending_reconcile_deleted_ids: set[int] = set()
        self._reconcile_full = False
        self._reconcile_startup_check = False
        self._reconcile_intent_epoch = 0
        self._reconcile_retry_count = 0
        self._auto_reconcile = True
        self._reconcile_debounce_ms = _DEFAULT_DEBOUNCE_MS
        self._startup_reconcile_delay_ms = 15_000
        self._vocabulary_refresh_delay_ms = 5_000
        self._captured_note_lock = threading.Lock()
        self._captured_note_ids: deque[int] = deque()
        self._completed_added_ids: deque[int] = deque()
        self._captured_deleted_ids: set[int] = set()
        self._semantic_autostart_timer: Any | None = None
        self._semantic_autostart_token: int | None = None
        self._semantic_autostart_attempted_token: int | None = None
        self._initial_setup_deferred = False
        self._dialog_refresh_timer: Any | None = None
        self._dialog_refresh_lock = threading.Lock()
        self._dialog_refresh_queued = False
        self._dialog_refresh_last = 0.0
        self._card_state_refresh_timer: Any | None = None
        self._vocabulary_refresh_timer: Any | None = None
        self._semantic_unload_timer: Any | None = None
        self._post_review_resume_timer: Any | None = None
        self._review_active = _host_state_name(getattr(mw, "state", "")) == "review"
        self._collection_temporarily_closed = False
        self._update_check_running = False
        self._update_profile_was_active = False
        self._hooks: list[tuple[Any, Callable[..., Any]]] = []

    def start(self) -> "SmartSearchAddonController":
        if self._started:
            return self
        from anki import hooks as anki_hooks
        from aqt import gui_hooks
        from aqt.qt import QTimer

        self._started = True
        self._append_named_hook(gui_hooks, "profile_did_open", self._on_profile_open)
        self._append_named_hook(
            gui_hooks,
            "profile_will_close",
            self._on_profile_will_close,
        )
        self._append_named_hook(
            gui_hooks,
            "operation_did_execute",
            self._on_operation,
        )
        self._append_named_hook(
            gui_hooks,
            "state_will_change",
            self._on_state_will_change,
        )
        self._append_named_hook(
            gui_hooks,
            "state_did_change",
            self._on_state_did_change,
        )
        self._append_named_hook(
            gui_hooks,
            "add_cards_did_add_note",
            self._capture_added_note,
        )
        self._append_named_hook(gui_hooks, "sync_did_finish", self._on_sync_finished)
        self._append_named_hook(
            gui_hooks,
            "collection_will_temporarily_close",
            self._on_collection_will_temporarily_close,
        )
        self._append_named_hook(
            gui_hooks,
            "collection_did_temporarily_close",
            self._on_collection_reopened,
        )
        self._append_named_hook(
            anki_hooks,
            "note_will_flush",
            self._capture_note_flush,
        )
        self._append_named_hook(
            anki_hooks,
            "notes_will_be_deleted",
            self._capture_deleted_notes,
        )

        self._reconcile_timer = QTimer(self.mw)
        self._reconcile_timer.setSingleShot(True)
        self._reconcile_timer.timeout.connect(self._run_reconcile)

        self._semantic_autostart_timer = QTimer(self.mw)
        self._semantic_autostart_timer.setSingleShot(True)
        self._semantic_autostart_timer.timeout.connect(
            self._run_semantic_autostart
        )

        self._dialog_refresh_timer = QTimer(self.mw)
        self._dialog_refresh_timer.setSingleShot(True)
        self._dialog_refresh_timer.timeout.connect(
            self._flush_queued_dialog_refresh
        )

        self._preview_open_timer = QTimer(self.mw)
        self._preview_open_timer.setSingleShot(True)
        self._preview_open_timer.timeout.connect(
            self._open_pending_previewer
        )

        self._card_state_refresh_timer = QTimer(self.mw)
        self._card_state_refresh_timer.setSingleShot(True)
        self._card_state_refresh_timer.timeout.connect(
            self._refresh_visible_card_states_now
        )

        self._vocabulary_refresh_timer = QTimer(self.mw)
        self._vocabulary_refresh_timer.setSingleShot(True)
        self._vocabulary_refresh_timer.timeout.connect(
            self._run_vocabulary_refresh
        )

        self._semantic_unload_timer = QTimer(self.mw)
        self._semantic_unload_timer.setSingleShot(True)
        self._semantic_unload_timer.timeout.connect(
            self.backend.release_semantic_runtime
        )
        self._post_review_resume_timer = QTimer(self.mw)
        self._post_review_resume_timer.setSingleShot(True)
        self._post_review_resume_timer.timeout.connect(
            self._resume_after_review
        )
        self.backend.set_semantic_activity_callback(
            lambda active: self._run_on_main(
                lambda: self._semantic_activity_changed(active)
            )
        )
        self.backend.set_semantic_cancelled_callback(
            lambda: self._run_on_main(self._semantic_maintenance_cancelled)
        )
        self.backend.set_background_maintenance_paused(self._review_active)

        if getattr(self.mw, "col", None) is not None:
            self._on_profile_open()
        return self

    def shutdown(self) -> None:
        if not self._started:
            return
        if self._reconcile_timer is not None:
            self._reconcile_timer.stop()
        if self._semantic_autostart_timer is not None:
            self._semantic_autostart_timer.stop()
        if self._dialog_refresh_timer is not None:
            self._dialog_refresh_timer.stop()
        if self._preview_open_timer is not None:
            self._preview_open_timer.stop()
        if self._card_state_refresh_timer is not None:
            self._card_state_refresh_timer.stop()
        if self._vocabulary_refresh_timer is not None:
            self._vocabulary_refresh_timer.stop()
        if self._semantic_unload_timer is not None:
            self._semantic_unload_timer.stop()
        if self._post_review_resume_timer is not None:
            self._post_review_resume_timer.stop()
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
        self._pending_preview_result = None
        self._clear_pending_reconciles()
        self._clear_captured_notes()
        self._semantic_autostart_token = None
        self._semantic_autostart_attempted_token = None
        self._close_dialog()
        self.backend.deactivate_profile()
        self.backend.set_semantic_activity_callback(None)
        self.backend.set_semantic_cancelled_callback(None)
        for hook, callback in reversed(self._hooks):
            try:
                hook.remove(callback)
            except ValueError:
                pass
        self._hooks.clear()
        self._started = False

    def _maintenance_blocked(self) -> bool:
        """True while background work would compete with core reviewing."""

        current = _host_state_name(getattr(self.mw, "state", ""))
        return (
            self._collection_temporarily_closed
            or self._review_active
            or current == "review"
        )

    def _set_backend_maintenance_pause(self, paused: bool) -> None:
        setter = getattr(self.backend, "set_background_maintenance_paused", None)
        if callable(setter):
            setter(bool(paused))

    def _on_state_will_change(self, new_state: Any, *_args: Any) -> None:
        if _host_state_name(new_state) == "review":
            self._enter_review_mode()

    def _on_state_did_change(self, new_state: Any, *_args: Any) -> None:
        if _host_state_name(new_state) == "review":
            self._enter_review_mode()
        elif self._review_active:
            self._leave_review_mode()

    def _enter_review_mode(self) -> None:
        """Stop all unsolicited index/model/UI work before reviews begin."""

        if self._review_active:
            return
        self._review_active = True
        self._set_backend_maintenance_pause(True)
        for timer in (
            self._reconcile_timer,
            self._semantic_autostart_timer,
            self._dialog_refresh_timer,
            self._vocabulary_refresh_timer,
            self._card_state_refresh_timer,
            self._preview_open_timer,
            self._semantic_unload_timer,
            self._post_review_resume_timer,
        ):
            if timer is not None:
                timer.stop()
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
        self._pending_preview_result = None
        if not self._dialog_is_visible():
            self._close_previewer(save=False)
        abort = getattr(self.backend, "abort_semantic_runtime_now", None)
        if callable(abort):
            abort()
        else:
            release = getattr(self.backend, "release_semantic_runtime", None)
            if callable(release):
                release()

    def _leave_review_mode(self) -> None:
        """Resume queued maintenance only after the reviewer has settled."""

        self._review_active = False
        still_paused = self._collection_temporarily_closed
        self._set_backend_maintenance_pause(still_paused)
        if not still_paused and self._post_review_resume_timer is not None:
            self._post_review_resume_timer.start(
                _POST_REVIEW_MAINTENANCE_DELAY_MS
            )
        self._schedule_semantic_autostart(
            delay_ms=_DEFAULT_SEMANTIC_AUTOSTART_DELAY_MS
        )

    def _resume_after_review(self) -> None:
        """Restart durable work after the review UI has had time to settle."""

        if self._maintenance_blocked():
            return
        if self._initial_setup_deferred:
            if not self.backend.active:
                self._initial_setup_deferred = False
                self._on_profile_open()
                return
            context = self.backend._context
            status = self.backend.get_status()
            if (
                context is not None
                and context.note_count > 0
                and status.state is IndexState.READY
            ):
                self._initial_setup_deferred = False
            else:
                with self.backend._state_lock:
                    maintenance_running = self.backend._maintenance_running
                if maintenance_running:
                    if self._post_review_resume_timer is not None:
                        self._post_review_resume_timer.start(250)
                    return
                self._initial_setup_deferred = False
                started = self.backend.rebuild_index(
                    on_progress=lambda _fraction, _detail: self._queue_dialog_refresh(),
                    on_success=lambda _status: self._profile_activation_ready(_status),
                    on_error=self._show_error,
                )
                if started is not None:
                    return
                self._initial_setup_deferred = True
                if self._post_review_resume_timer is not None:
                    self._post_review_resume_timer.start(250)
                return
        context = self.backend._context
        journal = context.maintenance if context is not None else None
        with self._reconcile_request_lock:
            pending = bool(
                self._pending_reconcile_note_ids
                or self._pending_reconcile_deleted_ids
                or self._reconcile_full
                or self._reconcile_startup_check
            )
        if journal is not None:
            work_state = journal.try_work_state()
            if work_state is None:
                if self._post_review_resume_timer is not None:
                    self._post_review_resume_timer.start(100)
                return
            pending = pending or bool(work_state[0] or work_state[1])
        if pending and self._reconcile_timer is not None:
            self._reconcile_timer.start(25)
            return
        resume = getattr(self.backend, "resume_pending_background_work", None)
        resumed = bool(resume()) if callable(resume) else False
        if not resumed:
            self._schedule_semantic_autostart(delay_ms=250)

    def _arm_semantic_idle_unload(self) -> None:
        timer = self._semantic_unload_timer
        if timer is None:
            return
        if self._maintenance_blocked() or not self._dialog_is_visible():
            timer.stop()
            abort = getattr(self.backend, "abort_semantic_runtime_now", None)
            if callable(abort):
                abort()
            return
        timer.start(_SEMANTIC_IDLE_UNLOAD_MS)

    def _semantic_activity_changed(self, active: bool) -> None:
        """Stop the idle countdown during inference; arm it after completion."""

        timer = self._semantic_unload_timer
        if timer is None:
            return
        if active:
            timer.stop()
            return
        self._arm_semantic_idle_unload()

    def _semantic_maintenance_cancelled(self) -> None:
        """Re-arm canceled first builds/deltas after their worker has exited."""

        self._semantic_autostart_attempted_token = None
        if (
            not self._maintenance_blocked()
            and self._post_review_resume_timer is not None
        ):
            self._post_review_resume_timer.start(250)

    def _dialog_is_visible(self) -> bool:
        dialog = self._dialog
        if dialog is None:
            return False
        is_visible = getattr(dialog, "isVisible", None)
        if not callable(is_visible):
            # Lightweight test doubles and older wrappers may omit it.  A live
            # dialog reference is the conservative interactive assumption.
            return True
        try:
            return bool(is_visible())
        except RuntimeError:
            return False

    def show_search(self) -> None:
        """Open or focus the keyboard-first Smart Search palette."""

        if self.backend.bundle_update_running:
            self._show_message(
                "Restart Anki to finish the Smart Search update."
            )
            return
        if self._dialog_close_in_progress:
            self._show_message(
                "Smart Search is finishing a save. Try again in a moment."
            )
            return
        if not self.backend.active:
            if self.backend.get_status().state is not IndexState.BUILDING:
                self.backend.activate_profile_async(
                    auto_rebuild=True,
                    on_ready=self._profile_activation_ready,
                    on_error=lambda _message: self._refresh_dialog(),
                )
        if not self.backend.active:
            self._show_message(
                "Smart Search is opening in the background. Try again in a moment."
            )
            return

        if self._dialog is None:
            from .ui.controller import SearchController
            from .ui.dialog import SearchDialog

            about = AboutInfo(
                product_name="Smart Search for Anki — Medical",
                creator="Saleh Mostafa",
                version=self._addon_version(),
                logo_path=str(
                    self.backend.bundle_root / "resources" / "medbrevia-logo.png"
                ),
                website_url="https://medbrevia.com/app",
                feedback_url="mailto:product@medbrevia.com",
                privacy_url="https://medbrevia.com/legal/smart-search-privacy",
                can_check_for_updates=self._native_update_available(),
            )
            dialog = SearchDialog(self.mw, about=about)
            ui_controller = SearchController(self.backend, dialog)
            ui_controller.set_browser_opener(
                lambda result: self._open_results_in_browser_safely((result,))
            )
            ui_controller.set_multi_browser_opener(
                self._open_results_in_browser_safely
            )
            ui_controller.semanticInstallRequested.connect(self._install_semantic)
            ui_controller.semanticIndexRequested.connect(self._index_semantic)
            ui_controller.updateRequested.connect(self._check_for_updates)
            ui_controller.textIndexRebuilt.connect(self._text_index_rebuilt)
            ui_controller.flagRequested.connect(self._flag_results)
            ui_controller.suspensionRequested.connect(
                self._set_results_suspended
            )
            ui_controller.burialRequested.connect(self._set_results_buried)
            ui_controller.createCopyRequested.connect(
                self._create_result_copy
            )
            ui_controller.deckChangeRequested.connect(
                self._change_results_deck
            )
            ui_controller.tagActionRequested.connect(self._change_result_tags)
            ui_controller.undoRequested.connect(self._undo_last_change)
            ui_controller.previewPreferenceChanged.connect(
                self._preview_preference_changed
            )
            ui_controller.previewDefaultChanged.connect(
                self._preview_default_changed
            )
            dialog.previewToggleRequested.connect(self._toggle_previewer)
            dialog.previewResultChanged.connect(
                self._preview_selection_changed
            )
            dialog.previewSideRequested.connect(self._show_preview_side)
            dialog.set_managed_close_handler(
                self._close_dialog_with_callback
            )
            dialog.dialogClosed.connect(
                lambda d=dialog: self._search_dialog_closed(d)
            )
            dialog.destroyed.connect(
                lambda _object=None, d=dialog, c=ui_controller:
                self._search_dialog_destroyed(d, c)
            )

            config = self.backend._read_config()
            debounce = _bounded_int(
                config.get("debounce_ms", 160), 50, 2000, 160
            )
            try:
                dialog.set_debounce_interval(debounce)
            except Exception:
                try:
                    dialog._debounce.setInterval(debounce)
                except Exception:
                    pass
            self._dialog = dialog
            self._ui_controller = ui_controller

        self._register_managed_dialog(self._dialog)
        self._dialog.show()
        if self._ui_controller is not None:
            self._ui_controller.resume()
        self._dialog.raise_()
        self._dialog.activateWindow()
        self._dialog.focus_query()
        warm = getattr(self.backend, "warm_lexical_on_demand", None)
        if callable(warm):
            warm()

    def _addon_version(self) -> str:
        """Canonical add-on version from the bundle manifest."""

        try:
            manifest = json.loads(
                (self.backend.bundle_root / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            version = str(manifest.get("human_version") or "").strip()
            if version:
                return version
        except Exception:
            pass
        package = sys.modules.get(self.backend.addon_module)
        return str(getattr(package, "__version__", "") or "")

    def _native_update_available(self) -> bool:
        """Expose updates only for this running public AnkiWeb copy."""

        try:
            from .updater import native_update_available

            return native_update_available(
                self.mw,
                self.backend.addon_module,
            )
        except Exception:
            return False

    def _check_for_updates(self) -> None:
        """Quiesce local files, then run Anki's targeted native updater."""

        if self._update_check_running or self.backend.bundle_update_running:
            self._show_message("Smart Search is already checking for updates.")
            return
        profile_was_active = self.backend.active
        if not self.backend.begin_bundle_update():
            self._show_info(
                "Please wait for the current Smart Search task to finish, "
                "then choose Check & Update again."
            )
            return
        self._update_profile_was_active = profile_was_active
        self._update_check_running = True
        self._pause_background_for_update()
        try:
            self._close_dialog_with_callback(
                self._quiesce_and_start_native_update
            )
        except Exception as error:
            self._update_check_failed(str(error))

    def _pause_background_for_update(self) -> None:
        """Stop every deferred launcher before the update gate is quiesced."""

        for timer in (
            self._reconcile_timer,
            self._semantic_autostart_timer,
            self._dialog_refresh_timer,
            self._preview_open_timer,
            self._card_state_refresh_timer,
            self._vocabulary_refresh_timer,
        ):
            if timer is not None:
                timer.stop()
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
        self._pending_preview_result = None

    def _quiesce_and_start_native_update(self) -> None:
        self.backend.quiesce_for_bundle_update(
            on_ready=self._start_native_update,
            on_error=self._update_check_failed,
        )

    def _start_native_update(self) -> None:
        try:
            from .updater import request_native_update

            request_native_update(
                self.mw,
                self.mw,
                self.backend.addon_module,
                self._native_update_finished,
            )
        except Exception as error:
            self._update_check_failed(str(error))

    def _native_update_finished(self, log: list[Any]) -> None:
        entries = list(log or [])
        try:
            from .updater import native_update_was_installed

            installed = native_update_was_installed(entries)
        except Exception:
            installed = False

        if not installed:
            self._release_update_gate_and_reopen()
        if not entries:
            self._show_info("Smart Search is up to date.")
            return
        self._show_native_update_log(entries)

    def _show_native_update_log(self, entries: list[Any]) -> None:
        try:
            native_completion = getattr(self.mw, "on_updates_installed", None)
            if callable(native_completion):
                native_completion(entries)
                return
            from aqt.addons import show_log_to_user

            show_log_to_user(self.mw, entries, title="Smart Search")
        except Exception as error:
            self._show_error(f"The update finished with a problem: {error}")

    def _update_check_failed(self, message: str) -> None:
        self._release_update_gate_and_reopen()
        self._show_error(str(message))

    def _release_update_gate_and_reopen(self) -> None:
        """Resume the old bundle only when no replacement was installed."""

        self._update_check_running = False
        self.backend.finish_bundle_update()
        reopen = self._update_profile_was_active
        self._update_profile_was_active = False
        if not reopen or getattr(self.mw, "col", None) is None:
            self._resume_background_after_update()
            return
        self.backend.activate_profile_async(
            auto_rebuild=False,
            on_ready=lambda _status: self._resume_background_after_update(),
            on_error=lambda message: self._show_error(
                f"Smart Search could not reopen after the update check: {message}"
            ),
        )

    def _resume_background_after_update(self) -> None:
        with self._reconcile_request_lock:
            reconcile_pending = bool(
                self._pending_reconcile_note_ids
                or self._pending_reconcile_deleted_ids
                or self._reconcile_full
                or self._reconcile_startup_check
            )
        if reconcile_pending and self._reconcile_timer is not None:
            self._reconcile_timer.start(250)
        self._schedule_semantic_autostart()
        self._refresh_dialog()

    def _open_results_in_browser_safely(
        self,
        results: Sequence[object],
    ) -> None:
        """Prevent two live Anki editors from racing on the same note."""

        self._with_preview_saved(
            lambda: _open_results_in_browser(results),
            detach_editor=True,
        )

    def _with_preview_saved(
        self,
        callback: Callable[[], None],
        *,
        detach_editor: bool,
    ) -> None:
        previewer = self._previewer
        if previewer is None:
            callback()
            return
        try:
            if detach_editor:
                previewer.prepare_for_external_change(callback)
            else:
                previewer.flush(callback)
        except (AttributeError, RuntimeError):
            callback()

    # ---------------------------------------------------------- card preview

    def _toggle_previewer(self, result: object | None) -> None:
        if result is None:
            # X is a temporary dismissal. Selecting another result (or
            # clicking the current one again) reopens the pane; Settings is
            # the persistent way to disable automatic previews.
            self._hide_previewer(suppress=False)
            return
        if not self._preview_feature_enabled() or not preview_card_ids(result):
            self._hide_previewer()
            return

        dialog = self._dialog
        if dialog is None or getattr(self.mw, "col", None) is None:
            return
        try:
            restore_result_focus = bool(dialog.results.hasFocus())
        except (AttributeError, RuntimeError):
            restore_result_focus = False
        self._preview_auto_suppressed = False
        try:
            if self._previewer is None:
                self._previewer = create_inline_result_inspector(
                    self.mw,
                    pane=dialog.preview_pane,
                    parent_window=dialog,
                    initial_result=result,
                    on_error=self._show_error,
                    default_view=self._preview_default_view(),
                )
            else:
                self._previewer.set_result(result, apply_default=True)
            dialog.set_preview_active(True)
            self._previewer.show()
            if restore_result_focus:
                try:
                    from aqt.qt import QTimer

                    QTimer.singleShot(
                        0,
                        lambda d=dialog: self._restore_dialog_result_focus(d),
                    )
                except (ImportError, RuntimeError):
                    pass
        except Exception as error:
            self._close_previewer()
            try:
                dialog.set_preview_active(False)
            except RuntimeError:
                pass
            self._show_error(f"Could not open the inline card preview: {error}")

    def _restore_dialog_result_focus(self, dialog: object) -> None:
        if self._dialog is not dialog:
            return
        try:
            dialog.results.setFocus()
        except (AttributeError, RuntimeError):
            pass

    def _preview_selection_changed(self, result: object | None) -> None:
        if not self._preview_feature_enabled():
            self._hide_previewer()
            return
        previewer = self._previewer
        if not preview_card_ids(result):
            self._hide_previewer()
            return
        if previewer is None:
            if self._preview_auto_suppressed:
                return
            dialog = self._dialog
            results = getattr(dialog, "results", None)
            try:
                browsing = bool(results is not None and results.hasFocus())
            except RuntimeError:
                browsing = False
            if browsing:
                self._schedule_auto_previewer(result)
            return
        try:
            previewer.set_result(result)
            dialog = self._dialog
            if (
                dialog is not None
                and not self._preview_auto_suppressed
                and not dialog.preview_pane.isVisible()
                and dialog.results.hasFocus()
            ):
                self._schedule_auto_previewer(result)
        except Exception as error:
            self._close_previewer()
            self._show_error(f"Could not update the inline preview: {error}")

    def _show_preview_side(self, result: object | None, side: str) -> None:
        """Open the selected card preview and deterministically show one side."""

        if not self._preview_feature_enabled() or not preview_card_ids(result):
            return
        dialog = self._dialog
        if dialog is None:
            return
        previewer = self._previewer
        needs_open = previewer is None
        if previewer is not None:
            try:
                needs_open = (
                    not dialog.preview_pane.isVisible()
                    or not previewer.is_targeting(result)
                )
            except (AttributeError, RuntimeError):
                needs_open = True
        if needs_open:
            self._toggle_previewer(result)
            previewer = self._previewer
        if previewer is None:
            return
        try:
            if dialog.preview_pane.mode() != "card":
                previewer.set_mode("card")
            previewer.show_side(side)
        except Exception as error:
            self._close_previewer()
            try:
                dialog.set_preview_active(False)
            except RuntimeError:
                pass
            self._show_error(f"Could not show the requested card side: {error}")

    def _preview_feature_enabled(self) -> bool:
        ui_controller = self._ui_controller
        return bool(
            ui_controller is not None
            and ui_controller.settings.preview_enabled
        )

    def _preview_default_view(self) -> PreviewDefault:
        ui_controller = self._ui_controller
        value = getattr(
            getattr(ui_controller, "settings", None),
            "preview_default",
            PreviewDefault.QUESTION,
        )
        try:
            return PreviewDefault(value)
        except (TypeError, ValueError):
            return PreviewDefault.QUESTION

    def _schedule_auto_previewer(self, result: object) -> None:
        self._pending_preview_result = result
        timer = self._preview_open_timer
        if timer is not None:
            timer.start(25)
        else:
            self._open_pending_previewer()

    def _open_pending_previewer(self) -> None:
        result = self._pending_preview_result
        self._pending_preview_result = None
        if result is None or not self._preview_feature_enabled():
            return
        dialog = self._dialog
        if dialog is None or self._preview_auto_suppressed:
            return
        try:
            if (
                not dialog.results.hasFocus()
                or dialog.results.current_result() != result
            ):
                return
        except RuntimeError:
            return
        self._toggle_previewer(result)

    def _preview_preference_changed(self, enabled: bool) -> None:
        if not enabled:
            # Keep the already-created native widgets available for reuse.
            # Recreating them on the same host is both expensive and prone to
            # stale Qt-layout ownership; full cleanup happens with the dialog.
            self._hide_previewer(suppress=True)
            return
        self._preview_auto_suppressed = False
        dialog = self._dialog
        if dialog is None:
            return
        try:
            if dialog.results.hasFocus():
                result = dialog.results.current_result()
                if preview_card_ids(result):
                    self._schedule_auto_previewer(result)
        except RuntimeError:
            return

    def _preview_default_changed(self, value: object) -> None:
        """Apply the accepted default immediately without reopening a pane."""

        previewer = self._previewer
        dialog = self._dialog
        if previewer is None:
            return
        visible = False
        if dialog is not None and self._preview_feature_enabled():
            try:
                visible = bool(dialog.preview_pane.isVisible())
            except RuntimeError:
                visible = False
        try:
            previewer.set_default_view(value, apply_now=visible)
        except (AttributeError, RuntimeError):
            return

    def _move_preview_result(self, offset: int) -> bool:
        dialog = self._dialog
        if dialog is None:
            return False
        try:
            return bool(dialog.move_result(offset))
        except RuntimeError:
            return False

    def _can_move_preview_result(self, offset: int) -> bool:
        dialog = self._dialog
        if dialog is None:
            return False
        try:
            return bool(dialog.can_move_result(offset))
        except RuntimeError:
            return False

    def _refresh_previewer(self) -> None:
        previewer = self._previewer
        dialog = self._dialog
        if previewer is None or dialog is None:
            return
        try:
            previewer.set_result(
                dialog.results.current_result(),
                force=True,
            )
        except Exception as error:
            self._close_previewer()
            self._show_error(f"Could not refresh the inline preview: {error}")

    def _hide_previewer(self, *, suppress: bool = False) -> None:
        timer = self._preview_open_timer
        if timer is not None:
            timer.stop()
        self._pending_preview_result = None
        if suppress:
            self._preview_auto_suppressed = True
        previewer = self._previewer
        if previewer is not None:
            try:
                previewer.hide()
            except RuntimeError:
                pass
        dialog = self._dialog
        if dialog is not None:
            try:
                dialog.set_preview_active(False)
            except RuntimeError:
                pass

    def _close_previewer(self, *, save: bool = True) -> None:
        timer = self._preview_open_timer
        if timer is not None:
            timer.stop()
        self._pending_preview_result = None
        previewer = self._previewer
        self._previewer = None
        self._preview_auto_suppressed = bool(previewer is not None and save)

        def cleaned() -> None:
            if self._previewer is None:
                self._preview_auto_suppressed = False

        if previewer is not None:
            if save:
                try:
                    previewer.cleanup_after_save(cleaned)
                except (AttributeError, RuntimeError):
                    try:
                        previewer.cleanup()
                    except (AttributeError, RuntimeError):
                        pass
                    cleaned()
            else:
                try:
                    previewer.cleanup()
                except (AttributeError, RuntimeError):
                    pass
                cleaned()
        else:
            cleaned()
        dialog = self._dialog
        if dialog is not None:
            try:
                dialog.set_preview_active(False)
            except RuntimeError:
                pass

    def _search_dialog_destroyed(
        self,
        dialog: object,
        ui_controller: object,
    ) -> None:
        """Clear stale Python wrappers after an unexpected native deletion."""

        self._clear_undo_offer()
        if self._dialog is dialog:
            self._end_semantic_search_session()
            self._close_previewer(save=False)
            self._dialog = None
            self._mark_managed_dialog_closed()
        if self._ui_controller is ui_controller:
            self._ui_controller = None
        try:
            ui_controller.dispose()
        except (AttributeError, RuntimeError):
            pass

    def open_native_search(self, query: str) -> None:
        """Public escape hatch for callers that explicitly want native search."""

        open_native_query_in_browser(_canonical_query(query))

    # ------------------------------------------------------- collection ops

    def _clear_undo_offer(self) -> None:
        self._pending_undo = None
        self._undo_in_flight = False
        dialog = self._dialog
        if dialog is not None:
            try:
                dialog.set_undo_available(False)
            except (AttributeError, RuntimeError):
                pass

    def _arm_undo_offer(
        self,
        action: CollectionAction,
        token: UndoToken | None,
        *,
        profile_token: int | None = None,
    ) -> None:
        context = self.backend._context
        dialog = self._dialog
        if (
            token is None
            or context is None
            or dialog is None
            or (
                profile_token is not None
                and context.token != profile_token
            )
            or (
                not token.collection_path
                or not context.collection_path
                or token.collection_path != context.collection_path
            )
        ):
            self._clear_undo_offer()
            return
        self._pending_undo = _PendingUndo(context.token, token, action)
        self._undo_in_flight = False
        try:
            dialog.set_undo_available(True, token.label)
        except (AttributeError, RuntimeError):
            self._clear_undo_offer()

    def _undo_last_change(self) -> None:
        pending = self._pending_undo
        if pending is None or self._undo_in_flight:
            return
        self._undo_in_flight = True
        dialog = self._dialog
        if dialog is not None:
            try:
                dialog.set_undo_available(False)
                dialog.set_batch_action_busy(True, "Undoing change…")
            except (AttributeError, RuntimeError):
                pass
        self._with_preview_saved(
            lambda: self._run_guarded_undo_after_save(pending),
            detach_editor=True,
        )

    def _run_guarded_undo_after_save(self, pending: _PendingUndo) -> None:
        context = self.backend._context
        if (
            self._pending_undo != pending
            or context is None
            or context.token != pending.profile_token
            or getattr(self.mw, "col", None) is None
        ):
            # Profile/dialog/sync teardown can invalidate the offer while an
            # inline-editor save is finishing. That is an expected lifecycle
            # cancellation, not an operation failure worth a modal alert.
            self._undo_cancelled_after_save()
            return
        try:
            start_guarded_undo(
                parent=self.mw,
                initiator=_OwnedUndo(context.token, pending.action),
                token=pending.token,
                on_success=lambda output: self._undo_succeeded(
                    pending,
                    output,
                ),
                on_failure=self._undo_failed,
            )
        except Exception as error:
            self._undo_failed(error)

    def _undo_succeeded(self, pending: _PendingUndo, output: Any) -> None:
        self._clear_undo_offer()
        try:
            from aqt import gui_hooks

            gui_hooks.state_did_undo(output)
        except (AttributeError, ImportError, RuntimeError):
            pass
        message = f"Undid {pending.token.label}"
        dialog = self._dialog
        if dialog is not None:
            try:
                finisher = getattr(dialog, "finish_undo_action", None)
                if callable(finisher):
                    finisher(True, message)
                else:
                    dialog.finish_batch_action(False, message)
            except (AttributeError, RuntimeError):
                pass
        self._refresh_visible_card_states()
        self._refresh_previewer()
        self._show_message(message)

    def _undo_failed(self, error: Exception) -> None:
        self._clear_undo_offer()
        if isinstance(error, UndoUnavailable):
            message = str(error)
        else:
            message = f"Could not undo the selected change: {error}"
        dialog = self._dialog
        if dialog is not None:
            try:
                finisher = getattr(dialog, "finish_undo_action", None)
                if callable(finisher):
                    finisher(False, message)
                else:
                    dialog.finish_batch_action(False, message)
            except (AttributeError, RuntimeError):
                pass
        self._show_error(message)

    def _undo_cancelled_after_save(self) -> None:
        self._clear_undo_offer()
        dialog = self._dialog
        if dialog is None:
            return
        try:
            finisher = getattr(dialog, "finish_undo_action", None)
            if callable(finisher):
                finisher(False, "Undo is no longer available.")
            else:
                dialog.set_batch_action_busy(False)
        except (AttributeError, RuntimeError):
            pass

    def _flag_results(self, results: Sequence[object], flag: int) -> None:
        note_ids, card_ids = result_ids(results)
        try:
            action = CollectionAction(
                ActionKind.FLAG,
                note_ids=note_ids,
                card_ids=card_ids,
                flag=flag,
            )
        except ValueError as error:
            self._show_error(str(error))
            return
        self._run_collection_action(action)

    def _create_result_copy(self, results: Sequence[object]) -> None:
        """Open Anki's native Add Cards editor with one note prefilled."""

        note_ids, card_ids = result_ids(results)
        if len(note_ids) != 1 or not card_ids:
            self._show_error("Select one note to create a copy.")
            return
        note_id = note_ids[0]
        self._with_preview_saved(
            lambda: self._create_result_copy_after_save(note_id, card_ids),
            detach_editor=False,
        )

    def _create_result_copy_after_save(
        self,
        note_id: int,
        card_ids: Sequence[int],
    ) -> None:
        """Revalidate the source, then mirror Anki's Create Copy action."""

        collection = getattr(self.mw, "col", None)
        if collection is None:
            self._show_error("Open an Anki profile before creating a copy.")
            return

        try:
            note = collection.get_note(int(note_id))
            if note is None:
                raise LookupError("The source note is no longer available.")
            source_card = None
            for card_id in card_ids:
                try:
                    candidate = collection.get_card(int(card_id))
                except Exception as error:
                    if error.__class__.__name__ in {
                        "NotFoundError",
                        "KeyError",
                    }:
                        continue
                    raise
                if candidate is not None and int(
                    getattr(candidate, "nid", 0) or 0
                ) == int(note_id):
                    source_card = candidate
                    break
            if source_card is None:
                raise LookupError("The source card is no longer available.")

            current_deck_id = getattr(source_card, "current_deck_id", None)
            if callable(current_deck_id):
                deck_id = int(current_deck_id())
            else:
                original = int(getattr(source_card, "odid", 0) or 0)
                deck_id = original or int(getattr(source_card, "did", 0) or 0)
            if deck_id <= 0:
                raise LookupError("The source deck is no longer available.")

            modern_opener = getattr(
                self.mw,
                "_open_new_or_legacy_dialog",
                None,
            )
            if callable(modern_opener):
                add_cards = modern_opener("AddCards")
            else:
                import aqt

                add_cards = aqt.dialogs.open("AddCards", self.mw)
            if add_cards is None or not callable(
                getattr(add_cards, "set_note", None)
            ):
                raise RuntimeError("Anki could not open Add Cards.")
            editor = getattr(add_cards, "editor", None)
            if getattr(editor, "note", None) is not None:
                sanitized_fields = (
                    self._copy_fields_without_external_identity(note)
                )
                if sanitized_fields is not None:
                    # Legacy Add Cards copies this detached Note object. This
                    # mirrors AnkiHub's native Browser integration without
                    # ever flushing the change back to the source note.
                    note.fields = list(sanitized_fields)
            add_cards.set_note(note, deck_id)
        except Exception as error:
            if error.__class__.__name__ in {"NotFoundError", "KeyError"}:
                message = "The source note is no longer available."
            else:
                message = str(error) or "The source note is no longer available."
            self._show_error(f"Could not create a copy: {message}")

    @staticmethod
    def _copy_fields_without_external_identity(
        note: Any,
    ) -> tuple[str, ...] | None:
        """Avoid cloning AnkiHub's source-note identity into a local copy."""

        try:
            names = tuple(str(name) for name in note.keys())
            fields = [str(value) for value in note.fields]
        except (AttributeError, TypeError):
            return None
        changed = False
        for index, name in enumerate(names):
            if name.casefold() == "ankihub_id" and index < len(fields):
                fields[index] = ""
                changed = True
        return tuple(fields) if changed else None

    def _set_results_suspended(
        self,
        results: Sequence[object],
        suspend: bool,
    ) -> None:
        note_ids, card_ids = result_ids(results)
        action = CollectionAction(
            ActionKind.SUSPEND if suspend else ActionKind.UNSUSPEND,
            note_ids=note_ids,
            card_ids=card_ids,
        )
        self._run_collection_action(action)

    def _set_results_buried(
        self,
        results: Sequence[object],
        bury: bool,
    ) -> None:
        note_ids, card_ids = result_ids(results)
        action = CollectionAction(
            ActionKind.BURY if bury else ActionKind.UNBURY,
            note_ids=note_ids,
            card_ids=card_ids,
        )
        self._run_collection_action(action)

    def _change_results_deck(
        self,
        results: Sequence[object],
        deck_id: int,
        deck_name: str,
    ) -> None:
        note_ids, card_ids = result_ids(results)
        try:
            action = CollectionAction(
                ActionKind.CHANGE_DECK,
                note_ids=note_ids,
                card_ids=card_ids,
                deck_id=deck_id,
                deck_name=deck_name,
            )
        except ValueError as error:
            self._show_error(str(error))
            return
        self._run_collection_action(action)

    def _change_result_tags(
        self,
        results: Sequence[object],
        add: bool,
    ) -> None:
        if getattr(self.mw, "col", None) is None:
            self._show_error("Open an Anki profile before changing tags.")
            return
        try:
            from aqt.utils import getTag

            prompt = "Enter tags to add:" if add else "Enter tags to remove:"
            tags, accepted = getTag(
                self._dialog or self.mw,
                self.mw.col,
                prompt,
            )
        except Exception as error:
            self._show_error(f"Could not open the tag chooser: {error}")
            return
        if not accepted:
            return

        note_ids, card_ids = result_ids(results)
        try:
            action = CollectionAction(
                ActionKind.ADD_TAGS if add else ActionKind.REMOVE_TAGS,
                note_ids=note_ids,
                card_ids=card_ids,
                tags=tags,
            )
        except ValueError as error:
            self._show_error(str(error))
            return
        self._run_collection_action(action)

    def _run_collection_action(self, action: CollectionAction) -> None:
        # Starting any later action retires the previous offer, including a
        # tag operation Anki records as an undo step despite changing 0 notes.
        self._clear_undo_offer()
        self._with_preview_saved(
            lambda: self._run_collection_action_after_save(action),
            detach_editor=True,
        )

    def _run_collection_action_after_save(
        self,
        action: CollectionAction,
    ) -> None:
        if getattr(self.mw, "col", None) is None or not self.backend.active:
            self._show_error("Open an Anki profile before changing cards.")
            return
        if not action.requested_count:
            target = (
                "notes"
                if action.kind in (ActionKind.ADD_TAGS, ActionKind.REMOVE_TAGS)
                else "cards"
            )
            self._show_error(f"The current selection has no {target} to change.")
            return

        dialog = self._dialog
        context = self.backend._context
        if dialog is not None:
            try:
                dialog.set_batch_action_busy(True, "Applying change…")
            except RuntimeError:
                pass

        try:
            if context is None:
                raise RuntimeError("No Anki profile is open.")
            start_collection_action(
                parent=self.mw,
                initiator=_OwnedMutation(context.token, action),
                action=action,
                on_success=lambda outcome: self._collection_action_succeeded(
                    action,
                    outcome,
                    profile_token=context.token,
                ),
                on_failure=self._collection_action_failed,
            )
        except Exception as error:
            self._collection_action_failed(error)

    def _collection_action_succeeded(
        self,
        action,
        outcome,
        *,
        profile_token: int | None = None,
    ) -> None:
        undo_token = getattr(outcome, "undo_token", None)
        self._arm_undo_offer(
            action,
            undo_token,
            profile_token=profile_token,
        )
        message = format_action_message(action, outcome)
        dialog = self._dialog
        if dialog is not None:
            try:
                outcome_card_ids = getattr(
                    outcome,
                    "changed_card_ids",
                    None,
                )
                changed_card_ids = (
                    action.card_ids
                    if outcome_card_ids is None
                    else outcome_card_ids
                )
                if action.kind is ActionKind.FLAG:
                    dialog.apply_card_state_change(
                        changed_card_ids,
                        flag=action.flag,
                    )
                elif action.kind is ActionKind.SUSPEND:
                    dialog.apply_card_state_change(
                        changed_card_ids,
                        suspended=True,
                        buried=False,
                    )
                elif action.kind is ActionKind.UNSUSPEND:
                    dialog.apply_card_state_change(
                        changed_card_ids,
                        suspended=False,
                    )
                elif action.kind is ActionKind.BURY:
                    dialog.apply_card_state_change(
                        changed_card_ids,
                        suspended=False,
                        buried=True,
                    )
                elif action.kind is ActionKind.UNBURY:
                    dialog.apply_card_state_change(
                        changed_card_ids,
                        buried=False,
                    )
                dialog.finish_batch_action(True, message)
            except RuntimeError:
                pass
        self._refresh_previewer()
        self._show_message(message)

    def _collection_action_failed(self, error: Exception) -> None:
        self._clear_undo_offer()
        message = f"Could not apply the selected change: {error}"
        dialog = self._dialog
        if dialog is not None:
            try:
                dialog.finish_batch_action(False, message)
            except RuntimeError:
                pass
        self._show_error(message)

    def schedule_reconcile(
        self,
        *,
        note_ids: Iterable[int] | None = None,
        deleted_note_ids: Iterable[int] = (),
        reasons: DirtyReason | int = DirtyReason.NOTE_CONTENT,
        initial_check: bool = False,
        full: bool = False,
    ) -> None:
        update_running = self.backend.bundle_update_running
        if (
            (not self.backend.active and not update_running)
            or self._reconcile_timer is None
        ):
            return
        if not self._auto_reconcile:
            return
        clean_note_ids = _positive_ids(note_ids or ())
        clean_deleted_ids = _positive_ids(deleted_note_ids)
        context = self.backend._context
        journal = context.maintenance if context is not None else None
        if (
            (clean_note_ids or clean_deleted_ids)
            and self._vocabulary_refresh_timer is not None
        ):
            self._vocabulary_refresh_timer.stop()
        if note_ids is None and not clean_deleted_ids and not initial_check:
            full = True
        audit_reason = (
            "startup" if initial_check and not full else "unknown-bulk-change"
        ) if (full or initial_check) else None
        with self._reconcile_request_lock:
            self._pending_reconcile_note_ids.update(clean_note_ids)
            self._pending_reconcile_deleted_ids.update(clean_deleted_ids)
            self._pending_reconcile_note_ids.difference_update(
                self._pending_reconcile_deleted_ids
            )
            self._reconcile_full = self._reconcile_full or bool(full)
            self._reconcile_startup_check = (
                self._reconcile_startup_check or bool(initial_check)
            )
            if full or initial_check:
                self._reconcile_intent_epoch += 1
        queued_async = False
        if journal is not None:
            queued_async = self.backend.queue_maintenance(
                note_ids=clean_note_ids,
                deleted_note_ids=clean_deleted_ids,
                reasons=reasons,
                audit_reason=audit_reason,
                on_ready=lambda saved, startup=bool(initial_check): (
                    self._maintenance_intent_saved(
                        saved,
                        initial_check=startup,
                    )
                ),
            )
        if update_running or self._maintenance_blocked() or queued_async:
            return
        self._arm_reconcile_timer(initial_check=initial_check)

    def _maintenance_intent_saved(
        self,
        saved: bool,
        *,
        initial_check: bool,
    ) -> None:
        """Retain a full-audit fallback when durable enqueueing fails."""

        if not saved:
            with self._reconcile_request_lock:
                self._reconcile_full = True
                self._reconcile_intent_epoch += 1
        self._run_on_main(
            lambda: self._arm_reconcile_timer(initial_check=initial_check)
        )

    def _arm_reconcile_timer(self, *, initial_check: bool = False) -> None:
        if (
            self.backend.bundle_update_running
            or self._maintenance_blocked()
            or self._reconcile_timer is None
        ):
            return
        with self._reconcile_request_lock:
            has_exact_work = bool(
                self._pending_reconcile_note_ids
                or self._pending_reconcile_deleted_ids
            )
        if initial_check:
            # Opening a healthy profile should not immediately materialize
            # 40k note snapshots.  First run a cheap aggregate fingerprint
            # after the UI has settled, and only snapshot if it differs.
            delay = _bounded_int(
                self._startup_reconcile_delay_ms,
                5_000,
                60_000,
                15_000,
            )
            if has_exact_work:
                delay = _bounded_int(
                    self._reconcile_debounce_ms,
                    250,
                    10_000,
                    _DEFAULT_DEBOUNCE_MS,
                )
        else:
            delay = _bounded_int(
                self._reconcile_debounce_ms,
                250,
                10_000,
                _DEFAULT_DEBOUNCE_MS,
            )
        remaining = -1
        try:
            if self._reconcile_timer.isActive():
                remaining = int(self._reconcile_timer.remainingTime())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            remaining = -1
        if remaining < 0 or delay < remaining:
            self._reconcile_timer.start(delay)

    def _run_reconcile(self) -> None:
        if self.backend.bundle_update_running or self._maintenance_blocked():
            return
        with self._reconcile_request_lock:
            note_ids = tuple(sorted(self._pending_reconcile_note_ids))[:250]
            deleted_ids = tuple(sorted(self._pending_reconcile_deleted_ids))[:250]
            full = self._reconcile_full
            startup_check = self._reconcile_startup_check
            intent_epoch = self._reconcile_intent_epoch
        context = self.backend._context
        journal = context.maintenance if context is not None else None
        journal_entries = journal.try_pending(limit=250) if journal is not None else ()
        if journal_entries is None:
            if self._reconcile_timer is not None:
                self._reconcile_timer.start(100)
            return
        audit_request: AuditRequest | None = None
        if journal is not None:
            audit_available, audit_request = journal.try_audit_request()
            if not audit_available:
                if self._reconcile_timer is not None:
                    self._reconcile_timer.start(100)
                return
        journal_ids = tuple(entry.note_id for entry in journal_entries)
        requested_ids = tuple(
            sorted(set(note_ids) | set(deleted_ids) | set(journal_ids))
        )[:250]
        if journal_entries:
            selected_ids = set(requested_ids)
            journal_entries = tuple(
                entry for entry in journal_entries if entry.note_id in selected_ids
            )

        ran_targeted = False
        ran_audit = False
        attempt_failed = False

        def retry_failure(
            message: str,
            retry_note_ids: tuple[int, ...] = (),
        ) -> None:
            nonlocal attempt_failed
            attempt_failed = True
            self._background_reconcile_failed(
                message,
                retry_note_ids=retry_note_ids,
            )

        if requested_ids:
            ran_targeted = True
            started = self.backend.reconcile_note_ids(
                requested_ids,
                journal_entries=journal_entries,
                on_success=self._targeted_reconcile_succeeded,
                on_error=lambda message: retry_failure(
                    message,
                    requested_ids,
                ),
            )
        elif journal is not None and audit_request is not None:
            ran_audit = True
            started = self.backend.audit_manifest(
                request=audit_request,
                on_success=lambda status, epoch=intent_epoch: (
                    self._manifest_audit_succeeded(
                        status,
                        intent_epoch=epoch,
                    )
                ),
                on_error=retry_failure,
            )
        elif full:
            started = self.backend.reconcile(
                check_fingerprint=False,
                on_success=lambda status: self._background_reconcile_succeeded(
                    status,
                    clear_full=True,
                    intent_epoch=intent_epoch,
                ),
                on_error=retry_failure,
            )
        elif startup_check:
            started = self.backend.reconcile(
                check_fingerprint=True,
                on_success=lambda status: self._background_reconcile_succeeded(
                    status,
                    clear_startup=True,
                    intent_epoch=intent_epoch,
                ),
                on_error=retry_failure,
            )
        else:
            return

        if not started:
            # A rebuild/reconcile already owns the maintenance lane. Preserve
            # this exact request and retry without promoting it to a full scan.
            if self._reconcile_timer is not None:
                self._reconcile_timer.start(250)
            return
        if not attempt_failed:
            with self._reconcile_request_lock:
                selected = set(requested_ids)
                self._pending_reconcile_note_ids.difference_update(selected)
                self._pending_reconcile_deleted_ids.difference_update(selected)
                if full:
                    if journal is None or ran_audit:
                        if self._reconcile_intent_epoch == intent_epoch:
                            self._reconcile_full = False
                if startup_check:
                    if (journal is None and not ran_targeted) or ran_audit:
                        if self._reconcile_intent_epoch == intent_epoch:
                            self._reconcile_startup_check = False
        if (
            ran_targeted
            and startup_check
            and journal is None
            and self._reconcile_timer is not None
        ):
            # The exact edit wins immediately; the startup audit returns to an
            # idle delay instead of turning that edit into a full-profile scan.
            self._reconcile_timer.start(self._startup_reconcile_delay_ms)

    def _targeted_reconcile_succeeded(self, _status: IndexStatus) -> None:
        self._reconcile_retry_count = 0
        self._refresh_dialog()
        context = self.backend._context
        journal = context.maintenance if context is not None else None
        work_state = journal.try_work_state() if journal is not None else (0, False)
        if work_state is None:
            if self._reconcile_timer is not None:
                self._reconcile_timer.start(100)
        elif (
            not self._maintenance_blocked()
            and self._reconcile_timer is not None
            and (work_state[0] or work_state[1])
        ):
            self._reconcile_timer.start(100)
        elif (
            not self._maintenance_blocked()
            and self._post_review_resume_timer is not None
        ):
            self._post_review_resume_timer.start(250)
        timer = self._vocabulary_refresh_timer
        if timer is not None and self._dialog_is_visible():
            timer.start(self._vocabulary_refresh_delay_ms)

    def _manifest_audit_succeeded(
        self,
        _status: IndexStatus,
        *,
        intent_epoch: int | None = None,
    ) -> None:
        self._reconcile_retry_count = 0
        with self._reconcile_request_lock:
            if (
                intent_epoch is None
                or self._reconcile_intent_epoch == intent_epoch
            ):
                self._reconcile_full = False
                self._reconcile_startup_check = False
        self._refresh_dialog()
        context = self.backend._context
        journal = context.maintenance if context is not None else None
        work_state = journal.try_work_state() if journal is not None else (0, False)
        if work_state is None:
            if self._reconcile_timer is not None:
                self._reconcile_timer.start(100)
        elif (
            not self._maintenance_blocked()
            and self._reconcile_timer is not None
            and (work_state[0] or work_state[1])
        ):
            self._reconcile_timer.start(100)
        elif (
            not self._maintenance_blocked()
            and self._post_review_resume_timer is not None
        ):
            self._post_review_resume_timer.start(250)

    def _background_reconcile_succeeded(
        self,
        _status: IndexStatus,
        *,
        clear_full: bool = False,
        clear_startup: bool = False,
        intent_epoch: int | None = None,
    ) -> None:
        self._reconcile_retry_count = 0
        if clear_full or clear_startup:
            with self._reconcile_request_lock:
                if (
                    intent_epoch is None
                    or self._reconcile_intent_epoch == intent_epoch
                ):
                    if clear_full:
                        self._reconcile_full = False
                    if clear_startup:
                        self._reconcile_startup_check = False
        self._refresh_dialog()

    def _background_reconcile_failed(
        self,
        message: str,
        *,
        retry_note_ids: Iterable[int] = (),
    ) -> None:
        """Retry durable maintenance after transient worker/collection errors."""

        retry_ids = _positive_ids(retry_note_ids)
        if retry_ids:
            with self._reconcile_request_lock:
                self._pending_reconcile_note_ids.update(retry_ids)
        self._reconcile_retry_count = min(6, self._reconcile_retry_count + 1)
        if self._reconcile_retry_count == 1:
            self._show_error(message)
        if self._maintenance_blocked() or self._reconcile_timer is None:
            return
        delay_ms = min(
            30_000,
            1_000 * (2 ** (self._reconcile_retry_count - 1)),
        )
        self._reconcile_timer.start(delay_ms)

    def _run_vocabulary_refresh(self) -> None:
        if (
            self.backend.bundle_update_running
            or self._maintenance_blocked()
            or not self._dialog_is_visible()
        ):
            return
        started = self.backend.refresh_vocabulary(
            on_success=self._vocabulary_refresh_finished,
            # Exact and lexical search remain valid if fuzzy warmup fails; a
            # later profile warmup or targeted edit will try again.
            on_error=lambda _message: None,
        )
        if (
            not started
            and self.backend.active
            and self._vocabulary_refresh_timer is not None
        ):
            self._vocabulary_refresh_timer.start(500)

    def _vocabulary_refresh_finished(self, current: bool) -> None:
        if (
            not current
            and self.backend.active
            and self._vocabulary_refresh_timer is not None
        ):
            self._vocabulary_refresh_timer.start(
                self._vocabulary_refresh_delay_ms
            )

    def _clear_pending_reconciles(self) -> None:
        with self._reconcile_request_lock:
            self._pending_reconcile_note_ids.clear()
            self._pending_reconcile_deleted_ids.clear()
            self._reconcile_full = False
            self._reconcile_startup_check = False
            self._reconcile_intent_epoch += 1
        self._reconcile_retry_count = 0

    def _load_reconcile_settings(self) -> None:
        config = self.backend._read_config()
        self._auto_reconcile = bool(config.get("auto_reconcile", True))
        self._reconcile_debounce_ms = _bounded_int(
            config.get("reconcile_debounce_ms", _DEFAULT_DEBOUNCE_MS),
            250,
            10_000,
            _DEFAULT_DEBOUNCE_MS,
        )
        self._startup_reconcile_delay_ms = _bounded_int(
            config.get("startup_reconcile_delay_ms", 15_000),
            5_000,
            60_000,
            15_000,
        )

    def _schedule_semantic_autostart(
        self, *, delay_ms: int | None = None
    ) -> None:
        """Queue one local semantic build after text-index startup work.

        Missing model/runtime assets are deliberately not installed here:
        installation can involve a network download and remains an explicit
        user action.  The timer only embeds a profile when all local assets are
        already ready.
        """

        if self.backend.bundle_update_running or self._maintenance_blocked():
            return
        timer = self._semantic_autostart_timer
        context = self.backend._context
        if timer is None or context is None:
            return
        config = self.backend._read_config()
        if (
            not bool(config.get("semantic_enabled", True))
            or not bool(config.get("auto_semantic_index", True))
        ):
            return
        if self._semantic_autostart_attempted_token == context.token:
            return
        self._semantic_autostart_token = context.token
        if delay_ms is None:
            semantic_delay = _bounded_int(
                config.get(
                    "semantic_autostart_delay_ms",
                    _DEFAULT_SEMANTIC_AUTOSTART_DELAY_MS,
                ),
                5_000,
                120_000,
                _DEFAULT_SEMANTIC_AUTOSTART_DELAY_MS,
            )
            if bool(config.get("auto_reconcile", True)):
                startup_reconcile_delay = _bounded_int(
                    config.get("startup_reconcile_delay_ms", 15_000),
                    5_000,
                    60_000,
                    15_000,
                )
                semantic_delay = max(
                    semantic_delay,
                    startup_reconcile_delay + 1_000,
                )
            delay_ms = semantic_delay
        timer.start(max(0, int(delay_ms)))

    def _run_semantic_autostart(self) -> None:
        """Start at most one first-time semantic build per profile opening."""

        if self.backend.bundle_update_running or self._maintenance_blocked():
            return
        context = self.backend._context
        token = self._semantic_autostart_token
        if context is None or token is None or context.token != token:
            return
        if self.backend._semantic_restart_pending(context):
            return
        config = self.backend._read_config()
        if (
            not bool(config.get("semantic_enabled", True))
            or not bool(config.get("auto_semantic_index", True))
            or self._semantic_autostart_attempted_token == token
        ):
            return

        with self.backend._state_lock:
            maintenance_running = self.backend._maintenance_running
        with self._reconcile_request_lock:
            lexical_pending = bool(
                self._pending_reconcile_note_ids
                or self._pending_reconcile_deleted_ids
                or self._reconcile_full
                or self._reconcile_startup_check
            )
        journal = getattr(context, "maintenance", None)
        if journal is not None:
            work_state = journal.try_work_state()
            if work_state is None:
                lexical_pending = True
            else:
                lexical_pending = lexical_pending or bool(
                    work_state[0] or work_state[1]
                )
        status = self.backend.get_status()
        if (
            maintenance_running
            or lexical_pending
            or status.state is IndexState.BUILDING
        ):
            self._schedule_semantic_autostart(
                delay_ms=_SEMANTIC_AUTOSTART_RETRY_MS
            )
            return
        if status.state is not IndexState.READY:
            return
        semantic = status.semantic
        raw_semantic = getattr(context, "semantic_snapshot", None)
        if (
            raw_semantic is not None
            and bool(getattr(raw_semantic, "supported", False))
            and bool(getattr(raw_semantic, "model_ready", False))
            and not bool(getattr(raw_semantic, "runtime_ready", False))
        ):
            # v1.0.20 and earlier stored the model in the same durable
            # location but installed native inference libraries directly into
            # Anki's interpreter.  Upgrade that local installation to the
            # bundled disposable worker in the background.  No network access
            # is needed when the already-verified model is present, and a
            # fresh user without the model still retains the explicit setup
            # flow.
            if self._semantic_unload_timer is not None:
                self._semantic_unload_timer.stop()
            started = self.backend.install_semantic(
                runtime_only=True,
                on_progress=lambda _fraction, _detail: self._queue_dialog_refresh(),
                on_success=lambda _status: self._run_on_main(
                    self._semantic_runtime_migration_complete
                ),
                on_error=lambda _message: self._run_on_main(
                    self._refresh_dialog_now
                ),
            )
            if started is not None:
                self._semantic_autostart_attempted_token = token
            self._refresh_dialog()
            return
        if semantic is None or semantic.state is not SemanticState.MODEL_READY:
            # READY/INDEXING need no second worker.  NOT_INSTALLED,
            # UNSUPPORTED, and ERROR remain explicit user-facing actions.
            return

        if self._semantic_unload_timer is not None:
            self._semantic_unload_timer.stop()
        started = self.backend.index_semantic(
            on_progress=lambda _fraction, _detail: self._queue_dialog_refresh(),
            on_success=lambda _status: self._run_on_main(
                self._semantic_background_index_complete
            ),
            # Automatic startup errors stay passive in the semantic status
            # area; an unexpected modal dialog at every launch is disruptive.
            on_error=lambda _message: self._run_on_main(
                self._refresh_dialog_now
            ),
        )
        if started is not None:
            self._semantic_autostart_attempted_token = token
        self._refresh_dialog()

    def _on_profile_open(self) -> None:
        if self.backend.bundle_update_running:
            return
        self._collection_temporarily_closed = False
        self._clear_undo_offer()
        self._close_dialog()
        self._clear_pending_reconciles()
        self._clear_captured_notes()
        self._load_reconcile_settings()
        if self._semantic_autostart_timer is not None:
            self._semantic_autostart_timer.stop()
        if self._dialog_refresh_timer is not None:
            self._dialog_refresh_timer.stop()
        if self._card_state_refresh_timer is not None:
            self._card_state_refresh_timer.stop()
        if self._vocabulary_refresh_timer is not None:
            self._vocabulary_refresh_timer.stop()
        if self._semantic_unload_timer is not None:
            self._semantic_unload_timer.stop()
        if self._post_review_resume_timer is not None:
            self._post_review_resume_timer.stop()
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
        self._semantic_autostart_token = None
        self._semantic_autostart_attempted_token = None
        self._initial_setup_deferred = False
        self._set_backend_maintenance_pause(self._maintenance_blocked())
        if self._maintenance_blocked():
            # A profile can be opened while Anki is already restoring the
            # reviewer. Do not even open the disposable indexes in that case;
            # the post-review lifecycle will activate them once the core UI is
            # idle. Detach any stale prior-profile context first.
            if self.backend.active:
                self.backend.deactivate_profile()
            self._initial_setup_deferred = True
            self._refresh_dialog()
            return
        activate_async = getattr(self.backend, "activate_profile_async", None)
        if callable(activate_async):
            activate_async(
                auto_rebuild=True,
                on_ready=self._profile_activation_ready,
                on_error=lambda _message: self._refresh_dialog(),
            )
        else:
            status = self.backend.activate_profile(auto_rebuild=True)
            self._profile_activation_ready(status)

    def _profile_activation_ready(self, status: IndexStatus) -> None:
        if self.backend.bundle_update_running:
            return
        if status.state is IndexState.READY:
            self._initial_setup_deferred = False
            self.schedule_reconcile(initial_check=True)
        elif (
            status.state is IndexState.BUILDING
            and self.backend._context is not None
            and self.backend._context.note_count == 0
        ):
            # Auto-rebuild is running for a fresh profile. Retain a restart
            # marker so reviewer cancellation cannot strand initial setup.
            self._initial_setup_deferred = True
        elif status.state is IndexState.UNAVAILABLE and self._maintenance_blocked():
            self._initial_setup_deferred = True
        self._schedule_semantic_autostart()
        self._refresh_dialog()

    def _on_profile_will_close(self) -> None:
        self._clear_undo_offer()
        if self._reconcile_timer is not None:
            self._reconcile_timer.stop()
        if self._semantic_autostart_timer is not None:
            self._semantic_autostart_timer.stop()
        if self._dialog_refresh_timer is not None:
            self._dialog_refresh_timer.stop()
        if self._card_state_refresh_timer is not None:
            self._card_state_refresh_timer.stop()
        if self._vocabulary_refresh_timer is not None:
            self._vocabulary_refresh_timer.stop()
        if self._semantic_unload_timer is not None:
            self._semantic_unload_timer.stop()
        if self._post_review_resume_timer is not None:
            self._post_review_resume_timer.stop()
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
        self._clear_pending_reconciles()
        self._clear_captured_notes()
        self._semantic_autostart_token = None
        self._semantic_autostart_attempted_token = None
        self._initial_setup_deferred = False
        self._close_dialog()
        self.backend.deactivate_profile()

    def _capture_note_flush(self, note: Any) -> None:
        note_id = _object_id(note)
        if note_id <= 0:
            return
        with self._captured_note_lock:
            self._captured_note_ids.append(note_id)

    def _capture_added_note(self, note: Any) -> None:
        note_id = _object_id(note)
        if note_id <= 0:
            return
        with self._captured_note_lock:
            self._completed_added_ids.append(note_id)

    def _capture_deleted_notes(
        self,
        _collection: Any,
        note_ids: Iterable[int],
    ) -> None:
        ids = _positive_ids(note_ids)
        if not ids:
            return
        with self._captured_note_lock:
            self._captured_deleted_ids.update(ids)

    def _consume_editor_flush_ids(self, committed_note_id: int) -> tuple[int, ...]:
        with self._captured_note_lock:
            if self._captured_note_ids:
                captured = self._captured_note_ids.popleft()
                if committed_note_id in self._captured_note_ids:
                    self._captured_note_ids.remove(committed_note_id)
                # If a live Editor advanced before its completion callback, or
                # a failed precommit hook left a stale hint, reading both IDs
                # is cheap and idempotent. It is safer than substituting one
                # ambiguous ID for the operation-scoped handler target.
                return _positive_ids((captured, committed_note_id))
        return (committed_note_id,)

    def _drain_completed_adds(self) -> tuple[int, ...]:
        with self._captured_note_lock:
            values = tuple(self._completed_added_ids)
            self._completed_added_ids.clear()
        return _positive_ids(values)

    def _drain_captured_deletes(self) -> tuple[int, ...]:
        with self._captured_note_lock:
            values = tuple(self._captured_deleted_ids)
            self._captured_deleted_ids.clear()
        return tuple(sorted(values))

    def _clear_captured_notes(self) -> None:
        with self._captured_note_lock:
            self._captured_note_ids.clear()
            self._completed_added_ids.clear()
            self._captured_deleted_ids.clear()

    def _on_operation(self, changes: Any, handler: object | None) -> None:
        if not isinstance(handler, (_OwnedMutation, _OwnedUndo)):
            self._clear_undo_offer()
        # Owned card-state operations update their exact targets in the
        # success callback. Keep the visible result set stable until the user
        # explicitly searches again, and avoid a redundant state refresh.
        if bool(getattr(changes, "card", False)) and not isinstance(
            handler, (_OwnedMutation, _OwnedUndo)
        ):
            self._refresh_visible_card_states()

        context = self.backend._context
        if isinstance(handler, _OwnedMutation):
            if context is None or handler.profile_token != context.token:
                return
            action = handler.action
            if action.kind in (
                ActionKind.ADD_TAGS,
                ActionKind.REMOVE_TAGS,
                ActionKind.CHANGE_DECK,
            ):
                self.schedule_reconcile(
                    note_ids=action.note_ids,
                )
            return

        if isinstance(handler, _OwnedUndo):
            self._clear_undo_offer()
            if context is None or handler.profile_token != context.token:
                return
            action = handler.action
            if action.kind in (
                ActionKind.ADD_TAGS,
                ActionKind.REMOVE_TAGS,
                ActionKind.CHANGE_DECK,
            ):
                self.schedule_reconcile(note_ids=action.note_ids)
            return

        broad_change = any(
            bool(getattr(changes, field, False))
            for field in ("deck", "notetype")
        )
        searchable_change = any(
            bool(getattr(changes, field, False))
            for field in ("note", "tag", "note_text")
        )
        handler_note_id = _handler_note_id(handler)
        if searchable_change and handler_note_id > 0 and not broad_change:
            self.schedule_reconcile(
                note_ids=self._consume_editor_flush_ids(handler_note_id),
            )
            return

        completed_adds = self._drain_completed_adds()
        if searchable_change and completed_adds and not broad_change:
            self.schedule_reconcile(note_ids=completed_adds)
            return

        captured_deleted_ids = ()
        if (
            searchable_change
            and bool(getattr(changes, "card", False))
            and not broad_change
        ):
            captured_deleted_ids = self._drain_captured_deletes()
        if captured_deleted_ids:
            self.schedule_reconcile(
                note_ids=(),
                deleted_note_ids=captured_deleted_ids,
            )
            return

        if broad_change:
            self._clear_captured_notes()
            self.schedule_reconcile(full=True)
        elif searchable_change:
            # Native bulk tags, imports, undo/redo, and some add-on operations
            # expose only boolean OpChanges. A debounced full reconciliation
            # is the stable fallback when Anki provides no affected IDs.
            self._clear_captured_notes()
            self.schedule_reconcile(full=True)
        elif (
            bool(getattr(changes, "card", False))
            and not self._maintenance_blocked()
        ):
            # Browser card creation/deletion and Change Deck may expose only
            # ``card=True``. Reviewer answers are intentionally excluded: due,
            # queue, reps, flags, and filtered-deck placement are not indexed.
            self.schedule_reconcile(full=True)

    def _on_sync_finished(self) -> None:
        self._clear_undo_offer()
        self.schedule_reconcile()
        self._refresh_visible_card_states()

    def _on_collection_will_temporarily_close(
        self,
        _collection: Any,
    ) -> None:
        # Match Anki's Browser: a live Editor must save and close before sync
        # temporarily swaps out the collection beneath its note/card objects.
        self._collection_temporarily_closed = True
        self._clear_undo_offer()
        self._set_backend_maintenance_pause(True)
        for timer in (
            self._reconcile_timer,
            self._semantic_autostart_timer,
            self._dialog_refresh_timer,
            self._vocabulary_refresh_timer,
            self._card_state_refresh_timer,
            self._preview_open_timer,
            self._semantic_unload_timer,
            self._post_review_resume_timer,
        ):
            if timer is not None:
                timer.stop()
        abort = getattr(self.backend, "abort_semantic_runtime_now", None)
        if callable(abort):
            abort()
        self._close_dialog_with_callback(lambda: None)

    def _on_collection_reopened(self, _collection: Any) -> None:
        self._collection_temporarily_closed = False
        self._set_backend_maintenance_pause(self._review_active)
        self.schedule_reconcile()
        if not self._review_active and self._post_review_resume_timer is not None:
            self._post_review_resume_timer.start(250)

    def _install_semantic(self) -> None:
        if self.backend.bundle_update_running:
            self._show_error("Restart Anki to finish the Smart Search update.")
            return
        if self._maintenance_blocked():
            self._show_message("Finish reviewing before setting up Semantic search.")
            return
        if self._semantic_unload_timer is not None:
            self._semantic_unload_timer.stop()
        self.backend.install_semantic(
            on_progress=lambda _fraction, _detail: self._queue_dialog_refresh(),
            on_success=lambda _status: self._semantic_install_complete(),
            on_error=lambda message: self._show_error(
                f"Semantic search setup failed: {message}"
            ),
        )
        self._refresh_dialog()

    def _semantic_install_complete(self) -> None:
        self._refresh_dialog_now()
        context = self.backend._context
        if self.backend._semantic_restart_pending(context):
            self._semantic_autostart_attempted_token = context.token
            self._show_message(
                "Semantic search was repaired. Restart Anki once to load the "
                "repaired components."
            )
            return
        config = self.backend._read_config()
        if bool(config.get("auto_semantic_index", True)):
            # A manual install is fresh user intent, so allow a prior failed
            # automatic attempt in this profile session to be retried.
            self._semantic_autostart_attempted_token = None
            self._schedule_semantic_autostart(delay_ms=0)
            self._show_message(
                "Semantic search is set up. Preparing this profile will start "
                "automatically; Smart and Exact remain available."
            )
        else:
            self._show_message(
                "Semantic search is set up. Choose Prepare Semantic Search to "
                "finish this profile."
            )

    def _semantic_runtime_migration_complete(self) -> None:
        """Continue automatic preparation after a silent runtime upgrade."""

        self._refresh_dialog_now()
        context = self.backend._context
        if context is None or self._maintenance_blocked():
            return
        self._semantic_autostart_attempted_token = None
        self._schedule_semantic_autostart(delay_ms=0)

    def _text_index_rebuilt(self) -> None:
        """Re-arm first-time semantic indexing after a later successful rebuild."""

        config = self.backend._read_config()
        if not bool(config.get("auto_semantic_index", True)):
            return
        self._semantic_autostart_attempted_token = None
        self._schedule_semantic_autostart(delay_ms=0)

    def _index_semantic(self) -> None:
        if self.backend.bundle_update_running:
            self._show_error("Restart Anki to finish the Smart Search update.")
            return
        if self._maintenance_blocked():
            self._show_message("Finish reviewing before preparing Semantic search.")
            return
        if self._semantic_unload_timer is not None:
            self._semantic_unload_timer.stop()
        self.backend.index_semantic(
            on_progress=lambda _fraction, _detail: self._queue_dialog_refresh(),
            on_success=lambda _status: self._semantic_index_complete(),
            on_error=lambda message: self._show_error(
                f"Semantic search preparation failed: {message}"
            ),
        )
        self._refresh_dialog()

    def _semantic_index_complete(self) -> None:
        self._refresh_dialog_now()
        self._arm_semantic_idle_unload()
        self._show_message("Semantic search is ready for this profile.")

    def _semantic_background_index_complete(self) -> None:
        self._refresh_dialog_now()
        self._arm_semantic_idle_unload()

    def _queue_dialog_refresh(self) -> None:
        """Coalesce worker progress into at most four GUI updates/second."""

        with self._dialog_refresh_lock:
            if self._dialog_refresh_queued:
                return
            self._dialog_refresh_queued = True
        self._run_on_main(self._arm_dialog_refresh)

    def _arm_dialog_refresh(self) -> None:
        elapsed = time.monotonic() - self._dialog_refresh_last
        remaining = max(0.0, 0.25 - elapsed)
        timer = self._dialog_refresh_timer
        if timer is not None and remaining > 0:
            timer.start(max(1, round(remaining * 1000)))
        else:
            self._flush_queued_dialog_refresh()

    def _flush_queued_dialog_refresh(self) -> None:
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
            self._dialog_refresh_last = time.monotonic()
        self._refresh_dialog()

    def _refresh_dialog_now(self) -> None:
        if self._dialog_refresh_timer is not None:
            self._dialog_refresh_timer.stop()
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
            self._dialog_refresh_last = time.monotonic()
        self._refresh_dialog()

    def _refresh_dialog(self) -> None:
        if self._ui_controller is not None:
            self._ui_controller.refresh_status()

    def _refresh_visible_card_states(self) -> None:
        if self._maintenance_blocked() or not self._dialog_is_visible():
            return
        timer = self._card_state_refresh_timer
        if timer is not None:
            timer.start(120)
            return
        self._refresh_visible_card_states_now()

    def _refresh_visible_card_states_now(self) -> None:
        """Refresh only flag/suspension metadata for the current result rows."""

        dialog = self._dialog
        if (
            dialog is None
            or self._maintenance_blocked()
            or not self._dialog_is_visible()
        ):
            return
        try:
            results = dialog.results.results_model().results()
        except RuntimeError:
            return
        if not results:
            return

        def apply(updated: tuple[SearchResult, ...]) -> None:
            current = self._dialog
            if current is None:
                return
            try:
                current.refresh_card_states(updated)
            except RuntimeError:
                return

        self.backend.refresh_card_states(
            results,
            on_success=apply,
            on_error=lambda _message: None,
        )

    def _run_on_main(self, callback: Callable[[], None]) -> None:
        taskman = getattr(self.mw, "taskman", None)
        run_on_main = getattr(taskman, "run_on_main", None)
        if callable(run_on_main):
            run_on_main(callback)
        else:
            callback()

    def _register_managed_dialog(self, dialog: object | None) -> None:
        if dialog is None:
            return
        try:
            import aqt

            aqt.dialogs.register_dialog(
                _DIALOG_MANAGER_NAME,
                lambda: dialog,
                instance=dialog,
            )
        except (AttributeError, ImportError, RuntimeError):
            return

    def _mark_managed_dialog_closed(self) -> None:
        try:
            import aqt

            aqt.dialogs.markClosed(_DIALOG_MANAGER_NAME)
        except (AttributeError, ImportError, KeyError, RuntimeError):
            return

    def _search_dialog_closed(self, dialog: object) -> None:
        if self._dialog is not dialog:
            return
        self._clear_undo_offer()
        if self._card_state_refresh_timer is not None:
            self._card_state_refresh_timer.stop()
        self._end_semantic_search_session()
        self._close_previewer()
        self._mark_managed_dialog_closed()

    def _end_semantic_search_session(self) -> None:
        """Release the disposable model for every dialog-close path."""

        if self._semantic_unload_timer is not None:
            self._semantic_unload_timer.stop()
        abort = getattr(self.backend, "abort_semantic_runtime_now", None)
        if callable(abort):
            abort()

    def _close_dialog(self) -> None:
        self._close_dialog_with_callback(lambda: None)

    def _close_dialog_with_callback(
        self,
        callback: Callable[[], None],
    ) -> None:
        self._clear_undo_offer()
        self._dialog_close_callbacks.append(callback)
        if self._dialog_close_in_progress:
            return

        dialog = self._dialog
        ui_controller = self._ui_controller
        if dialog is None:
            self._mark_managed_dialog_closed()
            self._run_dialog_close_callbacks()
            return

        self._dialog_close_in_progress = True
        if ui_controller is not None:
            try:
                ui_controller.pause()
            except (AttributeError, RuntimeError):
                pass
        # The managed close handshake clears ``self._dialog`` before Qt emits
        # ``dialogClosed``, so release the worker here as well as in the direct
        # close signal path.
        self._end_semantic_search_session()
        if self._preview_open_timer is not None:
            self._preview_open_timer.stop()
        self._pending_preview_result = None
        previewer = self._previewer

        def finish() -> None:
            if self._previewer is previewer:
                self._previewer = None
            self._preview_auto_suppressed = False
            if self._dialog is dialog:
                self._dialog = None
            if self._ui_controller is ui_controller:
                self._ui_controller = None
            try:
                dialog.set_managed_close_handler(None)
            except (AttributeError, RuntimeError):
                pass
            try:
                dialog.close()
            except RuntimeError:
                pass
            if ui_controller is not None:
                try:
                    ui_controller.dispose()
                except (AttributeError, RuntimeError):
                    pass
            self._mark_managed_dialog_closed()
            try:
                dialog.deleteLater()
            except RuntimeError:
                pass
            self._dialog_close_in_progress = False
            self._run_dialog_close_callbacks()

        if previewer is None:
            finish()
            return
        try:
            previewer.cleanup_after_save(finish)
        except (AttributeError, RuntimeError):
            try:
                previewer.cleanup()
            except (AttributeError, RuntimeError):
                pass
            finish()

    def _run_dialog_close_callbacks(self) -> None:
        callbacks = self._dialog_close_callbacks
        self._dialog_close_callbacks = []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue

    def _append_hook(self, hook: Any, callback: Callable[..., Any]) -> None:
        hook.append(callback)
        self._hooks.append((hook, callback))

    def _append_named_hook(
        self,
        owner: Any,
        name: str,
        callback: Callable[..., Any],
    ) -> bool:
        hook = optional_hook(owner, name)
        if hook is None:
            return False
        self._append_hook(hook, callback)
        return True

    def _show_error(self, message: str) -> None:
        try:
            from aqt.utils import showWarning

            showWarning(str(message), parent=self.mw)
        except Exception:
            return

    def _show_message(self, message: str) -> None:
        try:
            from aqt.utils import tooltip

            tooltip(str(message), parent=self.mw)
        except Exception:
            return

    def _show_info(self, message: str) -> None:
        try:
            from aqt.utils import showInfo

            showInfo(str(message), parent=self.mw, title="Smart Search")
        except Exception:
            self._show_message(message)


def create_controller(
    mw: Any,
    *,
    bundle_root: str | Path | None = None,
    addon_module: str | None = None,
) -> SmartSearchAddonController:
    """Create, hook, and return the add-on's long-lived host controller."""

    controller = SmartSearchAddonController(
        mw,
        bundle_root=bundle_root,
        addon_module=addon_module,
    )
    controller.start()
    return controller


def _open_results_in_browser(results: Sequence[object]) -> None:
    """Open the exact cards represented by the checked result rows."""

    note_ids, card_ids = result_ids(results)
    if card_ids:
        open_card_ids_in_browser(card_ids)
    else:
        open_note_ids_in_browser(note_ids)


# ---------------------------------------------------------------------------
# Pure conversion helpers


def _with_live_card_states(
    response: SearchResponse,
    states_by_note: dict[int, tuple[tuple[int, int, bool, bool], ...]],
) -> SearchResponse:
    """Return a response whose card IDs and state reflect the live collection."""

    return replace(
        response,
        results=_results_with_live_card_states(response.results, states_by_note),
    )


def _results_with_live_card_states(
    visible: Sequence[SearchResult],
    states_by_note: dict[int, tuple[tuple[int, int, bool, bool], ...]],
) -> tuple[SearchResult, ...]:
    results: list[SearchResult] = []
    for result in visible:
        snapshots = states_by_note.get(int(result.note_id))
        if snapshots is None:
            results.append(result)
            continue
        card_states = tuple(
            CardState(
                card_id=card_id,
                flag=flag,
                suspended=suspended,
                buried=buried,
            )
            for card_id, flag, suspended, buried in snapshots
        )
        card_ids = tuple(state.card_id for state in card_states)
        results.append(
            replace(
                result,
                card_ids=card_ids,
                card_states=card_states,
                sibling_count=len(card_ids),
            )
        )
    return tuple(results)


def _to_ui_response(
    request: SearchRequest,
    response: BackendSearchResponse,
    *,
    card_ids_by_note: Mapping[int, Sequence[int]] | None = None,
) -> SearchResponse:
    correction_terms = {
        item.corrected for item in response.corrections if item.automatic
    }
    alias_terms = {item.expanded for item in response.alias_expansions}
    literal_terms = {
        token
        for term in response.parsed_query.positive_terms
        for token in tokenize(term.normalized)
    }
    results_list: list[SearchResult] = []
    for item in response.results:
        card_ids = (
            tuple(int(card_id) for card_id in card_ids_by_note.get(item.note_id, ()))
            if card_ids_by_note is not None
            else item.card_ids
        )
        if card_ids_by_note is not None and not card_ids:
            continue
        results_list.append(
            SearchResult(
                note_id=item.note_id,
                card_ids=card_ids,
                title=item.title,
                snippet=item.snippet,
                spans=_highlight_spans(
                    item.snippet,
                    literal_terms=literal_terms,
                    correction_terms=correction_terms,
                    alias_terms=alias_terms,
                ),
                deck=item.deck,
                note_type=item.note_type,
                tags=item.tags,
                match_reasons=tuple(
                    dict.fromkeys(reason.label for reason in item.reasons)
                ),
                sibling_count=len(card_ids),
                browser_query=_browser_query(item.note_id, card_ids),
                score=item.score,
            )
        )
    results = tuple(results_list)
    corrections = tuple(
        _ui_spelling_correction(item, request.query)
        for item in response.corrections
        if item.automatic or item.alternatives
    ) + tuple(
        _ui_alias_correction(item, request.query)
        for item in response.alias_expansions
    )
    return SearchResponse(
        request_id=request.request_id,
        query=request.query,
        results=results,
        corrections=corrections,
        active_filters=_active_filter_chips(
            response.parsed_query,
            original_query=request.query,
        ),
        elapsed_ms=response.elapsed_ms,
        total_results=response.total_candidates,
        warnings=response.warnings,
        truncated=response.total_candidates > len(results),
    )


def _native_ui_response(
    request: SearchRequest,
    notes: Sequence[IndexedNote],
    warning: str,
    *,
    card_ids_by_note: Mapping[int, Sequence[int]] | None = None,
    parsed: Any | None = None,
    total_results: int | None = None,
) -> SearchResponse:
    query_terms = set(tokenize(normalize_text(request.query)))
    results_list: list[SearchResult] = []
    for note in notes:
        card_ids = (
            tuple(int(card_id) for card_id in card_ids_by_note.get(note.note_id, ()))
            if card_ids_by_note is not None
            else note.card_ids
        )
        if card_ids_by_note is not None and not card_ids:
            continue
        results_list.append(
            SearchResult(
                note_id=note.note_id,
                card_ids=card_ids,
                title=strip_html_and_cloze(note.title) or f"Note {note.note_id}",
                snippet=_fallback_snippet(note),
                spans=_highlight_spans(
                    _fallback_snippet(note),
                    literal_terms=query_terms,
                    correction_terms=set(),
                    alias_terms=set(),
                ),
                deck=note.decks[0] if note.decks else "",
                note_type=note.note_type,
                tags=note.tags,
                match_reasons=("Native Anki search",),
                sibling_count=len(card_ids),
                browser_query=_browser_query(note.note_id, card_ids),
                score=None,
            )
        )
    results = tuple(results_list)
    total = len(results) if total_results is None else int(total_results)
    return SearchResponse(
        request_id=request.request_id,
        query=request.query,
        results=results,
        active_filters=_active_filter_chips(
            parsed,
            original_query=request.query,
        ),
        total_results=total,
        warnings=(warning,) if warning else (),
        truncated=total > len(results),
    )


def _browser_query(note_id: int, card_ids: Sequence[int]) -> str:
    clean = tuple(
        dict.fromkeys(int(card_id) for card_id in card_ids if int(card_id) > 0)
    )
    if clean:
        return "cid:" + ",".join(str(card_id) for card_id in clean)
    return f"nid:{int(note_id)}"


def _active_filter_chips(
    parsed: Any | None,
    *,
    original_query: str = "",
) -> tuple[FilterChip, ...]:
    """Build chips only from the exact parse that drove filtering."""

    if parsed is None or getattr(parsed, "native_search_required", False):
        return ()
    filter_query = str(getattr(parsed, "anki_filter_query", "") or "")
    # Individual removal from a Boolean group can silently change or break
    # its meaning. The expression is still applied; it simply is not exposed
    # as a misleading one-token removable chip.
    if re.search(r"(?:^|\s)(?:AND|OR)(?:\s|$)|[()]", filter_query, re.IGNORECASE):
        return ()

    # ``notetype:`` is a UI-friendly alias that is compiled to Anki's
    # canonical ``note:`` operator before parsing. Keep the user's original
    # spelling on the removable chip so clicking its close button can remove
    # the exact text that is still present in the search field.
    aliases_by_canonical: dict[str, list[str]] = {}
    for match in _NOTETYPE_FILTER.finditer(str(original_query or "")):
        original = match.group(0)
        canonical = _canonical_query(original).casefold()
        aliases_by_canonical.setdefault(canonical, []).append(original)

    chips: list[FilterChip] = []
    for raw_value in getattr(parsed, "anki_filters", ()):
        raw = str(raw_value)
        aliases = aliases_by_canonical.get(raw.casefold(), [])
        display_raw = aliases.pop(0) if aliases else raw
        candidate = display_raw
        negated = candidate.startswith("-")
        body = candidate[1:] if negated else candidate
        if len(body) >= 2 and body[0] == body[-1] == '"':
            body = body[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
        if ":" not in body:
            continue
        key, value = body.split(":", 1)
        key = key.strip('"')
        if negated:
            key = "-" + key
        chips.append(
            FilterChip(
                key=key,
                value=value.strip('"'),
                label=display_raw,
                raw_token=display_raw,
            )
        )
    return tuple(chips)


def _ui_spelling_correction(
    correction: BackendCorrection,
    literal_query: str,
) -> Correction:
    alternatives = (
        f" Alternatives: {', '.join(correction.alternatives)}."
        if correction.alternatives
        else ""
    )
    return Correction(
        original=correction.original,
        replacement=correction.corrected,
        kind="spelling",
        explanation=f"Likely typo corrected.{alternatives}",
        literal_query=literal_query,
    )


def _ui_alias_correction(
    expansion: BackendAliasExpansion,
    literal_query: str,
) -> Correction:
    return Correction(
        original=expansion.original,
        replacement=expansion.expanded,
        kind="alias",
        explanation="Recognized medical equivalent.",
        literal_query=literal_query,
    )


def _highlight_spans(
    text: str,
    *,
    literal_terms: set[str],
    correction_terms: set[str],
    alias_terms: set[str],
) -> tuple[HighlightSpan, ...]:
    spans: list[HighlightSpan] = []
    occupied: list[tuple[int, int]] = []
    for terms, kind in (
        (literal_terms, MatchKind.EXACT),
        (correction_terms, MatchKind.CORRECTION),
        (alias_terms, MatchKind.ALIAS),
    ):
        for term in sorted(terms, key=lambda value: (-len(value), value)):
            if not term:
                continue
            for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
                start, end = match.span()
                if any(start < old_end and end > old_start for old_start, old_end in occupied):
                    continue
                occupied.append((start, end))
                spans.append(HighlightSpan(start, end, kind))
    return tuple(sorted(spans, key=lambda item: (item.start, item.end)))


def _semantic_documents(
    notes: Sequence[IndexedNote],
    *,
    cancel_check: Callable[[], None] | None = None,
) -> list[SemanticDocument]:
    documents: list[SemanticDocument] = []
    for index, note in enumerate(notes, start=1):
        if cancel_check is not None and (index == 1 or index % 64 == 0):
            cancel_check()
        text = "\n".join(
            value
            for value in (
                strip_html_and_cloze(field) for field in note.fields.values()
            )
            if value
        )
        if not text:
            text = strip_html_and_cloze(note.title)
        documents.append(SemanticDocument.from_text(note.note_id, text))
    if cancel_check is not None:
        cancel_check()
    return documents


def _semantic_documents_from_index(
    documents: Sequence[Any],
    *,
    cancel_check: Callable[[], None] | None = None,
) -> list[SemanticDocument]:
    """Build vector inputs from the external lexical index, never Anki."""

    output: list[SemanticDocument] = []
    for index, document in enumerate(documents, start=1):
        if cancel_check is not None and (index == 1 or index % 64 == 0):
            cancel_check()
        text = str(getattr(document, "plain_text", "") or "")
        if not text:
            text = str(getattr(document, "title", "") or "")
        output.append(
            SemanticDocument.from_text(int(document.note_id), text)
        )
    if cancel_check is not None:
        cancel_check()
    return output


def _stale_semantic_documents(
    semantic: Any,
    documents: Sequence[SemanticDocument],
    *,
    cancel_check: Callable[[], None] | None = None,
) -> list[SemanticDocument]:
    """Return only text-changed vectors without loading the model runtime."""

    stale = getattr(semantic, "stale_documents", None)
    if callable(stale):
        return list(stale(documents, cancel_check=cancel_check))
    # Compatibility with simple test providers and older retained services.
    if cancel_check is not None:
        cancel_check()
    return list(documents)


def _semantic_documents_from_lexical_index(
    index: SmartSearchIndex,
    *,
    cancel_check: Callable[[], None] | None = None,
    batch_size: int = 250,
) -> list[SemanticDocument]:
    """Read a full semantic source in bounded external-index lock windows."""

    note_ids = index.note_ids()
    output: list[SemanticDocument] = []
    size = max(1, int(batch_size))
    for start in range(0, len(note_ids), size):
        if cancel_check is not None:
            cancel_check()
        batch = index.documents(note_ids[start : start + size])
        output.extend(
            _semantic_documents_from_index(
                batch,
                cancel_check=cancel_check,
            )
        )
        # Hand the GIL and SQLite lock back to an interactive search between
        # batches. Native ONNX inference later releases the GIL internally.
        time.sleep(0)
    if cancel_check is not None:
        cancel_check()
    return output


def _fallback_snippet(note: IndexedNote, limit: int = 360) -> str:
    text = " ".join(
        value
        for value in (
            strip_html_and_cloze(field) for field in note.fields.values()
        )
        if value
    )
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _canonical_query(query: str) -> str:
    """Support the UI-friendly ``notetype:`` alias without changing Anki."""

    return _NOTETYPE_FILTER.sub(
        lambda match: f"{match.group('neg')}note:{match.group('value')}",
        str(query or "").strip(),
    )


def _profile_name(mw: Any) -> str:
    manager = getattr(mw, "pm", None)
    name = getattr(manager, "name", None)
    if callable(name):
        name = name()
    if not name:
        profile = getattr(manager, "profile", None)
        if isinstance(profile, dict):
            name = profile.get("name")
    return str(name or "Default")


def _collection_path(mw: Any, collection: Any) -> str:
    path = getattr(collection, "path", None)
    if callable(path):
        path = path()
    if not path:
        manager = getattr(mw, "pm", None)
        legacy = getattr(manager, "collectionPath", None)
        if callable(legacy):
            path = legacy()
    if not path:
        # A stable per-profile placeholder still prevents profiles from
        # sharing indexes when a test double omits Anki's path property.
        path = str(Path("profiles") / _profile_name(mw) / "collection.anki2")
    return str(path)


def _collection_manifest_inventory(
    reader: AnkiCollectionReader,
    collection: Any,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> tuple[
    tuple[Any, ...],
    tuple[Any, ...],
    tuple[FacetManifest, ...],
    tuple[int, ...],
]:
    """Read bounded manifest pages and discard raw field text after hashing."""

    note_rows: list[Any] = []
    after_note_id = 0
    while True:
        if cancel_check is not None:
            cancel_check()
        page = reader.note_manifest_batch(
            collection,
            after_note_id=after_note_id,
            limit=250,
        )
        if not page:
            break
        note_rows.extend(page)
        after_note_id = int(page[-1].note_id)
        if len(page) < 250:
            break

    card_rows: list[Any] = []
    after_card_id = 0
    while True:
        if cancel_check is not None:
            cancel_check()
        page = reader.card_manifest_batch(
            collection,
            after_card_id=after_card_id,
            limit=500,
        )
        if not page:
            break
        card_rows.extend(page)
        after_card_id = int(page[-1].card_id)
        if len(page) < 500:
            break
    if cancel_check is not None:
        cancel_check()
    filtered_note_ids = tuple(
        sorted(
            {
                int(row[0])
                for row in collection.db.all(
                    "SELECT DISTINCT nid FROM cards WHERE odid > 0"
                )
                if int(row[0]) > 0
            }
        )
    )
    return (
        tuple(note_rows),
        tuple(card_rows),
        _collection_facet_manifest(collection),
        filtered_note_ids,
    )


def _manifest_seed_mismatches(
    index: SmartSearchIndex,
    note_rows: Sequence[Any],
    card_rows: Sequence[Any],
    facet_rows: Sequence[FacetManifest],
    *,
    cancel_check: Callable[[], None] | None = None,
    batch_size: int = 250,
) -> tuple[int, ...]:
    """Verify an old index before seeding the first durable manifest.

    The legacy aggregate fingerprint can collide for compensating deck moves
    or same-second, same-length edits. Existing index payloads retain the raw
    fields/tags and stable card scope needed to close that migration gap. The
    comparison is external to Anki and reads bounded batches, so it does not
    retain a second copy of the profile or monopolize the collection worker.
    """

    deck_names = {
        int(row.object_id): str(row.signature)
        for row in facet_rows
        if row.facet == "deck"
    }
    notetypes: dict[int, tuple[str, tuple[str, ...]]] = {}
    for row in facet_rows:
        if row.facet != "notetype":
            continue
        try:
            name, fields = json.loads(row.signature)
            notetypes[int(row.object_id)] = (
                str(name),
                tuple(str(field) for field in fields),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            # An unreadable compact facet is itself a reason to hydrate notes
            # of that type instead of trusting the migration seed.
            continue

    cards_by_note: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in card_rows:
        cards_by_note[int(row.note_id)].append(
            (int(row.card_id), int(row.home_deck_id))
        )

    mismatches: set[int] = set()
    size = max(1, int(batch_size))
    for start in range(0, len(note_rows), size):
        if cancel_check is not None:
            cancel_check()
        batch = note_rows[start : start + size]
        documents = {
            document.note_id: document
            for document in index.documents(row.note_id for row in batch)
        }
        for row in batch:
            if cancel_check is not None:
                cancel_check()
            note_id = int(row.note_id)
            document = documents.get(note_id)
            if document is None:
                mismatches.add(note_id)
                continue

            raw_fields = "\x1f".join(str(value) for value in document.fields.values())
            raw_tags = (
                " " + " ".join(str(tag) for tag in document.tags) + " "
                if document.tags
                else ""
            )
            field_digest = hashlib.blake2b(
                raw_fields.encode("utf-8"),
                digest_size=16,
            ).digest()
            tag_digest = hashlib.blake2b(
                raw_tags.encode("utf-8"),
                digest_size=16,
            ).digest()

            note_type = notetypes.get(int(row.note_type_id))
            card_scope = sorted(cards_by_note.get(note_id, ()))
            expected_card_ids = tuple(card_id for card_id, _deck_id in card_scope)
            expected_decks = {
                name
                for _card_id, deck_id in card_scope
                if (name := deck_names.get(deck_id, ""))
            }
            if (
                field_digest != bytes(row.fields_digest)
                or tag_digest != bytes(row.tags_digest)
                or note_type is None
                or document.note_type != note_type[0]
                or tuple(document.fields) != note_type[1]
                or tuple(sorted(document.card_ids)) != expected_card_ids
                or set(document.decks) != expected_decks
            ):
                mismatches.add(note_id)
        time.sleep(0)
    if cancel_check is not None:
        cancel_check()
    return tuple(sorted(mismatches))


def _collection_facet_manifest(
    collection: Any,
) -> tuple[FacetManifest, ...]:
    """Return only metadata that changes indexed deck/notetype facets."""

    rows: list[FacetManifest] = []
    for item in collection.decks.all_names_and_ids(include_filtered=True):
        if isinstance(item, Mapping):
            raw_id = item.get("id", 0)
            name = item.get("name", "")
        else:
            raw_id = getattr(item, "id", 0)
            name = getattr(item, "name", "")
        try:
            object_id = int(raw_id or 0)
        except (TypeError, ValueError):
            continue
        if object_id > 0:
            rows.append(
                FacetManifest(
                    facet="deck",
                    object_id=object_id,
                    signature=str(name or ""),
                )
            )

    for model in collection.models.all():
        if not isinstance(model, Mapping):
            continue
        try:
            object_id = int(model.get("id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if object_id <= 0:
            continue
        fields = tuple(
            str(field.get("name", "") or "")
            for field in model.get("flds", ())
            if isinstance(field, Mapping)
        )
        rows.append(
            FacetManifest(
                facet="notetype",
                object_id=object_id,
                signature=json.dumps(
                    (str(model.get("name", "") or ""), fields),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        )
    return tuple(sorted(rows, key=lambda row: (row.facet, row.object_id)))


def _collection_fingerprint(
    collection: Any,
    *,
    legacy_card_scope: bool = False,
) -> str:
    """Return a cheap read-only signal for whether searchable data changed.

    Aggregate queries avoid transferring note/card text.  Deck/notetype names
    are included because renames may not change note modification timestamps.
    This is only a staleness gate; a mismatch triggers a canonical full
    snapshot, so it is never trusted as the search source itself.
    """

    note_row = collection.db.first(
        """
        SELECT count(*), coalesce(max(mod), 0), coalesce(sum(mod), 0),
               coalesce(sum(mid), 0), coalesce(sum(length(tags)), 0),
               coalesce(sum(length(flds)), 0)
        FROM notes
        """
    )
    if legacy_card_scope:
        # v1.0.19 and earlier persisted this form. It is accepted only to seed
        # the new durable manifest once without hydrating every existing note.
        card_row = collection.db.first(
            """
            SELECT count(*), coalesce(sum(did), 0), coalesce(sum(odid), 0)
            FROM cards
            """
        )
    else:
        # Filtered decks temporarily change did but preserve odid. Canonical
        # home-deck identity makes ordinary reviews fingerprint-invariant.
        card_row = collection.db.first(
            """
            SELECT count(*),
                   coalesce(sum(CASE WHEN odid > 0 THEN odid ELSE did END), 0)
            FROM cards
            """
        )
    deck_names = tuple(
        sorted(
            (int(item.id), str(item.name))
            for item in collection.decks.all_names_and_ids(include_filtered=True)
        )
    )
    notetypes = tuple(
        sorted(
            (
                int(model.get("id", 0)),
                str(model.get("name", "")),
                tuple(str(field.get("name", "")) for field in model.get("flds", ())),
            )
            for model in collection.models.all()
        )
    )
    payload = repr((tuple(note_row or ()), tuple(card_row or ()), deck_names, notetypes))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_index_fingerprint(index: SmartSearchIndex) -> str | None:
    try:
        row = index.connection.execute(
            "SELECT value FROM metadata WHERE key='collection_fingerprint'"
        ).fetchone()
    except Exception:
        return None
    return str(row[0]) if row else None


def _write_index_fingerprint(index: SmartSearchIndex, fingerprint: str) -> None:
    with index.transaction():
        index.connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES('collection_fingerprint', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(fingerprint),),
        )


def _clear_index_fingerprint(index: SmartSearchIndex) -> None:
    with index.transaction():
        index.connection.execute(
            "DELETE FROM metadata WHERE key='collection_fingerprint'"
        )


def _bounded_int(
    value: object,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


def _raise_if_cancelled(event: threading.Event) -> None:
    if event.is_set():
        raise _CancelledOperation()


def _semantic_count(context: _ProfileContext) -> int:
    status = context.semantic_snapshot
    if status is None:
        return 0
    return int(getattr(status, "index_count", 0))


__all__ = [
    "AnkiSearchBackend",
    "SmartSearchAddonController",
    "create_controller",
]
