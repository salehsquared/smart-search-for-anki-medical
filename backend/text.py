"""Medical-text cleanup and deterministic Unicode normalization."""

from __future__ import annotations

import html
from html.parser import HTMLParser
import re
import unicodedata
from collections.abc import Iterable


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
    normalized = normalize_text(clean)
    positions = [
        normalized.find(normalize_text(needle))
        for needle in needles
        if normalize_text(needle)
    ]
    positions = [position for position in positions if position >= 0]
    if not positions:
        return truncate_text(clean, maximum)
    center = min(positions)
    start = max(0, center - radius)
    end = min(len(clean), center + radius)
    snippet = clean[start:end].strip()
    if start:
        snippet = "…" + snippet
    if end < len(clean):
        snippet += "…"
    return truncate_text(snippet, maximum)
