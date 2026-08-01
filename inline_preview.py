"""Anki-hosted card renderer and native note editor for the inline pane.

The surrounding pane is pure Qt (``ui.preview_pane``). This adapter remains
behind the Anki boundary because reviewer/editor web APIs vary across supported
Anki releases.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import re
import time
from typing import Any


def _result_card_ids(result: object | None) -> tuple[int, ...]:
    output: list[int] = []
    seen: set[int] = set()
    for value in getattr(result, "card_ids", ()) or ():
        try:
            card_id = int(value)
        except (TypeError, ValueError):
            continue
        if card_id > 0 and card_id not in seen:
            output.append(card_id)
            seen.add(card_id)
    return tuple(output)


class InlineResultInspector:
    """Own the rendered card page and lazily-created native Anki Editor."""

    def __init__(
        self,
        mw: Any,
        *,
        pane: Any,
        parent_window: Any,
        initial_result: object,
        on_error: Callable[[str], None],
    ) -> None:
        from aqt import gui_hooks
        from aqt.qt import (
            QSizePolicy,
            QTimer,
            QVBoxLayout,
            QWidget,
        )
        from aqt.webview import AnkiWebView, AnkiWebViewKind

        class _PreviewWebView(AnkiWebView):
            """Force media-server treatment identical to Anki's Previewer."""

            def setHtml(self, html: str, context: Any = None) -> None:  # noqa: N802
                from aqt.mediasrv import PageContext

                super().setHtml(html, PageContext.PREVIEWER)

        self.mw = mw
        self.pane = pane
        self.parent_window = parent_window
        self._on_error = on_error
        self._result: object | None = None
        self._card_ids: tuple[int, ...] = ()
        self._sibling_index = 0
        self._state = "question"
        self._last_render_at = 0.0
        self._disposed = False
        self._hidden = False
        self._editor: Any | None = None
        self._editor_switching = False
        self._editor_target_generation = 0
        self._editor_target_force = False
        self._cleanup_started = False
        self._cleanup_callbacks: list[Callable[[], None]] = []

        self._card_container = QWidget(self.pane.card_host)
        card_host_layout = self.pane.card_host.layout()
        if card_host_layout is None:
            card_host_layout = QVBoxLayout(self.pane.card_host)
            card_host_layout.setContentsMargins(0, 0, 0, 0)
            card_host_layout.setSpacing(0)
        card_host_layout.addWidget(self._card_container)

        self._editor_container = QWidget(self.pane.editor_host)
        editor_host_layout = self.pane.editor_host.layout()
        if editor_host_layout is None:
            editor_host_layout = QVBoxLayout(self.pane.editor_host)
            editor_host_layout.setContentsMargins(0, 0, 0, 0)
            editor_host_layout.setSpacing(0)
        editor_host_layout.addWidget(self._editor_container)
        self._editor_container.hide()

        card_layout = QVBoxLayout(self._card_container)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self.web = _PreviewWebView(
            parent=self._card_container,
            kind=AnkiWebViewKind.PREVIEWER,
        )
        self.web.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        card_layout.addWidget(self.web, 1)

        # Keep the compact card controls in the pane header. They survive
        # renderer/editor surface changes and leave the full body for content.
        self.previous_button = self.pane.previous_button
        self.sibling_label = self.pane.sibling_label
        self.next_button = self.pane.next_button
        self.replay_button = self.pane.replay_button
        self.flip_button = self.pane.flip_button
        self._pane_signal_bindings = (
            (self.pane.previousSiblingRequested, self.previous_sibling),
            (self.pane.nextSiblingRequested, self.next_sibling),
            (self.pane.replayRequested, self.replay_audio),
            (self.pane.flipRequested, self.flip),
            (self.pane.modeChanged, self.set_mode),
        )
        for signal, slot in self._pane_signal_bindings:
            signal.connect(slot)

        self._render_timer = QTimer(self.parent_window)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._render_now)

        self.web.stdHtml(
            self.mw.reviewer.revHtml(),
            css=["css/reviewer.css"],
            js=[
                "js/mathjax.js",
                "js/vendor/mathjax/tex-chtml-full.js",
                "js/reviewer.js",
            ],
            context=self,
        )
        self.web.allow_drops = True
        self.web.eval("_blockDefaultDragDropBehavior();")
        self.web.set_bridge_command(self._on_bridge_command, self)

        gui_hooks.card_review_webview_did_init(
            self.web,
            AnkiWebViewKind.PREVIEWER,
        )
        gui_hooks.operation_did_execute.append(self._on_operation_did_execute)

        self.set_result(initial_result, force=True)

    # ---------------------------------------------------------------- target

    def set_result(self, result: object | None, *, force: bool = False) -> None:
        if self._disposed:
            return
        card_ids = _result_card_ids(result)
        try:
            note_id = int(getattr(result, "note_id", 0) or 0)
        except (TypeError, ValueError):
            note_id = 0
        old_key = (
            int(getattr(self._result, "note_id", 0) or 0)
            if self._result is not None
            else 0,
            self._card_ids,
        )
        new_key = (note_id, card_ids)
        self._result = result
        self._card_ids = card_ids
        if force or old_key != new_key:
            self._sibling_index = 0
            self._state = "question"
        elif card_ids:
            self._sibling_index = min(self._sibling_index, len(card_ids) - 1)
        else:
            self._sibling_index = 0

        title = str(getattr(result, "title", "") or "").strip()
        self.pane.set_target_title(title or "Preview")
        self._update_controls()
        self._editor_target_generation += 1
        self._editor_target_force = self._editor_target_force or bool(force)
        self._schedule_render(force=force)
        if (
            not self._hidden
            and self._editor is not None
            and self.pane.mode() == "edit"
        ):
            self._sync_editor_to_target()

    def _current_card_id(self) -> int:
        if not self._card_ids:
            return 0
        return self._card_ids[self._sibling_index]

    def _current_card(self) -> Any | None:
        card_id = self._current_card_id()
        if card_id <= 0 or getattr(self.mw, "col", None) is None:
            return None
        try:
            return self.mw.col.get_card(card_id)
        except Exception:
            return None

    def _update_controls(self) -> None:
        count = len(self._card_ids)
        self.pane.set_sibling_navigation_available(count > 1)
        self.sibling_label.setText(
            f"{self._sibling_index + 1}/{count}" if count else "0/0"
        )
        self.previous_button.setEnabled(count > 1 and self._sibling_index > 0)
        self.next_button.setEnabled(
            count > 1 and self._sibling_index + 1 < count
        )
        available = count > 0
        self.replay_button.setEnabled(available)
        self.flip_button.setEnabled(available)
        answer = self._state == "answer"
        self.flip_button.setText("Front" if answer else "Answer")
        self.flip_button.setAccessibleName(
            "Show card front" if answer else "Show card answer"
        )

    def previous_sibling(self) -> bool:
        return self._move_sibling(-1)

    def next_sibling(self) -> bool:
        return self._move_sibling(1)

    def _move_sibling(self, offset: int) -> bool:
        target = self._sibling_index + int(offset)
        if not 0 <= target < len(self._card_ids):
            return False
        self._sibling_index = target
        self._state = "question"
        self._editor_target_generation += 1
        self._update_controls()
        self._schedule_render(force=True)
        if (
            not self._hidden
            and self._editor is not None
            and self.pane.mode() == "edit"
        ):
            self._sync_editor_to_target()
        return True

    # -------------------------------------------------------------- rendering

    def _schedule_render(self, *, force: bool = False) -> None:
        if self._disposed or self._hidden or self.pane.mode() != "card":
            return
        if force:
            self._last_render_at = 0.0
        # Coalesce fast result motion while still feeling immediate.
        self._render_timer.start(35)

    def refresh(self) -> None:
        self._schedule_render(force=True)

    def _render_now(self) -> None:
        if self._disposed or self._hidden or self.pane.mode() != "card":
            return
        elapsed_ms = (time.monotonic() - self._last_render_at) * 1000
        if elapsed_ms < 120:
            self._render_timer.start(max(1, round(120 - elapsed_ms)))
            return
        self._last_render_at = time.monotonic()

        from anki.tags import MARKED_TAG
        from aqt import gui_hooks
        from aqt.sound import av_player
        from aqt.theme import theme_manager

        card = self._current_card()
        if card is None:
            text = "This card is no longer available."
            self.web.eval(
                f"_showQuestion({json.dumps(text)}, '', '');"
                "_drawFlag(0); _drawMark(false);"
            )
            self._card_ids = ()
            self._sibling_index = 0
            self._update_controls()
            if self._editor is not None:
                self._editor.set_note(None)
            return

        flag = int(card.user_flag())
        try:
            marked = bool(card.note(reload=True).has_tag(MARKED_TAG))
        except Exception:
            marked = False
        self.web.eval(
            f"_drawFlag({flag}); _drawMark({json.dumps(marked)});"
        )

        answer_text = card.answer()
        if self._state == "answer":
            function = "_showAnswer"
            text = answer_text
        else:
            function = "_showQuestion"
            text = card.question(reload=True)
        text = re.sub(r"\[\[type:[^]]+\]\]", "", text)
        body_class = theme_manager.body_classes_for_card_ord(card.ord)

        if card.autoplay():
            self.web.setPlaybackRequiresGesture(False)
            audio = (
                card.answer_av_tags()
                if self._state == "answer"
                else card.question_av_tags()
            )
        else:
            self.web.setPlaybackRequiresGesture(True)
            audio = []
        gui_hooks.av_player_will_play_tags(audio, self._state, self)
        av_player.play_tags(audio)

        text = self.mw.prepare_card_text_for_display(text)
        text = gui_hooks.card_will_show(
            text,
            card,
            f"preview{self._state.capitalize()}",
        )
        if self._state == "question":
            escaped_answer = self.mw.col.media.escape_media_filenames(
                answer_text
            )
            script = (
                f"{function}({json.dumps(text)}, "
                f"{json.dumps(escaped_answer)}, {json.dumps(body_class)});"
            )
        else:
            script = (
                f"{function}({json.dumps(text)}, "
                f"{json.dumps(body_class)});"
            )
        self.web.eval(script)
        self._update_controls()

    def flip(self) -> None:
        if not self._card_ids:
            return
        self._state = "answer" if self._state == "question" else "question"
        self._update_controls()
        self._schedule_render(force=True)

    def replay_audio(self) -> None:
        from aqt.reviewer import replay_audio

        card = self._current_card()
        if card is not None:
            replay_audio(card, self._state == "question")

    def _stop_audio(self) -> None:
        try:
            from aqt.sound import av_player

            av_player.stop_and_clear_queue()
        except (ImportError, RuntimeError):
            pass

    def _on_bridge_command(self, command: str) -> Any:
        if command.startswith("play:"):
            from aqt.sound import play_clicked_audio

            card = self._current_card()
            if card is not None:
                play_clicked_audio(command, card)
        return None

    # ---------------------------------------------------------------- editor

    def _set_surface_mode(self, mode: str) -> None:
        """Explicitly hide the inactive Anki web surface.

        QStackedWidget normally propagates page visibility, but QtWebEngine can
        retain a composited frame when two Anki webviews share a splitter.
        Toggling both each host and its webview prevents the rendered card from
        painting over the inline editor.
        """

        mode = str(mode).casefold()
        show_card = mode == "card"
        show_editor = mode == "edit"
        if not show_card:
            self._stop_audio()
        try:
            self.pane.set_card_controls_visible(show_card)
        except RuntimeError:
            pass
        editor_web = getattr(self._editor, "web", None)
        for widget, visible in (
            (self._card_container, show_card),
            (getattr(self, "web", None), show_card),
            (self._editor_container, show_editor),
            (editor_web, show_editor),
        ):
            if widget is None:
                continue
            try:
                widget.setVisible(visible)
            except RuntimeError:
                continue

    def set_mode(self, mode: str) -> None:
        if self._disposed:
            return
        mode = "edit" if str(mode).casefold() == "edit" else "card"
        self.pane.set_mode(mode)
        self._set_surface_mode(mode)
        if mode == "edit":
            try:
                self._ensure_editor()
                # Editor.setupWeb() calls show(), so re-assert the selected
                # surface after lazy editor construction.
                self._set_surface_mode("edit")
                self._sync_editor_to_target()
            except Exception as error:
                self.pane.set_mode("card")
                self._set_surface_mode("card")
                self._on_error(f"Could not open the inline editor: {error}")
                self._schedule_render(force=True)
                # The button's clicked signal can finish toggling after this
                # handler returns. Re-assert Card on the next event-loop turn
                # so a failed editor can never leave a misleading Edit state.
                from aqt.qt import QTimer

                QTimer.singleShot(0, self._restore_card_mode)
        else:
            self.prepare_for_external_change(self.refresh)

    def _restore_card_mode(self) -> None:
        if self._disposed:
            return
        try:
            self.pane.set_mode("card")
            self._set_surface_mode("card")
            self._schedule_render(force=True)
        except RuntimeError:
            return

    def _ensure_editor(self) -> None:
        if self._editor is not None:
            return
        from aqt import gui_hooks
        from aqt.editor import Editor, EditorMode

        def add_preview_link(editor: Any) -> None:
            editor._links["preview"] = lambda _editor: self.pane.set_mode(
                "card",
                emit=True,
            )

        gui_hooks.editor_did_init.append(add_preview_link)
        try:
            self._editor = Editor(
                self.mw,
                self._editor_container,
                self.parent_window,
                # This pane is an edit-current-card host, not an Anki Browser.
                # Browser mode invites third-party editor hooks to assume the
                # parent window exposes Browser-only attributes.
                editor_mode=EditorMode.EDIT_CURRENT,
            )
        finally:
            try:
                gui_hooks.editor_did_init.remove(add_preview_link)
            except ValueError:
                pass

    def _sync_editor_to_target(self) -> None:
        editor = self._editor
        if (
            editor is None
            or self._editor_switching
            or self._disposed
            or self._hidden
            or self.pane.mode() != "edit"
        ):
            return
        card = self._current_card()
        target_note_id = 0
        if card is not None:
            try:
                target_note_id = int(card.note().id)
            except Exception:
                card = None
        current_note_id = int(getattr(getattr(editor, "note", None), "id", 0) or 0)
        if current_note_id and current_note_id != target_note_id:
            self._editor_switching = True
            editor.call_after_note_saved(self._finish_editor_switch)
            return
        self._apply_editor_target(card)

    def _finish_editor_switch(self) -> None:
        if self._disposed:
            return
        self._editor_switching = False
        if self._hidden or self.pane.mode() != "edit":
            return
        self._apply_editor_target(self._current_card())

    def _apply_editor_target(self, card: Any | None) -> None:
        editor = self._editor
        if editor is None:
            return
        force = self._editor_target_force
        self._editor_target_force = False
        if card is None:
            editor.card = None
            editor.set_note(None)
            return
        note = card.note()
        current_note_id = int(getattr(getattr(editor, "note", None), "id", 0) or 0)
        editor.card = card
        if force or current_note_id != int(note.id):
            editor.set_note(note)

    def _on_operation_did_execute(self, changes: Any, handler: object | None) -> None:
        if self._disposed:
            return
        note_text_changed = bool(getattr(changes, "note_text", False))
        card_changed = bool(getattr(changes, "card", False))
        if note_text_changed or card_changed:
            self.refresh()
        if not note_text_changed:
            return
        editor = self._editor
        if (
            editor is None
            or self._hidden
            or self.pane.mode() != "edit"
            or handler is editor
            or editor.note is None
        ):
            return
        try:
            editor.note.load()
        except Exception:
            editor.set_note(None)
            return
        editor.set_note(editor.note)

    # --------------------------------------------------------------- lifecycle

    def show(self) -> None:
        self._hidden = False
        self._set_surface_mode(self.pane.mode())
        self._schedule_render(force=True)

    def hide(self) -> None:
        self._hidden = True
        self._set_surface_mode("hidden")
        try:
            self._render_timer.stop()
        except RuntimeError:
            pass
        # Closing the pane must not leave a live native editor attached to a
        # note. Save first, detach it, and return to Card mode for the next
        # open. While that asynchronous save finishes, _hidden also prevents
        # result navigation from switching the editor to another note.
        self.prepare_for_external_change()

    def flush(self, callback: Callable[[], None] | None = None) -> None:
        callback = callback or (lambda: None)
        editor = self._editor
        if editor is None or editor.note is None:
            callback()
            return
        try:
            editor.call_after_note_saved(callback)
        except RuntimeError:
            callback()

    def prepare_for_external_change(
        self,
        callback: Callable[[], None] | None = None,
    ) -> None:
        """Save and detach the native editor before another editor/op runs."""

        callback = callback or (lambda: None)

        def detached() -> None:
            if self._disposed:
                callback()
                return
            editor = self._editor
            if editor is not None:
                try:
                    editor.card = None
                    editor.set_note(None)
                except Exception:
                    pass
            try:
                self.pane.set_mode("card")
                self._set_surface_mode(
                    "hidden" if self._hidden else "card"
                )
                self._schedule_render(force=True)
            except RuntimeError:
                pass
            callback()

        self.flush(detached)

    def cleanup_after_save(
        self,
        callback: Callable[[], None] | None = None,
    ) -> None:
        """Save the last web-field edit, then release native resources once."""

        if callback is not None:
            self._cleanup_callbacks.append(callback)
        if self._disposed:
            self._run_cleanup_callbacks()
            return
        if self._cleanup_started:
            return
        self._cleanup_started = True
        self.flush(self.cleanup)

    def cleanup(self) -> None:
        if self._disposed:
            self._run_cleanup_callbacks()
            return
        self._disposed = True
        self._stop_audio()
        try:
            from aqt import gui_hooks

            gui_hooks.operation_did_execute.remove(
                self._on_operation_did_execute
            )
        except (ImportError, ValueError):
            pass
        render_timer = self._render_timer
        if render_timer is not None:
            try:
                render_timer.stop()
                render_timer.timeout.disconnect(self._render_now)
                render_timer.setParent(None)
                render_timer.deleteLater()
            except (RuntimeError, TypeError):
                pass
            self._render_timer = None
        for signal, slot in getattr(self, "_pane_signal_bindings", ()):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._pane_signal_bindings = ()
        if self._editor is not None:
            try:
                self._editor.cleanup()
            except RuntimeError:
                pass
            self._editor = None
        if self.web is not None:
            try:
                self.web.cleanup()
            except RuntimeError:
                pass
            self.web = None
        for host, container in (
            (self.pane.card_host, self._card_container),
            (self.pane.editor_host, self._editor_container),
        ):
            try:
                layout = host.layout()
                if layout is not None:
                    layout.removeWidget(container)
                container.hide()
                container.setParent(None)
                container.deleteLater()
            except RuntimeError:
                pass
        self._run_cleanup_callbacks()

    def _run_cleanup_callbacks(self) -> None:
        callbacks = self._cleanup_callbacks
        self._cleanup_callbacks = []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue


def create_inline_result_inspector(
    mw: Any,
    *,
    pane: Any,
    parent_window: Any,
    initial_result: object,
    on_error: Callable[[str], None],
) -> InlineResultInspector:
    """Create the Anki-specific contents for an existing inline pane."""

    return InlineResultInspector(
        mw,
        pane=pane,
        parent_window=parent_window,
        initial_result=initial_result,
        on_error=on_error,
    )
