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
                return [
                    SemanticHit(note.note_id, score)
                    for note, score in zip(notes, scores, strict=True)
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
                return [
                    SemanticHit(note.note_id, score)
                    for note, score in zip(notes, scores, strict=True)
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
                backups = list(path.parent.glob(path.name + ".schema-backup-*"))
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
