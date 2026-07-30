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
    textIndexRebuilt = pyqtSignal()
    flagRequested = pyqtSignal(object, int)
    suspensionRequested = pyqtSignal(object, bool)
    tagActionRequested = pyqtSignal(object, bool)

    # Internal marshalling signals (emitted from arbitrary threads).
    _sig_search_success = pyqtSignal(int, object)
    _sig_search_error = pyqtSignal(int, str)
    _sig_rebuild_progress = pyqtSignal(float, str)
    _sig_rebuild_success = pyqtSignal(object)
    _sig_rebuild_error = pyqtSignal(str)

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
        self._dismissed: set[Correction] = set()
        self._force_literal = False
        self._last_query = ""

        self._settings: UISettings = backend.load_settings()

        # Marshalled backend callbacks.
        self._sig_search_success.connect(self._on_search_success)
        self._sig_search_error.connect(self._on_search_error)
        self._sig_rebuild_progress.connect(self._on_rebuild_progress)
        self._sig_rebuild_success.connect(self._on_rebuild_success)
        self._sig_rebuild_error.connect(self._on_rebuild_error)

        # Dialog intents.
        dialog.queryEdited.connect(self._on_query_edited)
        dialog.searchRequested.connect(self.submit_search)
        dialog.modeSelected.connect(self._on_mode_selected)
        dialog.openRequested.connect(self.open_result)
        dialog.openAllRequested.connect(self.open_all_results)
        dialog.filterRemoveRequested.connect(self._on_filter_removed)
        dialog.correctionDismissRequested.connect(self._on_correction_dismissed)
        dialog.correctionLiteralRequested.connect(self._on_correction_literal)
        dialog.rebuildRequested.connect(self.start_rebuild)
        dialog.semanticInstallRequested.connect(self.semanticInstallRequested)
        dialog.semanticIndexRequested.connect(self.semanticIndexRequested)
        dialog.flagRequested.connect(self.flagRequested)
        dialog.suspensionRequested.connect(self.suspensionRequested)
        dialog.tagActionRequested.connect(self.tagActionRequested)
        dialog.settingsChanged.connect(self._on_settings_changed)
        dialog.dialogClosed.connect(self._on_dialog_closed)

        # Apply persisted settings.
        dialog.set_mode(self._settings.mode)
        dialog.set_result_limit(self._settings.result_limit)
        dialog.resize(self._settings.width, self._settings.height)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self.refresh_status)
        self._status_timer.start()
        self.refresh_status()

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

        self._invalidate_pending_search()

    def _invalidate_pending_search(self) -> None:
        self._request_counter += 1
        self._active_request_id = None
        self._cancel_pending_search()

    def _cancel_pending_search(self) -> None:
        if self._cancel_search is not None:
            try:
                self._cancel_search()
            except Exception:  # noqa: BLE001 - cancellation is best effort
                pass
            self._cancel_search = None

    def _on_search_success(self, request_id: int, response: SearchResponse) -> None:
        if (
            request_id != self._request_counter
            or request_id != self._active_request_id
        ):
            return  # stale response from a superseded request
        self._active_request_id = None
        self._cancel_search = None
        visible = [c for c in response.corrections if c not in self._dismissed]
        self._dialog.show_response(response, visible)

    def _on_search_error(self, request_id: int, message: str) -> None:
        if (
            request_id != self._request_counter
            or request_id != self._active_request_id
        ):
            return
        self._active_request_id = None
        self._cancel_search = None
        self._dialog.show_error(message)

    # ------------------------------------------------------------- opening

    def open_result(self, result: SearchResult) -> None:
        self.resultOpenRequested.emit(result)
        if self._browser_opener is not None:
            self._browser_opener(result)
        self._dialog.close()

    def open_all_results(self, results: tuple[SearchResult, ...]) -> None:
        self.openAllRequested.emit(results)
        if self._multi_browser_opener is not None:
            self._multi_browser_opener(results)
        self._dialog.close()

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
        self._dialog.show_status(self._backend.get_status())

    # -------------------------------------------------------------- rebuild

    def start_rebuild(self) -> None:
        if self._cancel_rebuild is not None:
            return  # already building
        self._dialog.show_rebuild_progress(0.0, "starting")
        self._cancel_rebuild = self._backend.rebuild_index(
            on_progress=lambda f, d: self._sig_rebuild_progress.emit(f, d),
            on_success=lambda status: self._sig_rebuild_success.emit(status),
            on_error=lambda message: self._sig_rebuild_error.emit(message),
        )

    def _on_rebuild_progress(self, fraction: float, detail: str) -> None:
        self._dialog.show_rebuild_progress(fraction, detail)

    def _on_rebuild_success(self, status) -> None:
        self._cancel_rebuild = None
        self._dialog.show_status(status)
        self._dialog.set_summary("Index rebuilt")
        if status.state is IndexState.READY:
            self.textIndexRebuilt.emit()
        if self._dialog.query().strip():
            self.submit_search(self._dialog.query())

    def _on_rebuild_error(self, message: str) -> None:
        self._cancel_rebuild = None
        self.refresh_status()
        self._dialog.show_error(f"Index rebuild failed:\n{message}")

    # ------------------------------------------------------------- settings

    def _on_mode_selected(self, mode) -> None:
        self._settings.mode = mode
        if self._dialog.query().strip():
            self.submit_search(self._dialog.query())

    def _on_settings_changed(self, mode, limit: int) -> None:
        self._settings.mode = mode
        self._settings.result_limit = limit
        self._dialog.set_result_limit(limit)
        self._backend.save_settings(self._settings)
        if mode != self._dialog.mode():
            self._dialog.set_mode(mode)  # emits modeSelected → re-search

    def _on_dialog_closed(self) -> None:
        self._invalidate_pending_search()
        if self._cancel_rebuild is not None:
            try:
                self._cancel_rebuild()
            except Exception:  # noqa: BLE001
                pass
            self._cancel_rebuild = None
        size = self._dialog.size()
        self._settings.width = size.width()
        self._settings.height = size.height()
        self._settings.mode = self._dialog.mode()
        try:
            self._backend.save_settings(self._settings)
        except Exception:  # noqa: BLE001 - never block closing on persistence
            pass
