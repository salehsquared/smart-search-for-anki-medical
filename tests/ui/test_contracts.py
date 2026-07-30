from __future__ import annotations

import unittest

from ui.contracts import (
    FilterChip,
    HighlightSpan,
    IndexState,
    IndexStatus,
    MatchKind,
    SemanticState,
    SemanticStatus,
    clamp_spans,
    merge_spans,
    normalize_query,
    parse_filter_tokens,
    remove_filter_token,
)


class ContractTests(unittest.TestCase):
    def test_normalizes_and_parses_structured_filters(self) -> None:
        filters, remainder = parse_filter_tokens(
            '  deck:AnKing   tag:"renal phys"   BUPROPION  '
        )

        self.assertEqual(normalize_query(remainder), "BUPROPION")
        self.assertEqual(
            [(item.key, item.value) for item in filters],
            [("deck", "AnKing"), ("tag", "renal phys")],
        )
        self.assertEqual(filters[1].token, 'tag:"renal phys"')

    def test_removes_only_requested_filter(self) -> None:
        query = "deck:AnKing tag:cardio hypertension"
        self.assertEqual(
            remove_filter_token(query, FilterChip("tag", "cardio")),
            "deck:AnKing hypertension",
        )

    def test_removes_authoritative_negative_or_hyphenated_filter_exactly(self) -> None:
        negative = FilterChip(
            "-tag",
            "cardio",
            raw_token="-tag:cardio",
        )
        custom_data = FilterChip(
            "has-cd",
            "v",
            raw_token="has-cd:v",
        )

        self.assertEqual(
            remove_filter_token(
                "deck:AnKing -tag:cardio has-cd:v bupropion",
                negative,
            ),
            "deck:AnKing has-cd:v bupropion",
        )
        self.assertEqual(
            remove_filter_token("has-cd:v bupropion", custom_data),
            "bupropion",
        )

    def test_parser_handles_negative_hyphenated_empty_and_wildcard_filters(self) -> None:
        filters, remainder = parse_filter_tokens(
            "-tag:cardio has-cd:v Front: Fr*:text https://example.test bupropion"
        )

        self.assertEqual(
            tuple(item.token for item in filters),
            ("-tag:cardio", "has-cd:v", "Front:", "Fr*:text"),
        )
        self.assertEqual(
            remainder,
            "https://example.test bupropion",
        )

    def test_clamps_and_merges_highlight_spans(self) -> None:
        spans = (
            HighlightSpan(2, 6),
            HighlightSpan(4, 10, MatchKind.ALIAS),
            HighlightSpan(18, 30),
        )
        merged = merge_spans(clamp_spans(spans, 20))
        self.assertEqual([(item.start, item.end) for item in merged], [(2, 10), (18, 20)])

    def test_semantic_state_is_distinct_from_lexical_index(self) -> None:
        status = IndexStatus(
            state=IndexState.READY,
            semantic=SemanticStatus(SemanticState.NOT_INSTALLED),
        )
        self.assertTrue(status.ready)
        self.assertFalse(status.semantic.ready)
        self.assertIn("needs setup", status.semantic.summary)

    def test_semantic_autostart_is_explicit_in_summary(self) -> None:
        status = SemanticStatus(
            SemanticState.MODEL_READY,
            auto_start=True,
        )

        self.assertIn("starts automatically", status.summary)

    def test_ready_summary_never_exposes_internal_model_name(self) -> None:
        status = IndexStatus(
            IndexState.READY,
            model_name="MedEmbed-small-v0.1-int8",
        )

        self.assertEqual(status.summary, "Smart & Exact ready")
        self.assertNotIn("MedEmbed", status.summary)


if __name__ == "__main__":
    unittest.main()
