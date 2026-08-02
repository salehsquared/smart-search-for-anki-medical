"""Verified installation of the optional local embedding model and runtime."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time
from typing import Any
import urllib.error
import urllib.request
import zipfile

from .manifest import (
    MODEL_ARTIFACTS,
    MODEL_LICENSE,
    MODEL_NAME,
    MODEL_REPOSITORY,
    MODEL_REVISION,
    RUNTIME_WHEELS,
    Artifact,
    runtime_tag,
)


ProgressCallback = Callable[[str, int, int], None]
MACOS_ARM64_PY39_RUNTIME_TAG = "darwin-arm64-py39"
MACOS_ARM64_RUNTIME_TAG = "darwin-arm64-py313"
MACOS_ARM64_RUNTIME_TAGS = frozenset(
    (MACOS_ARM64_PY39_RUNTIME_TAG, MACOS_ARM64_RUNTIME_TAG)
)
MINIMUM_SEMANTIC_MACOS_MAJOR = 14
DOWNLOAD_USER_AGENT = "Smart-Search-for-Anki/1.0.19"


class ModelInstallError(RuntimeError):
    pass


class RuntimeInstallError(RuntimeError):
    pass


class UnsupportedRuntimeError(RuntimeInstallError):
    pass


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_platform_supported(tag: str) -> bool:
    """Apply OS requirements that cannot be expressed by the runtime tag."""

    if tag not in MACOS_ARM64_RUNTIME_TAGS:
        return True
    version = platform.mac_ver()[0]
    try:
        major = int(version.split(".", 1)[0])
    except (TypeError, ValueError):
        return False
    return major >= MINIMUM_SEMANTIC_MACOS_MAJOR


class ModelManager:
    """Manage semantic assets without modifying the Anki collection.

    ``data_root`` should point inside the add-on's ``user_files`` directory.
    ``bundle_root`` is the read-only installed add-on directory containing
    ``vendor_wheels/<runtime-tag>``.
    """

    def __init__(self, data_root: Path, bundle_root: Path) -> None:
        self.data_root = Path(data_root)
        self.bundle_root = Path(bundle_root)
        self.model_dir = self.data_root / "model" / MODEL_NAME
        self.runtime_dir = self.data_root / "runtime" / runtime_tag()

    @property
    def model_path(self) -> Path:
        return self.model_dir / "model_int8.onnx"

    @property
    def tokenizer_path(self) -> Path:
        return self.model_dir / "tokenizer.json"

    def model_ready(self, *, verify: bool = False) -> bool:
        if not all((self.model_dir / item.local_path).is_file() for item in MODEL_ARTIFACTS):
            return False
        if not verify:
            return True
        return all(
            sha256_file(self.model_dir / item.local_path) == item.sha256
            for item in MODEL_ARTIFACTS
        )

    def runtime_supported(self) -> bool:
        tag = runtime_tag()
        return tag in RUNTIME_WHEELS and _runtime_platform_supported(tag)

    def runtime_ready(self) -> bool:
        if not self.runtime_supported():
            return False
        marker = self.runtime_dir / ".smart-search-runtime.json"
        if not marker.is_file():
            return False
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return payload.get("runtime_tag") == runtime_tag()

    def install_model(self, progress: ProgressCallback | None = None) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        for artifact in MODEL_ARTIFACTS:
            destination = self.model_dir / artifact.local_path
            if destination.is_file() and sha256_file(destination) == artifact.sha256:
                if progress:
                    progress(artifact.local_path, artifact.size, artifact.size)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._download(artifact, destination, progress)

        manifest = {
            "name": MODEL_NAME,
            "repository": MODEL_REPOSITORY,
            "revision": MODEL_REVISION,
            "license": MODEL_LICENSE,
            "installed_at": int(time.time()),
            "artifacts": [
                {
                    "path": artifact.local_path,
                    "sha256": artifact.sha256,
                    "size": artifact.size,
                }
                for artifact in MODEL_ARTIFACTS
            ],
        }
        self._atomic_write_json(self.model_dir / "install.json", manifest)

    def install_runtime(self, *, force: bool = False) -> None:
        tag = runtime_tag()
        wheels = RUNTIME_WHEELS.get(tag)
        if wheels is None or not _runtime_platform_supported(tag):
            raise UnsupportedRuntimeError(
                "Semantic Search currently requires macOS 14 or later on "
                "Apple silicon. Smart and Exact search remain available."
            )
        if self.runtime_ready() and not force:
            return

        wheel_dir = self.bundle_root / "vendor_wheels" / tag
        missing = [wheel.filename for wheel in wheels if not (wheel_dir / wheel.filename).is_file()]
        if missing:
            raise RuntimeInstallError(
                "The semantic runtime bundle is incomplete: " + ", ".join(missing)
            )

        staging = self.runtime_dir.with_name(self.runtime_dir.name + ".staging")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            for wheel in wheels:
                source = wheel_dir / wheel.filename
                if sha256_file(source) != wheel.sha256:
                    raise RuntimeInstallError(f"Checksum failed for {wheel.filename}")
                with zipfile.ZipFile(source) as archive:
                    archive.extractall(staging)
            self._atomic_write_json(
                staging / ".smart-search-runtime.json",
                {
                    "runtime_tag": tag,
                    "installed_at": int(time.time()),
                    "wheels": [wheel.filename for wheel in wheels],
                },
            )
            self.runtime_dir.parent.mkdir(parents=True, exist_ok=True)
            if self.runtime_dir.exists():
                shutil.rmtree(self.runtime_dir)
            os.replace(staging, self.runtime_dir)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def activate_runtime(self) -> None:
        if not self.runtime_ready():
            raise RuntimeInstallError("The local semantic runtime is not installed.")
        runtime_text = str(self.runtime_dir)
        if runtime_text not in sys.path:
            sys.path.insert(0, runtime_text)

    def install_all(
        self,
        progress: ProgressCallback | None = None,
        *,
        repair_runtime: bool = False,
    ) -> None:
        self.install_runtime(force=repair_runtime)
        self.install_model(progress)

    def describe(self) -> dict[str, Any]:
        return {
            "model_name": MODEL_NAME,
            "model_ready": self.model_ready(),
            "runtime_tag": runtime_tag(),
            "runtime_supported": self.runtime_supported(),
            "runtime_ready": self.runtime_ready(),
            "download_bytes": sum(item.size for item in MODEL_ARTIFACTS),
        }

    def _download(
        self,
        artifact: Artifact,
        destination: Path,
        progress: ProgressCallback | None,
    ) -> None:
        part = destination.with_suffix(destination.suffix + ".part")
        resumed_at = part.stat().st_size if part.exists() else 0
        headers = {
            "User-Agent": DOWNLOAD_USER_AGENT,
            "Accept": "application/octet-stream",
        }
        if resumed_at:
            headers["Range"] = f"bytes={resumed_at}-"
        request = urllib.request.Request(artifact.url, headers=headers)

        try:
            response = urllib.request.urlopen(request, timeout=60)
            status = getattr(response, "status", None)
            append = resumed_at > 0 and status == 206
            if not append:
                resumed_at = 0
            mode = "ab" if append else "wb"
            completed = resumed_at
            with response, part.open(mode) as handle:
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
                    completed += len(chunk)
                    if progress:
                        progress(artifact.local_path, completed, artifact.size)
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, urllib.error.URLError) as error:
            raise ModelInstallError(
                f"Could not download {artifact.local_path}: {error}"
            ) from error

        actual_size = part.stat().st_size
        if actual_size != artifact.size:
            raise ModelInstallError(
                f"Wrong size for {artifact.local_path}: "
                f"expected {artifact.size}, got {actual_size}"
            )
        actual_hash = sha256_file(part)
        if actual_hash != artifact.sha256:
            part.unlink(missing_ok=True)
            raise ModelInstallError(
                f"Checksum failed for {artifact.local_path}; the partial download was removed."
            )
        os.replace(part, destination)

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
