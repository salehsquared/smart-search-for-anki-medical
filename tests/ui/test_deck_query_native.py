from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ui.deck_query import build_deck_clause

try:
    from anki.collection import Collection
except ImportError as error:  # The plain-Python suite intentionally omits Anki.
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


@unittest.skipIf(
    IMPORT_ERROR is not None,
    f"Anki runtime unavailable: {IMPORT_ERROR}",
)
class NativeDeckQueryTests(unittest.TestCase):
    def test_parent_scope_can_exclude_one_subdeck_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            collection = Collection(str(Path(directory) / "probe.anki2"))
            try:
                model = collection.models.current()
                self.assertIsNotNone(model)
                assert model is not None
                cards: dict[str, int] = {}
                for name in (
                    "Parent",
                    "Parent::Child A",
                    "Parent::Child A::Grandchild",
                    "Parent::Child B",
                    "Other",
                ):
                    deck_id = collection.decks.id(name)
                    self.assertIsNotNone(deck_id)
                    assert deck_id is not None
                    note = collection.new_note(model)
                    note.fields[0] = name
                    note.fields[1] = "answer"
                    collection.add_note(note, deck_id)
                    cards[name] = tuple(collection.card_ids_of_note(note.id))[0]

                clause = build_deck_clause(
                    ("Parent", "Other"),
                    ("Parent::Child A",),
                )
                reverse = {card_id: name for name, card_id in cards.items()}
                matches = {
                    reverse[card_id] for card_id in collection.find_cards(clause)
                }

                self.assertEqual(matches, {"Parent", "Parent::Child B", "Other"})
            finally:
                collection.close()


if __name__ == "__main__":
    unittest.main()
