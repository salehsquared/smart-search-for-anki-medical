from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "_smart_search_inline_preview_tests",
    ROOT / "inline_preview.py",
)
assert spec and spec.loader
inline_preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inline_preview)


class _Surface:
    def __init__(self) -> None:
        self.visible: bool | None = None

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        self.visible = bool(visible)


class _Pane:
    def __init__(self) -> None:
        self.card_controls_visible: bool | None = None

    def set_card_controls_visible(self, visible: bool) -> None:
        self.card_controls_visible = bool(visible)


class InlinePreviewSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inspector = inline_preview.InlineResultInspector.__new__(
            inline_preview.InlineResultInspector
        )
        self.inspector._card_container = _Surface()
        self.inspector.web = _Surface()
        self.inspector._editor_container = _Surface()
        self.inspector._editor = type("Editor", (), {"web": _Surface()})()
        self.inspector.pane = _Pane()

    def test_edit_mode_hides_both_card_surfaces(self) -> None:
        self.inspector._set_surface_mode("edit")

        self.assertFalse(self.inspector._card_container.visible)
        self.assertFalse(self.inspector.web.visible)
        self.assertTrue(self.inspector._editor_container.visible)
        self.assertTrue(self.inspector._editor.web.visible)
        self.assertFalse(self.inspector.pane.card_controls_visible)

    def test_hidden_mode_hides_every_surface(self) -> None:
        self.inspector._set_surface_mode("hidden")

        self.assertFalse(self.inspector._card_container.visible)
        self.assertFalse(self.inspector.web.visible)
        self.assertFalse(self.inspector._editor_container.visible)
        self.assertFalse(self.inspector._editor.web.visible)
        self.assertFalse(self.inspector.pane.card_controls_visible)


if __name__ == "__main__":
    unittest.main()
