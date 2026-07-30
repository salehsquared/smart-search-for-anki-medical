#!/usr/bin/env python3
"""Compile a small deterministic drug alias resource from NLM RxTerms.

The output is retrieval data, not a prescribing database.  It intentionally
omits doses, routes, retired rows, and suppressed rows.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import csv
import gzip
import io
import json
from pathlib import Path
import re
import unicodedata
import zipfile


ROUTE_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
SPACE = re.compile(r"\s+")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    return SPACE.sub(" ", value).strip()


def split_synonyms(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;|]", value or "") if item.strip()]


def archive_member(archive: zipfile.ZipFile, prefix: str) -> str:
    matches = [
        name
        for name in archive.namelist()
        if Path(name).name.startswith(prefix) and name.casefold().endswith(".txt")
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {prefix} text file, found {matches}")
    return matches[0]


def rows(archive: zipfile.ZipFile, member: str):
    with archive.open(member) as binary:
        text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text, delimiter="|")


def build(source: Path, output: Path, release: str) -> dict:
    with zipfile.ZipFile(source) as archive:
        ingredient_member = archive_member(archive, "RxTermsIngredients")
        main_member = archive_member(archive, "RxTerms20")

        ingredients: dict[str, set[str]] = defaultdict(set)
        ingredient_surface: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows(archive, ingredient_member):
            rxcui = row["RXCUI"].strip()
            surface = row["INGREDIENT"].strip()
            key = normalize(surface)
            if rxcui and key:
                ingredients[rxcui].add(key)
                ingredient_surface[key][surface] += 1

        observations: dict[str, list[frozenset[str]]] = defaultdict(list)
        surfaces: dict[str, Counter[str]] = defaultdict(Counter)
        rxcuis: dict[str, set[str]] = defaultdict(set)
        for row in rows(archive, main_member):
            if row.get("SUPPRESS_FOR", "").strip() or row.get("IS_RETIRED", "").strip():
                continue
            rxcui = row["RXCUI"].strip()
            ingredient_set = frozenset(ingredients.get(rxcui, ()))
            if not ingredient_set:
                continue

            display = ROUTE_SUFFIX.sub("", row.get("DISPLAY_NAME", "")).strip()
            candidates = [
                row.get("BRAND_NAME", ""),
                display,
                *split_synonyms(row.get("DISPLAY_NAME_SYNONYM", "")),
            ]
            for surface in candidates:
                surface = surface.strip()
                key = normalize(surface)
                if len(key) < 2 or not any(character.isalpha() for character in key):
                    continue
                observations[key].append(ingredient_set)
                surfaces[key][surface] += 1
                rxcuis[key].add(rxcui)

        records = []
        for alias_key, sets in observations.items():
            shared = set(sets[0])
            for ingredient_set in sets[1:]:
                shared.intersection_update(ingredient_set)

            # A synonym used across combination drugs (eg HCTZ) maps to the
            # ingredient shared by all rows.  If no shared ingredient exists,
            # retain only aliases with one unambiguous ingredient tuple.
            if shared:
                canonical_keys = sorted(shared)
            else:
                unique_sets = set(sets)
                if len(unique_sets) != 1:
                    continue
                canonical_keys = sorted(next(iter(unique_sets)))

            if alias_key in canonical_keys and len(canonical_keys) == 1:
                continue
            canonical = " / ".join(
                ingredient_surface[key].most_common(1)[0][0]
                if ingredient_surface[key]
                else key
                for key in canonical_keys
            )
            alias_surface = surfaces[alias_key].most_common(1)[0][0]
            records.append(
                {
                    "alias": alias_surface,
                    "alias_norm": alias_key,
                    "canonical": canonical,
                    "canonical_norm": normalize(canonical),
                    "kind": "rxterms",
                    "rxcui_count": len(rxcuis[alias_key]),
                }
            )

        records.sort(key=lambda item: (item["alias_norm"], item["canonical_norm"]))
        payload = {
            "format": 1,
            "source": "NLM RxTerms",
            "release": release,
            "source_url": (
                "https://data.lhncbc.nlm.nih.gov/public/rxterms/release/"
                f"RxTerms{release}.zip"
            ),
            "notice": (
                "RxTerms is produced by the U.S. National Library of Medicine. "
                "It is used here only for local search expansion."
            ),
            "aliases": records,
        }
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(gzip.compress(encoded, compresslevel=9, mtime=0))
        return {
            "aliases": len(records),
            "uncompressed_bytes": len(encoded),
            "compressed_bytes": output.stat().st_size,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--release", required=True)
    arguments = parser.parse_args()
    print(
        json.dumps(
            build(arguments.source, arguments.output, arguments.release),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
