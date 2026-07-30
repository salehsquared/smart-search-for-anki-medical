#!/usr/bin/env python3
"""Rebuild the exact third-party notice bundle for the bundled tokenizers wheel.

The wheel's embedded CycloneDX SBOM is the component authority.  Registry
source archives are downloaded from crates.io and accepted only when their
SHA-256 matches the digest recorded in that SBOM.  The non-registry tokenizers
component is resolved to the exact upstream v0.23.1 commit.

The generated bundle preserves every license, notice, copying, copyright, and
unlicense file shipped in those exact source packages.  Identical texts are
stored once and cross-referenced from every component that supplied them.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
from typing import Any
from urllib.request import Request, urlopen
import zipfile


WHEEL_RELATIVE_PATH = (
    "vendor_wheels/darwin-arm64-py313/"
    "tokenizers-0.23.1-cp310-abi3-macosx_11_0_arm64.whl"
)
WHEEL_SHA256 = "e0948bbb1ac1d7cdfc9fb6d62c596e3b7550036ad60ecd654a66ad273326324e"
SBOM_PATH = "tokenizers-0.23.1.dist-info/sboms/tokenizers-python.cyclonedx.json"
EXPECTED_COMPONENT_COUNT = 120
EXPECTED_REGISTRY_COMPONENT_COUNT = 119

TOKENIZERS_COMMIT = "7f1623b90b5adfb9bc327d4c3468d2f70bbce262"
TOKENIZERS_SOURCE_URL = (
    "https://codeload.github.com/huggingface/tokenizers/tar.gz/"
    + TOKENIZERS_COMMIT
)
TOKENIZERS_LICENSE_PATH = f"tokenizers-{TOKENIZERS_COMMIT}/LICENSE"
TOKENIZERS_LICENSE_SHA256 = (
    "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
)

OUTPUT_RELATIVE_PATH = "licenses/tokenizers-0.23.1-NOTICES.txt"

NOTICE_BASENAME = re.compile(
    r"^(?:LICENSE|LICENCE|COPYING|COPYRIGHT|NOTICE|UNLICENSE)(?:[._-].*)?$",
    re.IGNORECASE,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "Smart-Search-for-Anki-notice-builder/1.0"},
    )
    with urlopen(request, timeout=120) as response:
        return response.read()


def _read_sbom(wheel: Path) -> tuple[dict[str, Any], str]:
    wheel_bytes = wheel.read_bytes()
    actual_wheel_hash = _sha256(wheel_bytes)
    if actual_wheel_hash != WHEEL_SHA256:
        raise SystemExit(
            f"Tokenizers wheel checksum mismatch: expected {WHEEL_SHA256}, "
            f"got {actual_wheel_hash}"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            sbom_bytes = archive.read(SBOM_PATH)
    except (KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Cannot read embedded tokenizers SBOM: {error}") from error
    try:
        sbom = json.loads(sbom_bytes)
    except (UnicodeError, ValueError) as error:
        raise SystemExit(f"Embedded tokenizers SBOM is invalid JSON: {error}") from error
    components = sbom.get("components")
    if not isinstance(components, list) or len(components) != EXPECTED_COMPONENT_COUNT:
        raise SystemExit(
            "Unexpected tokenizers SBOM component count: "
            f"expected {EXPECTED_COMPONENT_COUNT}, got "
            f"{len(components) if isinstance(components, list) else 'invalid'}"
        )
    return sbom, _sha256(sbom_bytes)


def _license_expression(component: dict[str, Any]) -> str:
    licenses = component.get("licenses")
    if not isinstance(licenses, list) or len(licenses) != 1:
        raise SystemExit(
            f"{component.get('name')}@{component.get('version')} has an "
            "unexpected SBOM license structure"
        )
    expression = licenses[0].get("expression")
    if not isinstance(expression, str) or not expression.strip():
        raise SystemExit(
            f"{component.get('name')}@{component.get('version')} has no "
            "SBOM license expression"
        )
    return " ".join(expression.split())


def _registry_checksum(component: dict[str, Any]) -> str:
    hashes = component.get("hashes")
    if not isinstance(hashes, list):
        raise SystemExit(
            f"{component.get('name')}@{component.get('version')} has no "
            "registry checksum in the SBOM"
        )
    values = [
        entry.get("content")
        for entry in hashes
        if entry.get("alg") == "SHA-256"
    ]
    if len(values) != 1 or not isinstance(values[0], str):
        raise SystemExit(
            f"{component.get('name')}@{component.get('version')} does not "
            "have exactly one SHA-256 registry checksum"
        )
    return values[0]


def _notice_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    return sorted(
        (
            member
            for member in archive.getmembers()
            if member.isfile()
            and NOTICE_BASENAME.match(Path(member.name).name) is not None
        ),
        key=lambda member: member.name,
    )


def _registry_component(
    component: dict[str, Any],
) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    name = component["name"]
    version = component["version"]
    expected_hash = _registry_checksum(component)
    source_url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
    source = _download(source_url)
    actual_hash = _sha256(source)
    if actual_hash != expected_hash:
        raise SystemExit(
            f"Registry checksum mismatch for {name}@{version}: "
            f"expected {expected_hash}, got {actual_hash}"
        )
    try:
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
            members = _notice_members(archive)
            notices = []
            for member in members:
                handle = archive.extractfile(member)
                if handle is None:
                    raise SystemExit(
                        f"Cannot extract notice file {member.name} from "
                        f"{name}@{version}"
                    )
                notices.append((member.name, handle.read()))
    except tarfile.TarError as error:
        raise SystemExit(
            f"Invalid registry source archive for {name}@{version}: {error}"
        ) from error
    if not notices:
        raise SystemExit(
            f"No license/notice files found in exact source for {name}@{version}"
        )
    return (
        {
            "source": source_url,
            "source_sha256": actual_hash,
        },
        notices,
    )


def _tokenizers_component() -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    source = _download(TOKENIZERS_SOURCE_URL)
    try:
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
            members = {member.name: member for member in archive.getmembers()}
            license_member = members.get(TOKENIZERS_LICENSE_PATH)
            if license_member is None or not license_member.isfile():
                raise SystemExit(
                    f"Upstream tokenizers source lacks {TOKENIZERS_LICENSE_PATH}"
                )
            handle = archive.extractfile(license_member)
            if handle is None:
                raise SystemExit("Cannot extract upstream tokenizers LICENSE")
            license_bytes = handle.read()
            if _sha256(license_bytes) != TOKENIZERS_LICENSE_SHA256:
                raise SystemExit(
                    "Upstream tokenizers LICENSE checksum does not match the "
                    "reviewed v0.23.1 source"
                )

            repository_root = f"tokenizers-{TOKENIZERS_COMMIT}"
            component_root = f"{repository_root}/tokenizers"
            relevant_notice_paths = [
                name
                for name, member in members.items()
                if member.isfile()
                and str(Path(name).parent) in {repository_root, component_root}
                and NOTICE_BASENAME.match(Path(name).name) is not None
            ]
            unexpected = sorted(
                name
                for name in relevant_notice_paths
                if name != TOKENIZERS_LICENSE_PATH
            )
            if unexpected:
                raise SystemExit(
                    "The pinned tokenizers component gained additional notice "
                    "files that require review: "
                    + ", ".join(unexpected)
                )
    except tarfile.TarError as error:
        raise SystemExit(f"Invalid upstream tokenizers source archive: {error}") from error
    return (
        {
            "source": (
                "https://github.com/huggingface/tokenizers/tree/"
                + TOKENIZERS_COMMIT
            ),
            "source_sha256": f"git:{TOKENIZERS_COMMIT}",
        },
        [("LICENSE", license_bytes)],
    )


def _clean_text(data: bytes, source: str) -> str:
    if b"\x00" in data:
        raise SystemExit(f"Notice file is not plain text: {source}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SystemExit(f"Notice file is not UTF-8: {source}: {error}") from error


def _render_bundle(sbom: dict[str, Any], sbom_sha256: str) -> str:
    components = sbom["components"]
    seen_pairs: set[tuple[str, str]] = set()
    component_records: list[dict[str, Any]] = []
    blobs: dict[str, dict[str, Any]] = {}
    registry_count = 0

    for component in components:
        name = component.get("name")
        version = component.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise SystemExit("SBOM component lacks a valid name or version")
        pair = (name, version)
        if pair in seen_pairs:
            raise SystemExit(f"Duplicate SBOM component: {name}@{version}")
        seen_pairs.add(pair)

        bom_ref = component.get("bom-ref", "")
        if isinstance(bom_ref, str) and bom_ref.startswith("registry+"):
            source_record, notices = _registry_component(component)
            registry_count += 1
        elif pair == ("tokenizers", "0.23.1"):
            source_record, notices = _tokenizers_component()
        else:
            raise SystemExit(
                f"Unreviewed non-registry SBOM component: {name}@{version}"
            )

        notice_records = []
        for path, data in notices:
            digest = _sha256(data)
            text = _clean_text(data, f"{name}@{version}:{path}")
            blob = blobs.setdefault(
                digest,
                {
                    "text": text,
                    "sources": [],
                },
            )
            if blob["text"] != text:
                raise SystemExit(f"SHA-256 collision while processing {digest}")
            blob["sources"].append(f"{name}@{version}:{path}")
            notice_records.append({"path": path, "sha256": digest})

        component_records.append(
            {
                "name": name,
                "version": version,
                "author": component.get("author") or "Not reported by SBOM",
                "license": _license_expression(component),
                **source_record,
                "notices": notice_records,
            }
        )

    if registry_count != EXPECTED_REGISTRY_COMPONENT_COUNT:
        raise SystemExit(
            "Unexpected registry component count: "
            f"expected {EXPECTED_REGISTRY_COMPONENT_COUNT}, got {registry_count}"
        )

    blob_ids = {
        digest: f"T{number:03d}"
        for number, digest in enumerate(sorted(blobs), start=1)
    }
    lines = [
        "HUGGING FACE TOKENIZERS 0.23.1 - EXACT THIRD-PARTY NOTICE BUNDLE",
        "",
        "Generated by scripts/build_tokenizers_notices.py. DO NOT EDIT BY HAND.",
        "",
        "This bundle covers the exact native tokenizers wheel redistributed by",
        "Smart Search for Anki - Medical. The wheel's embedded CycloneDX SBOM is",
        "the component authority. Each of the 119 crates.io source archives was",
        "accepted only after its SHA-256 matched the SBOM. The one local-path",
        "tokenizers component was resolved to the exact upstream v0.23.1 commit.",
        "",
        "Every license, licence, copying, copyright, notice, and unlicense file",
        "found in those exact source packages is reproduced below. Alternative",
        "license texts are intentionally retained rather than silently choosing a",
        "license branch. Identical byte-for-byte texts are stored once and mapped",
        "back to every component that supplied them.",
        "",
        f"Wheel: {WHEEL_RELATIVE_PATH}",
        f"Wheel-SHA256: {WHEEL_SHA256}",
        f"Embedded-SBOM: {SBOM_PATH}",
        f"Embedded-SBOM-SHA256: {sbom_sha256}",
        f"SBOM-Spec-Version: {sbom.get('specVersion', 'not reported')}",
        f"Component-Count: {len(component_records)}",
        f"Registry-Archive-Count: {registry_count}",
        f"Unique-Notice-Text-Count: {len(blobs)}",
        f"Tokenizers-Upstream-Commit: {TOKENIZERS_COMMIT}",
        "",
        "COMPONENT INDEX AND NOTICE MAP",
        "================================",
        "",
    ]

    for number, record in enumerate(component_records, start=1):
        lines.extend(
            [
                f"[{number:03d}] {record['name']} {record['version']}",
                f"SBOM author: {record['author']}",
                f"SBOM license expression: {record['license']}",
                f"Source: {record['source']}",
                f"Source integrity: {record['source_sha256']}",
                "Included source notice files:",
            ]
        )
        for notice in record["notices"]:
            lines.append(
                f"  - {notice['path']} -> "
                f"{blob_ids[notice['sha256']]} "
                f"(SHA-256 {notice['sha256']})"
            )
        lines.append("")

    lines.extend(
        [
            "VERBATIM NOTICE AND LICENSE TEXTS",
            "=================================",
            "",
        ]
    )
    for digest in sorted(blobs):
        blob = blobs[digest]
        lines.extend(
            [
                f"{blob_ids[digest]}",
                f"SHA-256: {digest}",
                "Supplied by:",
            ]
        )
        lines.extend(f"  - {source}" for source in sorted(blob["sources"]))
        lines.extend(["----- BEGIN VERBATIM TEXT -----"])
        text = blob["text"]
        lines.append(text)
        if not text.endswith("\n"):
            lines.append("")
        lines.extend(["----- END VERBATIM TEXT -----", ""])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Add-on source root",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=f"Output path (default: ROOT/{OUTPUT_RELATIVE_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the checked-in bundle matches a clean regeneration",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    wheel = root / WHEEL_RELATIVE_PATH
    output = args.output or (root / OUTPUT_RELATIVE_PATH)
    sbom, sbom_sha256 = _read_sbom(wheel)
    rendered = _render_bundle(sbom, sbom_sha256)

    if args.check:
        try:
            existing = output.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise SystemExit(f"Notice bundle does not exist: {output}") from error
        if existing != rendered:
            raise SystemExit(
                f"Notice bundle is stale; regenerate {output} without --check"
            )
        print(f"Verified deterministic notice bundle: {output}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote {output}")
    print(f"SHA-256: {_sha256(output.read_bytes())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
