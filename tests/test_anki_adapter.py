from __future__ import annotations

import importlib.util
import hashlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_smart_search_anki_adapter_tests"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    package.__package__ = PACKAGE
    sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.anki_adapter",
    ROOT / "anki_adapter.py",
)
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)


class NotFoundError(Exception):
    """Plain-Python stand-in for anki.errors.NotFoundError."""


class _Note:
    def __init__(
        self,
        note_id: int,
        *,
        fields: tuple[tuple[str, str], ...],
        tags: tuple[str, ...] = (),
        note_type: str = "Basic",
        modified: int = 0,
        guid: str = "",
    ) -> None:
        self.id = note_id
        self.tags = list(tags)
        self.mod = modified
        self.guid = guid
        self._fields = fields
        self._note_type = note_type

    def items(self):
        return self._fields

    def note_type(self):
        return {"name": self._note_type}


class _Card:
    def __init__(
        self,
        *,
        did: int,
        odid: int = 0,
        nid: int = 0,
        queue: int = 0,
        flag: int = 0,
    ) -> None:
        self.did = did
        self.odid = odid
        self.nid = nid
        self.queue = queue
        self._flag = flag

    def user_flag(self) -> int:
        return self._flag


class _Decks:
    def __init__(self, names: dict[int, str]) -> None:
        self.names = names
        self.lookups: list[int] = []

    def name(self, deck_id: int) -> str:
        self.lookups.append(deck_id)
        return self.names.get(deck_id, "")


class _Collection:
    def __init__(
        self,
        *,
        notes: dict[int, _Note],
        cards_by_note: dict[int, tuple[int, ...]] | None = None,
        cards: dict[int, _Card] | None = None,
        deck_names: dict[int, str] | None = None,
        query_cards: dict[str, tuple[int, ...]] | None = None,
    ) -> None:
        self.notes = notes
        self.cards_by_note = cards_by_note or {}
        self.cards = cards or {}
        self.decks = _Decks(deck_names or {})
        self.query_cards = query_cards or {}
        self.queries: list[str] = []
        self.note_lookups: list[int] = []
        self.card_id_lookups: list[int] = []
        self.card_lookups: list[int] = []

    def get_note(self, note_id: int):
        self.note_lookups.append(note_id)
        if note_id not in self.notes:
            raise NotFoundError(str(note_id))
        return self.notes[note_id]

    def card_ids_of_note(self, note_id: int):
        self.card_id_lookups.append(note_id)
        return self.cards_by_note.get(note_id, ())

    def get_card(self, card_id: int):
        self.card_lookups.append(card_id)
        if card_id not in self.cards:
            raise NotFoundError(str(card_id))
        return self.cards[card_id]

    def find_cards(self, query: str):
        self.queries.append(query)
        return self.query_cards.get(query, ())


class TargetedAnkiReaderTests(unittest.TestCase):
    @staticmethod
    def _paged_snapshot_collection():
        class _SnapshotDB:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[int, ...]]] = []
                self.notes = (
                    (1, "g1", 100, 10, " tag1 ", "one\x1ffirst"),
                    (2, "g2", 100, 20, " tag2 ", "two\x1fsecond"),
                    (3, "g3", 100, 30, "", "three\x1fthird"),
                )
                self.cards = (
                    (11, 1, 99, 10),
                    (12, 1, 20, 0),
                    (13, 2, 20, 0),
                )

            def first(self, sql):
                if "count(*) FROM notes" not in sql:
                    raise AssertionError(sql)
                return (len(self.notes),)

            def all(self, sql, *args):
                self.calls.append((sql, tuple(int(value) for value in args)))
                after, limit = (int(args[0]), int(args[1]))
                rows = self.cards if "FROM cards" in sql else self.notes
                return tuple(row for row in rows if int(row[0]) > after)[:limit]

        class _Models:
            @staticmethod
            def get(model_id):
                if int(model_id) != 100:
                    return None
                return {
                    "name": "Basic",
                    "flds": [{"name": "Front"}, {"name": "Back"}],
                }

        class _Decks:
            @staticmethod
            def all_names_and_ids(*, include_filtered):
                if not include_filtered:
                    raise AssertionError("filtered decks were unexpectedly excluded")
                return (
                    types.SimpleNamespace(id=10, name="Medicine"),
                    types.SimpleNamespace(id=20, name="Pharmacology"),
                    types.SimpleNamespace(id=99, name="Temporary filtered"),
                )

        database = _SnapshotDB()
        collection = types.SimpleNamespace(
            db=database,
            models=_Models(),
            decks=_Decks(),
            tags=types.SimpleNamespace(
                split=lambda value: tuple(str(value).strip().split())
            ),
        )
        return collection, database

    def test_full_snapshot_uses_bounded_keyset_pages(self) -> None:
        collection, database = self._paged_snapshot_collection()
        progress = []
        anki = types.ModuleType("anki")
        utils = types.ModuleType("anki.utils")
        utils.split_fields = lambda value: str(value).split("\x1f")
        anki.utils = utils

        with patch.dict(sys.modules, {"anki": anki, "anki.utils": utils}):
            notes = adapter.AnkiCollectionReader().snapshot(
                collection,
                page_size=2,
                progress=lambda done, total: progress.append((done, total)),
            )

        self.assertEqual(tuple(note.note_id for note in notes), (1, 2, 3))
        self.assertEqual(notes[0].decks, ("Medicine", "Pharmacology"))
        self.assertEqual(notes[0].card_ids, (11, 12))
        self.assertEqual(dict(notes[1].fields), {"Front": "two", "Back": "second"})
        self.assertEqual(progress[-1], (3, 3))
        self.assertEqual(len(database.calls), 4)
        for sql, args in database.calls:
            self.assertIn("WHERE id > ?", sql)
            self.assertIn("LIMIT ?", sql)
            self.assertEqual(args[1], 2)

    def test_full_snapshot_cancels_between_collection_pages(self) -> None:
        collection, database = self._paged_snapshot_collection()
        checkpoints = 0
        anki = types.ModuleType("anki")
        utils = types.ModuleType("anki.utils")
        utils.split_fields = lambda value: str(value).split("\x1f")
        anki.utils = utils

        def cancel() -> None:
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 4:
                raise RuntimeError("review started")

        with (
            patch.dict(sys.modules, {"anki": anki, "anki.utils": utils}),
            self.assertRaisesRegex(RuntimeError, "review started"),
        ):
            adapter.AnkiCollectionReader().snapshot(
                collection,
                page_size=2,
                cancel_check=cancel,
            )

        self.assertEqual(len(database.calls), 1)
        self.assertIn("FROM cards", database.calls[0][0])

    def test_deck_catalog_uses_public_api_and_marks_current_deck(self) -> None:
        class _CatalogDecks:
            def __init__(self) -> None:
                self.include_filtered_calls: list[bool] = []

            def all_names_and_ids(self, *, include_filtered: bool):
                self.include_filtered_calls.append(include_filtered)
                rows = (
                    types.SimpleNamespace(id=1, name="Default"),
                    types.SimpleNamespace(id=20, name="Medicine"),
                    types.SimpleNamespace(id=21, name="Medicine::Cardiology"),
                    types.SimpleNamespace(id=30, name="Filtered review"),
                    types.SimpleNamespace(id=20, name="duplicate id"),
                    types.SimpleNamespace(id=0, name="invalid"),
                )
                if include_filtered:
                    return rows
                return tuple(item for item in rows if item.id != 30)

            def get_current_id(self) -> int:
                return 21

        decks = _CatalogDecks()
        collection = types.SimpleNamespace(decks=decks)

        catalog = adapter.AnkiCollectionReader.deck_catalog(collection)

        self.assertIsInstance(catalog.decks, tuple)
        self.assertEqual(decks.include_filtered_calls, [False, True])
        self.assertEqual(
            tuple((deck.deck_id, deck.name) for deck in catalog.decks),
            (
                (1, "Default"),
                (20, "Medicine"),
                (21, "Medicine::Cardiology"),
                (30, "Filtered review"),
            ),
        )
        self.assertEqual(catalog.current_deck_id, 21)
        self.assertEqual(catalog.current_deck, catalog.decks[2])
        self.assertEqual(catalog.entries, catalog.decks)
        self.assertFalse(catalog.decks[2].filtered)
        self.assertTrue(catalog.decks[3].filtered)

    def test_deck_catalog_falls_back_to_current_deck_object(self) -> None:
        class _LegacyDecks:
            def all_names_and_ids(self, *, include_filtered: bool):
                self.include_filtered = include_filtered
                return (types.SimpleNamespace(id=7, name="Legacy"),)

            def current(self):
                return {"id": "7", "name": "Legacy"}

        decks = _LegacyDecks()

        catalog = adapter.AnkiCollectionReader.deck_catalog(
            types.SimpleNamespace(decks=decks)
        )

        self.assertTrue(decks.include_filtered)
        self.assertEqual(catalog.current_deck_id, 7)
        self.assertEqual(catalog.current_deck.name, "Legacy")

    def test_preview_card_scope_is_positive_ordered_and_deduplicated(self) -> None:
        result = types.SimpleNamespace(card_ids=(4, "5", 4, 0, -1, "bad"))

        self.assertEqual(adapter.preview_card_ids(result), (4, 5))
        self.assertEqual(adapter.preview_card_ids(None), ())

    def test_native_previewer_tracks_exact_siblings_and_result_navigation(
        self,
    ) -> None:
        class _Signal:
            def __init__(self) -> None:
                self.callbacks = []

            def connect(self, callback) -> None:
                self.callbacks.append(callback)

            def emit(self) -> None:
                for callback in tuple(self.callbacks):
                    callback()

        class _Shortcut:
            def __init__(self, sequence, parent) -> None:
                self.sequence = sequence
                self.parent = parent
                self.activated = _Signal()

        class _Previewer:
            def __init__(self, parent, mw, on_close) -> None:
                self.parent = parent
                self.mw = mw
                self._close_callback = on_close
                self._web = None
                self._state = "question"
                self._last_state = None
                self.render_count = 0
                self.button_update_count = 0
                self.title = ""

            def _create_gui(self) -> None:
                self.close_shortcut = _Shortcut("Ctrl+Shift+P", self)
                self.close_shortcut.activated.connect(self.close)

            def open(self) -> None:
                self._create_gui()
                self._web = object()
                self.render_card()

            def render_card(self) -> None:
                self.render_count += 1
                self.card_changed()
                self.card()

            def _render_scheduled(self) -> None:
                self.card_changed()
                self.card()

            def _updateButtons(self) -> None:
                self.button_update_count += 1

            def cancel_timer(self) -> None:
                return None

            def close(self) -> None:
                self._on_finished(0)

            def setWindowTitle(self, title: str) -> None:
                self.title = title

            def _should_enable_prev(self) -> bool:
                return False

            def _should_enable_next(self) -> bool:
                return False

            def _on_close(self) -> None:
                self._web = None
                self._close_callback()

        restored: list[str] = []
        saved: list[str] = []
        previous_moves: list[bool] = []
        next_moves: list[bool] = []
        closed: list[bool] = []
        cards = {
            11: object(),
            12: object(),
            21: object(),
        }
        mw = types.SimpleNamespace(
            col=types.SimpleNamespace(get_card=lambda card_id: cards[card_id])
        )

        aqt = types.ModuleType("aqt")
        browser = types.ModuleType("aqt.browser")
        previewer_module = types.ModuleType("aqt.browser.previewer")
        previewer_module.MultiCardPreviewer = _Previewer
        qt = types.ModuleType("aqt.qt")
        qt.QKeySequence = lambda value: value
        qt.QShortcut = _Shortcut
        qt.qconnect = lambda signal, callback: signal.connect(callback)
        utils = types.ModuleType("aqt.utils")
        utils.restoreGeom = lambda _widget, key: restored.append(key)
        utils.saveGeom = lambda _widget, key: saved.append(key)

        replacements = {
            "aqt": aqt,
            "aqt.browser": browser,
            "aqt.browser.previewer": previewer_module,
            "aqt.qt": qt,
            "aqt.utils": utils,
        }
        original = {name: sys.modules.get(name) for name in replacements}
        sys.modules.update(replacements)
        try:
            preview = adapter.create_result_previewer(
                mw,
                initial_result=types.SimpleNamespace(
                    note_id=1,
                    card_ids=(11, 12, 11),
                ),
                on_close=lambda: closed.append(True),
                on_previous=lambda: previous_moves.append(True) or True,
                on_next=lambda: next_moves.append(True) or True,
                has_previous=lambda: False,
                has_next=lambda: True,
            )
            preview.open()

            self.assertEqual(preview.card(), cards[11])
            self.assertEqual(preview.title, "Preview · Card 1 of 2")
            self.assertEqual(restored, ["smartSearchPreview"])

            preview._on_next_card()
            self.assertEqual(preview.card(), cards[12])
            self.assertEqual(preview.title, "Preview · Card 2 of 2")
            self.assertEqual(next_moves, [])
            preview._on_next_card()
            self.assertEqual(next_moves, [True])

            preview._on_prev_card()
            self.assertEqual(preview.card(), cards[11])
            self.assertEqual(previous_moves, [])
            preview._up_result_shortcut.activated.emit()
            preview._down_result_shortcut.activated.emit()
            self.assertEqual(previous_moves, [True])
            self.assertEqual(next_moves, [True, True])
            expected_preview_sequence = (
                "Meta+Shift+P"
                if sys.platform == "darwin"
                else "Ctrl+Shift+P"
            )
            self.assertEqual(
                preview._toggle_preview_shortcut.sequence,
                expected_preview_sequence,
            )

            preview.set_result(
                types.SimpleNamespace(note_id=2, card_ids=(21,))
            )
            self.assertEqual(preview.card(), cards[21])
            self.assertEqual(preview.title, "Preview")
            preview._render_scheduled()
            self.assertEqual(preview.button_update_count, 1)
            preview.set_result(
                types.SimpleNamespace(note_id=3, card_ids=(999,))
            )
            self.assertIsNone(preview.card())
            preview._render_scheduled()
            self.assertEqual(saved, ["smartSearchPreview"])
            self.assertEqual(closed, [True])
            preview._toggle_preview_shortcut.activated.emit()
            self.assertEqual(saved, ["smartSearchPreview", "smartSearchPreview"])
            self.assertEqual(closed, [True, True])
        finally:
            for name, module in original.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_reads_only_requested_live_ids_and_omits_deleted_ids(self) -> None:
        collection = _Collection(
            notes={
                11: _Note(11, fields=(("Front", "eleven"),)),
                22: _Note(22, fields=(("Front", "twenty-two"),)),
                33: _Note(33, fields=(("Front", "not requested"),)),
            }
        )

        notes = adapter.AnkiCollectionReader().snapshot_note_ids(
            collection,
            (22, 999, 22, 11, 0, -4, "invalid"),
        )

        self.assertEqual([note.note_id for note in notes], [22, 11])
        self.assertEqual(collection.note_lookups, [22, 999, 11])
        self.assertEqual(collection.card_id_lookups, [22, 11])

    def test_preserves_fields_note_metadata_cards_and_decks(self) -> None:
        collection = _Collection(
            notes={
                41: _Note(
                    41,
                    fields=(
                        ("Front", "<b>Bupropion</b>"),
                        ("Back", "NDRI"),
                    ),
                    tags=("Psychiatry", "Drugs"),
                    note_type="Cloze",
                    modified=1_234,
                    guid="guid-41",
                )
            },
            cards_by_note={41: (401, 402, 401)},
            cards={
                401: _Card(did=50, odid=60),
                402: _Card(did=70),
            },
            deck_names={
                50: "Filtered study",
                60: "AnKing",
                70: "Pharmacology",
            },
        )

        [note] = adapter.AnkiCollectionReader().snapshot_note_ids(collection, (41,))

        self.assertEqual(
            dict(note.fields),
            {"Front": "<b>Bupropion</b>", "Back": "NDRI"},
        )
        self.assertEqual(note.title, "<b>Bupropion</b>")
        self.assertEqual(note.tags, ("Psychiatry", "Drugs"))
        self.assertEqual(note.note_type, "Cloze")
        self.assertEqual(note.modified_seconds, 1_234)
        self.assertEqual(note.guid, "guid-41")
        self.assertEqual(note.card_ids, (401, 402))
        self.assertEqual(
            note.decks,
            ("AnKing", "Pharmacology"),
        )
        self.assertEqual(collection.card_lookups, [401, 402])

    def test_filtered_deck_uses_only_the_cards_canonical_home_deck(self) -> None:
        collection = _Collection(
            notes={51: _Note(51, fields=(("Front", "stable deck"),))},
            cards_by_note={51: (501,)},
            cards={501: _Card(did=99, odid=20)},
            deck_names={20: "Medicine", 99: "Temporary filtered deck"},
        )

        [note] = adapter.AnkiCollectionReader().snapshot_note_ids(collection, (51,))

        self.assertEqual(note.decks, ("Medicine",))
        self.assertEqual(collection.decks.lookups, [20])

    def test_bounded_hydration_rejects_accidental_oversized_batches(self) -> None:
        collection = _Collection(
            notes={
                1: _Note(1, fields=(("Front", "one"),)),
                2: _Note(2, fields=(("Front", "two"),)),
            }
        )
        reader = adapter.AnkiCollectionReader()

        notes = reader.snapshot_note_batch(
            collection,
            (2, 1, 2, 0),
            max_notes=2,
        )
        self.assertEqual(tuple(note.note_id for note in notes), (2, 1))
        with self.assertRaises(ValueError):
            reader.snapshot_note_batch(collection, (1, 2), max_notes=1)

    def test_targeted_hydration_honors_cancellation_between_notes(self) -> None:
        collection = _Collection(
            notes={
                1: _Note(1, fields=(("Front", "one"),)),
                2: _Note(2, fields=(("Front", "two"),)),
            }
        )
        checkpoints = 0

        def cancel() -> None:
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 2:
                raise RuntimeError("review started")

        with self.assertRaisesRegex(RuntimeError, "review started"):
            adapter.AnkiCollectionReader().snapshot_note_batch(
                collection,
                (1, 2),
                max_notes=2,
                cancel_check=cancel,
            )

        self.assertEqual(collection.note_lookups, [1])

    def test_compact_manifest_hashes_bounded_note_content(self) -> None:
        class _ManifestDB:
            def __init__(self) -> None:
                self.calls = []

            def all(self, sql, *args):
                self.calls.append((sql, args))
                return (
                    (11, 101, -1, 7, 120, 18, 999, "front\x1fback", " foo "),
                    (15, 102, 4, 8, 80, 0, 123, "other", ""),
                )

        database = _ManifestDB()
        collection = types.SimpleNamespace(db=database)

        manifest = adapter.AnkiCollectionReader.note_manifest_batch(
            collection,
            after_note_id=10,
            limit=2,
        )

        self.assertEqual(tuple(entry.note_id for entry in manifest), (11, 15))
        self.assertEqual(
            manifest[0].signature,
            (
                101,
                -1,
                7,
                120,
                18,
                999,
                hashlib.blake2b(b"front\x1fback", digest_size=16).digest(),
                hashlib.blake2b(b" foo ", digest_size=16).digest(),
            ),
        )
        self.assertEqual(database.calls[0][1], (10, 2))
        query = database.calls[0][0].casefold()
        self.assertIn("flds", query)
        self.assertIn("tags", query)
        self.assertNotIn("front\x1fback", repr(manifest))

    def test_card_manifest_excludes_scheduling_and_uses_home_deck(self) -> None:
        class _ManifestDB:
            def __init__(self) -> None:
                self.calls = []

            def all(self, sql, *args):
                self.calls.append((sql, args))
                # SQL computes odid-or-did; this is the resulting stable row.
                return ((501, 51, 20),)

        database = _ManifestDB()
        manifest = adapter.AnkiCollectionReader.card_manifest_batch(
            types.SimpleNamespace(db=database),
            after_card_id=500,
            limit=25,
        )

        self.assertEqual(manifest, ((501, 51, 20),))
        self.assertEqual(manifest[0].signature, (51, 20))
        self.assertEqual(database.calls[0][1], (500, 25))
        query = database.calls[0][0].casefold()
        for scheduling_column in ("queue", "due", "ivl", "reps", "flags"):
            self.assertNotIn(scheduling_column, query)

    def test_exact_manifest_ids_are_chunked_ordered_and_omit_deleted(self) -> None:
        class _ManifestDB:
            def __init__(self) -> None:
                self.calls = []

            def all(self, _sql, *args):
                self.calls.append(args)
                rows = {
                    11: (11, 101, -1, 7, 120, 18, 999, "one", " a "),
                    15: (15, 102, 4, 8, 80, 0, 123, "two", " b "),
                }
                # Deliberately reverse database order; the API restores the
                # exact caller order and omits nonexistent ID 13.
                return tuple(rows[value] for value in reversed(args) if value in rows)

        database = _ManifestDB()
        manifest = adapter.AnkiCollectionReader.note_manifest_for_ids(
            types.SimpleNamespace(db=database),
            (15, 13, 11, 15, 0),
        )

        self.assertEqual(tuple(entry.note_id for entry in manifest), (15, 11))
        self.assertEqual(database.calls, [(15, 13, 11)])

    def test_all_missing_ids_return_an_empty_snapshot(self) -> None:
        collection = _Collection(notes={})

        notes = adapter.AnkiCollectionReader().snapshot_note_ids(
            collection,
            (1001, 1002),
        )

        self.assertEqual(notes, [])
        self.assertEqual(collection.note_lookups, [1001, 1002])
        self.assertEqual(collection.card_id_lookups, [])

    def test_native_filter_scope_retains_only_matching_sibling_cards(self) -> None:
        collection = _Collection(
            notes={},
            cards={
                401: _Card(did=1, nid=41),
                402: _Card(did=2, nid=41),
                501: _Card(did=1, nid=50),
            },
            query_cards={"is:suspended": (402, 501)},
        )

        scope = adapter.AnkiCollectionReader.card_ids_by_note_for_query(
            collection,
            "is:suspended",
        )

        self.assertEqual(scope, {41: (402,), 50: (501,)})
        self.assertEqual(collection.queries, ["is:suspended"])

    def test_card_state_refresh_honors_an_exact_card_scope(self) -> None:
        collection = _Collection(
            notes={},
            cards_by_note={41: (401, 402)},
            cards={
                401: _Card(did=1, nid=41, queue=-1, flag=1),
                402: _Card(did=1, nid=41, queue=2, flag=4),
            },
        )

        states = adapter.AnkiCollectionReader.card_states_for_notes(
            collection,
            (41,),
            card_ids_by_note={41: (401,)},
        )

        self.assertEqual(states, {41: ((401, 1, True, False),)})
        self.assertEqual(collection.card_lookups, [401])

    def test_card_state_distinguishes_buried_from_suspended(self) -> None:
        collection = _Collection(
            notes={},
            cards_by_note={41: (401, 402, 403, 404)},
            cards={
                401: _Card(did=1, nid=41, queue=-1),
                402: _Card(did=1, nid=41, queue=-2),
                403: _Card(did=1, nid=41, queue=-3),
                404: _Card(did=1, nid=41, queue=2),
            },
        )

        states = adapter.AnkiCollectionReader.card_states_for_notes(
            collection,
            (41,),
        )

        self.assertEqual(
            states[41],
            (
                (401, 0, True, False),
                (402, 0, False, True),
                (403, 0, False, True),
                (404, 0, False, False),
            ),
        )


if __name__ == "__main__":
    unittest.main()
