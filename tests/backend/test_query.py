from __future__ import annotations

import unittest

from backend.query import QueryParser


class QueryParserTests(unittest.TestCase):
    def test_is_filter_is_not_misread_as_free_text(self) -> None:
        parsed = QueryParser().parse("deck:AnKing is:new buproprion")

        self.assertEqual(parsed.anki_filter_query, "deck:AnKing is:new")
        self.assertEqual(
            tuple(term.normalized for term in parsed.free_terms),
            ("buproprion",),
        )

    def test_separates_quoted_filter_and_casefolded_free_text(self) -> None:
        parsed = QueryParser().parse('deck:"AnKing Overhaul" BUPROPRION')
        self.assertEqual(parsed.anki_filter_query, 'deck:"AnKing Overhaul"')
        self.assertEqual(parsed.anki_filters, ('deck:"AnKing Overhaul"',))
        self.assertEqual(parsed.free_terms[0].normalized, "buproprion")

    def test_preserves_negative_filter_and_free_phrase(self) -> None:
        parsed = QueryParser().parse('-tag:suspended "heart failure"')
        self.assertEqual(parsed.anki_filter_query, "-tag:suspended")
        self.assertTrue(parsed.free_terms[0].quoted)
        self.assertEqual(parsed.free_terms[0].text, "heart failure")

    def test_filter_only_boolean_group_is_preserved(self) -> None:
        parsed = QueryParser().parse('(deck:A OR deck:B) "heart failure"')
        self.assertEqual(parsed.anki_filter_query, "( deck:A OR deck:B )")
        self.assertEqual(parsed.anki_filters, ("deck:A", "deck:B"))
        self.assertEqual(len(parsed.free_terms), 1)

    def test_mixed_boolean_is_not_silently_split(self) -> None:
        parsed = QueryParser().parse("deck:A OR bupropion")
        self.assertFalse(parsed.requires_anki_filter)
        self.assertTrue(parsed.native_search_required)
        self.assertTrue(any("Boolean OR" in warning for warning in parsed.warnings))

    def test_arbitrary_field_requires_allowlist(self) -> None:
        default = QueryParser().parse("Text:bupropion")
        allowed = QueryParser(field_names=("Text",)).parse("Text:bupropion")
        self.assertFalse(default.requires_anki_filter)
        self.assertEqual(allowed.anki_filter_query, "Text:bupropion")

    def test_quoted_field_name_is_allowlisted(self) -> None:
        parsed = QueryParser(field_names=("Back Extra",)).parse(
            '"Back Extra":bupropion smoking'
        )
        self.assertEqual(parsed.anki_filter_query, '"Back Extra":bupropion')
        self.assertEqual(parsed.free_terms[0].normalized, "smoking")

    def test_colon_and_fts_punctuation_remain_plain_input(self) -> None:
        parsed = QueryParser().parse('https://example.test bupropion" OR *')
        self.assertFalse(parsed.requires_anki_filter)
        self.assertGreaterEqual(len(parsed.free_terms), 2)

    def test_unmatched_quote_is_reported_not_raised(self) -> None:
        parsed = QueryParser().parse('"heart failure')
        self.assertTrue(parsed.warnings)

    def test_current_native_operators_and_empty_fields_are_filters(self) -> None:
        parser = QueryParser(field_names=("Front",))
        for query in (
            'preset:"Default"',
            "w:dog*",
            "sc:mnemonic",
            "has-cd:v",
            "Front:",
        ):
            with self.subTest(query=query):
                parsed = parser.parse(query)
                self.assertEqual(parsed.anki_filter_query, query)
                self.assertFalse(parsed.native_search_required)

    def test_wildcard_field_names_are_resolved_against_known_fields(self) -> None:
        parsed = QueryParser(field_names=("Front", "Back")).parse(
            "Fr*:text bupropion"
        )

        self.assertEqual(parsed.anki_filter_query, "Fr*:text")
        self.assertEqual(
            tuple(term.normalized for term in parsed.free_terms),
            ("bupropion",),
        )

    def test_whole_quoted_native_filter_is_preserved(self) -> None:
        parsed = QueryParser().parse('"deck:French Words" bupropion')

        self.assertEqual(parsed.anki_filter_query, '"deck:French Words"')
        self.assertEqual(parsed.anki_filters, ('"deck:French Words"',))

    def test_free_boolean_group_negation_and_wildcards_require_native_search(self) -> None:
        for query in (
            "bupropion OR sertraline",
            "bupropion (smoking OR depression)",
            "-(bupropion OR sertraline)",
            "buprop*",
            "d_g",
        ):
            with self.subTest(query=query):
                self.assertTrue(QueryParser().parse(query).native_search_required)

    def test_filter_boolean_group_remains_safe_for_hybrid_ranking(self) -> None:
        parsed = QueryParser().parse(
            '(deck:A OR deck:B) "heart failure"'
        )

        self.assertFalse(parsed.native_search_required)
        self.assertEqual(parsed.anki_filter_query, "( deck:A OR deck:B )")

    def test_ungrouped_filter_or_with_free_text_uses_native_precedence(self) -> None:
        parsed = QueryParser().parse("deck:A OR deck:B bupropion")

        self.assertTrue(parsed.native_search_required)
        self.assertEqual(parsed.anki_filter_query, "")

    def test_negated_filter_group_is_never_silently_de_negated(self) -> None:
        parsed = QueryParser().parse(
            "-(deck:A OR deck:B) bupropion"
        )

        self.assertTrue(parsed.native_search_required)
        self.assertEqual(parsed.anki_filter_query, "")


if __name__ == "__main__":
    unittest.main()
