from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import backend.index as index_module
from backend import IndexedNote, SearchEngine, SmartSearchIndex, UnsafeIndexPath
from backend.models import MatchKind, RankedCandidate
from backend.search import _adaptive_relevance_cutoff


def sample_notes() -> list[IndexedNote]:
    return [
        IndexedNote(
            1001,
            {
                "Text": "{{c1::Bupropion}} is used for depression and smoking cessation.",
                "Extra": "<b>Wellbutrin</b> inhibits norepinephrine and dopamine reuptake.",
            },
            tags=("Psychiatry", "Pharmacology"),
            decks=("AnKing::Step 2",),
            note_type="Cloze",
            card_ids=(2001,),
            modified_seconds=10,
            guid="one",
        ),
        IndexedNote(
            1002,
            {
                "Text": "ACE inhibitors improve mortality in chronic heart failure.",
                "Extra": "Monitor potassium and creatinine.",
            },
            tags=("Cardiology",),
            decks=("AnKing::Step 2",),
            note_type="Cloze",
            card_ids=(2002, 2003),
            modified_seconds=20,
            guid="two",
        ),
        IndexedNote(
            1003,
            {"Text": "The ileum contains Peyer patches."},
            tags=("GI",),
            decks=("AnKing::Step 1",),
            note_type="Basic",
            card_ids=(2004,),
            modified_seconds=30,
            guid="three",
        ),
    ]


class IndexSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "smart-search.sqlite3"
        self.index = SmartSearchIndex(self.path)
        self.assertEqual(self.index.rebuild(sample_notes()), 3)
        self.engine = SearchEngine(self.index)

    def tearDown(self) -> None:
        self.index.close()
        self.temporary.cleanup()

    def test_external_index_and_stats(self) -> None:
        stats = self.index.stats()
        self.assertEqual(stats.note_count, 3)
        self.assertGreater(stats.vocabulary_size, 10)
        self.assertGreater(stats.database_bytes, 0)

    def test_cancelled_note_delete_rolls_back_the_batch(self) -> None:
        checkpoints = 0

        def cancel() -> None:
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 2:
                raise RuntimeError("review started")

        with self.assertRaisesRegex(RuntimeError, "review started"):
            self.index.delete_notes(
                (1001, 1002),
                cancel_check=cancel,
            )

        self.assertEqual(self.index.count_documents((1001, 1002)), 2)

    def test_cancelled_rebuild_tail_keeps_the_published_index(self) -> None:
        vocabulary = " ".join(f"term{number:04d}" for number in range(1_200))
        replacement = IndexedNote(
            9001,
            {"Text": vocabulary},
            card_ids=(9901,),
            guid="large-rebuild",
        )
        checkpoints = 0

        def cancel() -> None:
            nonlocal checkpoints
            checkpoints += 1
            # One note checkpoint, the post-note checkpoint, and one complete
            # 500-term write occur before cancellation. The temporary rebuild
            # must still roll back without replacing the published index.
            if checkpoints == 4:
                raise RuntimeError("review started")

        with self.assertRaisesRegex(RuntimeError, "review started"):
            self.index.rebuild([replacement], cancel_check=cancel)

        self.assertEqual(self.index.note_ids(), (1001, 1002, 1003))
        self.assertEqual(self.index.count_documents((9001,)), 0)

    def test_compact_schema_has_no_per_note_term_or_field_tables(self) -> None:
        tables = {
            row[0]
            for row in self.index.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertNotIn("note_terms", tables)
        self.assertNotIn("note_fields", tables)
        self.assertIn("field_names", tables)
        payload_type = self.index.connection.execute(
            "SELECT typeof(payload) FROM notes LIMIT 1"
        ).fetchone()[0]
        self.assertEqual(payload_type, "blob")

    def test_existing_v2_index_backfills_scope_and_content_hashes_in_place(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy-v2.sqlite3"
        note = sample_notes()[0]
        prepared = index_module._prepare_note(note)
        connection = sqlite3.connect(legacy_path)
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
        connection.execute(
            "INSERT INTO notes(note_id, title, payload, content_hash) VALUES(?, ?, ?, ?)",
            (note.note_id, prepared["title"], prepared["payload"], b"legacy"),
        )
        connection.commit()
        connection.close()

        migrated = SmartSearchIndex(legacy_path)
        try:
            columns = {
                row[1]
                for row in migrated.connection.execute("PRAGMA table_info(notes)")
            }
            self.assertIn("card_scope_hash", columns)
            plan = migrated.plan_targeted_upsert([note])
            self.assertEqual(plan.changed, ())
            self.assertEqual(plan.card_scope_updates, ())
            self.assertEqual(migrated.generation, 9)
        finally:
            migrated.close()

    def test_incremental_field_allowlist_uses_aggregate_counts(self) -> None:
        self.index.upsert_notes(
            [
                IndexedNote(
                    1004,
                    {"Unique Clinical Field": "content", "Text": "another note"},
                )
            ]
        )
        self.assertIn("Unique Clinical Field", self.index.field_names())
        self.index.delete_notes([1004])
        self.assertNotIn("Unique Clinical Field", self.index.field_names())
        self.assertIn("Text", self.index.field_names())

    def test_case_insensitive_exact_search(self) -> None:
        lower = self.engine.search("bupropion")
        upper = self.engine.search("BUPROPION")
        self.assertEqual([result.note_id for result in lower.results], [1001])
        self.assertEqual(
            [result.note_id for result in lower.results],
            [result.note_id for result in upper.results],
        )
        self.assertTrue(
            any(reason.kind == MatchKind.EXACT_TERM for reason in lower.results[0].reasons)
        )

    def test_typo_correction_is_visible_and_explainable(self) -> None:
        response = self.engine.search("buproprion")
        self.assertEqual(response.results[0].note_id, 1001)
        self.assertEqual(response.corrections[0].corrected, "bupropion")
        self.assertTrue(response.corrections[0].automatic)
        self.assertTrue(
            any(reason.kind == MatchKind.TYPO for reason in response.results[0].reasons)
        )

    def test_phrase_and_word_proximity(self) -> None:
        response = self.engine.search('"heart failure"')
        self.assertEqual(response.results[0].note_id, 1002)
        kinds = {reason.kind for reason in response.results[0].reasons}
        self.assertIn(MatchKind.EXACT_PHRASE, kinds)
        self.assertIn(MatchKind.PROXIMITY, kinds)

    def test_alias_resource_flows_into_search(self) -> None:
        self.index.set_aliases({"zyban": ("bupropion",)}, source="test")
        response = self.engine.search("zyban")
        self.assertEqual(response.results[0].note_id, 1001)
        self.assertTrue(response.alias_expansions)
        self.assertTrue(
            any(reason.kind == MatchKind.ALIAS for reason in response.results[0].reasons)
        )

    def test_automatic_typo_correction_then_expands_drug_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(
                [
                    IndexedNote(
                        1,
                        {"Text": "Bupropion is an aminoketone antidepressant."},
                    ),
                    IndexedNote(
                        2,
                        {"Text": "Wellbutrin can lower the seizure threshold."},
                    ),
                ]
            )
            index.set_aliases({"wellbutrin": ("bupropion",)}, source="test")
            try:
                response = SearchEngine(index).search("buproprion")
                generic_result = next(
                    result for result in response.results if result.note_id == 1
                )
                brand_result = next(
                    result for result in response.results if result.note_id == 2
                )
                self.assertEqual(response.corrections[0].corrected, "bupropion")
                self.assertTrue(response.corrections[0].automatic)
                self.assertIn(
                    ("bupropion", "wellbutrin"),
                    {
                        (expansion.original, expansion.expanded)
                        for expansion in response.alias_expansions
                    },
                )
                self.assertTrue(
                    any(
                        reason.kind == MatchKind.ALIAS
                        for reason in brand_result.reasons
                    )
                )
                self.assertTrue(
                    any(
                        reason.kind == MatchKind.TYPO
                        for reason in generic_result.reasons
                    )
                )
                self.assertFalse(
                    any(
                        reason.kind == MatchKind.ALIAS
                        for reason in generic_result.reasons
                    )
                )
            finally:
                index.close()

    def test_corrected_alias_keeps_other_query_terms_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(
                [
                    IndexedNote(
                        1,
                        {"Text": "Wellbutrin supports smoking cessation."},
                    ),
                    IndexedNote(
                        2,
                        {"Text": "Wellbutrin can lower the seizure threshold."},
                    ),
                    IndexedNote(
                        3,
                        {"Text": "Bupropion supports smoking cessation."},
                    ),
                    IndexedNote(
                        4,
                        {"Text": "Varenicline supports smoking cessation."},
                    ),
                ]
            )
            index.set_aliases({"wellbutrin": ("bupropion",)}, source="test")
            try:
                response = SearchEngine(index).search("buproprion smoking")
                self.assertEqual(
                    {result.note_id for result in response.results},
                    {1, 3},
                )
                pairs = [
                    (expansion.original, expansion.expanded)
                    for expansion in response.alias_expansions
                ]
                self.assertEqual(pairs.count(("bupropion", "wellbutrin")), 1)
                result_by_id = {
                    result.note_id: result for result in response.results
                }
                self.assertTrue(
                    any(
                        reason.kind == MatchKind.ALIAS
                        for reason in result_by_id[1].reasons
                    )
                )
                self.assertTrue(
                    any(
                        reason.kind == MatchKind.TYPO
                        for reason in result_by_id[3].reasons
                    )
                )
            finally:
                index.close()

    def test_automatic_brand_typo_then_expands_to_generic_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(
                [
                    IndexedNote(
                        1,
                        {"Text": "Bupropion is an aminoketone antidepressant."},
                    ),
                ]
            )
            index.set_aliases({"wellbutrin": ("bupropion",)}, source="test")
            try:
                response = SearchEngine(index).search("welbutrin")
                self.assertEqual(response.results[0].note_id, 1)
                self.assertEqual(response.corrections[0].corrected, "wellbutrin")
                self.assertTrue(response.corrections[0].automatic)
                self.assertIn(
                    ("wellbutrin", "bupropion"),
                    {
                        (expansion.original, expansion.expanded)
                        for expansion in response.alias_expansions
                    },
                )
                self.assertTrue(
                    any(
                        reason.kind == MatchKind.ALIAS
                        for reason in response.results[0].reasons
                    )
                )
            finally:
                index.close()

    def test_structured_filters_are_never_ignored(self) -> None:
        unresolved = self.engine.search("deck:AnKing bupropion")
        self.assertFalse(unresolved.filters_applied)
        self.assertFalse(unresolved.results)
        resolved = self.engine.search(
            "deck:AnKing bupropion", allowed_note_ids={1002}
        )
        self.assertTrue(resolved.filters_applied)
        self.assertFalse(resolved.results)
        resolved = self.engine.search(
            "deck:AnKing bupropion", allowed_note_ids={1001}
        )
        self.assertEqual(resolved.results[0].note_id, 1001)

    def test_positive_filters_are_post_ranking_eligibility_masks(self) -> None:
        unfiltered = self.engine.search("bupropion", limit=50)

        with mock.patch.object(
            self.index,
            "search_fts",
            wraps=self.index.search_fts,
        ) as search_fts:
            filtered = self.engine.search(
                "deck:AnKing bupropion",
                allowed_note_ids={1001},
                limit=50,
            )

        # The free-text retrieval universe must be identical. Native filters
        # are applied only after global scoring and relevance cutoff.
        self.assertTrue(search_fts.call_args_list)
        self.assertTrue(
            all(
                call.kwargs["allowed_note_ids"] is None
                for call in search_fts.call_args_list
            )
        )
        expected = [
            result for result in unfiltered.results if result.note_id == 1001
        ]
        self.assertEqual(
            [result.note_id for result in filtered.results],
            [result.note_id for result in expected],
        )
        self.assertEqual(
            [result.score for result in filtered.results],
            [result.score for result in expected],
        )

    def test_empty_filter_scope_preserves_typo_and_alias_feedback(self) -> None:
        self.index.set_aliases(
            {"wellbutrin": ("bupropion",)},
            source="test",
        )
        unfiltered = self.engine.search("buproprion")
        filtered = self.engine.search(
            "deck:NoMatches buproprion",
            allowed_note_ids=set(),
        )

        self.assertFalse(filtered.results)
        self.assertEqual(filtered.corrections, unfiltered.corrections)
        self.assertEqual(filtered.alias_expansions, unfiltered.alias_expansions)

    def test_filter_preserves_global_order_with_limit_and_negative_term(self) -> None:
        notes = [
            IndexedNote(
                note_id,
                {
                    "Text": (
                        "A1c diabetes pediatric"
                        if note_id == 6
                        else "A1c diabetes monitoring"
                    )
                },
            )
            for note_id in range(1, 7)
        ]
        allowed = {2, 4, 5, 6}

        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(notes)
            try:
                engine = SearchEngine(index)
                global_results = engine.search(
                    "a1c -pediatric",
                    mode="smart",
                    limit=50,
                )
                visible_unfiltered = engine.search(
                    "a1c -pediatric",
                    mode="smart",
                    limit=3,
                )
                filtered = engine.search(
                    "deck:xyz a1c -pediatric",
                    mode="smart",
                    allowed_note_ids=allowed,
                    limit=3,
                )
            finally:
                index.close()

        global_eligible = [
            result
            for result in global_results.results
            if result.note_id in allowed
        ]
        self.assertEqual(
            [result.note_id for result in visible_unfiltered.results],
            [1, 2, 3],
        )
        self.assertEqual(
            [result.note_id for result in filtered.results],
            [result.note_id for result in global_eligible[:3]],
        )
        self.assertEqual(
            [result.score for result in filtered.results],
            [result.score for result in global_eligible[:3]],
        )
        self.assertIn(2, [result.note_id for result in filtered.results])
        self.assertNotIn(6, [result.note_id for result in filtered.results])

    def test_filter_cannot_change_smart_rank_fusion_before_limit(self) -> None:
        from backend.index import FTSDocumentHit, IndexedDocument
        from backend.models import AliasExpansion, Correction

        def document(note_id: int, text: str) -> IndexedDocument:
            return IndexedDocument(
                note_id,
                (note_id,),
                text,
                text,
                text,
                {"Text": text},
                (),
                (),
                "Cloze",
            )

        first = document(1, "orig")
        second = document(2, "orig corr alias")
        literal_fillers = [
            document(10_000 + offset, "orig") for offset in range(98)
        ]
        corrected_fillers = [
            document(20_000 + offset, "corr") for offset in range(100)
        ]
        alias_fillers = [
            document(30_000 + offset, "alias") for offset in range(100)
        ]

        class FakeIndex:
            generation = 1

            @staticmethod
            def field_names():
                return ()

            @staticmethod
            def search_fts(
                expression,
                *,
                limit=250,
                allowed_note_ids=None,
                cancel_check=None,
            ):
                if cancel_check:
                    cancel_check()
                if "alias" in expression:
                    sequence = [*alias_fillers, second]
                elif "corr" in expression:
                    sequence = [*corrected_fillers, second]
                else:
                    sequence = [first, *literal_fillers, second]
                if allowed_note_ids is not None:
                    sequence = [
                        item
                        for item in sequence
                        if item.note_id in allowed_note_ids
                    ]
                return tuple(
                    FTSDocumentHit(item, rank, float(rank))
                    for rank, item in enumerate(sequence[:limit], start=1)
                )

        class DeterministicEngine(SearchEngine):
            def _ensure_vocabulary(self, **_kwargs):
                return object()

            def _corrections(self, _parsed, _vocabulary, **_kwargs):
                return [Correction("orig", "corr", 1, 0.99, True)]

            def _aliases(self, _terms, _vocabulary):
                return [AliasExpansion("corr", "alias", "forward")]

        engine = DeterministicEngine(FakeIndex())
        unfiltered = engine.search("orig", mode="smart", limit=1)
        filtered = engine.search(
            "deck:F orig",
            mode="smart",
            limit=1,
            allowed_note_ids={1, 2},
        )

        self.assertEqual([item.note_id for item in unfiltered.results], [1])
        self.assertEqual([item.note_id for item in filtered.results], [1])
        self.assertEqual(
            filtered.results[0].score,
            unfiltered.results[0].score,
        )

    def test_filter_only_query_returns_allowed_documents(self) -> None:
        response = self.engine.search("tag:Cardiology", allowed_note_ids={1002})
        self.assertEqual(response.total_candidates, 1)
        self.assertEqual(response.results[0].note_id, 1002)

    def test_filter_with_only_negative_free_term_applies_exclusion(self) -> None:
        response = self.engine.search(
            "deck:AnKing -bupropion",
            allowed_note_ids={1001, 1002},
        )
        self.assertEqual([result.note_id for result in response.results], [1002])

    def test_incremental_update_and_delete_keep_vocabulary_consistent(self) -> None:
        changed, unchanged = self.index.upsert_notes(
            [
                IndexedNote(
                    1001,
                    {"Text": "Sertraline is an SSRI."},
                    card_ids=(2001,),
                    modified_seconds=11,
                ),
                sample_notes()[1],
            ]
        )
        self.assertEqual((changed, unchanged), (1, 1))
        self.assertFalse(self.engine.search("bupropion").results)
        self.assertEqual(self.engine.search("sertraline").results[0].note_id, 1001)
        self.assertEqual(self.index.delete_notes([1001, 999999]), 1)
        self.assertFalse(self.engine.search("sertraline").results)

    def test_failed_atomic_rebuild_preserves_previous_index(self) -> None:
        def broken_notes():
            yield sample_notes()[0]
            raise RuntimeError("simulated failure")

        with self.assertRaisesRegex(RuntimeError, "simulated"):
            self.index.rebuild(broken_notes())
        self.assertEqual(self.index.stats().note_count, 3)
        self.assertEqual(self.engine.search("bupropion").results[0].note_id, 1001)

    def test_fts_syntax_is_not_injected(self) -> None:
        # User punctuation is tokenized and every FTS term is double-quoted.
        response = self.engine.search('bupropion" OR *')
        self.assertIsNotNone(response)

    def test_persists_after_reopen(self) -> None:
        generation = self.index.generation
        self.index.close()
        self.index = SmartSearchIndex(self.path)
        self.engine = SearchEngine(self.index)
        self.assertEqual(self.engine.search("bupropion").results[0].note_id, 1001)
        self.assertEqual(self.index.generation, generation)

    def test_generation_advances_only_for_lexical_mutations(self) -> None:
        generation = self.index.generation
        changed, unchanged = self.index.upsert_notes([sample_notes()[0]])
        self.assertEqual((changed, unchanged), (0, 1))
        self.assertEqual(self.index.generation, generation)

        self.index.upsert_notes(
            [
                IndexedNote(
                    1001,
                    {"Text": "Sertraline is an SSRI."},
                    guid="one",
                )
            ]
        )
        self.assertEqual(self.index.generation, generation + 1)
        self.index.delete_notes([1001])
        self.assertEqual(self.index.generation, generation + 2)

    def test_unchanged_reconcile_skips_expensive_note_preparation(self) -> None:
        with mock.patch.object(
            index_module,
            "_prepare_note",
            wraps=index_module._prepare_note,
        ) as prepare:
            changed, unchanged = self.index.upsert_notes(sample_notes())
        self.assertEqual((changed, unchanged), (0, 3))
        prepare.assert_not_called()

    def test_upsert_plan_can_be_staged_before_short_atomic_apply(self) -> None:
        updated = IndexedNote(
            1001,
            {"Text": "Bupropion has an updated explanation."},
            card_ids=(2001,),
            modified_seconds=11,
            guid="one",
        )
        plan = self.index.plan_upsert([updated, *sample_notes()[1:]])
        self.assertEqual(plan.unchanged, 2)
        self.assertEqual([note.note_id for note, _hash in plan.changed], [1001])
        self.assertEqual(self.index.apply_upsert(plan), 1)
        self.assertIn("updated", self.engine.search("updated").results[0].snippet)

    def test_card_scope_and_timestamps_do_not_rebuild_lexical_content(self) -> None:
        original_generation = self.index.generation
        original = sample_notes()[0]
        rescheduled = IndexedNote(
            original.note_id,
            original.fields,
            tags=original.tags,
            decks=original.decks,
            note_type=original.note_type,
            card_ids=(2001, 2999),
            modified_seconds=999_999,
            guid="replacement-guid",
            title=original.title,
        )

        plan = self.index.plan_targeted_upsert([rescheduled])

        self.assertEqual(plan.changed, ())
        self.assertEqual(len(plan.card_scope_updates), 1)
        self.assertEqual(self.index.apply_upsert(plan), 0)
        self.assertEqual(self.index.generation, original_generation)
        self.assertEqual(
            self.index.get_documents((original.note_id,))[original.note_id].card_ids,
            (2001, 2999),
        )

    def test_searchable_deck_change_still_reindexes_the_note(self) -> None:
        original = sample_notes()[0]
        moved = IndexedNote(
            original.note_id,
            original.fields,
            tags=original.tags,
            decks=("AnKing::Moved",),
            note_type=original.note_type,
            card_ids=original.card_ids,
            modified_seconds=original.modified_seconds,
            guid=original.guid,
        )

        plan = self.index.plan_targeted_upsert([moved])

        self.assertEqual([note.note_id for note, _digest in plan.changed], [1001])
        self.assertEqual(self.index.apply_upsert(plan), 1)
        self.assertEqual(
            self.index.get_documents((1001,))[1001].decks,
            ("AnKing::Moved",),
        )

    def test_fts_search_honors_cancellation_before_sql_work(self) -> None:
        class Cancelled(RuntimeError):
            pass

        with self.assertRaises(Cancelled):
            self.index.search_fts(
                '"bupropion"',
                cancel_check=lambda: (_ for _ in ()).throw(Cancelled()),
            )

    def test_collection_database_names_are_refused(self) -> None:
        with self.assertRaises(UnsafeIndexPath):
            SmartSearchIndex(Path(self.temporary.name) / "collection.anki2")


class SemanticFusionTests(unittest.TestCase):
    def test_optional_semantic_provider_is_fused_and_explained(self) -> None:
        from backend.search import SemanticHit

        class FakeSemantic:
            def search(
                self,
                query,
                *,
                limit=50,
                allowed_note_ids=None,
                cancel_check=None,
            ):
                if cancel_check:
                    cancel_check()
                return [SemanticHit(1002, 0.92)]

        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(sample_notes())
            try:
                engine = SearchEngine(index, semantic_provider=FakeSemantic())
                response = engine.search(
                    "reduced ejection fraction therapy",
                    mode="semantic",
                )
                self.assertEqual(response.results[0].note_id, 1002)
                self.assertTrue(
                    any(
                        reason.kind == MatchKind.SEMANTIC
                        for reason in response.results[0].reasons
                    )
                )
            finally:
                index.close()

    def test_semantic_cutoff_uses_similarity_distribution_before_limit(self) -> None:
        from backend.search import SemanticHit

        notes = [
            IndexedNote(
                3000 + offset,
                {"Text": f"unrelated indexed note {offset}"},
            )
            for offset in range(20)
        ]

        class FakeSemantic:
            def search(
                self,
                query,
                *,
                limit=50,
                allowed_note_ids=None,
                cancel_check=None,
            ):
                del query, limit, allowed_note_ids
                if cancel_check:
                    cancel_check()
                scores = [0.90, 0.86, *([0.50] * 18)]
                assert len(notes) == len(scores)
                return [
                    SemanticHit(note.note_id, score)
                    for note, score in zip(notes, scores)
                ]

        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(notes)
            try:
                engine = SearchEngine(index, semantic_provider=FakeSemantic())
                response = engine.search(
                    "a concept absent from every note",
                    mode="semantic",
                    limit=1,
                )
                self.assertEqual(response.total_candidates, 2)
                self.assertEqual(len(response.results), 1)
                self.assertEqual(response.results[0].note_id, notes[0].note_id)
            finally:
                index.close()

    def test_semantic_cutoff_preserves_literal_match_below_floor(self) -> None:
        from backend.search import SemanticHit

        notes = [
            IndexedNote(
                4000 + offset,
                {
                    "Text": (
                        "rareterm appears literally here"
                        if offset == 19
                        else f"different medical concept {offset}"
                    )
                },
            )
            for offset in range(20)
        ]

        class FakeSemantic:
            def search(
                self,
                query,
                *,
                limit=50,
                allowed_note_ids=None,
                cancel_check=None,
            ):
                del query, limit, allowed_note_ids
                if cancel_check:
                    cancel_check()
                scores = [0.90, 0.86, *([0.50] * 18)]
                assert len(notes) == len(scores)
                return [
                    SemanticHit(note.note_id, score)
                    for note, score in zip(notes, scores)
                ]

        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(notes)
            try:
                engine = SearchEngine(index, semantic_provider=FakeSemantic())
                response = engine.search("rareterm", mode="semantic", limit=20)
                self.assertIn(notes[-1].note_id, [item.note_id for item in response.results])
            finally:
                index.close()

    def test_semantic_filter_cannot_raise_cutoff_or_remove_global_match(self) -> None:
        from backend.search import SemanticHit

        deck_ids = set(range(1, 16))
        notes = [
            IndexedNote(note_id, {"Text": f"indexed note {note_id}"})
            for note_id in range(1, 31)
        ]
        scores = {
            note_id: (0.50 if note_id == 1 else 0.90)
            for note_id in deck_ids
        }
        scores.update({note_id: 0.10 for note_id in range(16, 31)})
        received_scopes = []

        class FakeSemantic:
            def search(
                self,
                query,
                *,
                limit=50,
                allowed_note_ids=None,
                cancel_check=None,
            ):
                del query
                if cancel_check:
                    cancel_check()
                received_scopes.append(allowed_note_ids)
                eligible = (
                    scores
                    if allowed_note_ids is None
                    else {
                        note_id: score
                        for note_id, score in scores.items()
                        if note_id in allowed_note_ids
                    }
                )
                return [
                    SemanticHit(note_id, score)
                    for note_id, score in sorted(
                        eligible.items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:limit]
                ]

        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(notes)
            try:
                engine = SearchEngine(index, semantic_provider=FakeSemantic())
                unfiltered = engine.search("a1c", mode="semantic", limit=50)
                filtered = engine.search(
                    "deck:xyz a1c",
                    mode="semantic",
                    allowed_note_ids=deck_ids,
                    limit=50,
                )
            finally:
                index.close()

        expected = [
            result
            for result in unfiltered.results
            if result.note_id in deck_ids
        ]
        self.assertEqual(received_scopes, [None, None])
        self.assertIn(1, [result.note_id for result in expected])
        self.assertEqual(
            [result.note_id for result in filtered.results],
            [result.note_id for result in expected],
        )
        self.assertEqual(
            [result.score for result in filtered.results],
            [result.score for result in expected],
        )

    def test_semantic_mode_corrects_high_confidence_typo_before_embedding(self) -> None:
        from backend.search import SemanticHit

        seen_queries: list[str] = []

        class FakeSemantic:
            def search(
                self,
                query,
                *,
                limit=50,
                allowed_note_ids=None,
                cancel_check=None,
            ):
                del limit, allowed_note_ids
                if cancel_check:
                    cancel_check()
                seen_queries.append(query)
                return [SemanticHit(1001, 0.92)]

        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(sample_notes())
            index.set_aliases({"wellbutrin": ("bupropion",)}, source="test")
            try:
                engine = SearchEngine(index, semantic_provider=FakeSemantic())
                response = engine.search("buproprion", mode="semantic")
                self.assertEqual(seen_queries, ["bupropion"])
                self.assertTrue(response.corrections[0].automatic)
            finally:
                index.close()

    def test_smart_mode_does_not_start_semantic_runtime(self) -> None:
        class FailIfCalled:
            def search(
                self,
                query,
                *,
                limit=50,
                allowed_note_ids=None,
                cancel_check=None,
            ):
                del query, limit, allowed_note_ids, cancel_check
                raise AssertionError("Smart mode invoked semantic search")

        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            index.rebuild(sample_notes())
            try:
                engine = SearchEngine(index, semantic_provider=FailIfCalled())
                response = engine.search("bupropion", mode="smart")
                self.assertTrue(response.results)
            finally:
                index.close()


class AdaptiveSmartCutoffTests(unittest.TestCase):
    def test_all_term_match_removes_lower_coverage_or_tail(self) -> None:
        candidates = [
            RankedCandidate(
                1,
                score=0.12,
                source_ranks={"literal_strict": 1, "literal_broad": 1},
                matched_concepts=3,
                total_concepts=3,
            ),
            RankedCandidate(
                2,
                score=0.015,
                source_ranks={"literal_broad": 2},
                matched_concepts=2,
                total_concepts=3,
            ),
            RankedCandidate(
                3,
                score=0.014,
                source_ranks={"literal_broad": 3},
                matched_concepts=1,
                total_concepts=3,
            ),
        ]
        kept = _adaptive_relevance_cutoff(candidates, mode="smart")
        self.assertEqual([candidate.note_id for candidate in kept], [1])

    def test_best_partial_tier_is_kept_when_no_complete_match_exists(self) -> None:
        candidates = [
            RankedCandidate(
                1,
                source_ranks={"literal_broad": 1},
                matched_concepts=2,
                total_concepts=3,
            ),
            RankedCandidate(
                2,
                source_ranks={"literal_broad": 2},
                matched_concepts=1,
                total_concepts=3,
            ),
        ]
        kept = _adaptive_relevance_cutoff(candidates, mode="smart")
        self.assertEqual([candidate.note_id for candidate in kept], [1])

    def test_long_query_allows_one_missing_concept(self) -> None:
        candidates = [
            RankedCandidate(1, matched_concepts=4, total_concepts=4),
            RankedCandidate(2, matched_concepts=3, total_concepts=4),
            RankedCandidate(3, matched_concepts=2, total_concepts=4),
        ]
        kept = _adaptive_relevance_cutoff(candidates, mode="smart")
        self.assertEqual([candidate.note_id for candidate in kept], [1, 2])

    def test_typo_and_alias_sources_are_never_pruned_by_coverage(self) -> None:
        candidates = [
            RankedCandidate(1, matched_concepts=3, total_concepts=3),
            RankedCandidate(
                2,
                source_ranks={"corrected": 50},
                matched_concepts=0,
                total_concepts=3,
            ),
            RankedCandidate(
                3,
                source_ranks={"alias": 80},
                matched_concepts=0,
                total_concepts=3,
            ),
        ]
        kept = _adaptive_relevance_cutoff(candidates, mode="smart")
        self.assertEqual([candidate.note_id for candidate in kept], [1, 2, 3])


class SchemaUpgradeTests(unittest.TestCase):
    def test_old_derived_index_is_archived_until_compact_rebuild_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            legacy = sqlite3.connect(path)
            legacy.execute(
                "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            legacy.execute(
                "INSERT INTO metadata(key, value) VALUES('schema_version', '1')"
            )
            legacy.execute(
                """
                CREATE TABLE aliases(
                    alias TEXT NOT NULL,
                    canonical TEXT NOT NULL,
                    weight REAL NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY(alias, canonical, source)
                )
                """
            )
            legacy.execute(
                """
                INSERT INTO aliases(alias, canonical, weight, source)
                VALUES('custombrand', 'customgeneric', 1.0, 'user')
                """
            )
            legacy.execute("CREATE TABLE legacy_marker(value TEXT)")
            legacy.commit()
            legacy.close()

            index = SmartSearchIndex(path)
            try:
                backups = [
                    candidate
                    for candidate in path.parent.glob(path.name + ".schema-backup-*")
                    if not candidate.name.endswith(("-wal", "-shm"))
                ]
                self.assertEqual(len(backups), 1)
                self.assertEqual(index.stats().note_count, 0)
                self.assertEqual(
                    index.alias_map()["custombrand"],
                    {"customgeneric"},
                )
                index.rebuild(sample_notes())
                self.assertFalse(
                    list(path.parent.glob(path.name + ".schema-backup-*"))
                )
                self.assertEqual(index.stats().note_count, 3)
                self.assertEqual(
                    index.alias_map()["custombrand"],
                    {"customgeneric"},
                )
            finally:
                index.close()


if __name__ == "__main__":
    unittest.main()
