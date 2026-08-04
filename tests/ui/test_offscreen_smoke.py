from __future__ import annotations

import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BUNDLE_ROOT = Path(__file__).resolve().parents[2]
LOGO_PATH = BUNDLE_ROOT / "resources" / "medbrevia-logo.png"

try:
    from ui.contracts import (
        AboutInfo,
        CardState,
        Correction,
        DeckCatalog,
        DeckEntry,
        HighlightSpan,
        IndexState,
        IndexStatus,
        MatchKind,
        SearchMode,
        SearchResponse,
        SearchResult,
        SemanticRecovery,
        SemanticState,
        SemanticStatus,
        UISettings,
    )
    from ui.controller import SearchController
    from ui.deck_query import DeckScopeSelection
    from ui.dialog import SearchDialog, _SettingsDialog
    from ui.results import ResultsView, card_state_summary, snippet_html
    from ui.widgets import (
        AboutPanel,
        QApplication,
        QColor,
        QDialog,
        QEvent,
        QLabel,
        QPalette,
        QRect,
        Qt,
    )
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtGui import QKeyEvent, QWheelEvent
    from PyQt6.QtTest import QTest
except ImportError as error:  # PyQt6 is intentionally not a package dependency.
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


class _HeldSearchBackend:
    def __init__(self, *, submit_error: Exception | None = None) -> None:
        self.status = IndexStatus(
            IndexState.READY,
            semantic=SemanticStatus(SemanticState.READY),
        )
        self.submit_error = submit_error
        self.requests = []
        self.callbacks = []
        self.deck_callbacks = []
        self.cancel_count = 0
        self.deck_cancel_count = 0
        self.status_reads = 0
        self.saved_settings = []

    def load_settings(self):
        return UISettings()

    def save_settings(self, settings) -> None:
        self.saved_settings.append(settings)

    def get_status(self):
        self.status_reads += 1
        return self.status

    def submit_search(self, request, on_success, on_error):
        if self.submit_error is not None:
            raise self.submit_error
        self.requests.append(request)
        self.callbacks.append((on_success, on_error))

        def cancel() -> None:
            self.cancel_count += 1

        return cancel

    def rebuild_index(self, on_progress, on_success, on_error):
        del on_progress, on_success, on_error
        return None

    def load_decks(self, on_success, on_error):
        self.deck_callbacks.append((on_success, on_error))

        def cancel() -> None:
            self.deck_cancel_count += 1

        return cancel


@unittest.skipIf(IMPORT_ERROR is not None, f"Qt runtime unavailable: {IMPORT_ERROR}")
class OffscreenSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_renders_results_and_semantic_setup_state(self) -> None:
        dialog = SearchDialog()
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                semantic=SemanticStatus(SemanticState.NOT_INSTALLED),
            )
        )
        dialog.show_response(
            SearchResponse(
                request_id=1,
                query="buproprion",
                results=(
                    SearchResult(
                        note_id=42,
                        title="Bupropion",
                        snippet="Bupropion is an NDRI.",
                        spans=(HighlightSpan(0, 9, MatchKind.CORRECTION),),
                        deck="AnKing",
                        note_type="Cloze",
                        match_reasons=("Corrected typo",),
                        sibling_count=2,
                    ),
                ),
                corrections=(Correction("buproprion", "bupropion"),),
                total_results=1,
                elapsed_ms=12,
            ),
            (Correction("buproprion", "bupropion"),),
        )

        self.assertEqual(dialog.results.results_model().count(), 1)
        self.assertTrue(dialog.semantic_action.isVisibleTo(dialog))
        opened: list[tuple[SearchResult, ...]] = []
        dialog.openAllRequested.connect(opened.append)
        dialog._open_all_results()
        self.assertEqual(opened[0][0].note_id, 42)
        dialog.deleteLater()

    def test_deck_picker_applies_one_visible_query_and_search(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        dialog.search.setText("bupropion")
        dialog.deck_scope.set_scope(dialog.query())

        dialog.deck_scope.click()
        self.assertEqual(len(backend.deck_callbacks), 1)
        backend.deck_callbacks[-1][0](
            DeckCatalog(
                decks=(
                    DeckEntry(1, "AnKing"),
                    DeckEntry(2, "AnKing::Step 2"),
                    DeckEntry(3, "Personal"),
                ),
                current_deck_id=2,
            )
        )
        self.app.processEvents()

        dialog.deck_picker._selected = {"AnKing::Step 2", "Personal"}
        dialog.deck_picker._refresh_checks()
        dialog.deck_picker._apply()
        self.app.processEvents()

        self.assertEqual(
            dialog.query(),
            'bupropion (deck:"AnKing::Step 2" OR deck:"Personal")',
        )
        self.assertIn("2 decks", dialog.deck_scope.text())
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(backend.requests[0].query, dialog.query())
        controller.deleteLater()
        dialog.deleteLater()

    def test_controller_forwards_bury_and_change_deck_intents(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        result = SearchResult(note_id=7, card_ids=(71,))
        burials = []
        copies = []
        moves = []
        controller.burialRequested.connect(
            lambda target, bury: burials.append((target, bury))
        )
        controller.deckChangeRequested.connect(
            lambda target, deck_id, name: moves.append(
                (target, deck_id, name)
            )
        )
        controller.createCopyRequested.connect(copies.append)

        # Real Anki deck IDs are timestamp-sized and exceed Qt's signed
        # 32-bit ``int`` range.  Both UI signal hops must preserve every bit.
        deck_id = 1_785_791_800_898
        dialog.burialRequested.emit((result,), True)
        dialog.createCopyRequested.emit((result,))
        dialog.deckChangeRequested.emit((result,), deck_id, "Cardiology")

        self.assertEqual(burials, [((result,), True)])
        self.assertEqual(copies, [(result,)])
        self.assertEqual(moves, [((result,), deck_id, "Cardiology")])
        controller.deleteLater()
        dialog.deleteLater()

    def test_deck_picker_applies_exclusion_scope_to_visible_query(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        dialog.search.setText("bupropion")
        dialog.deck_scope.set_scope(dialog.query())

        dialog.deck_scope.click()
        backend.deck_callbacks[-1][0](
            DeckCatalog(
                decks=(
                    DeckEntry(1, "AnKing"),
                    DeckEntry(2, "AnKing::Step 1"),
                    DeckEntry(3, "AnKing::Step 2"),
                ),
                current_deck_id=1,
            )
        )
        self.app.processEvents()

        dialog.deck_picker._selected = {"AnKing"}
        dialog.deck_picker._excluded = {"AnKing::Step 1"}
        dialog.deck_picker._refresh_checks()
        dialog.deck_picker._apply()
        self.app.processEvents()

        self.assertEqual(
            dialog.query(),
            'bupropion (deck:"AnKing" -deck:"AnKing::Step 1")',
        )
        self.assertIn("−1", dialog.deck_scope.text())
        self.assertIn(
            "1 subdeck excluded",
            dialog.deck_scope.accessibleName(),
        )

        # Reopening the picker stages the exclusions from the visible query.
        dialog._open_deck_picker()
        self.app.processEvents()
        self.assertEqual(
            dialog.deck_picker.selection,
            DeckScopeSelection(("AnKing",), ("AnKing::Step 1",)),
        )
        self.assertEqual(
            dialog.deck_picker._items["AnKing"].checkState(0),
            Qt.CheckState.PartiallyChecked,
        )
        controller.deleteLater()
        dialog.deleteLater()

    def test_deck_picker_ignores_stale_catalog_completion(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)

        dialog.deck_scope.click()
        first_success = backend.deck_callbacks[-1][0]
        dialog._reload_deck_picker()
        second_success = backend.deck_callbacks[-1][0]
        self.assertEqual(backend.deck_cancel_count, 1)

        first_success(DeckCatalog((DeckEntry(1, "Stale"),), 1))
        second_success(DeckCatalog((DeckEntry(2, "Current"),), 2))
        self.app.processEvents()

        self.assertEqual(
            tuple(entry.name for entry in dialog._deck_catalog.decks),
            ("Current",),
        )
        controller.deleteLater()
        dialog.deleteLater()

    def test_unsafe_custom_deck_expression_is_never_rewritten(self) -> None:
        dialog = SearchDialog()
        original = 'deck:"A" OR bupropion'
        dialog.search.setText(original)
        dialog.deck_scope.set_scope(original)
        dialog.deck_picker.set_query(original)
        dialog.deck_picker.set_catalog(
            DeckCatalog((DeckEntry(1, "A"), DeckEntry(2, "New")), 1)
        )
        dialog.deck_picker.show()
        dialog._apply_deck_selection(("New",))
        self.app.processEvents()

        self.assertEqual(dialog.query(), original)
        self.assertTrue(dialog.deck_picker.isVisible())
        self.assertIn("must be edited", dialog.deck_picker.message_label.text())
        dialog.deleteLater()

    def test_closing_search_dialog_also_closes_deck_picker(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.deck_picker.show()
        self.app.processEvents()
        self.assertTrue(dialog.deck_picker.isVisible())

        dialog.close()
        self.app.processEvents()

        self.assertFalse(dialog.isVisible())
        self.assertFalse(dialog.deck_picker.isVisible())
        dialog.deleteLater()

    def test_snippet_builder_escapes_card_text(self) -> None:
        rendered = snippet_html(
            "<script>alert(1)</script>",
            (HighlightSpan(0, 8),),
            "#000000",
            "#ffff00",
        )
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_card_state_indicators_have_precise_nonvisual_descriptions(self) -> None:
        result = SearchResult(
            note_id=42,
            card_ids=(101, 102, 103),
            card_states=(
                CardState(101, flag=1, suspended=True),
                CardState(102, flag=4, suspended=True),
                CardState(103, buried=True),
            ),
            sibling_count=3,
            title="Mixed card state",
        )
        self.assertEqual(
            card_state_summary(result),
            "2 of 3 cards suspended. 1 of 3 cards buried. "
            "Flags: red 1, blue 1, unflagged 1.",
        )

        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(IndexStatus(IndexState.READY))
        dialog.show_response(
            SearchResponse(
                request_id=2,
                query="mixed",
                results=(result,),
                total_results=1,
            ),
            (),
        )
        self.app.processEvents()
        index = dialog.results.results_model().index(0, 0)
        self.assertEqual(
            index.data(Qt.ItemDataRole.ToolTipRole),
            card_state_summary(result),
        )
        self.assertIn(
            "Flags: red 1, blue 1, unflagged 1",
            index.data(Qt.ItemDataRole.AccessibleTextRole),
        )
        # Exercise custom painting with state icons in the actual Qt runtime.
        self.assertFalse(dialog.grab().isNull())
        dialog.deleteLater()

    def test_live_state_refresh_rejects_stale_sibling_scope(self) -> None:
        dialog = SearchDialog()
        model = dialog.results.results_model()
        current = SearchResult(
            note_id=42,
            card_ids=(101,),
            card_states=(CardState(101),),
            title="New filtered result",
            snippet="Current search content",
            score=0.98,
        )
        model.set_results((current,))

        accepted = model.refresh_results(
            (
                SearchResult(
                    note_id=42,
                    card_ids=(101, 102),
                    card_states=(
                        CardState(101, suspended=True),
                        CardState(102, flag=1),
                    ),
                    title="Stale result",
                    snippet="Old search content",
                    score=0.12,
                ),
            )
        )

        self.assertFalse(accepted)
        self.assertEqual(model.result_at(0), current)
        dialog.deleteLater()

    def test_live_state_refresh_merges_only_card_status_metadata(self) -> None:
        dialog = SearchDialog()
        model = dialog.results.results_model()
        current = SearchResult(
            note_id=42,
            card_ids=(101, 102),
            card_states=(CardState(101), CardState(102)),
            title="New result",
            snippet="Current snippet",
            spans=(HighlightSpan(0, 7),),
            deck="Current deck",
            note_type="Current type",
            tags=("current",),
            match_reasons=("Current reason",),
            sibling_count=2,
            browser_query="cid:101 OR cid:102",
            score=0.98,
        )
        model.set_results((current,))
        fresh_states = (
            CardState(101, flag=4, suspended=True),
            CardState(102, flag=1),
        )

        accepted = model.refresh_results(
            (
                SearchResult(
                    note_id=42,
                    card_ids=(101, 102),
                    card_states=fresh_states,
                    title="Stale title",
                    snippet="Stale snippet",
                    spans=(),
                    deck="Stale deck",
                    note_type="Stale type",
                    tags=("stale",),
                    match_reasons=("Stale reason",),
                    sibling_count=3,
                    browser_query="stale query",
                    score=0.01,
                ),
            )
        )

        self.assertTrue(accepted)
        merged = model.result_at(0)
        self.assertIsNotNone(merged)
        self.assertEqual(merged.card_states, fresh_states)
        self.assertEqual(merged.sibling_count, 3)
        self.assertEqual(merged.title, current.title)
        self.assertEqual(merged.snippet, current.snippet)
        self.assertEqual(merged.spans, current.spans)
        self.assertEqual(merged.deck, current.deck)
        self.assertEqual(merged.note_type, current.note_type)
        self.assertEqual(merged.tags, current.tags)
        self.assertEqual(merged.match_reasons, current.match_reasons)
        self.assertEqual(merged.browser_query, current.browser_query)
        self.assertEqual(merged.score, current.score)
        dialog.deleteLater()

    def test_initial_text_index_blocks_search_with_clear_progress(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(
            IndexStatus(
                IndexState.BUILDING,
                progress=0.42,
                detail="Indexing text 17,000/40,000 notes.",
            )
        )
        self.app.processEvents()

        self.assertFalse(dialog.search.isEnabled())
        self.assertFalse(dialog.segmented.isEnabled())
        self.assertIn("Initial setup", dialog.message_label.text())
        self.assertIn("Smart &amp; Exact", dialog.message_label.text())
        self.assertTrue(dialog.progress.isVisibleTo(dialog))
        self.assertEqual(dialog.progress.value(), 42)
        dialog.deleteLater()

    def test_semantic_indexing_keeps_smart_and_exact_available(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                detail="40,657 notes indexed",
                semantic=SemanticStatus(
                    SemanticState.INDEXING,
                    detail="Embedding notes 10,000/40,657.",
                    progress=0.25,
                ),
            )
        )
        self.app.processEvents()

        self.assertTrue(dialog.search.isEnabled())
        self.assertTrue(dialog.segmented.isEnabled())
        self.assertTrue(dialog.index_notice.isVisibleTo(dialog))
        self.assertIn("Smart and Exact search remain available", dialog.index_notice.text())
        self.assertTrue(dialog.semantic_progress.isVisibleTo(dialog))
        self.assertEqual(dialog.semantic_progress.value(), 25)
        self.assertFalse(dialog.semantic_action.isEnabled())

        requested: list[str] = []
        dialog.searchRequested.connect(requested.append)
        dialog.set_mode(SearchMode.SMART)
        dialog.set_query("bupropion")
        dialog.set_mode(SearchMode.EXACT)
        dialog._emit_search()
        self.assertEqual(requested, ["bupropion", "bupropion"])
        dialog.deleteLater()

    def test_leaving_unprepared_semantic_hides_stale_setup_notice(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                semantic=SemanticStatus(SemanticState.NOT_INSTALLED),
            )
        )
        dialog.set_query("heart failure")
        dialog.set_mode(SearchMode.SEMANTIC)
        self.app.processEvents()
        self.assertTrue(dialog.index_notice.isVisibleTo(dialog))

        dialog.set_mode(SearchMode.SMART)
        dialog.show_searching()
        self.app.processEvents()
        self.assertFalse(dialog.index_notice.isVisibleTo(dialog))

        dialog.show_response(
            SearchResponse(
                request_id=3,
                query="heart failure",
                results=(SearchResult(note_id=3, title="Heart failure"),),
                total_results=1,
                elapsed_ms=18,
            ),
            (),
        )
        self.assertFalse(dialog.index_notice.isVisibleTo(dialog))
        self.assertEqual(dialog.summary.text(), "1 result · 18 ms")
        dialog.deleteLater()

    def test_edit_during_debounce_does_not_claim_search_is_running(self) -> None:
        dialog = SearchDialog()
        dialog.show_status(IndexStatus(IndexState.READY))
        dialog.show_response(
            SearchResponse(
                request_id=1,
                query="old",
                results=(SearchResult(note_id=1, title="Old result"),),
                total_results=1,
            ),
            (),
        )
        self.assertEqual(dialog.results.results_model().count(), 1)
        dialog.set_summary("12 results · 18 ms")
        dialog.search.setText("bup")

        dialog._on_text_edited("bup")

        self.assertTrue(dialog._debounce.isActive())
        self.assertEqual(dialog.results.results_model().count(), 1)
        self.assertFalse(dialog.results.isEnabled())
        self.assertEqual(dialog.summary.text(), "")
        self.assertNotEqual(dialog.summary.text(), "Searching…")
        dialog.deleteLater()

    def test_semantic_typing_uses_a_cold_start_safe_debounce(self) -> None:
        dialog = SearchDialog()
        dialog.set_debounce_interval(160)

        dialog.set_mode(SearchMode.SMART)
        dialog._on_text_edited("heart")
        self.assertTrue(dialog._debounce.isActive())
        self.assertEqual(dialog._debounce.interval(), 160)

        dialog.set_mode(SearchMode.SEMANTIC)
        dialog._on_text_edited("heart failure")
        self.assertTrue(dialog._debounce.isActive())
        self.assertEqual(dialog._debounce.interval(), 450)

        dialog.deleteLater()

    def test_searching_keeps_prior_results_until_worker_replaces_them(self) -> None:
        dialog = SearchDialog()
        dialog.show_status(IndexStatus(IndexState.READY))
        dialog.show_response(
            SearchResponse(
                request_id=1,
                query="old",
                results=(SearchResult(note_id=1, title="Old result"),),
                total_results=1,
            ),
            (),
        )

        dialog.show_searching()

        self.assertEqual(dialog.results.results_model().count(), 1)
        self.assertFalse(dialog.results.isEnabled())
        self.assertEqual(dialog.summary.text(), "Searching…")

        dialog.show_response(
            SearchResponse(
                request_id=2,
                query="new",
                results=(SearchResult(note_id=2, title="New result"),),
                total_results=1,
            ),
            (),
        )
        self.assertTrue(dialog.results.isEnabled())
        self.assertEqual(dialog.results.results_model().result_at(0).note_id, 2)
        dialog.deleteLater()

    def test_clear_cancels_active_search_and_rejects_late_response(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        dialog.search.setText("first")
        controller.submit_search("first")
        self.assertEqual(dialog.summary.text(), "Searching…")

        dialog.search.setText("")
        dialog._on_text_edited("")
        self.assertEqual(backend.cancel_count, 1)
        self.assertEqual(dialog.summary.text(), "")

        backend.callbacks[0][0](
            SearchResponse(
                request_id=backend.requests[0].request_id,
                query="first",
                results=(SearchResult(note_id=1, title="Stale"),),
                total_results=1,
            )
        )
        self.app.processEvents()

        self.assertEqual(dialog.results.results_model().count(), 0)
        self.assertEqual(dialog.summary.text(), "")
        controller.deleteLater()
        dialog.deleteLater()

    def test_closed_dialog_pauses_status_timer_and_dispose_is_terminal(
        self,
    ) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        closed: list[bool] = []
        dialog.dialogClosed.connect(lambda: closed.append(True))
        controller = SearchController(backend, dialog)
        self.assertTrue(controller._status_timer.isActive())
        initial_reads = backend.status_reads

        dialog.show()
        dialog.close()
        self.app.processEvents()

        self.assertEqual(closed, [True])
        self.assertFalse(controller._active)
        self.assertFalse(controller._status_timer.isActive())
        controller.refresh_status()
        self.assertEqual(backend.status_reads, initial_reads)

        self.assertTrue(controller.resume())
        self.assertTrue(controller._status_timer.isActive())
        self.assertGreater(backend.status_reads, initial_reads)
        controller.dispose()
        self.assertFalse(controller.resume())
        self.assertFalse(controller._status_timer.isActive())
        controller.deleteLater()
        dialog.deleteLater()

    def test_managed_close_delegates_before_dialog_manager_continues(
        self,
    ) -> None:
        dialog = SearchDialog()
        pending = []
        completed: list[bool] = []
        dialog.set_managed_close_handler(pending.append)

        dialog.closeWithCallback(lambda: completed.append(True))

        self.assertEqual(len(pending), 1)
        self.assertEqual(completed, [])
        pending.pop()()
        self.assertEqual(completed, [True])
        dialog.deleteLater()

    def test_normal_window_close_waits_for_managed_cleanup(self) -> None:
        dialog = SearchDialog()
        pending = []
        closed: list[bool] = []
        dialog.dialogClosed.connect(lambda: closed.append(True))
        dialog.set_managed_close_handler(pending.append)
        dialog.show()
        self.app.processEvents()

        dialog.close()
        self.app.processEvents()

        self.assertEqual(len(pending), 1)
        self.assertEqual(closed, [])
        self.assertTrue(dialog.isVisible())

        dialog.set_managed_close_handler(None)
        dialog.close()
        self.app.processEvents()

        self.assertEqual(closed, [True])
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater()

    def test_disposed_controller_ignores_queued_search_success(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        controller.submit_search("queued")
        success, _failure = backend.callbacks[-1]
        controller.dispose()

        success(
            SearchResponse(
                request_id=backend.requests[-1].request_id,
                query="queued",
                results=(
                    SearchResult(
                        note_id=1,
                        card_ids=(11,),
                        title="Should stay hidden",
                    ),
                ),
                total_results=1,
            )
        )
        self.app.processEvents()

        self.assertEqual(dialog.results.results_model().count(), 0)
        controller.deleteLater()
        dialog.deleteLater()

    def test_mode_change_stops_debounce_and_dispatches_once(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        dialog.search.setText("heart failure")
        dialog._on_text_edited("heart failure")
        self.assertTrue(dialog._debounce.isActive())

        dialog.set_mode(SearchMode.EXACT)

        self.assertFalse(dialog._debounce.isActive())
        self.assertEqual(len(backend.requests), 1)
        self.assertIs(backend.requests[0].mode, SearchMode.EXACT)
        self.assertEqual(dialog.summary.text(), "Searching…")
        controller.deleteLater()
        dialog.deleteLater()

    def test_return_in_search_field_runs_search_without_opening_result(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        dialog.show_response(
            SearchResponse(
                request_id=1,
                query="old",
                results=(SearchResult(note_id=1, title="Old result"),),
                total_results=1,
            ),
            (),
        )
        opened: list[SearchResult] = []
        dialog.openRequested.connect(opened.append)
        dialog.search.setText("bupropion is:suspended")
        dialog._on_text_edited("bupropion is:suspended")
        self.assertTrue(dialog._debounce.isActive())

        dialog.search.returnPressed.emit()

        self.assertFalse(dialog._debounce.isActive())
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(
            backend.requests[0].query,
            "bupropion is:suspended",
        )
        self.assertEqual(opened, [])
        controller.deleteLater()
        dialog.deleteLater()

    def test_return_in_results_opens_highlighted_result(self) -> None:
        dialog = SearchDialog()
        result = SearchResult(note_id=1, title="Selected result")
        dialog.show_response(
            SearchResponse(
                request_id=1,
                query="selected",
                results=(result,),
                total_results=1,
            ),
            (),
        )
        opened: list[SearchResult] = []
        dialog.openRequested.connect(opened.append)
        dialog.show()
        dialog.results.select_row(0)
        dialog.results.setFocus()
        self.app.processEvents()

        QTest.keyClick(dialog.results, Qt.Key.Key_Return)
        QTest.keyClick(dialog.results, Qt.Key.Key_Enter)

        self.assertEqual(opened, [result, result])
        dialog.deleteLater()

    def test_double_click_browser_open_preserves_search_results_and_checks(
        self,
    ) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        result = SearchResult(
            note_id=42,
            card_ids=(4201,),
            title="Hypertension",
        )
        dialog.search.setText("hypertension is:suspended")
        dialog.show_response(
            SearchResponse(
                request_id=1,
                query=dialog.query(),
                results=(result,),
                total_results=1,
            ),
            (),
        )
        dialog.results.results_model().set_checked(0, True)
        opened: list[SearchResult] = []
        controller.set_browser_opener(opened.append)
        dialog.show()
        self.app.processEvents()

        index = dialog.results.results_model().index(0, 0)
        point = dialog.results.visualRect(index).center()
        QTest.mouseClick(
            dialog.results.viewport(),
            Qt.MouseButton.LeftButton,
            pos=point,
        )
        QTest.mouseDClick(
            dialog.results.viewport(),
            Qt.MouseButton.LeftButton,
            pos=point,
        )
        self.app.processEvents()

        self.assertEqual(opened, [result])
        self.assertEqual(dialog.query(), "hypertension is:suspended")
        self.assertEqual(dialog.results.results_model().count(), 1)
        self.assertEqual(
            dialog.results.results_model().checked_results(),
            (result,),
        )
        self.assertTrue(dialog.isVisible())
        self.assertTrue(controller._active)
        controller.deleteLater()
        dialog.deleteLater()

    def test_search_error_keeps_query_field_editable_and_focused(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.search.setText("bupropion is:")
        dialog.search.setFocus()
        self.app.processEvents()

        dialog.show_error("This search could not be completed.")
        self.app.processEvents()

        self.assertTrue(dialog.search.isEnabled())
        self.assertIs(dialog.focusWidget(), dialog.search)
        self.assertFalse(dialog.retry_button.hasFocus())
        dialog.deleteLater()

    def test_conditional_rerun_never_supersedes_newer_search_intent(self) -> None:
        backend = _HeldSearchBackend()
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)
        dialog.search.setText("is:suspended")
        controller.submit_search("is:suspended")
        backend.callbacks[-1][0](
            SearchResponse(
                request_id=backend.requests[-1].request_id,
                query="is:suspended",
                results=(SearchResult(note_id=1, card_ids=(11,), title="One"),),
                total_results=1,
            )
        )
        self.app.processEvents()

        current = controller.capture_search_generation()
        self.assertTrue(controller.rerun_search_if_current(current))
        self.assertEqual(len(backend.requests), 2)

        superseded = controller.capture_search_generation()
        dialog.search.setText("flag:1")
        dialog._on_text_edited("flag:1")
        self.assertFalse(controller.rerun_search_if_current(superseded))
        self.assertEqual(len(backend.requests), 2)

        before_mode_change = controller.capture_search_generation()
        dialog.set_mode(SearchMode.EXACT)
        self.assertFalse(controller.rerun_search_if_current(before_mode_change))
        self.assertEqual(len(backend.requests), 3)
        self.assertIs(backend.requests[-1].mode, SearchMode.EXACT)
        controller.deleteLater()
        dialog.deleteLater()

    def test_synchronous_submit_error_cannot_leave_searching_state(self) -> None:
        backend = _HeldSearchBackend(submit_error=RuntimeError("dispatch failed"))
        dialog = SearchDialog()
        controller = SearchController(backend, dialog)

        controller.submit_search("bupropion")

        self.assertEqual(dialog.summary.text(), "Error")
        self.assertIn("dispatch failed", dialog.message_label.text())
        controller.deleteLater()
        dialog.deleteLater()

    def test_semantic_mode_explains_separate_preparation_and_starts_it(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        requested: list[bool] = []
        dialog.semanticIndexRequested.connect(lambda: requested.append(True))
        status = IndexStatus(
            IndexState.READY,
            semantic=SemanticStatus(
                SemanticState.MODEL_READY,
                detail="Build this profile's local semantic index.",
            ),
        )
        dialog.show_status(status)
        dialog.set_mode(SearchMode.SEMANTIC)
        self.app.processEvents()

        self.assertIn("separate one-time preparation", dialog.message_label.text())
        self.assertEqual(
            dialog.message_action.text(), "Prepare / Resume Semantic Search"
        )
        self.assertTrue(dialog.message_action.isVisibleTo(dialog))
        dialog.message_action.click()
        self.assertEqual(requested, [True])
        dialog.deleteLater()

    def test_semantic_autostart_is_explained_with_build_now_action(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        status = SemanticStatus(
            SemanticState.MODEL_READY,
            auto_start=True,
        )
        dialog.show_status(IndexStatus(IndexState.READY, semantic=status))
        dialog.set_mode(SearchMode.SEMANTIC)

        self.assertIn("start automatically", dialog.message_label.text())
        self.assertEqual(
            dialog.message_action.text(),
            "Prepare Semantic Search Now",
        )
        self.assertEqual(dialog.semantic_action.text(), "Prepare Now")
        dialog.deleteLater()

    def test_no_results_replaces_semantic_action_and_escapes_query(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                semantic=SemanticStatus(SemanticState.MODEL_READY),
            )
        )
        dialog.set_mode(SearchMode.SEMANTIC)
        self.assertTrue(dialog.message_action.isVisibleTo(dialog))

        dialog.show_response(
            SearchResponse(request_id=2, query="<b>missing</b>"),
            (),
        )
        self.app.processEvents()

        self.assertFalse(dialog.message_action.isVisibleTo(dialog))
        self.assertEqual(
            dialog.message_label.textFormat(),
            Qt.TextFormat.PlainText,
        )
        self.assertIn("<b>missing</b>", dialog.message_label.text())
        dialog.deleteLater()

    def test_semantic_ready_retries_pending_query_once_and_clears_message(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                semantic=SemanticStatus(SemanticState.INDEXING, progress=0.99),
            )
        )
        dialog.set_mode(SearchMode.SEMANTIC)
        dialog.search.setText("heart failure")
        self.assertEqual(dialog._message_kind, "semantic")

        requested: list[str] = []
        dialog.searchRequested.connect(requested.append)
        ready = IndexStatus(
            IndexState.READY,
            semantic=SemanticStatus(SemanticState.READY, indexed_notes=100),
        )
        dialog.show_status(ready)
        self.app.processEvents()

        self.assertEqual(requested, ["heart failure"])
        self.assertEqual(dialog._message_kind, "")
        self.assertEqual(dialog.stack.currentIndex(), 0)
        self.assertFalse(dialog.message_action.isVisibleTo(dialog))

        # A later periodic READY refresh must not submit the same query again.
        dialog.show_status(ready)
        self.assertEqual(requested, ["heart failure"])
        dialog.deleteLater()

    def test_semantic_ready_without_query_returns_to_help(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                semantic=SemanticStatus(SemanticState.INDEXING),
            )
        )
        dialog.set_mode(SearchMode.SEMANTIC)
        self.assertEqual(dialog._message_kind, "semantic")

        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                semantic=SemanticStatus(SemanticState.READY),
            )
        )
        self.app.processEvents()

        self.assertEqual(dialog._message_kind, "")
        self.assertEqual(dialog.stack.currentIndex(), 0)
        dialog.deleteLater()

    def test_semantic_error_retries_index_not_install(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        index_requests: list[bool] = []
        install_requests: list[bool] = []
        dialog.semanticIndexRequested.connect(lambda: index_requests.append(True))
        dialog.semanticInstallRequested.connect(lambda: install_requests.append(True))
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                semantic=SemanticStatus(
                    SemanticState.ERROR,
                    detail="Embedding stopped.",
                ),
            )
        )

        self.assertEqual(dialog.semantic_action.text(), "Try Again")
        dialog.semantic_action.click()
        dialog.set_mode(SearchMode.SEMANTIC)
        self.assertEqual(dialog.message_action.text(), "Try Again")
        dialog.message_action.click()

        self.assertEqual(index_requests, [True, True])
        self.assertEqual(install_requests, [])
        dialog.deleteLater()

    def test_settings_semantic_error_retries_index(self) -> None:
        dialog = _SettingsDialog(
            SearchMode.SMART,
            50,
            SemanticStatus(SemanticState.ERROR),
        )
        dialog.show()
        index_requests: list[bool] = []
        install_requests: list[bool] = []
        dialog.semanticIndexRequested.connect(lambda: index_requests.append(True))
        dialog.semanticInstallRequested.connect(lambda: install_requests.append(True))

        self.assertEqual(dialog.semantic_action.text(), "Try Again")
        dialog.semantic_action.click()

        self.assertEqual(index_requests, [True])
        self.assertEqual(install_requests, [])
        dialog.deleteLater()

    def test_semantic_model_error_routes_to_repair(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        index_requests: list[bool] = []
        install_requests: list[bool] = []
        dialog.semanticIndexRequested.connect(lambda: index_requests.append(True))
        dialog.semanticInstallRequested.connect(lambda: install_requests.append(True))
        status = SemanticStatus(
            SemanticState.ERROR,
            detail="Could not load ONNX runtime.",
            recovery=SemanticRecovery.MODEL,
        )
        dialog.show_status(IndexStatus(IndexState.READY, semantic=status))

        self.assertEqual(dialog.semantic_action.text(), "Repair Semantic")
        self.assertNotIn("ONNX", dialog.semantic_status.toolTip())
        dialog.semantic_action.click()
        dialog.set_mode(SearchMode.SEMANTIC)
        self.assertEqual(dialog.message_action.text(), "Repair Semantic Search")
        self.assertNotIn("ONNX", dialog.message_label.text())
        dialog.message_action.click()

        self.assertEqual(index_requests, [])
        self.assertEqual(install_requests, [True, True])
        dialog.deleteLater()

    def test_settings_semantic_model_error_routes_to_repair(self) -> None:
        dialog = _SettingsDialog(
            SearchMode.SMART,
            50,
            SemanticStatus(
                SemanticState.ERROR,
                recovery=SemanticRecovery.MODEL,
            ),
        )
        dialog.show()
        install_requests: list[bool] = []
        dialog.semanticInstallRequested.connect(lambda: install_requests.append(True))

        self.assertEqual(dialog.semantic_action.text(), "Repair Semantic Search")
        dialog.semantic_action.click()

        self.assertEqual(install_requests, [True])
        dialog.deleteLater()

    def test_settings_semantic_action_waits_for_text_index(self) -> None:
        dialog = _SettingsDialog(
            SearchMode.SMART,
            50,
            SemanticStatus(SemanticState.MODEL_READY),
            text_index_ready=False,
        )
        dialog.show()
        self.app.processEvents()

        self.assertTrue(dialog.semantic_action.isVisibleTo(dialog))
        self.assertFalse(dialog.semantic_action.isEnabled())
        self.assertIn(
            "waiting for Smart & Exact setup",
            dialog.semantic_status.text(),
        )
        self.assertIn(
            "Wait for Smart & Exact setup",
            dialog.semantic_action.toolTip(),
        )
        dialog.deleteLater()

    def _assert_semantic_row_fits(self, dialog: _SettingsDialog) -> None:
        """Status copy and its action fit the Search page without overlap."""

        page = dialog.tabs.widget(0)
        page_rect = page.rect()

        def page_rect_of(widget) -> QRect:
            return QRect(widget.mapTo(page, widget.rect().topLeft()), widget.size())

        status_rect = page_rect_of(dialog.semantic_status)
        self.assertTrue(page_rect.contains(status_rect))
        # The wrapping label gets at least the height its width requires:
        # no vertical clipping of any line.
        self.assertGreaterEqual(
            status_rect.height(),
            dialog.semantic_status.heightForWidth(status_rect.width()),
        )
        if dialog.semantic_action.isVisibleTo(dialog):
            action_rect = page_rect_of(dialog.semantic_action)
            self.assertTrue(page_rect.contains(action_rect))
            self.assertFalse(status_rect.intersects(action_rect))
            # The button keeps its full natural width: no truncated label.
            self.assertGreaterEqual(
                action_rect.width(),
                dialog.semantic_action.sizeHint().width(),
            )

    def test_settings_search_tab_semantic_copy_fits_at_all_sizes(self) -> None:
        long_indexing = SemanticStatus(
            SemanticState.INDEXING,
            detail="Embedding notes 10,000/40,657.",
            progress=0.17,
        )
        model_error = SemanticStatus(
            SemanticState.ERROR,
            detail="Semantic preparation stopped unexpectedly.",
            recovery=SemanticRecovery.MODEL,
        )
        waiting = SemanticStatus(
            SemanticState.MODEL_READY,
            detail="Build this profile's local semantic index.",
        )
        ready = SemanticStatus(
            SemanticState.READY,
            indexed_notes=40_659,
        )
        cases = (
            # READY is the state that exposed the live Anki/macOS clipping.
            (ready, True),
            # Long INDEXING copy plus the waiting-for-Smart-&-Exact line.
            (long_indexing, False),
            (long_indexing, True),
            # Long ERROR copy plus the waiting line, with a visible action.
            (model_error, False),
            # Waiting-for-Smart-&-Exact copy with a disabled action.
            (waiting, False),
        )
        for width, height in ((620, 540), (760, 620), (982, 614)):
            for status, text_index_ready in cases:
                with self.subTest(
                    size=(width, height),
                    state=status.state,
                    text_index_ready=text_index_ready,
                ):
                    dialog = _SettingsDialog(
                        SearchMode.SMART,
                        50,
                        status,
                        text_index_ready=text_index_ready,
                    )
                    dialog.resize(width, height)
                    dialog.show()
                    self.app.processEvents()

                    self._assert_semantic_row_fits(dialog)
                    # All Search-tab controls stay keyboard reachable and
                    # screen-reader labeled.
                    for control in (
                        dialog.mode_combo,
                        dialog.limit_spin,
                        dialog.semantic_action,
                    ):
                        self.assertNotEqual(
                            control.focusPolicy(),
                            Qt.FocusPolicy.NoFocus,
                        )
                        self.assertTrue(control.accessibleName())
                    self.assertTrue(dialog.semantic_status.accessibleName())
                    self.assertEqual(
                        dialog.mode_combo.width(),
                        dialog.limit_spin.width(),
                    )
                    self.assertNotIn(
                        "searchSettingsPage QComboBox",
                        dialog.styleSheet(),
                    )
                    if status.state is SemanticState.READY:
                        self.assertIn(
                            "40,659 notes",
                            dialog.semantic_status.text(),
                        )
                    dialog.deleteLater()
                    self.app.processEvents()

    def _about_info(self, **overrides) -> AboutInfo:
        values = {
            "product_name": "Smart Search for Anki — Medical",
            "creator": "Saleh Mostafa",
            "version": "1.0.15",
            "logo_path": str(LOGO_PATH),
            "website_url": "https://medbrevia.com/app",
            "feedback_url": "mailto:product@medbrevia.com",
            "privacy_url": "https://medbrevia.com/legal/smart-search-privacy",
        }
        values.update(overrides)
        return AboutInfo(**values)

    def test_settings_dialog_offers_search_and_about_tabs(self) -> None:
        dialog = _SettingsDialog(
            SearchMode.EXACT,
            80,
            None,
            about=self._about_info(),
        )
        dialog.show()
        self.app.processEvents()

        self.assertEqual(dialog.tabs.count(), 2)
        self.assertEqual(dialog.tabs.tabText(0), "Search")
        self.assertEqual(dialog.tabs.tabText(1), "About")
        # Existing settings behavior and values are unchanged.
        self.assertEqual(dialog.values(), (SearchMode.EXACT, 80, True))
        dialog.mode_combo.setCurrentIndex(0)
        dialog.limit_spin.setValue(120)
        dialog.preview_check.setChecked(False)
        self.assertEqual(dialog.values(), (SearchMode.SMART, 120, False))
        dialog.tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertTrue(dialog.button_box.isVisibleTo(dialog))
        self.assertLessEqual(
            dialog.button_box.geometry().bottom(),
            dialog.contentsRect().bottom(),
        )
        dialog.deleteLater()

    def test_about_tab_shows_attribution_privacy_version_logo_and_buttons(self) -> None:
        dialog = _SettingsDialog(
            SearchMode.SMART,
            50,
            None,
            about=self._about_info(),
        )
        dialog.show()
        dialog.tabs.setCurrentIndex(1)
        self.app.processEvents()

        panel = dialog.about_panel
        self.assertTrue(panel.isVisibleTo(dialog))
        self.assertEqual(panel.name_label.text(), "Smart Search for Anki — Medical")
        self.assertIn("1.0.15", panel.version_label.text())
        self.assertIn("Created by Saleh Mostafa", panel.attribution_label.text())
        self.assertIn("MedBrevia", panel.attribution_label.text())
        self.assertEqual(
            panel.privacy_label.text(),
            "Searches, card contents, and indexes remain on this computer.",
        )
        self.assertEqual(
            panel.network_label.text(),
            "Anki may check for add-on updates. Other network access occurs "
            "only when you set up or repair Semantic Search, or open an "
            "About link.",
        )
        self.assertIn(
            "not affiliated with or endorsed by Anki",
            panel.independence_label.text(),
        )

        # The tagline and the inline text links were removed entirely.
        self.assertFalse(hasattr(panel, "tagline_label"))
        self.assertFalse(hasattr(panel, "links_label"))
        for label in panel.findChildren(QLabel):
            self.assertNotIn("Fast, forgiving", label.text())
            self.assertNotIn("<a ", label.text())
            self.assertNotIn("medbrevia.com/app", label.text())

        # The canonical bundled logo loads and renders at the designed size.
        self.assertTrue(LOGO_PATH.is_file())
        self.assertFalse(panel.logo_label.pixmap().isNull())
        self.assertLessEqual(panel.logo_label.pixmap().width(), 96)
        self.assertLessEqual(panel.logo_label.pixmap().height(), 96)

        # The Mobile App and Feedback buttons stay visible and keyboard
        # reachable. Local/manual installations do not offer native updates.
        self.assertEqual(panel.mobile_button.text(), "Mobile App")
        self.assertEqual(panel.feedback_button.text(), "Feedback")
        self.assertTrue(panel.mobile_button.isVisibleTo(dialog))
        self.assertTrue(panel.feedback_button.isVisibleTo(dialog))
        self.assertFalse(panel.update_button.isVisibleTo(dialog))
        self.assertNotEqual(
            panel.mobile_button.focusPolicy(),
            Qt.FocusPolicy.NoFocus,
        )
        self.assertNotEqual(
            panel.feedback_button.focusPolicy(),
            Qt.FocusPolicy.NoFocus,
        )

        # Meaningful accessible names for assistive technology.
        self.assertTrue(panel.accessibleName())
        self.assertTrue(panel.logo_label.accessibleName())
        self.assertTrue(panel.attribution_label.accessibleName())
        self.assertTrue(panel.privacy_label.accessibleName())
        self.assertTrue(panel.network_label.accessibleName())
        self.assertTrue(panel.mobile_button.accessibleName())
        self.assertTrue(panel.feedback_button.accessibleName())
        dialog.deleteLater()

    def test_public_about_offers_native_update_and_accepts_settings(self) -> None:
        dialog = _SettingsDialog(
            SearchMode.EXACT,
            80,
            None,
            about=self._about_info(can_check_for_updates=True),
        )
        requests: list[bool] = []
        dialog.updateRequested.connect(lambda: requests.append(True))
        dialog.show()
        dialog.tabs.setCurrentIndex(1)
        self.app.processEvents()

        panel = dialog.about_panel
        self.assertTrue(panel.update_button.isVisibleTo(dialog))
        self.assertEqual(panel.update_button.text(), "Check & Update")
        self.assertIn("Updates managed by Anki", panel.version_label.text())
        self.assertNotEqual(
            panel.update_button.focusPolicy(),
            Qt.FocusPolicy.NoFocus,
        )
        panel.update_button.click()
        self.app.processEvents()

        self.assertEqual(requests, [True])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.values(), (SearchMode.EXACT, 80, True))
        dialog.deleteLater()

    def test_about_link_activation_routes_to_desktop_services(self) -> None:
        panel = AboutPanel(self._about_info())
        opened: list[str] = []
        panel._url_opener = lambda url: opened.append(url.toString())

        panel.mobile_button.click()
        panel.feedback_button.click()

        self.assertEqual(
            opened,
            [
                "https://medbrevia.com/app",
                "mailto:product@medbrevia.com",
            ],
        )
        panel.deleteLater()

    def test_search_dialog_defaults_to_bundled_about_and_accepts_host_info(self) -> None:
        # Standalone default stays safe and truthful.
        dialog = SearchDialog()
        fallback = dialog._about
        self.assertEqual(fallback.product_name, "Smart Search for Anki — Medical")
        self.assertEqual(fallback.creator, "Saleh Mostafa")
        self.assertEqual(fallback.version, "1.0.24")
        self.assertTrue(Path(fallback.logo_path).is_file())
        panel = AboutPanel(fallback)
        self.assertFalse(panel.logo_label.pixmap().isNull())
        panel.deleteLater()
        dialog.deleteLater()

        # Host-provided attribution flows into the settings About tab.
        about = self._about_info(version="9.9.9-test")
        dialog = SearchDialog(about=about)
        self.assertIs(dialog._about, about)
        settings = _SettingsDialog(
            dialog.mode(),
            50,
            None,
            dialog,
            about=dialog._about,
        )
        self.assertIn("9.9.9-test", settings.about_panel.version_label.text())
        settings.deleteLater()
        dialog.deleteLater()

    def test_semantic_action_waits_for_text_index_then_reenables(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        semantic = SemanticStatus(SemanticState.MODEL_READY)
        dialog.show_status(
            IndexStatus(
                IndexState.BUILDING,
                progress=0.5,
                semantic=semantic,
            )
        )
        self.app.processEvents()

        self.assertTrue(dialog.semantic_action.isVisibleTo(dialog))
        self.assertFalse(dialog.semantic_action.isEnabled())
        self.assertIn("waiting for Smart & Exact", dialog.semantic_status.text())
        self.assertIn("after Smart & Exact", dialog.semantic_action.toolTip())

        dialog.show_status(IndexStatus(IndexState.READY, semantic=semantic))
        self.app.processEvents()

        self.assertTrue(dialog.semantic_action.isEnabled())
        self.assertNotIn("waiting for Smart & Exact", dialog.semantic_status.text())
        self.assertEqual(dialog.semantic_action.toolTip(), "")
        dialog.deleteLater()

    def test_missing_semantic_status_hides_old_progress(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                semantic=SemanticStatus(
                    SemanticState.INDEXING,
                    progress=0.4,
                ),
            )
        )
        self.app.processEvents()
        self.assertTrue(dialog.semantic_progress.isVisibleTo(dialog))

        dialog.show_status(IndexStatus(IndexState.READY, semantic=None))
        self.app.processEvents()

        self.assertFalse(dialog.semantic_progress.isVisibleTo(dialog))
        dialog.deleteLater()

    def test_bulk_selection_counts_all_none_invert_and_resets(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(IndexStatus(IndexState.READY))
        response = SearchResponse(
            request_id=10,
            query="pain",
            results=(
                SearchResult(
                    note_id=1,
                    card_ids=(101, 102),
                    title="First",
                    sibling_count=2,
                ),
                SearchResult(
                    note_id=2,
                    card_ids=(201,),
                    title="Second",
                    sibling_count=1,
                ),
            ),
            total_results=2,
        )
        dialog.show_response(response, ())
        self.app.processEvents()

        model = dialog.results.results_model()
        self.assertTrue(dialog.batch_bar.isVisibleTo(dialog))
        self.assertEqual(
            dialog.selection_summary.text(),
            "0 notes · 0 cards selected",
        )
        model.set_checked(0, True)
        self.assertEqual(
            dialog.selection_summary.text(),
            "1 note · 2 cards selected",
        )
        self.assertEqual(
            dialog.master_check.checkState(),
            Qt.CheckState.PartiallyChecked,
        )
        self.assertTrue(dialog.flag_button.isEnabled())
        self.assertTrue(dialog.tags_button.isEnabled())

        model.set_all_checked(True)
        self.assertEqual(
            dialog.selection_summary.text(),
            "2 notes · 3 cards selected",
        )
        self.assertEqual(
            dialog.master_check.checkState(),
            Qt.CheckState.Checked,
        )
        model.invert_checked()
        self.assertEqual(
            dialog.selection_summary.text(),
            "0 notes · 0 cards selected",
        )
        model.set_all_checked(True)
        model.set_all_checked(False)
        self.assertEqual(model.checked_results(), ())

        dialog.show_response(
            SearchResponse(
                request_id=11,
                query="new",
                results=(
                    SearchResult(note_id=3, card_ids=(301,), title="New"),
                ),
                total_results=1,
            ),
            (),
        )
        self.assertEqual(model.checked_results(), ())
        self.assertEqual(
            dialog.selection_summary.text(),
            "0 notes · 0 cards selected",
        )
        dialog.deleteLater()

    def test_batch_toolbar_sits_below_helpful_terms_and_above_results(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(IndexStatus(IndexState.READY))
        correction = Correction("buproprion", "bupropion")
        dialog.show_response(
            SearchResponse(
                request_id=11,
                query="buproprion",
                results=(
                    SearchResult(
                        note_id=3,
                        card_ids=(301,),
                        title="Bupropion",
                    ),
                ),
                total_results=1,
            ),
            (correction,),
        )
        self.app.processEvents()

        layout = dialog.layout()
        self.assertTrue(dialog.chip_bar.isVisibleTo(dialog))
        self.assertTrue(dialog.batch_bar.isVisibleTo(dialog))
        self.assertLess(
            layout.indexOf(dialog.chip_bar),
            layout.indexOf(dialog.batch_bar),
        )
        self.assertEqual(
            layout.indexOf(dialog.batch_bar) + 1,
            layout.indexOf(dialog.content_splitter),
        )
        dialog.deleteLater()

    def test_status_ui_hides_model_and_runtime_identifiers(self) -> None:
        internal_name = "MedEmbed-small-v0.1-int8"
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(
            IndexStatus(
                IndexState.READY,
                model_name=internal_name,
                semantic=SemanticStatus(
                    SemanticState.READY,
                    detail="Loaded MedEmbed through ONNX Runtime.",
                ),
            )
        )
        self.app.processEvents()

        visible_status = " ".join(
            (
                dialog.status._label.text(),
                dialog.status.toolTip(),
                dialog.status.accessibleDescription(),
                dialog.semantic_status.text(),
                dialog.semantic_status.toolTip(),
            )
        )
        self.assertNotIn("MedEmbed", visible_status)
        self.assertNotIn("ONNX", visible_status)

        settings = _SettingsDialog(
            SearchMode.SMART,
            50,
            SemanticStatus(
                SemanticState.READY,
                detail="Loaded MedEmbed through ONNX Runtime.",
            ),
        )
        settings.show()
        self.app.processEvents()
        settings_text = " ".join(
            (
                settings.semantic_status.text(),
                settings.semantic_status.toolTip(),
                settings.semantic_action.text(),
                settings.semantic_action.toolTip(),
            )
        )
        self.assertNotIn("MedEmbed", settings_text)
        self.assertNotIn("ONNX", settings_text)
        settings.deleteLater()
        dialog.deleteLater()

    def test_ready_status_dot_repaints_with_the_ready_color(self) -> None:
        dialog = SearchDialog()
        dialog.show_status(IndexStatus(IndexState.READY))
        dialog.show()
        self.app.processEvents()

        dot = dialog.status._dot
        center = dot.grab().toImage().pixelColor(
            dot.width() // 2,
            dot.height() // 2,
        )
        self.assertEqual(center.name(), dot._color.name())
        self.assertIn(dot._color.name(), {"#1d7a34", "#3d9a50"})
        dialog.deleteLater()

    def test_live_palette_switch_restyles_status_and_results(self) -> None:
        original = QPalette(self.app.palette())
        dialog = SearchDialog()
        dialog.show_status(IndexStatus(IndexState.READY))
        dialog.show_response(
            SearchResponse(
                request_id=4,
                query="palette",
                results=(SearchResult(note_id=4, title="Palette result"),),
                total_results=1,
            ),
            (),
        )
        dialog.show()
        try:
            dark = QPalette(original)
            dark.setColor(QPalette.ColorRole.Window, QColor("#191d24"))
            dark.setColor(QPalette.ColorRole.Base, QColor("#232830"))
            dark.setColor(QPalette.ColorRole.Text, QColor("#eef1f5"))
            dark.setColor(
                QPalette.ColorRole.PlaceholderText,
                QColor("#8992a0"),
            )
            self.app.setPalette(dark)
            QApplication.sendEvent(
                dialog,
                QEvent(QEvent.Type.PaletteChange),
            )
            self.app.processEvents()
            dark_dot = dialog.status._dot._color.name()

            light = QPalette(original)
            light.setColor(QPalette.ColorRole.Window, QColor("#f6f7f9"))
            light.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
            light.setColor(QPalette.ColorRole.Text, QColor("#20242b"))
            light.setColor(
                QPalette.ColorRole.PlaceholderText,
                QColor("#68707c"),
            )
            self.app.setPalette(light)
            QApplication.sendEvent(
                dialog,
                QEvent(QEvent.Type.PaletteChange),
            )
            self.app.processEvents()
            light_dot = dialog.status._dot._color.name()
            center = dialog.status._dot.grab().toImage().pixelColor(
                dialog.status._dot.width() // 2,
                dialog.status._dot.height() // 2,
            )

            self.assertNotEqual(dark_dot, light_dot)
            self.assertEqual(center.name(), light_dot)
            self.assertFalse(dialog.grab().isNull())
        finally:
            dialog.deleteLater()
            self.app.setPalette(original)
            self.app.processEvents()

    def test_range_checking_and_browser_open_use_only_checked_results(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(IndexStatus(IndexState.READY))
        results = tuple(
            SearchResult(
                note_id=note_id,
                card_ids=(note_id * 10,),
                card_states=(CardState(note_id * 10),),
                title=f"Result {note_id}",
            )
            for note_id in (1, 2, 3, 4)
        )
        dialog.show_response(
            SearchResponse(
                request_id=12,
                query="range",
                results=results,
                total_results=4,
            ),
            (),
        )
        self.app.processEvents()

        view = dialog.results
        view.select_row(1)
        self.assertTrue(view.check_range_to(3))
        self.assertEqual(
            [result.note_id for result in view.checked_results()],
            [2, 3, 4],
        )
        self.assertTrue(dialog.open_selected_button.isEnabled())

        opened: list[tuple[SearchResult, ...]] = []
        dialog.openAllRequested.connect(opened.append)
        dialog._open_selected_results()
        self.assertEqual([result.note_id for result in opened[-1]], [2, 3, 4])
        dialog._open_all_results()
        self.assertEqual([result.note_id for result in opened[-1]], [2, 3, 4])

        view.results_model().set_all_checked(False)
        dialog._open_all_results()
        self.assertEqual(
            [result.note_id for result in opened[-1]],
            [1, 2, 3, 4],
        )
        self.assertFalse(dialog.open_selected_button.isEnabled())
        dialog.deleteLater()

    def test_mouse_wheel_moves_about_one_card_without_changing_selection(
        self,
    ) -> None:
        view = ResultsView()
        view.resize(760, 320)
        view.results_model().set_results(
            tuple(
                SearchResult(
                    note_id=note_id,
                    card_ids=(note_id * 10,),
                    title=f"Result {note_id}",
                )
                for note_id in range(1, 31)
            )
        )
        view.show()
        self.app.processEvents()
        self.assertGreater(view.verticalScrollBar().maximum(), 0)
        self.assertEqual(view.verticalScrollBar().singleStep(), 40)

        view.select_row(0)
        view.results_model().set_checked(0, True)
        highlighted = []
        view.currentResultChanged.connect(highlighted.append)
        global_position = QPointF(
            view.viewport().mapToGlobal(QPoint(20, 20))
        )
        wheel = QWheelEvent(
            QPointF(20, 20),
            global_position,
            QPoint(),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(view.viewport(), wheel)
        self.app.processEvents()

        # The platform default is three wheel lines, so a 40px single step
        # moves about one 128px card instead of three cards per notch.
        self.assertGreater(view.verticalScrollBar().value(), 0)
        self.assertLessEqual(view.verticalScrollBar().value(), 128)
        self.assertEqual(view.currentIndex().row(), 0)
        self.assertEqual(
            [result.note_id for result in view.checked_results()],
            [1],
        )
        self.assertEqual(highlighted, [])
        view.deleteLater()

    def test_preview_follows_arrow_navigation_without_changing_checkboxes(
        self,
    ) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(IndexStatus(IndexState.READY))
        results = tuple(
            SearchResult(
                note_id=note_id,
                card_ids=(note_id * 10,),
                title=f"Result {note_id}",
            )
            for note_id in (1, 2, 3)
        )
        changed: list[SearchResult | None] = []
        toggled: list[SearchResult | None] = []
        dialog.previewResultChanged.connect(changed.append)
        dialog.previewToggleRequested.connect(toggled.append)
        dialog.show_response(
            SearchResponse(
                request_id=13,
                query="preview",
                results=results,
                total_results=3,
            ),
            (),
        )
        dialog.results.results_model().set_checked(0, True)
        dialog.results.setFocus()
        self.app.processEvents()

        self.assertTrue(dialog.preview_button.isEnabled())
        dialog.preview_button.click()
        self.assertEqual(toggled[-1], results[0])

        QTest.keyClick(dialog.results, Qt.Key.Key_Down)
        self.app.processEvents()
        self.assertEqual(dialog.results.current_result(), results[1])
        self.assertEqual(changed[-1], results[1])
        self.assertEqual(
            dialog.results.results_model().checked_results(),
            (results[0],),
        )

        self.assertTrue(dialog.move_result(1))
        self.assertEqual(dialog.results.current_result(), results[2])
        self.assertFalse(dialog.move_result(1))
        self.assertTrue(dialog.can_move_result(-1))
        self.assertFalse(dialog.can_move_result(1))

        dialog.set_preview_active(False)
        self.assertFalse(dialog.preview_button.isChecked())
        dialog.deleteLater()

    def test_result_keys_reveal_preview_and_shift_space_checks(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        result = SearchResult(note_id=7, card_ids=(71,), title="Seven")
        dialog.show_response(
            SearchResponse(
                request_id=14,
                query="seven",
                results=(result,),
                total_results=1,
            ),
            (),
        )
        requested: list[tuple[SearchResult, str]] = []
        dialog.previewSideRequested.connect(
            lambda current, side: requested.append((current, side))
        )
        dialog.results.setFocus()
        self.app.processEvents()

        QTest.keyClick(dialog.results, Qt.Key.Key_Space)
        QTest.keyClick(dialog.results, Qt.Key.Key_Right)
        QTest.keyClick(dialog.results, Qt.Key.Key_Left)
        self.app.processEvents()

        self.assertEqual(
            requested,
            [
                (result, "answer"),
                (result, "answer"),
                (result, "question"),
            ],
        )
        self.assertEqual(dialog.results.checked_results(), ())

        repeated = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.NoModifier,
            " ",
            True,
            2,
        )
        QApplication.sendEvent(dialog.results, repeated)
        self.assertEqual(len(requested), 3)

        QTest.keyClick(
            dialog.results,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.ShiftModifier,
        )
        self.app.processEvents()
        self.assertEqual(dialog.results.checked_results(), (result,))
        self.assertEqual(len(requested), 3)

        # Selection keeps one stable shortcut even when Card preview is off.
        dialog.set_preview_enabled(False)
        QTest.keyClick(dialog.results, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertEqual(dialog.results.checked_results(), (result,))
        self.assertEqual(len(requested), 3)
        dialog.deleteLater()

    def test_preview_side_keys_do_not_capture_search_or_no_card_results(
        self,
    ) -> None:
        dialog = SearchDialog()
        dialog.show()
        no_card = SearchResult(note_id=8, card_ids=(), title="Missing")
        dialog.show_response(
            SearchResponse(
                request_id=15,
                query="missing",
                results=(no_card,),
                total_results=1,
            ),
            (),
        )
        requested: list[tuple[SearchResult, str]] = []
        dialog.previewSideRequested.connect(
            lambda current, side: requested.append((current, side))
        )

        dialog.results.setFocus()
        QTest.keyClick(dialog.results, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertEqual(requested, [])
        self.assertEqual(dialog.results.checked_results(), ())

        QTest.keyClick(
            dialog.results,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.assertEqual(dialog.results.checked_results(), ())

        dialog.search.setText("ab")
        dialog.search.setCursorPosition(1)
        dialog.search.setFocus()
        QTest.keyClick(dialog.search, Qt.Key.Key_Space)
        QTest.keyClick(dialog.search, Qt.Key.Key_Left)
        self.app.processEvents()
        self.assertEqual(dialog.search.text(), "a b")
        self.assertEqual(requested, [])
        dialog.deleteLater()

    def test_inline_preview_pane_opens_edits_expands_and_closes_in_place(
        self,
    ) -> None:
        dialog = SearchDialog()
        result = SearchResult(
            note_id=7,
            card_ids=(71, 72),
            title="Seven",
        )
        dialog.search.setText("seven is:suspended")
        dialog.show_response(
            SearchResponse(
                request_id=14,
                query=dialog.query(),
                results=(result,),
                total_results=1,
            ),
            (),
        )
        dialog.results.results_model().set_checked(0, True)
        modes: list[str] = []
        toggled: list[SearchResult | None] = []
        dialog.previewModeChanged.connect(modes.append)
        dialog.previewToggleRequested.connect(toggled.append)
        dialog.show()
        dialog.set_preview_target_title("Seven")
        dialog.set_preview_active(True)
        self.app.processEvents()

        self.assertEqual(dialog.content_splitter.indexOf(dialog.stack), 0)
        self.assertEqual(dialog.content_splitter.indexOf(dialog.preview_pane), 1)
        self.assertTrue(dialog.preview_pane.isVisibleTo(dialog))
        self.assertTrue(all(size > 0 for size in dialog.content_splitter.sizes()))
        self.assertEqual(dialog.preview_pane.target_title(), "Seven")
        self.assertEqual(dialog.preview_pane.sibling_label.text(), "0/0")
        self.assertFalse(dialog.preview_pane.previous_button.isVisible())
        self.assertTrue(dialog.preview_pane.replay_button.isVisibleTo(dialog))

        dialog.preview_pane.edit_button.click()
        self.assertEqual(dialog.preview_pane.mode(), "edit")
        self.assertEqual(modes, ["edit"])
        self.assertFalse(dialog.preview_pane.replay_button.isVisible())

        dialog.preview_pane.expand_button.click()
        self.app.processEvents()
        self.assertTrue(dialog.preview_pane.is_expanded())
        self.assertFalse(dialog.stack.isVisible())
        dialog.preview_pane.expand_button.click()
        self.app.processEvents()
        self.assertFalse(dialog.preview_pane.is_expanded())
        self.assertTrue(dialog.stack.isVisibleTo(dialog))

        dialog.preview_pane.close_button.click()
        self.assertEqual(toggled, [None])
        dialog.set_preview_active(False)
        self.assertFalse(dialog.preview_pane.isVisible())
        self.assertEqual(dialog.query(), "seven is:suspended")
        self.assertEqual(dialog.results.results_model().results(), (result,))
        self.assertEqual(
            dialog.results.results_model().checked_results(),
            (result,),
        )
        dialog.deleteLater()

    def test_preview_shortcut_toggles_only_when_a_result_is_available(self) -> None:
        dialog = SearchDialog()
        toggled: list[SearchResult | None] = []
        dialog.previewToggleRequested.connect(toggled.append)
        dialog._toggle_preview_shortcut()
        self.assertEqual(toggled, [])

        result = SearchResult(note_id=7, card_ids=(71,), title="Seven")
        dialog.show_response(
            SearchResponse(
                request_id=14,
                query="seven",
                results=(result,),
                total_results=1,
            ),
            (),
        )
        dialog._toggle_preview_shortcut()
        dialog._toggle_preview_shortcut()

        self.assertEqual(toggled, [result, None])

        no_card = SearchResult(note_id=8, card_ids=(), title="No live cards")
        dialog.show_response(
            SearchResponse(
                request_id=15,
                query="missing",
                results=(no_card,),
                total_results=1,
            ),
            (),
        )
        self.assertFalse(dialog.preview_button.isEnabled())
        dialog._toggle_preview_shortcut()
        self.assertEqual(toggled, [result, None])

        dialog.set_preview_enabled(False)
        self.assertFalse(dialog.preview_button.isEnabled())
        dialog._toggle_preview_shortcut()
        self.assertEqual(toggled, [result, None])
        dialog.deleteLater()

    def test_down_into_results_requests_auto_preview_for_current_row(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        result = SearchResult(note_id=7, card_ids=(71,), title="Seven")
        browsed: list[SearchResult | None] = []
        dialog.previewResultChanged.connect(browsed.append)
        dialog.show_response(
            SearchResponse(
                request_id=16,
                query="seven",
                results=(result,),
                total_results=1,
            ),
            (),
        )
        dialog.search.setFocus()
        self.app.processEvents()
        browsed.clear()

        QTest.keyClick(dialog.search, Qt.Key.Key_Down)
        self.app.processEvents()

        self.assertTrue(dialog.results.hasFocus())
        self.assertEqual(browsed, [result])
        dialog.deleteLater()

    def test_bulk_action_payloads_busy_state_and_success_clear(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(IndexStatus(IndexState.READY))
        results = (
            SearchResult(note_id=7, card_ids=(71, 72), title="Seven"),
            SearchResult(note_id=8, card_ids=(81,), title="Eight"),
        )
        dialog.show_response(
            SearchResponse(
                request_id=20,
                query="heart",
                results=results,
                total_results=2,
            ),
            (),
        )
        model = dialog.results.results_model()
        model.set_all_checked(True)

        flags: list[tuple[tuple[SearchResult, ...], int]] = []
        suspensions: list[tuple[tuple[SearchResult, ...], bool]] = []
        tags: list[tuple[tuple[SearchResult, ...], bool]] = []
        dialog.flagRequested.connect(
            lambda selected, flag: flags.append((selected, flag))
        )
        dialog.suspensionRequested.connect(
            lambda selected, suspend: suspensions.append((selected, suspend))
        )
        dialog.tagActionRequested.connect(
            lambda selected, add: tags.append((selected, add))
        )

        dialog._emit_flag(3)
        dialog._emit_suspension(False)
        dialog._emit_tag_action(True)
        self.assertEqual(
            [result.note_id for result in flags[0][0]],
            [7, 8],
        )
        self.assertEqual(flags[0][1], 3)
        self.assertFalse(suspensions[0][1])
        self.assertTrue(tags[0][1])

        dialog.set_batch_action_busy(True, "Applying change…")
        self.assertFalse(dialog.results.isEnabled())
        self.assertFalse(dialog.search.isEnabled())
        self.assertFalse(dialog.segmented.isEnabled())
        self.assertFalse(dialog.settings_button.isEnabled())
        self.assertFalse(dialog.rebuild_button.isEnabled())
        self.assertFalse(dialog.flag_button.isEnabled())
        self.assertFalse(dialog.tags_button.isEnabled())
        dialog.finish_batch_action(
            True,
            "Suspended 3 cards from 2 notes — Undo available",
        )
        self.assertTrue(dialog.results.isEnabled())
        self.assertTrue(dialog.search.isEnabled())
        self.assertTrue(dialog.segmented.isEnabled())
        self.assertTrue(dialog.settings_button.isEnabled())
        self.assertTrue(dialog.rebuild_button.isEnabled())
        self.assertEqual(model.checked_results(), ())
        self.assertEqual(
            dialog.summary.text(),
            "Suspended 3 cards from 2 notes — Undo available",
        )
        dialog.deleteLater()

    def test_result_context_menu_targets_clicked_or_checked_results(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(IndexStatus(IndexState.READY))
        results = (
            SearchResult(
                note_id=1,
                card_ids=(11,),
                card_states=(CardState(11, suspended=False),),
                title="One",
            ),
            SearchResult(
                note_id=2,
                card_ids=(21,),
                card_states=(CardState(21, suspended=True),),
                title="Two",
            ),
            SearchResult(
                note_id=3,
                card_ids=(31,),
                card_states=(CardState(31, suspended=False),),
                title="Three",
            ),
        )
        dialog.show_response(
            SearchResponse(
                request_id=21,
                query="context",
                results=results,
                total_results=3,
            ),
            (),
        )
        model = dialog.results.results_model()
        model.set_checked(0, True)
        model.set_checked(1, True)

        # A checked row keeps the complete checked selection as the target.
        selected = dialog._prepare_result_context_selection(1)
        self.assertEqual([result.note_id for result in selected], [1, 2])
        mixed_menu = dialog._build_result_context_menu(selected)
        mixed_labels = [action.text() for action in mixed_menu.actions()]
        self.assertIn("Suspend", mixed_labels)
        self.assertIn("Unsuspend", mixed_labels)
        self.assertIn("Bury", mixed_labels)
        self.assertNotIn("Unbury", mixed_labels)
        self.assertIn("Change Deck…", mixed_labels)
        mixed_copy = next(
            action
            for action in mixed_menu.actions()
            if action.text() == "Create Copy…"
        )
        self.assertFalse(mixed_copy.isEnabled())
        self.assertIn("Flag", mixed_labels)
        self.assertIn("Tags", mixed_labels)

        # An unchecked row becomes the sole selection and sole action target.
        selected = dialog._prepare_result_context_selection(2)
        self.assertEqual([result.note_id for result in selected], [3])
        active_menu = dialog._build_result_context_menu(selected)
        active_labels = [action.text() for action in active_menu.actions()]
        self.assertIn("Suspend", active_labels)
        self.assertNotIn("Unsuspend", active_labels)
        self.assertIn("Bury", active_labels)
        active_copy = next(
            action
            for action in active_menu.actions()
            if action.text() == "Create Copy…"
        )
        self.assertTrue(active_copy.isEnabled())

        flags: list[tuple[tuple[SearchResult, ...], int]] = []
        tags: list[tuple[tuple[SearchResult, ...], bool]] = []
        opened: list[tuple[SearchResult, ...]] = []
        copied: list[tuple[SearchResult, ...]] = []
        dialog.flagRequested.connect(
            lambda target, flag: flags.append((target, flag))
        )
        dialog.tagActionRequested.connect(
            lambda target, add: tags.append((target, add))
        )
        dialog.openAllRequested.connect(opened.append)
        dialog.createCopyRequested.connect(copied.append)

        flag_action = next(
            action
            for action in next(
                action for action in active_menu.actions()
                if action.text() == "Flag"
            ).menu().actions()
            if action.text() == "Blue"
        )
        flag_action.trigger()
        tag_action = next(
            action
            for action in next(
                action for action in active_menu.actions()
                if action.text() == "Tags"
            ).menu().actions()
            if action.text() == "Add Tag…"
        )
        tag_action.trigger()
        next(
            action for action in active_menu.actions()
            if action.text() == "Open in Browser"
        ).trigger()
        active_copy.trigger()

        self.assertEqual([result.note_id for result in flags[0][0]], [3])
        self.assertEqual(flags[0][1], 4)
        self.assertEqual([result.note_id for result in tags[0][0]], [3])
        self.assertTrue(tags[0][1])
        self.assertEqual([result.note_id for result in opened[0]], [3])
        self.assertEqual([result.note_id for result in copied[0]], [3])

        mixed_menu.deleteLater()
        active_menu.deleteLater()
        dialog.deleteLater()

    def test_context_menu_bury_toggle_and_deck_move_keep_frozen_targets(self) -> None:
        dialog = SearchDialog()
        dialog.show()
        dialog.show_status(IndexStatus(IndexState.READY))
        results = (
            SearchResult(
                note_id=1,
                card_ids=(11,),
                card_states=(CardState(11),),
                title="Active",
            ),
            SearchResult(
                note_id=2,
                card_ids=(21,),
                card_states=(CardState(21, buried=True),),
                title="Buried",
            ),
            SearchResult(
                note_id=3,
                card_ids=(31,),
                card_states=(CardState(31),),
                title="Other",
            ),
        )
        dialog.show_response(
            SearchResponse(
                request_id=22,
                query="context actions",
                results=results,
                total_results=3,
            ),
            (),
        )
        model = dialog.results.results_model()
        model.set_checked(0, True)
        model.set_checked(1, True)
        selected = model.checked_results()

        menu = dialog._build_result_context_menu(selected)
        labels = [action.text() for action in menu.actions()]
        self.assertIn("Bury", labels)
        self.assertIn("Unbury", labels)

        burial = []
        dialog.burialRequested.connect(
            lambda target, bury: burial.append((target, bury))
        )
        model.set_all_checked(False)
        model.set_checked(2, True)
        next(action for action in menu.actions() if action.text() == "Bury").trigger()
        next(action for action in menu.actions() if action.text() == "Unbury").trigger()
        self.assertEqual(
            [([result.note_id for result in target], bury) for target, bury in burial],
            [([1, 2], True), ([1, 2], False)],
        )

        dialog.show_deck_catalog(
            DeckCatalog(
                (
                    DeckEntry(1, "Default"),
                    DeckEntry(2, "Medicine::Cardiology"),
                ),
                1,
            )
        )
        anchor = dialog.mapToGlobal(dialog.rect().center())
        dialog._open_deck_destination_picker(selected, anchor)
        moved = []
        dialog.deckChangeRequested.connect(
            lambda target, deck_id, name: moved.append((target, deck_id, name))
        )
        dialog._apply_deck_destination(DeckEntry(2, "Medicine::Cardiology"))

        self.assertEqual([result.note_id for result in moved[0][0]], [1, 2])
        self.assertEqual(moved[0][1:], (2, "Medicine::Cardiology"))
        dialog.deck_destination_picker.reject()
        menu.deleteLater()
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
