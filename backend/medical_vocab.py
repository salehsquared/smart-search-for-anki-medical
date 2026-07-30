"""Load curated medical aliases without importing them into the collection."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from collections.abc import Iterable

from .text import normalize_text


class MedicalVocabularyError(ValueError):
    pass


def load_alias_resource(path: str | Path) -> dict[str, set[str]]:
    """Load the add-on's compact JSON or JSON.GZ alias resource.

    Only the documented ``alias``/``canonical`` strings are consumed. Invalid
    records are skipped, while a malformed container raises a clear error.
    """

    resource = Path(path)
    opener = gzip.open if resource.suffix.casefold() == ".gz" else open
    try:
        with opener(resource, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise MedicalVocabularyError(
            f"Could not load medical vocabulary {resource}: {error}"
        ) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("aliases"), list):
        raise MedicalVocabularyError(
            "Medical vocabulary must be an object containing an aliases array."
        )
    aliases: dict[str, set[str]] = {}
    for entry in payload["aliases"]:
        if not isinstance(entry, dict):
            continue
        alias = normalize_text(entry.get("alias_norm") or entry.get("alias") or "")
        canonical = normalize_text(
            entry.get("canonical_norm") or entry.get("canonical") or ""
        )
        if not alias or not canonical or alias == canonical:
            continue
        aliases.setdefault(alias, set()).add(canonical)
    return aliases


def merge_alias_maps(
    *alias_maps: dict[str, Iterable[str]],
) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for alias_map in alias_maps:
        for alias, canonical_values in alias_map.items():
            normalized_alias = normalize_text(alias)
            if not normalized_alias:
                continue
            for canonical in canonical_values:
                normalized_canonical = normalize_text(canonical)
                if normalized_canonical and normalized_canonical != normalized_alias:
                    merged.setdefault(normalized_alias, set()).add(normalized_canonical)
    return merged
