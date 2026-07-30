"""Collection-safe search backend for the Anki Smart Search add-on.

The package deliberately has no import-time dependency on :mod:`anki` or
:mod:`aqt`, which keeps it testable outside Anki and prevents accidental
collection access.  ``anki_reader`` is the only module that knows about the
shape of Anki's public read APIs.
"""

from .fuzzy import DamerauLevenshtein, Vocabulary
from .index import FTS5Unavailable, SmartSearchIndex, UnsafeIndexPath
from .medical_vocab import MedicalVocabularyError, load_alias_resource
from .models import (
    AliasExpansion,
    Correction,
    IndexStats,
    IndexedNote,
    MatchReason,
    ParsedQuery,
    QueryTerm,
    SearchResponse,
    SearchResult,
)
from .query import QueryParser
from .search import SearchEngine, SemanticHit, SemanticProvider

__all__ = [
    "AliasExpansion",
    "Correction",
    "DamerauLevenshtein",
    "FTS5Unavailable",
    "IndexStats",
    "IndexedNote",
    "MatchReason",
    "MedicalVocabularyError",
    "ParsedQuery",
    "QueryParser",
    "QueryTerm",
    "SearchEngine",
    "SearchResponse",
    "SearchResult",
    "SemanticHit",
    "SemanticProvider",
    "SmartSearchIndex",
    "UnsafeIndexPath",
    "Vocabulary",
    "load_alias_resource",
]
