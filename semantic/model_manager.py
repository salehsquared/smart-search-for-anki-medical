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
import tarfile
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
    HOST_VECTOR_WHEELS,
    WORKER_PYTHON_FILENAME,
    WORKER_PYTHON_SHA256,
    WORKER_RUNTIME_TAG,
    WORKER_RUNTIME_WHEELS,
    Artifact,
    runtime_tag,
)


ProgressCallback = Callable[[str, int, int], None]
CancelCheck = Callable[[], None]
MACOS_ARM64_RUNTIME_TAG = WORKER_RUNTIME_TAG
MACOS_ARM64_PY39_RUNTIME_TAG = "darwin-arm64-py39"
MINIMUM_SEMANTIC_MACOS_MAJOR = 14
DOWNLOAD_USER_AGENT = "Smart-Search-for-Anki/1.0.23"


class ModelInstallError(RuntimeError):
    pass


class RuntimeInstallError(RuntimeError):
    pass


class UnsupportedRuntimeError(RuntimeInstallError):
    pass


def sha256_file(
    path: Path,
    chunk_size: int = 1024 * 1024,
    cancel_check: CancelCheck | None = None,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            if cancel_check is not None:
                cancel_check()
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_platform_supported(tag: str) -> bool:
    """Apply OS requirements that cannot be expressed by the runtime tag."""

    if tag != MACOS_ARM64_RUNTIME_TAG:
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
    def worker_python_path(self) -> Path:
        return self.runtime_dir / "python" / "bin" / "python3"

    @property
    def worker_site_packages(self) -> Path:
        return self.runtime_dir / "site-packages"

    @property
    def host_vector_tag(self) -> str:
        system = platform.system().casefold()
        machine = platform.machine().casefold()
        if system == "darwin" and machine in {"arm64", "aarch64"}:
            machine = "arm64"
        return (
            f"{system}-{machine}-py"
            f"{sys.version_info.major}{sys.version_info.minor}"
        )

    @property
    def host_vector_runtime_dir(self) -> Path:
        return self.data_root / "runtime" / ("vector-" + self.host_vector_tag)

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
        return (
            tag == WORKER_RUNTIME_TAG
            and self.host_vector_tag in HOST_VECTOR_WHEELS
            and _runtime_platform_supported(tag)
        )

    def runtime_ready(self) -> bool:
        if not self.runtime_supported():
            return False
        return self.worker_runtime_ready() and self._host_vector_runtime_ready()

    def worker_runtime_ready(self) -> bool:
        """Validate only the isolated worker's pinned runtime payload."""

        if runtime_tag() != WORKER_RUNTIME_TAG or not _runtime_platform_supported(
            runtime_tag()
        ):
            return False
        marker = self.runtime_dir / ".smart-search-runtime.json"
        if not marker.is_file():
            return False
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if (
            payload.get("runtime_tag") != runtime_tag()
            or payload.get("python") != WORKER_PYTHON_FILENAME
            or payload.get("python_sha256") != WORKER_PYTHON_SHA256
            or payload.get("wheels")
            != [wheel.filename for wheel in WORKER_RUNTIME_WHEELS]
            or not self.worker_python_path.is_file()
            or not os.access(self.worker_python_path, os.X_OK)
            or not self.worker_site_packages.is_dir()
        ):
            return False
        required_packages = (
            "numpy/__init__.py",
            "onnxruntime/__init__.py",
            "tokenizers/__init__.py",
            "flatbuffers/__init__.py",
        )
        return all(
            (self.worker_site_packages / relative).is_file()
            for relative in required_packages
        )

    def _host_vector_runtime_ready(self) -> bool:
        marker = self.host_vector_runtime_dir / ".smart-search-vector-runtime.json"
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            payload.get("runtime_tag") == self.host_vector_tag
            and payload.get("wheels")
            == [
                wheel.filename
                for wheel in HOST_VECTOR_WHEELS.get(self.host_vector_tag, ())
            ]
            and (self.host_vector_runtime_dir / "numpy" / "__init__.py").is_file()
        )

    def install_model(
        self,
        progress: ProgressCallback | None = None,
        *,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        for artifact in MODEL_ARTIFACTS:
            if cancel_check is not None:
                cancel_check()
            destination = self.model_dir / artifact.local_path
            if destination.is_file() and sha256_file(
                destination,
                cancel_check=cancel_check,
            ) == artifact.sha256:
                if progress:
                    progress(artifact.local_path, artifact.size, artifact.size)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._download(
                artifact,
                destination,
                progress,
                cancel_check=cancel_check,
            )

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

    def install_runtime(
        self,
        *,
        force: bool = False,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        if cancel_check is not None:
            cancel_check()
        tag = runtime_tag()
        if (
            tag != WORKER_RUNTIME_TAG
            or self.host_vector_tag not in HOST_VECTOR_WHEELS
            or not _runtime_platform_supported(tag)
        ):
            raise UnsupportedRuntimeError(
                "Semantic Search currently requires macOS 14 or later on "
                "Apple silicon. Smart and Exact search remain available."
            )
        if self.runtime_ready() and not force:
            return

        python_archive = (
            self.bundle_root
            / "vendor_runtime"
            / WORKER_RUNTIME_TAG
            / WORKER_PYTHON_FILENAME
        )
        wheel_dir = self.bundle_root / "vendor_wheels" / "darwin-arm64-py313"
        missing = [
            wheel.filename
            for wheel in WORKER_RUNTIME_WHEELS
            if not (wheel_dir / wheel.filename).is_file()
        ]
        if not python_archive.is_file():
            missing.insert(0, WORKER_PYTHON_FILENAME)
        host_vector_wheels = HOST_VECTOR_WHEELS[self.host_vector_tag]
        host_wheel_dir = self.bundle_root / "vendor_wheels" / self.host_vector_tag
        missing.extend(
            wheel.filename
            for wheel in host_vector_wheels
            if not (host_wheel_dir / wheel.filename).is_file()
        )
        if missing:
            raise RuntimeInstallError(
                "The semantic runtime bundle is incomplete: " + ", ".join(missing)
            )
        if sha256_file(
            python_archive,
            cancel_check=cancel_check,
        ) != WORKER_PYTHON_SHA256:
            raise RuntimeInstallError(
                f"Checksum failed for {WORKER_PYTHON_FILENAME}"
            )

        staging = self.runtime_dir.with_name(self.runtime_dir.name + ".staging")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            self._extract_python_archive(
                python_archive,
                staging,
                cancel_check=cancel_check,
            )
            site_packages = staging / "site-packages"
            site_packages.mkdir(parents=True, exist_ok=True)
            for wheel in WORKER_RUNTIME_WHEELS:
                if cancel_check is not None:
                    cancel_check()
                source = wheel_dir / wheel.filename
                if sha256_file(source, cancel_check=cancel_check) != wheel.sha256:
                    raise RuntimeInstallError(f"Checksum failed for {wheel.filename}")
                self._extract_wheel(
                    source,
                    site_packages,
                    cancel_check=cancel_check,
                )
            self._atomic_write_json(
                staging / ".smart-search-runtime.json",
                {
                    "runtime_tag": tag,
                    "installed_at": int(time.time()),
                    "python": WORKER_PYTHON_FILENAME,
                    "python_sha256": WORKER_PYTHON_SHA256,
                    "wheels": [
                        wheel.filename for wheel in WORKER_RUNTIME_WHEELS
                    ],
                },
            )
            self._replace_directory(staging, self.runtime_dir)
            self._install_host_vector_runtime(
                force=force,
                cancel_check=cancel_check,
            )
            self._remove_legacy_runtimes()
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def activate_runtime(self) -> None:
        if not self.worker_runtime_ready():
            raise RuntimeInstallError("The local semantic runtime is not installed.")
        runtime_text = str(self.worker_site_packages)
        if runtime_text not in sys.path:
            sys.path.insert(0, runtime_text)

    def activate_vector_runtime(self) -> None:
        if not self.runtime_ready():
            raise RuntimeInstallError("The local semantic runtime is not installed.")
        runtime_text = str(self.host_vector_runtime_dir)
        if runtime_text not in sys.path:
            sys.path.insert(0, runtime_text)

    def install_all(
        self,
        progress: ProgressCallback | None = None,
        *,
        repair_runtime: bool = False,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        self.install_runtime(
            force=repair_runtime,
            cancel_check=cancel_check,
        )
        self.install_model(progress, cancel_check=cancel_check)

    def describe(self) -> dict[str, Any]:
        return {
            "model_name": MODEL_NAME,
            "model_ready": self.model_ready(),
            "runtime_tag": runtime_tag(),
            "runtime_supported": self.runtime_supported(),
            "runtime_ready": self.runtime_ready(),
            "download_bytes": sum(item.size for item in MODEL_ARTIFACTS),
        }

    def _install_host_vector_runtime(
        self,
        *,
        force: bool,
        cancel_check: CancelCheck | None,
    ) -> None:
        if self._host_vector_runtime_ready() and not force:
            return
        wheels = HOST_VECTOR_WHEELS[self.host_vector_tag]
        wheel_dir = self.bundle_root / "vendor_wheels" / self.host_vector_tag
        destination = self.host_vector_runtime_dir
        staging = destination.with_name(destination.name + ".staging")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            for wheel in wheels:
                if cancel_check is not None:
                    cancel_check()
                source = wheel_dir / wheel.filename
                if sha256_file(source, cancel_check=cancel_check) != wheel.sha256:
                    raise RuntimeInstallError(f"Checksum failed for {wheel.filename}")
                self._extract_wheel(
                    source,
                    staging,
                    cancel_check=cancel_check,
                )
            self._atomic_write_json(
                staging / ".smart-search-vector-runtime.json",
                {
                    "runtime_tag": self.host_vector_tag,
                    "installed_at": int(time.time()),
                    "wheels": [wheel.filename for wheel in wheels],
                },
            )
            self._replace_directory(staging, destination)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def _remove_legacy_runtimes(self) -> None:
        """Remove only obsolete derived native runtimes after a good upgrade."""

        runtime_root = self.data_root / "runtime"
        for name in ("darwin-arm64-py39", "darwin-arm64-py313"):
            legacy = runtime_root / name
            if legacy != self.runtime_dir and legacy.exists():
                shutil.rmtree(legacy)

    @staticmethod
    def _replace_directory(staging: Path, destination: Path) -> None:
        """Atomically publish a staged directory while preserving rollback."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        previous = destination.with_name(destination.name + ".previous")
        if previous.exists():
            shutil.rmtree(previous)
        if destination.exists():
            os.replace(destination, previous)
        try:
            os.replace(staging, destination)
        except Exception:
            if previous.exists() and not destination.exists():
                os.replace(previous, destination)
            raise
        else:
            if previous.exists():
                shutil.rmtree(previous)

    @staticmethod
    def _extract_python_archive(
        source: Path,
        destination: Path,
        *,
        cancel_check: CancelCheck | None,
    ) -> None:
        destination_root = destination.resolve()
        with tarfile.open(source, mode="r:gz") as archive:
            for member in archive.getmembers():
                if cancel_check is not None:
                    cancel_check()
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise RuntimeInstallError(
                        f"Unsafe path in {source.name}: {member.name}"
                    )
                target = (destination / member_path).resolve()
                try:
                    target.relative_to(destination_root)
                except ValueError as error:
                    raise RuntimeInstallError(
                        f"Unsafe path in {source.name}: {member.name}"
                    ) from error
                if member.issym() or member.islnk():
                    link = Path(member.linkname)
                    if link.is_absolute() or ".." in link.parts:
                        raise RuntimeInstallError(
                            f"Unsafe link in {source.name}: {member.name}"
                        )
                    link_base = (
                        destination
                        if member.islnk()
                        else destination / member_path.parent
                    )
                    try:
                        (link_base / link).resolve().relative_to(destination_root)
                    except ValueError as error:
                        raise RuntimeInstallError(
                            f"Unsafe link in {source.name}: {member.name}"
                        ) from error
                elif not (member.isfile() or member.isdir()):
                    raise RuntimeInstallError(
                        f"Unsafe special file in {source.name}: {member.name}"
                    )
            if sys.version_info >= (3, 12):
                archive.extractall(destination, filter="fully_trusted")
            else:
                archive.extractall(destination)

    @staticmethod
    def _extract_wheel(
        source: Path,
        destination: Path,
        *,
        cancel_check: CancelCheck | None,
    ) -> None:
        destination_root = destination.resolve()
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                if cancel_check is not None:
                    cancel_check()
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise RuntimeInstallError(
                        f"Unsafe path in {source.name}: {member.filename}"
                    )
                target = (destination / member_path).resolve()
                try:
                    target.relative_to(destination_root)
                except ValueError as error:
                    raise RuntimeInstallError(
                        f"Unsafe path in {source.name}: {member.filename}"
                    ) from error
                archive.extract(member, destination)

    def _download(
        self,
        artifact: Artifact,
        destination: Path,
        progress: ProgressCallback | None,
        *,
        cancel_check: CancelCheck | None = None,
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
                    if cancel_check is not None:
                        cancel_check()
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
        actual_hash = sha256_file(part, cancel_check=cancel_check)
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
