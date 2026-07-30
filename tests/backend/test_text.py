from __future__ import annotations

import unittest

from backend.text import normalize_text, strip_cloze, strip_html_and_cloze, tokenize


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


if __name__ == "__main__":
    unittest.main()
