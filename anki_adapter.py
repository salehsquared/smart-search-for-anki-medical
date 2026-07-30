"""The narrow, read-only boundary between Anki and the external search index."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
import hashlib
from pathlib import Path
from typing import Any

from .backend.models import IndexedNote

CardStateSnapshot = tuple[int, int, bool]  # card_id, flag, suspended
CardIdsByNote = dict[int, tuple[int, ...]]


def profile_key(profile_name: str, collection_path: str) -> str:
    identity = f"{profile_name}\0{Path(collection_path).resolve()}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:20]


class AnkiCollectionReader:
    """Create immutable note snapshots through an open ``Collection`` object.

    SQL is strictly read-only and executed through Anki's own collection
    connection inside a serialized ``QueryOp``.  The add-on never opens
    ``collection.anki2`` itself and never writes to it.
    """

    def snapshot(
        self,
        collection: Any,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[IndexedNote]:
        from anki.utils import split_fields

        note_rows = collection.db.all(
            """
            SELECT id, guid, mid, mod, tags, flds
            FROM notes
            ORDER BY id
            """
        )
        card_rows = collection.db.all(
            """
            SELECT id, nid, did, odid
            FROM cards
            ORDER BY nid, id
            """
        )

        model_ids = {int(row[2]) for row in note_rows}
        models: dict[int, tuple[str, tuple[str, ...]]] = {}
        for model_id in model_ids:
            model = collection.models.get(model_id)
            if model:
                models[model_id] = (
                    str(model.get("name", "")),
                    tuple(str(field.get("name", "")) for field in model.get("flds", ())),
                )
            else:
                models[model_id] = ("", ())

        deck_names = {
            int(item.id): str(item.name)
            for item in collection.decks.all_names_and_ids(include_filtered=True)
        }
        cards_by_note: dict[int, list[int]] = defaultdict(list)
        decks_by_note: dict[int, list[str]] = defaultdict(list)
        for card_id, note_id, deck_id, original_deck_id in card_rows:
            note_id = int(note_id)
            cards_by_note[note_id].append(int(card_id))
            for candidate in (int(deck_id), int(original_deck_id or 0)):
                name = deck_names.get(candidate)
                if name and name not in decks_by_note[note_id]:
                    decks_by_note[note_id].append(name)

        snapshots: list[IndexedNote] = []
        total = len(note_rows)
        for index, (note_id, guid, model_id, modified, tags, fields_blob) in enumerate(
            note_rows, start=1
        ):
            note_type, field_names = models.get(int(model_id), ("", ()))
            values = list(split_fields(str(fields_blob)))
            if len(field_names) < len(values):
                field_names = field_names + tuple(
                    f"Field {offset + 1}"
                    for offset in range(len(field_names), len(values))
                )
            fields = {
                field_names[offset]: value
                for offset, value in enumerate(values)
                if offset < len(field_names)
            }
            snapshots.append(
                IndexedNote(
                    note_id=int(note_id),
                    guid=str(guid),
                    modified_seconds=int(modified),
                    fields=fields,
                    tags=tuple(collection.tags.split(str(tags))),
                    decks=tuple(decks_by_note.get(int(note_id), ())),
                    note_type=note_type,
                    card_ids=tuple(cards_by_note.get(int(note_id), ())),
                    title=values[0] if values else "",
                )
            )
            if progress and (index == 1 or index % 250 == 0 or index == total):
                progress(index, total)
        return snapshots

    def snapshot_note_ids(
        self,
        collection: Any,
        note_ids: Iterable[int],
    ) -> list[IndexedNote]:
        """Snapshot only the requested live notes through Anki's public APIs.

        Requested IDs are deduplicated without changing their order. Notes
        deleted before this read are omitted, which lets the caller identify
        deletions by comparing the returned IDs with the requested IDs.
        """

        requested_ids: list[int] = []
        seen_ids: set[int] = set()
        for value in note_ids:
            try:
                note_id = int(value)
            except (TypeError, ValueError):
                continue
            if note_id <= 0 or note_id in seen_ids:
                continue
            requested_ids.append(note_id)
            seen_ids.add(note_id)

        deck_names: dict[int, str] = {}
        snapshots: list[IndexedNote] = []
        for note_id in requested_ids:
            try:
                note = collection.get_note(note_id)
            except Exception as error:
                if error.__class__.__name__ == "NotFoundError":
                    continue
                raise
            if note is None:
                continue

            fields = {
                str(field_name): str(value)
                for field_name, value in note.items()
            }
            note_type = note.note_type() or {}
            card_ids: list[int] = []
            decks: list[str] = []
            try:
                raw_card_ids = collection.card_ids_of_note(note_id)
            except Exception as error:
                if error.__class__.__name__ == "NotFoundError":
                    raw_card_ids = ()
                else:
                    raise

            for value in raw_card_ids:
                try:
                    card_id = int(value)
                except (TypeError, ValueError):
                    continue
                if card_id <= 0 or card_id in card_ids:
                    continue
                card_ids.append(card_id)
                try:
                    card = collection.get_card(card_id)
                except Exception as error:
                    if error.__class__.__name__ == "NotFoundError":
                        continue
                    raise
                if card is None:
                    continue

                candidate_deck_ids: list[int] = []
                for attribute in ("did", "odid"):
                    try:
                        deck_id = int(getattr(card, attribute, 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if deck_id > 0 and deck_id not in candidate_deck_ids:
                        candidate_deck_ids.append(deck_id)
                if not candidate_deck_ids:
                    current_deck_id = getattr(card, "current_deck_id", None)
                    if callable(current_deck_id):
                        try:
                            deck_id = int(current_deck_id() or 0)
                        except (TypeError, ValueError):
                            deck_id = 0
                        if deck_id > 0:
                            candidate_deck_ids.append(deck_id)

                for deck_id in candidate_deck_ids:
                    if deck_id not in deck_names:
                        deck_names[deck_id] = str(collection.decks.name(deck_id) or "")
                    deck_name = deck_names[deck_id]
                    if deck_name and deck_name not in decks:
                        decks.append(deck_name)

            snapshots.append(
                IndexedNote(
                    note_id=int(getattr(note, "id", note_id) or note_id),
                    guid=str(getattr(note, "guid", "")),
                    modified_seconds=int(getattr(note, "mod", 0)),
                    fields=fields,
                    tags=tuple(str(tag) for tag in getattr(note, "tags", ())),
                    decks=tuple(decks),
                    note_type=str(note_type.get("name", "")),
                    card_ids=tuple(card_ids),
                    title=next(iter(fields.values()), ""),
                )
            )
        return snapshots

    @staticmethod
    def note_ids_for_query(collection: Any, query: str) -> set[int]:
        return {int(note_id) for note_id in collection.find_notes(str(query or ""))}

    @staticmethod
    def card_ids_by_note_for_query(
        collection: Any,
        query: str,
    ) -> CardIdsByNote:
        """Resolve native Anki syntax to its exact matching card scope.

        A note can have siblings in different decks or scheduling states.
        ``find_notes()`` loses that distinction, so every filter path starts
        with ``find_cards()`` and retains the matching card IDs per note.
        """

        ordered_card_ids = tuple(
            dict.fromkeys(
                int(card_id)
                for card_id in collection.find_cards(str(query or ""))
                if int(card_id) > 0
            )
        )
        if not ordered_card_ids:
            return {}

        card_to_note: dict[int, int] = {}
        database = getattr(collection, "db", None)
        read_rows = getattr(database, "all", None)
        if callable(read_rows):
            # Mapping potentially broad filters through one public get_card()
            # call per card is needlessly expensive. This is strictly
            # read-only, runs on Anki's serialized collection worker, and is
            # chunked below SQLite's usual variable limit.
            try:
                for offset in range(0, len(ordered_card_ids), 500):
                    chunk = ordered_card_ids[offset : offset + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = read_rows(
                        f"SELECT id, nid FROM cards WHERE id IN ({placeholders})",
                        *chunk,
                    )
                    for card_id, note_id in rows:
                        card_to_note[int(card_id)] = int(note_id)
            except Exception:
                card_to_note.clear()

        output_lists: dict[int, list[int]] = {}
        for card_id in ordered_card_ids:
            note_id = card_to_note.get(card_id)
            if note_id is None:
                try:
                    card = collection.get_card(card_id)
                except Exception as error:
                    if error.__class__.__name__ == "NotFoundError":
                        continue
                    raise
                if card is None:
                    continue
                note_id = int(getattr(card, "nid", 0) or 0)
            if note_id <= 0:
                continue
            output_lists.setdefault(note_id, []).append(card_id)
        return {
            note_id: tuple(card_ids)
            for note_id, card_ids in output_lists.items()
        }

    @staticmethod
    def card_states_for_notes(
        collection: Any,
        note_ids: Iterable[int],
        *,
        card_ids_by_note: Mapping[int, Iterable[int]] | None = None,
    ) -> dict[int, tuple[CardStateSnapshot, ...]]:
        """Read current sibling IDs, flags, and suspension through public APIs.

        Card scheduling state deliberately stays out of the disposable text
        index. This method is called in a serialized ``QueryOp`` for only the
        notes currently visible in the result list.
        """

        try:
            from anki.consts import QUEUE_TYPE_SUSPENDED

            suspended_queue = int(QUEUE_TYPE_SUSPENDED)
        except ImportError:  # plain-Python tests use Anki's historical value
            suspended_queue = -1

        output: dict[int, tuple[CardStateSnapshot, ...]] = {}
        seen_notes: set[int] = set()
        for value in note_ids:
            try:
                note_id = int(value)
            except (TypeError, ValueError):
                continue
            if note_id <= 0 or note_id in seen_notes:
                continue
            seen_notes.add(note_id)

            if card_ids_by_note is not None:
                card_ids = tuple(
                    dict.fromkeys(
                        int(card_id)
                        for card_id in card_ids_by_note.get(note_id, ())
                        if int(card_id) > 0
                    )
                )
            else:
                try:
                    card_ids = tuple(
                        dict.fromkeys(
                            int(card_id)
                            for card_id in collection.card_ids_of_note(note_id)
                            if int(card_id) > 0
                        )
                    )
                except Exception as error:
                    if error.__class__.__name__ == "NotFoundError":
                        output[note_id] = ()
                        continue
                    raise

            states: list[CardStateSnapshot] = []
            for card_id in card_ids:
                try:
                    card = collection.get_card(card_id)
                except Exception as error:
                    if error.__class__.__name__ == "NotFoundError":
                        continue
                    raise
                if card is None:
                    continue
                user_flag = getattr(card, "user_flag", None)
                flag = (
                    int(user_flag())
                    if callable(user_flag)
                    else int(getattr(card, "flags", 0)) & 0b111
                )
                states.append(
                    (
                        card_id,
                        min(7, max(0, flag)),
                        int(getattr(card, "queue", 0)) == suspended_queue,
                    )
                )
            output[note_id] = tuple(states)
        return output

    @staticmethod
    def all_field_names(collection: Any) -> tuple[str, ...]:
        names: list[str] = []
        seen: set[str] = set()
        for notetype in collection.models.all():
            for field in notetype.get("flds", ()):
                name = str(field.get("name", ""))
                folded = name.casefold()
                if name and folded not in seen:
                    names.append(name)
                    seen.add(folded)
        return tuple(names)


def open_note_ids_in_browser(
    note_ids: Iterable[int],
    *,
    prompt: str | None = None,
) -> None:
    """Open or retarget Anki's native Browser to the provided notes."""

    import aqt
    from aqt import mw
    from anki.collection import SearchNode

    ids = tuple(dict.fromkeys(int(note_id) for note_id in note_ids if int(note_id) > 0))
    if not ids:
        return
    nodes = tuple(SearchNode(nid=note_id) for note_id in ids)
    grouped = mw.col.group_searches(*nodes, joiner="OR")
    # Keep the Browser's visible query identical to the query it executed.
    # Showing the Smart Search prompt here would make a later Enter press run
    # different native Anki semantics against the same result set.
    aqt.dialogs.open("Browser", mw, search=(grouped,))


def open_card_ids_in_browser(card_ids: Iterable[int]) -> None:
    """Open Anki's Browser with exactly the provided cards."""

    import aqt
    from aqt import mw

    ids = tuple(dict.fromkeys(int(card_id) for card_id in card_ids if int(card_id) > 0))
    if not ids:
        return
    # ``cid:1,2,3`` is Anki's documented exact card-ID search syntax.
    query = "cid:" + ",".join(str(card_id) for card_id in ids)
    aqt.dialogs.open("Browser", mw, search=(query,))


def open_native_query_in_browser(query: str) -> None:
    import aqt
    from aqt import mw

    aqt.dialogs.open("Browser", mw, search=(str(query or ""),))
