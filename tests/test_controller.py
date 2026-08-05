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
updater = importlib.import_module(f"{PACKAGE}.updater")


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
    def _run_query_op(
        self,
        *,
        uses_collection,
        op,
        success,
        failure,
        allow_during_bundle_update=False,
    ):
        del uses_collection, allow_during_bundle_update
        try:
            success(op(self.mw.col))
        except Exception as error:  # exercise the same callback seam as QueryOp
            failure(error)


class _ConcurrentExternalBackend(_SynchronousBackend):
    def __init__(self, *args, **kwargs) -> None:
        self.threads = []
        super().__init__(*args, **kwargs)

    def _run_query_op(
        self,
        *,
        uses_collection,
        op,
        success,
        failure,
        allow_during_bundle_update=False,
    ):
        if uses_collection:
            return super()._run_query_op(
                uses_collection=uses_collection,
                op=op,
                success=success,
                failure=failure,
                allow_during_bundle_update=allow_during_bundle_update,
            )

        def run() -> None:
            try:
                success(op(self.mw.col))
            except Exception as error:
                failure(error)

        worker = threading.Thread(target=run, daemon=True)
        self.threads.append(worker)
        worker.start()


class _DeferredCollectionBackend(_SynchronousBackend):
    def __init__(self, *args, **kwargs) -> None:
        self.pending_query_ops = []
        super().__init__(*args, **kwargs)

    def _run_query_op(
        self,
        *,
        uses_collection,
        op,
        success,
        failure,
        allow_during_bundle_update=False,
    ):
        self.pending_query_ops.append(
            {
                "uses_collection": uses_collection,
                "op": op,
                "success": success,
                "failure": failure,
                "allow_during_bundle_update": allow_during_bundle_update,
            }
        )


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
        self.abort_calls = 0
        self.unload_calls = 0

    def status(self):
        return types.SimpleNamespace(
            supported=True,
            runtime_ready=True,
            model_ready=True,
            index_count=self.index_count,
            error=None,
            ready=True,
        )

    def known_hashes(self, _note_ids=None, *, cancel_check=None):
        if cancel_check is not None:
            cancel_check()
        return {}

    def clear_source_generation(self) -> None:
        self.cleared_generations.append(self.source_generation)
        self.source_generation = None

    def mark_source_generation(self, generation: int) -> None:
        self.source_generation = int(generation)
        self.marked_generations.append(int(generation))

    def remove_notes(self, note_ids, *, cancel_check=None) -> None:
        if cancel_check is not None:
            cancel_check()
        removed = tuple(int(value) for value in note_ids)
        self.removed_note_ids.append(removed)
        self.index_count = max(0, self.index_count - len(removed))
        self.indexed = self.index_count > 0

    def index_documents(
        self,
        documents,
        *,
        progress=None,
        cancel_check=None,
    ) -> int:
        if cancel_check is not None:
            cancel_check()
        self.index_calls += 1
        self.indexed_note_ids.append(
            tuple(int(document.note_id) for document in documents)
        )
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("semantic indexing test timed out")
        if cancel_check is not None:
            cancel_check()
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

    def abort_now(self) -> bool:
        self.abort_calls += 1
        self.release.set()
        self.release_search.set()
        return True

    def unload(self) -> None:
        self.unload_calls += 1


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
        self.bundle_update_running = False
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

    @staticmethod
    def _semantic_restart_pending(context):
        return bool(
            context is not None
            and getattr(context, "semantic_restart_required", False)
        )

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
        self.assertIs(
            settings.preview_default,
            contracts.PreviewDefault.QUESTION,
        )
        settings.mode = contracts.SearchMode.EXACT
        settings.preview_enabled = False
        settings.preview_default = contracts.PreviewDefault.ANSWER
        settings.width = 1234
        self.backend.save_settings(settings)
        saved = self.backend.mw.addonManager.config
        self.assertEqual(saved["shortcut"], "Meta+K")
        self.assertEqual(saved["ui"]["mode"], "exact")
        self.assertFalse(saved["preview_enabled"])
        self.assertFalse(saved["ui"]["preview_enabled"])
        self.assertEqual(saved["preview_default"], "answer")
        self.assertEqual(saved["ui"]["preview_default"], "answer")
        self.assertEqual(saved["ui"]["width"], 1234)

    def test_preview_default_config_is_case_insensitive_and_safely_falls_back(self) -> None:
        self.backend.mw.addonManager.config = {
            "preview_default": "answer",
            "ui": {"preview_default": "EdIt"},
        }
        self.assertIs(
            self.backend.load_settings().preview_default,
            contracts.PreviewDefault.EDIT,
        )

        self.backend.mw.addonManager.config = {
            "preview_default": "not-a-surface",
        }
        self.assertIs(
            self.backend.load_settings().preview_default,
            contracts.PreviewDefault.QUESTION,
        )

        self.backend.mw.addonManager.config = {
            "preview_default": "answer",
            "ui": {"preview_default": "not-a-surface"},
        }
        self.assertIs(
            self.backend.load_settings().preview_default,
            contracts.PreviewDefault.ANSWER,
        )

    def test_load_decks_uses_serialized_collection_query(self) -> None:
        self.backend.mw.col.decks = types.SimpleNamespace(
            all_names_and_ids=lambda *, include_filtered: (
                types.SimpleNamespace(id=1, name="Default"),
                types.SimpleNamespace(id=40, name="Medicine::Cardiology"),
            ),
            get_current_id=lambda: 40,
        )
        received = []
        errors = []

        cancel = self.backend.load_decks(received.append, errors.append)

        self.assertTrue(callable(cancel))
        self.assertEqual(errors, [])
        self.assertEqual(len(received), 1)
        catalog = received[0]
        self.assertEqual(catalog.current_deck_id, 40)
        self.assertEqual(
            tuple(deck.name for deck in catalog.decks),
            ("Default", "Medicine::Cardiology"),
        )

    def test_load_decks_ignores_cancelled_and_stale_callbacks(self) -> None:
        backend = _DeferredCollectionBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        backend.activate_profile(auto_rebuild=False)
        backend.mw.col.decks = types.SimpleNamespace(
            all_names_and_ids=lambda *, include_filtered: (
                types.SimpleNamespace(id=1, name="Default"),
            ),
            get_current_id=lambda: 1,
        )
        received = []
        errors = []

        cancel = backend.load_decks(received.append, errors.append)
        pending = backend.pending_query_ops.pop()
        self.assertTrue(pending["uses_collection"])
        catalog = pending["op"](backend.mw.col)
        cancel()
        pending["success"](catalog)
        self.assertEqual(received, [])
        self.assertEqual(errors, [])

        backend.load_decks(received.append, errors.append)
        stale = backend.pending_query_ops.pop()
        backend.deactivate_profile()
        cleanup = backend.pending_query_ops.pop()
        self.assertFalse(cleanup["uses_collection"])
        cleanup["success"](cleanup["op"](None))
        stale["failure"](RuntimeError("profile closed"))
        self.assertEqual(received, [])
        self.assertEqual(errors, [])

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

    def test_async_profile_activation_cannot_publish_after_reviewer_pause(
        self,
    ) -> None:
        self.backend.deactivate_profile()
        backend = _DeferredCollectionBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        ready = []
        errors = []

        backend.activate_profile_async(
            auto_rebuild=False,
            on_ready=ready.append,
            on_error=errors.append,
        )
        opening = backend.pending_query_ops.pop()
        payload = opening["op"](None)
        backend.set_background_maintenance_paused(True)
        opening["success"](payload)

        self.assertEqual(errors, [])
        self.assertEqual(len(ready), 1)
        self.assertIs(ready[0].state, contracts.IndexState.UNAVAILABLE)
        self.assertFalse(backend.active)
        cleanup = backend.pending_query_ops.pop()
        self.assertFalse(cleanup["uses_collection"])
        cleanup["success"](cleanup["op"](None))

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

    def test_corrupt_maintenance_journal_is_preserved_without_disabling_search(
        self,
    ) -> None:
        context = self.backend._context
        assert context is not None
        context.index.rebuild(
            [
                models.IndexedNote(
                    note_id=55,
                    fields={"Front": "bupropion"},
                    card_ids=(550,),
                )
            ]
        )
        key = context.key
        self.backend.deactivate_profile()
        journal_path = (
            self.bundle
            / "user_files"
            / "profiles"
            / key
            / "maintenance.sqlite3"
        )
        journal_path.write_bytes(b"not a sqlite database")

        status = self.backend.activate_profile(auto_rebuild=False)

        self.assertIs(status.state, contracts.IndexState.READY)
        recovered = self.backend._context
        assert recovered is not None and recovered.maintenance is not None
        self.assertTrue(recovered.maintenance.audit_needed)
        self.assertEqual(
            recovered.maintenance.get_meta("force_canonical_hydration"),
            "1",
        )
        archived = tuple(
            path
            for path in journal_path.parent.glob("maintenance.sqlite3.invalid-*")
            if not path.name.endswith(("-wal", "-shm"))
        )
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), b"not a sqlite database")

    def test_audited_fingerprint_waits_for_every_durable_note_batch(self) -> None:
        context = self.backend._context
        assert context is not None and context.maintenance is not None
        journal = context.maintenance
        context.fingerprint = "published-generation"
        controller._write_index_fingerprint(
            context.index,
            context.fingerprint,
        )
        sequence = journal.enqueue([77], controller.DirtyReason.NOTE_CONTENT)
        assert sequence is not None
        journal.set_meta("pending_collection_fingerprint", "audited-generation")

        self.assertFalse(
            self.backend._promote_pending_fingerprint(context, journal)
        )
        self.assertEqual(context.fingerprint, "published-generation")
        self.assertEqual(
            controller._read_index_fingerprint(context.index),
            "published-generation",
        )

        self.assertTrue(journal.acknowledge(77, sequence))
        self.assertTrue(
            self.backend._promote_pending_fingerprint(context, journal)
        )
        self.assertEqual(context.fingerprint, "audited-generation")
        self.assertEqual(
            controller._read_index_fingerprint(context.index),
            "audited-generation",
        )
        self.assertIsNone(
            journal.get_meta("pending_collection_fingerprint")
        )

    def test_manifest_audit_seeds_matching_index_and_targets_card_and_facet_changes(
        self,
    ) -> None:
        class InventoryDB:
            note_rows = [
                (1, 10, 0, 100, 20, 5, 30, "pulmonary embolism", " tag ")
            ]
            card_rows = [(11, 1, 10, 0)]

            def all(self, sql, *args):
                if "FROM notes" in sql and "WHERE id >" in sql:
                    cursor, limit = int(args[0]), int(args[1])
                    return tuple(
                        row
                        for row in self.note_rows
                        if int(row[0]) > cursor
                    )[:limit]
                if "FROM cards" in sql and "WHERE id >" in sql:
                    cursor, limit = int(args[0]), int(args[1])
                    return tuple(
                        (
                            card_id,
                            note_id,
                            original_deck_id
                            if original_deck_id > 0
                            else deck_id,
                        )
                        for card_id, note_id, deck_id, original_deck_id
                        in self.card_rows
                        if int(card_id) > cursor
                    )[:limit]
                if "SELECT DISTINCT nid FROM cards WHERE odid > 0" in sql:
                    return tuple(
                        (note_id,)
                        for _card_id, note_id, _deck_id, original_deck_id
                        in self.card_rows
                        if int(original_deck_id) > 0
                    )
                raise AssertionError(sql)

            def first(self, sql):
                if "FROM notes" in sql:
                    return (1, 10, 10, 100, 5, 20)
                if "FROM cards" in sql and "CASE WHEN" in sql:
                    canonical = sum(
                        odid if odid > 0 else did
                        for _cid, _nid, did, odid in self.card_rows
                    )
                    return (len(self.card_rows), canonical)
                if "FROM cards" in sql:
                    return (
                        len(self.card_rows),
                        sum(row[2] for row in self.card_rows),
                        sum(row[3] for row in self.card_rows),
                    )
                raise AssertionError(sql)

        class Decks:
            def __init__(self) -> None:
                self.items = [types.SimpleNamespace(id=10, name="Deck A")]

            def all_names_and_ids(self, *, include_filtered):
                self.include_filtered = include_filtered
                return tuple(self.items)

        class Models:
            def __init__(self) -> None:
                self.items = [
                    {"id": 100, "name": "Basic", "flds": [{"name": "Front"}]}
                ]

            def all(self):
                return tuple(self.items)

        collection = types.SimpleNamespace(
            db=InventoryDB(),
            decks=Decks(),
            models=Models(),
        )
        self.backend.mw.col = collection
        context = self.backend._context
        assert context is not None and context.maintenance is not None
        context.index.rebuild(
            [
                models.IndexedNote(
                    note_id=1,
                    fields={"Front": "pulmonary embolism"},
                    tags=("tag",),
                    decks=("Deck A",),
                    note_type="Basic",
                    card_ids=(11,),
                )
            ]
        )
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.fingerprint = controller._collection_fingerprint(collection)
        stable_fingerprint = context.fingerprint
        legacy_fingerprint = controller._collection_fingerprint(
            collection,
            legacy_card_scope=True,
        )
        collection.db.card_rows = [(11, 1, 999, 10)]
        self.assertEqual(
            controller._collection_fingerprint(collection),
            stable_fingerprint,
        )
        self.assertNotEqual(
            controller._collection_fingerprint(
                collection,
                legacy_card_scope=True,
            ),
            legacy_fingerprint,
        )
        collection.db.card_rows = [(11, 1, 10, 0)]

        context.maintenance.request_audit("initial")
        self.assertTrue(self.backend.audit_manifest(on_error=self.fail))
        self.assertEqual(context.maintenance.pending_count(), 0)
        self.assertEqual(context.maintenance.manifest_count(), 1)
        self.assertEqual(context.maintenance.card_manifest_count(), 1)

        # A recovered/corrupt journal must not trust even a matching cheap
        # fingerprint and existing note ID set. It queues canonical hydration
        # first, then atomically clears the recovery guard with the audit.
        context.maintenance.set_meta("manifest_initialized", "0")
        context.maintenance.set_meta("force_canonical_hydration", "1")
        context.maintenance.request_audit("journal recovery")
        self.assertTrue(self.backend.audit_manifest(on_error=self.fail))
        self.assertEqual(
            tuple(item.note_id for item in context.maintenance.pending()),
            (1,),
        )
        self.assertIsNone(
            context.maintenance.get_meta("force_canonical_hydration")
        )
        recovered_pending = context.maintenance.pending()[0]
        self.assertTrue(
            context.maintenance.acknowledge(
                recovered_pending.note_id,
                recovered_pending.sequence,
            )
        )
        self.assertTrue(
            self.backend._promote_pending_fingerprint(
                context,
                context.maintenance,
            )
        )

        collection.db.card_rows = [(11, 1, 20, 0)]
        collection.decks.items.append(types.SimpleNamespace(id=20, name="Deck B"))
        context.maintenance.request_audit("card move")
        self.assertTrue(self.backend.audit_manifest(on_error=self.fail))
        self.assertEqual(
            tuple(item.note_id for item in context.maintenance.pending()),
            (1,),
        )
        pending = context.maintenance.pending()[0]
        context.maintenance.acknowledge(pending.note_id, pending.sequence)

        collection.decks.items[-1].name = "Renamed Deck"
        context.maintenance.request_audit("deck rename")
        self.assertTrue(self.backend.audit_manifest(on_error=self.fail))
        self.assertEqual(context.maintenance.pending()[0].note_id, 1)
        pending = context.maintenance.pending()[0]
        context.maintenance.acknowledge(pending.note_id, pending.sequence)

        collection.models.items[0]["name"] = "Revised Basic"
        context.maintenance.request_audit("notetype rename")
        self.assertTrue(self.backend.audit_manifest(on_error=self.fail))
        self.assertEqual(context.maintenance.pending()[0].note_id, 1)

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

    def test_related_cards_use_external_lookup_then_live_state_hydration(
        self,
    ) -> None:
        context = self.backend._context
        assert context
        source_tag = "#AK_Step2_v12::#UWorld::Step::19231"
        context.index.rebuild(
            (
                models.IndexedNote(
                    note_id=1001,
                    fields={"Text": "Source question."},
                    tags=(source_tag,),
                    card_ids=(1101,),
                    guid="related-source",
                ),
                models.IndexedNote(
                    note_id=2001,
                    fields={"Text": "Related explanation card."},
                    tags=(source_tag.swapcase(),),
                    # The live collection has gained another sibling since
                    # this disposable text snapshot was written.
                    card_ids=(2101,),
                    guid="related-candidate",
                ),
                models.IndexedNote(
                    note_id=3001,
                    fields={"Text": "Different question."},
                    tags=("#AK_Step2_v12::#UWorld::Step::99999",),
                    card_ids=(3101,),
                    guid="related-unrelated",
                ),
            )
        )
        context.note_count = 3
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        self.backend._set_index_state(
            contracts.IndexState.READY,
            detail="3 notes indexed.",
        )
        self.backend.mw.col.cards_by_note[2001] = (2101, 2102)
        self.backend.mw.col.cards[2101] = types.SimpleNamespace(
            queue=-1,
            user_flag=lambda: 3,
        )
        self.backend.mw.col.cards[2102] = types.SimpleNamespace(
            queue=-2,
            user_flag=lambda: 0,
        )

        operation_lanes = []
        original_run_query_op = self.backend._run_query_op

        def tracked_run_query_op(*, uses_collection, **kwargs):
            operation_lanes.append(bool(uses_collection))
            return original_run_query_op(
                uses_collection=uses_collection,
                **kwargs,
            )

        received = []
        errors = []
        with patch.object(
            self.backend,
            "_run_query_op",
            side_effect=tracked_run_query_op,
        ):
            self.backend.submit_related_cards(
                contracts.RelatedCardsRequest(
                    request_id=81,
                    note_ids=(1001,),
                    tags=(source_tag,),
                    limit=50,
                ),
                received.append,
                errors.append,
            )

        self.assertEqual(errors, [])
        self.assertEqual(operation_lanes, [False, True])
        self.assertEqual(len(received), 1)
        response = received[0]
        self.assertEqual(response.request_id, 81)
        self.assertEqual(response.source_note_ids, (1001,))
        self.assertEqual(response.source_tags, (source_tag,))
        self.assertEqual(response.total_results, 1)
        self.assertFalse(response.truncated)
        self.assertEqual([result.note_id for result in response.results], [2001])
        result = response.results[0]
        self.assertEqual(result.card_ids, (2101, 2102))
        self.assertEqual(result.sibling_count, 2)
        self.assertEqual(result.browser_query, "cid:2101,2102")
        self.assertEqual(
            result.match_reasons,
            ("Related · UWorld · 19231",),
        )
        self.assertEqual(result.related_by_tags, (source_tag,))
        self.assertEqual(
            [
                (state.card_id, state.flag, state.suspended, state.buried)
                for state in result.card_states
            ],
            [(2101, 3, True, False), (2102, 0, False, True)],
        )

    def test_related_cards_without_specific_source_tags_skip_collection(
        self,
    ) -> None:
        context = self.backend._context
        assert context
        context.index.rebuild(
            (
                models.IndexedNote(
                    note_id=1001,
                    fields={"Text": "Broad source tag only."},
                    tags=("#AK_Step2_v12::#UWorld",),
                    card_ids=(1101,),
                    guid="related-broad-source",
                ),
            )
        )
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        self.backend._set_index_state(
            contracts.IndexState.READY,
            detail="1 note indexed.",
        )

        operation_lanes = []
        original_run_query_op = self.backend._run_query_op

        def tracked_run_query_op(*, uses_collection, **kwargs):
            operation_lanes.append(bool(uses_collection))
            return original_run_query_op(
                uses_collection=uses_collection,
                **kwargs,
            )

        received = []
        with patch.object(
            self.backend,
            "_run_query_op",
            side_effect=tracked_run_query_op,
        ):
            self.backend.submit_related_cards(
                contracts.RelatedCardsRequest(
                    request_id=82,
                    note_ids=(1001,),
                    tags=("#AK_Step2_v12::#UWorld",),
                    limit=50,
                ),
                received.append,
                self.fail,
            )

        self.assertEqual(operation_lanes, [False])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].source_tags, ())
        self.assertEqual(received[0].results, ())
        self.assertEqual(received[0].total_results, 0)

    def test_related_hydration_scheduling_failure_delivers_text_results(
        self,
    ) -> None:
        context = self.backend._context
        assert context
        source_tag = "#AK_Step2_v12::#AMBOSS::AbCdEf"
        context.index.rebuild(
            (
                models.IndexedNote(
                    note_id=1001,
                    fields={"Text": "Source."},
                    tags=(source_tag,),
                    card_ids=(1101,),
                    guid="related-hydration-source",
                ),
                models.IndexedNote(
                    note_id=2001,
                    fields={"Text": "Candidate."},
                    tags=(source_tag,),
                    card_ids=(2101,),
                    guid="related-hydration-candidate",
                ),
            )
        )
        context.note_count = 2
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        self.backend._set_index_state(
            contracts.IndexState.READY,
            detail="2 notes indexed.",
        )
        original_run_query_op = self.backend._run_query_op

        def reject_collection_lane(*, uses_collection, **kwargs):
            if uses_collection:
                raise RuntimeError("profile teardown rejected QueryOp")
            return original_run_query_op(
                uses_collection=uses_collection,
                **kwargs,
            )

        received = []
        errors = []
        with patch.object(
            self.backend,
            "_run_query_op",
            side_effect=reject_collection_lane,
        ):
            self.backend.submit_related_cards(
                contracts.RelatedCardsRequest(
                    request_id=83,
                    note_ids=(1001,),
                    tags=(source_tag,),
                ),
                received.append,
                errors.append,
            )

        self.assertEqual(errors, [])
        self.assertEqual(len(received), 1)
        self.assertEqual(
            [result.note_id for result in received[0].results],
            [2001],
        )
        self.assertEqual(received[0].results[0].card_states, ())
        self.assertEqual(self.backend._active_cancellations, set())

    def test_related_lookup_waits_for_the_rebuild_engine_lane(self) -> None:
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
        source_tag = "#AK_Step2_v12::#UWorld::18462"
        context.index.rebuild(
            (
                models.IndexedNote(
                    note_id=1001,
                    fields={"Text": "Source."},
                    tags=(source_tag,),
                    card_ids=(1101,),
                    guid="related-lock-source",
                ),
                models.IndexedNote(
                    note_id=2001,
                    fields={"Text": "Candidate."},
                    tags=(source_tag,),
                    card_ids=(2101,),
                    guid="related-lock-candidate",
                ),
            )
        )
        context.note_count = 2
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        backend._set_index_state(
            contracts.IndexState.READY,
            detail="2 notes indexed.",
        )
        backend.mw.col.cards_by_note[2001] = (2101,)
        backend.mw.col.cards[2101] = types.SimpleNamespace(
            queue=2,
            user_flag=lambda: 0,
        )
        received = []
        errors = []

        backend._engine_lock.acquire()
        try:
            backend.submit_related_cards(
                contracts.RelatedCardsRequest(
                    request_id=84,
                    note_ids=(1001,),
                    tags=(source_tag,),
                ),
                received.append,
                errors.append,
            )
            self.assertTrue(backend.threads)
            self.assertTrue(backend.threads[-1].is_alive())
            self.assertEqual(received, [])
        finally:
            backend._engine_lock.release()

        backend.threads[-1].join(timeout=2)
        self.assertFalse(backend.threads[-1].is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            [result.note_id for result in received[0].results],
            [2001],
        )

    def test_combined_smart_filters_keep_only_matching_sibling(self) -> None:
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
        native_filter = "deck:AnKing tag:Psychiatry is:suspended"
        collection.card_query_results[native_filter] = (2001,)

        unfiltered = []
        self.backend.submit_search(
            contracts.SearchRequest(
                request_id=71,
                query="bupropion",
                mode=contracts.SearchMode.SMART,
            ),
            unfiltered.append,
            self.fail,
        )
        received = []
        self.backend.submit_search(
            contracts.SearchRequest(
                request_id=72,
                query=f"{native_filter} bupropion",
                mode=contracts.SearchMode.SMART,
            ),
            received.append,
            self.fail,
        )

        result = received[0].results[0]
        base_result = unfiltered[0].results[0]
        self.assertEqual(collection.queries, [native_filter])
        self.assertEqual(base_result.card_ids, (2001, 2002))
        self.assertEqual(result.card_ids, (2001,))
        self.assertEqual(result.sibling_count, 1)
        self.assertEqual(result.score, base_result.score)
        self.assertEqual(
            [(state.card_id, state.flag, state.suspended) for state in result.card_states],
            [(2001, 1, True)],
        )
        self.assertEqual(
            tuple(chip.token for chip in received[0].active_filters),
            ("deck:AnKing", "tag:Psychiatry", "is:suspended"),
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

    def test_manifest_seed_verifies_content_and_canonical_deck_scope(self) -> None:
        context = self.backend._context
        assert context is not None
        context.index.rebuild(
            [
                models.IndexedNote(
                    note_id=1,
                    fields={"Front": "bupropion"},
                    tags=("psych",),
                    decks=("Medicine",),
                    note_type="Basic",
                    card_ids=(11,),
                )
            ]
        )
        fields_digest = controller.hashlib.blake2b(
            b"bupropion", digest_size=16
        ).digest()
        tags_digest = controller.hashlib.blake2b(
            b" psych ", digest_size=16
        ).digest()
        note_row = types.SimpleNamespace(
            note_id=1,
            note_type_id=100,
            fields_digest=fields_digest,
            tags_digest=tags_digest,
        )
        card_row = types.SimpleNamespace(
            card_id=11,
            note_id=1,
            home_deck_id=10,
        )
        facets = (
            controller.FacetManifest("deck", 10, "Medicine"),
            controller.FacetManifest(
                "notetype",
                100,
                controller.json.dumps(
                    ("Basic", ("Front",)),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        )

        self.assertEqual(
            controller._manifest_seed_mismatches(
                context.index,
                (note_row,),
                (card_row,),
                facets,
            ),
            (),
        )

        changed_tag = types.SimpleNamespace(
            note_id=1,
            note_type_id=100,
            fields_digest=fields_digest,
            tags_digest=controller.hashlib.blake2b(
                b" other ", digest_size=16
            ).digest(),
        )
        self.assertEqual(
            controller._manifest_seed_mismatches(
                context.index,
                (changed_tag,),
                (card_row,),
                facets,
            ),
            (1,),
        )

        moved_card = types.SimpleNamespace(
            card_id=11,
            note_id=1,
            home_deck_id=20,
        )
        moved_facets = facets + (
            controller.FacetManifest("deck", 20, "Psychiatry"),
        )
        self.assertEqual(
            controller._manifest_seed_mismatches(
                context.index,
                (note_row,),
                (moved_card,),
                moved_facets,
            ),
            (1,),
        )

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
        semantic_activity = []
        backend.set_semantic_activity_callback(
            semantic_activity.append
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
        self.assertEqual(semantic_activity, [True, False])

    def test_review_entry_aborts_semantic_search_silently_and_next_search_works(
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
        assert context is not None
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="review-semantic-cancel-test",
        )
        context.index.rebuild((note,))
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.field_names = context.index.field_names()
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, block_search=True)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        context.semantic_needs_reconcile = False
        backend._refresh_semantic_snapshot(context)
        backend._set_index_state(
            contracts.IndexState.READY,
            detail="1 note indexed.",
        )
        addon = controller.SmartSearchAddonController(
            backend.mw,
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = backend
        responses = []
        errors = []

        backend.submit_search(
            contracts.SearchRequest(
                request_id=24,
                query="atypical antidepressant",
                mode=contracts.SearchMode.SEMANTIC,
            ),
            responses.append,
            errors.append,
        )
        self.assertTrue(semantic.search_started.wait(timeout=1))

        addon._enter_review_mode()

        self.assertTrue(backend.background_maintenance_paused)
        self.assertEqual(semantic.abort_calls, 1)
        self.assertTrue(
            _wait_until(lambda: not any(thread.is_alive() for thread in backend.threads))
        )
        self.assertEqual(responses, [])
        self.assertEqual(errors, [])
        self.assertIsNone(context.semantic_error)
        self.assertIsNone(context.semantic_snapshot.error)

        addon._leave_review_mode()
        semantic.search_started.clear()
        next_finished = threading.Event()
        backend.submit_search(
            contracts.SearchRequest(
                request_id=25,
                query="atypical antidepressant",
                mode=contracts.SearchMode.SEMANTIC,
            ),
            lambda response: (responses.append(response), next_finished.set()),
            errors.append,
        )

        self.assertTrue(next_finished.wait(timeout=1))
        self.assertEqual([response.request_id for response in responses], [25])
        self.assertEqual(errors, [])
        self.assertTrue(semantic.search_started.is_set())

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

    def test_semantic_index_waits_for_inflight_durable_journal_write(self) -> None:
        self.backend.deactivate_profile()
        backend = _DeferredCollectionBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context is not None and context.maintenance is not None
        semantic = _BlockingSemanticService()
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        backend._set_index_state(
            contracts.IndexState.READY,
            detail="Smart & Exact ready.",
        )

        self.assertTrue(backend.queue_maintenance(note_ids=(77,)))
        self.assertEqual(context.maintenance.pending_count(), 0)
        self.assertTrue(backend._lexical_work_pending(context))
        errors = []

        self.assertIsNone(backend.index_semantic(on_error=errors.append))
        self.assertIn("Smart & Exact setup", errors[-1])
        self.assertFalse(semantic.started.is_set())

        persist = backend.pending_query_ops.pop()
        self.assertFalse(persist["uses_collection"])
        persist["success"](persist["op"](None))
        self.assertEqual(context.maintenance.pending_count(), 1)
        self.assertTrue(backend._lexical_work_pending(context))

    def test_semantic_full_index_rechecks_journal_gate_when_claiming_phase(
        self,
    ) -> None:
        context = self.backend._context
        assert context is not None
        semantic = _BlockingSemanticService()
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        self.backend._set_index_state(
            contracts.IndexState.READY,
            detail="Smart & Exact ready.",
        )
        original_pending = self.backend._lexical_work_pending
        calls = 0

        def race(candidate):
            nonlocal calls
            calls += 1
            if calls == 1:
                with self.backend._state_lock:
                    self.backend._journal_writes_inflight[candidate.token] = 1
                return False
            return original_pending(candidate)

        errors = []
        with patch.object(
            self.backend,
            "_lexical_work_pending",
            side_effect=race,
        ):
            self.assertIsNone(
                self.backend.index_semantic(on_error=errors.append)
            )

        self.backend._journal_writes_inflight.clear()
        self.assertGreaterEqual(calls, 2)
        self.assertIn("Smart & Exact setup", errors[-1])
        self.assertIsNone(self.backend._semantic_phase)
        self.assertFalse(semantic.started.is_set())

    def test_semantic_delta_rechecks_journal_gate_when_claiming_phase(self) -> None:
        context = self.backend._context
        assert context is not None
        semantic = _BlockingSemanticService(indexed=True, index_count=1)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        context.semantic_pending_note_ids.add(77)
        self.backend._refresh_semantic_snapshot(context)
        original_pending = self.backend._lexical_work_pending
        calls = 0

        def race(candidate):
            nonlocal calls
            calls += 1
            if calls == 1:
                with self.backend._state_lock:
                    self.backend._journal_writes_inflight[candidate.token] = 1
                return False
            return original_pending(candidate)

        with patch.object(
            self.backend,
            "_lexical_work_pending",
            side_effect=race,
        ):
            self.assertFalse(self.backend._start_semantic_delta(context))

        self.backend._journal_writes_inflight.clear()
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(context.semantic_pending_note_ids, {77})
        self.assertIsNone(self.backend._semantic_phase)
        self.assertFalse(semantic.started.is_set())

    def test_semantic_repair_status_requires_restart_and_blocks_indexing(self) -> None:
        context = self.backend._context
        assert context
        semantic = _BlockingSemanticService(indexed=True)
        context.semantic = semantic
        context.semantic_restart_required = True
        self.backend._refresh_semantic_snapshot(context)

        status = self.backend.get_status().semantic
        assert status is not None
        self.assertIs(status.state, contracts.SemanticState.RESTART_REQUIRED)
        errors = []
        self.assertIsNone(self.backend.index_semantic(on_error=errors.append))
        self.assertIn("Restart Anki", errors[-1])

    def test_semantic_runtime_failure_uses_current_service_recovery_hint(self) -> None:
        context = self.backend._context
        assert context is not None
        semantic = _BlockingSemanticService(indexed=True)
        semantic.last_error_kind = "model"
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        # The immutable UI snapshot was captured before the worker operation
        # failed and therefore contains no error kind.
        self.assertIsNone(getattr(context.semantic_snapshot, "error_kind", None))
        event = self.backend._new_cancellation(kind="semantic")
        errors = []
        with self.backend._state_lock:
            self.backend._semantic_phase = "indexing"

        with patch.object(
            self.backend,
            "release_semantic_runtime",
            return_value=True,
        ):
            self.backend._semantic_failed(
                RuntimeError("verified runtime failure"),
                event,
                context.token,
                context,
                errors.append,
            )

        self.assertIs(
            context.semantic_error_recovery,
            contracts.SemanticRecovery.MODEL,
        )
        self.assertIn("Repair Semantic Search", errors[-1])

    def test_semantic_delta_updates_are_bounded(self) -> None:
        context = self.backend._context
        assert context is not None
        semantic = _BlockingSemanticService(indexed=True)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        context.semantic_pending_deleted_ids.update(range(1, 601))

        self.assertTrue(self.backend._start_semantic_delta(context))

        self.assertEqual(
            [len(batch) for batch in semantic.removed_note_ids],
            [250, 250, 100],
        )
        self.assertEqual(context.semantic_pending_deleted_ids, set())
        self.assertEqual(
            semantic.marked_generations,
            [context.lexical_generation],
        )

    def test_failed_semantic_follow_up_launch_releases_worker(self) -> None:
        context = self.backend._context
        assert context is not None
        note = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="semantic-follow-up-release-test",
        )
        context.index.rebuild((note,))
        # Simulate a collection change landing after the full pass captured
        # its lexical generation.  The completed pass is useful but cannot be
        # published as current, so the controller attempts a follow-up.
        context.note_count = 2
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, index_count=1)
        semantic.release.set()
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        self.backend._set_index_state(
            contracts.IndexState.READY,
            detail="Smart & Exact ready.",
        )
        completed = []
        errors = []

        with (
            patch.object(
                self.backend,
                "_start_semantic_delta",
                return_value=False,
            ) as start_delta,
            patch.object(
                self.backend,
                "_refresh_existing_semantic_index",
                return_value=False,
            ) as refresh,
            patch.object(
                self.backend,
                "release_semantic_runtime",
                return_value=True,
            ) as release,
        ):
            cancel = self.backend.index_semantic(
                on_success=completed.append,
                on_error=errors.append,
            )

        self.assertTrue(callable(cancel))
        self.assertEqual(len(completed), 1)
        self.assertEqual(errors, [])
        self.assertTrue(context.semantic_needs_reconcile)
        start_delta.assert_called_once_with(context)
        refresh.assert_called_once_with(context)
        release.assert_called_once_with()

    def test_semantic_delta_coalescing_uses_latest_note_state(self) -> None:
        context = self.backend._context
        assert context is not None
        context.semantic_exact_delta_chain = True

        with self.backend._state_lock:
            self.backend._coalesce_semantic_delta_locked(context, (), (77,))
            self.backend._coalesce_semantic_delta_locked(context, (77,), ())
        self.assertEqual(context.semantic_pending_note_ids, {77})
        self.assertEqual(context.semantic_pending_deleted_ids, set())

        with self.backend._state_lock:
            self.backend._coalesce_semantic_delta_locked(context, (88,), ())
            self.backend._coalesce_semantic_delta_locked(context, (), (88,))
        self.assertEqual(context.semantic_pending_note_ids, {77})
        self.assertEqual(context.semantic_pending_deleted_ids, {88})

    def test_successive_journal_batches_preserve_every_semantic_delta(self) -> None:
        context = self.backend._context
        assert context is not None and context.maintenance is not None
        originals = (
            models.IndexedNote(note_id=1001, fields={"Text": "Bupropion."}),
            models.IndexedNote(note_id=2002, fields={"Text": "Metformin."}),
        )
        updates = (
            models.IndexedNote(note_id=1001, fields={"Text": "Sertraline."}),
            models.IndexedNote(note_id=2002, fields={"Text": "Empagliflozin."}),
        )
        context.index.rebuild(originals)
        context.note_count = 2
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, index_count=2)
        semantic.release.set()
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic

        for updated in updates:
            sequence = context.maintenance.enqueue(
                (updated.note_id,),
                controller.DirtyReason.NOTE_CONTENT,
            )
            assert sequence is not None
            entry = context.maintenance.pending()[0]
            event = self.backend._new_cancellation(kind="maintenance")
            self.assertTrue(self.backend._begin_maintenance())
            self.backend._reconcile_targeted_snapshot(
                context,
                (updated.note_id,),
                [updated],
                (),
                (),
                (entry,),
                event,
                lambda _status: None,
                self.fail,
            )

        self.assertEqual(context.maintenance.pending_count(), 0)
        self.assertTrue(context.semantic_exact_delta_chain)
        self.assertEqual(
            context.semantic_pending_note_ids,
            {1001, 2002},
        )

        self.assertTrue(self.backend.resume_pending_background_work())

        self.assertEqual(semantic.indexed_note_ids, [(1001, 2002)])
        self.assertEqual(semantic.marked_generations, [context.lexical_generation])
        self.assertFalse(context.semantic_needs_reconcile)
        self.assertIs(context.engine.semantic_provider, semantic)

    def test_prepared_empty_semantic_index_accepts_first_added_note_delta(self) -> None:
        context = self.backend._context
        assert context is not None
        original = models.IndexedNote(note_id=1001, fields={"Text": "Delete me."})
        added = models.IndexedNote(note_id=2002, fields={"Text": "New note."})
        context.index.rebuild((original,))
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, index_count=1)
        semantic.release.set()
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic

        snapshots = {1001: [], 2002: [added]}
        self.backend.reader.snapshot_note_ids = (
            lambda _collection, note_ids, **_kwargs: snapshots[int(note_ids[0])]
        )
        self.assertTrue(self.backend.reconcile_note_ids((1001,), on_error=self.fail))
        self.assertEqual(semantic.index_count, 0)
        self.assertEqual(semantic.source_generation, context.lexical_generation)

        self.assertTrue(self.backend.reconcile_note_ids((2002,), on_error=self.fail))

        self.assertEqual(context.note_count, 1)
        self.assertEqual(semantic.indexed_note_ids, [(2002,)])
        self.assertEqual(semantic.source_generation, context.lexical_generation)
        self.assertIs(context.engine.semantic_provider, semantic)

    def test_full_semantic_publish_fails_closed_for_midflight_journal_work(
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
        assert context is not None and context.maintenance is not None
        note = models.IndexedNote(note_id=1001, fields={"Text": "Bupropion."})
        context.index.rebuild((note,))
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, index_count=1)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        backend._set_index_state(contracts.IndexState.READY, detail="Ready.")
        finished = threading.Event()

        self.assertIsNotNone(
            backend.index_semantic(
                on_success=lambda _status: finished.set(),
                on_error=self.fail,
            )
        )
        self.assertTrue(semantic.started.wait(timeout=1))
        self.assertTrue(backend.queue_maintenance(note_ids=(77,)))
        self.assertTrue(
            _wait_until(lambda: context.maintenance.pending_count() == 1)
        )
        semantic.release.set()
        self.assertTrue(finished.wait(timeout=2))

        self.assertEqual(semantic.marked_generations, [])
        self.assertTrue(context.semantic_needs_reconcile)
        self.assertTrue(context.semantic_force_full_refresh)
        self.assertIsNone(context.engine.semantic_provider)

    def test_semantic_delta_publish_fails_closed_for_midflight_journal_work(
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
        assert context is not None and context.maintenance is not None
        note = models.IndexedNote(note_id=1001, fields={"Text": "Bupropion."})
        context.index.rebuild((note,))
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, index_count=1)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        backend._refresh_semantic_snapshot(context)
        context.semantic_exact_delta_chain = True
        context.semantic_needs_reconcile = True
        context.semantic_pending_note_ids.add(1001)

        self.assertTrue(backend._start_semantic_delta(context))
        self.assertTrue(semantic.started.wait(timeout=1))
        self.assertTrue(backend.queue_maintenance(note_ids=(77,)))
        self.assertTrue(
            _wait_until(lambda: context.maintenance.pending_count() == 1)
        )
        semantic.release.set()
        self.assertTrue(
            _wait_until(lambda: backend._semantic_phase is None)
        )

        self.assertEqual(semantic.marked_generations, [])
        self.assertTrue(context.semantic_needs_reconcile)
        self.assertTrue(context.semantic_exact_delta_chain)
        self.assertIsNone(context.engine.semantic_provider)

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
            lambda _collection, note_ids, **_kwargs: (
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
            lambda _collection, _note_ids, **_kwargs: []
        )
        self.assertTrue(
            self.backend.reconcile_note_ids((1001,), on_error=self.fail)
        )

        self.assertEqual(context.index.count_documents(), 0)
        self.assertEqual(context.note_count, 0)
        self.assertEqual(semantic.removed_note_ids, [(1001,)])
        self.assertEqual(semantic.indexed_note_ids, [])

    def test_cancel_after_targeted_commit_retains_semantic_delta_intent(self) -> None:
        context = self.backend._context
        assert context is not None
        original = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion."},
        )
        updated = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Sertraline."},
        )
        context.index.rebuild((original,))
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, index_count=1)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        context.engine.semantic_provider = semantic
        event = self.backend._new_cancellation(kind="maintenance")
        original_apply = context.index.apply_upsert

        def apply_then_cancel(plan, *, cancel_check=None):
            changed = original_apply(plan, cancel_check=cancel_check)
            event.set()
            return changed

        self.assertTrue(self.backend._begin_maintenance())
        with patch.object(
            context.index,
            "apply_upsert",
            side_effect=apply_then_cancel,
        ):
            self.backend._reconcile_targeted_snapshot(
                context,
                (1001,),
                [updated],
                (),
                (),
                (),
                event,
                lambda _status: None,
                self.fail,
            )

        self.assertIn(
            "Sertraline",
            context.index.get_documents((1001,))[1001].plain_text,
        )
        self.assertEqual(context.semantic_pending_note_ids, {1001})
        self.assertTrue(context.semantic_exact_delta_chain)
        self.assertFalse(self.backend._finalize_semantic_delta_chain(context))
        self.assertNotEqual(semantic.source_generation, context.index.generation)
        self.assertIsNone(context.engine.semantic_provider)

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

        def tracked_warmup(engine, **kwargs):
            warm_threads.append(threading.get_ident())
            return original_warmup(engine, **kwargs)

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

    def test_post_swap_rebuild_cancellation_never_leaves_stale_semantic_ready(
        self,
    ) -> None:
        context = self.backend._context
        assert context is not None
        original = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Bupropion treats depression."},
            guid="cancelled-rebuild",
        )
        replacement = models.IndexedNote(
            note_id=1001,
            fields={"Text": "Venlafaxine treats depression."},
            guid="cancelled-rebuild",
        )
        context.index.rebuild([original])
        context.note_count = 1
        context.lexical_generation = context.index.generation
        context.engine = controller.SearchEngine(context.index)
        semantic = _BlockingSemanticService(indexed=True, index_count=1)
        semantic.source_generation = context.lexical_generation
        context.semantic = semantic
        self.backend._refresh_semantic_snapshot(context)
        context.semantic_needs_reconcile = False
        context.engine.semantic_provider = semantic
        self.backend._set_index_state(
            contracts.IndexState.BUILDING,
            detail="Rebuilding.",
        )
        event = threading.Event()
        original_rebuild = context.index.rebuild

        def rebuild_then_cancel(notes, *, progress=None, cancel_check=None):
            count = original_rebuild(
                notes,
                progress=progress,
                cancel_check=cancel_check,
            )
            event.set()
            return count

        completions = []
        errors = []
        self.backend._begin_maintenance()
        with patch.object(
            context.index,
            "rebuild",
            side_effect=rebuild_then_cancel,
        ):
            self.backend._build_snapshot_in_background(
                context,
                [replacement],
                "cancelled-rebuild-fingerprint",
                event,
                1,
                lambda *_args: None,
                completions.append,
                errors.append,
            )

        self.assertEqual(errors, [])
        self.assertEqual(len(completions), 1)
        self.assertEqual(
            context.lexical_generation,
            context.index.generation,
        )
        self.assertIsNone(semantic.source_generation)
        self.assertIsNone(context.semantic_source_generation)
        self.assertTrue(context.semantic_needs_reconcile)
        self.assertIsNone(context.engine.semantic_provider)
        self.assertIsNot(
            self.backend.get_status().semantic.state,
            contracts.SemanticState.READY,
        )
        self.assertIs(self.backend.get_status().state, contracts.IndexState.READY)

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
            settings=types.SimpleNamespace(
                preview_enabled=True,
                preview_default=contracts.PreviewDefault.QUESTION,
            )
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

            def set_result(
                self,
                current,
                *,
                force=False,
                apply_default=False,
            ) -> None:
                self.updated.append((current, force, apply_default))

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
        self.assertIs(
            factory.call_args.kwargs["default_view"],
            contracts.PreviewDefault.QUESTION,
        )
        self.assertEqual(preview.show_count, 1)
        self.assertEqual(active_states, [True])

        addon._preview_selection_changed(replacement)
        addon._refresh_previewer()
        self.assertEqual(
            preview.updated,
            [
                (replacement, False, False),
                (replacement, True, False),
            ],
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

    def test_preview_default_change_applies_only_to_an_open_enabled_pane(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        pane = types.SimpleNamespace(visible=True)
        pane.isVisible = lambda: pane.visible
        addon._dialog = types.SimpleNamespace(preview_pane=pane)
        settings = types.SimpleNamespace(
            preview_enabled=True,
            preview_default=contracts.PreviewDefault.QUESTION,
        )
        addon._ui_controller = types.SimpleNamespace(settings=settings)
        changes = []
        addon._previewer = types.SimpleNamespace(
            set_default_view=lambda value, *, apply_now: changes.append(
                (value, apply_now)
            )
        )

        addon._preview_default_changed(contracts.PreviewDefault.ANSWER)
        pane.visible = False
        addon._preview_default_changed(contracts.PreviewDefault.EDIT)
        pane.visible = True
        settings.preview_enabled = False
        addon._preview_default_changed(contracts.PreviewDefault.QUESTION)

        self.assertEqual(
            changes,
            [
                (contracts.PreviewDefault.ANSWER, True),
                (contracts.PreviewDefault.EDIT, False),
                (contracts.PreviewDefault.QUESTION, False),
            ],
        )

    def test_preview_side_request_opens_card_mode_and_is_safely_scoped(
        self,
    ) -> None:
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
        pane_mode = ["edit"]
        addon._dialog = types.SimpleNamespace(
            preview_pane=types.SimpleNamespace(
                mode=lambda: pane_mode[0],
                isVisible=lambda: True,
            ),
            set_preview_active=lambda _active: None,
        )
        addon._ui_controller = types.SimpleNamespace(
            settings=types.SimpleNamespace(preview_enabled=True)
        )
        events: list[object] = []

        class _Preview:
            def is_targeting(self, current) -> bool:
                return current == result

            def set_mode(self, mode: str) -> None:
                events.append(("mode", mode))
                pane_mode[0] = mode

            def show_side(self, side: str) -> None:
                events.append(("side", side))

        preview = _Preview()

        def open_preview(current) -> None:
            events.append(("open", current))
            addon._previewer = preview

        with patch.object(addon, "_toggle_previewer", side_effect=open_preview):
            addon._show_preview_side(result, "answer")

        self.assertEqual(
            events,
            [
                ("open", result),
                ("mode", "card"),
                ("side", "answer"),
            ],
        )

        # Once the correct card is already open, another reveal bypasses the
        # reopen path; the inspector's idempotent side setter decides whether
        # any render is needed.
        events.clear()
        addon._previewer = preview
        with patch.object(addon, "_toggle_previewer") as toggle:
            addon._show_preview_side(result, "answer")
        toggle.assert_not_called()
        self.assertEqual(events, [("side", "answer")])

        events.clear()
        addon._ui_controller.settings.preview_enabled = False
        addon._show_preview_side(result, "question")
        addon._ui_controller.settings.preview_enabled = True
        addon._show_preview_side(
            contracts.SearchResult(note_id=8, card_ids=()),
            "answer",
        )
        self.assertEqual(events, [])

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
            pause=lambda: events.append("pause"),
            dispose=lambda: events.append("dispose"),
        )
        preview = _Preview()
        addon._dialog = dialog
        addon._ui_controller = ui_controller
        addon._previewer = preview
        semantic_timer = _FakeTimer()
        semantic_timer.start(90_000)
        addon._semantic_unload_timer = semantic_timer
        semantic_aborts = []
        addon.backend.abort_semantic_runtime_now = (
            lambda: semantic_aborts.append(True) or True
        )

        addon._close_dialog()
        self.assertEqual(events, ["pause", "save"])
        self.assertEqual(semantic_timer.stop_count, 1)
        self.assertEqual(semantic_aborts, [True])
        self.assertIs(addon._dialog, dialog)
        self.assertIs(addon._previewer, preview)

        saved_callbacks.pop()()
        self.assertEqual(
            events,
            ["pause", "save", "close", "dispose", "delete"],
        )
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
        timers = [_FakeTimer() for _ in range(8)]
        (
            addon._reconcile_timer,
            addon._semantic_autostart_timer,
            addon._dialog_refresh_timer,
            addon._vocabulary_refresh_timer,
            addon._card_state_refresh_timer,
            addon._preview_open_timer,
            addon._semantic_unload_timer,
            addon._post_review_resume_timer,
        ) = timers
        pauses = []
        aborts = []
        reconciles = []
        addon.backend.set_background_maintenance_paused = (
            lambda paused: pauses.append(bool(paused))
        )
        addon.backend.abort_semantic_runtime_now = lambda: aborts.append(True)
        addon.schedule_reconcile = lambda **kwargs: reconciles.append(kwargs)
        addon._close_dialog_with_callback = callbacks.append

        addon._on_collection_will_temporarily_close(object())

        self.assertTrue(addon._collection_temporarily_closed)
        self.assertTrue(addon._maintenance_blocked())
        self.assertEqual(len(callbacks), 1)
        self.assertTrue(callable(callbacks[0]))
        self.assertEqual(pauses, [True])
        self.assertEqual(aborts, [True])
        self.assertTrue(all(timer.stop_count == 1 for timer in timers))

        addon._on_collection_reopened(object())

        self.assertFalse(addon._collection_temporarily_closed)
        self.assertFalse(addon._maintenance_blocked())
        self.assertEqual(pauses, [True, False])
        self.assertEqual(reconciles, [{}])
        self.assertEqual(addon._post_review_resume_timer.starts, [250])

    def test_temporary_collection_reopen_stays_paused_during_review(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon._collection_temporarily_closed = True
        addon._review_active = True
        addon._post_review_resume_timer = _FakeTimer()
        pauses = []
        reconciles = []
        addon.backend.set_background_maintenance_paused = (
            lambda paused: pauses.append(bool(paused))
        )
        addon.schedule_reconcile = lambda **kwargs: reconciles.append(kwargs)

        addon._on_collection_reopened(object())

        self.assertFalse(addon._collection_temporarily_closed)
        self.assertTrue(addon._maintenance_blocked())
        self.assertEqual(pauses, [True])
        self.assertEqual(reconciles, [{}])
        self.assertEqual(addon._post_review_resume_timer.starts, [])

    def test_leaving_review_does_not_unpause_a_closed_collection(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon._review_active = True
        addon._collection_temporarily_closed = True
        addon._post_review_resume_timer = _FakeTimer()
        pauses = []
        addon.backend.set_background_maintenance_paused = (
            lambda paused: pauses.append(bool(paused))
        )

        addon._leave_review_mode()

        self.assertFalse(addon._review_active)
        self.assertTrue(addon._maintenance_blocked())
        self.assertEqual(pauses, [True])
        self.assertEqual(addon._post_review_resume_timer.starts, [])

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

    def test_semantic_autostart_silently_migrates_an_existing_model_runtime(
        self,
    ) -> None:
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
        backend._context.semantic_snapshot = types.SimpleNamespace(
            supported=True,
            model_ready=True,
            runtime_ready=False,
        )
        installs = []

        def install_semantic(**callbacks):
            installs.append(callbacks)
            return lambda: None

        backend.install_semantic = install_semantic
        timer = _FakeTimer()
        addon.backend = backend
        addon._semantic_autostart_timer = timer
        addon._refresh_dialog = lambda: None
        addon._refresh_dialog_now = lambda: None
        addon._run_on_main = lambda callback: callback()

        addon._schedule_semantic_autostart()
        addon._run_semantic_autostart()

        self.assertEqual(len(installs), 1)
        self.assertEqual(backend.index_calls, 0)
        self.assertEqual(
            addon._semantic_autostart_attempted_token,
            backend._context.token,
        )

        installs[0]["on_success"](status)

        self.assertIsNone(addon._semantic_autostart_attempted_token)
        self.assertEqual(timer.starts[-1], 0)

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

    def test_fresh_profile_setup_cancelled_by_review_is_restarted_afterward(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        addon._semantic_autostart_timer = _FakeTimer()
        addon._post_review_resume_timer = _FakeTimer()
        addon.schedule_reconcile = lambda **_kwargs: None
        addon._refresh_dialog = lambda: None
        context = self.backend._context
        assert context is not None
        context.note_count = 0
        self.backend._set_index_state(
            contracts.IndexState.BUILDING,
            detail="Starting snapshot.",
        )

        addon._profile_activation_ready(self.backend.get_status())
        self.assertTrue(addon._initial_setup_deferred)

        rebuilds = []
        self.backend._maintenance_running = False
        self.backend.rebuild_index = lambda **_kwargs: (
            rebuilds.append(True),
            (lambda: None),
        )[1]
        addon._review_active = False
        addon._resume_after_review()

        self.assertEqual(rebuilds, [True])
        self.assertFalse(addon._initial_setup_deferred)

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

    def test_repaired_semantic_install_waits_for_restart(self) -> None:
        status = contracts.IndexStatus(
            contracts.IndexState.READY,
            semantic=contracts.SemanticStatus(
                contracts.SemanticState.RESTART_REQUIRED
            ),
        )
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        backend = _AutostartBackend(status)
        backend._context.semantic_restart_required = True
        timer = _FakeTimer()
        messages = []
        addon.backend = backend
        addon._semantic_autostart_timer = timer
        addon._refresh_dialog = lambda: None
        addon._show_message = messages.append

        addon._semantic_install_complete()

        self.assertEqual(timer.starts, [])
        self.assertEqual(
            addon._semantic_autostart_attempted_token,
            backend._context.token,
        )
        self.assertIn("Restart Anki", messages[-1])

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

        addon._review_active = True
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

        addon._review_active = False
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
        self.assertEqual(reconciles, [True])

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
        self.assertEqual(reconciles, [True, True])

    def test_reviewer_card_answers_trigger_no_addon_work(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon._review_active = True
        timer = _FakeTimer()
        addon._card_state_refresh_timer = timer
        reconciles = []
        addon.schedule_reconcile = lambda **kwargs: reconciles.append(kwargs)
        changes = types.SimpleNamespace(
            card=True,
            note=False,
            deck=False,
            tag=False,
            notetype=False,
            note_text=False,
        )

        for _ in range(100):
            addon._on_operation(changes, object())

        self.assertEqual(reconciles, [])
        self.assertEqual(timer.starts, [])

    def test_semantic_worker_lease_is_warm_in_search_and_reaped_on_close(
        self,
    ) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        dialog = types.SimpleNamespace(isVisible=lambda: True)
        timer = _FakeTimer()
        card_timer = _FakeTimer()
        aborts: list[bool] = []
        closed: list[bool] = []
        addon._dialog = dialog
        addon._semantic_unload_timer = timer
        addon._card_state_refresh_timer = card_timer
        addon.backend.abort_semantic_runtime_now = (
            lambda: aborts.append(True) or True
        )
        addon._close_previewer = lambda: closed.append(True)
        addon._mark_managed_dialog_closed = lambda: closed.append(True)

        addon._arm_semantic_idle_unload()
        self.assertEqual(timer.starts, [90_000])
        self.assertEqual(aborts, [])

        addon._semantic_activity_changed(True)
        self.assertEqual(timer.stop_count, 1)
        addon._semantic_activity_changed(False)
        self.assertEqual(timer.starts, [90_000, 90_000])

        addon._search_dialog_closed(dialog)
        self.assertEqual(timer.stop_count, 2)
        self.assertEqual(card_timer.stop_count, 1)
        self.assertEqual(aborts, [True])
        self.assertEqual(closed, [True, True])

    def test_unexpected_dialog_deletion_releases_semantic_worker(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        dialog = object()
        disposed = []
        ui_controller = types.SimpleNamespace(
            dispose=lambda: disposed.append(True)
        )
        timer = _FakeTimer()
        aborts = []
        previews = []
        closed = []
        addon._dialog = dialog
        addon._ui_controller = ui_controller
        addon._semantic_unload_timer = timer
        addon.backend.abort_semantic_runtime_now = (
            lambda: aborts.append(True) or True
        )
        addon._close_previewer = lambda **kwargs: previews.append(kwargs)
        addon._mark_managed_dialog_closed = lambda: closed.append(True)

        addon._search_dialog_destroyed(dialog, ui_controller)

        self.assertEqual(timer.stop_count, 1)
        self.assertEqual(aborts, [True])
        self.assertEqual(previews, [{"save": False}])
        self.assertEqual(closed, [True])
        self.assertEqual(disposed, [True])
        self.assertIsNone(addon._dialog)
        self.assertIsNone(addon._ui_controller)

    def test_semantic_worker_lease_never_arms_during_review(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        timer = _FakeTimer()
        aborts: list[bool] = []
        addon._dialog = types.SimpleNamespace(isVisible=lambda: True)
        addon._semantic_unload_timer = timer
        addon._review_active = True
        addon.backend.abort_semantic_runtime_now = (
            lambda: aborts.append(True) or True
        )

        addon._arm_semantic_idle_unload()

        self.assertEqual(timer.starts, [])
        self.assertEqual(timer.stop_count, 1)
        self.assertEqual(aborts, [True])

    def test_reviewer_entry_is_idempotent_and_stops_each_background_timer_once(
        self,
    ) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        timers = [_FakeTimer() for _ in range(8)]
        (
            addon._reconcile_timer,
            addon._semantic_autostart_timer,
            addon._dialog_refresh_timer,
            addon._vocabulary_refresh_timer,
            addon._card_state_refresh_timer,
            addon._preview_open_timer,
            addon._semantic_unload_timer,
            addon._post_review_resume_timer,
        ) = timers
        pauses = []
        aborts = []
        addon.backend.set_background_maintenance_paused = (
            lambda paused: pauses.append(bool(paused))
        )
        addon.backend.abort_semantic_runtime_now = lambda: aborts.append(True)

        addon._enter_review_mode()
        first_stop_counts = tuple(timer.stop_count for timer in timers)
        addon._enter_review_mode()

        self.assertTrue(addon._review_active)
        self.assertEqual(pauses, [True])
        self.assertEqual(aborts, [True])
        self.assertEqual(
            tuple(timer.stop_count for timer in timers),
            first_stop_counts,
        )
        self.assertTrue(all(count >= 1 for count in first_stop_counts))

    def test_post_review_drains_durable_lexical_work_before_semantic_resume(
        self,
    ) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        addon._review_active = False
        addon._reconcile_timer = _FakeTimer()
        addon._post_review_resume_timer = _FakeTimer()
        context = self.backend._context
        assert context is not None and context.maintenance is not None
        context.maintenance.enqueue([777], controller.DirtyReason.NOTE_CONTENT)
        resumes = []
        self.backend.resume_pending_background_work = lambda: resumes.append(True)

        addon._resume_after_review()

        self.assertEqual(addon._reconcile_timer.starts, [25])
        self.assertEqual(resumes, [])

    def test_transient_targeted_failure_retries_in_memory_only_work(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        addon._review_active = False
        addon._reconcile_timer = _FakeTimer()
        addon._post_review_resume_timer = _FakeTimer()
        errors = []
        addon._show_error = errors.append
        addon._refresh_dialog = lambda: None
        addon._pending_reconcile_note_ids.add(88)
        attempts = []

        def fail_once(
            note_ids,
            *,
            journal_entries=(),
            on_success,
            on_error,
        ):
            attempts.append(tuple(note_ids))
            if len(attempts) == 1:
                self.assertEqual(tuple(journal_entries), ())
                on_error("temporary collection error")
            else:
                on_success(self.backend.get_status())
            return True

        self.backend.reconcile_note_ids = fail_once

        addon._run_reconcile()
        self.assertEqual(attempts, [(88,)])
        self.assertEqual(addon._pending_reconcile_note_ids, {88})
        self.assertEqual(addon._reconcile_timer.starts[-1], 1_000)
        self.assertEqual(errors, ["temporary collection error"])

        addon._run_reconcile()
        self.assertEqual(attempts, [(88,), (88,)])
        self.assertEqual(addon._pending_reconcile_note_ids, set())
        self.assertEqual(addon._reconcile_retry_count, 0)

    def test_filtered_action_success_updates_in_place_without_requery(self) -> None:
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
        captures: list[bool] = []
        reruns: list[object] = []
        addon._ui_controller = types.SimpleNamespace(
            capture_search_generation=lambda: captures.append(True),
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
        self.assertEqual(captures, [])
        self.assertEqual(reruns, [])

    def test_successful_action_arms_one_click_undo_without_requery(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        offers = []
        addon._dialog = types.SimpleNamespace(
            set_undo_available=lambda *args: offers.append(args),
            apply_card_state_change=lambda *_args, **_kwargs: None,
            finish_batch_action=lambda _ok, _message: None,
        )
        addon._show_message = lambda _message: None
        addon._refresh_previewer = lambda: None
        token = controller.UndoToken(
            9,
            "Suspend",
            self.backend._context.collection_path,
        )
        action = controller.CollectionAction(
            controller.ActionKind.SUSPEND,
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
            changed_card_ids=(101,),
            undo_token=token,
        )

        addon._collection_action_succeeded(action, outcome)

        self.assertEqual(offers[-1], (True, "Suspend"))
        self.assertEqual(addon._pending_undo.token, token)

    def test_one_click_undo_uses_guarded_worker_and_keeps_results(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        events = []
        addon._dialog = types.SimpleNamespace(
            set_undo_available=lambda *args: events.append(("offer", args)),
            set_batch_action_busy=lambda *args: events.append(("busy", args)),
            finish_undo_action=lambda *args: events.append(("finish", args)),
        )
        addon._show_message = lambda message: events.append(("message", message))
        addon._show_error = lambda message: events.append(("error", message))
        addon._refresh_visible_card_states = lambda: events.append(("refresh",))
        addon._refresh_previewer = lambda: events.append(("preview",))
        addon._with_preview_saved = (
            lambda callback, *, detach_editor: callback()
        )
        action = controller.CollectionAction(
            controller.ActionKind.SUSPEND,
            note_ids=(11,),
            card_ids=(101,),
        )
        pending = controller._PendingUndo(
            self.backend._context.token,
            controller.UndoToken(
                12,
                "Suspend",
                self.backend._context.collection_path,
            ),
            action,
        )
        addon._pending_undo = pending
        scheduled = []

        def complete(**kwargs):
            scheduled.append(kwargs)
            kwargs["on_success"](types.SimpleNamespace(changes=object()))

        with patch.object(controller, "start_guarded_undo", side_effect=complete):
            addon._undo_last_change()

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0]["token"], pending.token)
        self.assertIsInstance(scheduled[0]["initiator"], controller._OwnedUndo)
        self.assertIsNone(addon._pending_undo)
        self.assertIn(("finish", (True, "Undid Suspend")), events)
        self.assertIn(("refresh",), events)
        self.assertIn(("preview",), events)

    def test_editor_save_lifecycle_cancellation_retires_undo_silently(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        errors = []
        finished = []
        addon._dialog = types.SimpleNamespace(
            set_undo_available=lambda *_args: None,
            set_batch_action_busy=lambda *_args: None,
            finish_undo_action=lambda *args: finished.append(args),
        )
        addon._show_error = errors.append
        action = controller.CollectionAction(
            controller.ActionKind.SUSPEND,
            card_ids=(101,),
        )
        addon._pending_undo = controller._PendingUndo(
            self.backend._context.token,
            controller.UndoToken(12, "Suspend", "/tmp/collection.anki2"),
            action,
        )

        def save_then_change(callback, *, detach_editor):
            self.assertTrue(detach_editor)
            addon._clear_undo_offer()
            callback()

        addon._with_preview_saved = save_then_change
        with patch.object(controller, "start_guarded_undo") as start:
            addon._undo_last_change()

        start.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(finished, [(False, "Undo is no longer available.")])

    def test_bury_and_change_deck_build_exact_card_actions(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        results = (
            contracts.SearchResult(note_id=11, card_ids=(101, 102)),
            contracts.SearchResult(note_id=22, card_ids=(201,)),
        )

        with patch.object(addon, "_run_collection_action") as run:
            addon._set_results_buried(results, True)
            bury = run.call_args.args[0]
            self.assertIs(bury.kind, controller.ActionKind.BURY)
            self.assertEqual(bury.note_ids, (11, 22))
            self.assertEqual(bury.card_ids, (101, 102, 201))

            addon._change_results_deck(results, 9, "Medicine::Cardiology")
            move = run.call_args.args[0]
            self.assertIs(move.kind, controller.ActionKind.CHANGE_DECK)
            self.assertEqual(move.card_ids, (101, 102, 201))
            self.assertEqual(move.deck_id, 9)
            self.assertEqual(move.deck_name, "Medicine::Cardiology")

    def test_create_copy_flushes_preview_and_uses_modern_add_cards(self) -> None:
        mw = _MainWindow()
        addon = controller.SmartSearchAddonController(
            mw,
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        note = types.SimpleNamespace(id=11)
        card = types.SimpleNamespace(
            nid=11,
            current_deck_id=lambda: 1_785_791_800_898,
        )
        mw.col.notes[11] = note
        mw.col.cards[101] = card
        opened = []
        add_cards = types.SimpleNamespace(
            set_note=lambda source, deck_id: opened.append((source, deck_id))
        )
        mw._open_new_or_legacy_dialog = lambda name: (
            opened.append(name) or add_cards
        )
        pending = []

        class _Preview:
            def flush(self, callback) -> None:
                pending.append(callback)

        addon._previewer = _Preview()
        errors = []
        addon._show_error = errors.append
        result = contracts.SearchResult(note_id=11, card_ids=(101,))

        addon._create_result_copy((result,))

        self.assertEqual(opened, [])
        self.assertEqual(len(pending), 1)
        pending.pop()()
        self.assertEqual(
            opened,
            ["AddCards", (note, 1_785_791_800_898)],
        )
        self.assertEqual(errors, [])

    def test_create_copy_uses_legacy_dialog_and_filtered_home_deck(self) -> None:
        mw = _MainWindow()
        addon = controller.SmartSearchAddonController(
            mw,
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        note = types.SimpleNamespace(
            id=11,
            fields=["Question", "linked-note-uuid"],
            keys=lambda: ["Front", "ankihub_id"],
        )
        card = types.SimpleNamespace(nid=11, did=700, odid=42)
        mw.col.notes[11] = note
        mw.col.cards[101] = card
        opened = []
        add_cards = types.SimpleNamespace(
            editor=types.SimpleNamespace(note=object()),
            set_note=lambda source, deck_id: opened.append(
                (tuple(source.fields), deck_id)
            ),
        )
        fake_aqt = types.SimpleNamespace(
            dialogs=types.SimpleNamespace(
                open=lambda name, parent: (
                    opened.append((name, parent)) or add_cards
                )
            )
        )

        with patch.dict(sys.modules, {"aqt": fake_aqt}):
            addon._create_result_copy_after_save(11, (404, 101))

        self.assertEqual(opened[0], ("AddCards", mw))
        self.assertEqual(opened[1], (("Question", ""), 42))

    def test_create_copy_rejects_multiple_or_stale_notes(self) -> None:
        mw = _MainWindow()
        addon = controller.SmartSearchAddonController(
            mw,
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        errors = []
        addon._show_error = errors.append
        first = contracts.SearchResult(note_id=11, card_ids=(101,))
        second = contracts.SearchResult(note_id=22, card_ids=(202,))

        addon._create_result_copy((first, second))
        addon._create_result_copy_after_save(404, (101,))

        self.assertEqual(errors[0], "Select one note to create a copy.")
        self.assertIn("source note is no longer available", errors[1].lower())

    def test_bury_success_updates_only_changed_cards_in_place(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        applied = []
        addon._dialog = types.SimpleNamespace(
            apply_card_state_change=lambda card_ids, **change: applied.append(
                (tuple(card_ids), change)
            ),
            finish_batch_action=lambda _ok, _text: None,
        )
        addon._show_message = lambda _message: None
        action = controller.CollectionAction(
            controller.ActionKind.BURY,
            note_ids=(11, 22),
            card_ids=(101, 102, 201),
        )
        outcome = types.SimpleNamespace(
            changes=object(),
            kind=controller.ActionKind.BURY,
            requested=3,
            live=3,
            eligible=2,
            changed=2,
            stale=0,
            skipped=1,
            note_count=2,
            changed_card_ids=(101, 201),
        )

        addon._collection_action_succeeded(action, outcome)

        self.assertEqual(
            applied,
            [((101, 201), {"suspended": False, "buried": True})],
        )

    def test_tag_filtered_action_also_keeps_current_results_stable(self) -> None:
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
        captures: list[bool] = []
        reruns: list[object] = []
        addon._ui_controller = types.SimpleNamespace(
            capture_search_generation=lambda: captures.append(True),
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

        self.assertEqual(captures, [])
        self.assertEqual(reruns, [])

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

    def test_owned_tag_undo_reconciles_exact_ids_and_retires_offer(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        addon._dialog = types.SimpleNamespace(
            set_undo_available=lambda *_args: None
        )
        requests = []
        addon.schedule_reconcile = lambda **kwargs: requests.append(kwargs)
        context = self.backend._context
        assert context
        action = controller.CollectionAction(
            controller.ActionKind.ADD_TAGS,
            note_ids=(22, 11),
            tags="review",
        )
        addon._pending_undo = controller._PendingUndo(
            context.token,
            controller.UndoToken(
                5,
                "Add Tags",
                context.collection_path,
            ),
            action,
        )

        addon._on_operation(
            types.SimpleNamespace(
                card=False,
                note=True,
                deck=False,
                tag=True,
                notetype=False,
                note_text=True,
            ),
            controller._OwnedUndo(context.token, action),
        )

        self.assertIsNone(addon._pending_undo)
        self.assertEqual(requests, [{"note_ids": (22, 11)}])

    def test_foreign_collection_operation_invalidates_the_undo_offer(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        offers = []
        addon._dialog = types.SimpleNamespace(
            set_undo_available=lambda *args: offers.append(args)
        )
        context = self.backend._context
        assert context
        action = controller.CollectionAction(
            controller.ActionKind.SUSPEND,
            card_ids=(101,),
        )
        addon._pending_undo = controller._PendingUndo(
            context.token,
            controller.UndoToken(
                5,
                "Suspend",
                context.collection_path,
            ),
            action,
        )
        addon.schedule_reconcile = lambda **_kwargs: None

        addon._on_operation(
            types.SimpleNamespace(
                card=False,
                note=False,
                deck=False,
                tag=False,
                notetype=False,
                note_text=False,
            ),
            object(),
        )

        self.assertIsNone(addon._pending_undo)
        self.assertEqual(offers[-1], (False,))

    def test_owned_deck_move_reconciles_exact_notes_but_bury_does_not(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        requests = []
        addon.schedule_reconcile = lambda **kwargs: requests.append(kwargs)
        context = self.backend._context
        assert context
        changes = types.SimpleNamespace(
            card=True,
            note=False,
            deck=True,
            tag=False,
            notetype=False,
            note_text=False,
        )
        move = controller.CollectionAction(
            controller.ActionKind.CHANGE_DECK,
            note_ids=(22, 11),
            card_ids=(201, 101),
            deck_id=9,
            deck_name="Target",
        )

        addon._on_operation(
            changes,
            controller._OwnedMutation(context.token, move),
        )
        self.assertEqual(requests, [{"note_ids": (22, 11)}])

        bury = controller.CollectionAction(
            controller.ActionKind.BURY,
            note_ids=(22,),
            card_ids=(201,),
        )
        addon._on_operation(
            changes,
            controller._OwnedMutation(context.token, bury),
        )
        self.assertEqual(requests, [{"note_ids": (22, 11)}])

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
        # The durable queue remains authoritative. The exact edit stays ahead
        # of the compact audit without forcing a long whole-profile delay.
        self.assertEqual(timer.starts[-1], addon._reconcile_debounce_ms)

    def test_failed_journal_enqueue_retains_full_fallback_across_review_cancel(
        self,
    ) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        addon._review_active = False
        addon._reconcile_timer = _FakeTimer()
        addon._run_on_main = lambda callback: callback()

        def fail_persist(*, on_ready, **_kwargs):
            on_ready(False)
            return True

        self.backend.queue_maintenance = fail_persist
        targeted = []
        self.backend.reconcile_note_ids = lambda note_ids, **_kwargs: (
            targeted.append(tuple(note_ids)),
            True,
        )[1]

        addon.schedule_reconcile(note_ids=(909,))
        self.assertTrue(addon._reconcile_full)
        addon._run_reconcile()
        self.assertEqual(targeted, [(909,)])
        self.assertEqual(addon._pending_reconcile_note_ids, set())
        self.assertTrue(addon._reconcile_full)

        # The exact worker can be canceled without an error callback when the
        # reviewer opens. The retained full fallback still runs afterward.
        addon._review_active = True
        addon._run_reconcile()
        full_calls = []
        addon._review_active = False
        self.backend.reconcile = lambda **kwargs: (
            full_calls.append(kwargs),
            True,
        )[1]
        addon._run_reconcile()
        self.assertEqual(len(full_calls), 1)
        self.assertFalse(full_calls[0]["check_fingerprint"])

    def test_failed_journal_enqueue_registers_fallback_before_gate_opens(
        self,
    ) -> None:
        self.backend.deactivate_profile()
        backend = _DeferredCollectionBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        self.backend = backend
        backend.activate_profile(auto_rebuild=False)
        context = backend._context
        assert context is not None
        addon = controller.SmartSearchAddonController(
            backend.mw,
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = backend
        addon._reconcile_timer = _FakeTimer()
        queued_main_callbacks = []
        addon._run_on_main = queued_main_callbacks.append

        addon.schedule_reconcile(note_ids=(909,))
        self.assertTrue(backend._lexical_work_pending(context))
        persist = backend.pending_query_ops.pop()
        persist["failure"](RuntimeError("disk unavailable"))

        self.assertFalse(backend._lexical_work_pending(context))
        self.assertTrue(addon._reconcile_full)
        self.assertEqual(len(queued_main_callbacks), 1)

    def test_older_reconcile_success_cannot_erase_newer_full_fallback(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="smart_search_medical",
        )
        addon.backend = self.backend
        addon._reconcile_timer = _FakeTimer()
        addon._run_on_main = lambda callback: callback()
        with addon._reconcile_request_lock:
            addon._reconcile_full = True
            addon._reconcile_intent_epoch += 1
            old_epoch = addon._reconcile_intent_epoch

        addon._maintenance_intent_saved(False, initial_check=False)
        addon._background_reconcile_succeeded(
            self.backend.get_status(),
            clear_full=True,
            intent_epoch=old_epoch,
        )

        self.assertGreater(addon._reconcile_intent_epoch, old_epoch)
        self.assertTrue(addon._reconcile_full)

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

    def test_native_update_availability_uses_the_running_module(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="677438639",
        )
        with patch.object(
            updater,
            "native_update_available",
            return_value=True,
        ) as available:
            self.assertTrue(addon._native_update_available())

        available.assert_called_once_with(addon.mw, "677438639")

    def test_bundle_update_gate_is_atomic_and_blocks_new_writers(self) -> None:
        backend = controller.AnkiSearchBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="677438639",
        )
        backend._background_op_count = 1
        self.assertFalse(backend.begin_bundle_update())
        backend._background_op_count = 0

        self.assertTrue(backend.begin_bundle_update())
        self.assertTrue(backend.bundle_update_running)
        self.assertFalse(backend._begin_maintenance())
        self.assertTrue(backend._new_cancellation().is_set())
        errors = []
        self.assertIsNone(backend.install_semantic(on_error=errors.append))
        self.assertIn("Restart Anki", errors[-1])

        backend.finish_bundle_update()
        self.assertFalse(backend.bundle_update_running)
        self.assertTrue(backend._begin_maintenance())
        backend._end_maintenance()

    def test_query_op_holds_update_gate_until_callback_finishes(self) -> None:
        backend = controller.AnkiSearchBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="677438639",
        )
        pending = []

        class QueryOp:
            def __init__(self, *, parent, op, success):
                del parent
                self.op = op
                self.success = success
                self.failure_callback = None

            def failure(self, callback):
                self.failure_callback = callback
                return self

            def without_collection(self):
                return self

            def run_in_background(self):
                pending.append(self)

        aqt_module = types.ModuleType("aqt")
        aqt_module.__path__ = []
        operations_module = types.ModuleType("aqt.operations")
        operations_module.QueryOp = QueryOp
        callback_gate_attempts = []
        with patch.dict(
            sys.modules,
            {"aqt": aqt_module, "aqt.operations": operations_module},
        ):
            backend._run_query_op(
                uses_collection=False,
                op=lambda _collection: "done",
                success=lambda _result: callback_gate_attempts.append(
                    backend.begin_bundle_update()
                ),
                failure=lambda error: self.fail(str(error)),
            )
            self.assertFalse(backend.begin_bundle_update())
            operation = pending.pop()
            operation.success(operation.op(None))

        self.assertEqual(callback_gate_attempts, [False])
        self.assertTrue(backend.begin_bundle_update())

    def test_bundle_update_quiesce_closes_profile_owned_files(self) -> None:
        backend = _SynchronousBackend(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="677438639",
        )
        closed = []
        engine = types.SimpleNamespace(semantic_provider=object())
        index = types.SimpleNamespace(close=lambda: closed.append("lexical"))
        vector_index = types.SimpleNamespace(
            close=lambda: closed.append("semantic")
        )
        backend._context = controller._ProfileContext(
            token=backend._profile_token,
            key="profile",
            profile_name="Profile",
            collection_path="/tmp/collection.anki2",
            index=index,
            engine=engine,
            semantic=types.SimpleNamespace(index=vector_index),
        )
        ready = []
        errors = []

        self.assertTrue(backend.begin_bundle_update())
        backend.quiesce_for_bundle_update(
            on_ready=lambda: ready.append(True),
            on_error=errors.append,
        )

        self.assertEqual(errors, [])
        self.assertEqual(ready, [True])
        self.assertEqual(closed, ["lexical", "semantic"])
        self.assertIsNone(backend._context)
        self.assertIsNone(engine.semantic_provider)
        self.assertTrue(backend.bundle_update_running)

    def test_update_check_coalesces_and_reports_up_to_date(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="677438639",
        )
        pending = []
        messages = []
        info = []
        addon._show_message = messages.append
        addon._show_info = info.append

        def start(_parent, _mw, _module, on_done):
            pending.append(on_done)
            return "targeted"

        with patch.object(updater, "request_native_update", side_effect=start):
            addon._check_for_updates()
            addon._check_for_updates()
            self.assertTrue(addon._update_check_running)
            self.assertEqual(len(pending), 1)
            self.assertIn("already checking", messages[-1])
            pending[0]([])

        self.assertFalse(addon._update_check_running)
        self.assertEqual(info, ["Smart Search is up to date."])

    def test_completed_update_uses_anki_native_result_ui(self) -> None:
        mw = _MainWindow()
        completed = []
        mw.on_updates_installed = lambda log: completed.append(list(log))
        addon = controller.SmartSearchAddonController(
            mw,
            bundle_root=self.bundle,
            addon_module="677438639",
        )
        entry = (677438639, object())

        def finish(_parent, _mw, _module, on_done):
            on_done([entry])
            return "targeted"

        with (
            patch.object(updater, "request_native_update", side_effect=finish),
            patch.object(
                updater,
                "native_update_was_installed",
                return_value=True,
            ),
        ):
            addon._check_for_updates()

        self.assertTrue(addon._update_check_running)
        self.assertTrue(addon.backend.bundle_update_running)
        self.assertEqual(completed, [[entry]])

    def test_update_waits_for_index_maintenance_to_finish(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="677438639",
        )
        addon.backend._maintenance_running = True
        info = []
        addon._show_info = info.append
        with patch.object(updater, "request_native_update") as request:
            addon._check_for_updates()

        request.assert_not_called()
        self.assertFalse(addon._update_check_running)
        self.assertIn("wait", info[0].lower())

    def test_synchronous_update_failure_resets_busy_state(self) -> None:
        addon = controller.SmartSearchAddonController(
            _MainWindow(),
            bundle_root=self.bundle,
            addon_module="677438639",
        )
        errors = []
        addon._show_error = errors.append
        with patch.object(
            updater,
            "request_native_update",
            side_effect=RuntimeError("native updater unavailable"),
        ):
            addon._check_for_updates()

        self.assertFalse(addon._update_check_running)
        self.assertEqual(errors, ["native updater unavailable"])

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
