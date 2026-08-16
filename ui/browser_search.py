"""Native Anki Browser search-field integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
import weakref

from .widgets import QColor, QIcon, QLineEdit, QPainter, QPen, QPixmap, QRect, Qt


ACTION_OBJECT_NAME = "smartSearchMedicalBrowserHandoff"
ACTION_TEXT = "Open in Smart Search"
ACTION_TOOLTIP = "Open this search in Smart Search"
SMART_SEARCH_GREEN = "#187A57"
_SMART_SEARCH_GREEN_DARK = "#0C5E43"
_SMART_SEARCH_MINT = "#F1FAF6"
_CALLBACK_ATTRIBUTE = "_smart_search_medical_handoff_callback"


def _smart_search_logo_pixmap(size: int = 64) -> Any:
    """Draw the compact Smart Search magnifier-plus mark."""

    side = max(16, int(size))
    scale = side / 64.0

    def px(value: float) -> int:
        return int(round(value * scale))

    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    tile_pen = QPen(QColor(_SMART_SEARCH_GREEN_DARK), max(1.0, 2.0 * scale))
    tile_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(tile_pen)
    painter.setBrush(QColor(SMART_SEARCH_GREEN))
    painter.drawRoundedRect(
        QRect(px(3), px(3), px(58), px(58)),
        px(14),
        px(14),
    )

    lens_pen = QPen(QColor("#FFFFFF"), max(1.5, 5.0 * scale))
    lens_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    lens_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(lens_pen)
    painter.drawEllipse(px(14), px(15), px(27), px(27))
    painter.drawLine(px(37), px(39), px(49), px(51))

    badge_pen = QPen(
        QColor(_SMART_SEARCH_GREEN_DARK),
        max(1.0, 2.25 * scale),
    )
    painter.setPen(badge_pen)
    painter.setBrush(QColor(_SMART_SEARCH_MINT))
    painter.drawEllipse(px(37), px(6), px(21), px(21))

    plus_pen = QPen(QColor(SMART_SEARCH_GREEN), max(1.25, 3.25 * scale))
    plus_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(plus_pen)
    painter.drawLine(px(42), px(16.5), px(53), px(16.5))
    painter.drawLine(px(47.5), px(11), px(47.5), px(22))
    painter.end()
    return pixmap


def _smart_search_icon() -> Any:
    """Return the original green Smart Search mark at useful Qt sizes."""

    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 96):
        icon.addPixmap(_smart_search_logo_pixmap(size))
    return icon


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
            make_icon = icon_factory or _smart_search_icon
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
    except Exception:
        # Older wrappers, deleted Qt objects, and non-editable combo boxes all
        # fail closed without changing or disrupting the Browser.
        return None


def refresh_browser_search_action(action: Any) -> bool:
    """Refresh one live action after Anki changes light/dark theme."""

    try:
        action.setIcon(_smart_search_icon())
        return True
    except Exception:
        return False


def remove_browser_search_action(action: Any) -> None:
    """Remove a previously installed action during add-on shutdown."""

    try:
        parent = action.parent()
        if parent is not None:
            parent.removeAction(action)
        action.deleteLater()
    except Exception:
        return
