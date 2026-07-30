from __future__ import annotations

from pathlib import Path
import unittest

from backend.medical_vocab import load_alias_resource
from backend import SmartSearchIndex
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MedicalVocabularyTests(unittest.TestCase):
    def test_bundled_rxterms_resource_loads_normalized_aliases(self) -> None:
        resource = PROJECT_ROOT / "resources" / "medical_vocab" / "rxterms_202607.json.gz"
        aliases = load_alias_resource(resource)
        self.assertGreater(len(aliases), 5_000)
        self.assertIn("fluorouracil", aliases["5-fu"])

    def test_index_resource_load_is_idempotent(self) -> None:
        resource = PROJECT_ROOT / "resources" / "medical_vocab" / "rxterms_202607.json.gz"
        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            try:
                first = index.load_alias_resource(resource)
                generation = index.generation
                second = index.load_alias_resource(resource)
                self.assertGreater(first, 5_000)
                self.assertEqual(second, 0)
                self.assertEqual(index.generation, generation)
            finally:
                index.close()

if __name__ == "__main__":
    unittest.main()
