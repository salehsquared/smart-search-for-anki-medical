"""Tests for internationalization and French localization in Smart Search."""

import os
import unittest
from unittest.mock import patch

from anki_actions import ActionKind, ActionOutcome, CollectionAction, format_action_message
from ui.contracts import MatchKind, PreviewDefault, SearchMode
from ui.i18n import get_help_html, is_french_locale, tr, tr_plural


class TestI18n(unittest.TestCase):
    def test_translation_in_french_env(self):
        with patch.dict(os.environ, {"LANG": "fr_FR.UTF-8"}):
            self.assertTrue(is_french_locale())
            self.assertEqual(tr("Smart"), "Intelligent")
            self.assertEqual(tr("Exact"), "Exact")
            self.assertEqual(tr("Semantic"), "Sémantique")
            self.assertEqual(tr("All decks"), "Tous les paquets")
            self.assertEqual(tr("Search Settings"), "Paramètres de recherche")
            self.assertEqual(tr("Select all"), "Tout sélectionner")
            self.assertEqual(tr("Undo"), "Annuler")
            self.assertEqual(tr("Flag"), "Drapeau")

            # Contract enums with i18n
            self.assertEqual(SearchMode.SMART.label, "Intelligent")
            self.assertEqual(SearchMode.SEMANTIC.label, "Sémantique")
            self.assertEqual(PreviewDefault.ANSWER.label, "Réponse")
            self.assertEqual(PreviewDefault.EDIT.label, "Modifier")
            self.assertEqual(MatchKind.CORRECTION.badge, "correction")

            # Help HTML contains French
            help_text = get_help_html()
            self.assertIn("Rechercher dans votre collection", help_text)
            self.assertIn("Intelligent", help_text)

    def test_action_message_french(self):
        with patch.dict(os.environ, {"LANG": "fr_FR.UTF-8"}):
            # Flag action
            action = CollectionAction(kind=ActionKind.FLAG, card_ids=(1, 2), flag=1)
            outcome = ActionOutcome(
                changes=None,
                kind=ActionKind.FLAG,
                requested=2,
                live=2,
                eligible=2,
                changed=2,
                stale=0,
                skipped=0,
                note_count=1,
            )
            msg = format_action_message(action, outcome)
            self.assertIn("Drapeau rouge appliqué sur 2 cartes (1 note)", msg)

            # Suspend action
            action = CollectionAction(kind=ActionKind.SUSPEND, card_ids=(10,))
            outcome = ActionOutcome(
                changes=None,
                kind=ActionKind.SUSPEND,
                requested=1,
                live=1,
                eligible=1,
                changed=1,
                stale=0,
                skipped=0,
                note_count=1,
            )
            msg = format_action_message(action, outcome)
            self.assertIn("1 carte suspendue(s)", msg)

            # Change deck action
            action = CollectionAction(
                kind=ActionKind.CHANGE_DECK,
                card_ids=(1, 2, 3),
                deck_id=123,
                deck_name="EDN::Cardio",
            )
            outcome = ActionOutcome(
                changes=None,
                kind=ActionKind.CHANGE_DECK,
                requested=3,
                live=3,
                eligible=3,
                changed=3,
                stale=0,
                skipped=0,
                note_count=2,
            )
            msg = format_action_message(action, outcome)
            self.assertIn("3 cartes déplacée(s)", msg)
            self.assertIn("« EDN::Cardio »", msg)

    def test_english_fallback(self):
        with patch.dict(os.environ, {"LANG": "en_US.UTF-8", "LC_ALL": "C"}):
            # In English environment without anki mock
            with patch("ui.i18n.is_french_locale", return_value=False):
                self.assertEqual(tr("Smart"), "Smart")
                self.assertEqual(tr("All decks"), "All decks")
                self.assertEqual(SearchMode.SMART.label, "Smart")


if __name__ == "__main__":
    unittest.main()
