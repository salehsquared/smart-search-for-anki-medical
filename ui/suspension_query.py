"""Conservative helpers for the ``is:suspended`` quick filter.

The visible Anki query remains the source of truth.  These helpers only own
plain, unquoted ``is:suspended`` terms that are top-level AND conjuncts.  Any
ambiguous Boolean, quoted, negated, or malformed form is left byte-for-byte
unchanged so the user can edit it directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

try:  # installed add-on package
    from ..backend.query import (
        DEFAULT_FILTER_KEYS,
        strip_incomplete_filter_tokens,
    )
except ImportError:  # plain-Python tests import ``ui`` as a top-level package
    from backend.query import DEFAULT_FILTER_KEYS, strip_incomplete_filter_tokens

from .deck_query import query_expression_is_valid, query_has_quote_adjacency


class SuspensionQueryState(Enum):
    """How safely the quick filter can represent the visible query."""

    OFF = "off"
    ON = "on"
    CUSTOM = "custom"


@dataclass(frozen=True)
class SuspensionQueryAnalysis:
    """Syntax-aware state used by the dialog and the query mutator."""

    state: SuspensionQueryState
    has_top_level_or: bool = False
    reason: str = ""


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    text: str
    depth: int


@dataclass(frozen=True)
class _Scan:
    tokens: tuple[_Token, ...]
    quoted_terms: tuple[_Token, ...]
    malformed: bool


_CUSTOM_REASON = "Edit the suspension filter directly in the search field."


def _is_native_separator(char: str) -> bool:
    # These are the only separators shared by every supported Anki parser.
    return char in {" ", "\u3000"}


def _has_version_dependent_whitespace(query: str) -> bool:
    return any(
        char.isspace() and not _is_native_separator(char)
        for char in query
    )


def _only_native_separators(value: str) -> bool:
    return all(_is_native_separator(char) for char in value)


def _strip_native(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and _is_native_separator(value[start]):
        start += 1
    while end > start and _is_native_separator(value[end - 1]):
        end -= 1
    return value[start:end]


def _scan(query: str) -> _Scan:
    tokens: list[_Token] = []
    quoted_terms: list[_Token] = []
    length = len(query)
    depth = 0
    index = 0
    malformed = False

    while index < length:
        char = query[index]
        if _is_native_separator(char):
            index += 1
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            if depth == 0:
                malformed = True
            else:
                depth -= 1
            index += 1
            continue
        if char == '"':
            quote_start = index
            index += 1
            escaped = False
            while index < length:
                current = query[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
                index += 1
            if index >= length:
                malformed = True
                break
            quoted_terms.append(
                _Token(
                    quote_start,
                    index + 1,
                    query[quote_start : index + 1],
                    depth,
                )
            )
            index += 1
            continue

        start = index
        while index < length:
            current = query[index]
            if current == "\\" and index + 1 < length:
                index += 2
                continue
            if _is_native_separator(current) or current in '()"':
                break
            index += 1
        if index == start:
            # A quote embedded in a native field value is scanned separately;
            # advancing here guarantees progress for every other delimiter.
            index += 1
            continue
        tokens.append(_Token(start, index, query[start:index], depth))

    if depth:
        malformed = True
    return _Scan(tuple(tokens), tuple(quoted_terms), malformed)


def _is_positive(token: str) -> bool:
    key, separator, value = token.partition(":")
    return bool(separator) and key.lower() == "is" and value == "suspended"


def _is_negative(token: str) -> bool:
    return token.startswith("-") and _is_positive(token[1:])


def _looks_like_suspension(token: str) -> bool:
    candidate = token[1:] if token.startswith("-") else token
    key, separator, value = candidate.partition(":")
    return bool(separator) and key.lower() == "is" and value.lower() == "suspended"


def _quoted_suspension(query: str, term: _Token) -> bool:
    text = term.text
    if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
        return False
    if _is_quoted_field_value(query, term.start):
        return False
    content = text[1:-1]
    if content.startswith("-"):
        content = content[1:]
    key, separator, value = content.partition(":")
    return bool(separator) and key.lower() == "is" and value.lower() == "suspended"


def _is_quoted_field_value(query: str, quote_index: int) -> bool:
    """Match Anki's first-colon field-value boundary for adjacent quotes."""

    colon_index = quote_index - 1
    if (
        colon_index < 0
        or query[colon_index] != ":"
        or _is_backslash_escaped(query, colon_index)
    ):
        return False
    node_start = colon_index
    while node_start > 0:
        previous = node_start - 1
        char = query[previous]
        if (
            not _is_backslash_escaped(query, previous)
            and (_is_native_separator(char) or char in '()"')
        ):
            break
        node_start -= 1
    unescaped_colons = sum(
        1
        for index in range(node_start, quote_index)
        if query[index] == ":" and not _is_backslash_escaped(query, index)
    )
    return unescaped_colons == 1


def _is_backslash_escaped(query: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and query[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def analyze_suspension_query(query: str) -> SuspensionQueryAnalysis:
    """Return OFF, ON, or fail-closed CUSTOM for the visible query."""

    text = str(query)
    if _has_version_dependent_whitespace(text):
        return SuspensionQueryAnalysis(
            SuspensionQueryState.CUSTOM,
            reason=_CUSTOM_REASON,
        )
    _executable, incomplete = strip_incomplete_filter_tokens(
        text,
        filter_keys=DEFAULT_FILTER_KEYS | {"notetype"},
    )
    if (
        incomplete
        or query_has_quote_adjacency(text)
        or not query_expression_is_valid(text)
    ):
        return SuspensionQueryAnalysis(
            SuspensionQueryState.CUSTOM,
            reason=_CUSTOM_REASON,
        )
    scan = _scan(text)
    if scan.malformed:
        return SuspensionQueryAnalysis(
            SuspensionQueryState.CUSTOM,
            reason=_CUSTOM_REASON,
        )
    if any(_quoted_suspension(text, term) for term in scan.quoted_terms):
        return SuspensionQueryAnalysis(
            SuspensionQueryState.CUSTOM,
            reason=_CUSTOM_REASON,
        )

    top_level_or = any(
        token.depth == 0 and token.text.upper() == "OR"
        for token in scan.tokens
    )
    positives: list[_Token] = []
    for index, token in enumerate(scan.tokens):
        if token.text.lower() in {"is:", "-is:"}:
            return SuspensionQueryAnalysis(
                SuspensionQueryState.CUSTOM,
                top_level_or,
                _CUSTOM_REASON,
            )
        if _is_negative(token.text) or (
            _looks_like_suspension(token.text) and not _is_positive(token.text)
        ):
            return SuspensionQueryAnalysis(
                SuspensionQueryState.CUSTOM,
                top_level_or,
                _CUSTOM_REASON,
            )
        if _is_positive(token.text):
            if index and scan.tokens[index - 1].text == "-":
                previous = scan.tokens[index - 1]
                if (
                    previous.depth == token.depth
                    and _only_native_separators(
                        text[previous.end : token.start]
                    )
                ):
                    return SuspensionQueryAnalysis(
                        SuspensionQueryState.CUSTOM,
                        top_level_or,
                        _CUSTOM_REASON,
                    )
            positives.append(token)

    if not positives:
        return SuspensionQueryAnalysis(
            SuspensionQueryState.OFF,
            top_level_or,
        )
    if top_level_or or any(token.depth != 0 for token in positives):
        return SuspensionQueryAnalysis(
            SuspensionQueryState.CUSTOM,
            top_level_or,
            _CUSTOM_REASON,
        )
    return SuspensionQueryAnalysis(SuspensionQueryState.ON, top_level_or)


def _positive_tokens(query: str) -> tuple[_Token, ...]:
    return tuple(
        token
        for token in _scan(query).tokens
        if token.depth == 0 and _is_positive(token.text)
    )


def _top_level_ands(query: str) -> tuple[_Token, ...]:
    return tuple(
        token
        for token in _scan(query).tokens
        if token.depth == 0 and token.text.upper() == "AND"
    )


def _removal_intervals(query: str) -> tuple[tuple[int, int], ...]:
    positives = _positive_tokens(query)
    if not positives:
        return ()

    clusters: list[list[_Token]] = [[positives[0]]]
    for token in positives[1:]:
        between = _strip_native(
            query[clusters[-1][-1].end : token.start]
        )
        if not between or between.upper() == "AND":
            clusters[-1].append(token)
        else:
            clusters.append([token])

    ands = _top_level_ands(query)
    intervals: list[tuple[int, int]] = []
    for cluster in clusters:
        start = cluster[0].start
        end = cluster[-1].end
        previous = next(
            (
                token
                for token in reversed(ands)
                if token.end <= start
                and _only_native_separators(query[token.end:start])
            ),
            None,
        )
        following = next(
            (
                token
                for token in ands
                if token.start >= end
                and _only_native_separators(query[end:token.start])
            ),
            None,
        )
        # Preserve an earlier AND when both sides exist: ``a AND filter AND b``
        # becomes ``a AND b``. At an edge, remove the one dangling operator.
        if following is not None:
            end = following.end
        elif previous is not None:
            start = previous.start

        right = end
        while right < len(query) and _is_native_separator(query[right]):
            right += 1
        if right < len(query):
            # Keep all source bytes before the managed clause; consume only
            # its following separator so the remaining terms still join.
            end = right
        else:
            left = start
            while left > 0 and _is_native_separator(query[left - 1]):
                left -= 1
            if left > 0:
                start = left
            else:
                start, end = 0, len(query)
        intervals.append((start, end))

    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _remove_positive_terms(query: str) -> str:
    result = query
    for start, end in reversed(_removal_intervals(query)):
        result = result[:start] + result[end:]
    return "" if not _strip_native(result) else result


def apply_suspension_filter(query: str, enabled: bool) -> str:
    """Add or remove the owned filter, or preserve a CUSTOM query exactly."""

    text = str(query)
    analysis = analyze_suspension_query(text)
    if analysis.state is SuspensionQueryState.CUSTOM:
        return text
    if enabled:
        if analysis.state is SuspensionQueryState.ON:
            return text
        stripped = _strip_native(text)
        if not stripped:
            return "is:suspended"
        if analysis.has_top_level_or:
            return f"({text}) is:suspended"
        separator = "" if _is_native_separator(text[-1]) else " "
        return f"{text}{separator}is:suspended"
    if analysis.state is SuspensionQueryState.OFF:
        return text
    return _remove_positive_terms(text)


__all__ = [
    "SuspensionQueryAnalysis",
    "SuspensionQueryState",
    "analyze_suspension_query",
    "apply_suspension_filter",
]
