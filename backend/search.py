"""Explainable lexical, fuzzy, alias, and optional semantic retrieval."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from statistics import median
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol, runtime_checkable

from .fuzzy import Vocabulary
from .index import FTSDocumentHit, IndexedDocument, SmartSearchIndex
from .models import (
    AliasExpansion,
    Correction,
    MatchKind,
    MatchReason,
    ParsedQuery,
    RankedCandidate,
    SearchResponse,
    SearchResult,
)
from .query import QueryParser
from .text import make_snippet, normalize_text, tokenize


@dataclass(frozen=True, slots=True)
class SemanticHit:
    note_id: int
    score: float
    reason: str = "Semantic match"


@runtime_checkable
class SemanticProvider(Protocol):
    """Structural interface implemented by ``semantic.SemanticService``."""

    def search(
        self,
        query: str,
        *,
        limit: int = 50,
        allowed_note_ids: set[int] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> Sequence[SemanticHit]: ...


@runtime_checkable
class FilterResolver(Protocol):
    """Legacy note-ID adapter for non-UI embedding of the search engine."""

    def __call__(self, anki_query: str) -> Iterable[int]: ...


class SearchEngine:
    def __init__(
        self,
        index: SmartSearchIndex,
        *,
        semantic_provider: SemanticProvider | None = None,
        filter_resolver: FilterResolver | None = None,
    ) -> None:
        self.index = index
        self.semantic_provider = semantic_provider
        self.filter_resolver = filter_resolver
        self._vocabulary: Vocabulary | None = None
        self._vocabulary_generation = -1
        self._vocabulary_refresh_deferred = False

    def warmup(self) -> dict[str, int]:
        """Build fuzzy lookup structures before the first interactive query."""

        vocabulary = self._ensure_vocabulary()
        return {
            "generation": self._vocabulary_generation,
            "terms": len(vocabulary.frequencies),
            "trusted_terms": len(vocabulary.trusted_terms),
        }

    def defer_vocabulary_refresh(self) -> None:
        """Keep the current fuzzy vocabulary usable during a background delta.

        Exact/FTS matches are updated atomically by :class:`SmartSearchIndex`.
        Rebuilding the large spelling vocabulary can take noticeably longer
        than changing one note, so the integration builds a replacement engine
        off-thread and briefly lets in-flight searches use the previous fuzzy
        vocabulary.  The replacement is swapped in as soon as it is warm.
        """

        if self._vocabulary is not None:
            self._vocabulary_refresh_deferred = True

    def search(
        self,
        raw_query: str,
        *,
        limit: int = 50,
        allowed_note_ids: Iterable[int] | None = None,
        mode: str = "smart",
        cancel_check: Callable[[], None] | None = None,
    ) -> SearchResponse:
        started = time.perf_counter()
        checkpoint = cancel_check or (lambda: None)
        checkpoint()
        normalized_mode = str(mode).casefold()
        if normalized_mode not in {"smart", "exact", "semantic"}:
            raise ValueError("mode must be 'smart', 'exact', or 'semantic'")
        limit = min(500, max(1, int(limit)))
        parser = QueryParser(field_names=self.index.field_names())
        parsed = parser.parse(raw_query)
        checkpoint()
        warnings = list(parsed.warnings)

        allowed: set[int] | None = (
            {int(note_id) for note_id in allowed_note_ids}
            if allowed_note_ids is not None
            else None
        )
        filters_applied = not parsed.requires_anki_filter
        if parsed.requires_anki_filter:
            if allowed is not None:
                filters_applied = True
            elif self.filter_resolver is not None:
                try:
                    allowed = {
                        int(note_id)
                        for note_id in self.filter_resolver(parsed.anki_filter_query)
                    }
                    filters_applied = True
                except Exception as error:
                    warnings.append(f"Anki could not resolve the filters: {error}")
            if not filters_applied:
                warnings.append(
                    "Anki filters were parsed but not applied. Pass allowed_note_ids "
                    "or configure a read-only filter_resolver."
                )
                return self._response(
                    parsed,
                    started,
                    warnings=warnings,
                    filters_applied=False,
                )
        if allowed is not None and not allowed:
            return self._response(
                parsed,
                started,
                warnings=warnings,
                filters_applied=filters_applied,
            )

        if not parsed.positive_terms:
            if parsed.requires_anki_filter and filters_applied:
                documents = self.index.documents(allowed)
                if parsed.negative_terms:
                    documents = tuple(
                        document
                        for document in documents
                        if not _contains_negative(document, parsed)
                    )
                total = len(documents)
                results = tuple(
                    _result_for_filter(document) for document in documents[:limit]
                )
                return self._response(
                    parsed,
                    started,
                    results=results,
                    total_candidates=total,
                    warnings=warnings,
                    filters_applied=True,
                )
            return self._response(parsed, started, warnings=warnings)

        positive_terms = parsed.positive_terms
        corrections: list[Correction] = []
        automatic: dict[str, str] = {}
        effective_terms = positive_terms
        alias_expansions: list[AliasExpansion] = []
        if normalized_mode in {"smart", "semantic"}:
            vocabulary = self._ensure_vocabulary()
            checkpoint()
            corrections = self._corrections(
                parsed,
                vocabulary,
                cancel_check=checkpoint,
            )
            automatic = {
                normalize_text(correction.original): correction.corrected
                for correction in corrections
                if correction.automatic
            }
            if automatic:
                effective_terms = _replace_terms(positive_terms, automatic)
            if normalized_mode == "smart":
                alias_expansions = self._aliases(effective_terms, vocabulary)
            checkpoint()

        ranked: dict[int, RankedCandidate] = {}
        documents: dict[int, IndexedDocument] = {}
        source_hits: list[tuple[str, Sequence[FTSDocumentHit], float, MatchReason]] = []

        if normalized_mode != "semantic":
            literal_strict = _fts_expression(positive_terms, joiner="AND")
            if literal_strict:
                source_hits.append(
                    (
                        "literal_strict",
                        self.index.search_fts(
                            literal_strict,
                            limit=max(250, limit * 5),
                            allowed_note_ids=allowed,
                            cancel_check=checkpoint,
                        ),
                        4.0,
                        MatchReason(
                            MatchKind.LEXICAL,
                            "Strong text match",
                            "All query terms matched the local FTS index.",
                            4.0,
                        ),
                    )
                )
            if len(positive_terms) > 1:
                literal_broad = _fts_expression(positive_terms, joiner="OR")
                source_hits.append(
                    (
                        "literal_broad",
                        self.index.search_fts(
                            literal_broad,
                            limit=max(300, limit * 6),
                            allowed_note_ids=allowed,
                            cancel_check=checkpoint,
                        ),
                        1.0,
                        MatchReason(
                            MatchKind.LEXICAL,
                            "Related text",
                            "At least one query term matched.",
                            1.0,
                        ),
                    )
                )

            if automatic:
                corrected_expression = _fts_expression(effective_terms, joiner="AND")
                source_hits.append(
                    (
                        "corrected",
                        self.index.search_fts(
                            corrected_expression,
                            limit=max(250, limit * 5),
                            allowed_note_ids=allowed,
                            cancel_check=checkpoint,
                        ),
                        2.8,
                        MatchReason(
                            MatchKind.TYPO,
                            "Spelling correction",
                            ", ".join(
                                f"{correction.original} → {correction.corrected}"
                                for correction in corrections
                                if correction.automatic
                            ),
                            2.8,
                        ),
                    )
                )

            if alias_expansions:
                alias_expression = _alias_fts_expression(
                    effective_terms, alias_expansions
                )
                source_hits.append(
                    (
                        "alias",
                        self.index.search_fts(
                            alias_expression,
                            limit=max(250, limit * 5),
                            allowed_note_ids=allowed,
                            cancel_check=checkpoint,
                        ),
                        2.4,
                        MatchReason(
                            MatchKind.ALIAS,
                            "Medical alias",
                            ", ".join(
                                f"{expansion.original} ↔ {expansion.expanded}"
                                for expansion in alias_expansions[:4]
                            ),
                            2.4,
                        ),
                    )
                )

        for source_name, hits, source_weight, reason in source_hits:
            checkpoint()
            for index, hit in enumerate(hits, start=1):
                if index % 64 == 0:
                    checkpoint()
                if _contains_negative(hit.document, parsed):
                    continue
                documents[hit.document.note_id] = hit.document
                candidate = ranked.setdefault(
                    hit.document.note_id, RankedCandidate(hit.document.note_id)
                )
                candidate.score += source_weight / (60.0 + hit.rank)
                candidate.source_ranks[source_name] = hit.rank
                _add_reason(candidate, reason)

        semantic_query = " ".join(term.text for term in effective_terms)
        if normalized_mode == "semantic" and self.semantic_provider is not None:
            checkpoint()
            try:
                semantic_hits = self.semantic_provider.search(
                    semantic_query,
                    limit=max(100, limit * 3),
                    allowed_note_ids=allowed,
                    cancel_check=checkpoint,
                )
            except Exception as error:
                semantic_hits = ()
                warnings.append(f"Semantic search is temporarily unavailable: {error}")
            checkpoint()
            missing_ids = [
                int(hit.note_id)
                for hit in semantic_hits
                if int(hit.note_id) not in documents
            ]
            documents.update(self.index.get_documents(missing_ids))
            for rank, hit in enumerate(semantic_hits, start=1):
                if rank % 64 == 0:
                    checkpoint()
                document = documents.get(int(hit.note_id))
                if document is None or _contains_negative(document, parsed):
                    continue
                candidate = ranked.setdefault(
                    document.note_id, RankedCandidate(document.note_id)
                )
                candidate.score += 1.35 / (60.0 + rank)
                candidate.score += max(0.0, min(1.0, float(hit.score))) * 0.006
                candidate.source_ranks["semantic"] = rank
                candidate.semantic_similarity = float(hit.score)
                _add_reason(
                    candidate,
                    MatchReason(
                        MatchKind.SEMANTIC,
                        "Concept match",
                        getattr(hit, "reason", "Semantically similar medical content."),
                        float(hit.score),
                    ),
                )
        elif normalized_mode == "semantic":
            warnings.append("Semantic search is not configured.")

        all_needles = [term.normalized for term in positive_terms]
        all_needles.extend(
            correction.corrected for correction in corrections if correction.automatic
        )
        all_needles.extend(expansion.expanded for expansion in alias_expansions)
        for index, candidate in enumerate(ranked.values(), start=1):
            if index % 32 == 0:
                checkpoint()
            document = documents[candidate.note_id]
            _apply_explainable_boosts(
                candidate,
                document,
                parsed,
                coverage_terms=effective_terms,
                alias_expansions=alias_expansions,
            )

        checkpoint()
        ordered = sorted(
            ranked.values(), key=lambda candidate: (-candidate.score, candidate.note_id)
        )
        relevant = _adaptive_relevance_cutoff(
            ordered,
            mode=normalized_mode,
        )
        results = tuple(
            _to_search_result(
                candidate,
                documents[candidate.note_id],
                all_needles,
            )
            for candidate in relevant[:limit]
        )
        return self._response(
            parsed,
            started,
            results=results,
            corrections=tuple(corrections),
            alias_expansions=tuple(alias_expansions),
            total_candidates=len(relevant),
            warnings=warnings,
            filters_applied=filters_applied,
        )

    def _response(
        self,
        parsed: ParsedQuery,
        started: float,
        *,
        results: tuple[SearchResult, ...] = (),
        corrections: tuple[Correction, ...] = (),
        alias_expansions: tuple[AliasExpansion, ...] = (),
        total_candidates: int = 0,
        warnings: Sequence[str] = (),
        filters_applied: bool = True,
    ) -> SearchResponse:
        return SearchResponse(
            results=results,
            parsed_query=parsed,
            corrections=corrections,
            alias_expansions=alias_expansions,
            total_candidates=total_candidates,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            warnings=tuple(dict.fromkeys(warnings)),
            filters_applied=filters_applied,
        )

    def _ensure_vocabulary(self) -> Vocabulary:
        generation = self.index.generation
        if self._vocabulary_refresh_deferred and self._vocabulary is not None:
            return self._vocabulary
        if self._vocabulary is None or generation != self._vocabulary_generation:
            self._vocabulary = Vocabulary(
                self.index.vocabulary_entries(),
                self.index.alias_map(),
            )
            self._vocabulary_generation = generation
        return self._vocabulary

    @staticmethod
    def _corrections(
        parsed: ParsedQuery,
        vocabulary: Vocabulary,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[Correction]:
        corrections: list[Correction] = []
        seen: set[str] = set()
        for term in parsed.positive_terms:
            if cancel_check is not None:
                cancel_check()
            if term.quoted:
                continue
            for token in tokenize(term.normalized):
                if token in seen:
                    continue
                seen.add(token)
                correction = vocabulary.correction(
                    token,
                    cancel_check=cancel_check,
                )
                if correction:
                    corrections.append(correction)
        return corrections

    @staticmethod
    def _aliases(
        terms: Sequence, vocabulary: Vocabulary
    ) -> list[AliasExpansion]:
        expansions: list[AliasExpansion] = []
        seen: set[tuple[str, str]] = set()
        for term in terms:
            values = (term.normalized,) + tokenize(term.normalized)
            for value in values:
                for expansion in vocabulary.expand_aliases(value):
                    key = (normalize_text(expansion.original), expansion.expanded)
                    if key not in seen:
                        seen.add(key)
                        expansions.append(expansion)
        return expansions


def _fts_expression(terms: Sequence, *, joiner: str) -> str:
    clauses = [_fts_clause(term.normalized, phrase=term.quoted) for term in terms]
    clauses = [clause for clause in clauses if clause]
    return f" {joiner} ".join(clauses)


def _fts_clause(value: str, *, phrase: bool = False) -> str:
    tokens = tokenize(value)
    if not tokens:
        return ""
    if phrase or len(tokens) > 1:
        return _fts_quote(" ".join(tokens))
    return _fts_quote(tokens[0])


def _fts_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _replace_terms(terms: Sequence, replacements: dict[str, str]) -> tuple:
    from .models import QueryTerm

    replaced: list[QueryTerm] = []
    for term in terms:
        tokens = list(tokenize(term.normalized))
        updated = [replacements.get(token, token) for token in tokens]
        replaced.append(
            QueryTerm(
                text=" ".join(updated),
                normalized=" ".join(updated),
                quoted=term.quoted,
                negated=False,
            )
        )
    return tuple(replaced)


def _alias_fts_expression(
    terms: Sequence,
    expansions: Sequence[AliasExpansion],
) -> str:
    by_original: dict[str, list[str]] = defaultdict(list)
    for expansion in expansions:
        by_original[normalize_text(expansion.original)].append(expansion.expanded)
    clauses: list[str] = []
    for term in terms:
        original = _fts_clause(term.normalized, phrase=term.quoted)
        expanded = [
            _fts_clause(value, phrase=" " in value)
            for value in by_original.get(term.normalized, ())
        ]
        expanded = [value for value in expanded if value]
        if expanded:
            clauses.append("(" + " OR ".join([original, *expanded]) + ")")
        else:
            clauses.append(original)
    return " AND ".join(clause for clause in clauses if clause)


def _contains_negative(document: IndexedDocument, parsed: ParsedQuery) -> bool:
    document_tokens = set(tokenize(document.normalized_text, already_normalized=True))
    for term in parsed.negative_terms:
        term_tokens = tokenize(term.normalized)
        if term.quoted or len(term_tokens) > 1:
            if term.normalized in document.normalized_text:
                return True
        elif term_tokens and term_tokens[0] in document_tokens:
            return True
    return False


def _apply_explainable_boosts(
    candidate: RankedCandidate,
    document: IndexedDocument,
    parsed: ParsedQuery,
    *,
    coverage_terms: Sequence,
    alias_expansions: Sequence[AliasExpansion],
) -> None:
    positive = parsed.positive_terms
    document_tokens = tokenize(document.normalized_text, already_normalized=True)
    token_set = set(document_tokens)
    concepts = _query_concept_groups(coverage_terms, alias_expansions)
    candidate.total_concepts = len(concepts)
    candidate.matched_concepts = sum(
        1
        for alternatives in concepts
        if any(
            (
                concept in document.normalized_text
                if phrase
                else all(token in token_set for token in tokenize(concept))
            )
            for concept, phrase in alternatives
        )
    )
    exact_phrases = [
        term
        for term in positive
        if term.quoted and term.normalized in document.normalized_text
    ]
    if exact_phrases:
        candidate.score += 0.055
        _add_reason(
            candidate,
            MatchReason(
                MatchKind.EXACT_PHRASE,
                "Exact phrase",
                ", ".join(f"“{term.text}”" for term in exact_phrases[:3]),
                0.055,
            ),
        )
    query_tokens = [
        token
        for term in positive
        for token in tokenize(term.normalized)
    ]
    if query_tokens and all(token in token_set for token in query_tokens):
        candidate.score += 0.035
        _add_reason(
            candidate,
            MatchReason(
                MatchKind.EXACT_TERM,
                "All terms present",
                "Every normalized query term occurs in this note.",
                0.035,
            ),
        )
        span = _minimum_cover_span(document_tokens, query_tokens)
        if span is not None and len(set(query_tokens)) > 1:
            proximity = 0.018 / max(1.0, math.log2(span + 1))
            candidate.score += proximity
            _add_reason(
                candidate,
                MatchReason(
                    MatchKind.PROXIMITY,
                    "Nearby terms",
                    f"Query terms occur within a {span}-word span.",
                    proximity,
                ),
            )
    normalized_title = normalize_text(document.title)
    if query_tokens and all(token in set(tokenize(normalized_title)) for token in query_tokens):
        candidate.score += 0.02


def _query_concept_groups(
    terms: Sequence,
    alias_expansions: Sequence[AliasExpansion],
) -> tuple[tuple[tuple[str, bool], ...], ...]:
    aliases_by_original: dict[str, set[str]] = defaultdict(set)
    for expansion in alias_expansions:
        aliases_by_original[normalize_text(expansion.original)].add(
            normalize_text(expansion.expanded)
        )

    concepts: list[tuple[tuple[str, bool], ...]] = []
    for term in terms:
        tokens = tokenize(term.normalized)
        if not tokens:
            continue
        normalized = " ".join(tokens)
        alternatives: list[tuple[str, bool]] = [
            (normalized, bool(term.quoted or len(tokens) > 1))
        ]
        for alias in sorted(aliases_by_original.get(term.normalized, ())):
            alias_tokens = tokenize(alias)
            if alias_tokens:
                alternatives.append(
                    (" ".join(alias_tokens), len(alias_tokens) > 1)
                )
        concepts.append(tuple(dict.fromkeys(alternatives)))
    return tuple(concepts)


def _adaptive_relevance_cutoff(
    ordered: Sequence[RankedCandidate],
    *,
    mode: str,
) -> tuple[RankedCandidate, ...]:
    """Remove the low-relevance tail before applying the display maximum.

    Smart mode uses query-concept coverage because reciprocal-rank scores from
    typo and alias sources are not directly comparable with broad OR matches.
    Semantic mode uses the retrieved cosine distribution, which adapts to both
    the query and the profile instead of relying on one brittle fixed score.
    """

    candidates = tuple(ordered)
    if not candidates:
        return ()

    if mode == "semantic":
        scored = [
            candidate
            for candidate in candidates
            if candidate.semantic_similarity is not None
        ]
        # Small filtered collections do not contain enough of a distribution
        # to estimate a trustworthy tail.
        if len(scored) <= 12:
            return candidates
        similarities = [
            float(candidate.semantic_similarity) for candidate in scored
        ]
        top = max(similarities)
        baseline = float(median(similarities))
        threshold = baseline + 0.30 * (top - baseline)
        exact_kinds = {MatchKind.EXACT_PHRASE, MatchKind.EXACT_TERM}
        return tuple(
            candidate
            for candidate in candidates
            if (
                candidate.semantic_similarity is not None
                and float(candidate.semantic_similarity) >= threshold
            )
            or any(reason.kind in exact_kinds for reason in candidate.reasons)
        )

    if mode != "smart":
        return candidates

    best_coverage = max(candidate.matched_concepts for candidate in candidates)
    coverage_floor = best_coverage - 1 if best_coverage >= 4 else best_coverage
    protected_sources = {"literal_strict", "corrected", "alias"}
    return tuple(
        candidate
        for candidate in candidates
        if candidate.matched_concepts >= coverage_floor
        or bool(protected_sources.intersection(candidate.source_ranks))
    )


def _minimum_cover_span(
    document_tokens: Sequence[str], query_tokens: Sequence[str]
) -> int | None:
    required = Counter(query_tokens)
    present: Counter[str] = Counter()
    satisfied = 0
    needed = len(required)
    left = 0
    best: int | None = None
    for right, token in enumerate(document_tokens):
        if token in required:
            present[token] += 1
            if present[token] == required[token]:
                satisfied += 1
        while satisfied == needed and left <= right:
            best = min(best or (right - left + 1), right - left + 1)
            left_token = document_tokens[left]
            if left_token in required:
                if present[left_token] == required[left_token]:
                    satisfied -= 1
                present[left_token] -= 1
            left += 1
    return best


def _add_reason(candidate: RankedCandidate, reason: MatchReason) -> None:
    expansion_reason = reason.kind in {MatchKind.TYPO, MatchKind.ALIAS}
    if expansion_reason and (
        "literal_strict" in candidate.source_ranks
        or any(
            existing.kind in {MatchKind.EXACT_TERM, MatchKind.EXACT_PHRASE}
            for existing in candidate.reasons
        )
    ):
        # The expansion may still improve the fused score, but an exact literal
        # result should not be mislabeled as though it required correction. A
        # broad OR hit may have matched only another term, so it does not count.
        return
    if reason.kind == MatchKind.ALIAS and any(
        existing.kind == MatchKind.TYPO for existing in candidate.reasons
    ):
        # Alias FTS includes the corrected term as well as its expansions. A
        # card that matched that corrected term needed spelling recovery, but
        # did not need a medical alias.
        return
    if all(existing.kind != reason.kind for existing in candidate.reasons):
        candidate.reasons.append(reason)


def _to_search_result(
    candidate: RankedCandidate,
    document: IndexedDocument,
    needles: Iterable[str],
) -> SearchResult:
    reasons = tuple(
        sorted(candidate.reasons, key=lambda reason: (-reason.weight, reason.kind.value))
    )
    return SearchResult(
        note_id=document.note_id,
        card_ids=document.card_ids,
        title=document.title,
        snippet=make_snippet(document.plain_text, needles),
        decks=document.decks,
        note_type=document.note_type,
        tags=document.tags,
        fields=document.fields,
        score=round(candidate.score, 8),
        reasons=reasons,
    )


def _result_for_filter(document: IndexedDocument) -> SearchResult:
    reason = MatchReason(
        MatchKind.LEXICAL,
        "Anki filter",
        "Matched the structured Anki filters.",
        1.0,
    )
    return SearchResult(
        note_id=document.note_id,
        card_ids=document.card_ids,
        title=document.title,
        snippet=make_snippet(document.plain_text, ()),
        decks=document.decks,
        note_type=document.note_type,
        tags=document.tags,
        fields=document.fields,
        score=1.0,
        reasons=(reason,),
    )
