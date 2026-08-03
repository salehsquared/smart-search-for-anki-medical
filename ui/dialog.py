"""The smart-search dialog: layout, states, and keyboard-first interaction.

The dialog is intentionally "dumb": it renders states and emits intent
signals. All backend communication lives in :mod:`ui.controller`.
"""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
import platform
import sys
from collections.abc import Callable
from typing import Optional, Sequence

from .contracts import (
    AboutInfo,
    Correction,
    DeckCatalog,
    IndexState,
    IndexStatus,
    SearchMode,
    SearchResponse,
    SemanticRecovery,
    SemanticState,
    SemanticStatus,
)
from .deck_picker import DeckPickerPopup, DeckScopeButton
from .deck_query import apply_deck_selection
from .results import ResultsView
from .theme import chrome_colors, semantic_icon_pixmap
from .preview_pane import InlineResultPane
from .widgets import (  # Qt shim + custom widgets
    AboutPanel,
    ChipBar,
    IndexStatusWidget,
    SearchField,
    SegmentedModeControl,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QEvent,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    Qt,
    QTabWidget,
    QTimer,
    QToolButton,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)

DEBOUNCE_MS = 150
BATCH_CONFIRM_THRESHOLD = 100

_PAGE_HELP = 0
_PAGE_RESULTS = 1
_PAGE_MESSAGE = 2

_PRIMARY_KEY = "⌘" if sys.platform == "darwin" else "Ctrl"
# Qt swaps the logical Ctrl/Meta roles on macOS: ``Ctrl`` in a
# QKeySequence is the physical Command key, while ``Meta`` is the physical
# Control key.  Keep this shortcut on the physical Control key so it does not
# collide with Anki's Command+Shift+P "Switch Profile" action.
_PREVIEW_SHORTCUT = (
    "Meta+Shift+P" if sys.platform == "darwin" else "Ctrl+Shift+P"
)


def _semantic_availability_text() -> str:
    """Truthful platform availability line for the semantic setup card."""

    if sys.platform == "darwin" and platform.machine() in {"arm64", "aarch64"}:
        return "Available on macOS 14+ with Apple silicon"
    return "Semantic Search requires macOS 14+ with Apple silicon"


HELP_HTML = f"""\
<h3>Search your collection</h3>
<p>Type above to search notes. Smart mode fixes typos and expands common
medical aliases; Exact matches literally; Semantic matches by meaning.</p>
<p><b>Smart and Exact</b> use the initial fast-search setup. <b>Semantic</b>
has a separate one-time preparation that takes longer. You can keep using
Smart and Exact while Semantic is being prepared.</p>
<p>Use structured filters such as <b>deck:AnKing</b>, <b>tag:cardio</b>, or
<b>notetype:Cloze</b> to narrow results.</p>
<ul>
<li><b>Down / Up</b> — move through results; the preview follows</li>
<li><b>Space / Right Arrow</b> — show the card answer</li>
<li><b>Left Arrow</b> — return to the card front</li>
<li><b>Return in the search field</b> — run the current search immediately</li>
<li><b>Return in the results</b> — open the highlighted note in the Browser</li>
<li><b>Ctrl+Shift+P</b> — toggle the card preview pane</li>
<li><b>Shift+Space</b> — check or uncheck the highlighted result for bulk actions</li>
<li><b>Shift+click</b> — check a range of results</li>
<li><b>Right-click</b> — open, flag, suspend, or tag the clicked/checked results</li>
<li><b>{_PRIMARY_KEY}+Return</b> — open checked results, or all shown if none are checked</li>
<li><b>{_PRIMARY_KEY}+1 / 2 / 3</b> — Smart, Exact, Semantic mode</li>
<li><b>{_PRIMARY_KEY}+L</b> — jump back to the search field</li>
<li><b>Esc</b> — clear the query, then close</li>
</ul>
"""


def _default_about_info() -> AboutInfo:
    """Fallback attribution for standalone use; the host passes real values.

    The version prefers the bundle manifest and falls back to the package
    version, so tests and standalone development still see a truthful panel.
    """

    bundle_root = Path(__file__).resolve().parent.parent
    version = ""
    try:
        manifest = json.loads(
            (bundle_root / "manifest.json").read_text(encoding="utf-8")
        )
        version = str(manifest.get("human_version") or "").strip()
    except Exception:
        version = ""
    if not version:
        try:
            from . import __version__ as package_version

            version = str(package_version or "").strip()
        except Exception:
            version = ""
    return AboutInfo(
        product_name="Smart Search for Anki — Medical",
        creator="Saleh Mostafa",
        version=version,
        logo_path=str(bundle_root / "resources" / "medbrevia-logo.png"),
        website_url="https://medbrevia.com/app",
        feedback_url="mailto:product@medbrevia.com",
        privacy_url="https://medbrevia.com/legal/smart-search-privacy",
    )


class _SettingsDialog(QDialog):
    """Small preferences dialog backed by UISettings, plus a quiet About tab."""

    semanticInstallRequested = pyqtSignal()
    semanticIndexRequested = pyqtSignal()
    updateRequested = pyqtSignal()

    def __init__(
        self,
        mode: SearchMode,
        result_limit: int,
        semantic: Optional[SemanticStatus],
        parent: Optional[QWidget] = None,
        *,
        preview_enabled: bool = True,
        text_index_ready: bool = True,
        about: Optional[AboutInfo] = None,
    ) -> None:
        super().__init__(parent)
        self._text_index_ready = text_index_ready
        self.setObjectName("searchSettingsDialog")
        self.setWindowTitle("Search settings")
        self.setAccessibleName("Search settings")
        self.setMinimumSize(620, 540)
        self.resize(760, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("settingsTabs")
        self.tabs.setAccessibleName("Settings sections")
        root.addWidget(self.tabs)

        search_page = QWidget(self.tabs)
        search_page.setObjectName("searchSettingsPage")
        form = QFormLayout(search_page)
        form.setContentsMargins(28, 30, 28, 24)
        form.setHorizontalSpacing(22)
        form.setVerticalSpacing(18)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFormAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.tabs.addTab(search_page, "Search")

        self.mode_combo = QComboBox(search_page)
        for m in (SearchMode.SMART, SearchMode.EXACT, SearchMode.SEMANTIC):
            self.mode_combo.addItem(m.label, m)
        self.mode_combo.setCurrentIndex(
            [SearchMode.SMART, SearchMode.EXACT, SearchMode.SEMANTIC].index(mode)
        )
        self.mode_combo.setAccessibleName("Default search mode")
        form.addRow("Default mode", self.mode_combo)

        self.limit_spin = QSpinBox(search_page)
        self.limit_spin.setRange(10, 200)
        self.limit_spin.setSingleStep(10)
        self.limit_spin.setValue(result_limit)
        self.limit_spin.setAccessibleName("Maximum results per search")
        form.addRow("Result limit", self.limit_spin)

        control_width = max(
            190,
            self.mode_combo.sizeHint().width(),
            self.limit_spin.sizeHint().width(),
        )
        for control in (self.mode_combo, self.limit_spin):
            control.setMinimumWidth(control_width)
            control.setMaximumWidth(control_width)
            control.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Preferred,
            )

        self.preview_check = QCheckBox(
            "Show automatically while browsing results",
            search_page,
        )
        self.preview_check.setChecked(bool(preview_enabled))
        self.preview_check.setAccessibleName("Enable card preview")
        self.preview_check.setToolTip(
            "Open Anki's rendered card Preview when you enter or move through results"
        )
        form.addRow("Card preview", self.preview_check)

        # The status copy and action share one expanding field column. Native
        # height-for-width sizing keeps wrapped copy readable without forcing
        # a large fixed row in compact dialogs.
        self.semantic_field = QWidget(search_page)
        self.semantic_field.setObjectName("semanticSettingsField")
        self.semantic_field.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        semantic_column = QVBoxLayout(self.semantic_field)
        semantic_column.setContentsMargins(0, 0, 0, 0)
        semantic_column.setSpacing(12)
        semantic_column.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.semantic_status = QLabel(self.semantic_field)
        self.semantic_status.setWordWrap(True)
        self.semantic_status.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.semantic_status.setAccessibleName("Semantic search status")
        self.semantic_status.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        semantic_column.addWidget(self.semantic_status)

        self.semantic_action = QPushButton(self.semantic_field)
        self.semantic_action.setObjectName("semanticSettingsAction")
        self.semantic_action.setMinimumHeight(40)
        self.semantic_action.setAccessibleName("Set up semantic search")
        semantic_column.addWidget(
            self.semantic_action,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )
        self._set_semantic_status(semantic)
        form.addRow("Semantic search", self.semantic_field)
        semantic_label = form.labelForField(self.semantic_field)
        if semantic_label is not None:
            semantic_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            )

        self.about_panel = AboutPanel(
            about if about is not None else _default_about_info(),
            self.tabs,
        )
        self.about_panel.updateRequested.connect(self._request_update)
        self.tabs.addTab(self.about_panel, "About")

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.button_box.setObjectName("settingsButtons")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)
        self._apply_theme()

    def values(self) -> tuple[SearchMode, int, bool]:
        return (
            self.mode_combo.currentData(),
            self.limit_spin.value(),
            self.preview_check.isChecked(),
        )

    def _request_update(self) -> None:
        """Close Settings before Anki presents its native update progress."""

        self.updateRequested.emit()
        self.accept()

    def _set_semantic_status(self, status: Optional[SemanticStatus]) -> None:
        if status is None:
            self.semantic_status.setText("Status is not available.")
            self.semantic_action.setVisible(False)
            return

        separation = "\nUses a separate index from Smart and Exact."
        if status.state is SemanticState.INDEXING:
            separation += "\nSmart and Exact remain available while this finishes."
        self.semantic_status.setText(
            status.summary
            + separation
        )
        state = status.state
        if state is SemanticState.UNSUPPORTED:
            self.semantic_action.setVisible(False)
        elif state is SemanticState.NOT_INSTALLED:
            self.semantic_action.setText("Enable Semantic Search")
            self.semantic_action.setVisible(True)
            self.semantic_action.clicked.connect(self.semanticInstallRequested)
        elif state is SemanticState.MODEL_READY:
            self.semantic_action.setText(
                "Prepare Semantic Search Now"
                if status.auto_start
                else "Prepare Semantic Search"
            )
            self.semantic_action.setVisible(True)
            self.semantic_action.clicked.connect(self.semanticIndexRequested)
        elif state is SemanticState.ERROR:
            repair_model = status.recovery is SemanticRecovery.MODEL
            self.semantic_action.setText(
                "Repair Semantic Search"
                if repair_model
                else "Try Again"
            )
            self.semantic_action.setVisible(True)
            self.semantic_action.clicked.connect(
                self.semanticInstallRequested
                if repair_model
                else self.semanticIndexRequested
            )
        elif state is SemanticState.INDEXING:
            self.semantic_action.setText("Preparing Semantic Search…")
            self.semantic_action.setEnabled(False)
            self.semantic_action.setVisible(True)
        elif state is SemanticState.RESTART_REQUIRED:
            self.semantic_action.setVisible(False)
        else:
            self.semantic_action.setVisible(False)

        if not self._text_index_ready and not self.semantic_action.isHidden():
            wait_text = (
                "Wait for Smart & Exact setup to finish before using semantic "
                "controls."
            )
            self.semantic_status.setText(
                self.semantic_status.text()
                + "\nSemantic controls are waiting for Smart & Exact setup."
            )
            self.semantic_action.setEnabled(False)
            self.semantic_action.setToolTip(wait_text)
            self.semantic_action.setAccessibleDescription(wait_text)

    def _apply_theme(self) -> None:
        c = chrome_colors(self)
        self.setStyleSheet(
            "QDialog#searchSettingsDialog {"
            f" background: {c['window']}; color: {c['text']};"
            "}"
            "QTabWidget#settingsTabs::pane {"
            f" background: {c['surface']}; border: 1px solid {c['border']};"
            " border-radius: 14px; top: -1px;"
            "}"
            "QTabWidget#settingsTabs QTabBar::tab {"
            f" color: {c['muted']}; background: transparent;"
            " min-width: 112px; min-height: 42px; padding: 0 16px;"
            " border: none; border-radius: 9px; font-weight: 700;"
            "}"
            "QTabWidget#settingsTabs QTabBar::tab:selected {"
            f" color: {c['accent_text']}; background: {c['accent']};"
            "}"
            "QTabWidget#settingsTabs QTabBar::tab:hover:!selected {"
            f" color: {c['text']}; background: {c['surface_high']};"
            "}"
            "QWidget#searchSettingsPage QLabel {"
            f" color: {c['text']};"
            "}"
            "QPushButton#semanticSettingsAction {"
            f" background: {c['accent']}; color: {c['accent_text']};"
            f" border: 1px solid {c['accent']}; border-radius: 9px;"
            " padding: 6px 16px; font-weight: 700;"
            "}"
            "QPushButton#semanticSettingsAction:disabled {"
            f" background: {c['surface_high']}; color: {c['muted']};"
            f" border-color: {c['border']};"
            "}"
            "QDialogButtonBox#settingsButtons QPushButton {"
            f" background: {c['surface_high']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 9px;"
            " min-width: 82px; min-height: 36px; padding: 0 12px;"
            " font-weight: 600;"
            "}"
            "QDialogButtonBox#settingsButtons QPushButton:hover {"
            f" border-color: {c['accent_mid']};"
            "}"
        )

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type()
            in (
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
            )
            and not getattr(self, "_refreshing_palette", False)
        ):
            self._refreshing_palette = True
            try:
                self._apply_theme()
                if hasattr(self, "about_panel"):
                    self.about_panel.refresh_palette()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)


class SearchDialog(QDialog):
    """Keyboard-first command-palette search dialog."""

    # Intents consumed by the controller.
    queryEdited = pyqtSignal(str)              # immediate, before debounce
    searchRequested = pyqtSignal(str)          # debounced query text
    modeSelected = pyqtSignal(object)          # SearchMode
    openRequested = pyqtSignal(object)         # SearchResult
    openAllRequested = pyqtSignal(object)      # tuple[SearchResult, ...]
    previewToggleRequested = pyqtSignal(object)  # SearchResult | None
    previewResultChanged = pyqtSignal(object)  # SearchResult | None
    previewSideRequested = pyqtSignal(object, str)  # result, "question" | "answer"
    previewModeChanged = pyqtSignal(str)  # "card" | "edit"
    filterRemoveRequested = pyqtSignal(object)   # FilterChip
    correctionDismissRequested = pyqtSignal(object)  # Correction
    correctionLiteralRequested = pyqtSignal(object)  # Correction
    rebuildRequested = pyqtSignal()
    semanticInstallRequested = pyqtSignal()
    semanticIndexRequested = pyqtSignal()
    updateRequested = pyqtSignal()
    settingsChanged = pyqtSignal(object, int, bool)  # mode, limit, preview?
    flagRequested = pyqtSignal(object, int)  # tuple[SearchResult, ...], 0..7
    suspensionRequested = pyqtSignal(object, bool)  # results, suspend?
    tagActionRequested = pyqtSignal(object, bool)  # results, add?
    deckPickerRequested = pyqtSignal()
    dialogClosed = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        about: Optional[AboutInfo] = None,
    ) -> None:
        super().__init__(parent)
        self._about = about if about is not None else _default_about_info()
        self.setObjectName("smartSearchDialog")
        self.setWindowTitle("Smart Search")
        self.setMinimumSize(760, 520)
        self.resize(1040, 700)
        self.setSizeGripEnabled(True)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_search)

        self._last_response: Optional[SearchResponse] = None
        self._last_status: Optional[IndexStatus] = None
        self._message_kind = ""
        self._result_limit = 50
        self._preview_enabled = True
        self._deck_catalog: Optional[DeckCatalog] = None
        self._close_notified = False
        self._batch_busy = False
        self._batch_results_available = False
        self._batch_control_states: list[tuple[QWidget, bool]] = []
        self._preview_restore_sizes: list[int] = [620, 400]
        self._managed_close_handler: (
            Callable[[Callable[[], None]], None] | None
        ) = None
        self._managed_close_requested = False
        self._build_ui()
        self._install_shortcuts()
        self.show_help()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 14)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)

        self.search_group = QFrame(self)
        self.search_group.setObjectName("deckSearchGroup")
        search_group_layout = QHBoxLayout(self.search_group)
        search_group_layout.setContentsMargins(0, 0, 0, 0)
        search_group_layout.setSpacing(0)

        self.deck_scope = DeckScopeButton(self.search_group)
        self.deck_scope.clicked.connect(self._open_deck_picker)
        search_group_layout.addWidget(self.deck_scope)

        self.search = SearchField(self.search_group)
        self.search.set_compound(True)
        self.search.textEdited.connect(self._on_text_edited)
        self.search.returnPressed.connect(self._on_search_return)
        search_group_layout.addWidget(self.search, 1)
        top.addWidget(self.search_group, 1)

        self.deck_picker = DeckPickerPopup(self)
        self.deck_picker.applied.connect(self._apply_deck_selection)
        self.deck_picker.retryRequested.connect(self._reload_deck_picker)

        self.segmented = SegmentedModeControl(self)
        self.segmented.modeChanged.connect(self._on_mode_changed)
        top.addWidget(self.segmented)
        root.addLayout(top)

        self.chip_bar = ChipBar(self)
        self.chip_bar.filterRemoveRequested.connect(self.filterRemoveRequested)
        self.chip_bar.correctionDismissRequested.connect(self.correctionDismissRequested)
        self.chip_bar.correctionLiteralRequested.connect(self.correctionLiteralRequested)
        root.addWidget(self.chip_bar)

        self.index_notice = QLabel(self)
        self.index_notice.setObjectName("indexNotice")
        self.index_notice.setWordWrap(True)
        self.index_notice.setTextFormat(Qt.TextFormat.RichText)
        self.index_notice.setAccessibleName("Search index notice")
        self.index_notice.setVisible(False)
        root.addWidget(self.index_notice)

        self.stack = QStackedWidget(self)
        help_label = QLabel(HELP_HTML, self)
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        help_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        help_label.setAccessibleName("Search help")
        self.stack.insertWidget(_PAGE_HELP, help_label)

        self.results = ResultsView(self)
        # `activated` covers both Return and double-click on desktop styles.
        self.results.activated.connect(self._on_index_activated)
        self.results.resultContextRequested.connect(
            self._show_result_context_menu
        )
        self.results.currentResultChanged.connect(
            self._on_current_result_changed
        )
        self.results.resultBrowsed.connect(self._on_result_browsed)
        self.stack.insertWidget(_PAGE_RESULTS, self.results)

        message_page = QWidget(self)
        message_page.setObjectName("messagePage")
        message_layout = QVBoxLayout(message_page)
        message_layout.setContentsMargins(16, 18, 16, 18)
        message_layout.setSpacing(0)
        message_layout.addStretch(1)

        self.message_card = QFrame(message_page)
        self.message_card.setObjectName("messageCard")
        self.message_card.setMinimumWidth(480)
        self.message_card.setMaximumWidth(660)
        card_layout = QVBoxLayout(self.message_card)
        card_layout.setContentsMargins(42, 32, 42, 32)
        card_layout.setSpacing(14)

        self.message_icon = QLabel("◔", self.message_card)
        self.message_icon.setObjectName("messageIcon")
        self.message_icon.setFixedSize(84, 84)
        self.message_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_icon.setAccessibleName("Semantic search")
        self.message_icon.setVisible(False)
        card_layout.addWidget(
            self.message_icon,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

        self.message_label = QLabel(self.message_card)
        self.message_label.setObjectName("messageLabel")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setAccessibleName("Search status message")
        card_layout.addWidget(self.message_label)

        self.message_platform_label = QLabel(
            _semantic_availability_text(),
            self.message_card,
        )
        self.message_platform_label.setObjectName("messagePlatform")
        self.message_platform_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_platform_label.setAccessibleName(
            "Semantic search availability"
        )
        self.message_platform_label.setVisible(False)
        card_layout.addWidget(self.message_platform_label)

        self.retry_button = QPushButton("Retry", self.message_card)
        self.retry_button.setObjectName("retryAction")
        self.retry_button.setMinimumHeight(40)
        self.retry_button.setAccessibleName("Retry search")
        self.retry_button.clicked.connect(self._emit_search)
        self.retry_button.setVisible(False)
        card_layout.addWidget(
            self.retry_button,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

        self.message_action = QPushButton(self.message_card)
        self.message_action.setObjectName("messageAction")
        self.message_action.setMinimumHeight(42)
        self.message_action.setMinimumWidth(220)
        self.message_action.setVisible(False)
        self.message_action.clicked.connect(self._run_message_action)
        card_layout.addWidget(
            self.message_action, 0, Qt.AlignmentFlag.AlignCenter
        )
        message_layout.addWidget(
            self.message_card,
            0,
            Qt.AlignmentFlag.AlignCenter,
        )
        message_layout.addStretch(1)
        self.stack.insertWidget(_PAGE_MESSAGE, message_page)

        self.content_splitter = QSplitter(
            Qt.Orientation.Horizontal,
            self,
        )
        self.content_splitter.setObjectName("searchContentSplitter")
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setOpaqueResize(False)
        self.content_splitter.addWidget(self.stack)

        self.preview_pane = InlineResultPane(self.content_splitter)
        self.preview_pane.setVisible(False)
        self.preview_pane.closeRequested.connect(
            lambda: self.previewToggleRequested.emit(None)
        )
        self.preview_pane.expandedRequested.connect(
            self._set_preview_expanded
        )
        self.preview_pane.modeChanged.connect(self.previewModeChanged)
        self.content_splitter.addWidget(self.preview_pane)
        self.content_splitter.setStretchFactor(0, 3)
        self.content_splitter.setStretchFactor(1, 2)
        root.addWidget(self.content_splitter, 1)

        self.batch_bar = QFrame(self)
        self.batch_bar.setObjectName("batchBar")
        self.batch_bar.setMinimumHeight(56)
        batch = QHBoxLayout(self.batch_bar)
        batch.setContentsMargins(12, 7, 12, 7)
        batch.setSpacing(9)

        self.master_check = QCheckBox(self.batch_bar)
        self.master_check.setTristate(True)
        self.master_check.setAccessibleName("Select all shown results")
        self.master_check.setToolTip("Select or clear every result currently shown.")
        self.master_check.clicked.connect(self._toggle_all_results)
        batch.addWidget(self.master_check)

        self.selection_summary = QLabel(
            "0 notes · 0 cards selected",
            self.batch_bar,
        )
        self.selection_summary.setObjectName("selectionSummary")
        self.selection_summary.setAccessibleName("Bulk selection summary")
        batch.addWidget(self.selection_summary)

        self.select_button = QToolButton(self.batch_bar)
        self.select_button.setObjectName("batchSelect")
        self.select_button.setText("Select")
        self.select_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.select_button.setAccessibleName("Selection options")
        select_menu = QMenu(self.select_button)
        self.select_all_action = select_menu.addAction("All shown")
        self.select_all_action.triggered.connect(
            lambda _checked=False: self.results.results_model().set_all_checked(True)
        )
        self.select_none_action = select_menu.addAction("None")
        self.select_none_action.triggered.connect(
            lambda _checked=False: self.results.results_model().set_all_checked(False)
        )
        self.select_invert_action = select_menu.addAction("Invert")
        self.select_invert_action.triggered.connect(
            lambda _checked=False: self.results.results_model().invert_checked()
        )
        self.select_button.setMenu(select_menu)
        batch.addWidget(self.select_button)

        self.preview_button = QToolButton(self.batch_bar)
        self.preview_button.setObjectName("batchPreview")
        self.preview_button.setText("Preview")
        self.preview_button.setCheckable(True)
        self.preview_button.setToolTip(
            "Show the card preview beside the results"
        )
        self.preview_button.setAccessibleName(
            "Toggle card preview for highlighted result"
        )
        self.preview_button.clicked.connect(self._toggle_preview)
        batch.addWidget(self.preview_button)

        self.open_selected_button = QToolButton(self.batch_bar)
        self.open_selected_button.setObjectName("batchBrowser")
        self.open_selected_button.setText("Browser")
        self.open_selected_button.setToolTip(
            "Open exactly the checked cards in Anki's Browser"
        )
        self.open_selected_button.setAccessibleName(
            "Open checked cards in Browser"
        )
        self.open_selected_button.clicked.connect(self._open_selected_results)
        batch.addWidget(self.open_selected_button)

        batch.addStretch(1)

        self.flag_button = QToolButton(self.batch_bar)
        self.flag_button.setObjectName("batchFlag")
        self.flag_button.setText("Flag")
        self.flag_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.flag_button.setAccessibleName("Set card flags")
        flag_menu = QMenu(self.flag_button)
        for flag, label in (
            (0, "Clear"),
            (1, "Red"),
            (2, "Orange"),
            (3, "Green"),
            (4, "Blue"),
            (5, "Pink"),
            (6, "Turquoise"),
            (7, "Purple"),
        ):
            action = flag_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=flag: self._emit_flag(value)
            )
        self.flag_button.setMenu(flag_menu)
        batch.addWidget(self.flag_button)

        self.suspend_button = QToolButton(self.batch_bar)
        self.suspend_button.setObjectName("batchSuspend")
        self.suspend_button.setText("Suspend")
        self.suspend_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.suspend_button.setAccessibleName("Suspend or unsuspend cards")
        suspend_menu = QMenu(self.suspend_button)
        suspend_action = suspend_menu.addAction("Suspend")
        suspend_action.triggered.connect(
            lambda _checked=False: self._emit_suspension(True)
        )
        unsuspend_action = suspend_menu.addAction("Unsuspend")
        unsuspend_action.triggered.connect(
            lambda _checked=False: self._emit_suspension(False)
        )
        self.suspend_button.setMenu(suspend_menu)
        batch.addWidget(self.suspend_button)

        self.tags_button = QToolButton(self.batch_bar)
        self.tags_button.setObjectName("batchTags")
        self.tags_button.setText("Tags")
        self.tags_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.tags_button.setAccessibleName("Add or remove note tags")
        tags_menu = QMenu(self.tags_button)
        add_tag_action = tags_menu.addAction("Add…")
        add_tag_action.triggered.connect(
            lambda _checked=False: self._emit_tag_action(True)
        )
        remove_tag_action = tags_menu.addAction("Remove…")
        remove_tag_action.triggered.connect(
            lambda _checked=False: self._emit_tag_action(False)
        )
        self.tags_button.setMenu(tags_menu)
        batch.addWidget(self.tags_button)

        self.batch_bar.setVisible(False)
        root.insertWidget(root.indexOf(self.content_splitter), self.batch_bar)
        self.results.results_model().checkedChanged.connect(
            self._update_batch_bar
        )

        self.footer_bar = QFrame(self)
        self.footer_bar.setObjectName("footerBar")
        bottom = QHBoxLayout(self.footer_bar)
        bottom.setContentsMargins(8, 10, 0, 0)
        bottom.setSpacing(8)
        self.status = IndexStatusWidget(self)
        bottom.addWidget(self.status)

        self.semantic_status = QLabel("", self)
        self.semantic_status.setAccessibleName("Semantic search status")
        self.semantic_status.setVisible(False)
        bottom.addWidget(self.semantic_status)

        self.semantic_action = QToolButton(self)
        self.semantic_action.setAccessibleName("Set up semantic search")
        self.semantic_action.setVisible(False)
        self.semantic_action.clicked.connect(self._run_semantic_action)
        bottom.addWidget(self.semantic_action)

        self.semantic_progress = QProgressBar(self)
        self.semantic_progress.setRange(0, 100)
        self.semantic_progress.setFixedWidth(150)
        self.semantic_progress.setVisible(False)
        self.semantic_progress.setAccessibleName("Semantic search preparation progress")
        bottom.addWidget(self.semantic_progress)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(160)
        self.progress.setVisible(False)
        self.progress.setAccessibleName("Index rebuild progress")
        bottom.addWidget(self.progress)

        bottom.addStretch(1)

        self.summary = QLabel("", self)
        self.summary.setAccessibleName("Search summary")
        bottom.addWidget(self.summary)

        self.settings_button = QToolButton(self)
        self.settings_button.setObjectName("footerButton")
        self.settings_button.setText("Settings")
        self.settings_button.setAccessibleName("Search settings")
        self.settings_button.setAccessibleDescription("Open search preferences.")
        self.settings_button.clicked.connect(self._open_settings)
        bottom.addWidget(self.settings_button)

        self.rebuild_button = QToolButton(self)
        self.rebuild_button.setObjectName("footerButton")
        self.rebuild_button.setText("Refresh")
        self.rebuild_button.setToolTip(
            "Refresh Smart and Exact search data. Semantic refreshes separately."
        )
        self.rebuild_button.setAccessibleName("Refresh Smart and Exact search data")
        self.rebuild_button.setAccessibleDescription(
            "Refresh Smart and Exact search data. Semantic refreshes separately."
        )
        self.rebuild_button.clicked.connect(self.rebuildRequested)
        bottom.addWidget(self.rebuild_button)
        root.addWidget(self.footer_bar)

        self.search.installEventFilter(self)
        self.results.installEventFilter(self)
        self._apply_theme()

    def _apply_theme(self) -> None:
        c = chrome_colors(self)
        self.message_icon.setPixmap(semantic_icon_pixmap(c, 76))
        self.setStyleSheet(
            "QDialog#smartSearchDialog {"
            f" background: {c['window']}; color: {c['text']};"
            "}"
            "QLabel#indexNotice {"
            f" background: {c['accent_soft']}; color: {c['text']};"
            f" border: 1px solid {c['accent_mid']}; border-radius: 12px;"
            " padding: 12px 16px;"
            "}"
            "QFrame#messageCard {"
            f" background: {c['surface']}; border: 1px solid {c['border']};"
            " border-radius: 18px;"
            "}"
            "QLabel#messageLabel {"
            f" color: {c['text']}; background: transparent; border: none;"
            "}"
            "QLabel#messageIcon {"
            " background: transparent; border: none;"
            "}"
            "QLabel#messagePlatform {"
            f" color: {c['accent']}; background: transparent;"
            " border: none; font-weight: 700;"
            "}"
            "QPushButton#messageAction, QPushButton#retryAction {"
            f" color: {c['accent_text']}; background: {c['accent']};"
            f" border: 1px solid {c['accent']}; border-radius: 9px;"
            " padding: 7px 18px; font-weight: 700;"
            "}"
            "QPushButton#messageAction:hover, QPushButton#retryAction:hover {"
            f" background: {c['accent_mid']};"
            "}"
            "QFrame#batchBar {"
            f" background: {c['surface']}; border: 1px solid {c['border']};"
            " border-radius: 11px;"
            "}"
            "QFrame#batchBar QLabel {"
            " background: transparent; border: none;"
            "}"
            "QLabel#selectionSummary { font-weight: 700; }"
            "QFrame#batchBar QToolButton {"
            f" background: {c['surface_high']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 8px;"
            " min-height: 36px; padding: 0 13px; font-weight: 600;"
            "}"
            "QFrame#batchBar QToolButton:hover {"
            f" border-color: {c['accent_mid']};"
            "}"
            "QFrame#batchBar QToolButton:disabled {"
            f" color: {c['muted']}; background: {c['surface']};"
            f" border-color: {c['border']};"
            "}"
            "QFrame#inlineResultPane {"
            f" background: {c['surface']}; border: 1px solid {c['border']};"
            " border-radius: 11px;"
            "}"
            "QFrame#inlinePreviewHeader {"
            " background: transparent; border: none;"
            f" border-bottom: 1px solid {c['border']};"
            "}"
            "QLabel#inlinePreviewSibling {"
            f" color: {c['text']}; background: transparent; border: none;"
            " min-width: 28px; font-weight: 700;"
            "}"
            "QToolButton#inlinePreviewNavigation,"
            " QToolButton#inlinePreviewCardControl,"
            " QToolButton#inlinePreviewMode,"
            " QToolButton#inlinePreviewChrome {"
            f" color: {c['text']}; background: {c['surface_high']};"
            f" border: 1px solid {c['border']}; border-radius: 7px;"
            " min-height: 28px; font-weight: 600;"
            "}"
            "QToolButton#inlinePreviewCardControl,"
            " QToolButton#inlinePreviewMode {"
            " padding: 0 7px;"
            "}"
            "QToolButton#inlinePreviewNavigation {"
            " min-width: 26px; max-width: 26px; padding: 0;"
            "}"
            "QToolButton#inlinePreviewMode:checked {"
            f" color: {c['accent_text']}; background: {c['accent']};"
            f" border-color: {c['accent']};"
            "}"
            "QToolButton#inlinePreviewChrome {"
            " min-width: 28px; padding: 0;"
            "}"
            "QFrame#footerBar {"
            " background: transparent; border: none;"
            f" border-top: 1px solid {c['border']};"
            "}"
            "QFrame#footerBar QLabel {"
            f" color: {c['muted']}; background: transparent; border: none;"
            "}"
            "QToolButton#footerButton {"
            f" background: {c['surface_high']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 8px;"
            " min-height: 34px; padding: 0 12px; font-weight: 600;"
            "}"
            "QToolButton#footerButton:hover {"
            f" border-color: {c['accent_mid']};"
            "}"
            "QProgressBar {"
            f" background: {c['surface']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 6px;"
            " text-align: center;"
            "}"
            "QProgressBar::chunk {"
            f" background: {c['accent']}; border-radius: 5px;"
            "}"
        )

    def _set_message_presentation(self, *, semantic: bool) -> None:
        self.message_icon.setVisible(semantic)
        self.message_platform_label.setVisible(semantic)
        self.message_card.setMinimumHeight(350 if semantic else 210)

    def changeEvent(self, event: QEvent) -> None:
        if (
            event.type()
            in (
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
            )
            and not getattr(self, "_refreshing_palette", False)
        ):
            self._refreshing_palette = True
            try:
                self._apply_theme()
                # Theme palette changes reach the top-level dialog reliably,
                # but not every styled child on all Qt/Anki builds. Refresh
                # the palette-aware children here as well.
                for child_name in ("search", "segmented", "status"):
                    child = getattr(self, child_name, None)
                    if child is not None:
                        child.refresh_palette()
                if hasattr(self, "results"):
                    self.results.viewport().update()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)

    def _install_shortcuts(self) -> None:
        def sc(sequence: str, handler) -> None:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)

        sc("Ctrl+1", lambda: self.set_mode(SearchMode.SMART))
        sc("Ctrl+2", lambda: self.set_mode(SearchMode.EXACT))
        sc("Ctrl+3", lambda: self.set_mode(SearchMode.SEMANTIC))
        sc("Ctrl+L", self.focus_query)
        sc("Ctrl+Return", self._open_all_results)
        sc(_PREVIEW_SHORTCUT, self._toggle_preview_shortcut)
        if sys.platform == "darwin":
            sc("Meta+1", lambda: self.set_mode(SearchMode.SMART))
            sc("Meta+2", lambda: self.set_mode(SearchMode.EXACT))
            sc("Meta+3", lambda: self.set_mode(SearchMode.SEMANTIC))
            sc("Meta+L", self.focus_query)
            sc("Meta+Return", self._open_all_results)

    # ------------------------------------------------------------- accessors

    def query(self) -> str:
        return self.search.text()

    def show_deck_catalog(self, catalog: DeckCatalog) -> None:
        """Populate the open picker and cache its profile-scoped catalog."""

        self._deck_catalog = catalog
        self.deck_picker.set_catalog(catalog)

    def show_deck_picker_error(self, message: str) -> None:
        """Show a local picker error without interrupting normal searching."""

        if self._deck_catalog is None:
            self.deck_picker.set_error(message)
        else:
            self.deck_picker.set_refresh_error(message)

    def mode(self) -> SearchMode:
        return self.segmented.mode()

    def set_mode(self, mode: SearchMode) -> None:
        self.segmented.setMode(mode)

    def focus_query(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def move_result(self, offset: int) -> bool:
        """Move the highlighted result while preserving current focus."""

        if self.stack.currentIndex() != _PAGE_RESULTS:
            return False
        return self.results.move_current_row(offset)

    def can_move_result(self, offset: int) -> bool:
        return (
            self.stack.currentIndex() == _PAGE_RESULTS
            and self.results.can_move(offset)
        )

    def set_preview_active(self, active: bool) -> None:
        """Show or hide the inline pane without re-emitting preview intent."""

        active = bool(active)
        if active:
            if not self.preview_pane.isVisible():
                self.preview_pane.setVisible(True)
                self._restore_preview_split()
        else:
            self._remember_preview_split()
            if self.preview_pane.is_expanded():
                self._set_preview_expanded(False)
            self.preview_pane.setVisible(False)
        self.preview_button.setChecked(active)

    def set_preview_enabled(self, enabled: bool) -> None:
        """Enable or disable the card Preview feature in this dialog."""

        self._preview_enabled = bool(enabled)
        if not self._preview_enabled:
            self.set_preview_active(False)
        self._update_batch_bar()

    def _remember_preview_split(self) -> None:
        if not self.preview_pane.isVisible() or self.preview_pane.is_expanded():
            return
        sizes = self.content_splitter.sizes()
        if len(sizes) == 2 and sizes[0] > 0 and sizes[1] > 0:
            self._preview_restore_sizes = [int(sizes[0]), int(sizes[1])]

    def _restore_preview_split(self) -> None:
        self.stack.setVisible(True)
        self.preview_pane.set_expanded(False)
        left, right = self._preview_restore_sizes
        total = max(1, int(self.content_splitter.width()))
        if left <= 0 or right <= 0:
            left, right = max(420, round(total * 0.6)), max(320, round(total * 0.4))
        self.content_splitter.setSizes([left, right])

    def _set_preview_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self.preview_pane.is_expanded():
            return
        if expanded:
            self._remember_preview_split()
            self.stack.setVisible(False)
            self.preview_pane.setVisible(True)
        else:
            self.stack.setVisible(True)
            self.preview_pane.setVisible(True)
            self.content_splitter.setSizes(self._preview_restore_sizes)
        self.preview_pane.set_expanded(expanded)

    def set_preview_target_title(self, title: str) -> None:
        self.preview_pane.set_target_title(title)

    # --------------------------------------------------------- bulk actions

    def _update_batch_bar(self) -> None:
        model = self.results.results_model()
        selected_results = model.checked_results()
        selected_rows = len(selected_results)
        total_rows = model.count()
        note_count, card_count = model.checked_counts()

        if not selected_rows:
            state = Qt.CheckState.Unchecked
        elif selected_rows == total_rows:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.master_check.setCheckState(state)

        note_word = "note" if note_count == 1 else "notes"
        card_word = "card" if card_count == 1 else "cards"
        text = (
            f"{note_count} {note_word} · {card_count} {card_word} selected"
        )
        self.selection_summary.setText(text)
        self.selection_summary.setAccessibleDescription(text)
        self.select_all_action.setText(f"All {total_rows} shown")

        available = total_rows > 0 and not self._batch_busy
        has_selection = selected_rows > 0 and not self._batch_busy
        self.master_check.setEnabled(available)
        self.select_button.setEnabled(available)
        current_result = self.results.current_result()
        self.preview_button.setEnabled(
            available
            and self._preview_enabled
            and current_result is not None
            and bool(current_result.card_ids)
        )
        self.open_selected_button.setEnabled(has_selection and card_count > 0)
        self.flag_button.setEnabled(has_selection and card_count > 0)
        self.suspend_button.setEnabled(has_selection and card_count > 0)
        self.tags_button.setEnabled(has_selection and note_count > 0)
        self.batch_bar.setVisible(
            self._batch_results_available
            and self.stack.currentIndex() == _PAGE_RESULTS
            and total_rows > 0
        )

    def _toggle_all_results(self, _checked: bool = False) -> None:
        model = self.results.results_model()
        all_selected = (
            model.count() > 0
            and len(model.checked_results()) == model.count()
        )
        model.set_all_checked(not all_selected)

    def _selected_results(self):
        return self.results.results_model().checked_results()

    def _on_current_result_changed(self, result) -> None:
        self.preview_button.setEnabled(
            self._preview_enabled
            and result is not None
            and bool(result.card_ids)
            and not self._batch_busy
        )
        self.previewResultChanged.emit(result)

    def _on_result_browsed(self, result) -> None:
        """Re-emit an already-highlighted row when the user clicks it."""

        self.previewResultChanged.emit(result)

    def _toggle_preview(self, checked: bool = False) -> None:
        result = self.results.current_result()
        if checked and (
            not self._preview_enabled
            or result is None
            or not result.card_ids
        ):
            self.preview_button.setChecked(False)
            return
        self.previewToggleRequested.emit(result if checked else None)

    def _toggle_preview_shortcut(self) -> None:
        if self.preview_button.isEnabled():
            self.preview_button.click()

    def _prepare_result_context_selection(self, row: int):
        """Resolve the stable bulk-action target for a right-clicked row.

        A checked row belongs to the current bulk selection, so right-clicking
        it preserves every checked result.  An unchecked row becomes the sole
        target.  This mirrors native list context-menu behavior while keeping
        the UI's checkboxes as the single source of truth for bulk actions.
        """

        model = self.results.results_model()
        result = model.result_at(row)
        if result is None or self._batch_busy:
            return ()
        if not model.is_checked(row):
            model.set_all_checked(False)
            model.set_checked(row, True)
        self.results.select_row(row)
        return model.checked_results()

    @staticmethod
    def _context_suspension_actions(results) -> tuple[bool, bool]:
        """Return whether Suspend and Unsuspend should be offered."""

        card_ids = {
            int(card_id)
            for result in results
            for card_id in result.card_ids
            if int(card_id) > 0
        }
        states = {
            int(state.card_id): bool(state.suspended)
            for result in results
            for state in result.card_states
            if int(state.card_id) > 0
        }
        if not card_ids:
            return False, False
        # Older/stale results may not yet have live state for every card.  In
        # that case both idempotent operations are safe and avoid guessing.
        if not card_ids.issubset(states):
            return True, True
        values = {states[card_id] for card_id in card_ids}
        return False in values, True in values

    def _build_result_context_menu(self, results) -> QMenu:
        """Build the selection-aware result menu using existing batch intents."""

        menu = QMenu(self.results)
        note_ids = {
            int(result.note_id)
            for result in results
            if int(result.note_id) > 0
        }
        card_ids = {
            int(card_id)
            for result in results
            for card_id in result.card_ids
            if int(card_id) > 0
        }

        open_action = menu.addAction("Open in Browser")
        open_action.setEnabled(bool(card_ids))
        open_action.triggered.connect(
            lambda _checked=False: self._open_selected_results()
        )
        menu.addSeparator()

        flag_menu = menu.addMenu("Flag")
        flag_menu.setEnabled(bool(card_ids))
        for flag, label in (
            (0, "Clear Flag"),
            (1, "Red"),
            (2, "Orange"),
            (3, "Green"),
            (4, "Blue"),
            (5, "Pink"),
            (6, "Turquoise"),
            (7, "Purple"),
        ):
            action = flag_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, value=flag: self._emit_flag(value)
            )

        offer_suspend, offer_unsuspend = self._context_suspension_actions(
            results
        )
        if offer_suspend:
            suspend_action = menu.addAction("Suspend")
            suspend_action.triggered.connect(
                lambda _checked=False: self._emit_suspension(True)
            )
        if offer_unsuspend:
            unsuspend_action = menu.addAction("Unsuspend")
            unsuspend_action.triggered.connect(
                lambda _checked=False: self._emit_suspension(False)
            )

        tags_menu = menu.addMenu("Tags")
        tags_menu.setEnabled(bool(note_ids))
        add_tag_action = tags_menu.addAction("Add Tag…")
        add_tag_action.triggered.connect(
            lambda _checked=False: self._emit_tag_action(True)
        )
        remove_tag_action = tags_menu.addAction("Remove Tag…")
        remove_tag_action.triggered.connect(
            lambda _checked=False: self._emit_tag_action(False)
        )
        return menu

    def _show_result_context_menu(self, row: int, global_position) -> None:
        results = self._prepare_result_context_selection(row)
        if not results:
            return
        menu = self._build_result_context_menu(results)
        menu.exec(global_position)
        menu.deleteLater()

    def _open_selected_results(self) -> None:
        results = self._selected_results()
        if results and self.stack.currentIndex() == _PAGE_RESULTS:
            self.openAllRequested.emit(results)

    def _confirm_large_batch(self, action: str, count: int, target: str) -> bool:
        if count <= BATCH_CONFIRM_THRESHOLD:
            return True
        response = QMessageBox.question(
            self,
            "Confirm bulk action",
            f"{action} {count:,} selected {target}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return response == QMessageBox.StandardButton.Yes

    def _emit_flag(self, flag: int) -> None:
        results = self._selected_results()
        _notes, cards = self.results.results_model().checked_counts()
        label = "Clear flags from" if flag == 0 else "Flag"
        if results and cards and self._confirm_large_batch(label, cards, "cards"):
            self.flagRequested.emit(results, flag)

    def _emit_suspension(self, suspend: bool) -> None:
        results = self._selected_results()
        _notes, cards = self.results.results_model().checked_counts()
        label = "Suspend" if suspend else "Unsuspend"
        if results and cards and self._confirm_large_batch(label, cards, "cards"):
            self.suspensionRequested.emit(results, suspend)

    def _emit_tag_action(self, add: bool) -> None:
        results = self._selected_results()
        notes, _cards = self.results.results_model().checked_counts()
        label = "Add tags to" if add else "Remove tags from"
        if results and notes and self._confirm_large_batch(label, notes, "notes"):
            self.tagActionRequested.emit(results, add)

    def set_batch_action_busy(self, busy: bool, message: str = "") -> None:
        busy = bool(busy)
        if busy and not self._batch_busy:
            controls = (
                self.search,
                self.deck_scope,
                self.segmented,
                self.results,
                self.settings_button,
                self.rebuild_button,
                self.semantic_action,
                self.message_action,
                self.retry_button,
            )
            self._batch_control_states = [
                (control, control.isEnabled()) for control in controls
            ]
            for control, _enabled in self._batch_control_states:
                control.setEnabled(False)
        elif not busy and self._batch_busy:
            for control, enabled in self._batch_control_states:
                control.setEnabled(enabled)
            self._batch_control_states.clear()
        self._batch_busy = busy
        if message:
            self.set_summary(message)
        self._update_batch_bar()

    def apply_card_state_change(
        self,
        card_ids: Sequence[int],
        *,
        flag: Optional[int] = None,
        suspended: Optional[bool] = None,
    ) -> None:
        """Refresh wordless row indicators after a successful card operation."""

        self.results.results_model().apply_card_state_change(
            card_ids,
            flag=flag,
            suspended=suspended,
        )

    def refresh_card_states(self, results: Sequence) -> bool:
        """Apply a live metadata refresh while preserving checked rows."""

        return self.results.results_model().refresh_results(results)

    def finish_batch_action(self, success: bool, message: str) -> None:
        self.set_batch_action_busy(False)
        if success:
            self.results.results_model().set_all_checked(False)
        self.set_summary(message)
        self._update_batch_bar()

    # ------------------------------------------------------------ state API

    def _semantic_is_indexing(self) -> bool:
        semantic = self._last_status.semantic if self._last_status else None
        return (
            semantic is not None
            and semantic.state is SemanticState.INDEXING
        )

    def _hide_stale_semantic_notice(self) -> None:
        """Keep only the cross-mode notice for an active Semantic build."""

        if not self._semantic_is_indexing():
            self.index_notice.setVisible(False)

    def show_help(self) -> None:
        if self._last_status is not None:
            if self._last_status.state is not IndexState.READY:
                self.show_index_blocked(self._last_status)
                return
            semantic = self._last_status.semantic
            if (
                self.mode() is SearchMode.SEMANTIC
                and (semantic is None or not semantic.ready)
            ):
                self.show_semantic_needed(semantic)
                return
        self._message_kind = ""
        self._batch_results_available = False
        self._set_message_presentation(semantic=False)
        self._hide_stale_semantic_notice()
        self.results.setEnabled(True)
        self.stack.setCurrentIndex(_PAGE_HELP)
        self.summary.setText("")
        self.summary.setAccessibleDescription("")
        self._update_batch_bar()

    def show_debouncing(self) -> None:
        """Render an edited query that has not been dispatched yet."""

        self._message_kind = ""
        self._last_response = None
        self._batch_results_available = False
        self._hide_stale_semantic_notice()
        self.results.setEnabled(False)
        # Keep the prior list in place until replacement work actually starts.
        # Avoiding a model reset and stacked-page resize on every keystroke
        # keeps rapid edits and mode changes visually calm.
        if not self.results.results_model().count():
            self.stack.setCurrentIndex(_PAGE_HELP)
        self.message_action.setVisible(False)
        self.retry_button.setVisible(False)
        self.summary.setText("")
        self.summary.setAccessibleDescription("")
        self._update_batch_bar()

    def show_searching(self) -> None:
        self._message_kind = ""
        self._last_response = None
        self._batch_results_available = False
        self._hide_stale_semantic_notice()
        self.results.setEnabled(False)
        # Leave prior results visible but non-actionable while the worker finds
        # their replacement.  Clearing and rebuilding the view twice per
        # request caused unnecessary Qt layout/paint churn during tab changes.
        if not self.results.results_model().count():
            self.stack.setCurrentIndex(_PAGE_HELP)
        self.message_action.setVisible(False)
        self.summary.setText("Searching…")
        self.summary.setAccessibleDescription("Search in progress")
        self.retry_button.setVisible(False)
        self._update_batch_bar()

    def show_response(
        self,
        response: SearchResponse,
        corrections: Sequence[Correction],
    ) -> None:
        self._last_response = response
        self._message_kind = ""
        self._hide_stale_semantic_notice()
        self._set_message_presentation(semantic=False)
        self.results.setEnabled(True)
        self.message_action.setVisible(False)
        self.chip_bar.set_chips(response.active_filters, corrections)
        model = self.results.results_model()
        model.set_results(response.results)
        self._batch_results_available = bool(response.results)

        if response.results:
            self.stack.setCurrentIndex(_PAGE_RESULTS)
            self.results.select_row(0)
        else:
            self._message_kind = "no_results"
            self.message_label.setTextFormat(Qt.TextFormat.PlainText)
            self.message_label.setText(
                f"No results for “{response.query}”.\n"
                "Try Smart mode, fewer filters, or a broader term."
            )
            self.retry_button.setVisible(False)
            self.stack.setCurrentIndex(_PAGE_MESSAGE)

        if response.truncated:
            shown = len(response.results)
            text = (
                f"Showing the most relevant {shown} "
                f"result{'s' if shown != 1 else ''}"
            )
        else:
            text = (
                f"{response.total_results} "
                f"result{'s' if response.total_results != 1 else ''}"
            )
        if response.elapsed_ms > 0:
            text += f" · {response.elapsed_ms:.0f} ms"
        if response.warnings:
            count = len(response.warnings)
            text += f" · {count} notice{'s' if count != 1 else ''}"
            self.summary.setToolTip("\n".join(response.warnings))
        else:
            self.summary.setToolTip("")
        self.summary.setText(text)
        self.summary.setAccessibleDescription(text)
        self._update_batch_bar()

    def show_error(self, message: str) -> None:
        self.results.setEnabled(True)
        self.results.results_model().clear()
        self._batch_results_available = False
        self._message_kind = "search_error"
        self._set_message_presentation(semantic=False)
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setText(f"Search failed:\n{message}")
        self.message_action.setVisible(False)
        self.retry_button.setVisible(True)
        # A search syntax/host error should never steal the caret and prevent
        # the user from correcting the query. Retry remains reachable by Tab.
        self.search.setFocus(Qt.FocusReason.OtherFocusReason)
        self.stack.setCurrentIndex(_PAGE_MESSAGE)
        self.summary.setText("Error")
        self.summary.setAccessibleDescription(f"Search failed: {message}")
        self._update_batch_bar()

    def show_status(self, status: IndexStatus) -> None:
        self._last_status = status
        self.status.set_status(status)
        self._show_semantic_status(status.semantic)
        building = status.state is IndexState.BUILDING
        text_ready = status.state is IndexState.READY
        if status.semantic is not None and not text_ready:
            wait_text = (
                "Semantic controls will be available after Smart & Exact setup "
                "is ready."
            )
            self.semantic_status.setText(
                f"{status.semantic.summary} · waiting for Smart & Exact"
            )
            self.semantic_status.setToolTip(wait_text)
            self.semantic_action.setEnabled(False)
            self.semantic_action.setToolTip(wait_text)
            self.semantic_action.setAccessibleDescription(wait_text)
        interactive = text_ready and not self._batch_busy
        self.search.setEnabled(interactive)
        self.segmented.setEnabled(interactive)
        if text_ready:
            self.search.setPlaceholderText("Search notes, tags, decks…")
        else:
            self.search.setPlaceholderText("Waiting for initial search setup…")
        self.rebuild_button.setEnabled(not building and not self._batch_busy)
        if not building:
            self.progress.setVisible(False)
        if not text_ready:
            self.show_index_blocked(status)
            return

        semantic = status.semantic
        if semantic is not None and semantic.state is SemanticState.INDEXING:
            pct = (
                f" ({round(semantic.progress * 100)}%)"
                if semantic.progress is not None
                else ""
            )
            self.index_notice.setText(
                "<b>Semantic search is being prepared"
                f"{pct}.</b> It has a separate preparation step. "
                "<b>Smart and Exact search remain available now.</b>"
            )
            self.index_notice.setVisible(True)
        elif (
            self.mode() is SearchMode.SEMANTIC
            and (semantic is None or not semantic.ready)
        ):
            self.index_notice.setVisible(False)
        else:
            self.index_notice.setVisible(False)

        if (
            self.mode() is SearchMode.SEMANTIC
            and (semantic is None or not semantic.ready)
            and not self.query().strip()
        ):
            self.show_semantic_needed(semantic)
        elif (
            semantic is not None
            and semantic.ready
            and self._message_kind == "semantic"
        ):
            # A semantic setup/build message may be occupying the center page
            # when the periodic status refresh reports completion. Clear it
            # immediately and retry a pending query exactly once.
            query = self.query().strip()
            if query:
                self._debounce.stop()
                self.show_debouncing()
                self.stack.setCurrentIndex(_PAGE_HELP)
                self._emit_search()
            else:
                self.show_help()
        elif self._message_kind == "text_index":
            self.show_help()

    def show_index_blocked(self, status: IndexStatus) -> None:
        self._message_kind = "text_index"
        self._batch_results_available = False
        self._set_message_presentation(semantic=False)
        self.index_notice.setVisible(False)
        self.retry_button.setVisible(False)
        detail = status.detail or "Preparing your notes for fast local search."
        if status.state is IndexState.BUILDING:
            pct = (
                f" — {round(status.progress * 100)}%"
                if status.progress is not None
                else ""
            )
            heading = f"Preparing Smart & Exact Search{pct}"
            hint = (
                "Initial setup must finish before Smart and Exact searches can run."
            )
            self.message_action.setVisible(False)
            self.progress.setVisible(True)
            if status.progress is None:
                self.progress.setRange(0, 0)
            else:
                self.progress.setRange(0, 100)
                self.progress.setValue(round(status.progress * 100))
        else:
            heading = "Smart & Exact Search Need Setup"
            hint = (
                "Prepare Smart and Exact before searching. Semantic can be "
                "prepared afterward."
            )
            self.message_action.setText(
                "Retry Smart & Exact"
                if status.state is IndexState.ERROR
                else "Prepare Smart & Exact"
            )
            self.message_action.setAccessibleName(self.message_action.text())
            self.message_action.setProperty("action", "text_index")
            self.message_action.setVisible(True)
        self.message_label.setText(
            f"<b>{escape(heading)}</b><br><br>"
            f"{escape(hint)}<br>{escape(detail)}"
        )
        self.message_label.setTextFormat(Qt.TextFormat.RichText)
        self.stack.setCurrentIndex(_PAGE_MESSAGE)
        self.summary.setText(status.summary)
        self._update_batch_bar()

    def last_response(self) -> Optional[SearchResponse]:
        return self._last_response

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)
        self.summary.setAccessibleDescription(text)

    def set_result_limit(self, value: int) -> None:
        self._result_limit = min(200, max(10, int(value)))

    def show_semantic_needed(self, status: Optional[SemanticStatus]) -> None:
        self._message_kind = "semantic"
        self._batch_results_available = False
        self._set_message_presentation(semantic=True)
        self.retry_button.setVisible(False)
        self.message_action.setProperty("action", "semantic")
        self.index_notice.setText(
            "<b>Semantic has a separate one-time setup and index.</b><br>"
            "Smart and Exact remain available now—and throughout Semantic "
            "preparation."
        )
        self.index_notice.setAccessibleDescription(
            "Semantic search has a separate one-time setup. Smart and Exact "
            "remain available while it is prepared."
        )
        self.index_notice.setVisible(True)
        if status is None:
            heading = "Semantic Search Is Not Available Yet"
            detail = "Semantic status is unavailable."
            action = ""
        elif status.state is SemanticState.MODEL_READY:
            heading = "Semantic Search Needs Preparation"
            automatic = (
                " It will start automatically after startup checks; choose "
                "Prepare Now to begin immediately."
                if status.auto_start
                else ""
            )
            detail = (
                "Semantic has a separate one-time preparation and may take "
                "several minutes. You can keep using Smart or Exact while it "
                f"finishes.{automatic}"
            )
            action = (
                "Prepare Semantic Search Now"
                if status.auto_start
                else "Prepare / Resume Semantic Search"
            )
        elif status.state is SemanticState.INDEXING:
            pct = (
                f" — {round(status.progress * 100)}%"
                if status.progress is not None
                else ""
            )
            heading = f"Preparing Semantic Search{pct}"
            detail = (
                "This preparation is separate. Switch to Smart or Exact to "
                "keep searching while it finishes."
            )
            action = ""
        elif status.state is SemanticState.RESTART_REQUIRED:
            heading = "Restart Anki to Finish Semantic Search"
            detail = (
                "The repair is installed. Restart Anki once to load it; "
                "Smart and Exact remain available."
            )
            action = ""
        elif status.state is SemanticState.NOT_INSTALLED:
            heading = "Set Up Semantic Search"
            detail = (
                "Enable a private, local search by clinical meaning. Required "
                "files are downloaded only after you choose setup."
            )
            action = "Set Up Semantic Search"
        elif status.state is SemanticState.ERROR:
            heading = "Semantic Search Needs Attention"
            if status.recovery is SemanticRecovery.MODEL:
                detail = (
                    "Semantic search needs repair. Smart and Exact search "
                    "remain available."
                )
                action = "Repair Semantic Search"
            else:
                detail = (
                    "Semantic preparation did not finish. Smart and Exact "
                    "search remain available."
                )
                action = "Try Again"
        else:
            heading = status.summary
            detail = status.detail
            action = ""

        self.message_label.setText(
            '<span style="font-size: 24px; font-weight: 700;">'
            f"{escape(heading)}</span><br><br>"
            '<span style="font-size: 15px;">'
            f"{escape(detail)}</span>"
        )
        self.message_label.setTextFormat(Qt.TextFormat.RichText)
        self.message_action.setText(action)
        self.message_action.setAccessibleName(action or "Semantic search action")
        self.message_action.setVisible(bool(action))
        self.semantic_action.setVisible(False)
        self.stack.setCurrentIndex(_PAGE_MESSAGE)
        self.summary.setText("")
        self.summary.setAccessibleDescription(
            "Semantic search setup is required."
        )
        self._update_batch_bar()

    def _show_semantic_status(self, status: Optional[SemanticStatus]) -> None:
        if status is None:
            self.semantic_status.setVisible(False)
            self.semantic_action.setVisible(False)
            self.semantic_progress.setVisible(False)
            return
        self.semantic_status.setText(status.summary)
        self.semantic_status.setToolTip(
            "Semantic search runs privately on this computer."
        )
        self.semantic_status.setVisible(True)
        self.semantic_action.setToolTip("")
        self.semantic_action.setAccessibleDescription("")
        self.semantic_progress.setVisible(False)
        if status.state is SemanticState.NOT_INSTALLED:
            self.semantic_action.setText("Set Up Semantic")
            self.semantic_action.setEnabled(True)
            self.semantic_action.setVisible(True)
        elif status.state is SemanticState.ERROR:
            self.semantic_action.setText(
                "Repair Semantic"
                if status.recovery is SemanticRecovery.MODEL
                else "Try Again"
            )
            self.semantic_action.setEnabled(True)
            self.semantic_action.setVisible(True)
        elif status.state is SemanticState.MODEL_READY:
            self.semantic_action.setText(
                "Prepare Now" if status.auto_start else "Prepare Semantic"
            )
            self.semantic_action.setEnabled(True)
            self.semantic_action.setVisible(True)
        elif status.state is SemanticState.INDEXING:
            self.semantic_action.setText("Preparing…")
            self.semantic_action.setEnabled(False)
            self.semantic_action.setVisible(True)
            self.semantic_progress.setVisible(True)
            if status.progress is None:
                self.semantic_progress.setRange(0, 0)
                progress_text = "Preparing semantic search"
            else:
                self.semantic_progress.setRange(0, 100)
                self.semantic_progress.setValue(round(status.progress * 100))
                progress_text = (
                    f"Preparing semantic search {round(status.progress * 100)}%"
                )
            self.semantic_progress.setAccessibleDescription(progress_text)
            self.semantic_progress.setToolTip(
                progress_text
                + ". Smart and Exact search remain available."
            )
        elif status.state is SemanticState.RESTART_REQUIRED:
            self.semantic_action.setVisible(False)
        else:
            self.semantic_action.setVisible(False)
        if self._batch_busy:
            self.semantic_action.setEnabled(False)

    def _run_semantic_action(self) -> None:
        semantic = self._last_status.semantic if self._last_status else None
        if semantic is None:
            return
        if semantic.state is SemanticState.MODEL_READY:
            self.semanticIndexRequested.emit()
        elif semantic.state is SemanticState.ERROR:
            if semantic.recovery is SemanticRecovery.MODEL:
                self.semanticInstallRequested.emit()
            else:
                self.semanticIndexRequested.emit()
        elif semantic.state is SemanticState.NOT_INSTALLED:
            self.semanticInstallRequested.emit()

    def _run_message_action(self) -> None:
        action = self.message_action.property("action")
        if action == "text_index":
            self.rebuildRequested.emit()
        elif action == "semantic":
            self._run_semantic_action()

    def show_rebuild_progress(self, fraction: float, detail: str) -> None:
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(round(fraction * 100))
        pct = round(fraction * 100)
        text = (
            f"Refreshing Smart & Exact {pct}%"
            + (f" — {detail}" if detail else "")
        )
        self.summary.setText(text)
        self.summary.setAccessibleDescription(text)

    def clear_chips(self) -> None:
        self.chip_bar.set_chips([], [])

    def set_query(self, text: str) -> None:
        self.search.setText(text)
        self.deck_scope.set_scope(text)
        self.focus_query()
        self._emit_search()

    # ------------------------------------------------------------- behavior

    def _on_text_edited(self, text: str) -> None:
        self.deck_scope.set_scope(text)
        query = str(text).strip()
        self.queryEdited.emit(query)
        if not query:
            self._debounce.stop()
            self.results.results_model().clear()
            self.clear_chips()
            self.show_help()
            return
        if self._last_status is not None:
            if self._last_status.state is not IndexState.READY:
                self._debounce.stop()
                self.show_index_blocked(self._last_status)
                return
            semantic = self._last_status.semantic
            if (
                self.mode() is SearchMode.SEMANTIC
                and (semantic is None or not semantic.ready)
            ):
                self._debounce.stop()
                self.show_semantic_needed(semantic)
                return
        self.show_debouncing()
        self._debounce.start()

    def _open_deck_picker(self) -> None:
        """Open immediately, then refresh choices asynchronously."""

        self.deck_picker.set_query(self.query())
        if self._deck_catalog is None:
            self.deck_picker.set_loading()
        else:
            self.deck_picker.set_catalog(self._deck_catalog)
        self.deck_picker.open_anchored(self.deck_scope)
        self.deckPickerRequested.emit()

    def _reload_deck_picker(self) -> None:
        self.deck_picker.set_loading()
        self.deckPickerRequested.emit()

    def _apply_deck_selection(
        self,
        selection: object,
    ) -> None:
        included = getattr(selection, "included", None)
        if included is None:
            included, excluded = selection, ()
        else:
            excluded = getattr(selection, "excluded", ())
        try:
            query = apply_deck_selection(
                self.query(),
                included,
                excluded,
            )
        except ValueError as error:
            # Unsafe Boolean deck expressions must be edited directly; keep
            # the visible query intact instead of guessing at its meaning.
            self.deck_picker.show_validation_error(str(error))
            return
        self.deck_picker.accept()
        self.deck_scope.set_scope(query)
        self.set_query(query)

    def _emit_search(self) -> None:
        query = self.query().strip()
        if not query:
            self._debounce.stop()
            self.results.results_model().clear()
            self.clear_chips()
            self.show_help()
            return
        self.searchRequested.emit(query)

    def _on_mode_changed(self, mode: SearchMode) -> None:
        # A mode change dispatches immediately through the controller.  Do
        # not let a pending text-edit debounce submit the same query again.
        self._debounce.stop()
        if mode is not SearchMode.SEMANTIC:
            self._hide_stale_semantic_notice()
        if self._last_status is not None:
            semantic = self._last_status.semantic
            if mode is SearchMode.SEMANTIC and (
                semantic is None or not semantic.ready
            ):
                self.show_semantic_needed(semantic)
            elif self._message_kind == "semantic":
                if not self.query().strip():
                    self.show_help()
                if semantic is not None:
                    self._show_semantic_status(semantic)
        self.modeSelected.emit(mode)

    def _on_search_return(self) -> None:
        """Return in the search field commits the current query immediately."""

        self._debounce.stop()
        self._emit_search()

    def _on_index_activated(self, index) -> None:
        result = self.results.results_model().result_at(index.row())
        if result is not None:
            self.openRequested.emit(result)

    def _open_all_results(self) -> None:
        model = self.results.results_model()
        results = model.checked_results() or model.results()
        if results and self.stack.currentIndex() == _PAGE_RESULTS:
            self.openAllRequested.emit(results)

    def _open_settings(self) -> None:
        semantic = self._last_status.semantic if self._last_status else None
        text_index_ready = (
            self._last_status is not None
            and self._last_status.state is IndexState.READY
            and not self.progress.isVisible()
        )
        dialog = _SettingsDialog(
            self.mode(),
            self._result_limit,
            semantic,
            self,
            preview_enabled=self._preview_enabled,
            text_index_ready=text_index_ready,
            about=self._about,
        )
        dialog.semanticInstallRequested.connect(self.semanticInstallRequested)
        dialog.semanticIndexRequested.connect(self.semanticIndexRequested)
        update_requested = False

        def remember_update_request() -> None:
            nonlocal update_requested
            update_requested = True

        dialog.updateRequested.connect(remember_update_request)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            mode, limit, preview_enabled = dialog.values()
            self.settingsChanged.emit(mode, limit, preview_enabled)
            if update_requested:
                self.updateRequested.emit()
        self.focus_query()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if obj is self.search:
                if key == Qt.Key.Key_Down:
                    if self.results.results_model().count():
                        self.stack.setCurrentIndex(_PAGE_RESULTS)
                        self.results.setFocus(Qt.FocusReason.ShortcutFocusReason)
                        self.results.select_row(0)
                        current = self.results.current_result()
                        if current is not None:
                            self.previewResultChanged.emit(current)
                    return True
            elif obj is self.results:
                if key == Qt.Key.Key_Up and self.results.currentIndex().row() <= 0:
                    self.focus_query()
                    return True
                result = self.results.current_result()
                preview_key = key in {
                    Qt.Key.Key_Space,
                    Qt.Key.Key_Right,
                    Qt.Key.Key_Left,
                }
                if (
                    preview_key
                    and event.modifiers() == Qt.KeyboardModifier.NoModifier
                    and self._preview_enabled
                    and result is not None
                    and bool(result.card_ids)
                ):
                    # A held key must not repeatedly schedule renders or
                    # restart answer audio. Right/Space are deliberately
                    # one-way reveals; Left is the explicit way back.
                    if not event.isAutoRepeat():
                        side = (
                            "question"
                            if key == Qt.Key.Key_Left
                            else "answer"
                        )
                        self.previewSideRequested.emit(result, side)
                    event.accept()
                    return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        """Keep the two-step Escape behavior without hijacking window close."""

        if event.key() == Qt.Key.Key_Escape and self.query():
            self.search.clear()
            self._emit_search()
            self.focus_query()
            event.accept()
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        self.close()

    def set_managed_close_handler(
        self,
        handler: Callable[[Callable[[], None]], None] | None,
    ) -> None:
        """Install Anki's asynchronous dialog-manager close handshake."""

        self._managed_close_handler = handler

    def closeWithCallback(self, callback: Callable[[], None]) -> None:  # noqa: N802
        handler = self._managed_close_handler
        if handler is None:
            self.close()
            callback()
            return
        handler(callback)

    def closeEvent(self, event) -> None:
        handler = self._managed_close_handler
        if handler is not None:
            event.ignore()
            if not self._managed_close_requested:
                self._managed_close_requested = True
                try:
                    handler(lambda: None)
                except Exception:
                    self._managed_close_requested = False
            return
        self._prepare_close()
        # QDialog.closeEvent() delegates to the virtual reject() method.  Our
        # reject() deliberately routes Escape through close() so every close
        # path emits dialogClosed; calling the base implementation here would
        # therefore recurse into close() while this close event is active and
        # can leave the dialog visible.  Accepting the QWidget close event is
        # sufficient for close() to hide the dialog.
        event.accept()

    def _close(self) -> None:
        self.close()

    def _prepare_close(self) -> None:
        if self._close_notified:
            return
        self._close_notified = True
        self._debounce.stop()
        if self.deck_picker.isVisible():
            self.deck_picker.reject()
        self.dialogClosed.emit()

    def showEvent(self, event) -> None:
        self._close_notified = False
        self._managed_close_requested = False
        super().showEvent(event)
        self.search.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
