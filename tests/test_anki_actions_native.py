from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from anki_actions import ActionKind, CollectionAction, execute_collection_action

try:
    from anki.collection import Collection
except ImportError as error:  # Plain-Python CI intentionally omits Anki.
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


@unittest.skipIf(
    IMPORT_ERROR is not None,
    f"Anki runtime unavailable: {IMPORT_ERROR}",
)
class NativeCollectionActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.collection = Collection(
            str(Path(self.temporary.name) / "actions.anki2")
        )
        model = self.collection.models.current()
        self.assertIsNotNone(model)
        assert model is not None
        self.source_deck = int(self.collection.decks.id("Source") or 0)
        self.target_deck = int(self.collection.decks.id("Target") or 0)
        note = self.collection.new_note(model)
        note.fields[0] = "Question"
        note.fields[1] = "Answer"
        self.collection.add_note(note, self.source_deck)
        self.note_id = int(note.id)
        self.card_id = int(tuple(self.collection.card_ids_of_note(note.id))[0])

    def tearDown(self) -> None:
        self.collection.close()
        self.temporary.cleanup()

    def test_change_deck_and_undo_use_native_collection_state(self) -> None:
        outcome = execute_collection_action(
            self.collection,
            CollectionAction(
                ActionKind.CHANGE_DECK,
                note_ids=(self.note_id,),
                card_ids=(self.card_id,),
                deck_id=self.target_deck,
                deck_name="Target",
            ),
        )

        self.assertEqual(outcome.changed, 1)
        self.assertEqual(self.collection.get_card(self.card_id).did, self.target_deck)
        self.collection.undo()
        self.assertEqual(self.collection.get_card(self.card_id).did, self.source_deck)

    def test_manual_bury_and_undo_use_native_scheduler_state(self) -> None:
        outcome = execute_collection_action(
            self.collection,
            CollectionAction(ActionKind.BURY, card_ids=(self.card_id,)),
        )

        self.assertEqual(outcome.changed, 1)
        self.assertEqual(self.collection.get_card(self.card_id).queue, -3)
        self.collection.undo()
        self.assertNotIn(self.collection.get_card(self.card_id).queue, {-3, -2})

    def test_unbury_restores_both_native_bury_types_and_is_undoable(self) -> None:
        for manual, expected_queue in ((True, -3), (False, -2)):
            with self.subTest(manual=manual):
                self.collection.sched.bury_cards((self.card_id,), manual=manual)
                self.assertEqual(
                    self.collection.get_card(self.card_id).queue,
                    expected_queue,
                )
                outcome = execute_collection_action(
                    self.collection,
                    CollectionAction(
                        ActionKind.UNBURY,
                        card_ids=(self.card_id,),
                    ),
                )
                self.assertEqual(outcome.changed, 1)
                self.assertNotIn(
                    self.collection.get_card(self.card_id).queue,
                    {-3, -2},
                )
                self.collection.undo()
                self.assertEqual(
                    self.collection.get_card(self.card_id).queue,
                    expected_queue,
                )
                self.collection.sched.unbury_cards((self.card_id,))

    def test_unbury_never_unsuspends_a_native_suspended_card(self) -> None:
        self.collection.sched.suspend_cards((self.card_id,))
        self.assertEqual(self.collection.get_card(self.card_id).queue, -1)

        outcome = execute_collection_action(
            self.collection,
            CollectionAction(ActionKind.UNBURY, card_ids=(self.card_id,)),
        )

        self.assertEqual(outcome.changed, 0)
        self.assertEqual(self.collection.get_card(self.card_id).queue, -1)


if __name__ == "__main__":
    unittest.main()
