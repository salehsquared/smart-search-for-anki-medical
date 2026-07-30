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

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any

from .anki_actions import (
    ActionKind,
    CollectionAction,
    format_action_message,
    result_ids,
    start_collection_action,
)
from .anki_adapter import (
    AnkiCollectionReader,
    create_result_previewer,
    open_card_ids_in_browser,
    open_native_query_in_browser,
    open_note_ids_in_browser,
    preview_card_ids,
    profile_key,
)
from .backend.anki_reader import iter_collection_notes
from .backend.index import (
    FTS5Unavailable,
    SmartSearchIndex,
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
    FilterChip,
    HighlightSpan,
    IndexState,
    IndexStatus,
    MatchKind,
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
_RXTERMS_RESOURCE = Path("resources") / "medical_vocab" / "rxterms_202607.json.gz"
_NOTETYPE_FILTER = re.compile(
    r"(?<![\w\"])(?P<neg>-?)notetype:(?P<value>\"(?:\\.|[^\"])*\"|\S+)",
    flags=re.IGNORECASE,
)


class _CancelledOperation(RuntimeError):
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


@dataclass(frozen=True, slots=True)
class _OwnedMutation:
    """Exact scope carried through Anki's operation initiator hook."""

    profile_token: int
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

    # ------------------------------------------------------------------
    # Profile lifecycle

    @property
    def active(self) -> bool:
        return self._context is not None

    @property
    def profile_id(self) -> str | None:
        return self._context.key if self._context else None

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
        self._set_index_state(
            IndexState.BUILDING,
            detail="Opening search data.",
        )

        def opened(payload: tuple[_ProfileContext, str | None]) -> None:
            context, alias_error = payload
            if token != self._profile_token:
                self._close_context_in_background(context)
                return
            status = self._publish_profile_context(
                context,
                alias_error,
                auto_rebuild=auto_rebuild,
            )
            on_ready(status)

        def failed(_error: Exception) -> None:
            if token != self._profile_token:
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
            ),
            success=opened,
            failure=failed,
        )
        return self.get_status()

    def _begin_profile_activation(
        self,
    ) -> tuple[int, str, str, str] | None:
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
    ) -> tuple[_ProfileContext, str | None]:
        profile_root = self.data_root / "profiles" / key
        profile_root.mkdir(parents=True, exist_ok=True)
        index = self._open_or_recover_index(profile_root / "search.sqlite3")
        alias_error: str | None = None
        alias_path = self.bundle_root / _RXTERMS_RESOURCE
        if alias_path.is_file():
            try:
                index.load_alias_resource(alias_path)
            except Exception:
                # RxTerms improves search, but lexical/fuzzy search must not be
                # disabled by a damaged optional resource.
                alias_error = "Some medical aliases are temporarily unavailable."

        semantic: SemanticService | None
        semantic_error: str | None = None
        try:
            semantic = self._open_or_recover_semantic(key)
        except Exception as error:
            semantic = None
            semantic_error = str(error)

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
            semantic=semantic,
            note_count=note_count,
            semantic_error=semantic_error,
            semantic_error_recovery=(
                SemanticRecovery.REINDEX if semantic_error else None
            ),
            semantic_snapshot=semantic_snapshot,
            semantic_source_generation=semantic_source_generation,
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
            self._warm_engine(context)
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
            context = self._context
            self._context = None
            self._maintenance_running = False
            self._vocabulary_refresh_running = False
            self._vocabulary_refresh_token = None
            self._semantic_phase = None
            self._semantic_progress = None
        for event in events:
            event.set()
        self._set_index_state(
            IndexState.UNAVAILABLE,
            detail="Open an Anki profile to initialize Smart Search.",
        )
        if context is None:
            return
        self._close_context_in_background(context)

    def _close_context_in_background(self, context: _ProfileContext) -> None:
        def close_retired_context(_collection: Any) -> None:
            # Semantic readers take _search_lock and lexical readers take
            # _engine_lock. Waiting for both in the shared global order keeps
            # profile_will_close non-blocking without closing SQLite beneath
            # an in-flight reader.
            with self._search_lock, self._engine_lock:
                context.engine.semantic_provider = None
                context.index.close()
                semantic = context.semantic
                vector_index = getattr(semantic, "index", None)
                close = getattr(vector_index, "close", None)
                if callable(close):
                    close()

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

    def _open_or_recover_index(self, path: Path) -> SmartSearchIndex:
        try:
            return SmartSearchIndex(path)
        except FTS5Unavailable:
            raise
        except Exception:
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
            return SmartSearchIndex(path)

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

    def load_settings(self) -> UISettings:
        config = self._read_config()
        ui = config.get("ui") if isinstance(config.get("ui"), dict) else {}
        mode_text = str(ui.get("mode", config.get("default_mode", "smart"))).casefold()
        try:
            mode = SearchMode(mode_text)
        except ValueError:
            mode = SearchMode.SMART

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
            width=_bounded_int(ui.get("width", 1040), 760, 4000, 1040),
            height=_bounded_int(ui.get("height", 700), 520, 3000, 700),
        )

    def save_settings(self, settings: UISettings) -> None:
        config = self._read_config()
        config["result_limit"] = _bounded_int(
            settings.result_limit, 10, 200, _DEFAULT_RESULT_LIMIT
        )
        config["preview_enabled"] = bool(settings.preview_enabled)
        config["ui"] = {
            "mode": settings.mode.value,
            "result_limit": config["result_limit"],
            "preview_enabled": config["preview_enabled"],
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
        event = self._new_cancellation()
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
            states_by_note: dict[int, tuple[tuple[int, int, bool], ...]],
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
            states_by_note: dict[int, tuple[tuple[int, int, bool], ...]],
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

        event = self._new_cancellation()
        token = context.token
        previous_count = context.note_count
        self._set_index_state(IndexState.BUILDING, progress=0.0, detail="Starting snapshot.")

        def snapshot(collection: Any) -> tuple[list[IndexedNote], str]:
            def progress(done: int, total: int) -> None:
                _raise_if_cancelled(event)
                fraction = 0.20 * (done / max(1, total))
                self._set_index_state(
                    IndexState.BUILDING,
                    progress=fraction,
                    detail=f"Reading notes {done:,}/{total:,}.",
                )
                on_progress(fraction, f"reading notes {done:,}/{total:,}")

            notes = self.reader.snapshot(collection, progress=progress)
            return notes, _collection_fingerprint(collection)

        def snapshot_success(payload: tuple[list[IndexedNote], str]) -> None:
            notes, fingerprint = payload
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
    ) -> None:
        token = context.token

        def build(_collection: Any) -> int:
            # Lexical index replacement must not race a Semantic reader that
            # later resolves note IDs back through this same external index.
            # Keep the global order search -> engine used everywhere else.
            with self._search_lock, self._engine_lock:
                _raise_if_cancelled(event)

                def lexical_progress(done: int) -> None:
                    _raise_if_cancelled(event)
                    fraction = 0.20 + 0.60 * (done / max(1, len(notes)))
                    self._set_index_state(
                        IndexState.BUILDING,
                        progress=fraction,
                        detail=f"Preparing notes {done:,}/{len(notes):,}.",
                    )
                    on_progress(fraction, f"preparing notes {done:,}/{len(notes):,}")

                count = context.index.rebuild(notes, progress=lexical_progress)
                alias_path = self.bundle_root / _RXTERMS_RESOURCE
                if alias_path.is_file():
                    context.index.load_alias_resource(alias_path)
                _raise_if_cancelled(event)

                context.note_count = count
                context.field_names = context.index.field_names()
                context.fingerprint = fingerprint
                _write_index_fingerprint(context.index, fingerprint)
                context.lexical_generation = context.index.generation
                context.engine = SearchEngine(context.index)
                context.engine.warmup()
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
                    status = context.semantic_snapshot
                    if (
                        status is not None
                        and status.runtime_ready
                        and status.model_ready
                    ):
                        context.semantic_needs_reconcile = True
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
        event = self._new_cancellation()
        token = context.token

        def snapshot_if_needed(
            collection: Any,
        ) -> tuple[str, list[IndexedNote] | None]:
            fingerprint = _collection_fingerprint(collection)
            if check_fingerprint and fingerprint == context.fingerprint:
                return fingerprint, None
            return fingerprint, self.reader.snapshot(collection)

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

    def reconcile_note_ids(
        self,
        note_ids: Iterable[int],
        *,
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

        event = self._new_cancellation()
        token = context.token

        def snapshot(collection: Any) -> list[IndexedNote]:
            return self.reader.snapshot_note_ids(collection, requested)

        self._run_query_op(
            uses_collection=True,
            op=snapshot,
            success=lambda notes: self._reconcile_targeted_snapshot(
                context,
                requested,
                notes,
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
            semantic_status = context.semantic_snapshot
            semantic_delta_safe = bool(
                context.semantic is not None
                and semantic_status is not None
                and semantic_status.runtime_ready
                and semantic_status.model_ready
                and not context.semantic_needs_reconcile
                and context.semantic_source_generation == previous_generation
                and semantic_status.index_count == context.note_count
            )
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

            if has_delta:
                # Drain any in-flight Semantic reader and publish dirty state
                # before changing the lexical metadata it resolves against.
                with self._search_lock, self._engine_lock:
                    _raise_if_cancelled(event)
                    context.engine.defer_vocabulary_refresh()
                    context.engine.semantic_provider = None
                    context.semantic_needs_reconcile = context.semantic is not None

            changed = context.index.apply_upsert(
                plan,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            unchanged = plan.unchanged + (len(plan.changed) - changed)
            deleted = context.index.delete_notes(deleted_candidates)

            added = sum(
                1 for note_id in changed_ids if note_id not in plan.existing_note_ids
            )
            context.note_count = max(0, context.note_count + added - deleted)
            context.field_names = context.index.field_names()
            context.lexical_generation = context.index.generation
            # A targeted read deliberately does not scan 90+ MB of collection
            # text just to refresh the startup fingerprint. Mark it unknown;
            # the next profile-open audit performs the rare canonical check.
            context.fingerprint = None
            _clear_index_fingerprint(context.index)
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
                    self._queue_semantic_delta(context, changed_ids, deleted_ids)
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
                with self._search_lock, self._engine_lock:
                    _raise_if_cancelled(event)
                    context.engine.defer_vocabulary_refresh()
                    context.engine.semantic_provider = None
                    context.semantic_needs_reconcile = context.semantic is not None

            _raise_if_cancelled(event)
            changed = context.index.apply_upsert(
                plan,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            unchanged = plan.unchanged + (len(plan.changed) - changed)
            deleted = context.index.delete_notes(deleted_ids)
            if changed or deleted:
                replacement = SearchEngine(context.index)
                try:
                    replacement.warmup()
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
        if isinstance(error, _CancelledOperation) or event.is_set():
            state = IndexState.READY if previous_count else IndexState.UNAVAILABLE
            self._set_index_state(state, detail="Refresh cancelled.")
            on_success(self.get_status())
            return
        state = IndexState.READY if previous_count else IndexState.ERROR
        message = (
            "Refresh failed. Your previous Smart & Exact search data is still available."
            if previous_count
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
            if self._maintenance_running:
                return False
            self._maintenance_running = True
            return True

    def _end_maintenance(self) -> bool:
        with self._state_lock:
            self._maintenance_running = False
            return False

    # ------------------------------------------------------------------
    # Optional local semantic setup

    def _refresh_existing_semantic_index(self, context: _ProfileContext) -> bool:
        """Start a non-blocking refresh for an already-created vector index.

        Initial semantic setup remains a separate product decision.  This
        helper only preserves an existing semantic index after lexical changes,
        and routes every embedding pass through :meth:`index_semantic`.
        """

        if context is not self._context or not context.semantic_needs_reconcile:
            return False
        semantic = context.semantic
        if semantic is None:
            return False
        with self._state_lock:
            if self._semantic_phase is not None:
                return False
        status = context.semantic_snapshot
        if status is None:
            return False
        if (
            not status.runtime_ready
            or not status.model_ready
            or status.index_count == 0
        ):
            return False
        return self.index_semantic() is not None

    def _queue_semantic_delta(
        self,
        context: _ProfileContext,
        changed_note_ids: Iterable[int],
        deleted_note_ids: Iterable[int],
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
            or status.index_count == 0
        ):
            context.semantic_needs_reconcile = semantic is not None
            return False
        with self._state_lock:
            context.semantic_pending_note_ids.update(
                int(note_id)
                for note_id in changed_note_ids
                if int(note_id) > 0
            )
            context.semantic_pending_deleted_ids.update(
                int(note_id)
                for note_id in deleted_note_ids
                if int(note_id) > 0
            )
            context.semantic_pending_note_ids.difference_update(
                context.semantic_pending_deleted_ids
            )
            context.semantic_needs_reconcile = True
            if self._semantic_phase is not None:
                return True
        return self._start_semantic_delta(context)

    def _start_semantic_delta(self, context: _ProfileContext) -> bool:
        semantic = context.semantic
        if semantic is None or context is not self._context:
            return False
        with self._state_lock:
            if self._semantic_phase is not None:
                return False
            changed_ids = tuple(sorted(context.semantic_pending_note_ids))
            deleted_ids = tuple(sorted(context.semantic_pending_deleted_ids))
            if not changed_ids and not deleted_ids:
                return False
            context.semantic_pending_note_ids.clear()
            context.semantic_pending_deleted_ids.clear()
            self._semantic_phase = "updating"
            self._semantic_progress = 0.0
            self._semantic_phase_detail = "Updating semantic search."

        event = self._new_cancellation()
        token = context.token
        lexical_generation = context.lexical_generation

        def update(_collection: Any) -> tuple[int, bool]:
            _raise_if_cancelled(event)
            with self._search_lock, self._engine_lock:
                _raise_if_cancelled(event)
                context.engine.semantic_provider = None
                semantic.index.clear_source_generation()

            indexed_documents = context.index.documents(changed_ids)
            documents = _semantic_documents_from_index(
                indexed_documents,
                cancel_check=lambda: _raise_if_cancelled(event),
            )
            live_ids = {document.note_id for document in documents}
            removals = tuple(
                sorted(set(deleted_ids) | (set(changed_ids) - live_ids))
            )
            if removals:
                semantic.remove_notes(removals)

            def progress(done: int, total: int) -> None:
                _raise_if_cancelled(event)
                fraction = done / max(1, total)
                with self._state_lock:
                    self._semantic_progress = fraction
                    self._semantic_phase_detail = (
                        f"Updating notes {done:,}/{total:,}."
                    )

            changed = semantic.index_documents(documents, progress=progress)
            _raise_if_cancelled(event)
            with self._state_lock:
                pending = bool(
                    context.semantic_pending_note_ids
                    or context.semantic_pending_deleted_ids
                )
            with self._search_lock, self._engine_lock:
                _raise_if_cancelled(event)
                current = (
                    not pending
                    and lexical_generation == context.lexical_generation
                    and lexical_generation == context.index.generation
                )
                if current:
                    semantic.index.mark_source_generation(lexical_generation)
                context.semantic_needs_reconcile = not current
                context.engine.semantic_provider = semantic if current else None
                with self._state_lock:
                    self._semantic_phase = None
                    self._semantic_progress = None
                    self._semantic_phase_detail = ""
                self._refresh_semantic_snapshot(context)
                self._forget_cancellation(event)
            return changed, current

        def success(result: tuple[int, bool]) -> None:
            if self._cancelled(event, token):
                self._semantic_finished(event, token)
                return
            _changed, current = result
            if self._start_semantic_delta(context):
                return
            if not current:
                self._refresh_existing_semantic_index(context)

        def failure(error: Exception) -> None:
            with self._state_lock:
                context.semantic_pending_note_ids.update(changed_ids)
                context.semantic_pending_deleted_ids.update(deleted_ids)
            self._semantic_failed(error, event, token, context, _noop)

        self._run_query_op(
            uses_collection=False,
            op=update,
            success=success,
            failure=failure,
        )
        return True

    def install_semantic(
        self,
        *,
        on_progress: ProgressCallback = _noop,
        on_success: StatusCallback = _noop,
        on_error: ErrorCallback = _noop,
    ) -> Callable[[], None] | None:
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
                context.semantic_error = None
                context.semantic_error_recovery = None
                self._refresh_semantic_snapshot(context)
            except Exception as error:
                context.semantic_error = str(error)
                context.semantic_error_recovery = SemanticRecovery.REINDEX
                on_error("Semantic search setup could not start. Try again.")
                return None
        if self._semantic_phase is not None:
            on_error("Semantic setup is already running.")
            return None

        event = self._new_cancellation()
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
        context.semantic_error = None
        context.semantic_error_recovery = None
        with self._state_lock:
            self._semantic_phase = "installing"
            self._semantic_progress = 0.0
            self._semantic_phase_detail = "Setting up semantic search."

        def progress(_name: str, completed: int, total: int) -> None:
            _raise_if_cancelled(event)
            fraction = min(1.0, max(0.0, completed / max(1, total)))
            with self._state_lock:
                self._semantic_progress = fraction
                self._semantic_phase_detail = "Setting up semantic search."
            on_progress(fraction, "setting up semantic search")

        def install(_collection: Any) -> None:
            _raise_if_cancelled(event)
            context.semantic.install(progress, repair=repair)
            self._refresh_semantic_snapshot(context)
            _raise_if_cancelled(event)

        def success(_result: None) -> None:
            if self._cancelled(event, token):
                self._semantic_finished(event, token)
                return
            context.semantic_needs_reconcile = True
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
                context.semantic_error = None
                context.semantic_error_recovery = None
                self._refresh_semantic_snapshot(context)
            except Exception as error:
                context.semantic_error = str(error)
                context.semantic_error_recovery = SemanticRecovery.REINDEX
                on_error("Semantic search preparation could not start. Try again.")
                return None
        with self._state_lock:
            text_index_ready = (
                self._index_state is IndexState.READY
                and not self._maintenance_running
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
        if self._semantic_phase is not None:
            on_error("Semantic setup is already running.")
            return None

        event = self._new_cancellation()
        token = context.token
        lexical_generation = context.lexical_generation
        context.semantic_error = None
        context.semantic_error_recovery = None
        with self._state_lock:
            self._semantic_phase = "indexing"
            self._semantic_progress = 0.0
            self._semantic_phase_detail = "Reading local search data."

        self._run_query_op(
            uses_collection=False,
            op=lambda _collection: _semantic_documents_from_lexical_index(
                context.index,
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
        return event.set

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
            known_ids = set(semantic.index.known_hashes())
            current_ids = {document.note_id for document in documents}
            semantic.remove_notes(tuple(known_ids - current_ids))

            def progress(done: int, total: int) -> None:
                _raise_if_cancelled(event)
                fraction = done / max(1, total)
                with self._state_lock:
                    self._semantic_progress = fraction
                    self._semantic_phase_detail = (
                        f"Preparing notes {done:,}/{total:,}."
                    )
                on_progress(fraction, f"preparing notes {done:,}/{total:,}")

            changed = semantic.index_documents(documents, progress=progress)
            _raise_if_cancelled(event)
            # Publish the completed generation in this worker.  QueryOp
            # success callbacks run on Anki's GUI thread and must never wait
            # for a live semantic search to release these locks.
            with self._search_lock, self._engine_lock:
                _raise_if_cancelled(event)
                current = (
                    lexical_generation == context.lexical_generation
                    and lexical_generation == context.index.generation
                )
                if current:
                    semantic.index.mark_source_generation(lexical_generation)
                context.semantic_needs_reconcile = not current
                context.engine.semantic_provider = semantic if current else None
                with self._state_lock:
                    self._semantic_phase = None
                    self._semantic_progress = None
                    self._semantic_phase_detail = ""
                self._refresh_semantic_snapshot(context)
                self._forget_cancellation(event)
            return changed, current

        def success(result: tuple[int, bool]) -> None:
            if self._cancelled(event, token):
                self._semantic_finished(event, token)
                return
            _changed, current = result
            on_progress(1.0, "semantic search ready")
            on_success(self.get_status())
            if not current:
                if not self._start_semantic_delta(context):
                    self._refresh_existing_semantic_index(context)

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
        if not cancelled:
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
    ) -> None:
        """Schedule one official Anki ``QueryOp``.

        This small seam is intentionally overrideable in tests.  QueryOps that
        touch the collection remain serialized; external FTS/model work opts
        out with ``without_collection()``.
        """

        from aqt.operations import QueryOp

        operation = QueryOp(parent=self.mw, op=op, success=success).failure(failure)
        if not uses_collection:
            operation.without_collection()
        operation.run_in_background()

    def _new_cancellation(self) -> threading.Event:
        event = threading.Event()
        with self._state_lock:
            self._active_cancellations.add(event)
        return event

    def _forget_cancellation(self, event: threading.Event) -> None:
        with self._state_lock:
            self._active_cancellations.discard(event)

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
            if self._vocabulary_refresh_running:
                return False
            self._vocabulary_refresh_running = True
            self._vocabulary_refresh_token = context.token
        token = context.token
        generation = context.index.generation

        def build(_collection: Any) -> bool:
            replacement = SearchEngine(context.index)
            replacement.warmup()
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
            with self._state_lock:
                if self._vocabulary_refresh_token == token:
                    self._vocabulary_refresh_running = False
                    self._vocabulary_refresh_token = None
            if token == self._profile_token:
                on_success(bool(current))

        def failure(error: Exception) -> None:
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
        token = context.token

        def warm(_collection: Any) -> None:
            with self._search_lock, self._engine_lock:
                if token != self._profile_token:
                    return
                context.engine.warmup()

        self._run_query_op(
            uses_collection=False,
            op=warm,
            success=lambda _result: self._warm_semantic(context),
            failure=_noop,
        )

    def _warm_semantic(self, context: _ProfileContext) -> None:
        """Prepare the optional model before the first interactive query."""

        semantic = context.semantic
        status = context.semantic_snapshot
        if (
            semantic is None
            or status is None
            or not status.ready
            or context.semantic_needs_reconcile
            or context.token != self._profile_token
        ):
            return

        def warm(_collection: Any) -> None:
            try:
                with self._search_lock:
                    with self._state_lock:
                        semantic_phase = self._semantic_phase
                    status = context.semantic_snapshot
                    if (
                        context.token != self._profile_token
                        or semantic_phase is not None
                        or context.semantic_needs_reconcile
                        or status is None
                        or not status.ready
                    ):
                        return
                    semantic.warmup()
            finally:
                self._refresh_semantic_snapshot(context)

        self._run_query_op(
            uses_collection=False,
            op=warm,
            success=_noop,
            failure=_noop,
        )


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
        self._reconcile_timer: Any | None = None
        self._reconcile_request_lock = threading.Lock()
        self._pending_reconcile_note_ids: set[int] = set()
        self._pending_reconcile_deleted_ids: set[int] = set()
        self._reconcile_full = False
        self._reconcile_startup_check = False
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
        self._dialog_refresh_timer: Any | None = None
        self._dialog_refresh_lock = threading.Lock()
        self._dialog_refresh_queued = False
        self._dialog_refresh_last = 0.0
        self._card_state_refresh_timer: Any | None = None
        self._vocabulary_refresh_timer: Any | None = None
        self._hooks: list[tuple[Any, Callable[..., Any]]] = []

    def start(self) -> "SmartSearchAddonController":
        if self._started:
            return self
        from anki import hooks as anki_hooks
        from aqt import gui_hooks
        from aqt.qt import QTimer

        self._started = True
        self._append_hook(gui_hooks.profile_did_open, self._on_profile_open)
        self._append_hook(gui_hooks.profile_will_close, self._on_profile_will_close)
        self._append_hook(gui_hooks.operation_did_execute, self._on_operation)
        self._append_hook(
            gui_hooks.add_cards_did_add_note,
            self._capture_added_note,
        )
        self._append_hook(gui_hooks.sync_did_finish, self._on_sync_finished)
        self._append_hook(
            gui_hooks.collection_did_temporarily_close,
            self._on_collection_reopened,
        )
        self._append_hook(anki_hooks.note_will_flush, self._capture_note_flush)
        self._append_hook(
            anki_hooks.notes_will_be_deleted,
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
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
        self._pending_preview_result = None
        self._clear_pending_reconciles()
        self._clear_captured_notes()
        self._semantic_autostart_token = None
        self._semantic_autostart_attempted_token = None
        self._close_dialog()
        self.backend.deactivate_profile()
        for hook, callback in reversed(self._hooks):
            try:
                hook.remove(callback)
            except ValueError:
                pass
        self._hooks.clear()
        self._started = False

    def show_search(self) -> None:
        """Open or focus the keyboard-first Smart Search palette."""

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
            )
            dialog = SearchDialog(self.mw, about=about)
            ui_controller = SearchController(self.backend, dialog)
            ui_controller.set_browser_opener(
                lambda result: _open_results_in_browser((result,))
            )
            ui_controller.set_multi_browser_opener(_open_results_in_browser)
            ui_controller.semanticInstallRequested.connect(self._install_semantic)
            ui_controller.semanticIndexRequested.connect(self._index_semantic)
            ui_controller.textIndexRebuilt.connect(self._text_index_rebuilt)
            ui_controller.flagRequested.connect(self._flag_results)
            ui_controller.suspensionRequested.connect(
                self._set_results_suspended
            )
            ui_controller.tagActionRequested.connect(self._change_result_tags)
            ui_controller.previewPreferenceChanged.connect(
                self._preview_preference_changed
            )
            dialog.previewToggleRequested.connect(self._toggle_previewer)
            dialog.previewResultChanged.connect(
                self._preview_selection_changed
            )
            dialog.dialogClosed.connect(self._close_previewer)
            dialog.destroyed.connect(
                lambda _object=None, d=dialog, c=ui_controller:
                self._search_dialog_destroyed(d, c)
            )

            config = self.backend._read_config()
            debounce = _bounded_int(
                config.get("debounce_ms", 160), 50, 2000, 160
            )
            try:
                dialog._debounce.setInterval(debounce)
            except Exception:
                pass
            self._dialog = dialog
            self._ui_controller = ui_controller

        self._dialog.show()
        if self._ui_controller is not None:
            self._ui_controller.resume()
        self._dialog.raise_()
        self._dialog.activateWindow()
        self._dialog.focus_query()

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

    # ---------------------------------------------------------- card preview

    def _toggle_previewer(self, result: object | None) -> None:
        if (
            not self._preview_feature_enabled()
            or not preview_card_ids(result)
        ):
            self._close_previewer()
            return
        if self._previewer is not None:
            try:
                self._previewer.raise_()
                self._previewer.activateWindow()
            except Exception:
                self._close_previewer()
            else:
                return

        dialog = self._dialog
        if dialog is None or getattr(self.mw, "col", None) is None:
            return
        previewer = None
        try:
            previewer = create_result_previewer(
                self.mw,
                initial_result=result,
                on_close=self._previewer_closed,
                on_previous=lambda: self._move_preview_result(-1),
                on_next=lambda: self._move_preview_result(1),
                has_previous=lambda: self._can_move_preview_result(-1),
                has_next=lambda: self._can_move_preview_result(1),
            )
            self._previewer = previewer
            previewer.open()
            dialog.set_preview_active(True)
            # Leave the native Preview in front. Its own Up/Down shortcuts
            # move the underlying result list, so restoring the search dialog
            # here only hides the newly opened window on macOS.
            previewer.raise_()
            previewer.activateWindow()
        except Exception as error:
            self._previewer = None
            if previewer is not None:
                try:
                    previewer.cancel_timer()
                    previewer.close()
                except Exception:
                    pass
            try:
                dialog.set_preview_active(False)
            except RuntimeError:
                pass
            self._show_error(f"Could not open card preview: {error}")

    def _preview_selection_changed(self, result: object | None) -> None:
        if not self._preview_feature_enabled():
            self._close_previewer()
            return
        previewer = self._previewer
        if not preview_card_ids(result):
            self._close_previewer()
            return
        if previewer is None:
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
        except Exception as error:
            self._close_previewer()
            self._show_error(f"Could not update card preview: {error}")

    def _preview_feature_enabled(self) -> bool:
        ui_controller = self._ui_controller
        return bool(
            ui_controller is not None
            and ui_controller.settings.preview_enabled
        )

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
        if dialog is None or self._previewer is not None:
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
            self._close_previewer()
            return
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
            self._show_error(f"Could not refresh card preview: {error}")

    def _previewer_closed(self) -> None:
        self._previewer = None
        dialog = self._dialog
        if dialog is not None:
            try:
                dialog.set_preview_active(False)
            except RuntimeError:
                pass

    def _close_previewer(self) -> None:
        timer = self._preview_open_timer
        if timer is not None:
            timer.stop()
        self._pending_preview_result = None
        previewer = self._previewer
        self._previewer = None
        if previewer is not None:
            try:
                previewer.cancel_timer()
                previewer.close()
            except Exception:
                pass
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

        if self._dialog is dialog:
            self._dialog = None
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
        search_generation = None
        ui_controller = self._ui_controller
        context = self.backend._context
        if (
            dialog is not None
            and ui_controller is not None
            and context is not None
            and _collection_action_requires_requery(
                action,
                dialog.query(),
                field_names=context.field_names,
            )
        ):
            capture = getattr(ui_controller, "capture_search_generation", None)
            if callable(capture):
                search_generation = capture()
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
                    search_generation=search_generation,
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
        search_generation: Any | None = None,
    ) -> None:
        message = format_action_message(action, outcome)
        dialog = self._dialog
        if dialog is not None:
            try:
                if action.kind is ActionKind.FLAG:
                    dialog.apply_card_state_change(
                        action.card_ids,
                        flag=action.flag,
                    )
                elif action.kind is ActionKind.SUSPEND:
                    dialog.apply_card_state_change(
                        action.card_ids,
                        suspended=True,
                    )
                elif action.kind is ActionKind.UNSUSPEND:
                    dialog.apply_card_state_change(
                        action.card_ids,
                        suspended=False,
                    )
                dialog.finish_batch_action(True, message)
            except RuntimeError:
                pass
        self._refresh_previewer()
        self._show_message(message)
        if search_generation is None or int(getattr(outcome, "changed", 0)) <= 0:
            return
        ui_controller = self._ui_controller
        rerun = getattr(ui_controller, "rerun_search_if_current", None)
        if callable(rerun):
            rerun(search_generation)

    def _collection_action_failed(self, error: Exception) -> None:
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
        initial_check: bool = False,
        full: bool = False,
    ) -> None:
        if not self.backend.active or self._reconcile_timer is None:
            return
        if not self._auto_reconcile:
            return
        clean_note_ids = _positive_ids(note_ids or ())
        clean_deleted_ids = _positive_ids(deleted_note_ids)
        if (
            (clean_note_ids or clean_deleted_ids)
            and self._vocabulary_refresh_timer is not None
        ):
            self._vocabulary_refresh_timer.stop()
        if note_ids is None and not clean_deleted_ids and not initial_check:
            full = True
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
        else:
            delay = _bounded_int(
                self._reconcile_debounce_ms,
                250,
                10_000,
                _DEFAULT_DEBOUNCE_MS,
            )
        self._reconcile_timer.start(delay)

    def _run_reconcile(self) -> None:
        with self._reconcile_request_lock:
            note_ids = tuple(sorted(self._pending_reconcile_note_ids))
            deleted_ids = tuple(sorted(self._pending_reconcile_deleted_ids))
            full = self._reconcile_full
            startup_check = self._reconcile_startup_check
        requested_ids = tuple(sorted(set(note_ids) | set(deleted_ids)))

        ran_targeted = False
        if full:
            started = self.backend.reconcile(
                check_fingerprint=False,
                on_success=lambda _status: self._refresh_dialog(),
                on_error=self._show_error,
            )
        elif requested_ids:
            ran_targeted = True
            started = self.backend.reconcile_note_ids(
                requested_ids,
                on_success=self._targeted_reconcile_succeeded,
                on_error=self._show_error,
            )
        elif startup_check:
            started = self.backend.reconcile(
                check_fingerprint=True,
                on_success=lambda _status: self._refresh_dialog(),
                on_error=self._show_error,
            )
        else:
            return

        if not started:
            # A rebuild/reconcile already owns the maintenance lane. Preserve
            # this exact request and retry without promoting it to a full scan.
            if self._reconcile_timer is not None:
                self._reconcile_timer.start(250)
            return
        with self._reconcile_request_lock:
            self._pending_reconcile_note_ids.difference_update(note_ids)
            self._pending_reconcile_deleted_ids.difference_update(deleted_ids)
            if full:
                self._reconcile_full = False
            if startup_check:
                if not ran_targeted:
                    self._reconcile_startup_check = False
        if ran_targeted and startup_check and self._reconcile_timer is not None:
            # The exact edit wins immediately; the startup audit returns to an
            # idle delay instead of turning that edit into a full-profile scan.
            self._reconcile_timer.start(self._startup_reconcile_delay_ms)

    def _targeted_reconcile_succeeded(self, _status: IndexStatus) -> None:
        self._refresh_dialog()
        timer = self._vocabulary_refresh_timer
        if timer is not None:
            timer.start(self._vocabulary_refresh_delay_ms)

    def _run_vocabulary_refresh(self) -> None:
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

        context = self.backend._context
        token = self._semantic_autostart_token
        if context is None or token is None or context.token != token:
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
        status = self.backend.get_status()
        if maintenance_running or status.state is IndexState.BUILDING:
            self._schedule_semantic_autostart(
                delay_ms=_SEMANTIC_AUTOSTART_RETRY_MS
            )
            return
        if status.state is not IndexState.READY:
            return
        semantic = status.semantic
        if semantic is None or semantic.state is not SemanticState.MODEL_READY:
            # READY/INDEXING need no second worker.  NOT_INSTALLED,
            # UNSUPPORTED, and ERROR remain explicit user-facing actions.
            return

        self._semantic_autostart_attempted_token = token
        self.backend.index_semantic(
            on_progress=lambda _fraction, _detail: self._queue_dialog_refresh(),
            on_success=lambda _status: self._run_on_main(
                self._refresh_dialog_now
            ),
            # Automatic startup errors stay passive in the semantic status
            # area; an unexpected modal dialog at every launch is disruptive.
            on_error=lambda _message: self._run_on_main(
                self._refresh_dialog_now
            ),
        )
        self._refresh_dialog()

    def _on_profile_open(self) -> None:
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
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
        self._semantic_autostart_token = None
        self._semantic_autostart_attempted_token = None
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
        if status.state is IndexState.READY:
            self.schedule_reconcile(initial_check=True)
        self._schedule_semantic_autostart()
        self._refresh_dialog()

    def _on_profile_will_close(self) -> None:
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
        with self._dialog_refresh_lock:
            self._dialog_refresh_queued = False
        self._clear_pending_reconciles()
        self._clear_captured_notes()
        self._semantic_autostart_token = None
        self._semantic_autostart_attempted_token = None
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
        # Owned flag/suspension operations update their exact targets in the
        # success callback and may rerun a card-filtered query there. Avoid a
        # redundant state refresh racing that newer response.
        if bool(getattr(changes, "card", False)) and not isinstance(
            handler, _OwnedMutation
        ):
            self._refresh_visible_card_states()

        context = self.backend._context
        if isinstance(handler, _OwnedMutation):
            if context is None or handler.profile_token != context.token:
                return
            action = handler.action
            if action.kind in (ActionKind.ADD_TAGS, ActionKind.REMOVE_TAGS):
                self.schedule_reconcile(
                    note_ids=action.note_ids,
                )
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
                note_ids=self._consume_editor_flush_ids(handler_note_id)
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

    def _on_sync_finished(self) -> None:
        self.schedule_reconcile()
        self._refresh_visible_card_states()

    def _on_collection_reopened(self, _collection: Any) -> None:
        self.schedule_reconcile()

    def _install_semantic(self) -> None:
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

    def _text_index_rebuilt(self) -> None:
        """Re-arm first-time semantic indexing after a later successful rebuild."""

        config = self.backend._read_config()
        if not bool(config.get("auto_semantic_index", True)):
            return
        self._semantic_autostart_attempted_token = None
        self._schedule_semantic_autostart(delay_ms=0)

    def _index_semantic(self) -> None:
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
        self._show_message("Semantic search is ready for this profile.")

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
        timer = self._card_state_refresh_timer
        if timer is not None:
            timer.start(120)
            return
        self._refresh_visible_card_states_now()

    def _refresh_visible_card_states_now(self) -> None:
        """Refresh only flag/suspension metadata for the current result rows."""

        dialog = self._dialog
        if dialog is None:
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

    def _close_dialog(self) -> None:
        self._close_previewer()
        dialog = self._dialog
        ui_controller = self._ui_controller
        self._dialog = None
        self._ui_controller = None
        if dialog is not None:
            try:
                dialog.close()
            except RuntimeError:
                pass
        if ui_controller is not None:
            try:
                ui_controller.dispose()
            except (AttributeError, RuntimeError):
                pass
        if dialog is not None:
            try:
                dialog.deleteLater()
            except RuntimeError:
                pass

    def _append_hook(self, hook: Any, callback: Callable[..., Any]) -> None:
        hook.append(callback)
        self._hooks.append((hook, callback))

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
    states_by_note: dict[int, tuple[tuple[int, int, bool], ...]],
) -> SearchResponse:
    """Return a response whose card IDs and state reflect the live collection."""

    return replace(
        response,
        results=_results_with_live_card_states(response.results, states_by_note),
    )


def _results_with_live_card_states(
    visible: Sequence[SearchResult],
    states_by_note: dict[int, tuple[tuple[int, int, bool], ...]],
) -> tuple[SearchResult, ...]:
    results: list[SearchResult] = []
    for result in visible:
        snapshots = states_by_note.get(int(result.note_id))
        if snapshots is None:
            results.append(result)
            continue
        card_states = tuple(
            CardState(card_id=card_id, flag=flag, suspended=suspended)
            for card_id, flag, suspended in snapshots
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


def _collection_action_requires_requery(
    action: CollectionAction,
    query: str,
    *,
    field_names: Iterable[str] = (),
) -> bool:
    """Whether an action can change membership in the visible native filter.

    Most actions can be represented immediately by updating the row's live
    state. Native card/note filters are different: after ``is:suspended`` is
    unsuspended, for example, the card must disappear. Complex expressions
    are conservatively rerun because their full meaning is delegated to Anki.
    """

    relevant_keys = {
        ActionKind.FLAG: {"flag"},
        ActionKind.SUSPEND: {"is", "prop"},
        ActionKind.UNSUSPEND: {"is", "prop"},
        ActionKind.ADD_TAGS: {"tag"},
        ActionKind.REMOVE_TAGS: {"tag"},
    }.get(action.kind, set())
    if not relevant_keys:
        return False

    parsed = QueryParser(field_names=field_names).parse(_canonical_query(query))
    if parsed.native_search_required:
        # The complete expression is evaluated by Anki. Rerunning is cheap,
        # asynchronous, and safer than trying to infer Boolean membership.
        return True

    for raw_filter in parsed.anki_filters:
        candidate = str(raw_filter).strip()
        if candidate.startswith("-"):
            candidate = candidate[1:]
        if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
            candidate = candidate[1:-1]
        key, separator, _value = candidate.partition(":")
        if separator and key.strip('"').casefold() in relevant_keys:
            return True
    return False


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


def _collection_fingerprint(collection: Any) -> str:
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
    card_row = collection.db.first(
        """
        SELECT count(*), coalesce(sum(did), 0), coalesce(sum(odid), 0)
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
