"""Deterministic source-tag relationships for medical shared decks.

The relation deliberately does not use embeddings.  Two notes are related
only when they share the same complete, case-insensitively-normalized source
tag for a supported question bank.  Broad terminal roots such as
``#AK_Step2_v12::#UWorld`` are ignored because they describe a collection,
not a useful card-to-card relationship.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .compat import dataclass


SUPPORTED_RELATED_PROVIDERS = ("uworld", "amboss")

_LEADING_TAG_MARKERS_RE = re.compile(r"^[#^]+")
_PROVIDER_BOUNDARY_RE = {
    provider: re.compile(
        rf"(?:^|[^a-z0-9]){re.escape(provider)}(?=$|[^a-z]|[0-9])",
        flags=re.IGNORECASE,
    )
    for provider in SUPPORTED_RELATED_PROVIDERS
}


@dataclass(frozen=True, slots=True)
class RelatedSourceTag:
    """One complete source tag eligible for deterministic relationships."""

    key: str
    display: str
    provider: str
    leaf: str


def related_source_tags(tags: Iterable[str]) -> tuple[RelatedSourceTag, ...]:
    """Return unique, specific UWorld/AMBOSS tags in stable input order.

    Normalization intentionally stops at trimming and ``casefold()``.  We do
    not remove hierarchy components, punctuation, or AnKing version prefixes:
    related notes must share the complete source tag, not merely a question ID
    that happens to occur under a different source/version path.
    """

    output: list[RelatedSourceTag] = []
    seen: set[str] = set()
    for value in tags:
        display = str(value or "").strip()
        key = display.casefold()
        if not key or key in seen:
            continue
        provider = _specific_provider(display)
        if provider is None:
            continue
        components = tuple(
            component.strip()
            for component in display.split("::")
            if component.strip()
        )
        leaf = _display_component(components[-1]) if components else display
        output.append(
            RelatedSourceTag(
                key=key,
                display=display,
                provider=provider,
                leaf=leaf or provider.title(),
            )
        )
        seen.add(key)
    return tuple(output)


def related_reason(tag: RelatedSourceTag) -> str:
    """Return the compact, explainable chip shown in a related result row."""

    provider = "UWorld" if tag.provider == "uworld" else "AMBOSS"
    leaf = tag.leaf.strip()
    if leaf.casefold() == tag.provider:
        return f"Related · {provider}"
    return f"Related · {provider} · {leaf}"


def _specific_provider(tag: str) -> str | None:
    components = tuple(
        component.strip()
        for component in str(tag or "").split("::")
        if component.strip()
    )
    if not components:
        return None

    for index, component in enumerate(components):
        normalized_component = _display_component(component).casefold()
        for provider in SUPPORTED_RELATED_PROVIDERS:
            if not _PROVIDER_BOUNDARY_RE[provider].search(normalized_component):
                continue
            # A component that is only ``UWorld``/``AMBOSS`` is a broad root
            # unless a more-specific hierarchy component follows it.
            if normalized_component == provider and index == len(components) - 1:
                continue
            return provider
    return None


def _display_component(component: str) -> str:
    return _LEADING_TAG_MARKERS_RE.sub("", str(component or "").strip())


__all__ = [
    "RelatedSourceTag",
    "SUPPORTED_RELATED_PROVIDERS",
    "related_reason",
    "related_source_tags",
]
