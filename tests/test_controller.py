from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_smart_search_controller_tests"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    package.__package__ = PACKAGE
    sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.controller", ROOT / "controller.py"
)
assert spec and spec.loader
controller = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = controller
spec.loader.exec_module(controller)

models = sys.modules[f"{PACKAGE}.backend.models"]
contracts = sys.modules[f"{PACKAGE}.ui.contracts"]


class _AddonManager:
    def __init__(self) -> None:
        self.config = {
            "result_limit": 70,
            "semantic_enabled": True,
            "shortcut": "Meta+K",
        }

    def getConfig(self, _module: str):
        return dict(self.config)

    def writeConfig(self, _module: str, config):
        self.config = dict(config)


class _ProfileManager:
    name = "Step 1 Profile"


class _Collection:
    path = "/tmp/controller-tests/collection.anki2"

    def __init__(self) -> None:
        self.queries = []
        self.cards_by_note = {}
        self.cards = {}
        self.card_query_results = {}
        self.notes = {}
        self.decks = types.SimpleNamespace(
            name=lambda deck_id: f"Deck {int(deck_id)}"
        )

    def find_notes(self, query: str):
        self.queries.append(query)
        return []

    def find_cards(self, query: str):
        self.queries.append(query)
        return tuple(self.card_query_results.get(query, ()))

    def card_ids_of_note(self, note_id: int):
        return tuple(self.cards_by_note.get(int(note_id), ()))

    def get_card(self, card_id: int):
        return self.cards[int(card_id)]

    def get_note(self, note_id: int):
        return self.notes[int(note_id)]


class _MainWindow:
    def __init__(self) -> None:
        self.addonManager = _AddonManager()
        self.pm = _ProfileManager()
        self.col = _Collection()


class _SynchronousBackend(controller.AnkiSearchBackend):
    def _run_query_op(self, *, uses_collection, op, success, failure):
        del uses_collection
        try:
            success(op(self.mw.col))
        except Exception as error:  # exercise the same callback seam as QueryOp
            failure(error)


class _ConcurrentExternalBackend(_SynchronousBackend):
    def __init__(self, *args, **kwargs) -> None:
        self.threads = []
        super().__init__(*args, **kwargs)

    def _run_query_op(self, *, uses_collection, op, success, failure):
        if uses_collection:
            return super()._run_query_op(
                uses_collection=uses_collection,
                op=op,
                success=success,
                failure=failure,
            )

        def run() -> None:
            try:
                success(op(self.mw.col))
            except Exception as error:
                failure(error)

        worker = threading.Thread(target=run, daemon=True)
        self.threads.append(worker)
        worker.start()


class _BlockingSemanticService:
    def __init__(
        self,
        *,
        indexed: bool = False,
        block_search: bool = False,
        index_count: int | None = None,
    ) -> None:
        self.index = self
        self.started = threading.Event()
        self.release = threading.Event()
        self.indexed = indexed
        self.index_count = (
            int(index_count) if index_count is not None else int(indexed)
        )
        self.block_search = block_search
        self.search_started = threading.Event()
        self.release_search = threading.Event()
        self.index_calls = 0
        self.indexed_note_ids: list[tuple[int, ...]] = []
        self.removed_note_ids: list[tuple[int, ...]] = []
        self.source_generation = 1 if indexed else None
        self.cleared_generations: list[int | None] = []
        self.marked_generations: list[int] = []

    def status(self):
        return types.SimpleNamespace(
            supported=True,
            runtime_ready=True,
            model_ready=True,
            index_count=self.index_count,
            error=None,
            ready=True,
        )

    def known_hashes(self):
        return {}

    def clear_source_generation(self) -> None:
        self.cleared_generations.append(self.source_generation)
        self.source_generation = None

    def mark_source_generation(self, generation: int) -> None:
        self.source_generation = int(generation)
        self.marked_generations.append(int(generation))

    def remove_notes(self, note_ids) -> None:
        removed = tuple(int(value) for value in note_ids)
        self.removed_note_ids.append(removed)
        self.index_count = max(0, self.index_count - len(removed))
        self.indexed = self.index_count > 0

    def index_documents(self, documents, *, progress=None) -> int:
        self.index_calls += 1
        self.indexed_note_ids.append(
            tuple(int(document.note_id) for document in documents)
        )
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("semantic indexing test timed out")
        self.indexed = True
        self.index_count = max(self.index_count, len(documents))
        if progress:
            progress(len(documents), len(documents))
        return len(documents)

    def search(
        self,
        _query,
        *,
        limit=50,
        allowed_note_ids=None,
        cancel_check=None,
    ):
        del limit, allowed_note_ids
        if cancel_check is not None:
            cancel_check()
        self.search_started.set()
        if self.block_search and not self.release_search.wait(timeout=5):
            raise RuntimeError("semantic search test timed out")
        if cancel_check is not None:
            cancel_check()
        return ()


class _FakeTimer:
    def __init__(self) -> None:
        self.starts: list[int] = []
        self.stop_count = 0

    def start(self, delay_ms: int) -> None:
        self.starts.append(int(delay_ms))

    def stop(self) -> None:
        self.stop_count += 1


class _AutostartBackend:
    def __init__(
        self,
        status,
        *,
        token: int = 41,
        config: dict | None = None,
    ) -> None:
        self.status = status
        self.config = {
            "semantic_enabled": True,
            "auto_semantic_index": True,
            "semantic_autostart_delay_ms": 20_000,
            **(config or {}),
        }
        self._context = types.SimpleNamespace(token=token)
        self._state_lock = threading.RLock()
        self._maintenance_running = False
        self.index_calls = 0
        self.deactivated = False

    @property
    def active(self) -> bool:
        return self._context is not None

    def _read_config(self):
        return dict(self.config)

    def get_status(self):
        return self.status

    def index_semantic(self, **_callbacks):
        self.index_calls += 1
        return lambda: None

    def activate_profile(self, *, auto_rebuild=True):
        del auto_rebuild
        next_token = 1 if self._context is None else self._context.token + 1
        self._context = types.SimpleNamespace(token=next_token)
        return self.status

    def deactivate_profile(self) -> None:
        self.deactivated = True
        self._context = None


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.bundle = Path(self.temporary.name)
        self.backend = _SynchronousBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend.activate_profile(auto_rebuild=False)

    def tearDown(self) -> None:
        self.backend.deactivate_profile()
        self.temporary.cleanup()

    def test_profile_scoped_index_and_settings_round_trip(self) -> None:
        expected = controller.profile_key(
            "Step 1 Profile", "/tmp/controller-tests/collection.anki2"
        )
        self.assertEqual(self.backend.profile_id, expected)
        self.assertTrue(
            (
                self.bundle
                / "user_files"
                / "profiles"
                / expected
                / "search.sqlite3"
            ).is_file()
        )

        settings = self.backend.load_settings()
        self.assertEqual(settings.result_limit, 70)
        self.assertTrue(settings.preview_enabled)
        settings.mode = contracts.SearchMode.EXACT
        settings.preview_enabled = False
        settings.width = 1234
        self.backend.save_settings(settings)
        saved = self.backend.mw.addonManager.config
        self.assertEqual(saved["shortcut"], "Meta+K")
        self.assertEqual(saved["ui"]["mode"], "exact")
        self.assertFalse(saved["preview_enabled"])
        self.assertFalse(saved["ui"]["preview_enabled"])
        self.assertEqual(saved["ui"]["width"], 1234)

    def test_async_profile_activation_opens_indexes_off_caller_thread(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        caller_thread = threading.get_ident()
        open_threads = []
        ready = threading.Event()
        original = backend._open_profile_context

        def tracked_open(*args, **kwargs):
            open_threads.append(threading.get_ident())
            return original(*args, **kwargs)

        with patch.object(backend, "_open_profile_context", side_effect=tracked_open):
            status = backend.activate_profile_async(
                auto_rebuild=False,
                on_ready=lambda _status: ready.set(),
                on_error=self.fail,
            )
            self.assertIs(status.state, contracts.IndexState.BUILDING)
            self.assertTrue(ready.wait(timeout=2))

        self.assertTrue(backend.active)
        self.assertEqual(len(open_threads), 1)
        self.assertNotEqual(open_threads[0], caller_thread)

    def test_profile_deactivation_does_not_wait_for_inflight_reader_lock(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        backend._search_lock.acquire()
        try:
            started = __import__("time").perf_counter()
            backend.deactivate_profile()
            elapsed = __import__("time").perf_counter() - started
            self.assertLess(elapsed, 0.1)
            self.assertFalse(backend.active)
        finally:
            backend._search_lock.release()
        for worker in tuple(backend.threads):
            worker.join(timeout=2)

    def test_corrupt_semantic_index_is_preserved_and_recreated(self) -> None:
        expected = controller.profile_key(
            "Step 1 Profile", "/tmp/controller-tests/collection.anki2"
        )
        vector_root = (
            self.bundle / "user_files" / "profiles" / expected / "vectors"
        )
        self.backend.deactivate_profile()
        (vector_root / "semantic.sqlite3").write_bytes(b"not a sqlite database")

        status = self.backend.activate_profile(auto_rebuild=False)

        context = self.backend._context
        assert context
        self.assertIsNotNone(context.semantic)
        self.assertIsNot(status.semantic.state, contracts.SemanticState.ERROR)
        archived = tuple(vector_root.parent.glob("vectors.invalid-*"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(
            (archived[0] / "semantic.sqlite3").read_bytes(),
            b"not a sqlite database",
        )
        self.assertTrue((vector_root / "semantic.sqlite3").is_file())

    def test_profile_reopen_requires_matching_semantic_source_generation(self) -> None:
        self.backend.deactivate_profile()
        key = controller.profile_key(
            "Step 1 Profile", "/tmp/controller-tests/collection.anki2"
        )
        index_path = (
            self.bundle
            / "user_files"
            / "profiles"
            / key
            / "search.sqlite3"
        )
        lexical = controller.SmartSearchIndex(index_path)
        lexical.rebuild(
            [
                models.IndexedNote(
                    note_id=1001,
                    fields={"Text": "Bupropion treats depression."},
                    guid="semantic-reopen-test",
                )
            ]
        )
        generation = lexical.generation
        lexical.close()

        semantic_index = types.SimpleNamespace(source_generation=None)
        semantic = types.SimpleNamespace(
            index=semantic_index,
            status=lambda: types.SimpleNamespace(
                supported=True,
                runtime_ready=True,
                model_ready=True,
                index_count=1,
                error=None,
                ready=True,
            ),
        )
        with patch.object(controller, "SemanticService", return_value=semantic):
            self.backend.activate_profile(auto_rebuild=False)

        context = self.backend._context
        assert context
        self.assertEqual(context.lexical_generation, generation)
        self.assertTrue(context.semantic_needs_reconcile)
        self.assertIsNone(context.engine.semantic_provider)
        self.assertIs(
            self.backend.get_status().semantic.state,
            contracts.SemanticState.MODEL_READY,
        )

        self.backend.deactivate_profile()
        semantic_index.source_generation = generation
        with patch.object(controller, "SemanticService", return_value=semantic):
            self.backend.activate_profile(auto_rebuild=False)
        context = self.backend._context
        assert context
        self.assertFalse(context.semantic_needs_reconcile)
        self.assertIs(context.engine.semantic_provider, semantic)

    def test_smart_request_recovers_typo_and_explains_it(self) -> None:
        context = self.backend._context
        assert context
        context.index.rebuild(
            [
                models.IndexedNote(
                    note_id=1001,
                    fields={
                        "Text": "{{c1::Bupropion}} treats depression.",
                        "Extra": "Brand name Wellbutrin.",
                    },
                    tags=("Psychiatry",),
                    decks=("AnKing",),
                    note_type="Cloze",
                    card_ids=(2001,),
                    guid="controller-test",
                )
            ]
        )
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        self.backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )

        received = []
        errors = []
        request = contracts.SearchRequest(
            request_id=7,
            query="BUPROPRION",
            mode=contracts.SearchMode.SMART,
            limit=20,
        )
        self.backend.submit_search(request, received.append, errors.append)

        self.assertFalse(errors)
        self.assertEqual(received[0].request_id, 7)
        self.assertEqual(received[0].results[0].note_id, 1001)
        self.assertEqual(received[0].corrections[0].replacement, "bupropion")
        self.assertIn("Spelling correction", received[0].results[0].match_reasons)
        self.assertTrue(received[0].results[0].spans)

    def test_unfinished_filter_searches_completed_terms_without_error(self) -> None:
        context = self.backend._context
        assert context
        context.index.rebuild(
            [
                models.IndexedNote(
                    note_id=1001,
                    fields={"Text": "Bupropion treats depression."},
                    card_ids=(2001,),
                    guid="incomplete-filter-test",
                )
            ]
        )
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        self.backend._set_index_state(
            contracts.IndexState.READY,
            detail="1 note indexed.",
        )

        received = []
        errors = []
        self.backend.submit_search(
            contracts.SearchRequest(
                request_id=70,
                query="BUPROPRION is:",
                mode=contracts.SearchMode.SMART,
            ),
            received.append,
            errors.append,
        )

        self.assertEqual(errors, [])
        self.assertEqual(received[0].query, "BUPROPRION is:")
        self.assertEqual(received[0].results[0].note_id, 1001)
        self.assertTrue(
            any("unfinished filter" in warning for warning in received[0].warnings)
        )
        self.assertNotIn("is:", self.backend.mw.col.queries)

    def test_only_unfinished_filter_returns_nonfatal_empty_response(self) -> None:
        received = []
        errors = []

        self.backend.submit_search(
            contracts.SearchRequest(
                request_id=71,
                query="is:",
                mode=contracts.SearchMode.EXACT,
            ),
            received.append,
            errors.append,
        )

        self.assertEqual(errors, [])
        self.assertEqual(received[0].query, "is:")
        self.assertEqual(received[0].results, ())
        self.assertTrue(received[0].warnings)
        self.assertEqual(self.backend.mw.col.queries, [])

    def test_smart_results_include_live_flag_and_suspension_state(self) -> None:
        context = self.backend._context
        assert context
        context.index.rebuild(
            [
                models.IndexedNote(
                    note_id=1001,
                    fields={"Text": "Bupropion treats depression."},
                    card_ids=(1901,),  # stale ID; live siblings are authoritative
                    guid="live-card-state-test",
                )
            ]
        )
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        self.backend._set_index_state(
            contracts.IndexState.READY,
            detail="1 note indexed.",
        )
        self.backend.mw.col.cards_by_note[1001] = (2001, 2002)
        self.backend.mw.col.cards[2001] = types.SimpleNamespace(
            queue=-1,
            user_flag=lambda: 4,
        )
        self.backend.mw.col.cards[2002] = types.SimpleNamespace(
            queue=2,
            user_flag=lambda: 0,
        )

        received = []
        self.backend.submit_search(
            contracts.SearchRequest(
                request_id=71,
                query="bupropion",
                mode=contracts.SearchMode.SMART,
            ),
            received.append,
            self.fail,
        )

        result = received[0].results[0]
        self.assertEqual(result.card_ids, (2001, 2002))
        self.assertEqual(result.sibling_count, 2)
        self.assertEqual(
            [
                (state.card_id, state.flag, state.suspended)
                for state in result.card_states
            ],
            [(2001, 4, True), (2002, 0, False)],
        )

    def test_smart_filter_keeps_only_matching_sibling_through_refresh(self) -> None:
        context = self.backend._context
        assert context
        context.index.rebuild(
            [
                models.IndexedNote(
                    note_id=1001,
                    fields={"Text": "Bupropion treats depression."},
                    card_ids=(2001, 2002),
                    guid="card-scoped-smart-test",
                )
            ]
        )
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        self.backend._set_index_state(
            contracts.IndexState.READY,
            detail="1 note indexed.",
        )
        collection = self.backend.mw.col
        collection.cards_by_note[1001] = (2001, 2002)
        collection.cards[2001] = types.SimpleNamespace(
            nid=1001,
            did=1,
            queue=-1,
            user_flag=lambda: 1,
        )
        collection.cards[2002] = types.SimpleNamespace(
            nid=1001,
            did=2,
            queue=2,
            user_flag=lambda: 0,
        )
        collection.card_query_results["is:suspended"] = (2001,)

        received = []
        self.backend.submit_search(
            contracts.SearchRequest(
                request_id=72,
                query="is:suspended bupropion",
                mode=contracts.SearchMode.SMART,
            ),
            received.append,
            self.fail,
        )

        result = received[0].results[0]
        self.assertEqual(collection.queries, ["is:suspended"])
        self.assertEqual(result.card_ids, (2001,))
        self.assertEqual(result.sibling_count, 1)
        self.assertEqual(
            [(state.card_id, state.flag, state.suspended) for state in result.card_states],
            [(2001, 1, True)],
        )
        self.assertEqual(
            tuple(chip.token for chip in received[0].active_filters),
            ("is:suspended",),
        )

        refreshed = []
        self.backend.refresh_card_states((result,), refreshed.extend, self.fail)
        self.assertEqual(refreshed[0].card_ids, (2001,))
        self.assertEqual(
            tuple(state.card_id for state in refreshed[0].card_states),
            (2001,),
        )

    def test_exact_filter_returns_and_opens_only_matching_sibling(self) -> None:
        collection = self.backend.mw.col
        collection.notes[1001] = types.SimpleNamespace(
            id=1001,
            tags=[],
            mod=0,
            guid="card-scoped-exact-test",
            items=lambda: (("Text", "Bupropion treats depression."),),
            note_type=lambda: {"name": "Cloze"},
        )
        collection.cards_by_note[1001] = (2001, 2002)
        collection.cards[2001] = types.SimpleNamespace(
            nid=1001,
            did=1,
            queue=-1,
            user_flag=lambda: 4,
        )
        collection.cards[2002] = types.SimpleNamespace(
            nid=1001,
            did=2,
            queue=2,
            user_flag=lambda: 0,
        )
        collection.card_query_results["is:suspended"] = (2001,)

        received = []
        self.backend.submit_search(
            contracts.SearchRequest(
                request_id=73,
                query="is:suspended",
                mode=contracts.SearchMode.EXACT,
            ),
            received.append,
            self.fail,
        )

        result = received[0].results[0]
        self.assertEqual(result.card_ids, (2001,))
        self.assertEqual(result.browser_query, "cid:2001")
        self.assertEqual(result.sibling_count, 1)
        with patch.object(controller, "open_card_ids_in_browser") as open_cards:
            controller._open_results_in_browser((result,))
        open_cards.assert_called_once_with((2001,))

    def test_notetype_alias_is_compiled_to_native_anki_filter(self) -> None:
        self.assertEqual(
            controller._canonical_query('notetype:"AnKing Cloze" BUPROPION'),
            'note:"AnKing Cloze" BUPROPION',
        )

    def test_exact_mode_is_native_and_has_no_fallback_warning(self) -> None:
        received = []
        request = contracts.SearchRequest(
            request_id=8,
            query='notetype:"AnKing Cloze" BUPROPION',
            mode=contracts.SearchMode.EXACT,
        )
        self.backend.submit_search(request, received.append, self.fail)
        self.assertEqual(
            self.backend.mw.col.queries,
            ['note:"AnKing Cloze" BUPROPION'],
        )
        self.assertEqual(received[0].warnings, ())

    def test_mixed_boolean_smart_query_preserves_native_semantics(self) -> None:
        self.backend._set_index_state(
            contracts.IndexState.READY, detail="Index ready for parser test."
        )
        received = []
        request = contracts.SearchRequest(
            request_id=9,
            query="deck:AnKing OR bupropion",
            mode=contracts.SearchMode.SMART,
        )
        self.backend.submit_search(request, received.append, self.fail)
        self.assertEqual(
            self.backend.mw.col.queries,
            ["deck:AnKing OR bupropion"],
        )
        self.assertIn("Mixed Boolean", received[0].warnings[0])

    def test_active_filter_chips_come_from_authoritative_backend_parse(self) -> None:
        parsed = controller.QueryParser(field_names=("Front",)).parse(
            "-tag:cardio has-cd:v Front:"
        )
        chips = controller._active_filter_chips(parsed)

        self.assertEqual(
            tuple(chip.token for chip in chips),
            ("-tag:cardio", "has-cd:v", "Front:"),
        )
        self.assertEqual(
            controller._active_filter_chips(
                controller.QueryParser().parse("https://example.test")
            ),
            (),
        )
        grouped = controller.QueryParser().parse("(deck:A OR deck:B)")
        self.assertEqual(controller._active_filter_chips(grouped), ())

    def test_notetype_alias_chip_preserves_original_removable_token(self) -> None:
        query = 'notetype:"AnKing Cloze" BUPROPION'
        parsed = controller.QueryParser().parse(
            controller._canonical_query(query)
        )

        chips = controller._active_filter_chips(
            parsed,
            original_query=query,
        )

        self.assertEqual(tuple(chip.token for chip in chips), ('notetype:"AnKing Cloze"',))
        self.assertEqual(
            contracts.remove_filter_token(query, chips[0]),
            "BUPROPION",
        )

    def test_collection_fingerprint_ignores_review_modifications(self) -> None:
        class DB:
            card_query = ""

            def first(self, sql):
                if "FROM notes" in sql:
                    return (1, 10, 10, 20, 2, 40)
                self.card_query = sql
                return (2, 30, 0)

        class Names:
            @staticmethod
            def all_names_and_ids(include_filtered=True):
                del include_filtered
                return (types.SimpleNamespace(id=30, name="Deck"),)

        class Models:
            @staticmethod
            def all():
                return ({"id": 20, "name": "Cloze", "flds": [{"name": "Text"}]},)

        collection = types.SimpleNamespace(db=DB(), decks=Names(), models=Models())
        first = controller._collection_fingerprint(collection)
        # The aggregate intentionally excludes card.mod/due/interval, so
        # ordinary reviews do not trigger a 40k-note startup reconciliation.
        self.assertNotIn("mod", collection.db.card_query.casefold())
        second = controller._collection_fingerprint(collection)
        self.assertEqual(first, second)

    def test_lexical_and_exact_search_continue_during_semantic_indexing(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            tags=("Psychiatry",),
            decks=("AnKing",),
            note_type="Cloze",
            card_ids=(2001,),
            guid="concurrent-semantic-test",
        )
        context.index.rebuild([note])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService()
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        context.semantic_needs_reconcile = True
        backend.reader.snapshot = lambda _collection: [note]
        backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )

        semantic_finished = threading.Event()
        backend.index_semantic(
            on_success=lambda _status: semantic_finished.set(),
            on_error=self.fail,
        )
        self.assertTrue(semantic.started.wait(timeout=1))

        smart_finished = threading.Event()
        smart_responses = []
        backend.submit_search(
            contracts.SearchRequest(
                request_id=10,
                query="bupropion",
                mode=contracts.SearchMode.SMART,
            ),
            lambda response: (smart_responses.append(response), smart_finished.set()),
            self.fail,
        )
        self.assertTrue(
            smart_finished.wait(timeout=1),
            "Smart search waited for the semantic indexing worker",
        )
        self.assertEqual(smart_responses[0].results[0].note_id, 1001)

        exact_finished = threading.Event()
        backend.submit_search(
            contracts.SearchRequest(
                request_id=11,
                query="bupropion",
                mode=contracts.SearchMode.EXACT,
            ),
            lambda _response: exact_finished.set(),
            self.fail,
        )
        self.assertTrue(
            exact_finished.wait(timeout=1),
            "Exact search waited for the semantic indexing worker",
        )

        semantic.release.set()
        self.assertTrue(semantic_finished.wait(timeout=2))
        self.assertIs(context.engine.semantic_provider, semantic)

    def test_switching_to_smart_does_not_wait_for_semantic_search(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="semantic-tab-switch-test",
        )
        context.index.rebuild([note])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, block_search=True)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        context.semantic_needs_reconcile = False
        backend._refresh_semantic_snapshot(context)
        backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )

        semantic_responses = []
        errors = []
        cancel_semantic = backend.submit_search(
            contracts.SearchRequest(
                request_id=20,
                query="antidepressant without sexual side effects",
                mode=contracts.SearchMode.SEMANTIC,
            ),
            semantic_responses.append,
            errors.append,
        )
        self.assertTrue(semantic.search_started.wait(timeout=1))

        # This mirrors changing tabs while a cold/slow semantic query is still
        # running. Cancelling it is best effort; Smart must remain responsive
        # even until the native model call returns.
        cancel_semantic()
        smart_finished = threading.Event()
        smart_responses = []
        backend.submit_search(
            contracts.SearchRequest(
                request_id=21,
                query="bupropion",
                mode=contracts.SearchMode.SMART,
            ),
            lambda response: (smart_responses.append(response), smart_finished.set()),
            errors.append,
        )
        self.assertTrue(
            smart_finished.wait(timeout=1),
            "Smart search waited behind an in-flight semantic query",
        )
        self.assertEqual(smart_responses[0].results[0].note_id, 1001)

        semantic.release_search.set()
        self.assertTrue(
            _wait_until(
                lambda: not any(thread.is_alive() for thread in backend.threads[-2:])
            )
        )
        self.assertEqual(semantic_responses, [])
        self.assertEqual(errors, [])

    def test_status_reads_cached_semantic_metadata_only(self) -> None:
        context = self.backend._context
        assert context
        semantic = _BlockingSemanticService(indexed=True)
        context.note_count = 1
        context.lexical_generation = 1
        semantic.source_generation = 1
        context.semantic = semantic
        context.semantic_needs_reconcile = False
        self.backend._refresh_semantic_snapshot(context)

        def unexpected_status_read():
            raise AssertionError("GUI status opened semantic storage")

        semantic.status = unexpected_status_read
        status = self.backend.get_status()

        self.assertIs(status.semantic.state, contracts.SemanticState.READY)

    def test_semantic_delta_is_reported_as_active_indexing(self) -> None:
        context = self.backend._context
        assert context
        semantic = _BlockingSemanticService(indexed=True)
        context.semantic = semantic
        context.note_count = 1
        context.lexical_generation = 1
        semantic.source_generation = 1
        self.backend._refresh_semantic_snapshot(context)
        with self.backend._state_lock:
            self.backend._semantic_phase = "updating"
            self.backend._semantic_phase_detail = "Updating semantic search."
            self.backend._semantic_progress = 0.5

        status = self.backend.get_status()

        self.assertIs(status.semantic.state, contracts.SemanticState.INDEXING)
        self.assertEqual(status.semantic.progress, 0.5)
        self.assertEqual(status.semantic.detail, "Updating semantic search.")

    def test_search_parsing_reads_cached_field_names_only(self) -> None:
        context = self.backend._context
        assert context
        context.field_names = ("Clinical Field",)
        self.backend._set_index_state(
            contracts.IndexState.READY, detail="Cached parser metadata ready."
        )

        with (
            patch.object(
                context.index,
                "field_names",
                side_effect=AssertionError("GUI search opened lexical storage"),
            ),
            patch.object(self.backend, "_start_external_search") as start,
        ):
            self.backend.submit_search(
                contracts.SearchRequest(
                    request_id=22,
                    query="bupropion",
                    mode=contracts.SearchMode.SMART,
                ),
                self.fail,
                self.fail,
            )

        start.assert_called_once()

    def test_semantic_readiness_is_resolved_inside_reader_lock(self) -> None:
        context = self.backend._context
        assert context
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="semantic-readiness-lock-test",
        )
        context.index.rebuild([note])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.field_names = context.index.field_names()
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, index_count=1)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        context.semantic_needs_reconcile = False
        self.backend._refresh_semantic_snapshot(context)
        self.backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )
        original_status = self.backend._semantic_status
        lock_observations = []

        def observed_status(candidate):
            lock_observations.append(self.backend._search_lock.locked())
            return original_status(candidate)

        responses = []
        with patch.object(self.backend, "_semantic_status", side_effect=observed_status):
            self.backend.submit_search(
                contracts.SearchRequest(
                    request_id=23,
                    query="atypical antidepressant",
                    mode=contracts.SearchMode.SEMANTIC,
                ),
                responses.append,
                self.fail,
            )

        self.assertEqual(len(responses), 1)
        self.assertIn(
            True,
            lock_observations,
            "Semantic provider readiness was checked before joining the reader lock",
        )

    def test_semantic_index_refuses_during_text_index_maintenance(self) -> None:
        context = self.backend._context
        assert context
        semantic = _BlockingSemanticService()
        context.semantic = semantic
        self.backend._set_index_state(
            contracts.IndexState.BUILDING,
            detail="Building the Smart & Exact text index.",
        )
        self.backend._maintenance_running = True
        errors = []

        cancel = self.backend.index_semantic(on_error=errors.append)

        self.assertIsNone(cancel)
        self.assertEqual(len(errors), 1)
        self.assertIn("Smart & Exact setup", errors[0])
        self.assertFalse(semantic.started.is_set())

    def test_reconcile_refreshes_semantic_without_blocking_smart_search(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        original = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="reconcile-original",
        )
        updated = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Sertraline treats depression."},
            guid="reconcile-original",
        )
        context.index.rebuild([original])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True)
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic
        backend.reader.snapshot = lambda _collection: [updated]
        backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )

        reconcile_finished = threading.Event()
        event = backend._new_cancellation()
        backend._begin_maintenance()
        backend._reconcile_snapshot(
            context,
            "updated-fingerprint",
            [updated],
            event,
            lambda _status: reconcile_finished.set(),
            self.fail,
        )
        self.assertTrue(semantic.started.wait(timeout=1))
        self.assertTrue(reconcile_finished.is_set())

        search_finished = threading.Event()
        responses = []
        backend.submit_search(
            contracts.SearchRequest(
                request_id=12,
                query="sertraline",
                mode=contracts.SearchMode.SMART,
            ),
            lambda response: (responses.append(response), search_finished.set()),
            self.fail,
        )
        self.assertTrue(
            search_finished.wait(timeout=1),
            "Smart search waited for reconcile's semantic refresh",
        )
        self.assertEqual(responses[0].results[0].note_id, 1001)

        semantic.release.set()
        self.assertTrue(
            _wait_until(lambda: context.engine.semantic_provider is semantic)
        )

    def test_targeted_reconcile_reads_and_embeds_only_requested_note(self) -> None:
        context = self.backend._context
        assert context
        original = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="targeted-reconcile",
        )
        updated = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Sertraline treats depression."},
            guid="targeted-reconcile",
        )
        unrelated = models.IndexedNote(
            note_id=2002,
            fields={"Text": "Unrelated note."},
            guid="targeted-unrelated",
        )
        context.index.rebuild((original, unrelated))
        context.note_count = 2
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        context.engine.warmup()
        semantic = _BlockingSemanticService(indexed=True, index_count=2)
        semantic.release.set()
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic
        self.backend._set_index_state(
            contracts.IndexState.READY, detail="2 notes ready."
        )

        targeted_calls = []
        self.backend.reader.snapshot = lambda _collection: self.fail(
            "targeted reconcile called the full collection snapshot"
        )
        self.backend.reader.snapshot_note_ids = (
            lambda _collection, note_ids: (
                targeted_calls.append(tuple(note_ids)),
                [updated],
            )[1]
        )
        completed = []
        with patch.object(
            controller.SearchEngine,
            "warmup",
            side_effect=AssertionError(
                "targeted reconcile rebuilt the full fuzzy vocabulary"
            ),
        ):
            started = self.backend.reconcile_note_ids(
                (1001,),
                on_success=completed.append,
                on_error=self.fail,
            )

        self.assertTrue(started)
        self.assertEqual(targeted_calls, [(1001,)])
        self.assertEqual(len(completed), 1)
        self.assertIn(
            "Sertraline",
            context.index.get_documents((1001,))[1001].plain_text,
        )
        self.assertIn(
            "Unrelated",
            context.index.get_documents((2002,))[2002].plain_text,
        )
        self.assertEqual(semantic.index_calls, 1)
        self.assertEqual(semantic.indexed_note_ids, [(1001,)])
        self.assertIs(context.engine.semantic_provider, semantic)

    def test_targeted_reconcile_removes_a_deleted_note_without_full_snapshot(self) -> None:
        context = self.backend._context
        assert context
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Delete this note."},
            guid="targeted-delete",
        )
        context.index.rebuild((note,))
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True)
        semantic.release.set()
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic
        self.backend.reader.snapshot = lambda _collection: self.fail(
            "targeted delete called the full collection snapshot"
        )
        self.backend.reader.snapshot_note_ids = (
            lambda _collection, _note_ids: []
        )
        self.assertTrue(
            self.backend.reconcile_note_ids((1001,), on_error=self.fail)
        )

        self.assertEqual(context.index.count_documents(), 0)
        self.assertEqual(context.note_count, 0)
        self.assertEqual(semantic.removed_note_ids, [(1001,)])
        self.assertEqual(semantic.indexed_note_ids, [()])

    def test_deferred_vocabulary_refresh_builds_off_caller_and_swaps_atomically(
        self,
    ) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="deferred-vocabulary",
        )
        context.index.rebuild((note,))
        context.lexical_generation = context.index.generation
        old_engine = controller.SearchEngine(context.index)
        old_engine.warmup()
        provider = object()
        old_engine.semantic_provider = provider
        context.engine = old_engine

        caller_thread = threading.get_ident()
        warm_threads = []
        finished = threading.Event()
        original_warmup = controller.SearchEngine.warmup

        def tracked_warmup(engine):
            warm_threads.append(threading.get_ident())
            return original_warmup(engine)

        with patch.object(
            controller.SearchEngine,
            "warmup",
            autospec=True,
            side_effect=tracked_warmup,
        ):
            self.assertTrue(
                backend.refresh_vocabulary(
                    on_success=lambda current: (
                        self.assertTrue(current),
                        finished.set(),
                    ),
                    on_error=self.fail,
                )
            )
            self.assertTrue(finished.wait(timeout=2))

        self.assertEqual(len(warm_threads), 1)
        self.assertNotEqual(warm_threads[0], caller_thread)
        self.assertIsNot(context.engine, old_engine)
        self.assertIs(context.engine.semantic_provider, provider)

    def test_rebuild_refreshes_semantic_after_text_index_is_ready(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        original = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="rebuild-original",
        )
        rebuilt = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Venlafaxine treats depression."},
            guid="rebuild-original",
        )
        context.index.rebuild([original])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True)
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic
        backend.reader.snapshot = lambda _collection: [rebuilt]
        backend._set_index_state(
            contracts.IndexState.BUILDING, detail="Rebuilding."
        )

        rebuild_finished = threading.Event()
        event = backend._new_cancellation()
        backend._begin_maintenance()
        backend._build_snapshot_in_background(
            context,
            [rebuilt],
            "rebuilt-fingerprint",
            event,
            1,
            lambda *_args: None,
            lambda _status: rebuild_finished.set(),
            self.fail,
        )
        self.assertTrue(semantic.started.wait(timeout=1))
        self.assertTrue(rebuild_finished.is_set())
        self.assertIs(backend.get_status().state, contracts.IndexState.READY)

        search_finished = threading.Event()
        responses = []
        backend.submit_search(
            contracts.SearchRequest(
                request_id=13,
                query="venlafaxine",
                mode=contracts.SearchMode.SMART,
            ),
            lambda response: (responses.append(response), search_finished.set()),
            self.fail,
        )
        self.assertTrue(
            search_finished.wait(timeout=1),
            "Smart search waited for rebuild's semantic refresh",
        )
        self.assertEqual(responses[0].results[0].note_id, 1001)

        semantic.release.set()
        self.assertTrue(
            _wait_until(lambda: context.engine.semantic_provider is semantic)
        )

    def test_semantic_writer_waits_for_an_inflight_semantic_reader(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="semantic-reader-test",
        )
        context.index.rebuild([note])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, block_search=True)
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic
        context.semantic_needs_reconcile = False
        backend.reader.snapshot = lambda _collection: [note]
        backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )

        search_finished = threading.Event()
        backend.submit_search(
            contracts.SearchRequest(
                request_id=14,
                query="bupropion",
                mode=contracts.SearchMode.SEMANTIC,
            ),
            lambda _response: search_finished.set(),
            self.fail,
        )
        self.assertTrue(semantic.search_started.wait(timeout=1))

        semantic_finished = threading.Event()
        backend.index_semantic(
            on_success=lambda _status: semantic_finished.set(),
            on_error=self.fail,
        )
        self.assertFalse(
            semantic.started.wait(timeout=0.1),
            "Semantic writer started while an existing vector reader was active",
        )

        semantic.release_search.set()
        self.assertTrue(search_finished.wait(timeout=1))
        self.assertTrue(semantic.started.wait(timeout=1))
        semantic.release.set()
        self.assertTrue(semantic_finished.wait(timeout=2))

    def test_semantic_document_preparation_runs_in_background_worker(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "<b>Bupropion</b> treats depression."},
            guid="semantic-worker-thread-test",
        )
        context.index.rebuild([note])
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=False)
        semantic.release.set()
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )

        caller_thread = threading.get_ident()
        preparation_threads = []
        finished = threading.Event()
        event = backend._new_cancellation()
        original = controller._semantic_documents

        def tracked_documents(notes, *, cancel_check=None):
            preparation_threads.append(threading.get_ident())
            return original(notes, cancel_check=cancel_check)

        with patch.object(
            controller,
            "_semantic_documents",
            side_effect=tracked_documents,
        ):
            backend._index_semantic_snapshot(
                context,
                [note],
                event,
                context.token,
                context.lexical_generation,
                lambda _fraction, _detail: None,
                lambda _status: finished.set(),
                self.fail,
            )
            self.assertTrue(finished.wait(timeout=2))

        self.assertEqual(len(preparation_threads), 1)
        self.assertNotEqual(preparation_threads[0], caller_thread)

    def test_lexical_reconcile_waits_for_an_inflight_semantic_reader(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        original = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="semantic-reconcile-reader-test",
        )
        updated = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Sertraline treats depression."},
            guid="semantic-reconcile-reader-test",
        )
        context.index.rebuild([original])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.field_names = context.index.field_names()
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, block_search=True)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        context.semantic_needs_reconcile = False
        backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic
        backend.reader.snapshot = lambda _collection: [updated]
        backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )

        search_finished = threading.Event()
        backend.submit_search(
            contracts.SearchRequest(
                request_id=24,
                query="atypical antidepressant",
                mode=contracts.SearchMode.SEMANTIC,
            ),
            lambda _response: search_finished.set(),
            self.fail,
        )
        self.assertTrue(semantic.search_started.wait(timeout=1))

        reconcile_finished = threading.Event()
        event = backend._new_cancellation()
        backend._begin_maintenance()
        backend._reconcile_snapshot(
            context,
            "semantic-reader-reconcile-fingerprint",
            [updated],
            event,
            lambda _status: reconcile_finished.set(),
            self.fail,
        )
        self.assertFalse(
            reconcile_finished.wait(timeout=0.1),
            "Lexical reconcile changed the index during a Semantic read",
        )
        self.assertIn(
            "Bupropion",
            context.index.get_documents((1001,))[1001].plain_text,
        )

        semantic.release_search.set()
        self.assertTrue(search_finished.wait(timeout=1))
        self.assertTrue(reconcile_finished.wait(timeout=1))
        self.assertIn(
            "Sertraline",
            context.index.get_documents((1001,))[1001].plain_text,
        )
        semantic.release.set()
        self.assertTrue(
            _wait_until(lambda: context.engine.semantic_provider is semantic)
        )

    def test_change_during_semantic_pass_queues_a_fresh_pass(self) -> None:
        self.backend.deactivate_profile()
        backend = _ConcurrentExternalBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="semantic-generation-test",
        )
        context.index.rebuild([note])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService()
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        context.semantic_needs_reconcile = True
        current_notes = [note]
        backend.reader.snapshot = lambda _collection: list(current_notes)
        backend._set_index_state(
            contracts.IndexState.READY, detail="1 note indexed."
        )

        backend.index_semantic(on_error=self.fail)
        self.assertTrue(semantic.started.wait(timeout=1))
        # Simulate a lexical reconciliation finishing after this semantic
        # worker captured its snapshot.
        updated = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Sertraline treats depression."},
            guid="semantic-generation-test",
        )
        context.index.upsert_notes([updated])
        context.lexical_generation = context.index.generation
        context.semantic_needs_reconcile = True
        current_notes[:] = [updated]
        semantic.release.set()

        self.assertTrue(
            _wait_until(lambda: semantic.index_calls == 2),
            "A lexical change during embedding did not trigger a fresh pass",
        )
        self.assertTrue(
            _wait_until(lambda: context.engine.semantic_provider is semantic)
        )
        self.assertFalse(context.semantic_needs_reconcile)
        self.assertEqual(
            semantic.source_generation,
            context.index.generation,
        )

    def test_semantic_autostart_waits_for_maintenance_and_runs_once(self) -> None:
        status = contracts.IndexStatus(
            contracts.IndexState.READY,
            semantic=contracts.SemanticStatus(contracts.SemanticState.MODEL_READY),
        )
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        backend = _AutostartBackend(status)
        timer = _FakeTimer()
        addon.backend = backend
        addon._semantic_autostart_timer = timer
        addon._refresh_dialog = lambda: None
        addon._run_on_main = lambda callback: callback()

        addon._schedule_semantic_autostart()
        self.assertEqual(timer.starts, [20_000])

        backend._maintenance_running = True
        addon._run_semantic_autostart()
        self.assertEqual(backend.index_calls, 0)
        self.assertEqual(timer.starts[-1], 2_000)

        backend._maintenance_running = False
        addon._run_semantic_autostart()
        addon._run_semantic_autostart()
        self.assertEqual(backend.index_calls, 1)
        self.assertEqual(
            addon._semantic_autostart_attempted_token,
            backend._context.token,
        )

    def test_inline_preview_lifecycle_is_dialog_scoped_and_reusable(
        self,
    ) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        result = contracts.SearchResult(
            note_id=7,
            card_ids=(71, 72),
            title="Seven",
        )
        replacement = contracts.SearchResult(
            note_id=8,
            card_ids=(81,),
            title="Eight",
        )
        active_states: list[bool] = []
        pane = types.SimpleNamespace(visible=True)
        pane.isVisible = lambda: pane.visible
        dialog = types.SimpleNamespace(
            set_preview_active=active_states.append,
            preview_pane=pane,
            results=types.SimpleNamespace(
                hasFocus=lambda: True,
                current_result=lambda: replacement,
            ),
        )
        addon._dialog = dialog
        addon._ui_controller = types.SimpleNamespace(
            settings=types.SimpleNamespace(preview_enabled=True)
        )

        class _Preview:
            def __init__(self) -> None:
                self.show_count = 0
                self.hide_count = 0
                self.cleanup_count = 0
                self.cleanup_after_save_count = 0
                self.cleanup_callbacks = []
                self.updated = []

            def show(self) -> None:
                self.show_count += 1

            def set_result(self, current, *, force=False) -> None:
                self.updated.append((current, force))

            def hide(self) -> None:
                self.hide_count += 1

            def cleanup(self) -> None:
                self.cleanup_count += 1

            def cleanup_after_save(self, callback) -> None:
                self.cleanup_after_save_count += 1
                self.cleanup_callbacks.append(callback)

        preview_holder = {}

        def create_preview(_mw, **kwargs):
            preview = _Preview()
            preview_holder["preview"] = preview
            return preview

        with patch.object(
            controller,
            "create_inline_result_inspector",
            side_effect=create_preview,
        ) as factory:
            addon._toggle_previewer(result)

        preview = preview_holder["preview"]
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["initial_result"], result)
        self.assertIs(factory.call_args.kwargs["pane"], pane)
        self.assertIs(factory.call_args.kwargs["parent_window"], dialog)
        self.assertEqual(preview.show_count, 1)
        self.assertEqual(active_states, [True])

        addon._preview_selection_changed(replacement)
        addon._refresh_previewer()
        self.assertEqual(
            preview.updated,
            [(replacement, False), (replacement, True)],
        )

        addon._toggle_previewer(None)
        self.assertEqual(preview.hide_count, 1)
        self.assertIs(addon._previewer, preview)
        self.assertFalse(addon._preview_auto_suppressed)
        self.assertEqual(active_states[-1], False)

        pane.visible = False
        with patch.object(addon, "_schedule_auto_previewer") as schedule:
            addon._preview_selection_changed(replacement)
        schedule.assert_called_once_with(replacement)

        addon._toggle_previewer(replacement)
        self.assertEqual(preview.show_count, 2)
        self.assertFalse(addon._preview_auto_suppressed)

        addon._preview_preference_changed(False)
        self.assertEqual(preview.hide_count, 2)
        self.assertEqual(preview.cleanup_count, 0)
        self.assertIs(addon._previewer, preview)

        addon._preview_preference_changed(True)
        self.assertEqual(preview.show_count, 3)

        addon._close_previewer()
        self.assertEqual(preview.cleanup_after_save_count, 1)
        self.assertEqual(preview.cleanup_count, 0)
        self.assertTrue(addon._preview_auto_suppressed)
        preview.cleanup()
        preview.cleanup_callbacks.pop()()
        self.assertEqual(preview.cleanup_count, 1)
        self.assertFalse(addon._preview_auto_suppressed)
        self.assertIsNone(addon._previewer)

    def test_dialog_close_waits_for_inline_editor_save_before_cleanup(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        events: list[str] = []
        saved_callbacks = []

        class _Preview:
            def cleanup_after_save(self, callback) -> None:
                events.append("save")
                saved_callbacks.append(callback)

        dialog = types.SimpleNamespace(
            close=lambda: events.append("close"),
            deleteLater=lambda: events.append("delete"),
            set_managed_close_handler=lambda _handler: None,
        )
        ui_controller = types.SimpleNamespace(
            dispose=lambda: events.append("dispose")
        )
        preview = _Preview()
        addon._dialog = dialog
        addon._ui_controller = ui_controller
        addon._previewer = preview

        addon._close_dialog()
        self.assertEqual(events, ["save"])
        self.assertIs(addon._dialog, dialog)
        self.assertIs(addon._previewer, preview)

        saved_callbacks.pop()()
        self.assertEqual(events, ["save", "close", "dispose", "delete"])
        self.assertIsNone(addon._dialog)
        self.assertIsNone(addon._ui_controller)
        self.assertIsNone(addon._previewer)

    def test_browser_and_collection_actions_wait_for_editor_detach(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        events: list[str] = []
        pending = []

        class _Preview:
            def prepare_for_external_change(self, callback) -> None:
                events.append("save-detach")
                pending.append(callback)

        addon._previewer = _Preview()
        result = contracts.SearchResult(note_id=7, card_ids=(71,))
        with patch.object(
            controller,
            "_open_results_in_browser",
            side_effect=lambda results: events.append(
                f"browser:{len(tuple(results))}"
            ),
        ):
            addon._open_results_in_browser_safely((result,))
            self.assertEqual(events, ["save-detach"])
            pending.pop()()
        self.assertEqual(events, ["save-detach", "browser:1"])

        action = controller.CollectionAction(
            controller.ActionKind.FLAG,
            note_ids=(7,),
            card_ids=(71,),
            flag=2,
        )
        with patch.object(
            addon,
            "_run_collection_action_after_save",
            side_effect=lambda _action: events.append("mutation"),
        ):
            addon._run_collection_action(action)
            self.assertEqual(events[-1], "save-detach")
            pending.pop()()
        self.assertEqual(events[-1], "mutation")

    def test_temporary_collection_close_uses_managed_save_path(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        callbacks = []
        addon._close_dialog_with_callback = callbacks.append

        addon._on_collection_will_temporarily_close(object())

        self.assertEqual(len(callbacks), 1)
        self.assertTrue(callable(callbacks[0]))

    def test_auto_preview_is_queued_only_while_results_are_browsed(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        result = contracts.SearchResult(
            note_id=7,
            card_ids=(71,),
            title="Seven",
        )
        browsing = [False]
        addon._dialog = types.SimpleNamespace(
            results=types.SimpleNamespace(
                hasFocus=lambda: browsing[0],
                current_result=lambda: result,
            ),
            set_preview_active=lambda _active: None,
        )
        addon._ui_controller = types.SimpleNamespace(
            settings=types.SimpleNamespace(preview_enabled=True)
        )
        timer = _FakeTimer()
        addon._preview_open_timer = timer

        addon._preview_selection_changed(result)
        self.assertEqual(timer.starts, [])
        self.assertIsNone(addon._pending_preview_result)

        browsing[0] = True
        addon._preview_selection_changed(result)
        self.assertEqual(timer.starts, [25])
        self.assertEqual(addon._pending_preview_result, result)

        with patch.object(addon, "_toggle_previewer") as toggle:
            addon._open_pending_previewer()
        toggle.assert_called_once_with(result)
        self.assertIsNone(addon._pending_preview_result)

        addon._ui_controller.settings.preview_enabled = False
        addon._preview_preference_changed(False)
        self.assertGreaterEqual(timer.stop_count, 1)
        self.assertIsNone(addon._pending_preview_result)

    def test_close_dialog_disposes_controller_before_deferred_deletion(
        self,
    ) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        events: list[str] = []
        dialog = types.SimpleNamespace(
            close=lambda: events.append("close"),
            deleteLater=lambda: events.append("delete"),
            set_preview_active=lambda _active: None,
        )
        ui_controller = types.SimpleNamespace(
            dispose=lambda: events.append("dispose")
        )
        addon._dialog = dialog
        addon._ui_controller = ui_controller

        addon._close_dialog()

        self.assertEqual(events, ["close", "dispose", "delete"])
        self.assertIsNone(addon._dialog)
        self.assertIsNone(addon._ui_controller)

    def test_semantic_progress_updates_are_coalesced_for_the_gui(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        timer = _FakeTimer()
        main_callbacks = []
        refreshes = []
        addon._dialog_refresh_timer = timer
        addon._run_on_main = main_callbacks.append
        addon._refresh_dialog = lambda: refreshes.append(True)

        for _ in range(3_000):
            addon._queue_dialog_refresh()
        self.assertEqual(len(main_callbacks), 1)

        # The first update may render immediately. A subsequent burst inside
        # the 250 ms window should arm one timer and queue nothing else.
        main_callbacks.pop()()
        self.assertEqual(len(refreshes), 1)
        for _ in range(3_000):
            addon._queue_dialog_refresh()
        self.assertEqual(len(main_callbacks), 1)
        main_callbacks.pop()()
        self.assertEqual(len(timer.starts), 1)
        for _ in range(3_000):
            addon._queue_dialog_refresh()
        self.assertEqual(main_callbacks, [])

        addon._flush_queued_dialog_refresh()
        self.assertEqual(len(refreshes), 2)

    def test_semantic_autostart_does_not_install_missing_assets(self) -> None:
        status = contracts.IndexStatus(
            contracts.IndexState.READY,
            semantic=contracts.SemanticStatus(
                contracts.SemanticState.NOT_INSTALLED
            ),
        )
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        backend = _AutostartBackend(status)
        timer = _FakeTimer()
        addon.backend = backend
        addon._semantic_autostart_timer = timer

        addon._schedule_semantic_autostart()
        addon._run_semantic_autostart()

        self.assertEqual(backend.index_calls, 0)
        self.assertIsNone(addon._semantic_autostart_attempted_token)

    def test_semantic_autostart_respects_config_and_startup_reconcile(self) -> None:
        status = contracts.IndexStatus(
            contracts.IndexState.READY,
            semantic=contracts.SemanticStatus(contracts.SemanticState.MODEL_READY),
        )
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        backend = _AutostartBackend(
            status,
            config={"auto_semantic_index": False},
        )
        timer = _FakeTimer()
        addon.backend = backend
        addon._semantic_autostart_timer = timer

        addon._schedule_semantic_autostart()
        self.assertEqual(timer.starts, [])

        backend.config.update(
            {
                "auto_semantic_index": True,
                "semantic_autostart_delay_ms": 5_000,
                "startup_reconcile_delay_ms": 30_000,
            }
        )
        addon._schedule_semantic_autostart()
        self.assertEqual(timer.starts, [31_000])

    def test_semantic_autostart_resets_and_stops_with_profile_lifecycle(self) -> None:
        status = contracts.IndexStatus(
            contracts.IndexState.READY,
            semantic=contracts.SemanticStatus(contracts.SemanticState.MODEL_READY),
        )
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        backend = _AutostartBackend(status)
        semantic_timer = _FakeTimer()
        reconcile_timer = _FakeTimer()
        addon.backend = backend
        addon._semantic_autostart_timer = semantic_timer
        addon._reconcile_timer = reconcile_timer
        addon._close_dialog = lambda: None
        addon.schedule_reconcile = lambda **_kwargs: None
        addon._semantic_autostart_attempted_token = backend._context.token

        addon._on_profile_open()

        self.assertEqual(semantic_timer.stop_count, 1)
        self.assertEqual(semantic_timer.starts[-1], 20_000)
        self.assertIsNone(addon._semantic_autostart_attempted_token)
        self.assertEqual(
            addon._semantic_autostart_token,
            backend._context.token,
        )

        addon._semantic_autostart_attempted_token = backend._context.token
        addon._on_profile_will_close()
        self.assertEqual(semantic_timer.stop_count, 2)
        self.assertIsNone(addon._semantic_autostart_token)
        self.assertIsNone(addon._semantic_autostart_attempted_token)
        self.assertTrue(backend.deactivated)

    def test_manual_semantic_install_schedules_automatic_index(self) -> None:
        status = contracts.IndexStatus(
            contracts.IndexState.READY,
            semantic=contracts.SemanticStatus(contracts.SemanticState.MODEL_READY),
        )
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        backend = _AutostartBackend(status)
        timer = _FakeTimer()
        messages = []
        addon.backend = backend
        addon._semantic_autostart_timer = timer
        addon._refresh_dialog = lambda: None
        addon._show_message = messages.append
        addon._semantic_autostart_attempted_token = backend._context.token

        addon._semantic_install_complete()

        self.assertEqual(timer.starts[-1], 0)
        self.assertIsNone(addon._semantic_autostart_attempted_token)
        self.assertIn("start automatically", messages[-1])

    def test_successful_text_rebuild_rearms_semantic_autostart(self) -> None:
        status = contracts.IndexStatus(
            contracts.IndexState.READY,
            semantic=contracts.SemanticStatus(contracts.SemanticState.MODEL_READY),
        )
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        backend = _AutostartBackend(status)
        timer = _FakeTimer()
        addon.backend = backend
        addon._semantic_autostart_timer = timer
        addon._semantic_autostart_attempted_token = backend._context.token

        addon._text_index_rebuilt()

        self.assertIsNone(addon._semantic_autostart_attempted_token)
        self.assertEqual(timer.starts[-1], 0)

    def test_only_note_text_or_tag_changes_refresh_search_indexes(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        reconciles: list[bool] = []
        addon.schedule_reconcile = lambda **_kwargs: reconciles.append(True)

        addon._on_operation(
            types.SimpleNamespace(
                card=True,
                note=False,
                deck=False,
                tag=False,
                notetype=False,
                note_text=False,
            ),
            addon,
        )
        self.assertEqual(reconciles, [])

        addon._on_operation(
            types.SimpleNamespace(
                card=False,
                note=False,
                deck=False,
                tag=True,
                notetype=False,
                note_text=False,
            ),
            addon,
        )
        self.assertEqual(reconciles, [True])

    def test_collection_actions_requery_only_filters_they_can_invalidate(
        self,
    ) -> None:
        flag = controller.CollectionAction(
            controller.ActionKind.FLAG,
            card_ids=(1,),
            flag=4,
        )
        suspend = controller.CollectionAction(
            controller.ActionKind.SUSPEND,
            card_ids=(1,),
        )
        remove_tag = controller.CollectionAction(
            controller.ActionKind.REMOVE_TAGS,
            note_ids=(1,),
            tags="review",
        )

        self.assertTrue(
            controller._collection_action_requires_requery(flag, "flag:1")
        )
        self.assertTrue(
            controller._collection_action_requires_requery(
                suspend,
                "-is:suspended bupropion",
            )
        )
        self.assertTrue(
            controller._collection_action_requires_requery(
                suspend,
                "(is:suspended OR deck:AnKing)",
            )
        )
        self.assertTrue(
            controller._collection_action_requires_requery(
                remove_tag,
                "tag:review heart",
            )
        )
        self.assertFalse(
            controller._collection_action_requires_requery(
                flag,
                "deck:AnKing bupropion",
            )
        )
        self.assertFalse(
            controller._collection_action_requires_requery(
                suspend,
                "flag:1 bupropion",
            )
        )

    def test_filtered_action_success_reruns_captured_search_generation(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        applied: list[tuple[tuple[int, ...], bool]] = []
        finished: list[tuple[bool, str]] = []
        busy: list[tuple[bool, str]] = []
        addon._dialog = types.SimpleNamespace(
            query=lambda: "is:suspended bupropion",
            set_batch_action_busy=lambda value, text: busy.append((value, text)),
            apply_card_state_change=lambda card_ids, **change: applied.append(
                (tuple(card_ids), bool(change["suspended"]))
            ),
            finish_batch_action=lambda ok, text: finished.append((ok, text)),
        )
        captured = object()
        reruns: list[object] = []
        addon._ui_controller = types.SimpleNamespace(
            capture_search_generation=lambda: captured,
            rerun_search_if_current=lambda token: reruns.append(token) or True,
        )
        addon._show_message = lambda _message: None
        action = controller.CollectionAction(
            controller.ActionKind.UNSUSPEND,
            note_ids=(11,),
            card_ids=(101,),
        )
        outcome = types.SimpleNamespace(
            changed=1,
            requested=1,
            live=1,
            eligible=1,
            stale=0,
            skipped=0,
            note_count=1,
        )

        def complete(**kwargs):
            kwargs["on_success"](outcome)
            return object()

        with patch.object(controller, "start_collection_action", side_effect=complete):
            addon._run_collection_action(action)

        self.assertEqual(busy, [(True, "Applying change…")])
        self.assertEqual(applied, [((101,), False)])
        self.assertEqual(len(finished), 1)
        self.assertEqual(reruns, [captured])

    def test_tag_filtered_action_uses_the_same_generation_safe_rerun(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        addon._dialog = types.SimpleNamespace(
            query=lambda: "tag:review hypertension",
            set_batch_action_busy=lambda _value, _text: None,
            finish_batch_action=lambda _ok, _text: None,
        )
        captured = object()
        reruns: list[object] = []
        addon._ui_controller = types.SimpleNamespace(
            capture_search_generation=lambda: captured,
            rerun_search_if_current=lambda token: reruns.append(token) or True,
        )
        addon._show_message = lambda _message: None
        action = controller.CollectionAction(
            controller.ActionKind.REMOVE_TAGS,
            note_ids=(11,),
            tags="review",
        )
        outcome = types.SimpleNamespace(
            changed=1,
            requested=1,
            live=1,
            eligible=1,
            stale=0,
            skipped=0,
            note_count=1,
        )

        def complete(**kwargs):
            kwargs["on_success"](outcome)
            return object()

        with patch.object(controller, "start_collection_action", side_effect=complete):
            addon._run_collection_action(action)

        self.assertEqual(reruns, [captured])

    def test_owned_card_operation_does_not_schedule_racing_state_refresh(
        self,
    ) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        refreshes: list[bool] = []
        addon._refresh_visible_card_states = lambda: refreshes.append(True)
        context = self.backend._context
        assert context
        action = controller.CollectionAction(
            controller.ActionKind.FLAG,
            note_ids=(1,),
            card_ids=(10,),
            flag=2,
        )
        changes = types.SimpleNamespace(
            card=True,
            note=False,
            deck=False,
            tag=False,
            notetype=False,
            note_text=False,
        )

        addon._on_operation(
            changes,
            controller._OwnedMutation(context.token, action),
        )
        self.assertEqual(refreshes, [])

        addon._on_operation(changes, object())
        self.assertEqual(refreshes, [True])

    def test_note_flush_hook_routes_a_committed_edit_to_targeted_reconcile(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        requests = []
        addon.schedule_reconcile = lambda **kwargs: requests.append(kwargs)
        note = types.SimpleNamespace(id=431)

        addon._capture_note_flush(note)
        addon._on_operation(
            types.SimpleNamespace(
                card=False,
                note=True,
                deck=False,
                tag=False,
                notetype=False,
                note_text=True,
            ),
            types.SimpleNamespace(nid=431),
        )

        self.assertEqual(requests, [{"note_ids": (431,)}])

    def test_rapid_editor_saves_keep_each_captured_note_with_its_handler(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        requests = []
        addon.schedule_reconcile = lambda **kwargs: requests.append(kwargs)
        changes = types.SimpleNamespace(
            card=False,
            note=True,
            deck=False,
            tag=False,
            notetype=False,
            note_text=True,
        )

        # The collection worker may capture B before the GUI receives A's
        # completion callback. FIFO hints prevent A from draining B.
        addon._capture_note_flush(types.SimpleNamespace(id=101))
        addon._capture_note_flush(types.SimpleNamespace(id=202))
        addon._on_operation(changes, types.SimpleNamespace(nid=101))
        addon._on_operation(changes, types.SimpleNamespace(nid=202))

        self.assertEqual(
            requests,
            [
                {"note_ids": (101,)},
                {"note_ids": (202,)},
            ],
        )

    def test_owned_tag_operation_uses_exact_ids_and_never_requests_full_scan(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        requests = []
        addon.schedule_reconcile = lambda **kwargs: requests.append(kwargs)
        action = controller.CollectionAction(
            controller.ActionKind.ADD_TAGS,
            note_ids=(22, 11),
            tags=("review",),
        )
        context = self.backend._context
        assert context

        addon._on_operation(
            types.SimpleNamespace(
                card=False,
                note=True,
                deck=False,
                tag=True,
                notetype=False,
                note_text=True,
            ),
            controller._OwnedMutation(context.token, action),
        )

        self.assertEqual(
            requests,
            [{"note_ids": (22, 11)}],
        )
        self.assertFalse(any(request.get("full") for request in requests))

    def test_unknown_native_bulk_tag_operation_keeps_safe_full_fallback(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        requests = []
        addon.schedule_reconcile = lambda **kwargs: requests.append(kwargs)

        addon._on_operation(
            types.SimpleNamespace(
                card=False,
                note=False,
                deck=False,
                tag=True,
                notetype=False,
                note_text=False,
            ),
            object(),
        )

        self.assertEqual(requests, [{"full": True}])

    def test_targeted_edit_runs_before_pending_startup_audit(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        timer = _FakeTimer()
        addon._reconcile_timer = timer
        calls = []
        self.backend.reconcile = lambda **kwargs: (
            calls.append(("full", kwargs)),
            True,
        )[1]
        self.backend.reconcile_note_ids = lambda note_ids, **kwargs: (
            calls.append(("targeted", tuple(note_ids), kwargs)),
            True,
        )[1]

        addon.schedule_reconcile(initial_check=True)
        addon.schedule_reconcile(note_ids=(777,))
        addon._run_reconcile()

        self.assertEqual(calls[0][0], "targeted")
        self.assertEqual(calls[0][1], (777,))
        self.assertTrue(addon._reconcile_startup_check)
        self.assertEqual(timer.starts[-1], addon._startup_reconcile_delay_ms)

    def test_multi_browser_open_uses_exact_deduplicated_card_ids(self) -> None:
        results = (
            types.SimpleNamespace(note_id=10, card_ids=(101, 102)),
            types.SimpleNamespace(note_id=11, card_ids=(102, 103)),
        )

        with (
            patch.object(controller, "open_card_ids_in_browser") as open_cards,
            patch.object(controller, "open_note_ids_in_browser") as open_notes,
        ):
            controller._open_results_in_browser(results)

        open_cards.assert_called_once_with((101, 102, 103))
        open_notes.assert_not_called()

    def test_multi_browser_open_falls_back_to_notes_without_card_ids(self) -> None:
        results = (
            types.SimpleNamespace(note_id=10, card_ids=()),
            types.SimpleNamespace(note_id=11, card_ids=()),
        )

        with (
            patch.object(controller, "open_card_ids_in_browser") as open_cards,
            patch.object(controller, "open_note_ids_in_browser") as open_notes,
        ):
            controller._open_results_in_browser(results)

        open_cards.assert_not_called()
        open_notes.assert_called_once_with((10, 11))


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = __import__("time").monotonic() + timeout
    while __import__("time").monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.01)
    return bool(predicate())


if __name__ == "__main__":
    unittest.main()
