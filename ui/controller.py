"""Glue between the dialog and the async backend protocol.

Responsibilities:

- own monotonically increasing request IDs and cancel/ignore stale responses;
- marshal worker-thread callbacks onto the GUI thread via Qt signals
  (a signal emitted from a worker thread is delivered queued to slots on
  objects living in the GUI thread);
- own the "open in Browser" integration point (``set_browser_opener``),
  so neither the dialog nor the result delegate knows about Anki;
- persist UI settings through the backend.
"""

from __future__ import annotations

from typing import Callable, Optional

from .contracts import (
    CancelCallback,
    Correction,
    IndexState,
    PreviewDefault,
    RelatedCardsRequest,
    RelatedCardsResponse,
    ResultViewState,
    SearchBackend,
    SearchRequest,
    SearchResponse,
    SearchResult,
    UISettings,
    remove_filter_token,
    SearchMode,
)
from .dialog import SearchDialog
from .widgets import QObject, QTimer, pyqtSignal

BrowserOpener = Callable[[SearchResult], None]
MultiBrowserOpener = Callable[[tuple[SearchResult, ...]], None]
SearchGeneration = tuple[int, str, SearchMode]


class SearchController(QObject):
    # Host integration point: connect this to open Anki's Browser.
    resultOpenRequested = pyqtSignal(object)  # SearchResult
    openAllRequested = pyqtSignal(object)  # tuple[SearchResult, ...]
    semanticInstallRequested = pyqtSignal()
    semanticIndexRequested = pyqtSignal()
    updateRequested = pyqtSignal()
    textIndexRebuilt = pyqtSignal()
    flagRequested = pyqtSignal(object, int)
    suspensionRequested = pyqtSignal(object, bool)
    burialRequested = pyqtSignal(object, bool)
    createCopyRequested = pyqtSignal(object)
    # Preserve Anki's timestamp-sized 64-bit deck IDs across this signal hop.
    deckChangeRequested = pyqtSignal(object, object, str)
    tagActionRequested = pyqtSignal(object, bool)
    undoRequested = pyqtSignal()
    previewPreferenceChanged = pyqtSignal(bool)
    previewDefaultChanged = pyqtSignal(object)
    initialPreviewRequested = pyqtSignal(object)  # SearchResult | None
    previewAutoOpenCancelled = pyqtSignal()

    # Internal marshalling signals (emitted from arbitrary threads).
    _sig_search_success = pyqtSignal(int, object)
    _sig_search_error = pyqtSignal(int, str)
    _sig_related_success = pyqtSignal(int, object)
    _sig_related_error = pyqtSignal(int, str)
    _sig_restored_states = pyqtSignal(int, object)
    _sig_rebuild_progress = pyqtSignal(float, str)
    _sig_rebuild_success = pyqtSignal(object)
    _sig_rebuild_error = pyqtSignal(str)
    _sig_decks_success = pyqtSignal(int, object)
    _sig_decks_error = pyqtSignal(int, str)

    def __init__(
        self,
        backend: SearchBackend,
        dialog: SearchDialog,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._dialog = dialog
        self._browser_opener: Optional[BrowserOpener] = None
        self._multi_browser_opener: Optional[MultiBrowserOpener] = None

        self._request_counter = 0
        self._active_request_id: Optional[int] = None
        self._cancel_search: Optional[CancelCallback] = None
        self._cancel_rebuild: Optional[CancelCallback] = None
        self._deck_request_counter = 0
        self._active_deck_request_id: Optional[int] = None
        self._cancel_deck_load: Optional[CancelCallback] = None
        self._dismissed: set[Correction] = set()
        self._force_literal = False
        self._last_query = ""
        self._related_origin: (
            tuple[SearchResponse, tuple[Correction, ...], ResultViewState]
            | None
        ) = None
        self._active = False
        self._disposed = False

        self._settings: UISettings = backend.load_settings()

        # Marshalled backend callbacks.
        self._sig_search_success.connect(self._on_search_success)
        self._sig_search_error.connect(self._on_search_error)
        self._sig_related_success.connect(self._on_related_success)
        self._sig_related_error.connect(self._on_related_error)
        self._sig_restored_states.connect(self._on_restored_states)
        self._sig_rebuild_progress.connect(self._on_rebuild_progress)
        self._sig_rebuild_success.connect(self._on_rebuild_success)
        self._sig_rebuild_error.connect(self._on_rebuild_error)
        self._sig_decks_success.connect(self._on_decks_success)
        self._sig_decks_error.connect(self._on_decks_error)

        # Dialog intents.
        dialog.queryEdited.connect(self._on_query_edited)
        dialog.searchRequested.connect(self.submit_search)
        dialog.modeSelected.connect(self._on_mode_selected)
        dialog.openRequested.connect(self.open_result)
        dialog.openAllRequested.connect(self.open_all_results)
        dialog.relatedRequested.connect(self.find_related_cards)
        dialog.relatedBackRequested.connect(self.restore_original_search)
        dialog.filterRemoveRequested.connect(self._on_filter_removed)
        dialog.correctionDismissRequested.connect(self._on_correction_dismissed)
        dialog.correctionLiteralRequested.connect(self._on_correction_literal)
        dialog.rebuildRequested.connect(self.start_rebuild)
        dialog.semanticInstallRequested.connect(self.semanticInstallRequested)
        dialog.semanticIndexRequested.connect(self.semanticIndexRequested)
        dialog.updateRequested.connect(self.updateRequested)
        dialog.flagRequested.connect(self.flagRequested)
        dialog.suspensionRequested.connect(self.suspensionRequested)
        dialog.burialRequested.connect(self.burialRequested)
        dialog.createCopyRequested.connect(self.createCopyRequested)
        dialog.deckChangeRequested.connect(self.deckChangeRequested)
        dialog.tagActionRequested.connect(self.tagActionRequested)
        dialog.undoRequested.connect(self.undoRequested)
        dialog.deckPickerRequested.connect(self.load_decks)
        dialog.settingsChanged.connect(self._on_settings_changed)
        dialog.dialogClosed.connect(self._on_dialog_closed)

        # Apply persisted settings.
        dialog.set_mode(self._settings.mode)
        dialog.set_result_limit(self._settings.result_limit)
        dialog.set_preview_enabled(self._settings.preview_enabled)
        dialog.set_preview_default(self._settings.preview_default)
        dialog.resize(self._settings.width, self._settings.height)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self.refresh_status)
        self.resume()

    # ---------------------------------------------------------- integration

    def set_browser_opener(self, opener: Optional[BrowserOpener]) -> None:
        """Install the host callback that opens a result in Anki's Browser."""
        self._browser_opener = opener

    def set_multi_browser_opener(self, opener: Optional[MultiBrowserOpener]) -> None:
        """Install the host callback that opens all visible notes together."""
        self._multi_browser_opener = opener

    @property
    def settings(self) -> UISettings:
        return self._settings

    def resume(self) -> bool:
        """Resume a hidden dialog's lightweight status updates."""

        if self._disposed:
            return False
        self._active = True
        if not self._status_timer.isActive():
            self._status_timer.start()
        self.refresh_status()
        return True

    def pause(self) -> None:
        """Stop all dialog work while its window is hidden."""

        if self._disposed:
            return
        if self._related_origin is not None:
            self._restore_related_origin()
        else:
            self._dialog.leave_related_view()
        self._active = False
        self._status_timer.stop()
        self._invalidate_pending_search()
        if self._cancel_rebuild is not None:
            try:
                self._cancel_rebuild()
            except Exception:  # noqa: BLE001
                pass
            self._cancel_rebuild = None
        self._cancel_pending_deck_load()

    def dispose(self) -> None:
        """Permanently detach this controller before Qt deletes its dialog."""

        if self._disposed:
            return
        self._active = False
        self._disposed = True
        self._related_origin = None
        try:
            self._dialog.leave_related_view()
        except RuntimeError:
            pass
        self._status_timer.stop()
        self._invalidate_pending_search()
        if self._cancel_rebuild is not None:
            try:
                self._cancel_rebuild()
            except Exception:  # noqa: BLE001
                pass
            self._cancel_rebuild = None
        self._cancel_pending_deck_load()

    # ---------------------------------------------------------- deck picker

    def load_decks(self) -> None:
        """Refresh deck choices without blocking search or the GUI thread."""

        if self._disposed or not self._active:
            return
        self._cancel_pending_deck_load()
        self._deck_request_counter += 1
        request_id = self._deck_request_counter
        self._active_deck_request_id = request_id
        loader = getattr(self._backend, "load_decks", None)
        if not callable(loader):
            self._active_deck_request_id = None
            self._dialog.show_deck_picker_error(
                "Deck choices are unavailable in this Anki version."
            )
            return
        try:
            cancel = loader(
                on_success=lambda catalog: self._sig_decks_success.emit(
                    request_id, catalog
                ),
                on_error=lambda message: self._sig_decks_error.emit(
                    request_id, str(message)
                ),
            )
        except Exception as error:  # noqa: BLE001 - normalize host failures
            if self._active_deck_request_id == request_id:
                self._active_deck_request_id = None
                self._dialog.show_deck_picker_error(str(error))
            return
        if self._active_deck_request_id == request_id:
            self._cancel_deck_load = cancel

    def _cancel_pending_deck_load(self) -> None:
        self._deck_request_counter += 1
        self._active_deck_request_id = None
        if self._cancel_deck_load is not None:
            try:
                self._cancel_deck_load()
            except Exception:  # noqa: BLE001 - cancellation is best effort
                pass
            self._cancel_deck_load = None

    def _on_decks_success(self, request_id: int, catalog) -> None:
        if self._disposed or not self._active:
            return
        if request_id != self._active_deck_request_id:
            return
        self._active_deck_request_id = None
        self._cancel_deck_load = None
        self._dialog.show_deck_catalog(catalog)

    def _on_decks_error(self, request_id: int, message: str) -> None:
        if self._disposed or not self._active:
            return
        if request_id != self._active_deck_request_id:
            return
        self._active_deck_request_id = None
        self._cancel_deck_load = None
        self._dialog.show_deck_picker_error(message)

    def capture_search_generation(self) -> SearchGeneration:
        """Return an opaque snapshot for a later conditional refresh.

        Collection operations finish asynchronously.  Callers can retain this
        token when an operation starts, then use :meth:`rerun_search_if_current`
        without interrupting a query edit, mode switch, or newer search that
        happened while the operation was running.
        """

        return (
            self._request_counter,
            self._dialog.query().strip(),
            self._dialog.mode(),
        )

    def rerun_search_if_current(self, expected: SearchGeneration) -> bool:
        """Rerun the visible query only when no newer search intent exists."""

        current = self.capture_search_generation()
        if current != expected or not current[1]:
            return False
        self.submit_search(current[1])
        return True

    # --------------------------------------------------------------- search

    def submit_search(self, query: str) -> None:
        if self._disposed or not self._active:
            return
        self._abandon_related_view()
        query = query.strip()
        if query != self._last_query:
            self._dismissed.clear()
            self._force_literal = False
        self._last_query = query
        if not query:
            self._invalidate_pending_search()
            self._dialog.show_help()
            return

        status = self._backend.get_status()
        if status.state is not IndexState.READY:
            self._invalidate_pending_search()
            self._dialog.show_status(status)
            self._dialog.show_index_blocked(status)
            return
        if self._dialog.mode() is SearchMode.SEMANTIC:
            if status.semantic is None or not status.semantic.ready:
                self._invalidate_pending_search()
                self._dialog.show_status(status)
                self._dialog.show_semantic_needed(status.semantic)
                return

        self._invalidate_pending_search()
        request_id = self._request_counter
        request = SearchRequest(
            request_id=request_id,
            query=query,
            mode=self._dialog.mode(),
            # Active chips come back from the backend parse that actually
            # constrained the search. A separate UI parser can otherwise show
            # a token as active while Anki treated it as ordinary text.
            filters=(),
            literal=self._force_literal,
            limit=self._settings.result_limit,
        )
        self._active_request_id = request_id
        try:
            cancel = self._backend.submit_search(
                request,
                on_success=lambda response: self._sig_search_success.emit(
                    request_id, response
                ),
                on_error=lambda message: self._sig_search_error.emit(
                    request_id, message
                ),
            )
        except Exception as error:  # noqa: BLE001 - normalize host failures
            if self._active_request_id == request_id:
                self._active_request_id = None
                self._cancel_search = None
                self._dialog.show_error(str(error))
            return

        # A synchronous test/backend may already have delivered its result.
        # Only show the running state when this request is still active after
        # the backend has accepted it.
        if self._active_request_id == request_id:
            self._cancel_search = cancel
            self._dialog.show_searching()

    def _on_query_edited(self, _query: str) -> None:
        """Cancel old work immediately; the debounce may dispatch later."""

        self._abandon_related_view()
        self._invalidate_pending_search()

    def _invalidate_pending_search(self) -> None:
        self._request_counter += 1
        self._active_request_id = None
        self._cancel_pending_search()
        self.previewAutoOpenCancelled.emit()

    def _cancel_pending_search(self) -> None:
        if self._cancel_search is not None:
            try:
                self._cancel_search()
            except Exception:  # noqa: BLE001 - cancellation is best effort
                pass
            self._cancel_search = None

    def _on_search_success(self, request_id: int, response: SearchResponse) -> None:
        if self._disposed or not self._active:
            return
        if (
            request_id != self._request_counter
            or request_id != self._active_request_id
        ):
            return  # stale response from a superseded request
        self._active_request_id = None
        self._cancel_search = None
        visible = [c for c in response.corrections if c not in self._dismissed]
        self._dialog.show_response(response, visible)
        # A newly accepted result set is the one programmatic selection that
        # should open its preview without moving focus out of the query field.
        # Emit here, after stale-response gating, rather than from
        # ``show_response()``: correction redraws and restoring a related-card
        # origin also render through that method.  ``None`` closes a preview
        # left behind by an accepted empty response.
        self.initialPreviewRequested.emit(
            response.results[0] if response.results else None
        )

    def _on_search_error(self, request_id: int, message: str) -> None:
        if self._disposed or not self._active:
            return
        if (
            request_id != self._request_counter
            or request_id != self._active_request_id
        ):
            return
        self._active_request_id = None
        self._cancel_search = None
        self._dialog.show_error(message)

    # ---------------------------------------------------------- related cards

    def find_related_cards(self, results) -> None:
        if self._disposed or not self._active:
            return
        targets = tuple(results or ())
        note_ids = tuple(
            dict.fromkeys(
                int(result.note_id)
                for result in targets
                if int(result.note_id) > 0
            )
        )
        if not note_ids:
            return
        tags = tuple(
            dict.fromkeys(
                str(tag)
                for result in targets
                for tag in result.tags
                if str(tag).strip()
            )
        )
        if self._related_origin is None:
            response = self._dialog.last_response()
            if response is None:
                return
            self._related_origin = (
                response,
                self._dialog.last_corrections(),
                self._dialog.capture_result_view_state(),
            )

        self._invalidate_pending_search()
        request_id = self._request_counter
        request = RelatedCardsRequest(
            request_id=request_id,
            note_ids=note_ids,
            tags=tags,
            limit=self._settings.result_limit,
        )
        self._active_request_id = request_id
        submit = getattr(self._backend, "submit_related_cards", None)
        if not callable(submit):
            self._active_request_id = None
            self._restore_related_origin(
                "Related-card search is unavailable in this Anki version."
            )
            return
        try:
            cancel = submit(
                request,
                on_success=lambda response: self._sig_related_success.emit(
                    request_id, response
                ),
                on_error=lambda message: self._sig_related_error.emit(
                    request_id, str(message)
                ),
            )
        except Exception as error:  # noqa: BLE001
            if self._active_request_id == request_id:
                self._active_request_id = None
                self._cancel_search = None
                self._restore_related_origin(
                    f"Could not find related cards: {error}"
                )
            return
        if self._active_request_id == request_id:
            self._cancel_search = cancel
            self._dialog.show_related_searching(len(note_ids))

    def _on_related_success(
        self,
        request_id: int,
        response: RelatedCardsResponse,
    ) -> None:
        if self._disposed or not self._active:
            return
        if (
            request_id != self._request_counter
            or request_id != self._active_request_id
        ):
            return
        self._active_request_id = None
        self._cancel_search = None
        if not response.source_tags:
            self._restore_related_origin(
                "No specific UWorld or AMBOSS source tags were found on the "
                "selected notes."
            )
            return
        self._dialog.show_related_response(response)
        self.initialPreviewRequested.emit(
            response.results[0] if response.results else None
        )

    def _on_related_error(self, request_id: int, message: str) -> None:
        if self._disposed or not self._active:
            return
        if (
            request_id != self._request_counter
            or request_id != self._active_request_id
        ):
            return
        self._active_request_id = None
        self._cancel_search = None
        self._restore_related_origin(
            f"Could not find related cards: {message}"
        )

    def restore_original_search(self) -> None:
        if self._disposed or not self._active:
            return
        if self._dialog.batch_action_busy():
            return
        self._invalidate_pending_search()
        self._restore_related_origin()

    def _restore_related_origin(self, notice: str = "") -> None:
        origin = self._related_origin
        self._related_origin = None
        if origin is None:
            self._dialog.leave_related_view()
            if notice:
                self._dialog.set_summary(notice)
            return
        response, corrections, state = origin
        self._dialog.restore_search_view(response, corrections, state)
        self._refresh_restored_card_states(response)
        if notice:
            self._dialog.set_summary(notice)

    def _refresh_restored_card_states(self, response: SearchResponse) -> None:
        """Refresh mutable indicators after Back without rerunning search."""

        refresher = getattr(self._backend, "refresh_card_states", None)
        if not callable(refresher) or not response.results:
            return
        generation = self._request_counter
        try:
            refresher(
                response.results,
                on_success=lambda results: self._sig_restored_states.emit(
                    generation,
                    tuple(results),
                ),
                on_error=lambda _message: None,
            )
        except Exception:
            # Search restoration itself is already complete; live indicator
            # decoration is best-effort during profile teardown.
            return

    def _on_restored_states(self, generation: int, results) -> None:
        if (
            self._disposed
            or not self._active
            or int(generation) != self._request_counter
            or self._dialog.related_active()
            or self._dialog.batch_action_busy()
        ):
            return
        self._dialog.refresh_card_states(tuple(results))

    def _abandon_related_view(self) -> None:
        if self._related_origin is None and not self._dialog.related_active():
            return
        self._related_origin = None
        self._dialog.leave_related_view()

    # ------------------------------------------------------------- opening

    def open_result(self, result: SearchResult) -> None:
        self.resultOpenRequested.emit(result)
        if self._browser_opener is not None:
            self._browser_opener(result)

    def open_all_results(self, results: tuple[SearchResult, ...]) -> None:
        self.openAllRequested.emit(results)
        if self._multi_browser_opener is not None:
            self._multi_browser_opener(results)

    # ---------------------------------------------------- filters/chips

    def _on_filter_removed(self, chip) -> None:
        new_query = remove_filter_token(self._dialog.query(), chip)
        self._dialog.set_query(new_query)

    def _on_correction_dismissed(self, correction: Correction) -> None:
        self._dismissed.add(correction)
        response = self._dialog.last_response()
        if response is not None:
            visible = [c for c in response.corrections if c not in self._dismissed]
            self._dialog.show_response(response, visible)

    def _on_correction_literal(self, correction: Correction) -> None:
        self._force_literal = True
        self._last_query = correction.literal_query or self._dialog.query()
        self._dialog.set_query(self._last_query)

    # -------------------------------------------------------------- status

    def refresh_status(self) -> None:
        if self._disposed or not self._active:
            return
        try:
            self._dialog.show_status(self._backend.get_status())
        except RuntimeError:
            # A profile close can delete the native widgets before a queued
            # timer tick arrives. Make that controller permanently inert.
            self.dispose()

    # -------------------------------------------------------------- rebuild

    def start_rebuild(self) -> None:
        if self._disposed or not self._active:
            return
        if self._cancel_rebuild is not None:
            return  # already building
        self._dialog.show_rebuild_progress(0.0, "starting")
        self._cancel_rebuild = self._backend.rebuild_index(
            on_progress=lambda f, d: self._sig_rebuild_progress.emit(f, d),
            on_success=lambda status: self._sig_rebuild_success.emit(status),
            on_error=lambda message: self._sig_rebuild_error.emit(message),
        )

    def _on_rebuild_progress(self, fraction: float, detail: str) -> None:
        if self._disposed or not self._active:
            return
        self._dialog.show_rebuild_progress(fraction, detail)

    def _on_rebuild_success(self, status) -> None:
        if self._disposed or not self._active:
            return
        self._cancel_rebuild = None
        self._dialog.show_status(status)
        self._dialog.set_summary("Index rebuilt")
        if status.state is IndexState.READY:
            self.textIndexRebuilt.emit()
        if self._dialog.query().strip():
            self.submit_search(self._dialog.query())

    def _on_rebuild_error(self, message: str) -> None:
        if self._disposed or not self._active:
            return
        self._cancel_rebuild = None
        self.refresh_status()
        self._dialog.show_error(f"Index rebuild failed:\n{message}")

    # ------------------------------------------------------------- settings

    def _on_mode_selected(self, mode) -> None:
        self._settings.mode = mode
        self._abandon_related_view()
        if self._dialog.query().strip():
            self.submit_search(self._dialog.query())

    def _on_settings_changed(
        self,
        mode,
        limit: int,
        preview_enabled: bool,
        preview_default,
    ) -> None:
        self._settings.mode = mode
        self._settings.result_limit = limit
        previous_preview = self._settings.preview_enabled
        self._settings.preview_enabled = bool(preview_enabled)
        previous_default = self._settings.preview_default
        try:
            self._settings.preview_default = PreviewDefault(preview_default)
        except (TypeError, ValueError):
            self._settings.preview_default = PreviewDefault.QUESTION
        self._dialog.set_result_limit(limit)
        self._dialog.set_preview_enabled(self._settings.preview_enabled)
        self._dialog.set_preview_default(self._settings.preview_default)
        self._backend.save_settings(self._settings)
        if previous_preview != self._settings.preview_enabled:
            self.previewPreferenceChanged.emit(self._settings.preview_enabled)
        if previous_default != self._settings.preview_default:
            self.previewDefaultChanged.emit(self._settings.preview_default)
        if mode != self._dialog.mode():
            self._dialog.set_mode(mode)  # emits modeSelected → re-search

    def _on_dialog_closed(self) -> None:
        if self._disposed:
            return
        size = self._dialog.size()
        self._settings.width = size.width()
        self._settings.height = size.height()
        self._settings.mode = self._dialog.mode()
        try:
            self._backend.save_settings(self._settings)
        except Exception:  # noqa: BLE001 - never block closing on persistence
            pass
        self.pause()
