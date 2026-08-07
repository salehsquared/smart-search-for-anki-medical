from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "_smart_search_exact_native_tests"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    package.__package__ = PACKAGE
    sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.anki_adapter",
    ROOT / "anki_adapter.py",
)
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)

try:
    from anki.collection import Collection
except ImportError as error:  # The plain-Python suite intentionally omits Anki.
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


@unittest.skipIf(
    IMPORT_ERROR is not None,
    f"Anki runtime unavailable: {IMPORT_ERROR}",
)
class NativeExactQueryTests(unittest.TestCase):
    """Lock Exact mode to the same grammar used by Anki's Browser."""

    def test_words_phrases_boundaries_and_filters_keep_native_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            collection = Collection(str(Path(directory) / "probe.anki2"))
            try:
                model = collection.models.current()
                self.assertIsNotNone(model)
                assert model is not None
                included = collection.decks.id("Included")
                excluded = collection.decks.id("Excluded")
                self.assertIsNotNone(included)
                self.assertIsNotNone(excluded)
                assert included is not None and excluded is not None

                note_fronts: dict[int, str] = {}

                def add(front: str, back: str, deck_id: int) -> None:
                    note = collection.new_note(model)
                    note.fields[0] = front
                    note.fields[1] = back
                    collection.add_note(note, deck_id)
                    note_fronts[int(note.id)] = front

                add("alpha with intervening text beta", "answer", included)
                add("alpha on the front", "beta on the back", included)
                add("the alpha beta phrase", "answer", included)
                add("alphabet soup", "beta", included)
                add("unrelated", "answer", included)
                add("alpha outside the scope", "beta", excluded)

                def matches(query: str) -> set[str]:
                    scope = adapter.AnkiCollectionReader.card_ids_by_note_for_query(
                        collection,
                        query,
                    )
                    return {note_fronts[note_id] for note_id in scope}

                all_both = {
                    "alpha with intervening text beta",
                    "alpha on the front",
                    "the alpha beta phrase",
                    "alphabet soup",
                    "alpha outside the scope",
                }
                self.assertEqual(matches("alpha beta"), all_both)
                self.assertEqual(matches('"alpha" "beta"'), all_both)
                self.assertEqual(
                    matches('"alpha beta"'),
                    {"the alpha beta phrase"},
                )
                self.assertEqual(
                    matches("w:alpha w:beta"),
                    all_both - {"alphabet soup"},
                )
                self.assertEqual(
                    matches("deck:Included alpha beta"),
                    all_both - {"alpha outside the scope"},
                )
            finally:
                collection.close()


if __name__ == "__main__":
    unittest.main()
