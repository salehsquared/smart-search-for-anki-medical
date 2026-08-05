from __future__ import annotations

import unittest

from backend.related import related_reason, related_source_tags


UWORLD_TAG = "#AK_Step2_v12::#UWorld::18462"
AMBOSS_TAG = "#AK_Step2_v12::#AMBOSS::Q-77"


class RelatedSourceTagTests(unittest.TestCase):
    def test_classifies_specific_tags_and_deduplicates_by_casefolded_full_tag(
        self,
    ) -> None:
        sources = related_source_tags(
            (
                UWORLD_TAG,
                UWORLD_TAG.swapcase(),
                AMBOSS_TAG,
            )
        )

        self.assertEqual(
            [(source.provider, source.key) for source in sources],
            [
                ("uworld", UWORLD_TAG.casefold()),
                ("amboss", AMBOSS_TAG.casefold()),
            ],
        )
        self.assertEqual(
            [source.display for source in sources],
            [UWORLD_TAG, AMBOSS_TAG],
        )
        self.assertEqual([source.leaf for source in sources], ["18462", "Q-77"])

    def test_rejects_broad_terminal_roots_and_incidental_substrings(self) -> None:
        self.assertEqual(
            related_source_tags(
                (
                    "UWorld",
                    "#UWorld",
                    "#AK_Step2_v12::#UWorld",
                    "AMBOSS",
                    "#AMBOSS",
                    "#AK_Step2_v12::#AMBOSS",
                    "myuworldish",
                    "preambossy",
                    "#AK_Step2_v12::NotUWorld",
                )
            ),
            (),
        )

    def test_accepts_specific_identifier_below_or_beside_provider_component(
        self,
    ) -> None:
        sources = related_source_tags(
            (
                "#AK_Step2_v12::#UWorld::18462",
                "#AK_Step2_v12::AMBOSS-Q77",
                "UWorld_99101",
            )
        )

        self.assertEqual(
            [source.provider for source in sources],
            ["uworld", "amboss", "uworld"],
        )
        self.assertEqual(
            [source.leaf for source in sources],
            ["18462", "AMBOSS-Q77", "UWorld_99101"],
        )

    def test_preserves_the_complete_hierarchy_as_the_relationship_key(self) -> None:
        v12, v11, near_miss = related_source_tags(
            (
                UWORLD_TAG,
                "#AK_Step2_v11::#UWorld::18462",
                "#AK_Step2_v12::#UWorld::184620",
            )
        )

        self.assertEqual(len({v12.key, v11.key, near_miss.key}), 3)
        self.assertNotEqual(v12.key, v11.key)
        self.assertNotEqual(v12.key, near_miss.key)

    def test_reason_is_compact_provider_plus_specific_leaf(self) -> None:
        uworld, amboss = related_source_tags((UWORLD_TAG, AMBOSS_TAG))

        self.assertEqual(related_reason(uworld), "Related · UWorld · 18462")
        self.assertEqual(related_reason(amboss), "Related · AMBOSS · Q-77")


if __name__ == "__main__":
    unittest.main()
