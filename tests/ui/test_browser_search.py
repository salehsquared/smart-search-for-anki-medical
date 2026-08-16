from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from ui import browser_search as browser_search_module
    from ui.browser_search import (
        ACTION_OBJECT_NAME,
        ACTION_TEXT,
        ACTION_TOOLTIP,
        install_browser_search_action,
        refresh_browser_search_action,
        remove_browser_search_action,
    )
    from ui.widgets import (
        QApplication,
        QColor,
        QComboBox,
        QIcon,
        QLineEdit,
        QPixmap,
        QSize,
        QToolButton,
        Qt,
    )
    from PyQt6 import sip
except ImportError as error:  # PyQt6 is intentionally not a package dependency.
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


class _Browser:
    def __init__(self, query: str = "") -> None:
        self.query = query
        self.search_edit = QComboBox()
        self.search_edit.setEditable(True)
        self.form = SimpleNamespace(searchEdit=self.search_edit)

    def current_search(self) -> str:
        return self.query


def _icon(color: str) -> QIcon:
    pixmap = QPixmap(8, 8)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


def _visible_colors(icon: QIcon, size: int) -> list[QColor]:
    pixmap = icon.pixmap(size, size)
    if pixmap.isNull():
        return []
    image = pixmap.toImage()
    return [
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() >= 32
    ]


@unittest.skipIf(IMPORT_ERROR is not None, f"Qt runtime unavailable: {IMPORT_ERROR}")
class BrowserSearchActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_installs_leading_action_with_native_metadata(self) -> None:
        browser = _Browser()
        line_edit = browser.search_edit.lineEdit()
        self.assertIsNotNone(line_edit)
        line_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        trailing = line_edit.addAction(
            _icon("blue"),
            QLineEdit.ActionPosition.TrailingPosition,
        )

        action = install_browser_search_action(
            browser,
            lambda _query: None,
            icon_factory=lambda: _icon("red"),
        )

        self.assertIsNotNone(action)
        self.assertEqual(action.objectName(), ACTION_OBJECT_NAME)
        self.assertEqual(action.text(), ACTION_TEXT)
        self.assertEqual(action.toolTip(), ACTION_TOOLTIP)
        self.assertEqual(action.statusTip(), ACTION_TOOLTIP)
        self.assertIn(action, line_edit.actions())

        browser.search_edit.resize(480, 36)
        browser.search_edit.show()
        self.app.processEvents()
        buttons = {
            button.defaultAction(): button
            for button in line_edit.findChildren(QToolButton)
            if button.defaultAction() is not None
        }
        self.assertIn(action, buttons)
        self.assertIn(trailing, buttons)
        self.assertLess(buttons[action].geometry().center().x(), line_edit.rect().center().x())
        self.assertGreater(
            buttons[trailing].geometry().center().x(),
            line_edit.rect().center().x(),
        )

        remove_browser_search_action(action)
        browser.search_edit.deleteLater()

    def test_default_icon_is_a_visible_distinctive_green_glyph(self) -> None:
        browser = _Browser()

        action = install_browser_search_action(browser, lambda _query: None)

        self.assertIsNotNone(action)
        self.assertFalse(action.icon().isNull())
        available_sizes = {
            (available.width(), available.height())
            for available in action.icon().availableSizes()
        }
        self.assertTrue({(16, 16), (20, 20), (24, 24)} <= available_sizes)
        retina = action.icon().pixmap(QSize(16, 16), 2.0)
        self.assertEqual((retina.width(), retina.height()), (32, 32))
        self.assertEqual(retina.devicePixelRatio(), 2.0)
        for size in (16, 24):
            with self.subTest(size=size):
                visible = _visible_colors(action.icon(), size)
                self.assertGreater(len(visible), size)
                self.assertLess(len(visible), size * size)
                green = [
                    color
                    for color in visible
                    if color.green() >= color.red() + 20
                    and color.green() >= color.blue() + 10
                ]
                self.assertGreater(len(green), max(3, len(visible) // 5))
                light_detail = [
                    color
                    for color in visible
                    if min(color.red(), color.green(), color.blue()) >= 210
                ]
                self.assertGreater(len(light_detail), 1)
        browser.search_edit.deleteLater()

    def test_icon_construction_failure_does_not_change_the_browser(self) -> None:
        browser = _Browser()
        line_edit = browser.search_edit.lineEdit()

        action = install_browser_search_action(
            browser,
            lambda _query: None,
            icon_factory=lambda: (_ for _ in ()).throw(ValueError("paint failed")),
        )

        self.assertIsNone(action)
        self.assertEqual(line_edit.actions(), [])
        browser.search_edit.deleteLater()

    def test_click_reads_the_exact_current_search_at_trigger_time(self) -> None:
        browser = _Browser("first query")
        opened: list[str] = []
        action = install_browser_search_action(
            browser,
            opened.append,
            icon_factory=lambda: QIcon(),
        )
        self.assertIsNotNone(action)

        browser.query = '  deck:"Clinical Medicine" (tag:renal OR is:due)  '
        action.trigger()

        self.assertEqual(
            opened,
            ['  deck:"Clinical Medicine" (tag:renal OR is:due)  '],
        )
        browser.search_edit.deleteLater()

    def test_empty_current_search_is_forwarded_instead_of_ignored(self) -> None:
        browser = _Browser("non-empty at install time")
        opened: list[str] = []
        action = install_browser_search_action(
            browser,
            opened.append,
            icon_factory=lambda: QIcon(),
        )
        self.assertIsNotNone(action)

        browser.query = ""
        action.trigger()

        self.assertEqual(opened, [""])
        browser.search_edit.deleteLater()

    def test_install_is_idempotent_and_rebinds_a_reloaded_controller(self) -> None:
        browser = _Browser("tag:first")
        first_opened: list[str] = []
        second_opened: list[str] = []

        first_action = install_browser_search_action(
            browser,
            first_opened.append,
        )
        self.assertIsNotNone(first_action)
        first_icon_key = first_action.icon().cacheKey()
        second_action = install_browser_search_action(
            browser,
            second_opened.append,
        )

        self.assertIs(first_action, second_action)
        self.assertFalse(second_action.icon().isNull())
        self.assertEqual(second_action.icon().cacheKey(), first_icon_key)
        matching = [
            action
            for action in browser.search_edit.lineEdit().actions()
            if action.objectName() == ACTION_OBJECT_NAME
        ]
        self.assertEqual(matching, [first_action])
        first_action.trigger()
        self.assertEqual(first_opened, [])
        self.assertEqual(second_opened, ["tag:first"])
        browser.search_edit.deleteLater()

    def test_missing_or_non_editable_search_widgets_fail_closed(self) -> None:
        self.assertIsNone(
            install_browser_search_action(
                SimpleNamespace(),
                lambda _query: None,
            )
        )
        self.assertIsNone(
            install_browser_search_action(
                SimpleNamespace(form=SimpleNamespace(searchEdit=object())),
                lambda _query: None,
            )
        )

        combo = QComboBox()
        browser = SimpleNamespace(form=SimpleNamespace(searchEdit=combo))
        self.assertIsNone(install_browser_search_action(browser, lambda _query: None))
        combo.deleteLater()

    def test_deleted_search_widget_fails_closed(self) -> None:
        browser = _Browser("is:due")
        sip.delete(browser.search_edit)

        action = install_browser_search_action(browser, lambda _query: None)

        self.assertIsNone(action)

    def test_theme_refresh_rebuilds_icon_and_remove_detaches_action(self) -> None:
        browser = _Browser()
        action = install_browser_search_action(browser, lambda _query: None)
        self.assertIsNotNone(action)
        initial_key = action.icon().cacheKey()
        real_icon_factory = browser_search_module._smart_search_icon

        with patch.object(
            browser_search_module,
            "_smart_search_icon",
            side_effect=real_icon_factory,
        ) as icon_factory:
            self.assertTrue(refresh_browser_search_action(action))
        icon_factory.assert_called_once_with()
        self.assertNotEqual(action.icon().cacheKey(), initial_key)
        self.assertGreater(len(_visible_colors(action.icon(), 16)), 16)

        line_edit = browser.search_edit.lineEdit()
        self.assertIn(action, line_edit.actions())
        remove_browser_search_action(action)
        self.assertNotIn(action, line_edit.actions())
        browser.search_edit.deleteLater()

    def test_refresh_and_remove_deleted_action_fail_closed(self) -> None:
        browser = _Browser()
        action = install_browser_search_action(
            browser,
            lambda _query: None,
            icon_factory=lambda: QIcon(),
        )
        self.assertIsNotNone(action)
        sip.delete(action)

        self.assertFalse(refresh_browser_search_action(action))
        remove_browser_search_action(action)
        browser.search_edit.deleteLater()


if __name__ == "__main__":
    unittest.main()
