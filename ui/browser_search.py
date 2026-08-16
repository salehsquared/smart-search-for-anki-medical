"""Native Anki Browser search-field integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
import weakref

from .widgets import QIcon, QLineEdit


ACTION_OBJECT_NAME = "smartSearchMedicalBrowserHandoff"
ACTION_TEXT = "Open in Smart Search"
ACTION_TOOLTIP = "Open this search in Smart Search"
_CALLBACK_ATTRIBUTE = "_smart_search_medical_handoff_callback"


def _native_search_icon() -> Any:
    """Return Anki's palette-aware search icon, with a safe Qt fallback."""

    try:
        from aqt.theme import theme_manager

        return theme_manager.icon_from_resources("mdi:magnify")
    except Exception:
        return QIcon.fromTheme("edit-find")


def install_browser_search_action(
    browser: Any,
    open_query: Callable[[str], None],
    *,
    icon_factory: Callable[[], Any] | None = None,
) -> Any | None:
    """Install one leading action in an Anki Browser search field."""

    try:
        combo = browser.form.searchEdit
        line_edit = combo.lineEdit()
        if line_edit is None:
            return None
        action = None
        for existing in line_edit.actions():
            if existing.objectName() == ACTION_OBJECT_NAME:
                action = existing
                break

        if action is None:
            make_icon = icon_factory or _native_search_icon
            action = line_edit.addAction(
                make_icon(),
                QLineEdit.ActionPosition.LeadingPosition,
            )
        action.setObjectName(ACTION_OBJECT_NAME)
        action.setText(ACTION_TEXT)
        action.setToolTip(ACTION_TOOLTIP)
        action.setStatusTip(ACTION_TOOLTIP)

        browser_ref = weakref.ref(browser)

        def open_current_query(_checked: bool = False) -> None:
            current_browser = browser_ref()
            if current_browser is None:
                return
            try:
                query = str(current_browser.current_search())
                open_query(query)
            except Exception:
                # An optional Browser affordance must never disrupt Anki's
                # own search field if a window is closing or being rebuilt.
                return

        previous = getattr(action, _CALLBACK_ATTRIBUTE, None)
        if previous is not None:
            try:
                action.triggered.disconnect(previous)
            except (RuntimeError, TypeError):
                pass
        action.triggered.connect(open_current_query)
        setattr(action, _CALLBACK_ATTRIBUTE, open_current_query)
        return action
    except (AttributeError, RuntimeError, TypeError):
        # Older wrappers, deleted Qt objects, and non-editable combo boxes all
        # fail closed without changing the Browser.
        return None


def refresh_browser_search_action(action: Any) -> bool:
    """Refresh one live action after Anki changes light/dark theme."""

    try:
        action.setIcon(_native_search_icon())
        return True
    except (AttributeError, RuntimeError):
        return False


def remove_browser_search_action(action: Any) -> None:
    """Remove a previously installed action during add-on shutdown."""

    try:
        parent = action.parent()
        if parent is not None:
            parent.removeAction(action)
        action.deleteLater()
    except (AttributeError, RuntimeError):
        return
