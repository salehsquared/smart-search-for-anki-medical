"""Typed contracts shared between the smart-search UI and its backend.

This module is intentionally free of any Qt (or Anki) imports so that the
dataclasses, enums, and pure query helpers can be unit-tested with a plain
Python 3.13 interpreter. The UI talks to the backend exclusively through the
:class:`SearchBackend` protocol and the dataclasses declared here, which keeps
the UI easy to adapt to a different backend implementation.
"""

from __future__ import annotations

import enum
import re
from dataclasses import field
from typing import Callable, Optional, Protocol, Sequence, runtime_checkable

try:  # installed add-on package
    from ..backend.compat import dataclass
except ImportError:  # plain-Python tests import ``ui`` as a top-level package
    from backend.compat import dataclass

__all__ = [
    "SearchMode",
    "MatchKind",
    "IndexState",
    "SemanticState",
    "SemanticRecovery",
    "HighlightSpan",
    "FilterChip",
    "DeckEntry",
    "DeckCatalog",
    "Correction",
    "CardState",
    "SearchResult",
    "SearchRequest",
    "SearchResponse",
    "IndexStatus",
    "SemanticStatus",
    "UISettings",
    "AboutInfo",
    "SearchBackend",
    "CancelCallback",
    "ErrorCallback",
    "ProgressCallback",
    "SearchSuccessCallback",
    "StatusCallback",
    "DeckCatalogCallback",
    "clamp_spans",
    "merge_spans",
    "normalize_query",
    "parse_filter_tokens",
    "remove_filter_token",
]


class SearchMode(enum.Enum):
    """How the backend interprets the free-text part of the query."""

    SMART = "smart"
    EXACT = "exact"
    SEMANTIC = "semantic"

    @property
    def label(self) -> str:
        return {
            SearchMode.SMART: "Smart",
            SearchMode.EXACT: "Exact",
            SearchMode.SEMANTIC: "Semantic",
        }[self]


class MatchKind(enum.Enum):
    """Why a span (or a whole result) matched. Shown as badges."""

    EXACT = "exact"
    TITLE = "title"
    TAG = "tag"
    CORRECTION = "typo fix"
    ALIAS = "alias"
    SEMANTIC = "semantic"

    @property
    def badge(self) -> str:
        return self.value


class IndexState(enum.Enum):
    """Lifecycle of the search index / embedding model."""

    READY = "ready"
    BUILDING = "building"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class SemanticState(enum.Enum):
    """Honest lifecycle of the optional local semantic-search runtime."""

    UNSUPPORTED = "unsupported"
    NOT_INSTALLED = "not_installed"
    MODEL_READY = "model_ready"
    INDEXING = "indexing"
    READY = "ready"
    ERROR = "error"


class SemanticRecovery(enum.Enum):
    """User action most likely to recover a semantic-search error."""

    REINDEX = "reindex"
    MODEL = "model"


@dataclass(frozen=True, slots=True)
class HighlightSpan:
    """Half-open character range [start, end) into a *plain-text* snippet."""

    start: int
    end: int
    kind: MatchKind = MatchKind.EXACT

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("HighlightSpan.start must be >= 0")
        if self.end < self.start:
            raise ValueError("HighlightSpan.end must be >= start")


@dataclass(frozen=True, slots=True)
class FilterChip:
    """A structured filter such as ``deck:AnKing`` or ``tag:cardio``."""

    key: str
    value: str
    label: str = ""
    raw_token: str = ""

    @property
    def display(self) -> str:
        return self.label or f"{self.key}: {self.value}"

    @property
    def token(self) -> str:
        """Canonical query token, quoting values that contain whitespace."""
        if self.raw_token:
            return self.raw_token
        if re.search(r"\s", self.value):
            return f'{self.key}:"{self.value}"'
        return f"{self.key}:{self.value}"


@dataclass(frozen=True, slots=True)
class DeckEntry:
    """One native Anki deck exposed to the search UI."""

    deck_id: int
    name: str

    def __post_init__(self) -> None:
        deck_id = int(self.deck_id)
        name = str(self.name)
        if deck_id <= 0:
            raise ValueError("DeckEntry.deck_id must be a positive integer")
        if not name:
            raise ValueError("DeckEntry.name must not be empty")
        object.__setattr__(self, "deck_id", deck_id)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class DeckCatalog:
    """Immutable deck choices and the deck currently selected in Anki."""

    decks: tuple[DeckEntry, ...] = ()
    current_deck_id: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decks", tuple(self.decks))
        current_deck_id = self.current_deck_id
        if current_deck_id is not None:
            current_deck_id = int(current_deck_id)
            if current_deck_id <= 0:
                current_deck_id = None
        object.__setattr__(self, "current_deck_id", current_deck_id)

    @property
    def entries(self) -> tuple[DeckEntry, ...]:
        """Alias retained for consumers that refer to catalog entries."""

        return self.decks

    @property
    def current_deck(self) -> Optional[DeckEntry]:
        return next(
            (
                deck
                for deck in self.decks
                if deck.deck_id == self.current_deck_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class Correction:
    """A spelling correction or alias expansion applied by the backend."""

    original: str
    replacement: str
    kind: str = "spelling"  # "spelling" | "alias"
    explanation: str = ""
    literal_query: str = ""  # query to run if the user wants the literal text

    @property
    def display(self) -> str:
        return f"{self.original} → {self.replacement}"


@dataclass(frozen=True, slots=True)
class CardState:
    """Live, non-indexed state for one card represented by a result row."""

    card_id: int
    flag: int = 0
    suspended: bool = False

    def __post_init__(self) -> None:
        card_id = int(self.card_id)
        flag = int(self.flag)
        if card_id <= 0:
            raise ValueError("CardState.card_id must be a positive integer")
        if not 0 <= flag <= 7:
            raise ValueError("CardState.flag must be between 0 and 7")
        object.__setattr__(self, "card_id", card_id)
        object.__setattr__(self, "flag", flag)
        object.__setattr__(self, "suspended", bool(self.suspended))


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One note that matched. All user/card text is plain, unescaped text."""

    note_id: int
    card_ids: tuple[int, ...] = ()
    card_states: tuple[CardState, ...] = ()
    title: str = ""
    snippet: str = ""
    spans: tuple[HighlightSpan, ...] = ()
    deck: str = ""
    note_type: str = ""
    tags: tuple[str, ...] = ()
    match_reasons: tuple[str, ...] = ()
    sibling_count: int = 0
    browser_query: Optional[str] = None
    score: Optional[float] = None


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """A single search. ``request_id`` must increase monotonically."""

    request_id: int
    query: str
    mode: SearchMode = SearchMode.SMART
    filters: tuple[FilterChip, ...] = ()
    literal: bool = False  # bypass spelling corrections / alias expansion
    limit: int = 50


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """What the backend returns for one :class:`SearchRequest`."""

    request_id: int
    query: str
    results: tuple[SearchResult, ...] = ()
    corrections: tuple[Correction, ...] = ()
    active_filters: tuple[FilterChip, ...] = ()
    elapsed_ms: float = 0.0
    total_results: int = 0
    warnings: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class SemanticStatus:
    """Snapshot used by the UI without importing the semantic implementation."""

    state: SemanticState
    detail: str = ""
    indexed_notes: int = 0
    progress: Optional[float] = None
    recovery: SemanticRecovery = SemanticRecovery.REINDEX
    auto_start: bool = False

    @property
    def ready(self) -> bool:
        return self.state is SemanticState.READY

    @property
    def summary(self) -> str:
        if self.state is SemanticState.UNSUPPORTED:
            return "Semantic search unavailable on this computer"
        if self.state is SemanticState.NOT_INSTALLED:
            return "Semantic search needs setup"
        if self.state is SemanticState.MODEL_READY:
            return (
                "Semantic search preparation starts automatically"
                if self.auto_start
                else "Semantic search needs preparation"
            )
        if self.state is SemanticState.INDEXING:
            if self.progress is None:
                return "Preparing semantic search"
            return f"Preparing semantic search {round(self.progress * 100)}%"
        if self.state is SemanticState.READY:
            suffix = f" · {self.indexed_notes:,} notes" if self.indexed_notes else ""
            return f"Semantic search ready{suffix}"
        return "Semantic search needs attention"


@dataclass(frozen=True, slots=True)
class IndexStatus:
    """Snapshot of index/model health for the compact status control."""

    state: IndexState
    progress: Optional[float] = None  # 0.0–1.0 while BUILDING
    detail: str = ""
    model_name: Optional[str] = None
    semantic: Optional[SemanticStatus] = None

    @property
    def ready(self) -> bool:
        return self.state is IndexState.READY

    @property
    def summary(self) -> str:
        if self.state is IndexState.READY:
            # ``model_name`` is retained as backend metadata for compatibility,
            # but implementation identifiers never belong in normal UI.
            return "Smart & Exact ready"
        if self.state is IndexState.BUILDING:
            pct = f" {round((self.progress or 0) * 100)}%" if self.progress is not None else ""
            return f"Preparing Smart & Exact{pct}"
        if self.state is IndexState.UNAVAILABLE:
            return "Smart & Exact need setup"
        return "Smart & Exact need attention"


@dataclass(slots=True)
class UISettings:
    """Persisted UI preferences."""

    mode: SearchMode = SearchMode.SMART
    filters: tuple[FilterChip, ...] = ()
    result_limit: int = 50
    preview_enabled: bool = True
    width: int = 1040
    height: int = 700


@dataclass(frozen=True, slots=True)
class AboutInfo:
    """Attribution payload handed to the About surface by the host boundary.

    The UI never hardcodes bundle paths or versions; the host resolves the
    real bundle location and the canonical add-on version.  Empty optional
    fields simply hide the corresponding row.
    """

    product_name: str
    creator: str
    version: str
    logo_path: str = ""
    website_url: str = ""
    feedback_url: str = ""
    privacy_url: str = ""
    can_check_for_updates: bool = False


# ---------------------------------------------------------------------------
# Callback types and the backend protocol
# ---------------------------------------------------------------------------

CancelCallback = Callable[[], None]
ErrorCallback = Callable[[str], None]
ProgressCallback = Callable[[float, str], None]  # progress 0.0–1.0, detail text
SearchSuccessCallback = Callable[[SearchResponse], None]
StatusCallback = Callable[[IndexStatus], None]
DeckCatalogCallback = Callable[[DeckCatalog], None]


@runtime_checkable
class SearchBackend(Protocol):
    """Async boundary between the UI and the search implementation.

    Implementations may invoke the callbacks from a worker thread; the UI
    marshals them onto the GUI thread with Qt signals. ``submit_search`` and
    ``rebuild_index`` may return a callable that cancels the in-flight
    operation (best effort), or ``None`` if cancellation is unsupported.
    """

    def submit_search(
        self,
        request: SearchRequest,
        on_success: SearchSuccessCallback,
        on_error: ErrorCallback,
    ) -> Optional[CancelCallback]: ...

    def load_decks(
        self,
        on_success: DeckCatalogCallback,
        on_error: ErrorCallback,
    ) -> Optional[CancelCallback]: ...

    def get_status(self) -> IndexStatus: ...

    def rebuild_index(
        self,
        on_progress: ProgressCallback,
        on_success: StatusCallback,
        on_error: ErrorCallback,
    ) -> Optional[CancelCallback]: ...

    def load_settings(self) -> UISettings: ...

    def save_settings(self, settings: UISettings) -> None: ...


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without Qt)
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
# Matches `key:value` and `key:"quoted value"` structured-filter tokens.
FILTER_TOKEN_RE = re.compile(
    r'(?<!\S)(?P<neg>-?)(?P<key>"(?:\\.|[^"])*"|[\w*_-]+):'
    r'(?P<value>"(?:\\.|[^"])*"|[^\s()]*)'
)


def normalize_query(query: str) -> str:
    """Collapse whitespace and strip ends; safe for empty/odd input."""
    return _WS_RE.sub(" ", query or "").strip()


def clamp_spans(spans: Sequence[HighlightSpan], length: int) -> tuple[HighlightSpan, ...]:
    """Drop or clamp spans so every span fits inside ``length`` characters."""
    out: list[HighlightSpan] = []
    for span in spans:
        start = min(max(span.start, 0), length)
        end = min(max(span.end, 0), length)
        if end > start:
            out.append(HighlightSpan(start, end, span.kind))
    return tuple(out)


def merge_spans(spans: Sequence[HighlightSpan]) -> tuple[HighlightSpan, ...]:
    """Sort spans and merge overlaps, keeping the kind of the earliest span."""
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    merged: list[HighlightSpan] = []
    for span in ordered:
        if span.end <= span.start:
            continue
        if merged and span.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = HighlightSpan(last.start, max(last.end, span.end), last.kind)
        else:
            merged.append(span)
    return tuple(merged)


def parse_filter_tokens(query: str) -> tuple[tuple[FilterChip, ...], str]:
    """Split ``deck:x tag:"y z" free text`` into filters + remaining text."""
    filters: list[FilterChip] = []

    def _collect(match: re.Match[str]) -> str:
        key = match.group("key")
        value = match.group("value")
        if value.startswith("//"):
            return match.group(0)
        if len(key) >= 2 and key[0] == key[-1] == '"':
            key = key[1:-1]
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        key = ("-" if match.group("neg") else "") + key
        filters.append(
            FilterChip(
                key=key,
                value=value,
                raw_token=match.group(0),
            )
        )
        return " "

    remaining = FILTER_TOKEN_RE.sub(_collect, query or "")
    return tuple(filters), normalize_query(remaining)


def remove_filter_token(query: str, chip: FilterChip) -> str:
    """Remove the query text that produced ``chip`` (first occurrence)."""
    if chip.raw_token:
        start = (query or "").find(chip.raw_token)
        if start >= 0:
            end = start + len(chip.raw_token)
            return normalize_query((query[:start] + " " + query[end:]))
    for match in FILTER_TOKEN_RE.finditer(query or ""):
        key = match.group("key")
        value = match.group("value")
        if len(key) >= 2 and key[0] == key[-1] == '"':
            key = key[1:-1]
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        key = ("-" if match.group("neg") else "") + key
        if key == chip.key and value == chip.value:
            start, end = match.span()
            return normalize_query((query[:start] + " " + query[end:]))
    return normalize_query(query or "")
