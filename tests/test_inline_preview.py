from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


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


class _Signal:
    def __init__(self) -> None:
        self._handlers: list[object] = []

    def connect(self, handler: object) -> None:
        self._handlers.append(handler)

    def emit(self) -> None:
        for handler in self._handlers:
            handler()


class _Shortcut:
    created: list["_Shortcut"] = []

    def __init__(self, sequence: object, parent: object) -> None:
        self.sequence = sequence
        self.parent = parent
        self.context: object | None = None
        self.auto_repeat: bool | None = None
        self.activated = _Signal()
        self.created.append(self)

    def setContext(self, context: object) -> None:  # noqa: N802
        self.context = context

    def setAutoRepeat(self, enabled: bool) -> None:  # noqa: N802
        self.auto_repeat = bool(enabled)


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


class InlinePreviewSideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inspector = inline_preview.InlineResultInspector.__new__(
            inline_preview.InlineResultInspector
        )
        self.inspector._card_ids = (71,)
        self.inspector._state = "question"
        self.control_updates = 0
        self.render_requests: list[bool] = []

        def update_controls() -> None:
            self.control_updates += 1

        self.inspector._update_controls = update_controls
        self.inspector._schedule_render = (
            lambda *, force=False: self.render_requests.append(bool(force))
        )

    def test_side_requests_are_deterministic_and_idempotent(self) -> None:
        self.assertTrue(self.inspector.show_answer())
        self.assertEqual(self.inspector._state, "answer")
        self.assertEqual(self.control_updates, 1)
        self.assertEqual(self.render_requests, [True])

        # A held Space/Right key must not restart rendering or answer audio.
        self.assertFalse(self.inspector.show_answer())
        self.assertEqual(self.control_updates, 1)
        self.assertEqual(self.render_requests, [True])

        self.assertTrue(self.inspector.show_question())
        self.assertEqual(self.inspector._state, "question")
        self.assertEqual(self.control_updates, 2)
        self.assertEqual(self.render_requests, [True, True])

    def test_button_flip_uses_the_same_safe_side_transition(self) -> None:
        self.inspector.flip()
        self.assertEqual(self.inspector._state, "answer")
        self.inspector.flip()
        self.assertEqual(self.inspector._state, "question")
        self.assertEqual(self.render_requests, [True, True])

    def test_invalid_side_or_missing_card_is_a_no_op(self) -> None:
        self.assertFalse(self.inspector.show_side("backwards"))
        self.inspector._card_ids = ()
        self.assertFalse(self.inspector.show_answer())
        self.assertEqual(self.inspector._state, "question")
        self.assertEqual(self.render_requests, [])

    def test_target_identity_detects_a_card_that_became_stale(self) -> None:
        result = types.SimpleNamespace(note_id=7, card_ids=(71,))
        self.inspector._result = result
        self.assertTrue(self.inspector.is_targeting(result))

        self.inspector._card_ids = ()
        self.assertFalse(self.inspector.is_targeting(result))

    def test_card_web_shortcuts_reveal_without_repeat_or_page_scroll(self) -> None:
        web = object()
        context = object()
        self.inspector.web = web
        _Shortcut.created = []
        fake_aqt = types.ModuleType("aqt")
        fake_aqt.__path__ = []  # type: ignore[attr-defined]
        fake_qt = types.ModuleType("aqt.qt")
        fake_qt.QKeySequence = lambda sequence: sequence
        fake_qt.QShortcut = _Shortcut
        fake_qt.Qt = types.SimpleNamespace(
            ShortcutContext=types.SimpleNamespace(
                WidgetWithChildrenShortcut=context
            )
        )
        fake_aqt.qt = fake_qt  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {"aqt": fake_aqt, "aqt.qt": fake_qt},
        ):
            self.inspector._install_card_shortcuts()

        self.assertEqual(
            [shortcut.sequence for shortcut in _Shortcut.created],
            ["Space", "Right", "Left"],
        )
        for shortcut in _Shortcut.created:
            self.assertIs(shortcut.parent, web)
            self.assertIs(shortcut.context, context)
            self.assertFalse(shortcut.auto_repeat)

        _Shortcut.created[0].activated.emit()
        self.assertEqual(self.inspector._state, "answer")
        self.assertEqual(self.render_requests, [True])

        # An already-visible answer is still owned by the shortcut, but its
        # deterministic handler does not queue another render or replay audio.
        _Shortcut.created[0].activated.emit()
        self.assertEqual(self.render_requests, [True])

        _Shortcut.created[2].activated.emit()
        _Shortcut.created[1].activated.emit()
        self.assertEqual(self.inspector._state, "answer")
        self.assertEqual(self.render_requests, [True, True, True])


if __name__ == "__main__":
    unittest.main()
