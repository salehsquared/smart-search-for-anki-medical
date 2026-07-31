"""Small, host-agnostic shell for the inline card preview and note editor."""

from __future__ import annotations

from typing import Optional

from .widgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    Qt,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)


class InlineResultPane(QFrame):
    """Right-hand pane whose bodies are populated by the Anki host adapter.

    This module intentionally has no ``aqt`` imports, which keeps the search UI
    independently testable.  The host installs Anki's reviewer renderer in
    :attr:`card_host` and its native Editor in :attr:`editor_host`.
    """

    closeRequested = pyqtSignal()
    expandedRequested = pyqtSignal(bool)
    modeChanged = pyqtSignal(str)  # "card" | "edit"
    previousSiblingRequested = pyqtSignal()
    nextSiblingRequested = pyqtSignal()
    replayRequested = pyqtSignal()
    flipRequested = pyqtSignal()

    CARD_PAGE = 0
    EDIT_PAGE = 1

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("inlineResultPane")
        self.setMinimumWidth(410)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._expanded = False
        self._target_title = "Preview"
        self._has_sibling_navigation = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QFrame(self)
        self.header.setObjectName("inlinePreviewHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(6, 8, 6, 8)
        header_layout.setSpacing(4)

        self.previous_button = QToolButton(self.header)
        self.previous_button.setObjectName("inlinePreviewNavigation")
        self.previous_button.setText("‹")
        self.previous_button.setToolTip("Previous sibling card")
        self.previous_button.setAccessibleName("Previous sibling card")
        self.previous_button.setEnabled(False)
        self.previous_button.clicked.connect(
            lambda _checked=False: self.previousSiblingRequested.emit()
        )
        header_layout.addWidget(self.previous_button)

        self.sibling_label = QLabel("0/0", self.header)
        self.sibling_label.setObjectName("inlinePreviewSibling")
        self.sibling_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sibling_label.setAccessibleName("Sibling card position")
        header_layout.addWidget(self.sibling_label)

        self.next_button = QToolButton(self.header)
        self.next_button.setObjectName("inlinePreviewNavigation")
        self.next_button.setText("›")
        self.next_button.setToolTip("Next sibling card")
        self.next_button.setAccessibleName("Next sibling card")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(
            lambda _checked=False: self.nextSiblingRequested.emit()
        )
        header_layout.addWidget(self.next_button)

        self.replay_button = QToolButton(self.header)
        self.replay_button.setObjectName("inlinePreviewCardControl")
        self.replay_button.setText("Replay")
        self.replay_button.setAccessibleName("Replay card audio")
        self.replay_button.setEnabled(False)
        self.replay_button.clicked.connect(
            lambda _checked=False: self.replayRequested.emit()
        )
        header_layout.addWidget(self.replay_button)

        self.flip_button = QToolButton(self.header)
        self.flip_button.setObjectName("inlinePreviewCardControl")
        self.flip_button.setText("Answer")
        self.flip_button.setAccessibleName("Show answer")
        self.flip_button.setEnabled(False)
        self.flip_button.clicked.connect(
            lambda _checked=False: self.flipRequested.emit()
        )
        header_layout.addWidget(self.flip_button)
        header_layout.addStretch(1)

        self.card_button = QToolButton(self.header)
        self.card_button.setObjectName("inlinePreviewMode")
        self.card_button.setText("Card")
        self.card_button.setCheckable(True)
        self.card_button.setChecked(True)
        self.card_button.setAccessibleName("Show rendered card")
        header_layout.addWidget(self.card_button)

        self.edit_button = QToolButton(self.header)
        self.edit_button.setObjectName("inlinePreviewMode")
        self.edit_button.setText("Edit")
        self.edit_button.setCheckable(True)
        self.edit_button.setAccessibleName("Edit note fields")
        header_layout.addWidget(self.edit_button)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.card_button, self.CARD_PAGE)
        self.mode_group.addButton(self.edit_button, self.EDIT_PAGE)
        self.card_button.clicked.connect(
            lambda _checked=False: self._select_mode(self.CARD_PAGE)
        )
        self.edit_button.clicked.connect(
            lambda _checked=False: self._select_mode(self.EDIT_PAGE)
        )

        self.expand_button = QToolButton(self.header)
        self.expand_button.setObjectName("inlinePreviewChrome")
        self.expand_button.setText("↗")
        self.expand_button.setToolTip("Expand preview")
        self.expand_button.setAccessibleName("Expand preview")
        self.expand_button.clicked.connect(self._toggle_expanded)
        header_layout.addWidget(self.expand_button)

        self.close_button = QToolButton(self.header)
        self.close_button.setObjectName("inlinePreviewChrome")
        self.close_button.setText("×")
        self.close_button.setToolTip(
            "Close preview until another result is selected"
        )
        self.close_button.setAccessibleName("Close preview")
        self.close_button.clicked.connect(self.closeRequested)
        header_layout.addWidget(self.close_button)

        root.addWidget(self.header)

        self.body = QStackedWidget(self)
        self.body.setObjectName("inlinePreviewBody")
        self.card_host = QWidget(self.body)
        self.card_host.setObjectName("inlineCardHost")
        card_host_layout = QVBoxLayout(self.card_host)
        card_host_layout.setContentsMargins(0, 0, 0, 0)
        card_host_layout.setSpacing(0)
        self.editor_host = QWidget(self.body)
        self.editor_host.setObjectName("inlineEditorHost")
        editor_host_layout = QVBoxLayout(self.editor_host)
        editor_host_layout.setContentsMargins(0, 0, 0, 0)
        editor_host_layout.setSpacing(0)
        self.body.addWidget(self.card_host)
        self.body.addWidget(self.editor_host)
        root.addWidget(self.body, 1)
        self.set_card_controls_visible(True)

    def mode(self) -> str:
        return "edit" if self.body.currentIndex() == self.EDIT_PAGE else "card"

    def set_mode(self, mode: str, *, emit: bool = False) -> None:
        page = self.EDIT_PAGE if str(mode).casefold() == "edit" else self.CARD_PAGE
        self.body.setCurrentIndex(page)
        self.card_button.setChecked(page == self.CARD_PAGE)
        self.edit_button.setChecked(page == self.EDIT_PAGE)
        self.set_card_controls_visible(page == self.CARD_PAGE)
        if emit:
            self.modeChanged.emit(self.mode())

    def _select_mode(self, page: int) -> None:
        self.set_mode("edit" if page == self.EDIT_PAGE else "card")
        self.modeChanged.emit(self.mode())

    def set_target_title(self, text: str) -> None:
        value = str(text or "").strip()
        self._target_title = value or "Preview"
        self.header.setToolTip(self._target_title)
        self.setAccessibleDescription(self._target_title)

    def target_title(self) -> str:
        return self._target_title

    def set_card_controls_visible(self, visible: bool) -> None:
        visible = bool(visible)
        self.previous_button.setVisible(
            visible and self._has_sibling_navigation
        )
        self.next_button.setVisible(
            visible and self._has_sibling_navigation
        )
        for widget in (
            self.sibling_label,
            self.replay_button,
            self.flip_button,
        ):
            widget.setVisible(visible)

    def set_sibling_navigation_available(self, available: bool) -> None:
        self._has_sibling_navigation = bool(available)
        self.set_card_controls_visible(self.mode() == "card")

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.expand_button.setText("↙" if self._expanded else "↗")
        label = "Restore split view" if self._expanded else "Expand preview"
        self.expand_button.setToolTip(label)
        self.expand_button.setAccessibleName(label)

    def is_expanded(self) -> bool:
        return self._expanded

    def _toggle_expanded(self) -> None:
        self.expandedRequested.emit(not self._expanded)
