"""Medical-text cleanup and deterministic Unicode normalization."""

from __future__ import annotations

import html
from html.parser import HTMLParser
import re
import unicodedata
from collections.abc import Iterable, Mapping


_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "dl",
    "dt",
    "dd",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tr",
    "ul",
}
_SKIP_TAGS = {"script", "style", "svg"}
_MEDIA_RE = re.compile(r"\[(?:sound|anki:play|anki:tts)[^\]]*\]", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)

_CHAR_TRANSLATION = str.maketrans(
    {
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2033": '"',
        "β": " beta ",
        "Β": " beta ",
        "α": " alpha ",
        "Α": " alpha ",
        "γ": " gamma ",
        "Γ": " gamma ",
        "δ": " delta ",
        "Δ": " delta ",
    }
)


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if lowered in _BLOCK_TAGS:
            self.parts.append(" ")
        if lowered == "img":
            attributes = {key.casefold(): value for key, value in attrs}
            alt = attributes.get("alt")
            if alt:
                self.parts.extend((" ", alt, " "))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if not self._skip_depth and lowered in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def strip_cloze(text: str) -> str:
    """Return visible cloze content while dropping cloze numbers and hints.

    The bounded pattern is intentionally repeated so adjacent and simple nested
    markup are handled without treating arbitrary ``{{...}}`` text as a cloze.
    """

    cloze_re = re.compile(
        r"\{\{c\d+::(?P<answer>.*?)(?:::(?P<hint>.*?))?\}\}",
        re.IGNORECASE | re.DOTALL,
    )
    previous = None
    current = text
    for _ in range(8):
        if current == previous:
            break
        previous = current
        current = cloze_re.sub(lambda match: match.group("answer"), current)
    return current


def strip_html_and_cloze(value: str) -> str:
    """Convert an Anki field into human-readable plain text."""

    source = strip_cloze(str(value))
    source = _MEDIA_RE.sub(" ", source)
    parser = _PlainTextParser()
    try:
        parser.feed(source)
        parser.close()
        plain = "".join(parser.parts)
    except Exception:
        # HTMLParser is tolerant, but malformed input must never abort indexing.
        plain = re.sub(r"<[^>]*>", " ", source)
    return _SPACE_RE.sub(" ", html.unescape(plain)).strip()


def normalize_text(value: str, *, remove_diacritics: bool = True) -> str:
    """Normalize text for case-insensitive matching.

    NFKC is applied before Unicode ``casefold()``.  Search normalization also
    removes combining marks by default, making ``Behçet`` and ``behcet``
    equivalent while the original spelling remains available for display.
    """

    normalized = unicodedata.normalize("NFKC", str(value)).translate(_CHAR_TRANSLATION)
    normalized = normalized.casefold()
    if remove_diacritics:
        decomposed = unicodedata.normalize("NFKD", normalized)
        normalized = "".join(
            character
            for character in decomposed
            if not unicodedata.combining(character)
        )
    return _SPACE_RE.sub(" ", normalized).strip()


def tokenize(value: str, *, already_normalized: bool = False) -> tuple[str, ...]:
    normalized = value if already_normalized else normalize_text(value)
    return tuple(match.group(0) for match in _WORD_RE.finditer(normalized))


def _normalized_with_source_offsets(value: str) -> tuple[str, tuple[int, ...]]:
    """Normalize text while retaining an offset for every output character.

    Unicode case-folding, diacritic removal, and medical-symbol expansion can
    change string length. Keeping this map lets display snippets and highlight
    spans locate normalized matches in the original human-readable text.
    """

    normalized_characters: list[str] = []
    source_offsets: list[int] = []
    for source_index, character in enumerate(str(value)):
        fragment = unicodedata.normalize("NFKC", character).translate(
            _CHAR_TRANSLATION
        )
        fragment = unicodedata.normalize("NFKD", fragment.casefold())
        fragment = "".join(
            item for item in fragment if not unicodedata.combining(item)
        )
        for item in fragment:
            if item.isspace():
                if normalized_characters and normalized_characters[-1] != " ":
                    normalized_characters.append(" ")
                    source_offsets.append(source_index)
                continue
            normalized_characters.append(item)
            source_offsets.append(source_index)
    if normalized_characters and normalized_characters[-1] == " ":
        normalized_characters.pop()
        source_offsets.pop()
    return "".join(normalized_characters), tuple(source_offsets)


def normalized_spans(value: str, needle: str) -> tuple[tuple[int, int], ...]:
    """Return nonoverlapping original-text spans for a normalized needle."""

    normalized, source_offsets = _normalized_with_source_offsets(value)
    target = normalize_text(needle)
    if not normalized or not target:
        return ()
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor <= len(normalized) - len(target):
        position = normalized.find(target, cursor)
        if position < 0:
            break
        end_position = position + len(target) - 1
        spans.append(
            (
                source_offsets[position],
                source_offsets[end_position] + 1,
            )
        )
        cursor = position + len(target)
    return tuple(spans)


def join_searchable_text(parts: Iterable[str]) -> str:
    return normalize_text(" ".join(part for part in parts if part))


def truncate_text(value: str, length: int = 160) -> str:
    clean = _SPACE_RE.sub(" ", value).strip()
    if len(clean) <= length:
        return clean
    return clean[: max(1, length - 1)].rstrip() + "…"


def make_snippet(
    plain_text: str,
    needles: Iterable[str],
    *,
    radius: int = 90,
    maximum: int = 240,
) -> str:
    """Return a plain-text excerpt centered on the first normalized match."""

    clean = _SPACE_RE.sub(" ", plain_text).strip()
    if not clean:
        return ""
    matches = [
        span
        for needle in needles
        for span in normalized_spans(clean, needle)
    ]
    if not matches:
        return truncate_text(clean, maximum)
    match_start, match_end = min(matches, key=lambda span: (span[0], span[1]))
    start = max(0, match_start - radius)
    end = min(len(clean), match_end + radius)
    if start:
        next_boundary = clean.find(" ", start, match_start)
        if next_boundary >= 0:
            start = next_boundary + 1
    if end < len(clean):
        previous_boundary = clean.rfind(" ", match_end, end)
        if previous_boundary >= 0:
            end = previous_boundary
    snippet = clean[start:end].strip()
    if start:
        snippet = "…" + snippet
    if end < len(clean):
        snippet += "…"
    return truncate_text(snippet, maximum)


# ---------------------------------------------------------------------------
# Compact result-card display contract
# ---------------------------------------------------------------------------

# Field names (compared case-insensitively after normalization) that hold the
# card's primary prompt. These match the conventions used by the common
# medical note types (AnKing, Basic, and Cloze variants) without depending
# on any one template.
_PRIMARY_FIELD_NAMES = ("text", "front", "question", "prompt", "header")
# Only a field whose normalized name is exactly ``extra`` may become the
# supporting line. ``Back``, ``Answer``, ``Explanation``, ``Back Extra``,
# ``Extra 1``, lecture/resource fields, and every other field stay searchable
# but are never substituted for the supporting line.
_SUPPORT_FIELD_NAME = "extra"

# Internal/admin/identity and image-mask fields never make useful result-row
# text: they hold sync identities, display-mode toggles, or encoded mask data
# rather than study content.
_INTERNAL_FIELD_TOKENS = {
    "ankihub",
    "hidden",
    "id",
    "guid",
    "image",
    "images",
    "img",
    "mask",
    "masks",
    "occlusion",
}
_INTERNAL_FIELD_NAMES = {
    "related card",
    "related cards",
}

# Display bounds. The primary line is represented in the accessible row text,
# so it is bounded independently of paint-time elision; the supporting line is
# a single short excerpt.
PRIMARY_DISPLAY_LIMIT = 220
SUPPORT_DISPLAY_LIMIT = 180


def _is_internal_field(name: str) -> bool:
    """Whether a field name marks identity, admin, or image-mask content."""

    normalized = normalize_text(name)
    if not normalized:
        return True
    if normalized in _INTERNAL_FIELD_NAMES:
        return True
    if "one by one" in normalized:
        return True
    return bool(set(tokenize(normalized, already_normalized=True)) & _INTERNAL_FIELD_TOKENS)


def display_lines(
    fields: Mapping[str, str],
    needles: Iterable[str] = (),
    *,
    note_id: int = 0,
    primary_limit: int = PRIMARY_DISPLAY_LIMIT,
    support_limit: int = SUPPORT_DISPLAY_LIMIT,
) -> tuple[str, str]:
    """Return the compact ``(primary, supporting)`` lines for one note.

    This is a pure display transformation over fields already materialized
    for a returned note; the complete field corpus stays indexed and
    searchable. The primary line prefers ``Text``/``Front``/``Question``
    (case-insensitively), then the first nonempty non-internal textual field.
    The supporting line is exactly one bounded excerpt from the note's real
    ``Extra`` field (normalized name match only). It never repeats the primary
    text, and it is never substituted with ``Back``, ``Answer``,
    ``Explanation``, ``Back Extra``, ``Extra 1``, lecture/resource fields, or
    any other field — those stay searchable without being displayed. When the
    query matches later inside ``Extra``, the excerpt is centered on that
    match; no other field may be surfaced to explain a match. A note with no
    displayable text falls back to ``Note <id>`` and no supporting line.
    """

    # Reject known administrative/media fields before cleaning their values.
    # Image-occlusion masks and generated resource fields can be much larger
    # than the human-readable content, and result projection must stay cheap.
    displayable = [
        (str(name), cleaned)
        for name, value in fields.items()
        if not _is_internal_field(str(name))
        and (cleaned := strip_html_and_cloze(value))
    ]

    primary_index: int | None = None
    for preferred in _PRIMARY_FIELD_NAMES:
        for index, (name, _text) in enumerate(displayable):
            if normalize_text(name) == preferred:
                primary_index = index
                break
        if primary_index is not None:
            break
    if primary_index is None and displayable:
        primary_index = 0

    if primary_index is None:
        primary = f"Note {int(note_id)}" if int(note_id) > 0 else ""
        return primary, ""

    primary_full = displayable[primary_index][1]
    primary = truncate_text(primary_full, primary_limit)
    primary_normalized = normalize_text(primary_full)
    remaining = [
        (name, text)
        for index, (name, text) in enumerate(displayable)
        if index != primary_index and normalize_text(text) != primary_normalized
    ]
    normalized_needles = tuple(
        dict.fromkeys(
            needle
            for needle in (normalize_text(value) for value in needles)
            if needle
        )
    )
    extra_text = ""
    for name, text in remaining:
        if normalize_text(name) == _SUPPORT_FIELD_NAME:
            extra_text = text
            break
    if not extra_text:
        return primary, ""

    def matching_needles(value: str) -> set[str]:
        normalized_value = normalize_text(value)
        return {
            needle for needle in normalized_needles if needle in normalized_value
        }

    # A query term matched only later inside Extra earns a match-centered
    # excerpt from Extra itself; otherwise the excerpt starts at the cleaned
    # beginning of the field. No other field is ever surfaced.
    uncovered_needles = tuple(
        needle
        for needle in normalized_needles
        if needle not in matching_needles(primary)
    )
    if uncovered_needles and matching_needles(extra_text) & set(uncovered_needles):
        support = make_snippet(
            extra_text,
            uncovered_needles,
            maximum=support_limit,
        )
    else:
        support = truncate_text(extra_text, support_limit)
    if normalize_text(support) == normalize_text(primary):
        support = ""
    return primary, support
