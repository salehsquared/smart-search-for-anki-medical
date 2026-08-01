#!/usr/bin/env python3
"""Rebuild the third-party notice bundle for tokenizers 0.20.3.

The 0.20.3 wheel predates tokenizers' embedded CycloneDX SBOM.  Its exact,
version-matched PyPI sdist is therefore the dependency authority.  This script
checksum-verifies the wheel and sdist, resolves the non-dev dependency closure
from the sdist's Cargo.lock, checksum-verifies every crates.io archive, and
preserves every license/notice file found in those exact sources.
"""

from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import re
import tarfile
from typing import Any
from urllib.request import Request, urlopen
import zipfile

try:
    import tomllib
except ImportError as error:  # pragma: no cover - release tooling only
    raise SystemExit("Python 3.11+ is required to rebuild this notice bundle") from error


WHEEL_RELATIVE_PATH = (
    "vendor_wheels/darwin-arm64-py39/"
    "tokenizers-0.20.3-cp39-cp39-macosx_11_0_arm64.whl"
)
WHEEL_SHA256 = "f4cb0c614b0135e781de96c2af87e73da0389ac1458e2a97562ed26e29490d8d"
WHEEL_METADATA_PATH = "tokenizers-0.20.3.dist-info/METADATA"
WHEEL_METADATA_SHA256 = (
    "d6e2927bc0a81c0a318e2e14d9a8f54dffb5fc5aba0055982784c231eb3f6d28"
)

SDIST_URL = (
    "https://files.pythonhosted.org/packages/da/25/"
    "b1681c1c30ea3ea6e584ae3fffd552430b12faa599b558c4c4783f56d7ff/"
    "tokenizers-0.20.3.tar.gz"
)
SDIST_SHA256 = "2278b34c5d0dd78e087e1ca7f9b1dcbf129d80211afa645f214bd6e051037539"
SDIST_ROOT = "tokenizers-0.20.3"
CARGO_LOCK_PATH = f"{SDIST_ROOT}/bindings/python/Cargo.lock"
CARGO_LOCK_SHA256 = (
    "41ae195f80e1bb41ced42f4a195bbb7874be878c2b8673623d45a0fdac42e596"
)
PYTHON_CARGO_TOML_PATH = f"{SDIST_ROOT}/bindings/python/Cargo.toml"
PYTHON_CARGO_TOML_SHA256 = (
    "212322ef5157ac6f3a8198f47b95767a97909006fd067d2e1bfe35edcde91690"
)
TOKENIZERS_CARGO_TOML_PATH = f"{SDIST_ROOT}/tokenizers/Cargo.toml"
TOKENIZERS_CARGO_TOML_SHA256 = (
    "605916604e53acd6fed95b3ac40869b54a7ebd234c926a61835215bc78e8dd16"
)
TOKENIZERS_LICENSE_PATH = f"{SDIST_ROOT}/tokenizers/LICENSE"
TOKENIZERS_LICENSE_SHA256 = (
    "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
)

EXPECTED_COMPONENT_COUNT = 120
EXPECTED_REGISTRY_COMPONENT_COUNT = 118
OUTPUT_RELATIVE_PATH = "licenses/tokenizers-0.20.3-NOTICES.txt"

# Cargo.lock includes the workspace's dev-only roots.  These reviewed lists are
# the ordinary dependencies plus the optional dependencies activated by the
# tokenizers crate's default features (progressbar, onig, and esaxx_fast).
PYTHON_RUNTIME_ROOTS = frozenset(
    {
        "env_logger",
        "itertools",
        "libc",
        "ndarray",
        "numpy",
        "pyo3",
        "rayon",
        "serde",
        "serde_json",
        "tokenizers",
    }
)
TOKENIZERS_RUNTIME_ROOTS = frozenset(
    {
        "aho-corasick",
        "derive_builder",
        "esaxx-rs",
        "getrandom",
        "indicatif",
        "itertools",
        "lazy_static",
        "log",
        "macro_rules_attribute",
        "monostate",
        "onig",
        "paste",
        "rand",
        "rayon",
        "rayon-cond",
        "regex",
        "regex-syntax",
        "serde",
        "serde_json",
        "spm_precompiled",
        "thiserror",
        "unicode-normalization-alignments",
        "unicode-segmentation",
        "unicode_categories",
    }
)

NOTICE_BASENAME = re.compile(
    r"^(?:LICENSE|LICENCE|COPYING|COPYRIGHT|NOTICE|UNLICENSE)(?:[._-].*)?$",
    re.IGNORECASE,
)

# number_prefix intentionally excludes its LICENCE from the crates.io archive.
# Cargo's embedded VCS record pins this exact repository commit, so retrieve
# the omitted notice from that immutable revision and verify it independently.
MISSING_CRATE_NOTICES = {
    ("number_prefix", "0.4.0"): (
        "repository@eb6ebd215d50df1b199737f0356b988bebaedc84:LICENCE",
        (
            "https://raw.githubusercontent.com/ogham/rust-number-prefix/"
            "eb6ebd215d50df1b199737f0356b988bebaedc84/LICENCE"
        ),
        "df8d11b64ecce43b1229cd72745c59513b54ba39f05e3ec7c06780617b7b1fcc",
    )
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "Smart-Search-for-Anki-notice-builder/1.0"},
    )
    with urlopen(request, timeout=120) as response:
        return response.read()


def _read_tar_member(archive: tarfile.TarFile, path: str) -> bytes:
    try:
        member = archive.getmember(path)
    except KeyError as error:
        raise SystemExit(f"Pinned tokenizers sdist lacks {path}") from error
    if not member.isfile():
        raise SystemExit(f"Pinned tokenizers sdist member is not a file: {path}")
    handle = archive.extractfile(member)
    if handle is None:
        raise SystemExit(f"Cannot extract pinned tokenizers sdist member: {path}")
    return handle.read()


def _verified_source() -> tuple[list[dict[str, Any]], bytes, str]:
    source = _download(SDIST_URL)
    actual_sdist_hash = _sha256(source)
    if actual_sdist_hash != SDIST_SHA256:
        raise SystemExit(
            f"Tokenizers sdist checksum mismatch: expected {SDIST_SHA256}, "
            f"got {actual_sdist_hash}"
        )
    try:
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
            lock_bytes = _read_tar_member(archive, CARGO_LOCK_PATH)
            python_toml_bytes = _read_tar_member(archive, PYTHON_CARGO_TOML_PATH)
            tokenizers_toml_bytes = _read_tar_member(
                archive, TOKENIZERS_CARGO_TOML_PATH
            )
            license_bytes = _read_tar_member(archive, TOKENIZERS_LICENSE_PATH)
    except tarfile.TarError as error:
        raise SystemExit(f"Invalid pinned tokenizers sdist: {error}") from error

    expected_hashes = {
        CARGO_LOCK_PATH: (lock_bytes, CARGO_LOCK_SHA256),
        PYTHON_CARGO_TOML_PATH: (python_toml_bytes, PYTHON_CARGO_TOML_SHA256),
        TOKENIZERS_CARGO_TOML_PATH: (
            tokenizers_toml_bytes,
            TOKENIZERS_CARGO_TOML_SHA256,
        ),
        TOKENIZERS_LICENSE_PATH: (license_bytes, TOKENIZERS_LICENSE_SHA256),
    }
    for path, (data, expected_hash) in expected_hashes.items():
        actual_hash = _sha256(data)
        if actual_hash != expected_hash:
            raise SystemExit(
                f"Pinned tokenizers sdist member mismatch for {path}: "
                f"expected {expected_hash}, got {actual_hash}"
            )

    lock = tomllib.loads(lock_bytes.decode("utf-8"))
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise SystemExit("Pinned Cargo.lock has no package inventory")

    # The manifests are hash-pinned above.  Recheck the reviewed root names so
    # a future edit to this generator cannot silently add or omit a root.
    python_manifest = tomllib.loads(python_toml_bytes.decode("utf-8"))
    tokenizers_manifest = tomllib.loads(tokenizers_toml_bytes.decode("utf-8"))
    python_dependencies = python_manifest.get("dependencies", {})
    tokenizers_dependencies = tokenizers_manifest.get("dependencies", {})
    python_non_optional = {
        name
        for name, spec in python_dependencies.items()
        if not (isinstance(spec, dict) and spec.get("optional") is True)
    }
    tokenizers_non_optional = {
        name
        for name, spec in tokenizers_dependencies.items()
        if not (isinstance(spec, dict) and spec.get("optional") is True)
    }
    if python_non_optional != PYTHON_RUNTIME_ROOTS:
        raise SystemExit("Reviewed Python runtime dependency roots are stale")
    if not tokenizers_non_optional.issubset(TOKENIZERS_RUNTIME_ROOTS):
        raise SystemExit("Reviewed tokenizers runtime dependency roots are stale")
    optional_enabled = TOKENIZERS_RUNTIME_ROOTS - tokenizers_non_optional
    if optional_enabled != {"indicatif", "onig"}:
        raise SystemExit("Reviewed tokenizers default-feature roots are stale")

    return packages, license_bytes, actual_sdist_hash


def _package_pair(package: dict[str, Any]) -> tuple[str, str]:
    name = package.get("name")
    version = package.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise SystemExit("Cargo.lock package lacks a valid name or version")
    return name, version


def _resolved_components(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for package in packages:
        pair = _package_pair(package)
        if pair in by_pair:
            raise SystemExit(f"Duplicate Cargo.lock package: {pair[0]}@{pair[1]}")
        by_pair[pair] = package
        by_name.setdefault(pair[0], []).append(package)

    local_pairs = {
        ("tokenizers", "0.20.3"),
        ("tokenizers-python", "0.20.3"),
    }
    if not local_pairs.issubset(by_pair):
        raise SystemExit("Pinned Cargo.lock lacks reviewed local packages")

    def dependency_pair(dependency: str) -> tuple[str, str]:
        candidates = by_name.get(dependency, [])
        if len(candidates) == 1:
            return _package_pair(candidates[0])
        for package in candidates:
            pair = _package_pair(package)
            if dependency == f"{pair[0]} {pair[1]}":
                return pair
        name, separator, version = dependency.rpartition(" ")
        if separator:
            package = by_pair.get((name, version))
            if package is not None:
                return _package_pair(package)
        raise SystemExit(f"Cannot resolve Cargo.lock dependency: {dependency}")

    def reviewed_roots(pair: tuple[str, str], names: frozenset[str]) -> list[tuple[str, str]]:
        package = by_pair[pair]
        dependencies = package.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise SystemExit(f"Cargo.lock dependencies are invalid for {pair[0]}")
        roots: list[tuple[str, str]] = []
        for name in sorted(names):
            matches = [
                dependency
                for dependency in dependencies
                if dependency == name or dependency.startswith(name + " ")
            ]
            if len(matches) != 1:
                raise SystemExit(
                    f"Expected one Cargo.lock root for {pair[0]} -> {name}; "
                    f"got {len(matches)}"
                )
            roots.append(dependency_pair(matches[0]))
        return roots

    stack = reviewed_roots(
        ("tokenizers-python", "0.20.3"), PYTHON_RUNTIME_ROOTS
    ) + reviewed_roots(("tokenizers", "0.20.3"), TOKENIZERS_RUNTIME_ROOTS)
    resolved = set(local_pairs)
    while stack:
        pair = stack.pop()
        if pair in resolved:
            continue
        package = by_pair.get(pair)
        if package is None:
            raise SystemExit(f"Cargo.lock dependency is missing: {pair[0]}@{pair[1]}")
        resolved.add(pair)
        dependencies = package.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise SystemExit(f"Cargo.lock dependencies are invalid for {pair[0]}")
        stack.extend(dependency_pair(dependency) for dependency in dependencies)

    selected = [by_pair[pair] for pair in sorted(resolved)]
    registry_count = sum(
        1
        for package in selected
        if str(package.get("source", "")).startswith("registry+")
    )
    if len(selected) != EXPECTED_COMPONENT_COUNT:
        raise SystemExit(
            f"Unexpected runtime component count: expected {EXPECTED_COMPONENT_COUNT}, "
            f"got {len(selected)}"
        )
    if registry_count != EXPECTED_REGISTRY_COMPONENT_COUNT:
        raise SystemExit(
            "Unexpected registry component count: "
            f"expected {EXPECTED_REGISTRY_COMPONENT_COUNT}, got {registry_count}"
        )
    return selected


def _notice_members(
    archive: tarfile.TarFile, license_file: str | None
) -> list[tarfile.TarInfo]:
    matches = []
    for member in archive.getmembers():
        if not member.isfile():
            continue
        basename = Path(member.name).name
        if NOTICE_BASENAME.match(basename) is not None:
            matches.append(member)
        elif license_file and member.name.endswith("/" + license_file):
            matches.append(member)
    return sorted({member.name: member for member in matches}.values(), key=lambda m: m.name)


def _registry_component(
    package: dict[str, Any],
) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    name, version = _package_pair(package)
    expected_hash = package.get("checksum")
    if not isinstance(expected_hash, str):
        raise SystemExit(f"Cargo.lock lacks checksum for {name}@{version}")
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
            cargo_toml_path = f"{name}-{version}/Cargo.toml"
            cargo_toml_bytes = _read_tar_member(archive, cargo_toml_path)
            cargo_toml = tomllib.loads(cargo_toml_bytes.decode("utf-8"))
            cargo_package = cargo_toml.get("package", {})
            license_expression = cargo_package.get("license")
            license_file = cargo_package.get("license-file")
            if not isinstance(license_expression, str):
                if isinstance(license_file, str):
                    license_expression = f"License file: {license_file}"
                else:
                    license_expression = "Not declared in packaged Cargo.toml"
            members = _notice_members(
                archive, license_file if isinstance(license_file, str) else None
            )
            notices = []
            for member in members:
                handle = archive.extractfile(member)
                if handle is None:
                    raise SystemExit(
                        f"Cannot extract notice file {member.name} from {name}@{version}"
                    )
                notices.append((member.name, handle.read()))
    except tarfile.TarError as error:
        raise SystemExit(
            f"Invalid registry source archive for {name}@{version}: {error}"
        ) from error
    if not notices:
        reviewed_notice = MISSING_CRATE_NOTICES.get((name, version))
        if reviewed_notice is None:
            raise SystemExit(f"No license/notice files found for {name}@{version}")
        notice_path, notice_url, expected_notice_hash = reviewed_notice
        notice_bytes = _download(notice_url)
        actual_notice_hash = _sha256(notice_bytes)
        if actual_notice_hash != expected_notice_hash:
            raise SystemExit(
                f"Reviewed repository notice mismatch for {name}@{version}: "
                f"expected {expected_notice_hash}, got {actual_notice_hash}"
            )
        notices = [(notice_path, notice_bytes)]
    return (
        {
            "license": license_expression,
            "source": source_url,
            "source_sha256": actual_hash,
        },
        notices,
    )


def _clean_text(data: bytes, source: str) -> str:
    if b"\x00" in data:
        raise SystemExit(f"Notice file is not plain text: {source}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SystemExit(f"Notice file is not UTF-8: {source}: {error}") from error


def _validate_wheel(wheel: Path) -> None:
    wheel_bytes = wheel.read_bytes()
    actual_hash = _sha256(wheel_bytes)
    if actual_hash != WHEEL_SHA256:
        raise SystemExit(
            f"Tokenizers wheel checksum mismatch: expected {WHEEL_SHA256}, "
            f"got {actual_hash}"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            metadata = archive.read(WHEEL_METADATA_PATH)
    except (KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Cannot read tokenizers wheel metadata: {error}") from error
    if _sha256(metadata) != WHEEL_METADATA_SHA256:
        raise SystemExit("Tokenizers wheel metadata checksum mismatch")


def _render_bundle(
    packages: list[dict[str, Any]], license_bytes: bytes, sdist_hash: str
) -> str:
    component_records: list[dict[str, Any]] = []
    blobs: dict[str, dict[str, Any]] = {}
    registry_count = 0

    for package in packages:
        name, version = _package_pair(package)
        source = str(package.get("source", ""))
        if source.startswith("registry+"):
            source_record, notices = _registry_component(package)
            registry_count += 1
        elif (name, version) in {
            ("tokenizers", "0.20.3"),
            ("tokenizers-python", "0.20.3"),
        }:
            source_record = {
                "license": "Apache-2.0 (repository license)",
                "source": SDIST_URL,
                "source_sha256": sdist_hash,
            }
            notices = [(TOKENIZERS_LICENSE_PATH, license_bytes)]
        else:
            raise SystemExit(f"Unreviewed non-registry package: {name}@{version}")

        notice_records = []
        for path, data in notices:
            digest = _sha256(data)
            text = _clean_text(data, f"{name}@{version}:{path}")
            blob = blobs.setdefault(digest, {"text": text, "sources": []})
            if blob["text"] != text:
                raise SystemExit(f"SHA-256 collision while processing {digest}")
            blob["sources"].append(f"{name}@{version}:{path}")
            notice_records.append({"path": path, "sha256": digest})

        component_records.append(
            {
                "name": name,
                "version": version,
                **source_record,
                "notices": notice_records,
            }
        )

    blob_ids = {
        digest: f"T{number:03d}"
        for number, digest in enumerate(sorted(blobs), start=1)
    }
    lines = [
        "HUGGING FACE TOKENIZERS 0.20.3 - THIRD-PARTY NOTICE BUNDLE",
        "",
        "Generated by scripts/build_tokenizers_0203_notices.py. DO NOT EDIT BY HAND.",
        "",
        "The tokenizers 0.20.3 wheel predates embedded CycloneDX SBOMs. Its",
        "exact, version-matched PyPI source distribution and Cargo.lock are the",
        "dependency authority for this conservative notice bundle. The generator",
        "excludes the source workspace's dev-only root closure. Cargo.lock can",
        "still retain target-specific packages that a particular platform binary",
        "does not link, so this bundle may intentionally over-report rather than",
        "omit attribution.",
        "",
        "Every license, licence, copying, copyright, notice, and unlicense file",
        "found in the exact resolved source packages is reproduced below.",
        "Alternative license texts are retained rather than silently selecting a",
        "license branch. Identical byte-for-byte texts are stored once and mapped",
        "back to every component that supplied them.",
        "",
        f"Wheel: {WHEEL_RELATIVE_PATH}",
        f"Wheel-SHA256: {WHEEL_SHA256}",
        f"Wheel-Metadata: {WHEEL_METADATA_PATH}",
        f"Wheel-Metadata-SHA256: {WHEEL_METADATA_SHA256}",
        f"Version-Matched-Sdist: {SDIST_URL}",
        f"Sdist-SHA256: {sdist_hash}",
        f"Cargo-Lock: {CARGO_LOCK_PATH}",
        f"Cargo-Lock-SHA256: {CARGO_LOCK_SHA256}",
        f"Component-Count: {len(component_records)}",
        f"Registry-Archive-Count: {registry_count}",
        f"Unique-Notice-Text-Count: {len(blobs)}",
        "",
        "COMPONENT INDEX AND NOTICE MAP",
        "================================",
        "",
    ]

    for number, record in enumerate(component_records, start=1):
        lines.extend(
            [
                f"[{number:03d}] {record['name']} {record['version']}",
                f"License declaration: {record['license']}",
                f"Source: {record['source']}",
                f"Source integrity: {record['source_sha256']}",
                "Included source notice files:",
            ]
        )
        for notice in record["notices"]:
            lines.append(
                f"  - {notice['path']} -> {blob_ids[notice['sha256']]} "
                f"(SHA-256 {notice['sha256']})"
            )
        lines.append("")

    lines.extend(["VERBATIM NOTICE AND LICENSE TEXTS", "=================================", ""])
    for digest in sorted(blobs):
        blob = blobs[digest]
        lines.extend([f"{blob_ids[digest]}", f"SHA-256: {digest}", "Supplied by:"])
        lines.extend(f"  - {source}" for source in sorted(blob["sources"]))
        lines.append("----- BEGIN VERBATIM TEXT -----")
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
    _validate_wheel(wheel)
    packages, license_bytes, sdist_hash = _verified_source()
    selected = _resolved_components(packages)
    rendered = _render_bundle(selected, license_bytes, sdist_hash)

    if args.check:
        try:
            # Preserve CRLF sequences inside verbatim upstream notices.  The
            # default text reader performs universal-newline translation and
            # would incorrectly report a byte-identical bundle as stale.
            existing = output.read_bytes().decode("utf-8")
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
