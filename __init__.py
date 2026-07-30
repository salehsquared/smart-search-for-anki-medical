"""Smart Search for Anki — Medical.

The add-on exposes one global command-palette action and delegates all
collection reads/index maintenance to :mod:`controller`. Its indexes are
derived, profile-scoped files under ``user_files``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


__version__ = "1.0.12"

_controller: Any | None = None
_menu_action: Any | None = None


def _install() -> None:
    global _controller, _menu_action

    from aqt import mw
    from aqt.qt import QAction, QKeySequence, Qt

    from .controller import create_controller

    bundle_root = Path(__file__).resolve().parent
    _controller = create_controller(
        mw,
        bundle_root=bundle_root,
        addon_module=__name__,
    )

    config = mw.addonManager.getConfig(__name__) or {}
    shortcut_text = str(config.get("shortcut", "Ctrl+K")).strip() or "Ctrl+K"
    shortcut = QKeySequence(shortcut_text)
    if shortcut.isEmpty():
        shortcut = QKeySequence("Ctrl+K")

    action = QAction("Smart Search…", mw)
    action.setObjectName("smart_search_medical_open")
    action.setToolTip("Search cards with typo recovery, medical aliases, and local semantics")
    action.setShortcut(shortcut)
    action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
    action.triggered.connect(_controller.show_search)
    mw.form.menuTools.addAction(action)
    _menu_action = action


_install()


__all__ = ["__version__"]
