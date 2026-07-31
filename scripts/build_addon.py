#!/usr/bin/env python3
"""Build and validate a deterministic external Anki add-on package."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import py_compile
import re
import tempfile
import zipfile


PUBLIC_TOP_LEVEL_FILES = {
    "CHANGELOG.md",
    "DATA_SOURCES.md",
    "LICENSE.txt",
    "PRIVACY.md",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "THIRD_PARTY_NOTICES.md",
    "__init__.py",
    "anki_actions.py",
    "anki_adapter.py",
    "config.json",
    "config.md",
    "controller.py",
    "inline_preview.py",
    "manifest.json",
}
PUBLIC_CODE_DIRECTORIES = {
    "backend",
    "semantic",
    "ui",
}
PUBLIC_RESOURCE_FILES = {
    "resources/medbrevia-logo.png",
    "resources/medical_vocab/rxterms_202607.json.gz",
}
PUBLIC_LICENSE_FILES = {
    "licenses/Apache-2.0.txt",
    "licenses/bge-small-en-v1.5-MIT.txt",
    "licenses/numpy-LICENSE.txt",
    "licenses/onnxruntime-LICENSE.txt",
    "licenses/onnxruntime-ThirdPartyNotices.txt",
    "licenses/tokenizers-0.23.1-NOTICES.txt",
    "licenses/tokenizers-0.23.1-transitive-inventory.md",
}
PUBLIC_USER_FILES = {
    "user_files/README.txt",
}
BUNDLED_WHEEL_SHA256 = {
    (
        "vendor_wheels/darwin-arm64-py313/"
        "flatbuffers-25.12.19-py2.py3-none-any.whl"
    ): "7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4",
    (
        "vendor_wheels/darwin-arm64-py313/"
        "numpy-2.5.1-cp313-cp313-macosx_14_0_arm64.whl"
    ): "6165343f81b56ef8f514f396989e529b61d9dc709b99421b07e9f3e698e2287d",
    (
        "vendor_wheels/darwin-arm64-py313/"
        "onnxruntime-1.28.0-cp313-cp313-macosx_14_0_arm64.whl"
    ): "31410f544674f534c2f27348af52ef81682ca9c8719154bf4d48f0ef23823b1e",
    (
        "vendor_wheels/darwin-arm64-py313/"
        "tokenizers-0.23.1-cp310-abi3-macosx_11_0_arm64.whl"
    ): "e0948bbb1ac1d7cdfc9fb6d62c596e3b7550036ad60ecd654a66ad273326324e",
}
TOKENIZERS_NOTICE_BUNDLE_SHA256 = (
    "514952837bda657205f2d31ed08dc7b4796752c6eea9abdacb9e5908dfde1116"
)
CONTROLLED_DIRECTORY_FILES = (
    PUBLIC_RESOURCE_FILES
    | PUBLIC_LICENSE_FILES
    | PUBLIC_USER_FILES
    | set(BUNDLED_WHEEL_SHA256)
)
REQUIRED = {
    "__init__.py",
    "manifest.json",
    "config.json",
    "anki_actions.py",
    "inline_preview.py",
    "backend/__init__.py",
    "resources/medbrevia-logo.png",
    "ui/__init__.py",
    "semantic/__init__.py",
    "semantic/model_manager.py",
    "user_files/README.txt",
} | CONTROLLED_DIRECTORY_FILES

# A public build intentionally ships wheel archives, not the locally expanded
# runtime or downloaded model.  Keeping this well below AnkiWeb's approximate
# upload ceiling also catches accidental payload regressions.
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
ZIP_TIMESTAMP = (2026, 7, 29, 0, 0, 0)

# These development directories are never distribution inputs and may contain
# generated archives/databases of their own.
IGNORED_DEVELOPMENT_TOP_LEVEL = {
    ".git",
    ".pytest_cache",
    "dist",
    "scripts",
    "tests",
}
GENERATED_USER_FILE_DIRECTORIES = {
    "model",
    "runtime",
}
SENSITIVE_EXACT_NAMES = {
    ".env",
    "collection.anki2",
    "collection.anki21",
    "credentials.json",
    "search.sqlite",
    "search.sqlite3",
    "secrets.json",
    "semantic.sqlite",
    "semantic.sqlite3",
    "token.json",
    "vectors.bin",
    "vectors.dat",
    "vectors.npy",
    "vectors.npz",
}
SENSITIVE_SUFFIXES = {
    ".anki2",
    ".anki21",
    ".apkg",
    ".colpkg",
    ".db",
    ".faiss",
    ".key",
    ".log",
    ".pem",
    ".pfx",
    ".p12",
    ".sqlite",
    ".sqlite3",
}
PUBLIC_TEXT_SUFFIXES = {".json", ".md", ".py", ".txt"}
FORBIDDEN_PUBLIC_TEXT = {
    "local macOS home path": re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    "local Linux home path": re.compile(r"/home/[A-Za-z0-9._-]+/"),
    "local Windows home path": re.compile(
        r"[A-Za-z]:\\\\Users\\\\[A-Za-z0-9._ -]+\\\\"
    ),
    "private key": re.compile(
        r"-----BEGIN (?:EC |OPENSSH |RSA |DSA )?PRIVATE KEY-----"
    ),
    "GitHub credential": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "Hugging Face credential": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private developer email": re.compile(
        r"salehmostafamain@gmail\\.com",
        re.IGNORECASE,
    ),
}


def _relative_name(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_ignored_generated_path(relative: Path) -> bool:
    return (
        len(relative.parts) >= 2
        and relative.parts[0] == "user_files"
        and relative.parts[1] in GENERATED_USER_FILE_DIRECTORIES
    )


def _looks_sensitive(relative: Path) -> bool:
    lowered = relative.name.casefold()
    if lowered in SENSITIVE_EXACT_NAMES or lowered.startswith(".env."):
        return True
    return any(lowered.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)


def validate_source_safety(root: Path) -> None:
    """Reject private/generated data before enumerating package contents.

    Locally installed model/runtime assets are expected during development and
    are deliberately ignored.  Any other content beneath ``user_files`` is
    treated as profile-owned data and blocks a public build.
    """

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] in IGNORED_DEVELOPMENT_TOP_LEVEL:
            continue
        if "__pycache__" in relative.parts:
            continue

        if relative.parts[0] == "user_files":
            relative_name = relative.as_posix()
            if relative_name in PUBLIC_USER_FILES:
                continue
            if _is_ignored_generated_path(relative):
                continue
            raise SystemExit(
                "Refusing to build with profile/private data under user_files: "
                + relative_name
            )

        if _looks_sensitive(relative):
            raise SystemExit(
                "Refusing to build with a private or generated artifact: "
                + relative.as_posix()
            )


def _controlled_actual_files(root: Path, directory: str) -> set[str]:
    base = root / directory
    if not base.exists():
        return set()
    return {
        _relative_name(root, path)
        for path in base.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.relative_to(root).parts
        and path.name != ".DS_Store"
        and not _is_ignored_generated_path(path.relative_to(root))
    }


def validate_controlled_directories(root: Path) -> None:
    """Require an explicit code review for every bundled binary/static file."""

    expected_by_directory = {
        "resources": PUBLIC_RESOURCE_FILES,
        "licenses": PUBLIC_LICENSE_FILES,
        "user_files": PUBLIC_USER_FILES,
        "vendor_wheels": set(BUNDLED_WHEEL_SHA256),
    }
    for directory, expected in expected_by_directory.items():
        actual = _controlled_actual_files(root, directory)
        unexpected = sorted(actual - expected)
        if unexpected:
            raise SystemExit(
                f"Unapproved public files under {directory}: "
                + ", ".join(unexpected)
            )


def included_files(root: Path) -> list[Path]:
    validate_source_safety(root)
    validate_controlled_directories(root)

    public_names = (
        PUBLIC_TOP_LEVEL_FILES
        | PUBLIC_RESOURCE_FILES
        | PUBLIC_LICENSE_FILES
        | PUBLIC_USER_FILES
        | set(BUNDLED_WHEEL_SHA256)
    )
    output = [root / name for name in sorted(public_names)]
    for directory in sorted(PUBLIC_CODE_DIRECTORIES):
        output.extend(
            path
            for path in (root / directory).rglob("*.py")
            if "__pycache__" not in path.relative_to(root).parts
        )
    return sorted(output, key=lambda item: item.relative_to(root).as_posix())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_bundled_payloads(root: Path) -> None:
    for relative_name, expected_digest in BUNDLED_WHEEL_SHA256.items():
        wheel = root / relative_name
        actual_digest = _sha256_file(wheel)
        if actual_digest != expected_digest:
            raise SystemExit(
                f"Checksum mismatch for deliberately bundled wheel {relative_name}: "
                f"expected {expected_digest}, got {actual_digest}"
            )
        try:
            with zipfile.ZipFile(wheel) as archive:
                bad = archive.testzip()
                if bad:
                    raise SystemExit(
                        f"Bundled wheel CRC validation failed for {relative_name}: {bad}"
                    )
        except zipfile.BadZipFile as error:
            raise SystemExit(
                f"Bundled wheel is not a valid wheel archive: {relative_name}"
            ) from error

    logo = root / "resources/medbrevia-logo.png"
    if not logo.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
        raise SystemExit("resources/medbrevia-logo.png is not a valid PNG payload")

    aliases = root / "resources/medical_vocab/rxterms_202607.json.gz"
    try:
        with gzip.open(aliases, "rt", encoding="utf-8") as handle:
            aliases_payload = json.load(handle)
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit(
            "resources/medical_vocab/rxterms_202607.json.gz is invalid"
        ) from error
    if not isinstance(aliases_payload, dict) or not isinstance(
        aliases_payload.get("aliases"), list
    ):
        raise SystemExit("The RxTerms resource is missing its aliases list")

    for relative_name in sorted(PUBLIC_LICENSE_FILES):
        if not (root / relative_name).read_text(encoding="utf-8").strip():
            raise SystemExit(f"Required license/notice is empty: {relative_name}")

    # This is a generated, source-verified bundle for the exact 120-component
    # SBOM embedded in the pinned tokenizers wheel.  Pinning the finished
    # bundle makes any regenerated or hand-edited legal payload require an
    # explicit release review.
    tokenizers_notices = root / "licenses/tokenizers-0.23.1-NOTICES.txt"
    actual_notice_digest = _sha256_file(tokenizers_notices)
    if actual_notice_digest != TOKENIZERS_NOTICE_BUNDLE_SHA256:
        raise SystemExit(
            "Checksum mismatch for the reviewed tokenizers notice bundle: "
            f"expected {TOKENIZERS_NOTICE_BUNDLE_SHA256}, "
            f"got {actual_notice_digest}"
        )


def validate_sources(root: Path, files: list[Path]) -> None:
    relative_names = {path.relative_to(root).as_posix() for path in files}
    missing = sorted(REQUIRED - relative_names)
    if missing:
        raise SystemExit("Missing required package files: " + ", ".join(missing))
    missing_on_disk = sorted(
        relative_name
        for relative_name in relative_names
        if not (root / relative_name).is_file()
    )
    if missing_on_disk:
        raise SystemExit(
            "Missing allowlisted package files: " + ", ".join(missing_on_disk)
        )
    symlinks = sorted(
        path.relative_to(root).as_posix() for path in files if path.is_symlink()
    )
    if symlinks:
        raise SystemExit(
            "Public package inputs must be regular files, not symlinks: "
            + ", ".join(symlinks)
        )

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("package") or not manifest.get("name"):
        raise SystemExit("manifest.json must define package and name")
    validate_release_versions(root, manifest)
    json.loads((root / "config.json").read_text(encoding="utf-8"))
    validate_public_text(files)
    validate_bundled_payloads(root)

    with tempfile.TemporaryDirectory() as directory:
        compile_root = Path(directory)
        for path in files:
            if path.suffix == ".py":
                destination = compile_root / (path.relative_to(root).as_posix() + "c")
                destination.parent.mkdir(parents=True, exist_ok=True)
                py_compile.compile(str(path), cfile=str(destination), doraise=True)


def validate_release_versions(root: Path, manifest: dict[str, object]) -> None:
    """Keep every user-visible/package version tied to the manifest."""

    version = str(manifest.get("human_version", "")).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise SystemExit("manifest.json must define a valid human_version")

    declarations = (
        (
            "__init__.py",
            re.compile(r'(?m)^__version__\s*=\s*"([^"]+)"'),
        ),
        (
            "ui/__init__.py",
            re.compile(r'(?m)^__version__\s*=\s*"([^"]+)"'),
        ),
        (
            "semantic/model_manager.py",
            re.compile(
                r'(?m)^DOWNLOAD_USER_AGENT\s*=\s*'
                r'"Smart-Search-for-Anki/([^"]+)"'
            ),
        ),
    )
    for relative_name, pattern in declarations:
        text = (root / relative_name).read_text(encoding="utf-8")
        match = pattern.search(text)
        declared = match.group(1) if match else None
        if declared != version:
            raise SystemExit(
                f"Release version mismatch in {relative_name}: "
                f"expected {version}, got {declared or 'missing'}"
            )

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(
        rf"(?m)^## \[{re.escape(version)}\](?:\s|$)",
        changelog,
    ):
        raise SystemExit(
            f"CHANGELOG.md has no release heading for version {version}"
        )


def default_output_path(root: Path) -> Path:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest.get("human_version", "")).strip()
    if not version:
        raise SystemExit("manifest.json must define human_version")
    return root / "dist" / f"Smart_Search_Medical_{version}.ankiaddon"


def validate_public_text(files: list[Path]) -> None:
    """Reject common local-path and credential leaks from public text files."""

    for path in files:
        if path.suffix.casefold() not in PUBLIC_TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise SystemExit(f"Public text file is not valid UTF-8: {path}") from error
        for label, pattern in FORBIDDEN_PUBLIC_TEXT.items():
            if pattern.search(text):
                raise SystemExit(
                    f"Refusing to package {label} found in {path.name}"
                )


def build(root: Path, destination: Path) -> dict[str, int | str]:
    files = included_files(root)
    validate_sources(root, files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()

    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for source in files:
            relative = PurePosixPath(source.relative_to(root).as_posix())
            info = zipfile.ZipInfo(relative.as_posix(), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)

    temporary.replace(destination)
    if destination.stat().st_size > MAX_PACKAGE_BYTES:
        destination.unlink()
        raise SystemExit("The add-on package exceeds the 64 MB public release budget.")

    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise SystemExit("The package contains duplicate archive paths.")
        if any(
            name.startswith("/") or ".." in PurePosixPath(name).parts for name in names
        ):
            raise SystemExit("The package contains an unsafe archive path.")
        if any(
            name.startswith("user_files/") and name not in PUBLIC_USER_FILES
            for name in names
        ):
            raise SystemExit(
                "The package contains generated or profile-owned user_files data."
            )
        if any(_looks_sensitive(Path(name)) for name in names):
            raise SystemExit(
                "The package contains a private or generated sensitive artifact."
            )
        bad = archive.testzip()
        if bad:
            raise SystemExit(f"Archive CRC validation failed for {bad}")

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum_path = destination.with_suffix(destination.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    return {
        "files": len(files),
        "bytes": destination.stat().st_size,
        "sha256": digest,
        "artifact": str(destination),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    output = (
        arguments.output.resolve()
        if arguments.output is not None
        else default_output_path(root)
    )
    print(
        json.dumps(
            build(root, output),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
