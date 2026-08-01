"""Pure helpers for applying a deck picker to Anki search text.

The picker deliberately owns only a narrow, unambiguous subset of Anki's
search language: one positive exact ``deck:`` token, or one parenthesized OR
group made entirely from such tokens. Everything else is reported as custom
syntax so the UI cannot silently reinterpret a hand-written query.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from collections.abc import Iterable


class DeckScopeKind(Enum):
    ALL = "all"
    SELECTED = "selected"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class DeckQueryAnalysis:
    kind: DeckScopeKind
    names: tuple[str, ...]
    clause_start: int | None
    clause_end: int | None
    remaining_query: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class _Token:
    raw: str
    start: int
    end: int
    depth: int


@dataclass(frozen=True, slots=True)
class _DeckToken:
    present: bool
    exact: bool = False
    name: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class _Node:
    kind: str
    raw: str = ""
    children: tuple["_Node", ...] = ()


_DECK_RE = re.compile(
    r"^(?P<negative>-?)deck:(?P<value>.*)$",
    re.IGNORECASE | re.ASCII,
)


def analyze_deck_query(query: str) -> DeckQueryAnalysis:
    """Describe whether ``query`` contains a picker-manageable deck clause."""

    raw = str(query or "")
    stripped = raw.strip()
    if _has_quote_adjacency(raw) and _contains_deck_operator(raw):
        return DeckQueryAnalysis(
            DeckScopeKind.CUSTOM,
            (),
            None,
            None,
            stripped,
            "Adjacent quoted terms use advanced Anki search syntax.",
        )
    tokens, balanced = _scan(raw)
    candidates = {
        index: candidate
        for index, token in enumerate(tokens)
        if (candidate := _deck_token(token.raw)).present
    }
    if not candidates:
        if _contains_deck_operator(raw):
            return DeckQueryAnalysis(
                DeckScopeKind.CUSTOM,
                (),
                None,
                None,
                stripped,
                "Advanced deck syntax is active.",
            )
        return DeckQueryAnalysis(
            DeckScopeKind.ALL,
            (),
            None,
            None,
            stripped,
        )

    expression_valid = balanced and _ExpressionParser(tokens).parse() is not None
    if expression_valid and len(candidates) == 1:
        index, candidate = next(iter(candidates.items()))
        token = tokens[index]
        if (
            candidate.exact
            and token.depth == 0
            and not _preceded_by_separate_minus(tokens, index)
            and not _has_top_level_or(tokens)
        ):
            start, end = token.start, token.end
            return DeckQueryAnalysis(
                DeckScopeKind.SELECTED,
                _canonical_names((candidate.name,)),
                start,
                end,
                _remove_recognized_clause(raw, tokens, start, end),
            )

    if expression_valid:
        group = _recognized_or_group(tokens, candidates)
        if group is not None and not _has_top_level_or(tokens):
            start, end, names = group
            return DeckQueryAnalysis(
                DeckScopeKind.SELECTED,
                _canonical_names(names),
                start,
                end,
                _remove_recognized_clause(raw, tokens, start, end),
            )

    reason = next(
        (
            candidate.reason
            for candidate in candidates.values()
            if candidate.reason
        ),
        "Deck filters use custom or mixed Boolean syntax.",
    )
    if len(candidates) > 1 and not any(
        candidate.reason for candidate in candidates.values()
    ):
        reason = "Multiple deck clauses require custom handling."
    return DeckQueryAnalysis(
        DeckScopeKind.CUSTOM,
        (),
        None,
        None,
        stripped,
        reason,
    )


def build_deck_clause(names: Iterable[str]) -> str:
    """Return a canonical, exact Anki deck clause for ``names``."""

    canonical = _canonical_names(names)
    if not canonical:
        return ""
    tokens = tuple(
        f'deck:"{_escape_name(_literal_deck_name(name))}"'
        for name in canonical
    )
    if len(tokens) == 1:
        return tokens[0]
    return "(" + " OR ".join(tokens) + ")"


def apply_deck_selection(
    query: str,
    names: Iterable[str],
) -> str:
    """Apply ``names`` only when the existing deck syntax is unambiguous.

    Custom native Anki expressions are always preserved. Users can edit those
    expressions directly in the visible search field.
    """

    raw = str(query or "")
    clause = build_deck_clause(names)
    analysis = analyze_deck_query(raw)

    if analysis.kind is DeckScopeKind.CUSTOM:
        raise ValueError(
            "This advanced deck expression must be edited in the search box."
        )

    if analysis.kind is DeckScopeKind.SELECTED:
        if not clause:
            return analysis.remaining_query
        assert analysis.clause_start is not None
        assert analysis.clause_end is not None
        return (
            raw[: analysis.clause_start]
            + clause
            + raw[analysis.clause_end :]
        ).strip()

    return _conjoin(analysis.remaining_query, clause)


def _canonical_names(names: Iterable[str]) -> tuple[str, ...]:
    canonical: list[str] = []
    keys: list[str] = []
    for value in names:
        name = str(value).strip()
        if not name:
            raise ValueError("Deck names must not be empty.")
        key = _deck_identity_key(name)
        if key in keys:
            continue
        if any(_is_descendant(key, parent) for parent in keys):
            continue
        retained = [
            (existing_name, existing_key)
            for existing_name, existing_key in zip(canonical, keys)
            if not _is_descendant(existing_key, key)
        ]
        canonical = [item[0] for item in retained]
        keys = [item[1] for item in retained]
        canonical.append(name)
        keys.append(key)
    return tuple(canonical)


def _is_descendant(candidate: str, parent: str) -> bool:
    return candidate.startswith(parent + "::")


def _deck_identity_key(name: str) -> str:
    # Anki's regex engine uses simple case folding. Python's casefold() can
    # expand one character into several (for example ß -> ss), which would
    # incorrectly merge distinct native deck queries. ASCII folding covers
    # ordinary deck names while non-ASCII names stay conservatively distinct.
    return name.lower() if name.isascii() else name


def _escape_name(name: str) -> str:
    return (
        name.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("*", "\\*")
        .replace("_", "\\_")
    )


def _literal_deck_name(name: str) -> str:
    """Avoid Anki's two lowercase reserved deck-search values.

    Deck-name matching is case-insensitive, while ``deck:current`` and
    ``deck:filtered`` are special only in lowercase.  Capitalizing the first
    character therefore addresses a real deck with one of those names.
    """

    if name in {"current", "filtered"}:
        return name[0].upper() + name[1:]
    return name


def _scan(query: str) -> tuple[tuple[_Token, ...], bool]:
    tokens: list[_Token] = []
    depth = 0
    balanced = True
    index = 0
    while index < len(query):
        if _is_native_separator(query[index]):
            index += 1
            continue
        if query[index] == "(":
            tokens.append(_Token("(", index, index + 1, depth))
            depth += 1
            index += 1
            continue
        if query[index] == ")":
            depth -= 1
            if depth < 0:
                balanced = False
                depth = 0
            tokens.append(_Token(")", index, index + 1, depth))
            index += 1
            continue

        start = index
        quoted = False
        while index < len(query):
            char = query[index]
            if char == "\\" and index + 1 < len(query):
                index += 2
                continue
            if char == '"':
                quoted = not quoted
                index += 1
                continue
            if not quoted and (_is_native_separator(char) or char in "()"):
                break
            index += 1
        if quoted:
            balanced = False
        tokens.append(_Token(query[start:index], start, index, depth))
    if depth:
        balanced = False
    return tuple(tokens), balanced


def _deck_token(raw: str) -> _DeckToken:
    match = _DECK_RE.match(raw)
    if match is None:
        # Anki also accepts a whole quoted native term. It is intentionally
        # treated as custom because the picker never emits that representation.
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            inner = _decode_escaped(raw[1:-1])
            if inner.lower().startswith("-deck:"):
                return _DeckToken(False)
            if _DECK_RE.match(inner):
                return _DeckToken(
                    True,
                    reason="Whole-term quoted deck filters are custom.",
                )
        return _DeckToken(False)

    if match.group("negative"):
        return _DeckToken(True, reason="Negative deck filters are custom.")
    value = match.group("value")
    if not value:
        return _DeckToken(True, reason="Empty deck filters are custom.")

    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            return _DeckToken(True, reason="Incomplete deck filters are custom.")
        encoded = value[1:-1]
    else:
        if '"' in value:
            return _DeckToken(True, reason="Malformed deck filters are custom.")
        encoded = value

    if _has_invalid_escape(encoded):
        return _DeckToken(True, reason="Invalid deck-filter escaping is custom.")
    if _contains_unescaped_wildcard(encoded):
        return _DeckToken(True, reason="Wildcard deck filters are custom.")
    name = _decode_escaped(encoded)
    if not name:
        return _DeckToken(True, reason="Empty deck filters are custom.")
    if name in {"current", "filtered"}:
        return _DeckToken(
            True,
            reason=(
                "Anki reserves this lowercase deck search value for a "
                "dynamic deck scope."
            ),
        )
    return _DeckToken(True, True, name)


def _contains_unescaped_wildcard(value: str) -> bool:
    escaped = False
    for char in value:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in {"*", "_"}:
            return True
    return False


def _decode_escaped(value: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            decoded.append(value[index + 1])
            index += 2
        else:
            decoded.append(value[index])
            index += 1
    return "".join(decoded)


def _has_invalid_escape(value: str) -> bool:
    allowed = {"\\", '"', ":", "*", "_", "(", ")", "-"}
    index = 0
    while index < len(value):
        if value[index] != "\\":
            index += 1
            continue
        if index + 1 >= len(value) or value[index + 1] not in allowed:
            return True
        index += 2
    return False


def _has_quote_adjacency(query: str) -> bool:
    """Detect Anki's implicit term boundaries that our narrow scanner avoids."""

    escaped = False
    in_quote = False
    for index, char in enumerate(query):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char != '"':
            continue
        if not in_quote:
            previous = query[index - 1] if index else ""
            if (
                previous
                and not _is_native_separator(previous)
                and previous not in ":()-"
            ):
                return True
            in_quote = True
            continue
        in_quote = False
        following = query[index + 1] if index + 1 < len(query) else ""
        if (
            following
            and not _is_native_separator(following)
            and following not in "()"
        ):
            return True
    return False


def _contains_deck_operator(query: str) -> bool:
    # Quotes are native Anki term boundaries even when no space surrounds
    # them. Walk quote state explicitly so ``foo"deck:A"`` is detected,
    # while the plain text inside ``"foo deck:A"`` and ``"-deck:A"`` is
    # not mistaken for an active deck operator.
    index = 0
    in_quote = False
    at_boundary = True
    while index < len(query):
        char = query[index]
        if char == "\\" and index + 1 < len(query):
            index += 2
            if not in_quote:
                at_boundary = False
            continue
        if char == '"':
            if in_quote:
                in_quote = False
                at_boundary = True
            else:
                in_quote = True
                if (
                    not _is_quoted_field_value(query, index)
                    and _starts_deck_operator(query, index + 1)
                ):
                    return True
                at_boundary = False
            index += 1
            continue
        if in_quote:
            index += 1
            continue
        if _is_native_separator(char) or char in "()":
            at_boundary = True
            index += 1
            continue
        if at_boundary and (
            _starts_deck_operator(query, index)
            or (
                char == "-"
                and _starts_deck_operator(query, index + 1)
            )
        ):
            return True
        at_boundary = False
        index += 1
    return False


def _starts_deck_operator(query: str, index: int) -> bool:
    candidate = query[index : index + 5]
    return candidate.isascii() and candidate.lower() == "deck:"


def _is_quoted_field_value(query: str, quote_index: int) -> bool:
    """Return whether this quote starts a native field value.

    Anki treats only the first unescaped colon in one unquoted node as the
    field separator. A later colon ends that node before an adjacent quote, so
    a multiple-colon form can contain a real whole-term deck filter.
    """

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


def _is_native_separator(char: str) -> bool:
    """Match the separators used by Anki's native search parser.

    Python's ``str.isspace()`` is much broader. Treating a pasted tab, NBSP,
    or em-space as a separator here could make the picker remove text that
    Anki actually parses as part of a single search term.
    """

    return char in {" ", "\u3000"}


def _recognized_or_group(
    tokens: tuple[_Token, ...],
    candidates: dict[int, _DeckToken],
) -> tuple[int, int, tuple[str, ...]] | None:
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for index, token in enumerate(tokens):
        if token.raw == "(":
            stack.append(index)
        elif token.raw == ")" and stack:
            pairs.append((stack.pop(), index))

    for open_index, close_index in pairs:
        opening = tokens[open_index]
        closing = tokens[close_index]
        if opening.depth != 0 or closing.depth != 0:
            continue
        contained = tuple(
            index
            for index in candidates
            if open_index < index < close_index
        )
        if set(contained) != set(candidates) or len(contained) < 2:
            continue
        if open_index > 0 and tokens[open_index - 1].raw == "-":
            continue
        interior = tokens[open_index + 1 : close_index]
        names: list[str] = []
        expect_deck = True
        valid = True
        for offset, token in enumerate(interior, start=open_index + 1):
            if token.depth != 1:
                valid = False
                break
            if expect_deck:
                candidate = candidates.get(offset)
                if candidate is None or not candidate.exact:
                    valid = False
                    break
                names.append(candidate.name)
            elif token.raw.casefold() != "or":
                valid = False
                break
            expect_deck = not expect_deck
        if valid and not expect_deck:
            return opening.start, closing.end, tuple(names)
    return None


def _has_top_level_or(tokens: tuple[_Token, ...]) -> bool:
    return any(
        token.depth == 0 and token.raw.casefold() == "or"
        for token in tokens
    )


def _preceded_by_separate_minus(
    tokens: tuple[_Token, ...],
    index: int,
) -> bool:
    if index <= 0:
        return False
    previous = tokens[index - 1]
    return previous.raw == "-" and previous.depth == tokens[index].depth


def _remove_recognized_clause(
    query: str,
    tokens: tuple[_Token, ...],
    start: int,
    end: int,
) -> str:
    before = [token for token in tokens if token.end <= start]
    after = [token for token in tokens if token.start >= end]
    if after and after[0].depth == 0 and after[0].raw.casefold() == "and":
        end = after[0].end
    elif before and before[-1].depth == 0 and before[-1].raw.casefold() == "and":
        start = before[-1].start
    return _splice(query, start, end)


def _splice(query: str, start: int, end: int) -> str:
    left = query[:start].rstrip()
    right = query[end:].lstrip()
    if left and right:
        return left + " " + right
    return left or right


def _conjoin(query: str, clause: str) -> str:
    query = str(query or "").strip()
    if not clause:
        return query
    if not query:
        return clause
    tokens, _balanced = _scan(query)
    if _has_top_level_or(tokens) or _has_quote_adjacency(query):
        query = f"({query})"
    return f"{query} {clause}"


class _ExpressionParser:
    def __init__(self, tokens: tuple[_Token, ...]) -> None:
        self.tokens = tokens
        self.index = 0

    def parse(self) -> _Node | None:
        node = self._parse_or()
        if self.index != len(self.tokens):
            return None
        return node

    def _parse_or(self) -> _Node | None:
        children: list[_Node] = []
        first = self._parse_and()
        if first is None:
            return None
        children.append(first)
        while self._peek("or"):
            self.index += 1
            child = self._parse_and()
            if child is None:
                return None
            children.append(child)
        if len(children) == 1:
            return children[0]
        return _Node("or", children=tuple(children))

    def _parse_and(self) -> _Node | None:
        children: list[_Node] = []
        first = self._parse_factor()
        if first is None:
            return None
        children.append(first)
        while self.index < len(self.tokens):
            raw = self.tokens[self.index].raw.casefold()
            if raw in {")", "or"}:
                break
            if raw == "and":
                self.index += 1
            child = self._parse_factor()
            if child is None:
                return None
            children.append(child)
        if len(children) == 1:
            return children[0]
        return _Node("and", children=tuple(children))

    def _parse_factor(self) -> _Node | None:
        if self.index >= len(self.tokens):
            return None
        token = self.tokens[self.index]
        if token.raw == "-":
            self.index += 1
            child = self._parse_factor()
            return None if child is None else _Node("not", children=(child,))
        if token.raw == "(":
            self.index += 1
            child = self._parse_or()
            if child is None or self.index >= len(self.tokens):
                return None
            if self.tokens[self.index].raw != ")":
                return None
            self.index += 1
            return _Node("group", children=(child,))
        if token.raw == ")" or token.raw.casefold() in {"and", "or"}:
            return None
        self.index += 1
        return _Node("atom", raw=token.raw)

    def _peek(self, value: str) -> bool:
        return (
            self.index < len(self.tokens)
            and self.tokens[self.index].raw.casefold() == value
        )
