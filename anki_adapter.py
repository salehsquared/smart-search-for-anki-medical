"""The narrow, read-only boundary between Anki and the external search index."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
import hashlib
from pathlib import Path
import sys
from typing import Any, NamedTuple

from .backend.models import IndexedNote
from .ui.contracts import DeckCatalog, DeckEntry

CardStateSnapshot = tuple[int, int, bool]  # card_id, flag, suspended
CardIdsByNote = dict[int, tuple[int, ...]]
DEFAULT_HYDRATION_BATCH_SIZE = 250


class NoteManifestEntry(NamedTuple):
    """Compact note identity used to find likely changes before hydration.

    The fixed-size digest is computed while each bounded collection page is in
    memory; note text is never retained in the manifest. Exact operation hooks
    remain the primary source of changed IDs, while the digest closes the
    same-second/same-length edit gap for sync/import/undo safety audits.
    """

    note_id: int
    modified_seconds: int
    usn: int
    note_type_id: int
    fields_length: int
    tags_length: int
    field_checksum: int
    fields_digest: bytes
    tags_digest: bytes

    @property
    def signature(self) -> tuple[int, int, int, int, int, int, bytes, bytes]:
        return self[1:]

    # Descriptive aliases for callers that do not use MaintenanceJournal's
    # persisted field vocabulary.
    @property
    def update_sequence(self) -> int:
        return self.usn

    @property
    def field_bytes(self) -> int:
        return self.fields_length

    @property
    def tag_bytes(self) -> int:
        return self.tags_length

    @property
    def checksum(self) -> int:
        return self.field_checksum


class CardManifestEntry(NamedTuple):
    """Compact stable card scope, excluding all scheduling state."""

    card_id: int
    note_id: int
    home_deck_id: int

    @property
    def signature(self) -> tuple[int, int]:
        return self[1:]


def profile_key(profile_name: str, collection_path: str) -> str:
    identity = f"{profile_name}\0{Path(collection_path).resolve()}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:20]


def preview_card_ids(result: object | None) -> tuple[int, ...]:
    """Return the exact, de-duplicated card scope represented by a result."""

    if result is None:
        return ()
    output: list[int] = []
    seen: set[int] = set()
    for value in getattr(result, "card_ids", ()) or ():
        try:
            card_id = int(value)
        except (TypeError, ValueError):
            continue
        if card_id > 0 and card_id not in seen:
            output.append(card_id)
            seen.add(card_id)
    return tuple(output)


def create_result_previewer(
    mw: Any,
    *,
    initial_result: object,
    on_close: Callable[[], None],
    on_previous: Callable[[], bool],
    on_next: Callable[[], bool],
    has_previous: Callable[[], bool],
    has_next: Callable[[], bool],
) -> Any:
    """Create Anki's native reviewer-rendered Preview for search results.

    Imports remain lazy so this module is safe in plain-Python tests. Using
    Anki's own Previewer preserves card templates, clozes, media, MathJax,
    reviewer hooks, flags, audio, and theme behavior. Only the card supplier,
    exact-scope sibling traversal, and Up/Down result navigation are custom.
    """

    from aqt.browser.previewer import MultiCardPreviewer
    from aqt.qt import QKeySequence, QShortcut, qconnect
    from aqt.utils import restoreGeom, saveGeom

    class SearchResultPreviewer(MultiCardPreviewer):
        def __init__(self) -> None:
            self._result_key: tuple[int, tuple[int, ...]] = (0, ())
            self._result_card_ids: tuple[int, ...] = ()
            self._sibling_index = 0
            self._cached_card: Any | None = None
            self._cached_card_id = 0
            self._last_result_card_id = 0
            self._set_result_state(initial_result)
            super().__init__(parent=None, mw=mw, on_close=on_close)

        def _set_result_state(self, result: object | None) -> bool:
            card_ids = preview_card_ids(result)
            try:
                note_id = int(getattr(result, "note_id", 0) or 0)
            except (TypeError, ValueError):
                note_id = 0
            key = (note_id, card_ids)
            changed = key != self._result_key
            self._result_key = key
            self._result_card_ids = card_ids
            if changed:
                self._sibling_index = 0
            elif card_ids:
                self._sibling_index = min(
                    self._sibling_index,
                    len(card_ids) - 1,
                )
            else:
                self._sibling_index = 0
            return changed

        def _current_card_id(self) -> int:
            if not self._result_card_ids:
                return 0
            return self._result_card_ids[self._sibling_index]

        def _update_title(self) -> None:
            count = len(self._result_card_ids)
            if count > 1:
                self.setWindowTitle(
                    f"Preview · Card {self._sibling_index + 1} of {count}"
                )
            else:
                self.setWindowTitle("Preview")

        def set_result(self, result: object | None, *, force: bool = False) -> None:
            changed = self._set_result_state(result)
            if changed or force:
                self._cached_card = None
                self._cached_card_id = 0
            if force:
                self._last_state = None
            self._update_title()
            if getattr(self, "_web", None) is not None:
                self.render_card()

        def open(self) -> None:
            # Base Previewer restores Anki's native Browser geometry. Apply a
            # separate key immediately afterwards so neither preview changes
            # the other one's saved placement.
            super().open()
            restoreGeom(self, "smartSearchPreview")
            self._update_title()

        def _on_finished(self, _ok: int) -> None:
            saveGeom(self, "smartSearchPreview")
            self._on_close()

        def card(self) -> Any | None:
            card_id = self._current_card_id()
            if card_id <= 0 or getattr(self.mw, "col", None) is None:
                return None
            if self._cached_card is not None and self._cached_card_id == card_id:
                return self._cached_card
            return self._refresh_current_card()

        def _refresh_current_card(self) -> Any | None:
            """Reload the current card so deleted results never reach Previewer."""

            card_id = self._current_card_id()
            self._cached_card = None
            self._cached_card_id = 0
            if card_id <= 0 or getattr(self.mw, "col", None) is None:
                return None
            try:
                card = self.mw.col.get_card(card_id)
            except Exception:
                # A card can be deleted after the immutable result arrives.
                return None
            self._cached_card = card
            self._cached_card_id = card_id
            return card

        def _close_for_missing_card(self) -> None:
            self.cancel_timer()
            self.close()

        def card_changed(self) -> bool:
            card_id = self._current_card_id()
            changed = card_id != self._last_result_card_id
            self._last_result_card_id = card_id
            return changed

        def _create_gui(self) -> None:
            super()._create_gui()
            self._up_result_shortcut = QShortcut(QKeySequence("Up"), self)
            self._down_result_shortcut = QShortcut(QKeySequence("Down"), self)
            qconnect(self._up_result_shortcut.activated, on_previous)
            qconnect(self._down_result_shortcut.activated, on_next)
            if sys.platform == "darwin":
                # Anki's native Preview already owns Ctrl+Shift+P, which Qt
                # maps to the physical Command key on macOS. Add the physical
                # Control-key variant without duplicating the native shortcut
                # on Windows/Linux.
                self._toggle_preview_shortcut = QShortcut(
                    QKeySequence("Meta+Shift+P"),
                    self,
                )
                qconnect(self._toggle_preview_shortcut.activated, self.close)
            else:
                self._toggle_preview_shortcut = self.close_shortcut
            self._update_title()

        def _select_sibling(self, offset: int) -> bool:
            target = self._sibling_index + int(offset)
            if not 0 <= target < len(self._result_card_ids):
                return False
            self._sibling_index = target
            self._cached_card = None
            self._cached_card_id = 0
            self._update_title()
            self.render_card()
            return True

        def _on_prev_card(self) -> None:
            if not self._select_sibling(-1):
                on_previous()

        def _on_next_card(self) -> None:
            if not self._select_sibling(1):
                on_next()

        def _should_enable_prev(self) -> bool:
            return (
                super()._should_enable_prev()
                or self._sibling_index > 0
                or has_previous()
            )

        def _should_enable_next(self) -> bool:
            return (
                super()._should_enable_next()
                or self._sibling_index + 1 < len(self._result_card_ids)
                or has_next()
            )

        def _on_replay_audio(self) -> None:
            if self._refresh_current_card() is None:
                self._close_for_missing_card()
                return
            super()._on_replay_audio()

        def _on_show_both_sides(self, toggle: bool) -> None:
            if self._refresh_current_card() is None:
                self._close_for_missing_card()
                return
            super()._on_show_both_sides(toggle)

        def _on_bridge_cmd(self, cmd: str) -> Any:
            if cmd.startswith("play:") and self._refresh_current_card() is None:
                self._close_for_missing_card()
                return None
            return super()._on_bridge_cmd(cmd)

        def _render_scheduled(self) -> None:
            # MultiCardPreviewer provides the navigation buttons, while its
            # Browser-specific subclass normally refreshes their enabled
            # states after each render. This preview has a different result
            # source, so mirror that final native step here.
            if self._refresh_current_card() is None:
                self._close_for_missing_card()
                return
            super()._render_scheduled()
            self._updateButtons()

    return SearchResultPreviewer()


class AnkiCollectionReader:
    """Create immutable note snapshots through an open ``Collection`` object.

    SQL is strictly read-only and executed through Anki's own collection
    connection inside a serialized ``QueryOp``.  The add-on never opens
    ``collection.anki2`` itself and never writes to it.
    """

    @staticmethod
    def deck_catalog(collection: Any) -> DeckCatalog:
        """Read all native deck choices and Anki's current deck.

        The caller is responsible for invoking this through a serialized
        collection ``QueryOp``.  The public deck manager API is used instead
        of inspecting Anki's database or legacy deck dictionary directly.
        """

        deck_manager = collection.decks
        decks: list[DeckEntry] = []
        seen_ids: set[int] = set()
        for item in deck_manager.all_names_and_ids(include_filtered=True):
            try:
                if isinstance(item, Mapping):
                    deck_id = int(item.get("id", 0) or 0)
                    name = str(item.get("name", "") or "")
                else:
                    deck_id = int(getattr(item, "id", 0) or 0)
                    name = str(getattr(item, "name", "") or "")
            except (TypeError, ValueError):
                continue
            if deck_id <= 0 or not name or deck_id in seen_ids:
                continue
            decks.append(DeckEntry(deck_id=deck_id, name=name))
            seen_ids.add(deck_id)

        current_deck_id: int | None = None
        for accessor_name in ("get_current_id", "selected"):
            accessor = getattr(deck_manager, accessor_name, None)
            if not callable(accessor):
                continue
            try:
                candidate = int(accessor() or 0)
            except Exception:  # noqa: BLE001 - profile transition race
                continue
            if candidate > 0:
                current_deck_id = candidate
                break

        if current_deck_id is None:
            current = getattr(deck_manager, "current", None)
            if callable(current):
                try:
                    current = current()
                except Exception:  # noqa: BLE001 - profile transition race
                    current = None
            if isinstance(current, Mapping):
                current = current.get("id")
            else:
                current = getattr(current, "id", current)
            try:
                candidate = int(current or 0)
            except (TypeError, ValueError):
                candidate = 0
            if candidate > 0:
                current_deck_id = candidate

        return DeckCatalog(
            decks=tuple(decks),
            current_deck_id=current_deck_id,
        )

    def snapshot(
        self,
        collection: Any,
        *,
        progress: Callable[[int, int], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
        page_size: int = 500,
    ) -> list[IndexedNote]:
        """Read a full immutable profile snapshot in cancellable pages.

        Full rebuilds are intentionally rare, but they must still yield when
        Anki enters the reviewer. Keyset paging avoids one uninterruptible
        ``all()`` call containing every note field or card in the collection.
        """

        from anki.utils import split_fields

        size = max(1, min(5_000, int(page_size)))
        if cancel_check is not None:
            cancel_check()
        count_row = collection.db.first("SELECT count(*) FROM notes")
        total = int(count_row[0] if count_row is not None else 0)

        models: dict[int, tuple[str, tuple[str, ...]]] = {}
        if cancel_check is not None:
            cancel_check()
        deck_names = {
            int(item.id): str(item.name)
            for item in collection.decks.all_names_and_ids(include_filtered=True)
        }
        cards_by_note: dict[int, list[int]] = defaultdict(list)
        decks_by_note: dict[int, list[str]] = defaultdict(list)
        after_card_id = 0
        while True:
            if cancel_check is not None:
                cancel_check()
            card_rows = collection.db.all(
                """
                SELECT id, nid, did, odid
                FROM cards
                WHERE id > ?
                ORDER BY id
                LIMIT ?
                """,
                after_card_id,
                size,
            )
            if not card_rows:
                break
            for card_id, note_id, deck_id, original_deck_id in card_rows:
                note_id = int(note_id)
                cards_by_note[note_id].append(int(card_id))
                # While a card is in a filtered deck, ``did`` is temporary and
                # ``odid`` is its canonical home. Index only that stable identity
                # so ordinary filtered-deck reviews cannot churn the text index.
                home_deck_id = _canonical_home_deck_id(
                    deck_id,
                    original_deck_id,
                )
                name = deck_names.get(home_deck_id)
                if name and name not in decks_by_note[note_id]:
                    decks_by_note[note_id].append(name)
            after_card_id = int(card_rows[-1][0])
            if len(card_rows) < size:
                break

        snapshots: list[IndexedNote] = []
        after_note_id = 0
        while True:
            if cancel_check is not None:
                cancel_check()
            note_rows = collection.db.all(
                """
                SELECT id, guid, mid, mod, tags, flds
                FROM notes
                WHERE id > ?
                ORDER BY id
                LIMIT ?
                """,
                after_note_id,
                size,
            )
            if not note_rows:
                break
            for note_id, guid, model_id, modified, tags, fields_blob in note_rows:
                index = len(snapshots) + 1
                if cancel_check is not None and (
                    index == 1 or index % 128 == 0
                ):
                    cancel_check()
                model_key = int(model_id)
                if model_key not in models:
                    model = collection.models.get(model_key)
                    if model:
                        models[model_key] = (
                            str(model.get("name", "")),
                            tuple(
                                str(field.get("name", ""))
                                for field in model.get("flds", ())
                            ),
                        )
                    else:
                        models[model_key] = ("", ())
                note_type, field_names = models[model_key]
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
                if progress and (
                    index == 1 or index % 250 == 0 or index == total
                ):
                    progress(index, total)
            after_note_id = int(note_rows[-1][0])
            if len(note_rows) < size:
                break
        if cancel_check is not None:
            cancel_check()
        return snapshots

    def snapshot_note_ids(
        self,
        collection: Any,
        note_ids: Iterable[int],
        *,
        cancel_check: Callable[[], None] | None = None,
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
            if cancel_check is not None:
                cancel_check()
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
                if cancel_check is not None:
                    cancel_check()
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

                home_deck_id = _canonical_home_deck_id(
                    getattr(card, "did", 0),
                    getattr(card, "odid", 0),
                )
                if home_deck_id <= 0:
                    current_deck_id = getattr(card, "current_deck_id", None)
                    if callable(current_deck_id):
                        try:
                            home_deck_id = int(current_deck_id() or 0)
                        except (TypeError, ValueError):
                            home_deck_id = 0

                if home_deck_id > 0:
                    if home_deck_id not in deck_names:
                        deck_names[home_deck_id] = str(
                            collection.decks.name(home_deck_id) or ""
                        )
                    deck_name = deck_names[home_deck_id]
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
        if cancel_check is not None:
            cancel_check()
        return snapshots

    def snapshot_note_batch(
        self,
        collection: Any,
        note_ids: Iterable[int],
        *,
        max_notes: int = DEFAULT_HYDRATION_BATCH_SIZE,
        cancel_check: Callable[[], None] | None = None,
    ) -> tuple[IndexedNote, ...]:
        """Hydrate one explicitly bounded batch of note IDs.

        Controllers can page a manifest and invoke this method in separate
        serialized ``QueryOp`` calls. Rejecting oversized input avoids an
        accidental return to whole-profile materialization.
        """

        limit = int(max_notes)
        if limit <= 0:
            raise ValueError("max_notes must be positive")
        requested = _positive_unique_ids(note_ids)
        if len(requested) > limit:
            raise ValueError(
                f"snapshot batch contains {len(requested)} notes; maximum is {limit}"
            )
        return tuple(
            self.snapshot_note_ids(
                collection,
                requested,
                cancel_check=cancel_check,
            )
        )

    @staticmethod
    def note_manifest_batch(
        collection: Any,
        *,
        after_note_id: int = 0,
        limit: int = 500,
    ) -> tuple[NoteManifestEntry, ...]:
        """Return a compact, keyset-paged note inventory with content digests.

        ``mod``/``usn`` are deliberately supplemented with note type, byte
        lengths, Anki's sort-field checksum, and fixed-size field/tag digests.
        Raw text exists only in this bounded collection page and is discarded
        immediately after hashing.
        """

        page_size = int(limit)
        if page_size <= 0:
            raise ValueError("limit must be positive")
        page_size = min(page_size, 5_000)
        cursor = max(0, int(after_note_id))
        rows = collection.db.all(
            """
            SELECT id, mod, usn, mid,
                   length(CAST(flds AS BLOB)),
                   length(CAST(tags AS BLOB)),
                   csum, flds, tags
            FROM notes
            WHERE id > ?
            ORDER BY id
            LIMIT ?
            """,
            cursor,
            page_size,
        )
        return _note_manifest_entries(rows)

    @staticmethod
    def note_manifest_for_ids(
        collection: Any,
        note_ids: Iterable[int],
    ) -> tuple[NoteManifestEntry, ...]:
        """Return compact manifests for exact live IDs, preserving input order.

        Missing/deleted notes are omitted. SQL work is chunked below SQLite's
        common variable limit so a durable queue can be acknowledged after a
        targeted reconcile without requiring a whole-profile inventory pass.
        """

        requested = _positive_unique_ids(note_ids)
        if not requested:
            return ()
        by_note_id: dict[int, NoteManifestEntry] = {}
        for offset in range(0, len(requested), 500):
            chunk = requested[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = collection.db.all(
                f"""
                SELECT id, mod, usn, mid,
                       length(CAST(flds AS BLOB)),
                       length(CAST(tags AS BLOB)),
                       csum, flds, tags
                FROM notes
                WHERE id IN ({placeholders})
                """,
                *chunk,
            )
            for entry in _note_manifest_entries(rows):
                by_note_id[entry.note_id] = entry
        return tuple(
            by_note_id[note_id]
            for note_id in requested
            if note_id in by_note_id
        )

    @staticmethod
    def card_manifest_batch(
        collection: Any,
        *,
        after_card_id: int = 0,
        limit: int = 500,
    ) -> tuple[CardManifestEntry, ...]:
        """Return keyset-paged card ownership and canonical home decks.

        Queue, due, interval, reps, flags, and filtered-deck placement are
        intentionally absent, so answering cards cannot dirty this manifest.
        """

        page_size = int(limit)
        if page_size <= 0:
            raise ValueError("limit must be positive")
        page_size = min(page_size, 5_000)
        cursor = max(0, int(after_card_id))
        rows = collection.db.all(
            """
            SELECT id, nid,
                   CASE WHEN odid > 0 THEN odid ELSE did END
            FROM cards
            WHERE id > ?
            ORDER BY id
            LIMIT ?
            """,
            cursor,
            page_size,
        )
        return tuple(
            CardManifestEntry(
                card_id=int(card_id),
                note_id=int(note_id),
                home_deck_id=max(0, int(home_deck_id or 0)),
            )
            for card_id, note_id, home_deck_id in rows
        )

    @staticmethod
    def card_manifest_for_note_ids(
        collection: Any,
        note_ids: Iterable[int],
    ) -> tuple[CardManifestEntry, ...]:
        """Return stable card scope for an exact bounded note set."""

        requested = _positive_unique_ids(note_ids)
        if not requested:
            return ()
        output: list[CardManifestEntry] = []
        for offset in range(0, len(requested), 500):
            chunk = requested[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = collection.db.all(
                f"""
                SELECT id, nid,
                       CASE WHEN odid > 0 THEN odid ELSE did END
                FROM cards
                WHERE nid IN ({placeholders})
                ORDER BY id
                """,
                *chunk,
            )
            output.extend(
                CardManifestEntry(
                    card_id=int(card_id),
                    note_id=int(note_id),
                    home_deck_id=max(0, int(home_deck_id or 0)),
                )
                for card_id, note_id, home_deck_id in rows
            )
        return tuple(sorted(output, key=lambda row: row.card_id))

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


def _canonical_home_deck_id(deck_id: object, original_deck_id: object) -> int:
    """Return a card's stable home deck, not its temporary filtered deck."""

    try:
        original = int(original_deck_id or 0)
    except (TypeError, ValueError):
        original = 0
    if original > 0:
        return original
    try:
        current = int(deck_id or 0)
    except (TypeError, ValueError):
        return 0
    return current if current > 0 else 0


def _positive_unique_ids(values: Iterable[int]) -> tuple[int, ...]:
    output: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            continue
        if candidate > 0 and candidate not in seen:
            output.append(candidate)
            seen.add(candidate)
    return tuple(output)


def _note_manifest_entries(rows: Iterable[Sequence[object]]) -> tuple[NoteManifestEntry, ...]:
    return tuple(
        NoteManifestEntry(
            note_id=int(note_id),
            modified_seconds=int(modified or 0),
            usn=int(update_sequence or 0),
            note_type_id=int(note_type_id or 0),
            fields_length=int(field_bytes or 0),
            tags_length=int(tag_bytes or 0),
            field_checksum=int(checksum or 0),
            fields_digest=hashlib.blake2b(
                str(fields or "").encode("utf-8"),
                digest_size=16,
            ).digest(),
            tags_digest=hashlib.blake2b(
                str(tags or "").encode("utf-8"),
                digest_size=16,
            ).digest(),
        )
        for (
            note_id,
            modified,
            update_sequence,
            note_type_id,
            field_bytes,
            tag_bytes,
            checksum,
            fields,
            tags,
        ) in rows
    )


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
