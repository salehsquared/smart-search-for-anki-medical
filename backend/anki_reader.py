"""Small read-only boundary around Anki 26.5's public collection APIs."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from .models import IndexedNote
from .text import strip_html_and_cloze


def iter_collection_notes(
    collection: Any,
    note_ids: Iterable[int] | None = None,
) -> Iterator[IndexedNote]:
    """Yield immutable snapshots without using SQL or collection mutators.

    Call this on Anki's collection-owning thread. The resulting snapshots can
    safely be handed to an indexing worker.
    """

    ids = (
        [int(note_id) for note_id in note_ids]
        if note_ids is not None
        else [int(note_id) for note_id in collection.find_notes("")]
    )
    for note_id in ids:
        yield snapshot_note(collection, note_id)


def snapshot_note(collection: Any, note_id: int) -> IndexedNote:
    note = collection.get_note(int(note_id))
    fields = dict(note.items())
    note_type = note.note_type() or {}
    note_type_name = str(note_type.get("name", ""))
    card_ids = tuple(int(card_id) for card_id in collection.card_ids_of_note(note.id))
    deck_names: list[str] = []
    for card_id in card_ids:
        card = collection.get_card(card_id)
        try:
            original_deck_id = int(getattr(card, "odid", 0) or 0)
        except (TypeError, ValueError):
            original_deck_id = 0
        try:
            current_deck_id = int(getattr(card, "did", 0) or 0)
        except (TypeError, ValueError):
            current_deck_id = 0
        deck_id = original_deck_id if original_deck_id > 0 else current_deck_id
        if deck_id <= 0 and callable(getattr(card, "current_deck_id", None)):
            deck_id = int(card.current_deck_id() or 0)
        name = str(collection.decks.name(deck_id))
        if name and name not in deck_names:
            deck_names.append(name)
    title = ""
    for value in fields.values():
        title = strip_html_and_cloze(value)
        if title:
            break
    return IndexedNote(
        note_id=int(note.id),
        fields=fields,
        tags=tuple(str(tag) for tag in note.tags),
        decks=tuple(deck_names),
        note_type=note_type_name,
        card_ids=card_ids,
        modified_seconds=int(getattr(note, "mod", 0)),
        guid=str(getattr(note, "guid", "")),
        title=title,
    )


class CollectionFilterResolver:
    """Legacy note-ID view over Anki's documented card search."""

    def __init__(self, collection: Any) -> None:
        self.collection = collection

    def __call__(self, anki_query: str) -> Sequence[int]:
        note_ids: list[int] = []
        seen: set[int] = set()
        for card_id in self.collection.find_cards(anki_query):
            card = self.collection.get_card(int(card_id))
            note_id = int(card.nid)
            if note_id > 0 and note_id not in seen:
                note_ids.append(note_id)
                seen.add(note_id)
        return tuple(note_ids)
