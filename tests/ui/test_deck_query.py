from __future__ import annotations

import unittest

from ui.deck_query import (
    DeckScopeKind,
    analyze_deck_query,
    apply_deck_selection,
    build_deck_clause,
)


class DeckQueryTests(unittest.TestCase):
    def test_query_without_deck_filter_is_all(self) -> None:
        analysis = analyze_deck_query('  "heart failure" tag:cardio  ')

        self.assertIs(analysis.kind, DeckScopeKind.ALL)
        self.assertEqual(analysis.names, ())
        self.assertIsNone(analysis.clause_start)
        self.assertIsNone(analysis.clause_end)
        self.assertEqual(analysis.remaining_query, '"heart failure" tag:cardio')
        self.assertEqual(analysis.reason, "")

    def test_single_positive_exact_token_is_selected_with_source_span(self) -> None:
        query = 'bupropion deck:"Psychiatry Cards" tag:pharm'
        analysis = analyze_deck_query(query)

        self.assertIs(analysis.kind, DeckScopeKind.SELECTED)
        self.assertEqual(analysis.names, ("Psychiatry Cards",))
        assert analysis.clause_start is not None
        assert analysis.clause_end is not None
        self.assertEqual(
            query[analysis.clause_start : analysis.clause_end],
            'deck:"Psychiatry Cards"',
        )
        self.assertEqual(analysis.remaining_query, "bupropion tag:pharm")

    def test_single_token_may_be_an_explicit_top_level_conjunct(self) -> None:
        analysis = analyze_deck_query(
            'tag:pharm AND DECK:"Psychiatry" AND bupropion'
        )

        self.assertIs(analysis.kind, DeckScopeKind.SELECTED)
        self.assertEqual(analysis.names, ("Psychiatry",))
        self.assertEqual(analysis.remaining_query, "tag:pharm AND bupropion")

    def test_parenthesized_deck_or_group_is_selected(self) -> None:
        analysis = analyze_deck_query(
            'bupropion (deck:"Step 1" OR deck:"Step 2") tag:pharm'
        )

        self.assertIs(analysis.kind, DeckScopeKind.SELECTED)
        self.assertEqual(analysis.names, ("Step 1", "Step 2"))
        self.assertEqual(analysis.remaining_query, "bupropion tag:pharm")

    def test_nested_non_deck_boolean_group_does_not_make_clause_custom(self) -> None:
        analysis = analyze_deck_query(
            '(tag:cardio OR tag:renal) deck:"AnKing" bupropion'
        )

        self.assertIs(analysis.kind, DeckScopeKind.SELECTED)
        self.assertEqual(analysis.names, ("AnKing",))

    def test_builder_quotes_and_escapes_all_native_wildcards(self) -> None:
        clause = build_deck_clause(
            (
                "Clinical Medicine",
                'Say "yes"',
                r"Path\Cards",
                "Literal*Star",
                "High_Yield",
            )
        )

        self.assertEqual(
            clause,
            '(deck:"Clinical Medicine" OR deck:"Say \\"yes\\"" OR '
            'deck:"Path\\\\Cards" OR deck:"Literal\\*Star" OR '
            'deck:"High\\_Yield")',
        )
        self.assertEqual(
            analyze_deck_query(clause).names,
            (
                "Clinical Medicine",
                'Say "yes"',
                r"Path\Cards",
                "Literal*Star",
                "High_Yield",
            ),
        )

    def test_reserved_lowercase_deck_values_are_never_misrepresented(self) -> None:
        for query in ("deck:current", 'deck:"current"', "deck:filtered"):
            with self.subTest(query=query):
                self.assertIs(
                    analyze_deck_query(query).kind,
                    DeckScopeKind.CUSTOM,
                )

        # Anki reserves only the lowercase spelling, while deck-name matching
        # is case-insensitive. The capitalized query addresses a real deck.
        self.assertEqual(build_deck_clause(("current",)), 'deck:"Current"')
        self.assertEqual(build_deck_clause(("filtered",)), 'deck:"Filtered"')
        self.assertIs(
            analyze_deck_query('deck:"Current"').kind,
            DeckScopeKind.SELECTED,
        )

    def test_builder_rejects_empty_names_but_empty_selection_means_all(self) -> None:
        self.assertEqual(build_deck_clause(()), "")
        for names in (("",), ("A", "   ")):
            with self.subTest(names=names), self.assertRaises(ValueError):
                build_deck_clause(names)

    def test_duplicate_names_are_case_insensitive_and_stable(self) -> None:
        self.assertEqual(
            build_deck_clause(("Step 1", "step 1", "STEP 2", "Step 2")),
            '(deck:"Step 1" OR deck:"STEP 2")',
        )

    def test_parent_selection_removes_descendant_redundancy_in_either_order(self) -> None:
        expected = 'deck:"AnKing"'
        self.assertEqual(
            build_deck_clause(("AnKing", "AnKing::Step 1", "AnKing::Step 2")),
            expected,
        )
        self.assertEqual(
            build_deck_clause(("AnKing::Step 1::Cardio", "anking::step 1", "AnKing")),
            expected,
        )
        self.assertEqual(
            build_deck_clause(("AnKing::Step 1", "AnKing::Step 2")),
            '(deck:"AnKing::Step 1" OR deck:"AnKing::Step 2")',
        )

    def test_analysis_canonicalizes_duplicate_and_parent_child_group_members(self) -> None:
        analysis = analyze_deck_query(
            '(deck:"AnKing::Step 1" OR deck:"anking" OR deck:"AnKing")'
        )

        self.assertIs(analysis.kind, DeckScopeKind.SELECTED)
        self.assertEqual(analysis.names, ("anking",))

    def test_negative_wildcard_repeated_and_mixed_boolean_are_custom(self) -> None:
        queries = (
            '-deck:"AnKing" bupropion',
            '- deck:"AnKing" bupropion',
            'bupropion - deck:"AnKing"',
            'deck:"AnKing*" bupropion',
            'deck:"AnKing_Step" bupropion',
            'deck:"A" deck:"B" bupropion',
            'deck:"A" OR bupropion',
            '(deck:"A" AND deck:"B") bupropion',
            '(deck:"A" OR tag:cardio) bupropion',
            '-(deck:"A" OR deck:"B") bupropion',
            '"deck:Whole term" bupropion',
            '-"deck:AnKing" bupropion',
            'deck:"Unfinished bupropion',
            'deck:"A" AND',
            'AND deck:"A"',
            'Or deck:"A"',
        )
        for query in queries:
            with self.subTest(query=query):
                analysis = analyze_deck_query(query)
                self.assertIs(analysis.kind, DeckScopeKind.CUSTOM)
                self.assertEqual(analysis.remaining_query, query)
                self.assertTrue(analysis.reason)

    def test_quote_adjacent_native_syntax_is_preserved_conservatively(self) -> None:
        for query in (
            '"foo"OR deck:A',
            '"foo"OR -deck:A',
            '"deck:A"foo',
            'foo"deck:A"',
            'foo-"deck:A"',
            '(foo)-"deck:A"',
            '"foo"-"deck:A"',
            'x-"deck:A"',
            '--"deck:A"',
            '"foo"deck:A',
        ):
            with self.subTest(query=query):
                analysis = analyze_deck_query(query)
                self.assertIs(analysis.kind, DeckScopeKind.CUSTOM)
                with self.assertRaises(ValueError):
                    apply_deck_selection(query, ("D",))

        self.assertEqual(
            apply_deck_selection('"foo"OR bar', ("D",)),
            '("foo"OR bar) deck:"D"',
        )

    def test_native_whole_term_quotes_are_preserved_with_a_managed_deck(self) -> None:
        query = '"animal front:a dog" deck:"AnKing"'
        analysis = analyze_deck_query(query)

        self.assertIs(analysis.kind, DeckScopeKind.SELECTED)
        self.assertEqual(analysis.remaining_query, '"animal front:a dog"')
        self.assertEqual(
            apply_deck_selection(query, ("Personal",)),
            '"animal front:a dog" deck:"Personal"',
        )

    def test_quoted_non_deck_field_values_do_not_disable_picker(self) -> None:
        for query in (
            'Front:"deck:A"',
            'tag:"deck:A"',
            'note:"deck:A"',
            'foo:"deck:A"',
            're:"deck:A"',
            'nc:"deck:A"',
            'foo:"x deck:A"',
        ):
            with self.subTest(query=query):
                analysis = analyze_deck_query(query)
                self.assertIs(analysis.kind, DeckScopeKind.ALL)
                self.assertEqual(
                    apply_deck_selection(query, ("D",)),
                    f'{query} deck:"D"',
                )

    def test_quote_after_a_second_node_colon_preserves_native_deck_filter(self) -> None:
        for query in ('foo::"deck:A"', 'foo:bar:"deck:A"'):
            with self.subTest(query=query):
                analysis = analyze_deck_query(query)
                self.assertIs(analysis.kind, DeckScopeKind.CUSTOM)
                with self.assertRaises(ValueError):
                    apply_deck_selection(query, ("D",))

    def test_invalid_native_escapes_are_custom(self) -> None:
        for query in (r'deck:"A\q"', r'deck:A\ B', r'deck:"A\%"'):
            with self.subTest(query=query):
                self.assertIs(
                    analyze_deck_query(query).kind,
                    DeckScopeKind.CUSTOM,
                )

    def test_apply_replaces_recognized_clause_in_place(self) -> None:
        self.assertEqual(
            apply_deck_selection(
                'bupropion deck:"Old" tag:pharm',
                ("New Deck",),
            ),
            'bupropion deck:"New Deck" tag:pharm',
        )
        self.assertEqual(
            apply_deck_selection(
                'bupropion (deck:"A" OR deck:"B") tag:pharm',
                ("C", "D"),
            ),
            'bupropion (deck:"C" OR deck:"D") tag:pharm',
        )

    def test_all_removes_only_a_recognized_clause_and_adjacent_and(self) -> None:
        cases = {
            'deck:"A" AND bupropion': "bupropion",
            'bupropion AND deck:"A"': "bupropion",
            'tag:x AND deck:"A" AND bupropion': "tag:x AND bupropion",
            'tag:x (deck:"A" OR deck:"B") bupropion': "tag:x bupropion",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(apply_deck_selection(query, ()), expected)

    def test_apply_to_all_conjoins_and_protects_top_level_or_precedence(self) -> None:
        self.assertEqual(
            apply_deck_selection("bupropion tag:pharm", ("A", "B")),
            'bupropion tag:pharm (deck:"A" OR deck:"B")',
        )
        self.assertEqual(
            apply_deck_selection("bupropion OR sertraline", ("Psych",)),
            '(bupropion OR sertraline) deck:"Psych"',
        )
        self.assertEqual(apply_deck_selection("  bupropion  ", ()), "bupropion")

    def test_custom_syntax_is_unchanged_and_rejected(self) -> None:
        query = '(deck:"A" OR tag:cardio) bupropion'
        analysis = analyze_deck_query(query)

        self.assertEqual(analysis.remaining_query, query)
        with self.assertRaises(ValueError):
            apply_deck_selection(query, ("New",))
        self.assertEqual(query, '(deck:"A" OR tag:cardio) bupropion')

    def test_custom_deck_syntax_is_always_preserved(self) -> None:
        queries = (
            '-deck:"Old" bupropion tag:x',
            'deck:"A*" bupropion',
            'deck:"A" deck:"B" bupropion',
            '(deck:"A" AND deck:"B") bupropion',
            '-(deck:"A" OR deck:"B") bupropion',
        )
        for query in queries:
            with self.subTest(query=query), self.assertRaises(ValueError):
                apply_deck_selection(query, ("New",))

    def test_unsafe_mixed_boolean_is_always_rejected(self) -> None:
        queries = (
            'deck:"A" OR bupropion',
            'tag:x OR deck:"A"',
            '(deck:"A" OR tag:x) bupropion',
            '-(deck:"A" OR tag:x) bupropion',
            'deck:"Unfinished bupropion',
        )
        for query in queries:
            with self.subTest(query=query), self.assertRaises(ValueError):
                apply_deck_selection(query, ("New",))

    def test_whole_term_negative_edge_cases_are_not_rewritten(self) -> None:
        self.assertIs(
            analyze_deck_query('-"deck:A" bupropion').kind,
            DeckScopeKind.CUSTOM,
        )
        # A minus inside the whole-term quote is not Anki's deck negation.
        self.assertIs(
            analyze_deck_query('"-deck:A" bupropion').kind,
            DeckScopeKind.ALL,
        )

    def test_non_ascii_names_are_not_over_deduplicated(self) -> None:
        self.assertEqual(
            build_deck_clause(("Straße", "STRASSE")),
            '(deck:"Straße" OR deck:"STRASSE")',
        )

    def test_non_ascii_key_and_non_native_whitespace_are_not_rewritten(self) -> None:
        for query in (
            "decK:A bupropion",
            "foo\tdeck:A",
            "foo\u00a0deck:A",
            "foo\u2003deck:A",
        ):
            with self.subTest(query=query):
                analysis = analyze_deck_query(query)
                self.assertIs(analysis.kind, DeckScopeKind.ALL)
                self.assertEqual(
                    apply_deck_selection(query, ("D",)),
                    f'{query} deck:"D"',
                )

    def test_ideographic_space_is_a_native_term_separator(self) -> None:
        query = "bupropion\u3000deck:A"
        analysis = analyze_deck_query(query)

        self.assertIs(analysis.kind, DeckScopeKind.SELECTED)
        self.assertEqual(analysis.names, ("A",))
        self.assertEqual(analysis.remaining_query, "bupropion")


if __name__ == "__main__":
    unittest.main()
