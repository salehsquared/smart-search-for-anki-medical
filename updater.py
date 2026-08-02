"""Safe bridge to Anki's native AnkiWeb add-on updater.

The public updater is deliberately available only to the numeric AnkiWeb
installation.  A development or manually installed ``smart_search_medical``
copy must never ask Anki to install the public ID beside itself, as that would
create two live copies of the add-on.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable


ANKIWEB_ADDON_ID = 677438639
ANKIWEB_MODULE = str(ANKIWEB_ADDON_ID)


def is_public_ankiweb_install(addon_manager: Any, addon_module: str) -> bool:
    """Return whether this exact running module is the public AnkiWeb copy.

    The check intentionally fails closed.  Looking only for another installed
    numeric copy is insufficient: the currently running development module
    could otherwise update that copy and leave both add-ons enabled.
    """

    if str(addon_module or "") != ANKIWEB_MODULE:
        return False
    try:
        meta = addon_manager.addon_meta(ANKIWEB_MODULE)
        identifier = getattr(meta, "ankiweb_id", None)
        return callable(identifier) and int(identifier()) == ANKIWEB_ADDON_ID
    except Exception:
        return False


def native_update_available(
    mw: Any,
    addon_module: str,
    *,
    importer: Callable[[str], Any] = importlib.import_module,
) -> bool:
    """Return whether the public copy can delegate to a native updater."""

    manager = getattr(mw, "addonManager", None)
    if manager is None or not is_public_ankiweb_install(manager, addon_module):
        return False
    try:
        addons = importer("aqt.addons")
    except (ImportError, AttributeError, RuntimeError):
        addons = None
    return bool(callable(getattr(addons, "install_or_update_addon", None)))


def native_update_was_installed(
    log: list[Any],
    *,
    importer: Callable[[str], Any] = importlib.import_module,
) -> bool:
    """Return whether Anki reports a successful bundle replacement."""

    try:
        install_ok = getattr(importer("aqt.addons"), "InstallOk")
    except (ImportError, AttributeError, RuntimeError):
        return False
    if not isinstance(install_ok, type):
        return False
    return any(
        isinstance(entry, (tuple, list))
        and len(entry) >= 2
        and isinstance(entry[1], install_ok)
        for entry in log
    )


def request_native_update(
    parent: Any,
    mw: Any,
    addon_module: str,
    on_done: Callable[[list[Any]], None],
    *,
    importer: Callable[[str], Any] = importlib.import_module,
) -> str:
    """Start Anki's native update flow and return the selected strategy.

    The targeted helper is present throughout the supported Anki range.  We
    deliberately do not fall back to Anki's all-add-on chooser: cancellation
    of that flow has no completion callback, so an exclusive update gate could
    not be released reliably.
    """

    manager = getattr(mw, "addonManager", None)
    if manager is None or not is_public_ankiweb_install(manager, addon_module):
        raise RuntimeError(
            "Updates are available only for the AnkiWeb installation."
        )

    try:
        addons = importer("aqt.addons")
    except (ImportError, AttributeError, RuntimeError):
        addons = None
    targeted = getattr(addons, "install_or_update_addon", None)
    if callable(targeted):
        targeted(parent, manager, ANKIWEB_ADDON_ID, on_done)
        return "targeted"

    raise RuntimeError("This version of Anki cannot check for add-on updates.")


__all__ = [
    "ANKIWEB_ADDON_ID",
    "is_public_ankiweb_install",
    "native_update_available",
    "native_update_was_installed",
    "request_native_update",
]
