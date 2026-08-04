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


class InlinePreviewDefaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inspector = inline_preview.InlineResultInspector.__new__(
            inline_preview.InlineResultInspector
        )
        self.inspector._disposed = False
        self.inspector._hidden = False
        self.inspector._default_view = "answer"
        self.inspector._default_apply_generation = 0
        self.inspector._default_detach_pending = False
        self.inspector._result = None
        self.inspector._card_ids = ()
        self.inspector._sibling_index = 0
        self.inspector._state = "question"
        self.inspector._editor = None
        self.inspector._editor_switching = False
        self.inspector._editor_target_generation = 0
        self.inspector._editor_target_force = False
        self.inspector._update_controls = lambda: None
        self.inspector._schedule_render = lambda *, force=False: None
        self.inspector._sync_editor_to_target = lambda: None
        self.titles = []
        self.mode = ["card"]
        self.inspector.pane = types.SimpleNamespace(
            set_target_title=self.titles.append,
            mode=lambda: self.mode[0],
            set_mode=lambda value: self.mode.__setitem__(0, value),
        )

    def test_new_target_applies_default_but_force_refresh_preserves_manual_side(self) -> None:
        applied = []
        self.inspector._apply_default_view = lambda: applied.append(True)
        result = types.SimpleNamespace(
            note_id=7,
            card_ids=(71,),
            title="Seven",
        )

        self.inspector.set_result(result)
        self.inspector._state = "question"
        self.inspector.set_result(result, force=True)

        self.assertEqual(applied, [True])
        self.assertEqual(self.inspector._state, "question")
        self.assertEqual(self.titles, ["Seven", "Seven"])

    def test_answer_default_sets_card_surface_and_answer_side(self) -> None:
        surfaces = []
        renders = []
        self.inspector._set_surface_mode = surfaces.append
        self.inspector._schedule_render = (
            lambda *, force=False: renders.append(bool(force))
        )

        self.inspector._apply_card_default()

        self.assertEqual(self.mode[0], "card")
        self.assertEqual(self.inspector._state, "answer")
        self.assertEqual(surfaces, ["card"])
        self.assertEqual(renders, [True])

    def test_visible_default_change_applies_immediately_but_hidden_change_waits(self) -> None:
        applied = []
        self.inspector._apply_default_view = lambda: applied.append(
            self.inspector._default_view
        )

        self.inspector.set_default_view("EDIT", apply_now=True)
        self.inspector._hidden = True
        self.inspector.set_default_view("answer", apply_now=True)

        self.assertEqual(applied, ["edit"])
        self.assertEqual(self.inspector._default_view, "answer")

    def test_switching_from_edit_to_answer_waits_for_editor_save(self) -> None:
        self.mode[0] = "edit"
        callbacks = []
        applied = []
        self.inspector.prepare_for_external_change = callbacks.append
        self.inspector._apply_card_default = lambda: applied.append(
            self.inspector._default_view
        )

        self.inspector._apply_default_view()

        self.assertEqual(applied, [])
        self.assertEqual(len(callbacks), 1)
        callbacks.pop()()
        self.assertEqual(applied, ["answer"])


class _EditorNote:
    def __init__(self, note_id: int) -> None:
        self.id = note_id


class _EditorCard:
    def __init__(self, note_id: int) -> None:
        self._note = _EditorNote(note_id)

    def note(self):
        return self._note


class _EditorBase:
    def __init__(self) -> None:
        self.card = None
        self.set_calls = []
        self.save_callbacks = []
        self.reload_calls = 0

    def call_after_note_saved(self, callback) -> None:
        self.save_callbacks.append(callback)

    def finish_save(self) -> None:
        self.save_callbacks.pop(0)()


class _LegacyEditor(_EditorBase):
    def __init__(self, note=None) -> None:
        super().__init__()
        self.note = note

    def set_note(self, note) -> None:
        self.set_calls.append(note)
        self.note = note


class _ModernEditor(_EditorBase):
    """Anki 26.08 shape: no .note and set_note(None) keeps stale nid."""

    def __init__(self, nid=None) -> None:
        super().__init__()
        self.nid = nid

    def set_note(self, note) -> None:
        self.set_calls.append(note)
        if note is not None:
            self.nid = note.id

    def reload_note(self) -> None:
        self.reload_calls += 1


class InlinePreviewEditorCompatibilityTests(unittest.TestCase):
    def _inspector(self, editor, card):
        inspector = inline_preview.InlineResultInspector.__new__(
            inline_preview.InlineResultInspector
        )
        inspector._editor = editor
        inspector._attached_editor_note_id = 0
        inspector._editor_switching = False
        inspector._editor_target_force = False
        inspector._disposed = False
        inspector._hidden = False
        inspector._default_detach_pending = False
        inspector._current_card = lambda: card
        inspector.pane = types.SimpleNamespace(mode=lambda: "edit")
        return inspector

    def test_modern_editor_initial_target_does_not_require_note_attribute(self) -> None:
        card = _EditorCard(20)
        editor = _ModernEditor()
        inspector = self._inspector(editor, card)

        inspector._sync_editor_to_target()

        self.assertEqual(inspector._attached_editor_note_id, 20)
        self.assertIs(editor.card, card)
        self.assertEqual(editor.nid, 20)

    def test_legacy_editor_initial_target_uses_the_same_owned_identity(self) -> None:
        card = _EditorCard(20)
        editor = _LegacyEditor()
        inspector = self._inspector(editor, card)

        inspector._sync_editor_to_target()

        self.assertEqual(inspector._attached_editor_note_id, 20)
        self.assertEqual(editor.note.id, 20)

    def test_switch_saves_old_note_and_rapid_motion_uses_latest_target(self) -> None:
        current = [_EditorCard(20)]
        editor = _ModernEditor(nid=10)
        inspector = self._inspector(editor, current[0])
        inspector._attached_editor_note_id = 10
        inspector._current_card = lambda: current[0]

        inspector._sync_editor_to_target()
        self.assertTrue(inspector._editor_switching)
        self.assertEqual(editor.set_calls, [])

        current[0] = _EditorCard(30)
        inspector._sync_editor_to_target()
        self.assertEqual(len(editor.save_callbacks), 1)
        editor.finish_save()

        self.assertFalse(inspector._editor_switching)
        self.assertEqual(inspector._attached_editor_note_id, 30)
        self.assertEqual(editor.set_calls[-1].id, 30)

    def test_flush_and_detach_work_when_modern_nid_remains_stale(self) -> None:
        editor = _ModernEditor(nid=20)
        inspector = self._inspector(editor, _EditorCard(20))
        inspector._attached_editor_note_id = 20
        completed = []

        inspector.flush(lambda: completed.append(True))
        self.assertEqual(completed, [])
        editor.finish_save()
        self.assertEqual(completed, [True])

        inspector._detach_editor()
        self.assertEqual(inspector._attached_editor_note_id, 0)
        self.assertEqual(editor.nid, 20)
        self.assertIsNone(editor.set_calls[-1])

        inspector.flush(lambda: completed.append(False))
        self.assertEqual(completed, [True, False])

    def test_foreign_note_change_never_reloads_live_unsaved_editor(self) -> None:
        editor = _ModernEditor(nid=20)
        inspector = self._inspector(editor, _EditorCard(20))
        inspector._attached_editor_note_id = 20
        inspector.refresh = lambda: None

        inspector._on_operation_did_execute(
            types.SimpleNamespace(note_text=True, card=False),
            object(),
        )

        self.assertEqual(editor.reload_calls, 0)
        self.assertEqual(editor.set_calls, [])


if __name__ == "__main__":
    unittest.main()
