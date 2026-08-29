from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from backend.index import SmartSearchIndex
from backend.medical_vocab import load_alias_resource
from backend.models import IndexedNote
from backend.search import SearchEngine
from backend.fuzzy import Vocabulary


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FrenchMedicalVocabularyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vocab_path = (
            PROJECT_ROOT
            / "resources"
            / "medical_vocab"
            / "french_medical_aliases.json.gz"
        )

    def test_load_french_alias_resource(self) -> None:
        self.assertTrue(self.vocab_path.is_file(), f"Missing {self.vocab_path}")
        aliases = load_alias_resource(self.vocab_path)
        self.assertGreater(len(aliases), 300)

        # Check French drug brand names
        self.assertIn("doliprane", aliases)
        self.assertIn("paracetamol", aliases["doliprane"])

        self.assertIn("spasfon", aliases)
        self.assertIn("phloroglucinol", aliases["spasfon"])

        self.assertIn("kardegic", aliases)
        self.assertIn("acide acetylsalicylique", aliases["kardegic"])

        self.assertIn("augmentin", aliases)

        # Check French medical acronyms (EDN / R2C)
        self.assertIn("hta", aliases)
        self.assertIn("hypertension arterielle", aliases["hta"])

        self.assertIn("idm", aliases)
        self.assertIn("infarctus du myocarde", aliases["idm"])

        self.assertIn("avc", aliases)
        self.assertIn("accident vasculaire cerebral", aliases["avc"])

        self.assertIn("bpco", aliases)
        self.assertIn("oap", aliases)

    def test_search_expansion_with_french_drugs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            try:
                index.load_alias_resource(self.vocab_path, source="bdpm_fr")
                note = IndexedNote(
                    note_id=101,
                    card_ids=(1001,),
                    note_type="EDN Medical Card",
                    fields={
                        "Front": "Traitement de premiere intention des douleurs",
                        "Back": "Prescription de paracetamol 1000mg",
                    },
                    tags=("EDN", "Pharmacologie"),
                    decks=("EDN::Pharmaco",),
                )
                index.rebuild([note])
                engine = SearchEngine(index)
                engine.warmup()

                response = engine.search("doliprane")
                self.assertEqual(len(response.results), 1)
                self.assertEqual(response.results[0].note_id, 101)
                alias_matches = [exp for exp in response.alias_expansions if "paracetamol" in exp.expanded]
                self.assertTrue(len(alias_matches) > 0)
            finally:
                index.close()

    def test_search_expansion_with_french_acronyms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = SmartSearchIndex(Path(directory) / "index.sqlite3")
            try:
                index.load_alias_resource(self.vocab_path, source="bdpm_fr")
                note = IndexedNote(
                    note_id=202,
                    card_ids=(2002,),
                    note_type="EDN Card",
                    fields={
                        "Front": "Definition item 224",
                        "Back": "Prise en charge de l'hypertension arterielle essentielle selon les recommandations.",
                    },
                    tags=("Cardio", "EDN"),
                    decks=("EDN::Cardiologie",),
                )
                index.rebuild([note])
                engine = SearchEngine(index)
                engine.warmup()

                response = engine.search("HTA")
                self.assertEqual(len(response.results), 1)
                self.assertEqual(response.results[0].note_id, 202)
            finally:
                index.close()


if __name__ == "__main__":
    unittest.main()
