from __future__ import annotations

import unittest

from ui.suspension_query import (
    SuspensionQueryState,
    analyze_suspension_query,
    apply_suspension_filter,
)


class SuspensionQueryTests(unittest.TestCase):
    def test_empty_and_free_text_gain_one_filter(self) -> None:
        self.assertEqual(apply_suspension_filter("", True), "is:suspended")
        self.assertEqual(
            apply_suspension_filter("bupropion", True),
            "bupropion is:suspended",
        )

    def test_positive_top_level_terms_are_on_and_key_is_case_insensitive(self) -> None:
        for query in (
            "is:suspended",
            "IS:suspended",
            "is:suspended bupropion",
            "bupropion is:suspended tag:pharm",
        ):
            with self.subTest(query=query):
                self.assertIs(
                    analyze_suspension_query(query).state,
                    SuspensionQueryState.ON,
                )

    def test_removal_handles_positions_duplicates_and_explicit_and(self) -> None:
        cases = {
            "is:suspended bupropion": "bupropion",
            "bupropion is:suspended": "bupropion",
            "bupropion is:suspended tag:pharm": "bupropion tag:pharm",
            "is:suspended is:suspended": "",
            "a AND is:suspended AND b": "a AND b",
            "is:suspended AND b": "b",
            "a AND is:suspended": "a",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(
                    apply_suspension_filter(query, False),
                    expected,
                )

    def test_top_level_or_is_grouped_before_filter_is_added(self) -> None:
        self.assertEqual(
            apply_suspension_filter("foo OR bar", True),
            "(foo OR bar) is:suspended",
        )
        self.assertEqual(
            apply_suspension_filter("(foo OR bar) tag:pharm", True),
            "(foo OR bar) tag:pharm is:suspended",
        )
        self.assertEqual(
            apply_suspension_filter("  foo OR bar  ", True),
            "(  foo OR bar  ) is:suspended",
        )

    def test_positive_filter_in_or_or_nested_group_is_custom(self) -> None:
        for query in (
            "foo OR is:suspended",
            "is:suspended OR foo",
            "(is:suspended)",
            "(foo is:suspended)",
        ):
            with self.subTest(query=query):
                analysis = analyze_suspension_query(query)
                self.assertIs(analysis.state, SuspensionQueryState.CUSTOM)
                self.assertEqual(apply_suspension_filter(query, False), query)

    def test_negative_conflicting_and_wrong_value_case_are_custom(self) -> None:
        for query in (
            "-is:suspended",
            "- is:suspended",
            "foo -IS:suspended",
            "is:suspended -is:suspended",
            "is:SUSPENDED",
        ):
            with self.subTest(query=query):
                analysis = analyze_suspension_query(query)
                self.assertIs(analysis.state, SuspensionQueryState.CUSTOM)
                self.assertEqual(apply_suspension_filter(query, True), query)
                self.assertEqual(apply_suspension_filter(query, False), query)

    def test_whole_term_quoted_and_malformed_queries_are_custom(self) -> None:
        for query in (
            '"is:suspended"',
            '-"is:suspended"',
            'foo"is:suspended"',
            '"is:suspended"foo',
            'foo:bar:"is:suspended"',
            '"foo"AND',
            'field:"x"AND',
            '"unfinished',
            "(unfinished",
            "unfinished)",
            "foo AND",
            "AND foo",
            "foo AND AND bar",
            "()",
            "is:",
            "-is:",
            "flag:",
            "cid:",
            "prop:",
        ):
            with self.subTest(query=query):
                self.assertIs(
                    analyze_suspension_query(query).state,
                    SuspensionQueryState.CUSTOM,
                )
                self.assertEqual(apply_suspension_filter(query, True), query)

    def test_field_values_and_substrings_are_not_false_matches(self) -> None:
        for query in (
            "tag:is:suspended",
            'field:"is:suspended"',
            'field:"-is:suspended"',
            "notis:suspended",
        ):
            with self.subTest(query=query):
                self.assertIs(
                    analyze_suspension_query(query).state,
                    SuspensionQueryState.OFF,
                )
                self.assertEqual(
                    apply_suspension_filter(query, True),
                    query + " is:suspended",
                )

    def test_version_dependent_whitespace_fails_closed(self) -> None:
        escaped = r"literal\(text\)"
        self.assertIs(
            analyze_suspension_query(escaped).state,
            SuspensionQueryState.OFF,
        )
        for query in (
            "prefix\tis:suspended",
            "foo\tOR\tbar",
            "prefix\nis:suspended",
            "prefix\u00a0is:suspended",
        ):
            with self.subTest(query=query):
                self.assertIs(
                    analyze_suspension_query(query).state,
                    SuspensionQueryState.CUSTOM,
                )
                self.assertEqual(apply_suspension_filter(query, True), query)

    def test_owned_edits_preserve_unrelated_source_whitespace(self) -> None:
        self.assertEqual(
            apply_suspension_filter(
                "  alpha   is:suspended   beta  ",
                False,
            ),
            "  alpha   beta  ",
        )
        self.assertEqual(
            apply_suspension_filter("  alpha  ", True),
            "  alpha  is:suspended",
        )

    def test_enabling_and_disabling_are_idempotent(self) -> None:
        query = "bupropion is:suspended"
        self.assertEqual(apply_suspension_filter(query, True), query)
        self.assertEqual(
            apply_suspension_filter("bupropion", False),
            "bupropion",
        )


if __name__ == "__main__":
    unittest.main()
