"""Compact, keyboard-friendly deck scope controls for Smart Search.

The picker is deliberately host-agnostic.  The Anki integration supplies an
immutable :class:`DeckCatalog`; this module never reads the collection and
never rewrites the query itself.  Applying a staged selection emits one intent
for the controller, which keeps the visible Anki query as the source of truth.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Optional

from .contracts import DeckCatalog, DeckEntry
from .deck_query import DeckScopeSelection, analyze_deck_query
from .theme import chrome_colors
from .widgets import (
    QApplication,
    QAbstractItemView,
    QBrush,
    QColor,
    QDialog,
    QEvent,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    Qt,
    QVBoxLayout,
    QWidget,
    PaletteMixin,
    pyqtSignal,
)


_ROLE_NAME = int(Qt.ItemDataRole.UserRole)
_ROLE_REAL = _ROLE_NAME + 1
_ROLE_INHERITED = _ROLE_NAME + 2
_ROLE_MISSING = _ROLE_NAME + 3


def _read(value: object, *names: str, default: Any = None) -> Any:
    """Read a small immutable contract while tolerating namedtuple variants."""

    for name in names:
        if hasattr(value, name):
            candidate = getattr(value, name)
            return candidate() if callable(candidate) else candidate
    return default


def _scope_name(analysis: object | None) -> str:
    kind = _read(analysis, "kind", "scope", "scope_kind", default="all")
    value = getattr(kind, "value", kind)
    return str(value or "all").casefold()


def _selected_from_analysis(analysis: object | None) -> tuple[str, ...]:
    values = _read(
        analysis,
        "names",
        "selected_names",
        "deck_names",
        "decks",
        "names",
        default=(),
    )
    if isinstance(values, str):
        values = (values,)
    return _unique_names(values or ())


def _unique_names(values: Iterable[object]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value or "").strip()
        identity = _name_key(name)
        if name and identity not in seen:
            output.append(name)
            seen.add(identity)
    return tuple(output)


def _excluded_from_analysis(analysis: object | None) -> tuple[str, ...]:
    values = _read(analysis, "excluded", "excluded_names", default=())
    if isinstance(values, str):
        values = (values,)
    return _unique_names(values or ())


def _name_key(name: str) -> str:
    return name.lower() if name.isascii() else name


def _is_custom(analysis: object | None) -> bool:
    name = _scope_name(analysis)
    return name in {"custom", "advanced"} or "custom" in name


def _entry_name(entry: DeckEntry | object) -> str:
    return str(_read(entry, "name", "full_name", default="") or "").strip()


def _entry_id(entry: DeckEntry | object) -> int:
    try:
        return int(_read(entry, "deck_id", "id", default=0) or 0)
    except (TypeError, ValueError):
        return 0


def _catalog_entries(catalog: DeckCatalog | object | None) -> tuple[object, ...]:
    if catalog is None:
        return ()
    entries = _read(catalog, "entries", "decks", default=())
    return tuple(entries or ())


def _catalog_current_name(catalog: DeckCatalog | object | None) -> str:
    if catalog is None:
        return ""
    direct = str(
        _read(catalog, "current_deck_name", "current_name", default="") or ""
    ).strip()
    if direct:
        return direct
    current = _read(catalog, "current_deck", "current", default=None)
    if current is not None:
        direct = _entry_name(current)
        if direct:
            return direct
    try:
        current_id = int(
            _read(catalog, "current_deck_id", "current_id", default=0) or 0
        )
    except (TypeError, ValueError):
        current_id = 0
    if current_id:
        for entry in _catalog_entries(catalog):
            if _entry_id(entry) == current_id:
                return _entry_name(entry)
    return ""


class DeckScopeButton(QToolButton, PaletteMixin):
    """Fixed-width summary button for the deck clause in a visible query."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("deckScopeButton")
        self.setMinimumHeight(48)
        self.setMinimumWidth(132)
        self.setMaximumWidth(148)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._analysis: object | None = None
        self._query = ""
        self.set_scope("")
        self.refresh_palette()

    @property
    def analysis(self) -> object | None:
        return self._analysis

    def set_scope(self, query: str) -> None:
        self._query = str(query or "")
        self._analysis = analyze_deck_query(self._query)
        names = _selected_from_analysis(self._analysis)
        excluded = _excluded_from_analysis(self._analysis)
        custom = _is_custom(self._analysis)
        if custom:
            label = "Custom decks"
            summary = "custom deck expression"
        elif len(names) > 1:
            label = f"{len(names)} decks"
            summary = f"{len(names)} decks selected"
        elif names:
            label = names[0].rsplit("::", 1)[-1]
            summary = names[0]
        else:
            label = "All decks"
            summary = "all decks"
        if excluded and not custom:
            count = len(excluded)
            label = f"{label} −{count}"
            summary = (
                f"{summary}, {count} "
                f"subdeck{'s' if count != 1 else ''} excluded"
            )
        # Keep the compound search row stable even for very long deck names.
        metrics = self.fontMetrics()
        label = metrics.elidedText(label, Qt.TextElideMode.ElideRight, 105)
        self.setText(f"{label}  ▾")
        if not names:
            tooltip = "Choose decks"
        else:
            tooltip = "Search " + ", ".join(names)
            if excluded:
                tooltip += "\nExcluding " + ", ".join(excluded)
        self.setToolTip(tooltip)
        self.setAccessibleName(f"Deck filter, {summary}")
        self.setAccessibleDescription(
            "Open a searchable deck picker. Selecting a parent includes its "
            "subdecks; unchecking an included subdeck excludes it."
        )
        self.setProperty("customScope", custom)
        self.refresh_palette()

    def refresh_palette(self) -> None:
        c = chrome_colors(self)
        self.setStyleSheet(
            "QToolButton#deckScopeButton {"
            f" background: {c['surface']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-right: none;"
            " border-top-left-radius: 11px; border-bottom-left-radius: 11px;"
            " border-top-right-radius: 0; border-bottom-right-radius: 0;"
            " padding: 0 10px; font-weight: 600;"
            "}"
            "QToolButton#deckScopeButton:hover {"
            f" background: {c['surface_high']}; border-color: {c['accent_mid']};"
            "}"
            "QToolButton#deckScopeButton:focus {"
            f" border: 1px solid {c['accent']}; border-right: none;"
            "}"
            "QToolButton#deckScopeButton[customScope=\"true\"] {"
            f" background: {c['callout_bg']}; border-color: {c['callout_border']};"
            "}"
        )

    def changeEvent(self, event: QEvent) -> None:
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ) and not getattr(self, "_refreshing_palette", False):
            self._refreshing_palette = True
            try:
                self.refresh_palette()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)


class DeckPickerPopup(QDialog):
    """Modeless searchable hierarchical deck selector anchored to a button."""

    applied = pyqtSignal(object)  # DeckScopeSelection
    retryRequested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        super().__init__(parent, flags)
        self.setObjectName("deckPickerPopup")
        self.setWindowTitle("Choose decks")
        self.setAccessibleName("Choose decks")
        self.setAccessibleDescription(
            "Search and select one or more decks. Selecting a parent includes "
            "all subdecks; unchecking an included subdeck excludes it."
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(320)
        self.resize(430, 540)

        self._query = ""
        self._analysis: object | None = None
        self._catalog: DeckCatalog | object | None = None
        self._selected: set[str] = set()
        self._excluded: set[str] = set()
        self._items: dict[str, QTreeWidgetItem] = {}
        self._updating = False
        self._ready = False
        self._anchor: QToolButton | None = None
        self._filter_active = False
        self._expanded_before_filter: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.surface = QFrame(self)
        self.surface.setObjectName("deckPickerSurface")
        root.addWidget(self.surface)
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        heading = QHBoxLayout()
        heading.setContentsMargins(2, 0, 2, 0)
        self.title_label = QLabel("Choose decks", self.surface)
        self.title_label.setObjectName("deckPickerTitle")
        self.title_label.setAccessibleName("Choose decks")
        heading.addWidget(self.title_label)
        heading.addStretch(1)
        self.clear_button = QToolButton(self.surface)
        self.clear_button.setObjectName("deckPickerClear")
        self.clear_button.setText("Clear")
        self.clear_button.setToolTip("Search all decks")
        self.clear_button.setAccessibleName("Clear selected decks")
        self.clear_button.clicked.connect(self._choose_all)
        heading.addWidget(self.clear_button)
        layout.addLayout(heading)

        self.filter_edit = QLineEdit(self.surface)
        self.filter_edit.setObjectName("deckPickerFilter")
        self.filter_edit.setPlaceholderText("Find a deck…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMinimumHeight(38)
        self.filter_edit.setAccessibleName("Filter deck list")
        self.filter_edit.textChanged.connect(self._filter_tree)
        self.filter_edit.installEventFilter(self)
        layout.addWidget(self.filter_edit)

        quick = QVBoxLayout()
        quick.setContentsMargins(0, 0, 0, 0)
        quick.setSpacing(7)
        self.all_button = QToolButton(self.surface)
        self.all_button.setObjectName("deckPickerQuick")
        self.all_button.setText("All decks")
        self.all_button.setCheckable(True)
        self.all_button.setAccessibleName("Search all decks")
        self.all_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.all_button.clicked.connect(self._choose_all)
        quick.addWidget(self.all_button)
        self.current_button = QToolButton(self.surface)
        self.current_button.setObjectName("deckPickerQuick")
        self.current_button.setText("Current")
        self.current_button.setCheckable(True)
        self.current_button.setAccessibleName("Search current deck")
        self.current_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.current_button.clicked.connect(self._choose_current)
        quick.addWidget(self.current_button)
        layout.addLayout(quick)

        self.decks_label = QLabel("YOUR DECKS", self.surface)
        self.decks_label.setObjectName("deckPickerSection")
        self.decks_label.setAccessibleName("Your decks")
        layout.addWidget(self.decks_label)

        self.custom_frame = QFrame(self.surface)
        self.custom_frame.setObjectName("deckPickerCustom")
        custom_layout = QVBoxLayout(self.custom_frame)
        custom_layout.setContentsMargins(10, 8, 10, 8)
        custom_layout.setSpacing(6)
        self.custom_label = QLabel(
            "Advanced deck syntax is active. Edit this deck logic directly in the search box.",
            self.custom_frame,
        )
        self.custom_label.setWordWrap(True)
        self.custom_label.setAccessibleName("Custom deck filter notice")
        custom_layout.addWidget(self.custom_label)
        self.custom_frame.hide()
        layout.addWidget(self.custom_frame)

        self.message_label = QLabel(self.surface)
        self.message_label.setObjectName("deckPickerMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setAccessibleName("Deck picker status")
        self.message_label.hide()
        layout.addWidget(self.message_label)

        self.tree = QTreeWidget(self.surface)
        self.tree.setObjectName("deckPickerTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setAccessibleName("Decks")
        self.tree.setAccessibleDescription(
            "Use arrows to navigate, Space to toggle a deck, and Return to apply. "
            "A checked parent includes every subdeck; unchecking an included "
            "subdeck excludes it."
        )
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.installEventFilter(self)
        layout.addWidget(self.tree, 1)

        self.no_matches_label = QLabel(self.surface)
        self.no_matches_label.setObjectName("deckPickerNoMatches")
        self.no_matches_label.setWordWrap(True)
        self.no_matches_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_matches_label.setAccessibleName("Deck filter results")
        self.no_matches_label.hide()
        layout.addWidget(self.no_matches_label)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self.selection_label = QLabel("All decks", self.surface)
        self.selection_label.setObjectName("deckPickerSelection")
        self.selection_label.setAccessibleName("Deck selection count")
        footer.addWidget(self.selection_label)
        footer.addStretch(1)
        self.retry_button = QPushButton("Retry", self.surface)
        self.retry_button.setObjectName("deckPickerSecondary")
        self.retry_button.clicked.connect(self.retryRequested)
        self.retry_button.hide()
        footer.addWidget(self.retry_button)
        self.cancel_button = QPushButton("Cancel", self.surface)
        self.cancel_button.setObjectName("deckPickerSecondary")
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.apply_button = QPushButton("Apply", self.surface)
        self.apply_button.setObjectName("deckPickerApply")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)

        QWidget.setTabOrder(self.filter_edit, self.clear_button)
        QWidget.setTabOrder(self.clear_button, self.all_button)
        QWidget.setTabOrder(self.all_button, self.current_button)
        QWidget.setTabOrder(self.current_button, self.tree)
        QWidget.setTabOrder(self.tree, self.retry_button)
        QWidget.setTabOrder(self.retry_button, self.cancel_button)
        QWidget.setTabOrder(self.cancel_button, self.apply_button)
        self._apply_theme()
        self.set_loading()

    @property
    def analysis(self) -> object | None:
        return self._analysis

    @property
    def selected_names(self) -> tuple[str, ...]:
        return self._ordered_selection()

    @property
    def selection(self) -> DeckScopeSelection:
        return DeckScopeSelection(
            self._ordered_selection(),
            self._ordered_exclusions(),
        )

    def set_query(self, query: str) -> None:
        self._query = str(query or "")
        self._analysis = analyze_deck_query(self._query)
        self._selected = set(_selected_from_analysis(self._analysis))
        self._excluded = set(_excluded_from_analysis(self._analysis))
        custom = _is_custom(self._analysis)
        reason = str(_read(self._analysis, "reason", default="") or "").strip()
        self.custom_frame.setVisible(custom)
        if custom:
            suffix = "Edit this deck logic directly in the search box."
            self.custom_label.setText(
                " ".join(part for part in (reason, suffix) if part)
            )
        self._set_picker_controls_enabled(self._ready and not custom)
        self._refresh_checks()

    def set_catalog(self, catalog: DeckCatalog) -> None:
        self._catalog = catalog
        self._ready = True
        self.message_label.hide()
        self.message_label.setToolTip("")
        self.message_label.setProperty("validationError", False)
        self.retry_button.hide()
        self.tree.show()
        self._rebuild_tree()
        custom = _is_custom(self._analysis)
        self._set_picker_controls_enabled(not custom)
        self._update_current_button()
        self._update_summary()

    def set_loading(self) -> None:
        self._ready = False
        self.tree.clear()
        self._items.clear()
        self.tree.hide()
        self.message_label.setText("Loading decks…")
        self.message_label.setToolTip("")
        self.message_label.setProperty("validationError", False)
        self.message_label.show()
        self.no_matches_label.hide()
        self.retry_button.hide()
        self._set_picker_controls_enabled(False)
        self.apply_button.setEnabled(False)
        self._apply_theme()

    def set_error(self, message: str) -> None:
        self._ready = False
        self.tree.hide()
        self.message_label.setText(str(message or "Decks could not be loaded."))
        self.message_label.setToolTip("")
        self.message_label.setProperty("validationError", True)
        self.message_label.show()
        self.no_matches_label.hide()
        self.retry_button.show()
        self._set_picker_controls_enabled(False)
        self.apply_button.setEnabled(False)
        self._apply_theme()

    def set_refresh_error(self, message: str) -> None:
        """Keep cached deck choices usable when a background refresh fails."""

        if self._catalog is None:
            self.set_error(message)
            return
        self._ready = True
        self.tree.show()
        self.message_label.setText(
            "Deck choices could not be refreshed. Showing the last loaded list."
        )
        self.message_label.setToolTip(str(message or ""))
        self.message_label.setProperty("validationError", True)
        self.message_label.show()
        self.no_matches_label.hide()
        self.retry_button.show()
        custom = _is_custom(self._analysis)
        self._set_picker_controls_enabled(not custom)
        self._update_current_button()
        self._apply_theme()

    def show_validation_error(self, message: str) -> None:
        """Keep staged choices open when query syntax cannot be rewritten."""

        self.message_label.setText(
            str(message or "Edit the deck filter in the search box.")
        )
        self.message_label.setToolTip("")
        self.message_label.setProperty("validationError", True)
        self.message_label.show()
        self.retry_button.hide()
        self._apply_theme()

    def open_anchored(self, button: QToolButton) -> None:
        self._anchor = button
        self.filter_edit.clear()
        # Each open is a fresh staged copy of the visible query.
        self.set_query(self._query)
        anchor = button.mapToGlobal(button.rect().bottomLeft())
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(
                min(max(390, self.width()), max(320, available.width() - 16)),
                min(max(460, self.height()), max(320, available.height() - 16)),
            )
            x = max(
                available.left() + 8,
                min(anchor.x(), available.right() - self.width() - 8),
            )
            below = anchor.y() + 6
            if below + self.height() <= available.bottom() - 8:
                y = below
            else:
                above = button.mapToGlobal(button.rect().topLeft()).y()
                y = max(available.top() + 8, above - self.height() - 6)
            self.move(x, y)
        else:
            self.resize(max(390, self.width()), max(460, self.height()))
            self.move(anchor)
        self.show()
        self.raise_()
        self.filter_edit.setFocus(Qt.FocusReason.PopupFocusReason)

    def reject(self) -> None:
        super().reject()
        anchor = self._anchor
        if anchor is not None:
            try:
                anchor.setFocus(Qt.FocusReason.PopupFocusReason)
            except RuntimeError:
                pass

    # --------------------------------------------------------------- contents

    def _rebuild_tree(self) -> None:
        self._updating = True
        try:
            self.tree.clear()
            self._items.clear()
            paths: dict[str, QTreeWidgetItem] = {}
            real_names = {
                _entry_name(entry)
                for entry in _catalog_entries(self._catalog)
                if _entry_name(entry)
            }
            for full_name in sorted(real_names, key=str.casefold):
                parent: QTreeWidgetItem | None = None
                segments = full_name.split("::")
                for index, segment in enumerate(segments):
                    path = "::".join(segments[: index + 1])
                    item = paths.get(path)
                    if item is None:
                        item = QTreeWidgetItem(parent or self.tree)
                        item.setText(0, segment)
                        item.setData(0, _ROLE_NAME, path)
                        paths[path] = item
                    parent = item
            self._items = paths
            for name, item in self._items.items():
                real = name in real_names
                item.setData(0, _ROLE_REAL, real)
                item.setToolTip(0, name)
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if real:
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                item.setFlags(flags)
                item.setData(
                    0,
                    Qt.ItemDataRole.AccessibleTextRole,
                    f"Deck {name}",
                )

            catalog_names = {_name_key(name) for name in real_names}
            missing_selected = tuple(
                name
                for name in self._selected
                if _name_key(name) not in catalog_names
            )
            missing_excluded = tuple(
                name
                for name in self._excluded
                if _name_key(name) not in catalog_names
            )
            missing = _unique_names((*missing_selected, *missing_excluded))
            if missing:
                unavailable = QTreeWidgetItem(self.tree)
                unavailable.setText(0, "Unavailable decks")
                unavailable.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                unavailable.setData(
                    0,
                    Qt.ItemDataRole.AccessibleTextRole,
                    "Unavailable decks from the current query",
                )
                for name in sorted(missing, key=str.casefold):
                    item = QTreeWidgetItem(unavailable)
                    item.setText(0, f"{name.rsplit('::', 1)[-1]} (unavailable)")
                    item.setData(0, _ROLE_NAME, name)
                    item.setData(0, _ROLE_REAL, True)
                    item.setData(0, _ROLE_MISSING, True)
                    item.setToolTip(
                        0,
                        f"{name}\nThis deck is in the query but is not in the current profile.",
                    )
                    self._items[name] = item
            self.tree.collapseAll()
            if missing:
                unavailable.setExpanded(True)
            self._expand_relevant_paths()
        finally:
            self._updating = False
        self._refresh_checks()
        self._filter_tree(self.filter_edit.text())

    def _expand_relevant_paths(self) -> None:
        targets = set(self._selected) | set(self._excluded)
        current = _catalog_current_name(self._catalog)
        if current:
            targets.add(current)
        for target in targets:
            parts = target.split("::")
            for index in range(1, len(parts)):
                ancestor = self._items.get("::".join(parts[:index]))
                if ancestor is not None:
                    ancestor.setExpanded(True)

    def _refresh_checks(self) -> None:
        if not self._items:
            self._update_summary()
            return
        self._updating = True
        try:
            selected_folded = {_name_key(name): name for name in self._selected}
            excluded_folded = {_name_key(name): name for name in self._excluded}
            colors = chrome_colors(self)
            for name, item in self._items.items():
                folded = _name_key(name)
                explicit = folded in selected_folded
                exclusion = folded in excluded_folded
                ancestor = self._selected_ancestor(name)
                inherited = bool(ancestor and not explicit)
                blocked_by = self._excluded_ancestor(name)
                missing = bool(item.data(0, _ROLE_MISSING))
                descendants = any(
                    _name_key(chosen).startswith(folded + "::")
                    for chosen in self._selected
                )
                exclusions_beneath = any(
                    key.startswith(folded + "::") for key in excluded_folded
                )
                real = bool(item.data(0, _ROLE_REAL))
                if exclusion or blocked_by:
                    state = Qt.CheckState.Unchecked
                elif explicit or inherited:
                    state = (
                        Qt.CheckState.PartiallyChecked
                        if exclusions_beneath
                        else Qt.CheckState.Checked
                    )
                elif descendants:
                    state = Qt.CheckState.PartiallyChecked
                else:
                    state = Qt.CheckState.Unchecked
                item.setCheckState(0, state)
                item.setData(0, _ROLE_INHERITED, inherited)
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if real and not blocked_by:
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                item.setFlags(flags)
                item.setForeground(
                    0,
                    QBrush(
                        QColor(
                            colors["muted"]
                            if missing or blocked_by
                            else colors["text"]
                        )
                    ),
                )
                leaf = name.rsplit("::", 1)[-1]
                if missing and exclusion:
                    item.setText(0, f"{leaf} (excluded, unavailable)")
                elif missing:
                    item.setText(0, f"{leaf} (unavailable)")
                elif exclusion or blocked_by:
                    item.setText(0, f"{leaf}  ·  excluded")
                elif inherited:
                    item.setText(0, f"{leaf}  ·  included")
                else:
                    item.setText(0, leaf)
                if missing and exclusion:
                    detail = f"Deck {name}, excluded but unavailable"
                elif missing and explicit:
                    detail = f"Deck {name}, selected but unavailable"
                elif missing:
                    detail = f"Deck {name}, unavailable"
                elif blocked_by:
                    detail = (
                        f"Deck {name}, excluded because {blocked_by} is excluded"
                    )
                elif exclusion:
                    detail = f"Deck {name}, excluded"
                elif explicit and exclusions_beneath:
                    detail = f"Deck {name}, selected, some subdecks excluded"
                elif explicit:
                    detail = f"Deck {name}, selected"
                elif inherited and exclusions_beneath:
                    detail = (
                        f"Deck {name}, included by {ancestor}, "
                        "some subdecks excluded"
                    )
                elif inherited:
                    detail = f"Deck {name}, included by {ancestor}"
                elif descendants:
                    detail = f"Deck {name}, some subdecks selected"
                else:
                    detail = f"Deck {name}, not selected"
                item.setData(0, Qt.ItemDataRole.AccessibleTextRole, detail)
                item.setData(
                    0,
                    Qt.ItemDataRole.AccessibleDescriptionRole,
                    (
                        "This subdeck stays excluded while its ancestor is "
                        "excluded. Check the excluded ancestor to restore it."
                    )
                    if blocked_by
                    else (
                        "Selecting this parent includes all of its subdecks; "
                        "unchecking an included subdeck excludes it."
                        if item.childCount()
                        else detail
                    ),
                )
        finally:
            self._updating = False
        self._update_quick_buttons()
        self._update_summary()

    def _excluded_ancestor(self, name: str) -> str:
        folded = _name_key(name)
        ancestors = [
            chosen
            for chosen in self._excluded
            if folded.startswith(_name_key(chosen) + "::")
        ]
        return max(ancestors, key=len, default="")

    def _selected_ancestor(self, name: str) -> str:
        folded = _name_key(name)
        ancestors = [
            chosen
            for chosen in self._selected
            if folded.startswith(_name_key(chosen) + "::")
        ]
        return max(ancestors, key=len, default="")

    def _item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._updating or not self._selection_controls_enabled():
            return
        name = str(item.data(0, _ROLE_NAME) or "")
        if not name or not item.data(0, _ROLE_REAL):
            return
        folded = _name_key(name)
        if item.checkState(0) == Qt.CheckState.Checked:
            if any(_name_key(excluded) == folded for excluded in self._excluded):
                # Re-checking an excluded subdeck restores the whole branch.
                self._excluded = {
                    excluded
                    for excluded in self._excluded
                    if _name_key(excluded) != folded
                }
            elif not self._excluded_ancestor(name):
                # Checking an included node restores exclusions beneath it.
                self._excluded = {
                    excluded
                    for excluded in self._excluded
                    if not _name_key(excluded).startswith(folded + "::")
                }
                if not self._selected_ancestor(name):
                    self._selected = {
                        selected
                        for selected in self._selected
                        if not _name_key(selected).startswith(folded + "::")
                    }
                    self._selected.add(name)
        else:
            if any(_name_key(selected) == folded for selected in self._selected):
                # Unchecking a selected root removes the whole scope.
                self._selected = {
                    selected
                    for selected in self._selected
                    if _name_key(selected) != folded
                }
                self._excluded = {
                    excluded
                    for excluded in self._excluded
                    if not _name_key(excluded).startswith(folded + "::")
                }
            elif self._selected_ancestor(name):
                # Unchecking an inherited subdeck excludes it explicitly.
                self._excluded = {
                    excluded
                    for excluded in self._excluded
                    if not _name_key(excluded).startswith(folded + "::")
                }
                self._excluded.add(name)
        self._refresh_checks()

    def _choose_all(self, _checked: bool = False) -> None:
        if not self._selection_controls_enabled():
            return
        self._selected.clear()
        self._excluded.clear()
        self._refresh_checks()

    def _choose_current(self, _checked: bool = False) -> None:
        if not self._selection_controls_enabled():
            return
        current = _catalog_current_name(self._catalog)
        if current:
            self._selected = {current}
            self._excluded.clear()
        self._refresh_checks()

    def _update_current_button(self) -> None:
        current = _catalog_current_name(self._catalog)
        leaf = current.rsplit("::", 1)[-1] if current else ""
        self.current_button.setText(
            f"Current deck  ·  {leaf}" if leaf else "Current deck"
        )
        self.current_button.setEnabled(
            self._selection_controls_enabled() and bool(current)
        )
        self.current_button.setToolTip(
            f"Use {current} and its subdecks"
            if current
            else "No current deck is available"
        )
        self.current_button.setAccessibleDescription(self.current_button.toolTip())
        self._update_quick_buttons()

    def _update_quick_buttons(self) -> None:
        current = _catalog_current_name(self._catalog)
        custom = _is_custom(self._analysis)
        self._updating = True
        try:
            self.all_button.setChecked(not custom and not self._selected)
            self.current_button.setChecked(
                not custom
                and bool(current)
                and len(self._selected) == 1
                and _name_key(next(iter(self._selected))) == _name_key(current)
            )
        finally:
            self._updating = False

    def _update_summary(self) -> None:
        count = len(self._selected)
        excluded = len(self._excluded)
        if _is_custom(self._analysis):
            text = "Custom deck filter"
        elif not count:
            text = "All decks"
        else:
            text = f"{count} deck{'s' if count != 1 else ''}"
            text += f" · {excluded} excluded" if excluded else " selected"
        self.selection_label.setText(text)
        self.selection_label.setAccessibleDescription(text)
        self.apply_button.setEnabled(
            self._ready and not _is_custom(self._analysis)
        )

    def _ordered_selection(self) -> tuple[str, ...]:
        order = {
            _name_key(_entry_name(entry)): index
            for index, entry in enumerate(_catalog_entries(self._catalog))
        }
        return tuple(
            sorted(
                self._selected,
                key=lambda name: (order.get(_name_key(name), 10**9), name.casefold()),
            )
        )

    def _ordered_exclusions(self) -> tuple[str, ...]:
        order = {
            _name_key(_entry_name(entry)): index
            for index, entry in enumerate(_catalog_entries(self._catalog))
        }
        return tuple(
            sorted(
                self._excluded,
                key=lambda name: (order.get(_name_key(name), 10**9), name.casefold()),
            )
        )

    # ------------------------------------------------------------ interaction

    def _filter_tree(self, text: str) -> None:
        raw_needle = str(text or "").strip()
        needle = raw_needle.casefold()
        if needle and not self._filter_active:
            self._expanded_before_filter = {
                name for name, item in self._items.items() if item.isExpanded()
            }
            self._filter_active = True
        restoring = not needle and self._filter_active

        def visit(item: QTreeWidgetItem) -> bool:
            # Do not use a generator with ``any()`` here: its short-circuiting
            # would leave later siblings with stale visibility.
            child_visible = any(
                [visit(item.child(index)) for index in range(item.childCount())]
            )
            name = str(item.data(0, _ROLE_NAME) or "")
            own = not needle or needle in name.casefold()
            visible = own or child_visible
            item.setHidden(not visible)
            if needle and child_visible:
                item.setExpanded(True)
            return visible

        visible = False
        for index in range(self.tree.topLevelItemCount()):
            visible = visit(self.tree.topLevelItem(index)) or visible

        if restoring:
            for name, item in self._items.items():
                item.setExpanded(name in self._expanded_before_filter)
            self._expanded_before_filter.clear()
            self._filter_active = False

        no_matches = bool(needle) and not visible and self._ready
        self.no_matches_label.setText(
            f'No decks match “{raw_needle}”. Clear the field to show all decks.'
            if no_matches
            else ""
        )
        self.no_matches_label.setVisible(no_matches)
        self.no_matches_label.setAccessibleDescription(
            self.no_matches_label.text()
        )

    def _first_visible_item(self) -> QTreeWidgetItem | None:
        needle = self.filter_edit.text().strip().casefold()

        def find(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            name = str(item.data(0, _ROLE_NAME) or "")
            if not item.isHidden() and (
                not needle or needle in name.casefold()
            ):
                return item
            for index in range(item.childCount()):
                found = find(item.child(index))
                if found is not None:
                    return found
            return None

        for index in range(self.tree.topLevelItemCount()):
            found = find(self.tree.topLevelItem(index))
            if found is not None:
                return found
        return None

    def _set_picker_controls_enabled(self, enabled: bool) -> None:
        self.all_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.tree.setEnabled(enabled)
        current = _catalog_current_name(self._catalog)
        self.current_button.setEnabled(enabled and bool(current))
        self._update_summary()

    def _selection_controls_enabled(self) -> bool:
        return self._ready and not _is_custom(self._analysis)

    def _apply(self) -> None:
        if not self.apply_button.isEnabled():
            return
        self.applied.emit(self.selection)

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if obj is self.filter_edit and key == Qt.Key.Key_Down:
                item = self._first_visible_item()
                if item is not None:
                    self.tree.setCurrentItem(item)
                    self.tree.setFocus(Qt.FocusReason.ShortcutFocusReason)
                return True
            if obj is self.filter_edit and key in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            ):
                self._apply()
                return True
            if obj is self.tree and key == Qt.Key.Key_Space:
                item = self.tree.currentItem()
                if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    state = item.checkState(0)
                    item.setCheckState(
                        0,
                        Qt.CheckState.Unchecked
                        if state == Qt.CheckState.Checked
                        else Qt.CheckState.Checked,
                    )
                return True
            if obj is self.tree and key in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            ):
                self._apply()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---------------------------------------------------------------- styling

    def _apply_theme(self) -> None:
        c = chrome_colors(self)
        self.setStyleSheet(
            "QDialog#deckPickerPopup { background: transparent; }"
            "QFrame#deckPickerSurface {"
            f" background: {c['surface']}; color: {c['text']};"
            f" border: 1px solid {c['border_strong']}; border-radius: 12px;"
            "}"
            "QLineEdit#deckPickerFilter {"
            f" background: {c['base']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 9px;"
            " padding: 0 10px;"
            "}"
            "QLineEdit#deckPickerFilter:focus {"
            f" border-color: {c['accent']};"
            "}"
            "QToolButton#deckPickerQuick {"
            f" background: {c['surface_high']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 8px;"
            " min-height: 36px; padding: 0 10px; font-weight: 600;"
            "}"
            "QToolButton#deckPickerQuick:checked {"
            f" background: {c['accent']}; color: {c['accent_text']};"
            f" border-color: {c['accent']};"
            "}"
            "QFrame#deckPickerCustom {"
            f" background: {c['callout_bg']}; border: 1px solid {c['callout_border']};"
            " border-radius: 9px;"
            "}"
            "QLabel#deckPickerTitle {"
            f" color: {c['text']}; font-size: 16px; font-weight: 700;"
            " background: transparent; border: none;"
            "}"
            "QLabel#deckPickerSection {"
            f" color: {c['muted']}; font-size: 10px; font-weight: 700;"
            " letter-spacing: 1px; background: transparent; border: none;"
            "}"
            "QToolButton#deckPickerClear {"
            f" color: {c['accent']}; background: transparent;"
            " border: 1px solid transparent; border-radius: 6px;"
            " padding: 3px 6px; font-weight: 600;"
            "}"
            "QToolButton#deckPickerClear:focus {"
            f" background: {c['accent_soft']}; border-color: {c['accent']};"
            "}"
            "QToolButton#deckPickerClear:disabled {"
            f" color: {c['muted']};"
            "}"
            "QTreeWidget#deckPickerTree {"
            f" background: {c['base']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 9px;"
            " outline: none; padding: 4px;"
            "}"
            "QTreeWidget#deckPickerTree::item { min-height: 28px; }"
            "QTreeWidget#deckPickerTree::item:hover {"
            f" background: {c['surface_high']};"
            "}"
            "QTreeWidget#deckPickerTree::item:selected {"
            f" background: {c['accent_soft']}; color: {c['text']};"
            "}"
            "QTreeWidget#deckPickerTree:focus {"
            f" border-color: {c['accent']};"
            "}"
            "QLabel#deckPickerMessage, QLabel#deckPickerSelection,"
            " QLabel#deckPickerNoMatches {"
            f" color: {c['muted']}; background: transparent; border: none;"
            "}"
            "QLabel#deckPickerMessage[validationError=\"true\"] {"
            f" color: {c['err']};"
            "}"
            "QPushButton#deckPickerSecondary, QPushButton#deckPickerApply {"
            " min-height: 34px; padding: 0 12px; border-radius: 8px;"
            " font-weight: 600;"
            "}"
            "QPushButton#deckPickerSecondary {"
            f" background: {c['surface_high']}; color: {c['text']};"
            f" border: 1px solid {c['border']};"
            "}"
            "QPushButton#deckPickerApply {"
            f" background: {c['accent']}; color: {c['accent_text']};"
            f" border: 1px solid {c['accent']};"
            "}"
            "QPushButton#deckPickerApply:disabled {"
            f" background: {c['surface_high']}; color: {c['muted']};"
            f" border-color: {c['border']};"
            "}"
            "QToolButton#deckPickerQuick:focus,"
            "QPushButton#deckPickerSecondary:focus {"
            f" border-color: {c['accent']};"
            "}"
            "QToolButton#deckPickerQuick:checked:focus {"
            f" border-color: {c['accent_text']};"
            "}"
            "QPushButton#deckPickerApply:focus {"
            f" background: {c['accent_hover']};"
            "}"
        )

    def changeEvent(self, event: QEvent) -> None:
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ) and not getattr(self, "_refreshing_palette", False):
            self._refreshing_palette = True
            try:
                self._apply_theme()
                if hasattr(self, "_items"):
                    self._refresh_checks()
            finally:
                self._refreshing_palette = False
        super().changeEvent(event)


__all__ = ["DeckPickerPopup", "DeckScopeButton"]
