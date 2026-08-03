"""Undoable collection actions used by the Smart Search result toolbar.

This module is deliberately separate from :mod:`anki_adapter`, whose contract
is read-only. Every mutation below runs inside one Anki ``CollectionOp`` and
uses only public collection APIs. No direct SQLite or backend calls are used.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping
from typing import Any, Callable, Iterable, Sequence

try:  # installed add-on package
    from .backend.compat import dataclass
except ImportError:  # direct module loading in the plain-Python test suite
    from backend.compat import dataclass


class ActionKind(enum.Enum):
    FLAG = "flag"
    SUSPEND = "suspend"
    UNSUSPEND = "unsuspend"
    BURY = "bury"
    UNBURY = "unbury"
    CHANGE_DECK = "change_deck"
    ADD_TAGS = "add_tags"
    REMOVE_TAGS = "remove_tags"


def normalize_ids(values: Iterable[object]) -> tuple[int, ...]:
    """Return unique positive integer IDs while preserving input order."""

    output: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return tuple(output)


def normalize_tag_text(value: str) -> str:
    """Normalize Browser-style whitespace in a tag entry field."""

    return re.sub(r"[ \n\t\v]+", " ", str(value)).strip()


def result_ids(results: Sequence[object]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Extract de-duplicated note/card IDs from immutable search results."""

    note_ids: list[object] = []
    card_ids: list[object] = []
    for result in results:
        note_ids.append(getattr(result, "note_id", 0))
        card_ids.extend(getattr(result, "card_ids", ()) or ())
    return normalize_ids(note_ids), normalize_ids(card_ids)


@dataclass(frozen=True, slots=True)
class CollectionAction:
    """One user click and therefore one native Anki undo step."""

    kind: ActionKind
    note_ids: tuple[int, ...] = ()
    card_ids: tuple[int, ...] = ()
    flag: int | None = None
    tags: str = ""
    deck_id: int | None = None
    deck_name: str = ""

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, ActionKind):
            kind = ActionKind(kind)
            object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "note_ids", normalize_ids(self.note_ids))
        object.__setattr__(self, "card_ids", normalize_ids(self.card_ids))
        object.__setattr__(self, "tags", normalize_tag_text(self.tags))
        object.__setattr__(
            self,
            "deck_name",
            str(self.deck_name or "").strip(),
        )

        if kind is ActionKind.FLAG:
            if self.flag is None or not 0 <= int(self.flag) <= 7:
                raise ValueError("Anki card flags must be between 0 and 7.")
            object.__setattr__(self, "flag", int(self.flag))
        elif kind in (ActionKind.ADD_TAGS, ActionKind.REMOVE_TAGS):
            if not self.tags:
                raise ValueError("Enter at least one tag.")
        elif kind is ActionKind.CHANGE_DECK:
            try:
                deck_id = int(self.deck_id or 0)
            except (TypeError, ValueError) as error:
                raise ValueError("Choose a destination deck.") from error
            if deck_id <= 0:
                raise ValueError("Choose a destination deck.")
            object.__setattr__(self, "deck_id", deck_id)

    @property
    def requested_count(self) -> int:
        if self.kind in (ActionKind.ADD_TAGS, ActionKind.REMOVE_TAGS):
            return len(self.note_ids)
        return len(self.card_ids)


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """CollectionOp result plus exact target accounting for user feedback."""

    changes: Any
    kind: ActionKind
    requested: int
    live: int
    eligible: int
    changed: int
    stale: int
    skipped: int
    note_count: int
    changed_card_ids: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class _FallbackOpChanges:
    """Test-only empty changes object when imported outside Anki."""


def _empty_changes() -> Any:
    try:
        from anki.collection import OpChanges

        return OpChanges()
    except ImportError:
        return _FallbackOpChanges()


def _is_not_found(error: Exception) -> bool:
    try:
        from anki.errors import NotFoundError

        return isinstance(error, NotFoundError)
    except ImportError:
        # Plain-Python tests use a same-named stand-in without importing Anki.
        return error.__class__.__name__ == "NotFoundError"


def _live_cards(collection: Any, card_ids: Sequence[int]):
    live: list[tuple[int, Any]] = []
    stale = 0
    for card_id in card_ids:
        try:
            card = collection.get_card(card_id)
        except Exception as error:
            if _is_not_found(error):
                stale += 1
                continue
            raise
        if card is None:
            stale += 1
        else:
            live.append((card_id, card))
    return live, stale


def _live_notes(collection: Any, note_ids: Sequence[int]):
    live: list[tuple[int, Any]] = []
    stale = 0
    for note_id in note_ids:
        try:
            note = collection.get_note(note_id)
        except Exception as error:
            if _is_not_found(error):
                stale += 1
                continue
            raise
        if note is None:
            stale += 1
        else:
            live.append((note_id, note))
    return live, stale


def _suspended_queue() -> int:
    try:
        from anki.consts import QUEUE_TYPE_SUSPENDED

        return int(QUEUE_TYPE_SUSPENDED)
    except ImportError:
        return -1


def _buried_queues() -> set[int]:
    try:
        from anki.consts import (
            QUEUE_TYPE_MANUALLY_BURIED,
            QUEUE_TYPE_SIBLING_BURIED,
        )

        return {
            int(QUEUE_TYPE_MANUALLY_BURIED),
            int(QUEUE_TYPE_SIBLING_BURIED),
        }
    except ImportError:
        return {-3, -2}


def _home_deck_id(card: Any) -> int:
    original = int(getattr(card, "odid", 0) or 0)
    return original if original > 0 else int(getattr(card, "did", 0) or 0)


def _normal_deck_ids(collection: Any) -> set[int]:
    """Return current non-filtered deck IDs through Anki's public API."""

    output: set[int] = set()
    for item in collection.decks.all_names_and_ids(include_filtered=False):
        raw_id = (
            item.get("id", 0)
            if isinstance(item, Mapping)
            else getattr(item, "id", 0)
        )
        try:
            deck_id = int(raw_id or 0)
        except (TypeError, ValueError):
            continue
        if deck_id > 0:
            output.add(deck_id)
    return output


def _user_flag(card: Any) -> int | None:
    getter = getattr(card, "user_flag", None)
    if callable(getter):
        return int(getter())
    flags = getattr(card, "flags", None)
    return int(flags) & 0b111 if flags is not None else None


def _changes_and_count(result: Any, fallback_count: int) -> tuple[Any, int]:
    changes = getattr(result, "changes", result)
    count = getattr(result, "count", fallback_count)
    return changes, max(0, int(count))


def execute_collection_action(
    collection: Any,
    action: CollectionAction,
) -> ActionOutcome:
    """Revalidate and execute one action inside Anki's serialized worker."""

    requested = action.requested_count

    if action.kind in (
        ActionKind.FLAG,
        ActionKind.SUSPEND,
        ActionKind.UNSUSPEND,
        ActionKind.BURY,
        ActionKind.UNBURY,
        ActionKind.CHANGE_DECK,
    ):
        live, stale = _live_cards(collection, action.card_ids)
        suspended = _suspended_queue()
        buried = _buried_queues()

        if (
            action.kind is ActionKind.CHANGE_DECK
            and int(action.deck_id or 0) not in _normal_deck_ids(collection)
        ):
            raise ValueError(
                "That destination deck is no longer available. "
                "Choose another deck."
            )

        if action.kind is ActionKind.FLAG:
            eligible = [
                (card_id, card)
                for card_id, card in live
                if _user_flag(card) != action.flag
            ]
        elif action.kind is ActionKind.SUSPEND:
            eligible = [
                (card_id, card)
                for card_id, card in live
                if int(getattr(card, "queue", 0)) != suspended
            ]
        elif action.kind is ActionKind.UNSUSPEND:
            # Anki's backend operation is "unbury or unsuspend". Filtering is
            # essential so the Smart Search action can never unbury a card.
            eligible = [
                (card_id, card)
                for card_id, card in live
                if int(getattr(card, "queue", 0)) == suspended
            ]
        elif action.kind is ActionKind.BURY:
            eligible = [
                (card_id, card)
                for card_id, card in live
                if int(getattr(card, "queue", 0)) not in buried
                and int(getattr(card, "queue", 0)) != suspended
            ]
        elif action.kind is ActionKind.UNBURY:
            # ``unbury_cards()`` shares Anki's restore operation with
            # unsuspension, so suspended cards must never be passed here.
            eligible = [
                (card_id, card)
                for card_id, card in live
                if int(getattr(card, "queue", 0)) in buried
            ]
        else:
            target_deck_id = int(action.deck_id or 0)
            eligible = [
                (card_id, card)
                for card_id, card in live
                if _home_deck_id(card) != target_deck_id
            ]

        eligible_ids = [card_id for card_id, _card in eligible]
        note_count = len(
            {
                int(getattr(card, "nid", 0))
                for _card_id, card in eligible
                if int(getattr(card, "nid", 0)) > 0
            }
        )
        if not eligible_ids:
            return ActionOutcome(
                changes=_empty_changes(),
                kind=action.kind,
                requested=requested,
                live=len(live),
                eligible=0,
                changed=0,
                stale=stale,
                skipped=len(live),
                note_count=0,
                changed_card_ids=(),
            )

        if action.kind is ActionKind.FLAG:
            result = collection.set_user_flag_for_cards(
                int(action.flag),
                eligible_ids,
            )
        elif action.kind is ActionKind.SUSPEND:
            result = collection.sched.suspend_cards(eligible_ids)
        elif action.kind is ActionKind.UNSUSPEND:
            result = collection.sched.unsuspend_cards(eligible_ids)
        elif action.kind is ActionKind.BURY:
            result = collection.sched.bury_cards(eligible_ids)
        elif action.kind is ActionKind.UNBURY:
            result = collection.sched.unbury_cards(eligible_ids)
        else:
            result = collection.set_deck(eligible_ids, int(action.deck_id or 0))
        changes, changed = _changes_and_count(result, len(eligible_ids))
        return ActionOutcome(
            changes=changes,
            kind=action.kind,
            requested=requested,
            live=len(live),
            eligible=len(eligible_ids),
            changed=changed,
            stale=stale,
            skipped=len(live) - len(eligible_ids),
            note_count=note_count,
            changed_card_ids=tuple(eligible_ids),
        )

    live_notes, stale = _live_notes(collection, action.note_ids)
    live_ids = [note_id for note_id, _note in live_notes]
    if not live_ids:
        return ActionOutcome(
            changes=_empty_changes(),
            kind=action.kind,
            requested=requested,
            live=0,
            eligible=0,
            changed=0,
            stale=stale,
            skipped=0,
            note_count=0,
        )
    if action.kind is ActionKind.ADD_TAGS:
        result = collection.tags.bulk_add(live_ids, action.tags)
    elif action.kind is ActionKind.REMOVE_TAGS:
        result = collection.tags.bulk_remove(live_ids, action.tags)
    else:  # defensive guard if a future enum value is added
        raise ValueError(f"Unsupported collection action: {action.kind}")
    changes, changed = _changes_and_count(result, len(live_ids))
    return ActionOutcome(
        changes=changes,
        kind=action.kind,
        requested=requested,
        live=len(live_ids),
        eligible=len(live_ids),
        changed=changed,
        stale=stale,
        skipped=0,
        note_count=changed,
    )


def start_collection_action(
    *,
    parent: Any,
    initiator: object,
    action: CollectionAction,
    on_success: Callable[[ActionOutcome], None],
    on_failure: Callable[[Exception], None],
    collection_op_factory: Any | None = None,
) -> Any:
    """Schedule one official, undoable Anki ``CollectionOp``."""

    if collection_op_factory is None:
        from aqt.operations import CollectionOp

        collection_op_factory = CollectionOp

    operation = collection_op_factory(
        parent=parent,
        op=lambda collection: execute_collection_action(collection, action),
    )
    operation.success(on_success)
    operation.failure(on_failure)
    return operation.run_in_background(initiator=initiator)


_FLAG_NAMES = {
    1: "red",
    2: "orange",
    3: "green",
    4: "blue",
    5: "pink",
    6: "turquoise",
    7: "purple",
}


def _count_label(count: int, singular: str) -> str:
    suffix = "" if int(count) == 1 else "s"
    return f"{int(count):,} {singular}{suffix}"


def format_action_message(
    action: CollectionAction,
    outcome: ActionOutcome,
) -> str:
    """Create concise feedback with stale/no-op accounting."""

    if outcome.changed:
        if action.kind is ActionKind.FLAG:
            if action.flag == 0:
                message = f"Cleared flags on {_count_label(outcome.changed, 'card')}"
            else:
                color = _FLAG_NAMES.get(int(action.flag), "selected")
                message = (
                    f"Set the {color} flag on "
                    f"{_count_label(outcome.changed, 'card')}"
                )
            if outcome.note_count:
                message += f" from {_count_label(outcome.note_count, 'note')}"
        elif action.kind is ActionKind.SUSPEND:
            message = f"Suspended {_count_label(outcome.changed, 'card')}"
            if outcome.note_count:
                message += f" from {_count_label(outcome.note_count, 'note')}"
        elif action.kind is ActionKind.UNSUSPEND:
            message = f"Unsuspended {_count_label(outcome.changed, 'card')}"
            if outcome.note_count:
                message += f" from {_count_label(outcome.note_count, 'note')}"
        elif action.kind is ActionKind.BURY:
            message = f"Buried {_count_label(outcome.changed, 'card')}"
            if outcome.note_count:
                message += f" from {_count_label(outcome.note_count, 'note')}"
        elif action.kind is ActionKind.UNBURY:
            message = f"Unburied {_count_label(outcome.changed, 'card')}"
            if outcome.note_count:
                message += f" from {_count_label(outcome.note_count, 'note')}"
        elif action.kind is ActionKind.CHANGE_DECK:
            destination = action.deck_name or f"deck {action.deck_id}"
            message = f"Moved {_count_label(outcome.changed, 'card')}"
            if outcome.note_count:
                message += f" from {_count_label(outcome.note_count, 'note')}"
            message += f" to “{destination}”"
        elif action.kind is ActionKind.ADD_TAGS:
            message = (
                f"Added “{action.tags}” to "
                f"{_count_label(outcome.changed, 'note')}"
            )
        else:
            message = (
                f"Removed “{action.tags}” from "
                f"{_count_label(outcome.changed, 'note')}"
            )
        message += " — Undo available"
    else:
        target = (
            "notes"
            if action.kind in (ActionKind.ADD_TAGS, ActionKind.REMOVE_TAGS)
            else "cards"
        )
        message = f"No selected {target} needed changing"

    details: list[str] = []
    if outcome.stale:
        details.append(f"{outcome.stale:,} no longer available")
    if outcome.skipped:
        if action.kind is ActionKind.SUSPEND:
            details.append(f"{outcome.skipped:,} already suspended")
        elif action.kind is ActionKind.UNSUSPEND:
            details.append(f"{outcome.skipped:,} not suspended")
        elif action.kind is ActionKind.FLAG:
            details.append(f"{outcome.skipped:,} already had that flag")
        elif action.kind is ActionKind.BURY:
            details.append(
                f"{outcome.skipped:,} already buried or suspended"
            )
        elif action.kind is ActionKind.UNBURY:
            details.append(f"{outcome.skipped:,} not buried")
        elif action.kind is ActionKind.CHANGE_DECK:
            details.append(f"{outcome.skipped:,} already in that deck")
    if details:
        message += " · " + " · ".join(details)
    return message


__all__ = [
    "ActionKind",
    "ActionOutcome",
    "CollectionAction",
    "execute_collection_action",
    "format_action_message",
    "normalize_ids",
    "normalize_tag_text",
    "result_ids",
    "start_collection_action",
]
