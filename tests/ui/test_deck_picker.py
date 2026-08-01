from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from ui.contracts import DeckCatalog, DeckEntry
    from ui.deck_picker import DeckPickerPopup, DeckScopeButton
    from ui.widgets import QApplication, Qt, QWidget
    from PyQt6.QtTest import QTest
except ImportError as error:  # PyQt6 is intentionally not a package dependency.
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


def _analysis(kind: str = "all", *names: str):
    return SimpleNamespace(
        kind=kind,
        names=tuple(names),
        clause_start=None,
        clause_end=None,
        remaining_query="",
    )


@unittest.skipIf(IMPORT_ERROR is not None, f"Qt runtime unavailable: {IMPORT_ERROR}")
class DeckPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def catalog() -> DeckCatalog:
        return DeckCatalog(
            decks=(
                DeckEntry(1, "AnKing"),
                DeckEntry(2, "AnKing::Step 1"),
                DeckEntry(3, "AnKing::Step 2"),
                DeckEntry(4, "Personal"),
            ),
            current_deck_id=3,
        )

    def test_scope_button_summarizes_all_one_many_and_custom(self) -> None:
        button = DeckScopeButton()
        with patch(
            "ui.deck_picker.analyze_deck_query",
            side_effect=(
                _analysis("all"),
                _analysis("selected", "AnKing::Step 2"),
                _analysis("selected", "AnKing::Step 1", "Personal"),
                _analysis("custom"),
            ),
        ):
            button.set_scope("")
            self.assertIn("All decks", button.text())
            button.set_scope('deck:"AnKing::Step 2"')
            self.assertIn("Step 2", button.text())
            self.assertIn("AnKing::Step 2", button.toolTip())
            button.set_scope('(deck:"AnKing::Step 1" OR deck:"Personal")')
            self.assertIn("2 decks", button.text())
            button.set_scope("-deck:AnKing")
            self.assertIn("Custom decks", button.text())
            self.assertIn("custom", button.accessibleName().casefold())
        button.deleteLater()

    def test_hierarchy_multi_select_and_parent_inheritance(self) -> None:
        popup = DeckPickerPopup()
        with patch(
            "ui.deck_picker.analyze_deck_query",
            return_value=_analysis("all"),
        ):
            popup.set_query("")
        popup.set_catalog(self.catalog())

        child = popup._items["AnKing::Step 1"]
        child.setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(popup.selected_names, ("AnKing::Step 1",))
        self.assertEqual(
            popup._items["AnKing"].checkState(0),
            Qt.CheckState.PartiallyChecked,
        )

        parent = popup._items["AnKing"]
        parent.setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(popup.selected_names, ("AnKing",))
        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)
        self.assertFalse(
            bool(child.flags() & Qt.ItemFlag.ItemIsUserCheckable),
            "A child inherited from its selected parent must not promise exclusion",
        )
        self.assertTrue(bool(child.flags() & Qt.ItemFlag.ItemIsEnabled))
        self.assertIn(
            "included by AnKing",
            str(child.data(0, Qt.ItemDataRole.AccessibleTextRole)),
        )
        self.assertIn("included", child.text(0))

        emitted = []
        popup.applied.connect(emitted.append)
        popup._apply()
        self.assertEqual(emitted, [("AnKing",)])
        popup.deleteLater()

    def test_all_and_current_are_explicit_staged_scopes(self) -> None:
        popup = DeckPickerPopup()
        with patch(
            "ui.deck_picker.analyze_deck_query",
            return_value=_analysis("all"),
        ):
            popup.set_query("")
        popup.set_catalog(self.catalog())

        popup.current_button.click()
        self.assertEqual(popup.selected_names, ("AnKing::Step 2",))
        self.assertTrue(popup.current_button.isChecked())
        self.assertIn("AnKing::Step 2", popup.current_button.toolTip())
        popup.current_button.click()
        self.assertEqual(popup.selected_names, ("AnKing::Step 2",))

        popup.all_button.click()
        self.assertEqual(popup.selected_names, ())
        self.assertTrue(popup.all_button.isChecked())
        self.assertEqual(popup.selection_label.text(), "All decks")
        popup.deleteLater()

    def test_custom_expression_is_read_only_and_must_be_edited_directly(self) -> None:
        popup = DeckPickerPopup()
        with patch(
            "ui.deck_picker.analyze_deck_query",
            return_value=_analysis("custom"),
        ):
            popup.set_query("-deck:AnKing")
        popup.set_catalog(self.catalog())
        popup.show()
        self.app.processEvents()

        self.assertTrue(popup.custom_frame.isVisibleTo(popup))
        self.assertFalse(popup.tree.isEnabled())
        self.assertFalse(popup.apply_button.isEnabled())
        self.assertFalse(popup.all_button.isChecked())
        self.assertFalse(popup.current_button.isChecked())
        self.assertEqual(popup.selection_label.text(), "Custom deck filter")
        self.assertIn("edit", popup.custom_label.text().casefold())
        popup.deleteLater()

    def test_filter_no_matches_and_unavailable_selected_deck_are_explicit(self) -> None:
        popup = DeckPickerPopup()
        with patch(
            "ui.deck_picker.analyze_deck_query",
            return_value=_analysis("selected", "Deleted::Deck"),
        ):
            popup.set_query('deck:"Deleted::Deck"')
        popup.set_catalog(self.catalog())
        popup.show()
        self.app.processEvents()

        missing = popup._items["Deleted::Deck"]
        self.assertIn("unavailable", missing.text(0).casefold())
        self.assertIn("current profile", missing.toolTip(0))
        missing.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(popup.selected_names, ())

        popup.filter_edit.setText("definitely absent")
        self.app.processEvents()
        self.assertTrue(popup.no_matches_label.isVisibleTo(popup))
        self.assertIn("No decks match", popup.no_matches_label.text())
        popup.deleteLater()

    def test_filter_keyboard_and_loading_error_states(self) -> None:
        popup = DeckPickerPopup()
        with patch(
            "ui.deck_picker.analyze_deck_query",
            return_value=_analysis("all"),
        ):
            popup.set_query("")
        popup.set_catalog(self.catalog())
        popup.show()
        popup.filter_edit.setFocus()
        popup.filter_edit.setText("Step 2")
        QTest.keyClick(popup.filter_edit, Qt.Key.Key_Down)
        self.app.processEvents()
        current = popup.tree.currentItem()
        self.assertIsNotNone(current)
        self.assertIn("Step 2", str(current.data(0, int(Qt.ItemDataRole.UserRole))))
        QTest.keyClick(popup.tree, Qt.Key.Key_Space)
        self.assertEqual(popup.selected_names, ("AnKing::Step 2",))

        popup.set_loading()
        self.assertFalse(popup.tree.isVisible())
        self.assertIn("Loading", popup.message_label.text())
        retried = []
        popup.retryRequested.connect(lambda: retried.append(True))
        popup.set_error("Try loading decks again.")
        popup.retry_button.click()
        self.assertEqual(retried, [True])
        popup.deleteLater()

    def test_refresh_error_keeps_cached_choices_usable(self) -> None:
        popup = DeckPickerPopup()
        with patch(
            "ui.deck_picker.analyze_deck_query",
            return_value=_analysis("all"),
        ):
            popup.set_query("")
        popup.set_catalog(self.catalog())
        popup.show()
        popup.set_refresh_error("temporary collection failure")
        self.app.processEvents()

        self.assertTrue(popup.tree.isVisibleTo(popup))
        self.assertTrue(popup.apply_button.isEnabled())
        self.assertTrue(popup.retry_button.isVisibleTo(popup))
        self.assertIn("last loaded", popup.message_label.text())
        self.assertEqual(
            popup.message_label.toolTip(),
            "temporary collection failure",
        )
        popup.deleteLater()

    def test_open_anchored_is_modeless_and_focuses_filter(self) -> None:
        host = QWidget()
        host.resize(640, 480)
        button = DeckScopeButton(host)
        button.move(20, 20)
        popup = DeckPickerPopup(host)
        with patch(
            "ui.deck_picker.analyze_deck_query",
            return_value=_analysis("all"),
        ):
            popup.set_query("")
            popup.set_catalog(self.catalog())
            host.show()
            popup.open_anchored(button)
            self.app.processEvents()

        self.assertTrue(popup.isVisible())
        self.assertFalse(popup.isModal())
        self.assertTrue(popup.filter_edit.hasFocus())
        popup.reject()
        popup.deleteLater()
        host.deleteLater()


if __name__ == "__main__":
    unittest.main()
