"""Small compatibility seams for supported Anki and Python hosts.

The official Anki 24.11/25.02 builds use Python 3.9, while newer Anki
releases use Python 3.13.  Keep version-sensitive behavior in this module so
the search implementation can remain ordinary, testable Python.

Nothing here imports Anki at module import time.  Host probing is deliberately
best-effort and capability-based: add-ons are more resilient when optional UI
features can be disabled independently of the core search path.
"""

from __future__ import annotations

from dataclasses import dataclass as _stdlib_dataclass
import importlib
import sys
from typing import Any, Callable


def dataclass(_cls: Any = None, /, **kwargs: Any) -> Any:
    """Backport the subset of :func:`dataclasses.dataclass` that we use.

    ``slots=`` was added in Python 3.10.  On Anki's Python 3.9 builds we omit
    that memory optimization while preserving all dataclass behavior.  Newer
    hosts continue to receive real slotted dataclasses.
    """

    if sys.version_info < (3, 10):
        kwargs.pop("slots", None)
        kwargs.pop("match_args", None)
        kwargs.pop("kw_only", None)
    if sys.version_info < (3, 11):
        kwargs.pop("weakref_slot", None)
    if _cls is None:
        return lambda cls: _stdlib_dataclass(cls, **kwargs)
    return _stdlib_dataclass(_cls, **kwargs)


def int_bit_count(value: int) -> int:
    """Return the population count on Python 3.8+.

    ``int.bit_count()`` arrived in Python 3.10.  The fallback is only used on
    the older Anki runtime and receives the same non-negative masks as the
    native fast path.
    """

    number = int(value)
    native = getattr(number, "bit_count", None)
    if callable(native):
        return int(native())
    return bin(number if number >= 0 else -number).count("1")


def anki_point_version(version_getter: Callable[[], Any] | None = None) -> int:
    """Return Anki's sortable point version, or ``0`` outside Anki."""

    if version_getter is None:
        try:
            from anki.utils import int_version
        except (ImportError, AttributeError):
            return 0
        version_getter = int_version
    try:
        version = int(version_getter())
    except (TypeError, ValueError, RuntimeError):
        return 0
    return max(0, version)


def optional_hook(owner: Any, name: str) -> Any | None:
    """Return an appendable Anki hook when available on this host."""

    hook = getattr(owner, str(name), None)
    return hook if callable(getattr(hook, "append", None)) else None


def append_optional_hook(owner: Any, name: str, callback: Callable[..., Any]) -> bool:
    """Append to a host hook when present and report whether it was installed."""

    hook = optional_hook(owner, name)
    if hook is None:
        return False
    hook.append(callback)
    return True


def _module_has(
    module_name: str,
    *attributes: str,
    importer: Callable[[str], Any],
) -> bool:
    try:
        module = importer(module_name)
    except (ImportError, AttributeError, RuntimeError):
        return False
    return all(getattr(module, attribute, None) is not None for attribute in attributes)


def _browser_dialog_available(importer: Callable[[str], Any]) -> bool:
    """Detect Anki's dialog manager across its historical layouts."""

    try:
        dialogs = getattr(importer("aqt"), "dialogs", None)
    except (ImportError, AttributeError, RuntimeError):
        dialogs = None
    if callable(getattr(dialogs, "open", None)):
        return True

    # Retain a defensive fallback for development hosts that expose dialogs as
    # an importable module instead of Anki's normal ``aqt.dialogs`` attribute.
    return _module_has("aqt.dialogs", "open", importer=importer)


@_stdlib_dataclass(frozen=True)
class AnkiHostFeatures:
    """Snapshot of optional and required APIs exposed by an Anki process."""

    point_version: int
    query_operations: bool
    collection_operations: bool
    lifecycle_hooks: bool
    toolbar_hook: bool
    temporary_collection_hooks: bool
    browser_dialog: bool
    native_previewer: bool
    inline_editor: bool

    @property
    def core_search_available(self) -> bool:
        return self.query_operations and self.lifecycle_hooks

    @property
    def card_actions_available(self) -> bool:
        return self.collection_operations


def probe_anki_host(
    *,
    point_version: int | None = None,
    gui_hooks: Any | None = None,
    importer: Callable[[str], Any] = importlib.import_module,
) -> AnkiHostFeatures:
    """Feature-detect the narrow Anki surface used by this add-on.

    Arguments are injectable so release tests can model old and future Anki
    hosts without importing or monkey-patching a live collection.
    """

    if gui_hooks is None:
        try:
            gui_hooks = importer("aqt").gui_hooks
        except (ImportError, AttributeError, RuntimeError):
            gui_hooks = None

    def hooks_available(*names: str) -> bool:
        return gui_hooks is not None and all(
            optional_hook(gui_hooks, name) is not None for name in names
        )

    return AnkiHostFeatures(
        point_version=(
            anki_point_version() if point_version is None else max(0, int(point_version))
        ),
        query_operations=_module_has(
            "aqt.operations",
            "QueryOp",
            importer=importer,
        ),
        collection_operations=_module_has(
            "aqt.operations",
            "CollectionOp",
            importer=importer,
        ),
        lifecycle_hooks=hooks_available(
            "profile_did_open",
            "profile_will_close",
            "operation_did_execute",
        ),
        toolbar_hook=hooks_available("top_toolbar_did_init_links"),
        temporary_collection_hooks=hooks_available(
            "collection_will_temporarily_close",
            "collection_did_temporarily_close",
        ),
        browser_dialog=_browser_dialog_available(importer),
        native_previewer=_module_has(
            "aqt.browser.previewer",
            "MultiCardPreviewer",
            importer=importer,
        ),
        inline_editor=(
            _module_has("aqt.editor", "Editor", "EditorMode", importer=importer)
            and _module_has(
                "aqt.webview",
                "AnkiWebView",
                "AnkiWebViewKind",
                importer=importer,
            )
            and _module_has("aqt.mediasrv", "PageContext", importer=importer)
        ),
    )


__all__ = [
    "AnkiHostFeatures",
    "anki_point_version",
    "append_optional_hook",
    "dataclass",
    "int_bit_count",
    "optional_hook",
    "probe_anki_host",
]
