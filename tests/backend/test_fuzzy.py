from __future__ import annotations

import unittest

from backend.fuzzy import DamerauLevenshtein, Vocabulary


class FuzzyTests(unittest.TestCase):
    def test_true_damerau_levenshtein(self) -> None:
        self.assertEqual(DamerauLevenshtein.distance("buproprion", "bupropion"), 1)
        self.assertEqual(DamerauLevenshtein.distance("buproipon", "bupropion"), 1)
        # This differentiates unrestricted DL (2) from OSA distance (3).
        self.assertEqual(DamerauLevenshtein.distance("CA", "ABC"), 2)

    def test_casefolds_before_distance(self) -> None:
        self.assertEqual(DamerauLevenshtein.distance("BUPROPION", "bupropion"), 0)

    def test_corrects_collection_derived_medical_term(self) -> None:
        vocabulary = Vocabulary([("bupropion", 30), ("buspirone", 2)])
        correction = vocabulary.correction("buproprion")
        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.corrected, "bupropion")
        self.assertEqual(correction.distance, 1)
        self.assertTrue(correction.automatic)

    def test_never_corrects_known_or_very_short_term(self) -> None:
        vocabulary = Vocabulary([("ileum", 20), ("ilium", 20), ("ms", 5)])
        self.assertIsNone(vocabulary.correction("ileum"))
        self.assertIsNone(vocabulary.correction("ns"))

    def test_single_collection_typo_can_correct_to_trusted_drug(self) -> None:
        vocabulary = Vocabulary(
            [("acetominophen", 1), ("acetaminophen", 112)],
            {"tylenol": ("acetaminophen",)},
        )
        correction = vocabulary.correction("acetominophen")
        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.corrected, "acetaminophen")
        self.assertTrue(correction.automatic)

    def test_repeated_collection_typo_can_correct_to_dominant_trusted_drug(
        self,
    ) -> None:
        vocabulary = Vocabulary(
            [("buproprion", 4), ("bupropion", 66)],
            {"wellbutrin": ("bupropion",)},
        )
        correction = vocabulary.correction("buproprion")
        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.corrected, "bupropion")
        self.assertTrue(correction.automatic)

    def test_repeated_valid_term_is_not_rewritten_to_weak_candidate(self) -> None:
        vocabulary = Vocabulary(
            [("validterm", 4), ("validterma", 10)],
            {"alias": ("validterma",)},
        )
        self.assertIsNone(vocabulary.correction("validterm"))

    def test_single_untrusted_valid_term_is_not_automatically_rewritten(self) -> None:
        vocabulary = Vocabulary([("ilium", 1), ("ileum", 100)])
        correction = vocabulary.correction("ilium")
        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertFalse(correction.automatic)

    def test_aliases_expand_in_both_directions(self) -> None:
        vocabulary = Vocabulary(
            [("bupropion", 10)],
            {"Wellbutrin": ("bupropion",)},
        )
        forward = vocabulary.expand_aliases("WELLBUTRIN")
        reverse = vocabulary.expand_aliases("bupropion")
        self.assertEqual(forward[0].expanded, "bupropion")
        self.assertEqual(reverse[0].expanded, "wellbutrin")

    def test_large_candidate_scan_honors_cancellation(self) -> None:
        vocabulary = Vocabulary(
            [(f"medterm{index:04d}", 1) for index in range(1_000)]
        )
        checkpoints = 0

        def cancel() -> None:
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 2:
                raise RuntimeError("cancelled")

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            vocabulary.suggest("medtermxxxx", cancel_check=cancel)


if __name__ == "__main__":
    unittest.main()
