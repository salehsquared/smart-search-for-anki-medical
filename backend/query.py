"""Conservative parsing of hybrid free-text and native Anki filters."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import re
from collections.abc import Iterable

from .models import ParsedQuery, QueryTerm
from .text import normalize_text


# These keys have stable meaning in Anki's search grammar.  Arbitrary note
# field names are accepted only when the caller explicitly supplies them.
DEFAULT_FILTER_KEYS = frozenset(
    {
        "added",
        "card",
        "cid",
        "deck",
        "dupe",
        "edited",
        "flag",
        "introduced",
        "is",
        "nid",
        "nc",
        "note",
        "preset",
        "prop",
        "rated",
        "re",
        "sc",
        "tag",
        "template",
        "w",
        "has-cd",
    }
)
_FILTER_RE = re.compile(
    r'^(?P<neg>-?)(?P<key>"(?:\\.|[^"])*"|[^:\s()]+):(?P<value>.*)$',
    re.DOTALL,
)
_CONTROL = {"AND", "OR"}


@dataclass(frozen=True, slots=True)
class _Part:
    raw: str
    kind: str


def strip_incomplete_filter_tokens(
    raw_query: str,
    *,
    filter_keys: Iterable[str] = DEFAULT_FILTER_KEYS,
    field_names: Iterable[str] = (),
) -> tuple[str, tuple[str, ...]]:
    """Remove only unfinished recognized filters from an executable query.

    Search runs after a short debounce, so a user may pause at ``is:`` or in
    the middle of ``deck:"Some deck``. Passing those transient fragments to
    Anki produces a syntax error and can interrupt typing. Built-in operators
    with no value and recognized filters with an unterminated quoted value are
    therefore ignored until completed. Empty note-field searches such as
    ``Front:`` remain valid and untouched.
    """

    raw = str(raw_query or "").strip()
    if not raw:
        return "", ()

    native_keys = {normalize_text(key) for key in filter_keys}
    known_fields = {normalize_text(name) for name in field_names}
    tokens, _warnings = _scan(raw)
    incomplete: list[str] = []

    # Only suppress trailing fragments. An empty operator in the middle of a
    # submitted query may be intentional input that Anki should validate; the
    # final token is the transient state produced while someone is typing.
    for token in reversed(tokens):
        # Match the token as written. A deliberately quoted literal such as
        # ``"is:"`` should continue to behave as free text.
        match = _FILTER_RE.match(token)
        if match is None:
            break

        key_text = match.group("key")
        if len(key_text) >= 2 and key_text[0] == key_text[-1] == '"':
            key_text = _unescape_quoted(key_text[1:-1])
        key = normalize_text(key_text)
        native = key in native_keys
        field = (
            key in known_fields
            or _matches_field_pattern(key, known_fields)
        )
        value = match.group("value")
        unfinished = (
            (native and _filter_value_is_empty(value))
            or ((native or field) and _quoted_value_is_unfinished(value))
        )
        if unfinished:
            incomplete.append(token)
        else:
            break

    if not incomplete:
        return raw, ()

    # Source-slice instead of rebuilding tokens so quotes, grouping, Boolean
    # casing, and whitespace in the valid prefix remain exactly as entered.
    cut = len(raw)
    for token in incomplete:
        start = raw.rfind(token, 0, cut)
        if start < 0:  # Defensive: _scan() tokens always originate in raw.
            break
        cut = start
    return raw[:cut].rstrip(), tuple(reversed(incomplete))


class QueryParser:
    """Split simple conjunctive queries without changing their meaning.

    Native filter clauses are returned untouched for card-aware resolution
    through ``Collection.find_cards()``. Free text is normalized separately
    for FTS.
    Mixed Boolean expressions such as ``deck:A OR bupropion`` cannot be split
    into independent filter and lexical searches without changing semantics;
    they therefore fall back to Anki's native evaluation of the full query.
    """

    def __init__(
        self,
        *,
        filter_keys: Iterable[str] = DEFAULT_FILTER_KEYS,
        field_names: Iterable[str] = (),
    ) -> None:
        self.filter_keys = {normalize_text(key) for key in filter_keys}
        self.field_names = {normalize_text(name) for name in field_names}

    def parse(self, raw_query: str) -> ParsedQuery:
        raw = str(raw_query or "").strip()
        if not raw:
            return ParsedQuery(raw="", free_terms=(), anki_filters=(), anki_filter_query="")

        tokens, scan_warnings = _scan(raw)
        parts = [self._classify(token) for token in tokens]
        warnings = list(scan_warnings)

        native_search_required = self._requires_native_search(parts)
        if native_search_required:
            warnings.append(
                "Anki will evaluate this Boolean OR, grouped, or wildcard expression "
                "directly so its meaning is preserved."
            )
            free_terms = tuple(
                term
                for part in parts
                if part.kind not in {"control", "paren"}
                for term in self._free_terms(part.raw)
            )
            return ParsedQuery(
                raw=raw,
                free_terms=free_terms,
                anki_filters=(),
                anki_filter_query="",
                warnings=tuple(warnings),
                native_search_required=True,
            )

        filter_parts: list[str] = []
        filter_tokens: list[str] = []
        free_terms_list: list[QueryTerm] = []

        for index, part in enumerate(parts):
            if part.kind == "filter":
                filter_parts.append(part.raw)
                filter_tokens.append(part.raw)
            elif part.kind == "free":
                free_terms_list.extend(self._free_terms(part.raw))
            elif part.kind == "control":
                previous_kind = _nearest_kind(parts, index, -1)
                next_kind = _nearest_kind(parts, index, 1)
                if previous_kind == next_kind == "filter":
                    filter_tokens.append(part.raw.upper())
            elif part.kind == "paren":
                # Parentheses are kept only when they enclose filters. A small
                # cleanup below removes empty or dangling pairs.
                filter_tokens.append(part.raw)

        anki_filter_query = _clean_filter_expression(filter_tokens)
        return ParsedQuery(
            raw=raw,
            free_terms=tuple(free_terms_list),
            anki_filters=tuple(filter_parts),
            anki_filter_query=anki_filter_query,
            warnings=tuple(warnings),
            native_search_required=False,
        )

    def _classify(self, raw: str) -> _Part:
        if raw in {"(", ")"}:
            return _Part(raw, "paren")
        if raw.upper() in _CONTROL:
            return _Part(raw, "control")
        candidate = _unwrap_whole_term_quote(raw)
        match = _FILTER_RE.match(candidate)
        if match:
            key_text = match.group("key")
            if len(key_text) >= 2 and key_text[0] == key_text[-1] == '"':
                key_text = _unescape_quoted(key_text[1:-1])
            key = normalize_text(key_text)
            if (
                key in self.filter_keys
                or key in self.field_names
                or _matches_field_pattern(key, self.field_names)
            ):
                return _Part(raw, "filter")
        return _Part(raw, "free")

    @staticmethod
    def _requires_native_search(parts: list[_Part]) -> bool:
        """Return true when splitting native syntax would change its meaning.

        Simple conjunctive filters can safely constrain an externally ranked
        fuzzy/semantic search. Boolean free text, grouped free text, and free
        wildcards cannot: Anki must evaluate the complete expression.
        """

        has_free = any(part.kind == "free" for part in parts)
        if not has_free:
            return False
        if _has_group_negation(parts):
            return True
        for index, part in enumerate(parts):
            if part.kind != "control" or part.raw.upper() != "OR":
                continue
            if not _inside_filter_only_group(parts, index):
                return True
        if _parenthesized_free_text(parts):
            return True
        for part in parts:
            if part.kind == "free" and _has_unescaped_wildcard(part.raw):
                return True
        return False

    def _free_terms(self, raw: str) -> tuple[QueryTerm, ...]:
        negated = raw.startswith("-") and len(raw) > 1
        value = raw[1:] if negated else raw
        quoted = len(value) >= 2 and value[0] == value[-1] == '"'
        if quoted:
            value = _unescape_quoted(value[1:-1])
        value = value.strip("() ")
        normalized = normalize_text(value)
        if not normalized:
            return ()
        return (
            QueryTerm(
                text=value,
                normalized=normalized,
                quoted=quoted,
                negated=negated,
            ),
        )


def _scan(raw: str) -> tuple[list[str], tuple[str, ...]]:
    tokens: list[str] = []
    warnings: list[str] = []
    current: list[str] = []
    in_quote = False
    escaped = False

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    for character in raw:
        if escaped:
            current.append(character)
            escaped = False
            continue
        if character == "\\" and in_quote:
            current.append(character)
            escaped = True
            continue
        if character == '"':
            current.append(character)
            in_quote = not in_quote
            continue
        if not in_quote and character.isspace():
            flush()
            continue
        if not in_quote and character in "()":
            flush()
            tokens.append(character)
            continue
        current.append(character)
    flush()
    if in_quote:
        warnings.append("The query contains an unmatched quote.")
    return tokens, tuple(warnings)


def _unescape_quoted(value: str) -> str:
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def _quoted_value_is_unfinished(value: str) -> bool:
    if not value.startswith('"'):
        return False
    escaped = False
    for character in value[1:]:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return False
    return True


def _filter_value_is_empty(value: str) -> bool:
    if not value:
        return True
    if _quoted_value_is_unfinished(value):
        return False
    return (
        len(value) >= 2
        and value[0] == value[-1] == '"'
        and not _unescape_quoted(value[1:-1])
    )


def _unwrap_whole_term_quote(raw: str) -> str:
    """Expose ``"deck:French Words"`` for classification only."""

    negated = raw.startswith("-")
    value = raw[1:] if negated else raw
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = _unescape_quoted(value[1:-1])
        return ("-" if negated else "") + value
    return raw


def _matches_field_pattern(key: str, field_names: set[str]) -> bool:
    if not field_names or not _has_unescaped_wildcard(key):
        return False
    pattern: list[str] = []
    escaped = False
    for character in key:
        if escaped:
            pattern.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "_":
            pattern.append("?")
        else:
            pattern.append(character)
    if escaped:
        pattern.append("\\")
    native_pattern = "".join(pattern)
    return any(fnmatchcase(field_name, native_pattern) for field_name in field_names)


def _has_unescaped_wildcard(value: str) -> bool:
    escaped = False
    quoted = False
    for character in value:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == '"':
            quoted = not quoted
            continue
        # Wildcards retain their native meaning inside Anki quotes too.
        if character in {"*", "_"}:
            return True
    return False


def _parenthesized_free_text(parts: list[_Part]) -> bool:
    """Whether a structural parenthesis group contains lexical text."""

    stack: list[bool] = []
    for part in parts:
        if part.kind == "paren" and part.raw == "(":
            stack.append(False)
        elif part.kind == "free" and stack:
            stack[-1] = True
        elif part.kind == "paren" and part.raw == ")" and stack:
            contains_free = stack.pop()
            if contains_free:
                return True
    return False


def _inside_filter_only_group(parts: list[_Part], target: int) -> bool:
    """Whether ``target`` is protected by an explicit filter-only group."""

    opening: int | None = None
    depth = 0
    for index in range(target - 1, -1, -1):
        part = parts[index]
        if part.kind != "paren":
            continue
        if part.raw == ")":
            depth += 1
        elif depth:
            depth -= 1
        else:
            opening = index
            break
    if opening is None:
        return False

    depth = 0
    closing: int | None = None
    for index in range(opening, len(parts)):
        part = parts[index]
        if part.kind != "paren":
            continue
        if part.raw == "(":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                closing = index
                break
    if closing is None or not opening < target < closing:
        return False
    return not any(
        part.kind == "free"
        for part in parts[opening + 1 : closing]
    )


def _has_group_negation(parts: list[_Part]) -> bool:
    """Anki binds a standalone ``-`` to the following parenthesized group."""

    return any(
        part.kind == "free"
        and part.raw == "-"
        and index + 1 < len(parts)
        and parts[index + 1].kind == "paren"
        and parts[index + 1].raw == "("
        for index, part in enumerate(parts)
    )


def _nearest_kind(parts: list[_Part], index: int, direction: int) -> str | None:
    cursor = index + direction
    while 0 <= cursor < len(parts):
        if parts[cursor].kind not in {"control", "paren"}:
            return parts[cursor].kind
        cursor += direction
    return None


def _clean_filter_expression(tokens: list[str]) -> str:
    if not tokens:
        return ""
    cleaned: list[str] = []
    balance = 0
    for token in tokens:
        if token == "(":
            balance += 1
            cleaned.append(token)
        elif token == ")":
            if balance and cleaned and cleaned[-1] != "(":
                balance -= 1
                cleaned.append(token)
        elif token in _CONTROL:
            if cleaned and cleaned[-1] not in _CONTROL and cleaned[-1] != "(":
                cleaned.append(token)
        else:
            cleaned.append(token)
    while cleaned and cleaned[-1] in _CONTROL | {"("}:
        cleaned.pop()
    # Dropping unmatched parentheses is safer than passing invalid syntax.
    if any(token in {"(", ")"} for token in cleaned):
        depth = 0
        valid: list[str] = []
        for token in cleaned:
            if token == "(":
                depth += 1
            elif token == ")":
                if depth == 0:
                    continue
                depth -= 1
            valid.append(token)
        if depth:
            for _ in range(depth):
                try:
                    valid.remove("(")
                except ValueError:
                    break
        cleaned = valid
    return " ".join(cleaned)
