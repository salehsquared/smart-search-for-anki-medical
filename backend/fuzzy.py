"""Dependency-free spelling correction and medical alias expansion."""

from __future__ import annotations

from collections import defaultdict
import math
from collections.abc import Callable, Iterable, Mapping

from .compat import dataclass, int_bit_count
from .models import AliasExpansion, Correction
from .text import normalize_text, tokenize


class DamerauLevenshtein:
    """True Damerau-Levenshtein distance with adjacent transpositions."""

    @staticmethod
    def distance(left: str, right: str, max_distance: int | None = None) -> int:
        left = normalize_text(left)
        right = normalize_text(right)
        if left == right:
            return 0
        if not left:
            return len(right)
        if not right:
            return len(left)
        if max_distance is not None and abs(len(left) - len(right)) > max_distance:
            return max_distance + 1

        # Lowrance-Wagner algorithm. Unlike optimal-string-alignment distance,
        # it correctly handles a substring participating in multiple edits.
        maximum = len(left) + len(right)
        matrix = [
            [0 for _ in range(len(right) + 2)] for _ in range(len(left) + 2)
        ]
        matrix[0][0] = maximum
        for i in range(len(left) + 1):
            matrix[i + 1][0] = maximum
            matrix[i + 1][1] = i
        for j in range(len(right) + 1):
            matrix[0][j + 1] = maximum
            matrix[1][j + 1] = j

        last_row: dict[str, int] = defaultdict(int)
        for i, left_character in enumerate(left, start=1):
            last_match_column = 0
            row_minimum = maximum
            for j, right_character in enumerate(right, start=1):
                i1 = last_row[right_character]
                j1 = last_match_column
                substitution_cost = 0 if left_character == right_character else 1
                if substitution_cost == 0:
                    last_match_column = j
                matrix[i + 1][j + 1] = min(
                    matrix[i][j] + substitution_cost,
                    matrix[i + 1][j] + 1,
                    matrix[i][j + 1] + 1,
                    matrix[i1][j1]
                    + (i - i1 - 1)
                    + 1
                    + (j - j1 - 1),
                )
                row_minimum = min(row_minimum, matrix[i + 1][j + 1])
            last_row[left_character] = i
            # This cutoff is conservative. Earlier rows can improve by at most
            # the number of source characters still unprocessed.
            if (
                max_distance is not None
                and row_minimum - (len(left) - i) > max_distance
            ):
                return max_distance + 1
        result = matrix[len(left) + 1][len(right) + 1]
        if max_distance is not None and result > max_distance:
            return max_distance + 1
        return result


@dataclass(frozen=True, slots=True)
class VocabularySuggestion:
    term: str
    distance: int
    confidence: float
    frequency: int
    trusted: bool = False


class Vocabulary:
    """Frequency-aware, memory-bounded spelling vocabulary.

    Terms are bucketed by length and prefiltered with a compact character mask;
    every remaining candidate is verified with exact Damerau-Levenshtein
    distance.  This avoids the hundreds of megabytes a Python SymSpell delete
    map can consume for a large AnKing vocabulary.
    """

    def __init__(
        self,
        entries: Iterable[tuple[str, int]] = (),
        aliases: Mapping[str, Iterable[str]] | None = None,
        *,
        max_delete_distance: int = 2,
    ) -> None:
        self.max_delete_distance = max(1, int(max_delete_distance))
        self.frequencies: dict[str, int] = {}
        self._length_buckets: dict[int, list[str]] = defaultdict(list)
        self._character_masks: dict[str, int] = {}
        self.aliases: dict[str, set[str]] = defaultdict(set)
        self.reverse_aliases: dict[str, set[str]] = defaultdict(set)
        self.trusted_terms: set[str] = set()
        for term, frequency in entries:
            self.add(term, frequency)
        if aliases:
            for alias, canonicals in aliases.items():
                for canonical in canonicals:
                    self.add_alias(alias, canonical)

    def add(self, term: str, frequency: int = 1) -> None:
        tokens = tokenize(term)
        if len(tokens) != 1:
            return
        normalized = tokens[0]
        frequency = max(1, int(frequency))
        previous = self.frequencies.get(normalized)
        self.frequencies[normalized] = max(frequency, previous or 0)
        if previous is None:
            self._length_buckets[len(normalized)].append(normalized)
            self._character_masks[normalized] = _character_mask(normalized)

    def add_alias(self, alias: str, canonical: str) -> None:
        normalized_alias = normalize_text(alias)
        normalized_canonical = normalize_text(canonical)
        if (
            not normalized_alias
            or not normalized_canonical
            or normalized_alias == normalized_canonical
        ):
            return
        self.aliases[normalized_alias].add(normalized_canonical)
        self.reverse_aliases[normalized_canonical].add(normalized_alias)
        for token in tokenize(normalized_alias):
            self.add(token)
            self.trusted_terms.add(token)
        for token in tokenize(normalized_canonical):
            self.add(token)
            self.trusted_terms.add(token)

    def contains(self, term: str) -> bool:
        normalized_tokens = tokenize(term)
        return len(normalized_tokens) == 1 and normalized_tokens[0] in self.frequencies

    def suggest(
        self,
        term: str,
        *,
        limit: int = 5,
        cancel_check: Callable[[], None] | None = None,
    ) -> tuple[VocabularySuggestion, ...]:
        tokens = tokenize(term)
        if len(tokens) != 1:
            return ()
        query = tokens[0]
        maximum = _maximum_distance(query)
        query_frequency = self.frequencies.get(query, 0)
        if maximum <= 0 or query in self.trusted_terms:
            return ()

        candidates: list[str] = []
        query_mask = _character_mask(query)
        examined = 0
        for length in range(max(1, len(query) - maximum), len(query) + maximum + 1):
            for candidate in self._length_buckets.get(length, ()):
                examined += 1
                if cancel_check is not None and examined % 256 == 0:
                    cancel_check()
                # One substitution can add and remove one distinct character;
                # insertion/deletion changes at most one. Modulo collisions
                # cause only extra work, never false negatives.
                if int_bit_count(
                    query_mask ^ self._character_masks[candidate]
                ) <= maximum * 2:
                    candidates.append(candidate)
        suggestions: list[VocabularySuggestion] = []
        for index, candidate in enumerate(candidates, start=1):
            if cancel_check is not None and index % 128 == 0:
                cancel_check()
            if candidate == query:
                continue
            if abs(len(query) - len(candidate)) > maximum:
                continue
            distance = DamerauLevenshtein.distance(
                query, candidate, max_distance=maximum
            )
            if distance > maximum:
                continue
            similarity = 1.0 - (distance / max(len(query), len(candidate), 1))
            frequency = self.frequencies.get(candidate, 1)
            frequency_bonus = min(0.035, math.log10(frequency + 1) * 0.008)
            trusted = candidate in self.trusted_terms
            # Imported decks can contain the same misspelling several times.
            # For such an observed token, only consider a trusted medical term
            # with a decisive corpus-frequency advantage. This recovers common
            # errors like `buproprion` without rewriting arbitrary valid words.
            if query_frequency > 1 and (
                not trusted or frequency < query_frequency * 4
            ):
                continue
            suggestions.append(
                VocabularySuggestion(
                    term=candidate,
                    distance=distance,
                    confidence=min(
                        0.999,
                        similarity + frequency_bonus + (0.012 if trusted else 0.0),
                    ),
                    frequency=frequency,
                    trusted=trusted,
                )
            )
        suggestions.sort(
            key=lambda suggestion: (
                suggestion.distance,
                -int(suggestion.trusted),
                -suggestion.confidence,
                -suggestion.frequency,
                suggestion.term,
            )
        )
        return tuple(suggestions[: max(1, limit)])

    def correction(
        self,
        term: str,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> Correction | None:
        suggestions = self.suggest(term, cancel_check=cancel_check)
        if not suggestions:
            return None
        best = suggestions[0]
        second = suggestions[1] if len(suggestions) > 1 else None
        margin = (
            best.confidence - second.confidence
            if second and second.distance == best.distance
            else 1.0
        )
        normalized = normalize_text(term)
        rare_untrusted_exact = (
            self.frequencies.get(normalized) == 1
            and normalized not in self.trusted_terms
        )
        observed_untrusted_exact = (
            self.frequencies.get(normalized, 0) > 1
            and normalized not in self.trusted_terms
        )
        high_confidence = (
            best.distance == 1
            and len(normalized) >= 6
            and (margin >= 0.012 or best.frequency >= (second.frequency * 5 if second else 1))
        ) or (
            best.distance == 2
            and len(normalized) >= 9
            and best.confidence >= 0.84
            and margin >= 0.02
        )
        if rare_untrusted_exact and not best.trusted:
            high_confidence = False
        if observed_untrusted_exact:
            high_confidence = (
                best.trusted
                and best.distance == 1
                and best.frequency >= self.frequencies[normalized] * 4
                and len(normalized) >= 6
            )
        return Correction(
            original=term,
            corrected=best.term,
            distance=best.distance,
            confidence=best.confidence,
            automatic=high_confidence,
            alternatives=tuple(suggestion.term for suggestion in suggestions[1:4]),
        )

    def expand_aliases(self, value: str) -> tuple[AliasExpansion, ...]:
        normalized = normalize_text(value)
        expansions: list[AliasExpansion] = []
        for canonical in sorted(self.aliases.get(normalized, ())):
            expansions.append(
                AliasExpansion(
                    original=value,
                    expanded=canonical,
                    direction="alias_to_canonical",
                )
            )
        for alias in sorted(self.reverse_aliases.get(normalized, ())):
            alias_tokens = tokenize(alias)
            if len(alias_tokens) > 1:
                # Reverse suggestions stay concise and never invent a broad
                # first-word alias such as "plan" from "Plan B". Full product
                # names remain in the forward map, so entering one still finds
                # its ingredient.
                continue
            expansions.append(
                AliasExpansion(
                    original=value,
                    expanded=alias,
                    direction="canonical_to_alias",
                )
            )
        return tuple(expansions)


def _maximum_distance(term: str) -> int:
    length = len(term)
    if length <= 3:
        return 0
    if length <= 5:
        return 1
    if length <= 8:
        return 2
    return 2


def _character_mask(term: str) -> int:
    mask = 0
    for character in term:
        mask |= 1 << (ord(character) % 64)
    return mask
