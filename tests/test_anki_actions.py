from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "_smart_search_anki_actions_tests"
spec = importlib.util.spec_from_file_location(
    MODULE_NAME,
    ROOT / "anki_actions.py",
)
assert spec and spec.loader
actions = importlib.util.module_from_spec(spec)
sys.modules[MODULE_NAME] = actions
spec.loader.exec_module(actions)


class NotFoundError(Exception):
    """Plain-Python stand-in for anki.errors.NotFoundError."""


class _Card:
    def __init__(
        self,
        *,
        nid: int,
        queue: int = 0,
        flag: int = 0,
    ) -> None:
        self.nid = nid
        self.queue = queue
        self.flags = flag
        self._flag = flag

    def user_flag(self) -> int:
        return self._flag


class _FlagsOnlyCard:
    """Older/card-like object without a user_flag() helper."""

    def __init__(self, *, nid: int, queue: int, flags: int) -> None:
        self.nid = nid
        self.queue = queue
        self.flags = flags


class _BackendResult:
    def __init__(
        self,
        *,
        changes: object,
        count: int | None = None,
    ) -> None:
        self.changes = changes
        if count is not None:
            self.count = count


class _Scheduler:
    def __init__(self) -> None:
        self.suspend_calls: list[tuple[int, ...]] = []
        self.unsuspend_calls: list[tuple[int, ...]] = []
        self.suspend_result: object = _BackendResult(
            changes="suspend-changes"
        )
        self.unsuspend_result: object = _BackendResult(
            changes="unsuspend-changes"
        )

    def suspend_cards(self, card_ids) -> object:
        self.suspend_calls.append(tuple(card_ids))
        return self.suspend_result

    def unsuspend_cards(self, card_ids) -> object:
        self.unsuspend_calls.append(tuple(card_ids))
        return self.unsuspend_result


class _Tags:
    def __init__(self) -> None:
        self.add_calls: list[tuple[tuple[int, ...], str]] = []
        self.remove_calls: list[tuple[tuple[int, ...], str]] = []
        self.add_result: object = _BackendResult(changes="add-changes")
        self.remove_result: object = _BackendResult(
            changes="remove-changes"
        )

    def bulk_add(self, note_ids, tags: str) -> object:
        self.add_calls.append((tuple(note_ids), tags))
        return self.add_result

    def bulk_remove(self, note_ids, tags: str) -> object:
        self.remove_calls.append((tuple(note_ids), tags))
        return self.remove_result


class _Collection:
    def __init__(
        self,
        *,
        cards: dict[int, object | None] | None = None,
        notes: dict[int, object | None] | None = None,
    ) -> None:
        self.cards = dict(cards or {})
        self.notes = dict(notes or {})
        self.card_lookups: list[int] = []
        self.note_lookups: list[int] = []
        self.flag_calls: list[tuple[int, tuple[int, ...]]] = []
        self.flag_result: object = _BackendResult(changes="flag-changes")
        self.sched = _Scheduler()
        self.tags = _Tags()

    def get_card(self, card_id: int) -> object | None:
        self.card_lookups.append(card_id)
        if card_id not in self.cards:
            raise NotFoundError(str(card_id))
        return self.cards[card_id]

    def get_note(self, note_id: int) -> object | None:
        self.note_lookups.append(note_id)
        if note_id not in self.notes:
            raise NotFoundError(str(note_id))
        return self.notes[note_id]

    def set_user_flag_for_cards(self, flag: int, card_ids) -> object:
        self.flag_calls.append((flag, tuple(card_ids)))
        return self.flag_result


def _outcome(
    kind,
    *,
    requested: int = 0,
    live: int = 0,
    eligible: int = 0,
    changed: int = 0,
    stale: int = 0,
    skipped: int = 0,
    note_count: int = 0,
):
    return actions.ActionOutcome(
        changes=object(),
        kind=kind,
        requested=requested,
        live=live,
        eligible=eligible,
        changed=changed,
        stale=stale,
        skipped=skipped,
        note_count=note_count,
    )


class NormalizationAndValidationTests(unittest.TestCase):
    def test_normalize_ids_deduplicates_and_preserves_order(self) -> None:
        values = [
            "42",
            7,
            "42",
            0,
            -5,
            None,
            "not-an-id",
            9.9,
            7,
        ]

        self.assertEqual(actions.normalize_ids(values), (42, 7, 9))

    def test_normalize_tag_text_matches_browser_style_whitespace(self) -> None:
        self.assertEqual(
            actions.normalize_tag_text(
                " \t cardiology::HF\n\n urgent\vreviewed "
            ),
            "cardiology::HF urgent reviewed",
        )

    def test_result_ids_deduplicates_notes_and_sibling_cards(self) -> None:
        results = (
            types.SimpleNamespace(note_id="10", card_ids=(101, 102)),
            types.SimpleNamespace(note_id=11, card_ids=(102, "103")),
            types.SimpleNamespace(note_id=10, card_ids=None),
            types.SimpleNamespace(note_id=0),
        )

        self.assertEqual(
            actions.result_ids(results),
            ((10, 11), (101, 102, 103)),
        )

    def test_collection_action_normalizes_all_user_input(self) -> None:
        action = actions.CollectionAction(
            kind="flag",
            note_ids=("12", 12, -1),
            card_ids=("22", 22, 23, 0),
            flag="7",
            tags=" ignored\n text ",
        )

        self.assertIs(action.kind, actions.ActionKind.FLAG)
        self.assertEqual(action.note_ids, (12,))
        self.assertEqual(action.card_ids, (22, 23))
        self.assertEqual(action.flag, 7)
        self.assertEqual(action.tags, "ignored text")
        self.assertEqual(action.requested_count, 2)

    def test_tag_requested_count_uses_notes_not_cards(self) -> None:
        action = actions.CollectionAction(
            actions.ActionKind.ADD_TAGS,
            note_ids=(1, 2),
            card_ids=(10, 11, 12),
            tags="review",
        )

        self.assertEqual(action.requested_count, 2)

    def test_flag_validation_rejects_missing_or_out_of_range_values(self) -> None:
        for invalid in (None, -1, 8, "not-a-number"):
            with self.subTest(invalid=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    actions.CollectionAction(
                        actions.ActionKind.FLAG,
                        card_ids=(1,),
                        flag=invalid,
                    )

    def test_tag_validation_rejects_empty_normalized_text(self) -> None:
        for kind in (
            actions.ActionKind.ADD_TAGS,
            actions.ActionKind.REMOVE_TAGS,
        ):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(
                    ValueError, "at least one tag"
                ):
                    actions.CollectionAction(
                        kind,
                        note_ids=(1,),
                        tags=" \n\t ",
                    )

    def test_unknown_action_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            actions.CollectionAction("delete", note_ids=(1,))


class CardActionTests(unittest.TestCase):
    def execute(self, collection, action):
        with (
            patch.object(actions, "_suspended_queue", return_value=-1),
            patch.object(
                actions,
                "_is_not_found",
                side_effect=lambda error: isinstance(
                    error, NotFoundError
                ),
            ),
        ):
            return actions.execute_collection_action(collection, action)

    def test_flag_revalidates_filters_noops_and_calls_api_once(self) -> None:
        collection = _Collection(
            cards={
                10: _Card(nid=1, flag=0),
                11: _Card(nid=1, flag=3),
                12: _Card(nid=2, flag=3),
            }
        )
        collection.flag_result = _BackendResult(
            changes="flagged",
            count=1,
        )
        action = actions.CollectionAction(
            actions.ActionKind.FLAG,
            card_ids=(10, 11, 12, 404, 10),
            flag=3,
        )

        outcome = self.execute(collection, action)

        self.assertEqual(collection.card_lookups, [10, 11, 12, 404])
        self.assertEqual(collection.flag_calls, [(3, (10,))])
        self.assertEqual(outcome.changes, "flagged")
        self.assertEqual(outcome.requested, 4)
        self.assertEqual(outcome.live, 3)
        self.assertEqual(outcome.eligible, 1)
        self.assertEqual(outcome.changed, 1)
        self.assertEqual(outcome.stale, 1)
        self.assertEqual(outcome.skipped, 2)
        self.assertEqual(outcome.note_count, 1)

    def test_flag_reads_low_bits_when_user_flag_method_is_absent(self) -> None:
        collection = _Collection(
            cards={
                1: _FlagsOnlyCard(nid=1, queue=0, flags=0b1011),
                2: _FlagsOnlyCard(nid=2, queue=0, flags=0b1000),
            }
        )
        action = actions.CollectionAction(
            actions.ActionKind.FLAG,
            card_ids=(1, 2),
            flag=3,
        )

        outcome = self.execute(collection, action)

        self.assertEqual(collection.flag_calls, [(3, (2,))])
        self.assertEqual(outcome.skipped, 1)

    def test_suspend_filters_already_suspended_and_calls_once(self) -> None:
        collection = _Collection(
            cards={
                1: _Card(nid=10, queue=0),
                2: _Card(nid=10, queue=-1),
                3: _Card(nid=11, queue=-2),
                4: _Card(nid=11, queue=2),
            }
        )
        collection.sched.suspend_result = _BackendResult(
            changes="suspended",
            count=3,
        )
        action = actions.CollectionAction(
            actions.ActionKind.SUSPEND,
            card_ids=(1, 2, 3, 4),
        )

        outcome = self.execute(collection, action)

        self.assertEqual(collection.sched.suspend_calls, [(1, 3, 4)])
        self.assertEqual(collection.sched.unsuspend_calls, [])
        self.assertEqual(outcome.changes, "suspended")
        self.assertEqual(outcome.eligible, 3)
        self.assertEqual(outcome.changed, 3)
        self.assertEqual(outcome.skipped, 1)
        self.assertEqual(outcome.note_count, 2)

    def test_unsuspend_only_passes_suspended_cards_not_buried_cards(self) -> None:
        collection = _Collection(
            cards={
                1: _Card(nid=10, queue=-1),
                2: _Card(nid=10, queue=-2),  # sibling buried
                3: _Card(nid=11, queue=-3),  # manually buried
                4: _Card(nid=12, queue=0),
                5: _Card(nid=13, queue=2),
            }
        )
        collection.sched.unsuspend_result = _BackendResult(
            changes="unsuspended",
            count=1,
        )
        action = actions.CollectionAction(
            actions.ActionKind.UNSUSPEND,
            card_ids=(1, 2, 3, 4, 5),
        )

        outcome = self.execute(collection, action)

        self.assertEqual(collection.sched.unsuspend_calls, [(1,)])
        self.assertNotIn(2, collection.sched.unsuspend_calls[0])
        self.assertNotIn(3, collection.sched.unsuspend_calls[0])
        self.assertEqual(collection.sched.suspend_calls, [])
        self.assertEqual(outcome.eligible, 1)
        self.assertEqual(outcome.changed, 1)
        self.assertEqual(outcome.skipped, 4)
        self.assertEqual(outcome.note_count, 1)

    def test_all_noop_cards_return_without_mutation_call(self) -> None:
        collection = _Collection(
            cards={
                1: _Card(nid=10, queue=-1),
                2: _Card(nid=11, queue=-1),
            }
        )
        action = actions.CollectionAction(
            actions.ActionKind.SUSPEND,
            card_ids=(1, 2),
        )

        outcome = self.execute(collection, action)

        self.assertEqual(collection.sched.suspend_calls, [])
        self.assertEqual(outcome.requested, 2)
        self.assertEqual(outcome.live, 2)
        self.assertEqual(outcome.eligible, 0)
        self.assertEqual(outcome.changed, 0)
        self.assertEqual(outcome.stale, 0)
        self.assertEqual(outcome.skipped, 2)

    def test_stale_cards_are_skipped_and_never_sent_to_api(self) -> None:
        collection = _Collection(cards={1: None})
        action = actions.CollectionAction(
            actions.ActionKind.FLAG,
            card_ids=(1, 404),
            flag=2,
        )

        outcome = self.execute(collection, action)

        self.assertEqual(collection.flag_calls, [])
        self.assertEqual(outcome.requested, 2)
        self.assertEqual(outcome.live, 0)
        self.assertEqual(outcome.stale, 2)
        self.assertEqual(outcome.changed, 0)

    def test_unexpected_lookup_errors_are_not_misclassified_as_stale(self) -> None:
        collection = _Collection(cards={1: _Card(nid=1)})

        def fail(_card_id):
            raise RuntimeError("database unavailable")

        collection.get_card = fail
        action = actions.CollectionAction(
            actions.ActionKind.FLAG,
            card_ids=(1,),
            flag=2,
        )

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.execute(collection, action)


class TagActionTests(unittest.TestCase):
    def execute(self, collection, action):
        with patch.object(
            actions,
            "_is_not_found",
            side_effect=lambda error: isinstance(error, NotFoundError),
        ):
            return actions.execute_collection_action(collection, action)

    def test_add_tags_revalidates_notes_and_uses_one_bulk_call(self) -> None:
        collection = _Collection(notes={10: object(), 11: object()})
        collection.tags.add_result = _BackendResult(
            changes="tags-added",
            count=2,
        )
        action = actions.CollectionAction(
            actions.ActionKind.ADD_TAGS,
            note_ids=(10, 11, 404, 10),
            card_ids=(1, 2, 3),
            tags=" cardio \n urgent ",
        )

        outcome = self.execute(collection, action)

        self.assertEqual(collection.note_lookups, [10, 11, 404])
        self.assertEqual(
            collection.tags.add_calls,
            [((10, 11), "cardio urgent")],
        )
        self.assertEqual(collection.tags.remove_calls, [])
        self.assertEqual(outcome.changes, "tags-added")
        self.assertEqual(outcome.requested, 3)
        self.assertEqual(outcome.live, 2)
        self.assertEqual(outcome.eligible, 2)
        self.assertEqual(outcome.changed, 2)
        self.assertEqual(outcome.stale, 1)
        self.assertEqual(outcome.note_count, 2)

    def test_remove_tags_uses_one_bulk_call_and_fallback_count(self) -> None:
        collection = _Collection(notes={20: object(), 21: object()})
        # Anki APIs commonly return OpChanges directly, without a count.
        remove_changes = object()
        collection.tags.remove_result = remove_changes
        action = actions.CollectionAction(
            actions.ActionKind.REMOVE_TAGS,
            note_ids=(20, 21),
            tags="reviewed",
        )

        outcome = self.execute(collection, action)

        self.assertEqual(
            collection.tags.remove_calls,
            [((20, 21), "reviewed")],
        )
        self.assertEqual(collection.tags.add_calls, [])
        self.assertIs(outcome.changes, remove_changes)
        self.assertEqual(outcome.changed, 2)
        self.assertEqual(outcome.note_count, 2)

    def test_all_stale_notes_return_without_bulk_mutation(self) -> None:
        collection = _Collection(notes={10: None})
        action = actions.CollectionAction(
            actions.ActionKind.ADD_TAGS,
            note_ids=(10, 404),
            tags="review",
        )

        outcome = self.execute(collection, action)

        self.assertEqual(collection.tags.add_calls, [])
        self.assertEqual(outcome.requested, 2)
        self.assertEqual(outcome.live, 0)
        self.assertEqual(outcome.eligible, 0)
        self.assertEqual(outcome.changed, 0)
        self.assertEqual(outcome.stale, 2)
        self.assertEqual(outcome.note_count, 0)


class MessageFormattingTests(unittest.TestCase):
    def test_changed_action_messages_include_exact_scope_and_undo(self) -> None:
        cases = (
            (
                actions.CollectionAction(
                    actions.ActionKind.FLAG,
                    card_ids=(1, 2),
                    flag=4,
                ),
                _outcome(
                    actions.ActionKind.FLAG,
                    changed=2,
                    note_count=2,
                ),
                "Set the blue flag on 2 cards from 2 notes"
                " — Undo available",
            ),
            (
                actions.CollectionAction(
                    actions.ActionKind.FLAG,
                    card_ids=(1,),
                    flag=0,
                ),
                _outcome(
                    actions.ActionKind.FLAG,
                    changed=1,
                    note_count=1,
                ),
                "Cleared flags on 1 card from 1 note — Undo available",
            ),
            (
                actions.CollectionAction(
                    actions.ActionKind.SUSPEND,
                    card_ids=(1, 2),
                ),
                _outcome(
                    actions.ActionKind.SUSPEND,
                    changed=2,
                    note_count=1,
                ),
                "Suspended 2 cards from 1 note — Undo available",
            ),
            (
                actions.CollectionAction(
                    actions.ActionKind.UNSUSPEND,
                    card_ids=(1,),
                ),
                _outcome(
                    actions.ActionKind.UNSUSPEND,
                    changed=1,
                    note_count=1,
                ),
                "Unsuspended 1 card from 1 note — Undo available",
            ),
            (
                actions.CollectionAction(
                    actions.ActionKind.ADD_TAGS,
                    note_ids=(1, 2),
                    tags="high yield",
                ),
                _outcome(actions.ActionKind.ADD_TAGS, changed=2),
                "Added “high yield” to 2 notes — Undo available",
            ),
            (
                actions.CollectionAction(
                    actions.ActionKind.REMOVE_TAGS,
                    note_ids=(1,),
                    tags="old",
                ),
                _outcome(actions.ActionKind.REMOVE_TAGS, changed=1),
                "Removed “old” from 1 note — Undo available",
            ),
        )

        for action, outcome, expected in cases:
            with self.subTest(kind=action.kind, flag=action.flag):
                self.assertEqual(
                    actions.format_action_message(action, outcome),
                    expected,
                )

    def test_message_reports_stale_and_skipped_reasons(self) -> None:
        action = actions.CollectionAction(
            actions.ActionKind.SUSPEND,
            card_ids=(1, 2, 3, 4),
        )
        outcome = _outcome(
            actions.ActionKind.SUSPEND,
            changed=1,
            stale=1,
            skipped=2,
            note_count=1,
        )

        self.assertEqual(
            actions.format_action_message(action, outcome),
            "Suspended 1 card from 1 note — Undo available"
            " · 1 no longer available · 2 already suspended",
        )

    def test_noop_unsuspend_message_explains_why_nothing_changed(self) -> None:
        action = actions.CollectionAction(
            actions.ActionKind.UNSUSPEND,
            card_ids=(1, 2, 3, 4),
        )
        outcome = _outcome(
            actions.ActionKind.UNSUSPEND,
            stale=1,
            skipped=3,
        )

        self.assertEqual(
            actions.format_action_message(action, outcome),
            "No selected cards needed changing"
            " · 1 no longer available · 3 not suspended",
        )

    def test_noop_tag_message_uses_note_scope(self) -> None:
        action = actions.CollectionAction(
            actions.ActionKind.REMOVE_TAGS,
            note_ids=(1,),
            tags="missing",
        )

        self.assertEqual(
            actions.format_action_message(
                action,
                _outcome(actions.ActionKind.REMOVE_TAGS),
            ),
            "No selected notes needed changing",
        )


class CollectionOpTests(unittest.TestCase):
    def test_start_uses_exactly_one_collection_op_and_initiator(self) -> None:
        collection = _Collection(notes={10: object(), 11: object()})
        action = actions.CollectionAction(
            actions.ActionKind.ADD_TAGS,
            note_ids=(10, 11),
            tags="review",
        )
        parent = object()
        initiator = object()
        successes: list[actions.ActionOutcome] = []
        failures: list[Exception] = []
        created: list[object] = []

        class FakeCollectionOp:
            def __init__(self, *, parent, op) -> None:
                self.parent = parent
                self.op = op
                self.success_callbacks = []
                self.failure_callbacks = []
                self.run_calls = []
                self.op_calls = 0
                created.append(self)

            def success(self, callback):
                self.success_callbacks.append(callback)
                return self

            def failure(self, callback):
                self.failure_callbacks.append(callback)
                return self

            def run_in_background(self, *, initiator):
                self.run_calls.append(initiator)
                self.op_calls += 1
                outcome = self.op(collection)
                self.success_callbacks[0](outcome)
                return "scheduled-operation"

        result = actions.start_collection_action(
            parent=parent,
            initiator=initiator,
            action=action,
            on_success=successes.append,
            on_failure=failures.append,
            collection_op_factory=FakeCollectionOp,
        )

        self.assertEqual(result, "scheduled-operation")
        self.assertEqual(len(created), 1)
        operation = created[0]
        self.assertIs(operation.parent, parent)
        self.assertEqual(operation.op_calls, 1)
        self.assertEqual(operation.run_calls, [initiator])
        self.assertEqual(len(operation.success_callbacks), 1)
        self.assertEqual(len(operation.failure_callbacks), 1)
        self.assertEqual(len(successes), 1)
        self.assertEqual(failures, [])
        self.assertEqual(
            collection.tags.add_calls,
            [((10, 11), "review")],
        )

    def test_start_wires_failure_callback_without_retrying_operation(
        self,
    ) -> None:
        action = actions.CollectionAction(
            actions.ActionKind.FLAG,
            card_ids=(1,),
            flag=1,
        )
        expected_error = RuntimeError("worker failed")
        successes = []
        failures = []
        created = []

        class FailingCollectionOp:
            def __init__(self, *, parent, op) -> None:
                del parent
                self.op = op
                self.success_callback = None
                self.failure_callback = None
                self.op_calls = 0
                self.run_calls = 0
                created.append(self)

            def success(self, callback):
                self.success_callback = callback
                return self

            def failure(self, callback):
                self.failure_callback = callback
                return self

            def run_in_background(self, *, initiator):
                self.run_calls += 1
                self.initiator = initiator
                self.op_calls += 1
                try:
                    self.op(
                        types.SimpleNamespace(
                            get_card=lambda _card_id: (_ for _ in ()).throw(
                                expected_error
                            )
                        )
                    )
                except Exception as error:
                    self.failure_callback(error)
                return self

        initiator = object()
        returned = actions.start_collection_action(
            parent=object(),
            initiator=initiator,
            action=action,
            on_success=successes.append,
            on_failure=failures.append,
            collection_op_factory=FailingCollectionOp,
        )

        self.assertIs(returned, created[0])
        self.assertEqual(created[0].run_calls, 1)
        self.assertEqual(created[0].op_calls, 1)
        self.assertIs(created[0].initiator, initiator)
        self.assertEqual(successes, [])
        self.assertEqual(failures, [expected_error])


if __name__ == "__main__":
    unittest.main()
