from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


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
                return None

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
            ("Filtered study", "AnKing", "Pharmacology"),
        )
        self.assertEqual(collection.card_lookups, [401, 402])

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

        self.assertEqual(states, {41: ((401, 1, True),)})
        self.assertEqual(collection.card_lookups, [401])


if __name__ == "__main__":
    unittest.main()
