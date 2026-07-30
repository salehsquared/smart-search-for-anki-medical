"""Data contracts shared by indexing, retrieval, and the UI adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class IndexedNote:
    """An immutable, read-only snapshot of one Anki note.

    An adapter creates these snapshots through Anki's public collection APIs.
    The search index never receives the collection object itself.
    """

    note_id: int
    fields: Mapping[str, str]
    tags: tuple[str, ...] = ()
    decks: tuple[str, ...] = ()
    note_type: str = ""
    card_ids: tuple[int, ...] = ()
    modified_seconds: int = 0
    guid: str = ""
    title: str = ""

    def __post_init__(self) -> None:
        if self.note_id <= 0:
            raise ValueError("note_id must be a positive integer")
        object.__setattr__(
            self,
            "fields",
            MappingProxyType({str(key): str(value) for key, value in self.fields.items()}),
        )
        object.__setattr__(self, "tags", _unique_strings(self.tags))
        object.__setattr__(self, "decks", _unique_strings(self.decks))
        object.__setattr__(
            self, "card_ids", tuple(dict.fromkeys(int(card_id) for card_id in self.card_ids))
        )


def _unique_strings(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value).strip()))


@dataclass(frozen=True, slots=True)
class QueryTerm:
    text: str
    normalized: str
    quoted: bool = False
    negated: bool = False


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    raw: str
    free_terms: tuple[QueryTerm, ...]
    anki_filters: tuple[str, ...]
    anki_filter_query: str
    warnings: tuple[str, ...] = ()
    native_search_required: bool = False

    @property
    def requires_anki_filter(self) -> bool:
        return bool(self.anki_filter_query)

    @property
    def positive_terms(self) -> tuple[QueryTerm, ...]:
        return tuple(term for term in self.free_terms if not term.negated)

    @property
    def negative_terms(self) -> tuple[QueryTerm, ...]:
        return tuple(term for term in self.free_terms if term.negated)


@dataclass(frozen=True, slots=True)
class Correction:
    original: str
    corrected: str
    distance: int
    confidence: float
    automatic: bool
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AliasExpansion:
    original: str
    expanded: str
    direction: str


class MatchKind(str, Enum):
    EXACT_PHRASE = "exact_phrase"
    EXACT_TERM = "exact_term"
    LEXICAL = "lexical"
    PROXIMITY = "proximity"
    TYPO = "typo"
    ALIAS = "alias"
    SEMANTIC = "semantic"


@dataclass(frozen=True, slots=True)
class MatchReason:
    kind: MatchKind
    label: str
    detail: str = ""
    weight: float = 0.0


@dataclass(frozen=True, slots=True)
class SearchResult:
    note_id: int
    card_ids: tuple[int, ...]
    title: str
    snippet: str
    decks: tuple[str, ...]
    note_type: str
    tags: tuple[str, ...]
    fields: Mapping[str, str]
    score: float
    reasons: tuple[MatchReason, ...]

    @property
    def deck(self) -> str:
        return self.decks[0] if self.decks else ""

    @property
    def badges(self) -> tuple[str, ...]:
        return tuple(reason.label for reason in self.reasons)


@dataclass(frozen=True, slots=True)
class SearchResponse:
    results: tuple[SearchResult, ...]
    parsed_query: ParsedQuery
    corrections: tuple[Correction, ...] = ()
    alias_expansions: tuple[AliasExpansion, ...] = ()
    total_candidates: int = 0
    elapsed_ms: float = 0.0
    warnings: tuple[str, ...] = ()
    filters_applied: bool = True

    @property
    def total(self) -> int:
        return len(self.results)


@dataclass(frozen=True, slots=True)
class IndexStats:
    note_count: int
    vocabulary_size: int
    alias_count: int
    database_bytes: int
    schema_version: int
    generation: int


@dataclass(slots=True)
class RankedCandidate:
    """Internal mutable accumulator used while fusing ranked lists."""

    note_id: int
    score: float = 0.0
    reasons: list[MatchReason] = field(default_factory=list)
    source_ranks: dict[str, int] = field(default_factory=dict)
    matched_concepts: int = 0
    total_concepts: int = 0
    semantic_similarity: float | None = None
