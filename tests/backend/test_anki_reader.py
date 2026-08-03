from __future__ import annotations

import unittest

from backend.anki_reader import CollectionFilterResolver, iter_collection_notes


class FakeNote:
    id = 1001
    tags = ["Psychiatry"]
    mod = 123
    guid = "abc"

    def items(self):
        return [("Text", "{{c1::Bupropion}}")]

    def note_type(self):
        return {"name": "Cloze"}


class FakeCard:
    did = 50
    odid = 0
    nid = 1001

    def current_deck_id(self):
        return self.did


class FakeDecks:
    def name(self, deck_id):
        return "AnKing" if deck_id == 50 else ""


class FakeCollection:
    decks = FakeDecks()

    def __init__(self):
        self.queries = []

    def find_notes(self, query):
        self.queries.append(query)
        return [1001]

    def find_cards(self, query):
        self.queries.append(query)
        return [2001]

    def get_note(self, note_id):
        self.assert_read_id = note_id
        return FakeNote()

    def card_ids_of_note(self, note_id):
        return [2001]

    def get_card(self, card_id):
        return FakeCard()

    # Any accidental mutation would fail the test immediately.
    def update_note(self, *_):
        raise AssertionError("collection write attempted")

    def remove_notes(self, *_):
        raise AssertionError("collection write attempted")


class AnkiReaderTests(unittest.TestCase):
    def test_public_read_api_snapshot(self) -> None:
        collection = FakeCollection()
        notes = list(iter_collection_notes(collection))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note_id, 1001)
        self.assertEqual(notes[0].card_ids, (2001,))
        self.assertEqual(notes[0].decks, ("AnKing",))
        self.assertEqual(notes[0].title, "Bupropion")

    def test_filter_resolver_uses_card_aware_native_search(self) -> None:
        collection = FakeCollection()
        resolver = CollectionFilterResolver(collection)
        self.assertEqual(resolver("tag:Psychiatry"), (1001,))
        self.assertEqual(collection.queries, ["tag:Psychiatry"])

    def test_filtered_card_indexes_its_stable_home_deck(self) -> None:
        collection = FakeCollection()
        collection.decks = type(
            "Decks",
            (),
            {
                "name": lambda _self, deck_id: {
                    50: "Filtered",
                    60: "Home",
                }.get(deck_id, "")
            },
        )()
        card = FakeCard()
        card.did = 50
        card.odid = 60
        collection.get_card = lambda _card_id: card

        [note] = iter_collection_notes(collection)

        self.assertEqual(note.decks, ("Home",))


if __name__ == "__main__":
    unittest.main()
