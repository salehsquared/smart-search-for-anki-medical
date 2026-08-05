from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

import backend.index as index_module
from backend.index import SmartSearchIndex
from backend.models import IndexedNote


UWORLD_A = "#AK_Step2_v12::#UWorld::18462"
AMBOSS_A = "#AK_Step2_v12::#AMBOSS::Q-77"
UWORLD_B = "#AK_Step2_v12::#UWorld::99101"
BROAD_UWORLD = "#AK_Step2_v12::#UWorld"
BROAD_AMBOSS = "#AK_Step2_v12::#AMBOSS"


def _note(
    note_id: int,
    *tags: str,
    card_ids: tuple[int, ...] | None = None,
) -> IndexedNote:
    return IndexedNote(
        note_id=note_id,
        fields={"Text": f"Synthetic related-card note {note_id}"},
        tags=tags,
        decks=("Synthetic::Medicine",),
        note_type="Cloze",
        card_ids=card_ids or (note_id * 10 + 1,),
        modified_seconds=note_id,
        guid=f"related-{note_id}",
        title=f"Note {note_id}",
    )


def related_notes() -> tuple[IndexedNote, ...]:
    return (
        _note(1001, UWORLD_A, AMBOSS_A, BROAD_UWORLD, card_ids=(1101, 1102)),
        _note(1002, UWORLD_B, card_ids=(1201,)),
        _note(
            2001,
            UWORLD_A.swapcase(),
            AMBOSS_A.swapcase(),
            card_ids=(2101, 2102),
        ),
        _note(2002, UWORLD_A, UWORLD_B, card_ids=(2201,)),
        _note(2003, UWORLD_A, card_ids=(2301, 2302)),
        _note(2004, "UWorld", "#AMBOSS", BROAD_UWORLD, BROAD_AMBOSS),
        _note(
            2005,
            "#AK_Step2_v12::#UWorld::184620",
            "#AK_Step2_v12::#AMBOSS::Q-770",
        ),
        _note(2006, "#AK_Step2_v11::#UWorld::18462"),
        _note(2007, "Cardiology", "myuworldish"),
    )


class RelatedIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "search.sqlite3"
        self.index = SmartSearchIndex(self.path)
        self.assertEqual(self.index.rebuild(related_notes()), len(related_notes()))

    def tearDown(self) -> None:
        self.index.close()
        self.temporary.cleanup()

    def test_exact_full_tag_match_is_case_insensitive_and_preserves_siblings(
        self,
    ) -> None:
        result = self.index.related_documents(
            (UWORLD_A.swapcase(),),
            exclude_note_ids=(1001,),
            limit=50,
        )

        self.assertEqual([hit.document.note_id for hit in result.hits], [2001, 2002, 2003])
        self.assertEqual(result.total_candidates, 3)
        self.assertEqual(result.hits[0].shared_tags[0].key, UWORLD_A.casefold())
        siblings = next(
            hit.document.card_ids
            for hit in result.hits
            if hit.document.note_id == 2003
        )
        self.assertEqual(siblings, (2301, 2302))

    def test_broad_roots_full_hierarchy_near_misses_and_incidental_text_do_not_match(
        self,
    ) -> None:
        broad = self.index.related_documents(
            ("UWorld", "#AMBOSS", BROAD_UWORLD, BROAD_AMBOSS),
            limit=50,
        )
        exact = self.index.related_documents(
            (UWORLD_A, AMBOSS_A),
            exclude_note_ids=(1001,),
            limit=50,
        )

        self.assertEqual(broad.source_tags, ())
        self.assertEqual(broad.hits, ())
        self.assertEqual(broad.total_candidates, 0)
        exact_ids = {hit.document.note_id for hit in exact.hits}
        self.assertNotIn(2004, exact_ids)
        self.assertNotIn(2005, exact_ids)
        self.assertNotIn(2006, exact_ids)
        self.assertNotIn(2007, exact_ids)

    def test_multi_seed_union_deduplicates_and_ranks_by_distinct_shared_tags(
        self,
    ) -> None:
        result = self.index.related_documents(
            (UWORLD_A, UWORLD_A.swapcase(), AMBOSS_A, UWORLD_B),
            exclude_note_ids=(1001, 1002),
            limit=50,
        )

        self.assertEqual([source.key for source in result.source_tags], [
            UWORLD_A.casefold(),
            AMBOSS_A.casefold(),
            UWORLD_B.casefold(),
        ])
        self.assertEqual([hit.document.note_id for hit in result.hits], [2001, 2002, 2003])
        self.assertEqual([len(hit.shared_tags) for hit in result.hits], [2, 2, 1])
        self.assertEqual(result.total_candidates, 3)
        self.assertNotIn(1001, {hit.document.note_id for hit in result.hits})
        self.assertNotIn(1002, {hit.document.note_id for hit in result.hits})

    def test_limit_retains_global_candidate_count_for_truncation(self) -> None:
        result = self.index.related_documents(
            (UWORLD_A, AMBOSS_A, UWORLD_B),
            exclude_note_ids=(1001, 1002),
            limit=2,
        )

        self.assertEqual([hit.document.note_id for hit in result.hits], [2001, 2002])
        self.assertEqual(len(result.hits), 2)
        self.assertEqual(result.total_candidates, 3)
        self.assertGreater(result.total_candidates, len(result.hits))

    def test_incremental_tag_replacement_and_delete_update_reverse_lookup(self) -> None:
        replacement = _note(2003, UWORLD_B, card_ids=(2301, 2302))
        changed, unchanged = self.index.upsert_notes((replacement,))

        self.assertEqual((changed, unchanged), (1, 0))
        old_relation = self.index.related_documents(
            (UWORLD_A,),
            exclude_note_ids=(1001,),
            limit=50,
        )
        new_relation = self.index.related_documents(
            (UWORLD_B,),
            exclude_note_ids=(1002,),
            limit=50,
        )
        self.assertNotIn(2003, {hit.document.note_id for hit in old_relation.hits})
        self.assertIn(2003, {hit.document.note_id for hit in new_relation.hits})

        self.assertEqual(self.index.delete_notes((2003,)), 1)
        after_delete = self.index.related_documents(
            (UWORLD_B,),
            exclude_note_ids=(1002,),
            limit=50,
        )
        self.assertNotIn(2003, {hit.document.note_id for hit in after_delete.hits})
        row = self.index.connection.execute(
            "SELECT 1 FROM related_source_tags WHERE note_id=?",
            (2003,),
        ).fetchone()
        self.assertIsNone(row)


class RelatedIndexMigrationTests(unittest.TestCase):
    def test_existing_v2_index_backfills_related_tags_without_generation_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-v2.sqlite3"
            seed = _note(9001, UWORLD_A, card_ids=(9101,))
            candidate = _note(9002, UWORLD_A.swapcase(), card_ids=(9201, 9202))
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO metadata(key, value) VALUES('schema_version', '2');
                INSERT INTO metadata(key, value) VALUES('generation', '9');
                CREATE TABLE notes (
                    note_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    content_hash BLOB NOT NULL
                );
                """
            )
            for note in (seed, candidate):
                prepared = index_module._prepare_note(note)
                connection.execute(
                    """
                    INSERT INTO notes(note_id, title, payload, content_hash)
                    VALUES(?, ?, ?, ?)
                    """,
                    (
                        note.note_id,
                        prepared["title"],
                        prepared["payload"],
                        prepared["content_hash"],
                    ),
                )
            connection.commit()
            connection.close()

            migrated = SmartSearchIndex(path)
            try:
                self.assertEqual(migrated.generation, 9)
                self.assertEqual(migrated.count_documents(), 2)
                revision = migrated.connection.execute(
                    """
                    SELECT value FROM metadata
                    WHERE key='related_source_tag_index_revision'
                    """
                ).fetchone()
                self.assertIsNotNone(revision)
                related_generation = migrated.connection.execute(
                    """
                    SELECT value FROM metadata
                    WHERE key='related_source_tag_index_generation'
                    """
                ).fetchone()
                self.assertEqual(related_generation["value"], "9")
                result = migrated.related_documents(
                    (UWORLD_A,),
                    exclude_note_ids=(9001,),
                    limit=50,
                )
                self.assertEqual(
                    [hit.document.note_id for hit in result.hits],
                    [9002],
                )
                self.assertEqual(result.hits[0].document.card_ids, (9201, 9202))
                self.assertEqual(migrated.generation, 9)
                self.assertEqual(
                    list(path.parent.glob(path.name + ".schema-backup-*")),
                    [],
                )
            finally:
                migrated.close()

    def test_generation_mismatch_repairs_map_after_an_older_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollback.sqlite3"
            source = _note(9001, UWORLD_A, card_ids=(9101,))
            candidate = _note(9002, UWORLD_A, card_ids=(9201,))
            current = SmartSearchIndex(path)
            current.rebuild((source, candidate))
            generation = current.generation
            current.close()

            # Simulate an older add-on writing the same schema: its note
            # transaction advances the lexical generation but cannot maintain
            # the newer auxiliary reverse map or its generation marker.
            legacy = sqlite3.connect(path)
            legacy.execute(
                "DELETE FROM related_source_tags WHERE note_id=?",
                (9002,),
            )
            legacy.execute(
                "UPDATE metadata SET value=? WHERE key='generation'",
                (str(generation + 1),),
            )
            legacy.commit()
            legacy.close()

            repaired = SmartSearchIndex(path)
            try:
                result = repaired.related_documents(
                    (UWORLD_A,),
                    exclude_note_ids=(9001,),
                    limit=50,
                )
                self.assertEqual(
                    [hit.document.note_id for hit in result.hits],
                    [9002],
                )
                marker = repaired.connection.execute(
                    """
                    SELECT value FROM metadata
                    WHERE key='related_source_tag_index_generation'
                    """
                ).fetchone()
                self.assertEqual(marker["value"], str(generation + 1))
                self.assertEqual(repaired.generation, generation + 1)
            finally:
                repaired.close()


if __name__ == "__main__":
    unittest.main()
