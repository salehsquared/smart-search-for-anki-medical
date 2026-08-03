from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import PropertyMock, patch
import zipfile

from semantic.manifest import RuntimeWheel, WORKER_RUNTIME_TAG
from semantic.model_manager import (
    MACOS_ARM64_RUNTIME_TAG,
    ModelManager,
    RuntimeInstallError,
    sha256_file,
)


class ModelManagerTests(unittest.TestCase):
    def test_semantic_runtime_requires_macos_14_or_later(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ModelManager(root / "data", root / "bundle")
            with patch(
                "semantic.model_manager.platform.mac_ver",
                return_value=("13.7.6", ("", "", ""), ""),
            ):
                self.assertFalse(manager.runtime_supported())

            with patch(
                "semantic.model_manager.platform.mac_ver",
                return_value=("14.0", ("", "", ""), ""),
            ), patch.object(
                ModelManager,
                "host_vector_tag",
                new_callable=PropertyMock,
                return_value="darwin-arm64-py313",
            ):
                self.assertTrue(manager.runtime_supported())

    def test_runtime_tag_is_independent_of_ankis_python_version(self) -> None:
        self.assertEqual(MACOS_ARM64_RUNTIME_TAG, WORKER_RUNTIME_TAG)
        self.assertNotIn("py39", WORKER_RUNTIME_TAG)

    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload"
            path.write_bytes(b"smart-search")
            self.assertEqual(
                sha256_file(path),
                hashlib.sha256(b"smart-search").hexdigest(),
            )

    def test_worker_runtime_is_verified_and_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, patches = self._fake_runtime(root)
            with patches:
                manager.install_runtime()

                self.assertTrue(manager.runtime_ready())
                self.assertTrue(manager.worker_python_path.is_file())
                self.assertTrue(
                    (manager.worker_site_packages / "numpy" / "__init__.py").is_file()
                )
                self.assertNotEqual(
                    manager.host_vector_runtime_dir,
                    manager.worker_site_packages,
                )
                self.assertFalse(
                    (manager.host_vector_runtime_dir / "onnxruntime").exists()
                )
                self.assertFalse(
                    (manager.host_vector_runtime_dir / "tokenizers").exists()
                )
                marker = json.loads(
                    (manager.runtime_dir / ".smart-search-runtime.json").read_text()
                )
                self.assertEqual(marker["runtime_tag"], WORKER_RUNTIME_TAG)

    def test_force_runtime_install_repairs_existing_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, patches = self._fake_runtime(root)
            with patches:
                manager.install_runtime()
                runtime_file = manager.worker_site_packages / "numpy" / "__init__.py"
                runtime_file.write_text("BROKEN = True\n", encoding="utf-8")

                manager.install_runtime(force=True)

                self.assertEqual(
                    runtime_file.read_text(encoding="utf-8"),
                    "READY = True\n",
                )

    def test_python39_host_gets_only_a_small_vector_numpy_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, patches = self._fake_runtime(
                root,
                host_tag="darwin-arm64-py39",
            )
            with patches:
                manager.install_runtime()

                vector_root = manager.host_vector_runtime_dir
                self.assertTrue((vector_root / "numpy" / "__init__.py").is_file())
                marker = json.loads(
                    (vector_root / ".smart-search-vector-runtime.json").read_text()
                )
                self.assertEqual(marker["runtime_tag"], "darwin-arm64-py39")
                self.assertFalse((vector_root / "onnxruntime").exists())
                self.assertFalse((vector_root / "tokenizers").exists())

    def test_python_archive_checksum_failure_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, patches = self._fake_runtime(root)
            archive = next((root / "bundle" / "vendor_runtime").rglob("*.tar.gz"))
            archive.write_bytes(b"tampered")
            with patches, self.assertRaisesRegex(RuntimeInstallError, "Checksum"):
                manager.install_runtime()

    def test_stale_runtime_marker_is_reinstalled_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, patches = self._fake_runtime(root)
            with patches:
                manager.install_runtime()
                marker_path = manager.runtime_dir / ".smart-search-runtime.json"
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                marker["python_sha256"] = "obsolete"
                marker_path.write_text(json.dumps(marker), encoding="utf-8")
                self.assertFalse(manager.runtime_ready())

                manager.install_runtime()

                self.assertTrue(manager.runtime_ready())

    def test_failed_repair_preserves_the_previous_good_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, patches = self._fake_runtime(root)
            with patches:
                manager.install_runtime()
                archive = next(
                    (root / "bundle" / "vendor_runtime").rglob("*.tar.gz")
                )
                archive.write_bytes(b"tampered")

                with self.assertRaisesRegex(RuntimeInstallError, "Checksum"):
                    manager.install_runtime(force=True)

                self.assertTrue(manager.worker_python_path.is_file())
                self.assertTrue(manager.runtime_ready())

    def test_successful_worker_upgrade_removes_only_legacy_derived_runtimes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager, patches = self._fake_runtime(root)
            legacy39 = manager.data_root / "runtime" / "darwin-arm64-py39"
            legacy313 = manager.data_root / "runtime" / "darwin-arm64-py313"
            unrelated = manager.data_root / "runtime" / "keep-me"
            for path in (legacy39, legacy313, unrelated):
                path.mkdir(parents=True, exist_ok=True)
                (path / "marker").write_text("derived", encoding="utf-8")

            with patches:
                manager.install_runtime()

            self.assertFalse(legacy39.exists())
            self.assertFalse(legacy313.exists())
            self.assertTrue(unrelated.exists())

    def _fake_runtime(
        self,
        root: Path,
        *,
        host_tag: str = "darwin-arm64-py313",
    ):
        bundle = root / "bundle"
        data = root / "data"
        archive_name = "python-test.tar.gz"
        archive_path = (
            bundle / "vendor_runtime" / WORKER_RUNTIME_TAG / archive_name
        )
        archive_path.parent.mkdir(parents=True)
        with tarfile.open(archive_path, "w:gz") as archive:
            payload = b"#!/bin/sh\n"
            info = tarfile.TarInfo("python/bin/python3")
            info.mode = 0o755
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

        worker_wheel_dir = bundle / "vendor_wheels" / "darwin-arm64-py313"
        worker_wheel_dir.mkdir(parents=True)
        worker_wheel = worker_wheel_dir / "worker-test.whl"
        with zipfile.ZipFile(worker_wheel, "w") as archive:
            archive.writestr("numpy/__init__.py", "READY = True\n")
            archive.writestr("onnxruntime/__init__.py", "READY = True\n")
            archive.writestr("tokenizers/__init__.py", "READY = True\n")
            archive.writestr("flatbuffers/__init__.py", "READY = True\n")
        manifest_wheel = RuntimeWheel(
            filename=worker_wheel.name,
            sha256=sha256_file(worker_wheel),
        )

        host_wheel_dir = bundle / "vendor_wheels" / host_tag
        host_wheel_dir.mkdir(parents=True, exist_ok=True)
        host_wheel = host_wheel_dir / "numpy-host-test.whl"
        with zipfile.ZipFile(host_wheel, "w") as archive:
            archive.writestr("numpy/__init__.py", "READY = True\n")
        host_wheels = (
            RuntimeWheel(host_wheel.name, sha256_file(host_wheel)),
        )

        manager = ModelManager(data, bundle)
        patches = _PatchGroup(
            patch.object(
                ModelManager,
                "host_vector_tag",
                new_callable=PropertyMock,
                return_value=host_tag,
            ),
            patch(
                "semantic.model_manager.platform.mac_ver",
                return_value=("14.0", ("", "", ""), ""),
            ),
            patch("semantic.model_manager.WORKER_PYTHON_FILENAME", archive_name),
            patch(
                "semantic.model_manager.WORKER_PYTHON_SHA256",
                sha256_file(archive_path),
            ),
            patch(
                "semantic.model_manager.WORKER_RUNTIME_WHEELS",
                (manifest_wheel,),
            ),
            patch(
                "semantic.model_manager.HOST_VECTOR_WHEELS",
                {host_tag: host_wheels},
            ),
        )
        return manager, patches


class _PatchGroup:
    def __init__(self, *patchers) -> None:
        self.patchers = patchers

    def __enter__(self):
        for patcher in self.patchers:
            patcher.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        for patcher in reversed(self.patchers):
            patcher.stop()
        return False


if __name__ == "__main__":
    unittest.main()
