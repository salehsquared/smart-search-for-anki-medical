"""Smart Search for Anki — Medical.

The add-on exposes one global command-palette action and delegates all
collection reads/index maintenance to :mod:`controller`. Its indexes are
derived, profile-scoped files under ``user_files``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


__version__ = "1.0.22"

_controller: Any | None = None
_menu_action: Any | None = None
_TOOLBAR_COMMAND = "smart_search_medical_toolbar"
_TOOLBAR_LINK_ID = "smart-search-medical"
_TOOLBAR_ICON = (
    '<svg aria-hidden="true" focusable="false" width="13" height="13" '
    'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.2" stroke-linecap="round" '
    'style="display:inline-block;vertical-align:-2px;margin-right:4px">'
    '<circle cx="10.5" cy="10.5" r="6.25"></circle>'
    '<path d="m15.2 15.2 4.3 4.3"></path>'
    "</svg>"
)


def _link_has_id(link: str, link_id: str) -> bool:
    return any(
        marker in link
        for marker in (
            f'id="{link_id}"',
            f"id='{link_id}'",
            f"id={link_id}",
        )
    )


def _add_top_toolbar_link(links: list[str], toolbar: Any) -> None:
    """Place the compact Smart entry immediately after Anki's Browse link."""

    if _controller is None or any(
        _link_has_id(link, _TOOLBAR_LINK_ID) for link in links
    ):
        return

    link = toolbar.create_link(
        _TOOLBAR_COMMAND,
        "Smart",
        _controller.show_search,
        tip="Smart Search (Command/Ctrl-K)",
        id=_TOOLBAR_LINK_ID,
    )
    # create_link() uses the plain label for aria-label as well as the visible
    # text. Replace only the visible occurrence so accessibility stays clean.
    link = link.replace(
        ">Smart</a>",
        f">{_TOOLBAR_ICON}Smart</a>",
        1,
    )

    insert_at = len(links)
    for index, existing in enumerate(links):
        if _link_has_id(existing, "browse"):
            insert_at = index + 1
            break
    else:
        for index, existing in enumerate(links):
            if _link_has_id(existing, "stats"):
                insert_at = index
                break
    links.insert(insert_at, link)


def _install() -> None:
    global _controller, _menu_action

    from aqt import gui_hooks, mw
    from aqt.qt import QAction, QKeySequence, Qt

    from .backend.compat import optional_hook
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

    toolbar_hook = optional_hook(gui_hooks, "top_toolbar_did_init_links")
    if toolbar_hook is not None:
        toolbar_hook.append(_add_top_toolbar_link)


_install()


__all__ = ["__version__"]
