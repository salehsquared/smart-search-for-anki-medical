from __future__ import annotations

import unittest

from backend.text import (
    PRIMARY_DISPLAY_LIMIT,
    SUPPORT_DISPLAY_LIMIT,
    display_lines,
    make_snippet,
    normalize_text,
    normalized_spans,
    strip_cloze,
    strip_html_and_cloze,
    tokenize,
)


class TextTests(unittest.TestCase):
    def test_nfkc_casefold_diacritics_and_medical_symbols(self) -> None:
        self.assertEqual(normalize_text("ＢＥＨÇET"), "behcet")
        self.assertEqual(normalize_text("Straße"), "strasse")
        self.assertEqual(tokenize("β-blocker"), ("beta", "blocker"))

    def test_html_cloze_media_and_hidden_content_are_removed(self) -> None:
        value = (
            "<style>secret css</style><div>{{c1::Bupropion::antidepressant}}</div>"
            "<script>secret js</script><img alt='molecule' src='x'>[sound:x.mp3]"
        )
        self.assertEqual(
            strip_html_and_cloze(value),
            "Bupropion molecule",
        )

    def test_only_actual_cloze_markup_is_unwrapped(self) -> None:
        self.assertEqual(strip_cloze("{{not-a-cloze}} {{c2::answer}}"), "{{not-a-cloze}} answer")

    def test_normalized_spans_map_unicode_expansion_to_source_text(self) -> None:
        self.assertEqual(normalized_spans("Behçet and β-blocker", "behcet"), ((0, 6),))
        self.assertEqual(normalized_spans("Behçet and β-blocker", "beta"), ((11, 12),))

    def test_match_centered_snippet_uses_original_unicode_offsets(self) -> None:
        source = ("β " * 100) + "warfarin target"
        snippet = make_snippet(source, ("warfarin",), radius=20, maximum=80)
        self.assertIn("warfarin", snippet)


class DisplayLinesTests(unittest.TestCase):
    def test_text_and_extra_form_the_compact_pair(self) -> None:
        primary, support = display_lines(
            {
                "Text": "{{c1::Bupropion}} is used for depression.",
                "Extra": "<b>Wellbutrin</b> inhibits dopamine reuptake.",
            }
        )
        self.assertEqual(primary, "Bupropion is used for depression.")
        self.assertEqual(support, "Wellbutrin inhibits dopamine reuptake.")

    def test_front_and_back_are_used_without_a_text_field(self) -> None:
        primary, support = display_lines(
            {
                "Front": "First-line therapy for hypertension?",
                "Back": "ACE inhibitors in chronic kidney disease.",
            }
        )
        self.assertEqual(primary, "First-line therapy for hypertension?")
        # ``Back`` is not ``Extra``: it stays searchable but never displays.
        self.assertEqual(support, "")

    def test_field_name_preferences_are_case_insensitive(self) -> None:
        primary, support = display_lines(
            {
                "tExT": "Primary content.",
                "EXTRA": "Supporting content.",
            }
        )
        self.assertEqual((primary, support), ("Primary content.", "Supporting content."))

    def test_front_is_preferred_over_question(self) -> None:
        primary, _support = display_lines(
            {
                "Question": "Question wording.",
                "Front": "Front wording.",
            }
        )
        self.assertEqual(primary, "Front wording.")

    def test_prompt_and_header_are_supported_primary_names(self) -> None:
        prompt, _support = display_lines(
            {"Header": "Header wording.", "Prompt": "Prompt wording."}
        )
        header, _support = display_lines({"Header": "Header wording."})
        self.assertEqual(prompt, "Prompt wording.")
        self.assertEqual(header, "Header wording.")

    def test_first_noninternal_field_is_used_without_preferred_names(self) -> None:
        primary, support = display_lines(
            {
                "AnkiHub ID": "d3adb33f-1234",
                "Stem": "Mitral valve prolapse murmur.",
                "Notes": "Mid-systolic click.",
            }
        )
        self.assertEqual(primary, "Mitral valve prolapse murmur.")
        self.assertEqual(support, "")

    def test_empty_extra_yields_no_support_line(self) -> None:
        primary, support = display_lines(
            {
                "Text": "Bupropion is used for depression.",
                "Extra": "  \n ",
            }
        )
        self.assertEqual(primary, "Bupropion is used for depression.")
        self.assertEqual(support, "")

    def test_non_extra_fields_never_become_the_support_line(self) -> None:
        primary, support = display_lines(
            {
                "Text": "Beta blockers reduce mortality after infarction.",
                "Back": "Back wording.",
                "Answer": "Answer wording.",
                "Explanation": "Explanation wording.",
                "Remarks": "Remarks wording.",
                "Back Extra": "Back-extra wording.",
                "Extra 1": "Extra-one wording.",
                "Lecture Notes": "Carvedilol also blocks alpha-1 receptors.",
            },
            ("carvedilol",),
        )
        self.assertEqual(
            primary, "Beta blockers reduce mortality after infarction."
        )
        self.assertEqual(support, "")

    def test_match_later_in_extra_becomes_match_centered_excerpt(self) -> None:
        fields = {
            "Text": "Beta blockers reduce mortality after myocardial infarction.",
            "Extra": ("General context. " * 20) + "Carvedilol also blocks alpha-1.",
            "Lecture Notes": "Unrelated lecture wording about carvedilol.",
        }
        primary, support = display_lines(fields, ("carvedilol",))
        self.assertEqual(
            primary,
            "Beta blockers reduce mortality after myocardial infarction.",
        )
        self.assertIn("Carvedilol", support)
        self.assertNotIn("Unrelated lecture", support)

    def test_extra_support_shows_its_start_without_a_late_match(self) -> None:
        fields = {
            "Text": "Beta blockers reduce mortality after myocardial infarction.",
            "Extra": "Avoid in acute decompensated heart failure.",
            "Lecture Notes": "Carvedilol also blocks alpha-1 receptors.",
        }
        primary, support = display_lines(fields, ("carvedilol",))
        self.assertEqual(
            primary,
            "Beta blockers reduce mortality after myocardial infarction.",
        )
        # The Lecture Notes match stays searchable but is never displayed;
        # the supporting line remains the bounded Extra excerpt.
        self.assertEqual(support, "Avoid in acute decompensated heart failure.")

    def test_visible_primary_match_keeps_preferred_support(self) -> None:
        primary, support = display_lines(
            {
                "Text": "Bupropion treats depression.",
                "Extra": "Brand name Wellbutrin.",
                "Lecture Notes": "Bupropion inhibits dopamine reuptake.",
            },
            ("bupropion",),
        )
        self.assertEqual(primary, "Bupropion treats depression.")
        self.assertEqual(support, "Brand name Wellbutrin.")

    def test_needle_match_is_case_and_diacritic_insensitive(self) -> None:
        _primary, support = display_lines(
            {
                "Text": "Hemolytic uremic syndrome triad.",
                "Extra": "Behçet disease features oral ulcers.",
            },
            ("BEHCET",),
        )
        self.assertIn("Behçet", support)

    def test_support_never_repeats_the_primary_text(self) -> None:
        primary, support = display_lines(
            {
                "Text": "Duplicated card content.",
                "Extra": "Duplicated card content.",
            }
        )
        self.assertEqual(primary, "Duplicated card content.")
        self.assertEqual(support, "")

    def test_normalized_duplicate_support_is_suppressed(self) -> None:
        primary, support = display_lines(
            {
                "Text": "Behçet disease",
                "Extra": "  BEHCET   DISEASE  ",
            }
        )
        self.assertEqual(primary, "Behçet disease")
        self.assertEqual(support, "")

    def test_duplicate_extra_is_never_replaced_by_another_field(self) -> None:
        primary, support = display_lines(
            {
                "Text": "Primary wording.",
                "Extra": "Primary wording.",
                "Back Extra": "Distinct supporting wording.",
            }
        )
        # The duplicate Extra is suppressed and ``Back Extra`` is not a real
        # ``Extra`` field, so no supporting line is invented.
        self.assertEqual(primary, "Primary wording.")
        self.assertEqual(support, "")

    def test_html_cloze_media_and_multiline_markup_are_cleaned(self) -> None:
        primary, support = display_lines(
            {
                "Text": "<div>{{c1::Warfarin::drug}} inhibits</div>\n<div>VKORC1.</div>[sound:w.mp3]",
                "Extra": "<ul><li>Monitor</li><li>INR.</li></ul><img src='x.png'>",
            }
        )
        self.assertEqual(primary, "Warfarin inhibits VKORC1.")
        self.assertEqual(support, "Monitor INR.")

    def test_internal_identity_and_image_mask_fields_are_skipped(self) -> None:
        primary, support = display_lines(
            {
                "AnkiHub ID": "a1b2c3",
                "One by one": "1",
                "Image Occlusion": "{{c1::image-occlusion:rect}}",
                "Hidden ID": "hidden-value",
                "Question": "Actual study question?",
            },
            ("a1b2c3", "hidden-value"),
        )
        self.assertEqual(primary, "Actual study question?")
        self.assertEqual(support, "")

    def test_generated_related_cards_field_is_never_displayed(self) -> None:
        primary, support = display_lines(
            {
                "Text": "Human-readable card content.",
                "Related Cards": '{"note_ids":[1,2,3],"query":"warfarin"}',
            },
            ("warfarin",),
        )
        self.assertEqual(primary, "Human-readable card content.")
        self.assertEqual(support, "")

    def test_late_primary_match_never_invents_a_support_line(self) -> None:
        primary, support = display_lines(
            {
                "Text": ("Earlier content. " * 30) + "Warfarin target.",
            },
            ("warfarin",),
        )
        self.assertNotIn("Warfarin", primary)
        # The match stays searchable on the full field, but without a real
        # Extra field no other content is surfaced to explain it.
        self.assertEqual(support, "")

    def test_uncovered_second_term_in_a_later_field_is_not_surfaced(self) -> None:
        primary, support = display_lines(
            {
                "Text": "Warfarin is an anticoagulant.",
                "Extra": "Monitor for bleeding.",
                "Lecture Notes": "INR monitoring guides dosing.",
            },
            ("warfarin", "inr"),
        )
        self.assertIn("Warfarin", primary)
        # ``INR`` matched only in Lecture Notes; the support line stays the
        # real Extra excerpt instead of exposing that field.
        self.assertEqual(support, "Monitor for bleeding.")

    def test_media_only_note_falls_back_to_note_id(self) -> None:
        primary, support = display_lines(
            {
                "Image": "<img src='scan.png'>",
                "Audio": "[sound:clip.mp3]",
            },
            note_id=4711,
        )
        self.assertEqual(primary, "Note 4711")
        self.assertEqual(support, "")

    def test_long_lines_are_hard_bounded(self) -> None:
        primary, support = display_lines(
            {
                "Text": "primary " + "word " * 120,
                "Extra": "support " + "term " * 120,
            }
        )
        self.assertLessEqual(len(primary), PRIMARY_DISPLAY_LIMIT)
        self.assertTrue(primary.endswith("…"))
        self.assertLessEqual(len(support), SUPPORT_DISPLAY_LIMIT)
        self.assertTrue(support.endswith("…"))


if __name__ == "__main__":
    unittest.main()
