"""Keyboard-first smart-search UI for supported Anki Desktop releases.

Layout:

- :mod:`ui.contracts` — pure dataclasses, enums, backend protocol, and
  query helpers (no Qt imports; unit-testable with plain Python).
- :mod:`ui.widgets` — Qt import shim (``aqt.qt`` with a PyQt6 fallback)
  plus the search field, segmented mode control, chips, and status pill.
- :mod:`ui.results` — result list model, delegate, and view.
- :mod:`ui.dialog` — the dialog: layout, states, keyboard behavior.
- :mod:`ui.controller` — backend marshalling, request IDs, Browser opening.
Importing this package does not import Qt, so contracts stay testable
outside Anki; import the submodules you need directly.
"""

from __future__ import annotations

__version__ = "1.0.18"

__all__ = [
    "SearchDialog",
    "SearchController",
    "SearchBackend",
    "SearchMode",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "IndexStatus",
    "SemanticStatus",
]


def __getattr__(name: str):
    """Keep Qt imports lazy while offering a convenient public API."""
    if name == "SearchDialog":
        from .dialog import SearchDialog

        return SearchDialog
    if name == "SearchController":
        from .controller import SearchController

        return SearchController
    if name in {
        "SearchBackend",
        "SearchMode",
        "SearchRequest",
        "SearchResponse",
        "SearchResult",
        "IndexStatus",
        "SemanticStatus",
    }:
        from . import contracts

        return getattr(contracts, name)
    raise AttributeError(name)
