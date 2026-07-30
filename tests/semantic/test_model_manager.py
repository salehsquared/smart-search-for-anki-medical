from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from semantic.manifest import RuntimeWheel
from semantic.model_manager import (
    MACOS_ARM64_RUNTIME_TAG,
    ModelManager,
    sha256_file,
)


class ModelManagerTests(unittest.TestCase):
    def test_semantic_runtime_requires_macos_14_or_later(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ModelManager(root / "data", root / "bundle")
            with (
                patch(
                    "semantic.model_manager.runtime_tag",
                    return_value=MACOS_ARM64_RUNTIME_TAG,
                ),
                patch(
                    "semantic.model_manager.platform.mac_ver",
                    return_value=("13.7.6", ("", "", ""), ""),
                ),
            ):
                self.assertFalse(manager.runtime_supported())

            with (
                patch(
                    "semantic.model_manager.runtime_tag",
                    return_value=MACOS_ARM64_RUNTIME_TAG,
                ),
                patch(
                    "semantic.model_manager.platform.mac_ver",
                    return_value=("14.0", ("", "", ""), ""),
                ),
            ):
                self.assertTrue(manager.runtime_supported())

    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload"
            path.write_bytes(b"smart-search")
            self.assertEqual(
                sha256_file(path),
                hashlib.sha256(b"smart-search").hexdigest(),
            )

    def test_runtime_wheels_are_verified_and_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            data = root / "data"
            tag = "test-runtime"
            wheel_dir = bundle / "vendor_wheels" / tag
            wheel_dir.mkdir(parents=True)
            wheel = wheel_dir / "fake.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("fake_runtime.py", "READY = True\n")
            manifest_wheel = RuntimeWheel(
                filename=wheel.name,
                sha256=sha256_file(wheel),
            )

            manager = ModelManager(data, bundle)
            manager.runtime_dir = data / "runtime" / tag
            with (
                patch("semantic.model_manager.runtime_tag", return_value=tag),
                patch(
                    "semantic.model_manager.RUNTIME_WHEELS",
                    {tag: (manifest_wheel,)},
                ),
            ):
                manager.install_runtime()
                self.assertTrue(manager.runtime_ready())
                self.assertTrue((manager.runtime_dir / "fake_runtime.py").is_file())
                marker = json.loads(
                    (manager.runtime_dir / ".smart-search-runtime.json").read_text()
                )
                self.assertEqual(marker["runtime_tag"], tag)

    def test_force_runtime_install_repairs_existing_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            data = root / "data"
            tag = "test-runtime"
            wheel_dir = bundle / "vendor_wheels" / tag
            wheel_dir.mkdir(parents=True)
            wheel = wheel_dir / "fake.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("fake_runtime.py", "READY = True\n")
            manifest_wheel = RuntimeWheel(
                filename=wheel.name,
                sha256=sha256_file(wheel),
            )

            manager = ModelManager(data, bundle)
            manager.runtime_dir = data / "runtime" / tag
            with (
                patch("semantic.model_manager.runtime_tag", return_value=tag),
                patch(
                    "semantic.model_manager.RUNTIME_WHEELS",
                    {tag: (manifest_wheel,)},
                ),
            ):
                manager.install_runtime()
                runtime_file = manager.runtime_dir / "fake_runtime.py"
                runtime_file.write_text("BROKEN = True\n", encoding="utf-8")

                manager.install_runtime(force=True)

                self.assertEqual(
                    runtime_file.read_text(encoding="utf-8"),
                    "READY = True\n",
                )


if __name__ == "__main__":
    unittest.main()
