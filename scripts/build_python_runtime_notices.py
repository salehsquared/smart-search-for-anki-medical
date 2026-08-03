#!/usr/bin/env python3
"""Rebuild the exact notice bundle for the standalone Python worker.

The compact ``install_only_stripped`` archive intentionally omits the
top-level ``python/licenses`` directory present in the matching full release
artifact. This generator retrieves that exact full artifact, verifies it,
and preserves every release-supplied component license in one reviewable text
file. It also retains the python-build-standalone MPL-2.0 license.

Regeneration requires the ``zstd`` command. Normal add-on builds use the
checked-in, checksum-pinned result and do not need network access or zstd.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tarfile
import tempfile
from urllib.request import Request, urlopen


RELEASE = "20260728"
FULL_ARCHIVE_NAME = (
    "cpython-3.13.14+20260728-aarch64-apple-darwin-pgo+lto-full.tar.zst"
)
FULL_ARCHIVE_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20260728/cpython-3.13.14%2B20260728-aarch64-apple-darwin-"
    "pgo%2Blto-full.tar.zst"
)
FULL_ARCHIVE_SIZE = 58_335_956
FULL_ARCHIVE_SHA256 = (
    "85c6b957a86e44ec423e57b7af1b5abdffe33107bc951297a7a88eb46361d20b"
)
PROJECT_LICENSE_URL = (
    "https://raw.githubusercontent.com/astral-sh/python-build-standalone/"
    "20260728/LICENSE"
)
PROJECT_LICENSE_SHA256 = (
    "1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5"
)
OUTPUT_RELATIVE_PATH = (
    "licenses/python-build-standalone-20260728-NOTICES.txt"
)
USER_AGENT = "Smart-Search-for-Anki-license-reproducer/1"

LICENSE_SHA256 = {
    "python/licenses/LICENSE.bdb.txt": (
        "3bdff06e69991c94664f2ef5c5f8096f60b7dbec071756ea6cc26b445e06ec5b"
    ),
    "python/licenses/LICENSE.bzip2.txt": (
        "1f38bbc7caacafd65169276d759c0d88c991b753b643ce35d0e45ea1971dd441"
    ),
    "python/licenses/LICENSE.cpython.txt": (
        "86e61415828a8b5b06ec8d024e6f086ce155a8b85fd0c419c0ba4dc004e74fdd"
    ),
    "python/licenses/LICENSE.expat.txt": (
        "122f2c27000472a201d337b9b31f7eb2b52d091b02857061a8880371612d9534"
    ),
    "python/licenses/LICENSE.libX11.txt": (
        "2daec087a88e7c9b8082557cdeebad5bbb8155a4137472f0b22e269cd99d0c1e"
    ),
    "python/licenses/LICENSE.libXau.txt": (
        "56abe29bb1d9806a9e04fa9f80fed2c0f18027594df3f098148d814aef6bddfa"
    ),
    "python/licenses/LICENSE.libedit.txt": (
        "29cea33c32bbc9785142386377915612a2fa786482c46843383384aded2e09b1"
    ),
    "python/licenses/LICENSE.libffi.txt": (
        "deaf3a42effb551a5b140fa9afefed183a27f1341c6d1bf430d106a5e6931fc0"
    ),
    "python/licenses/LICENSE.liblzma.txt": (
        "9a4062de0a2c388a98cf35a35d348b62fa97c838a71c3c28ee1a2d7d0a565b02"
    ),
    "python/licenses/LICENSE.libuuid.txt": (
        "122ee1f7e258f2c3c0e538a75c037684f420454bf3850ddc74ce750bbf5fe86b"
    ),
    "python/licenses/LICENSE.libxcb.txt": (
        "c5ffbfeaa501071ceeb97b7de2c0d703fdaa35de01c0fb6cbac1c28453a3e9fd"
    ),
    "python/licenses/LICENSE.mpdecimal.txt": (
        "669512af7219f58be03a398766d7c9da11a3b3df9d3f05cb74c5ceca25c8da3b"
    ),
    "python/licenses/LICENSE.ncurses.txt": (
        "87a4c4442337b8968ef956031c406b74f9cb7149b7ba87311bdaba534816201c"
    ),
    "python/licenses/LICENSE.openssl-1.1.txt": (
        "9c04cce50c4989d5601dd8b07f6ab922c40388b66ac736c9007cb1ed9d9dd560"
    ),
    "python/licenses/LICENSE.openssl-3.txt": (
        "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a"
    ),
    "python/licenses/LICENSE.sqlite.txt": (
        "38bef3d28b24f145ea293bd3b6eb4b20396982abc8303128fb493986ea5bc719"
    ),
    "python/licenses/LICENSE.tcl.txt": (
        "c0a69a2bfd757361ec7e6143973b103c90409316b49e9c88db26ad6388e79f16"
    ),
    "python/licenses/LICENSE.tix.txt": (
        "3ac5cdd0bef6c43ce34c6a7ced452081d9e5a0bf94082b9f9147d23ec9e214f5"
    ),
    "python/licenses/LICENSE.zlib.txt": (
        "818922b2620f12801a12bf78e399644a30990e66824abd8ca8ec24d451d6f92c"
    ),
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=120) as response:
        return response.read()


def _verified_download(url: str, sha256: str, size: int | None = None) -> bytes:
    payload = _download(url)
    if size is not None and len(payload) != size:
        raise SystemExit(
            f"Downloaded byte-size mismatch for {url}: expected {size}, "
            f"got {len(payload)}"
        )
    actual = _sha256(payload)
    if actual != sha256:
        raise SystemExit(
            f"Downloaded checksum mismatch for {url}: expected {sha256}, "
            f"got {actual}"
        )
    return payload


def _license_payloads(full_archive: bytes) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory() as directory:
        compressed = Path(directory) / "full.tar.zst"
        uncompressed = Path(directory) / "full.tar"
        compressed.write_bytes(full_archive)
        try:
            with uncompressed.open("wb") as output:
                subprocess.run(
                    ["zstd", "-q", "-d", "-c", str(compressed)],
                    stdout=output,
                    check=True,
                )
        except (OSError, subprocess.CalledProcessError) as error:
            raise SystemExit(
                "Could not decompress the pinned full runtime; install zstd "
                "and retry"
            ) from error

        result: dict[str, bytes] = {}
        try:
            with tarfile.open(uncompressed, mode="r:") as archive:
                actual_names = {
                    member.name
                    for member in archive.getmembers()
                    if member.name.startswith("python/licenses/")
                    and member.isfile()
                }
                if actual_names != set(LICENSE_SHA256):
                    raise SystemExit(
                        "The pinned full runtime's license inventory changed"
                    )
                for name, expected_digest in LICENSE_SHA256.items():
                    extracted = archive.extractfile(name)
                    if extracted is None:
                        raise SystemExit(f"Cannot read pinned license {name}")
                    payload = extracted.read()
                    actual_digest = _sha256(payload)
                    if actual_digest != expected_digest:
                        raise SystemExit(
                            f"Pinned license checksum mismatch for {name}: "
                            f"expected {expected_digest}, got {actual_digest}"
                        )
                    result[name] = payload
        except tarfile.TarError as error:
            raise SystemExit("The pinned full runtime is not a valid tar archive") from error
        return result


def _section(title: str, source: str, sha256: str, payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SystemExit(f"Pinned notice is not UTF-8: {source}") from error
    return (
        f"\n{'=' * 78}\n{title}\nSource member: {source}\n"
        f"SHA-256: {sha256}\n{'=' * 78}\n\n{text.rstrip()}\n"
    )


def build(root: Path) -> Path:
    full_archive = _verified_download(
        FULL_ARCHIVE_URL,
        FULL_ARCHIVE_SHA256,
        FULL_ARCHIVE_SIZE,
    )
    project_license = _verified_download(
        PROJECT_LICENSE_URL,
        PROJECT_LICENSE_SHA256,
    )
    component_licenses = _license_payloads(full_archive)

    parts = [
        "Generated by scripts/build_python_runtime_notices.py. DO NOT EDIT BY HAND.\n",
        "\nThis bundle covers the standalone Python runtime redistributed by Smart\n",
        "Search for Anki. The compact install-only archive omits the top-level\n",
        "license directory from its matching full release artifact, so the exact\n",
        "release-supplied texts are preserved here.\n",
        f"\nRelease: {RELEASE}\n",
        f"Full license-source artifact: {FULL_ARCHIVE_NAME}\n",
        f"Full artifact URL: {FULL_ARCHIVE_URL}\n",
        f"Full artifact bytes: {FULL_ARCHIVE_SIZE}\n",
        f"Full artifact SHA-256: {FULL_ARCHIVE_SHA256}\n",
        "Project source: https://github.com/astral-sh/python-build-standalone/",
        f"tree/{RELEASE}\n",
    ]
    parts.append(
        _section(
            "python-build-standalone project license (MPL-2.0)",
            PROJECT_LICENSE_URL,
            PROJECT_LICENSE_SHA256,
            project_license,
        )
    )
    for name, expected_digest in LICENSE_SHA256.items():
        parts.append(
            _section(
                name.removeprefix("python/licenses/"),
                name,
                expected_digest,
                component_licenses[name],
            )
        )

    destination = root / OUTPUT_RELATIVE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(parts), encoding="utf-8", newline="\n")
    return destination


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    destination = build(root)
    print(
        f"Wrote {destination} ({destination.stat().st_size:,} bytes, "
        f"SHA-256 {_sha256(destination.read_bytes())})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
