from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import tarfile
import unittest
import zipfile

from scripts import build_addon


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BuildAddonTests(unittest.TestCase):
    def _copy_public_source(self, destination: Path) -> None:
        paths = (
            build_addon.PUBLIC_TOP_LEVEL_FILES
            | build_addon.PUBLIC_RESOURCE_FILES
            | build_addon.PUBLIC_LICENSE_FILES
            | build_addon.PUBLIC_USER_FILES
            | set(build_addon.BUNDLED_WHEEL_SHA256)
            | set(build_addon.BUNDLED_RUNTIME_SHA256)
            | build_addon.REQUIRED
        )
        for relative_name in sorted(paths):
            source = PROJECT_ROOT / relative_name
            target = destination / relative_name
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative_name in (
                set(build_addon.BUNDLED_WHEEL_SHA256)
                | set(build_addon.BUNDLED_RUNTIME_SHA256)
            ):
                os.link(source, target)
            else:
                shutil.copy2(source, target)

    def test_only_readme_from_user_files_is_public(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            model = root / "user_files" / "model" / "local-model.onnx"
            runtime = root / "user_files" / "runtime" / "darwin-arm64-py313"
            model.parent.mkdir(parents=True)
            runtime.mkdir(parents=True)
            model.write_bytes(b"local model")
            (runtime / "native.so").write_bytes(b"expanded runtime")

            names = {
                path.relative_to(root).as_posix()
                for path in build_addon.included_files(root)
            }

            self.assertEqual(
                {name for name in names if name.startswith("user_files/")},
                {"user_files/README.txt"},
            )
            self.assertTrue(set(build_addon.BUNDLED_WHEEL_SHA256) <= names)
            self.assertTrue(set(build_addon.BUNDLED_RUNTIME_SHA256) <= names)

    def test_profile_owned_data_blocks_public_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            profile_index = (
                root
                / "user_files"
                / "profiles"
                / "Default"
                / "search.sqlite3"
            )
            profile_index.parent.mkdir(parents=True)
            profile_index.write_bytes(b"private card text")

            with self.assertRaisesRegex(
                SystemExit,
                "profile/private data under user_files",
            ):
                build_addon.included_files(root)

    def test_sensitive_artifact_outside_user_files_blocks_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            private_key = root / "credentials.pem"
            private_key.write_text("secret", encoding="utf-8")

            with self.assertRaisesRegex(
                SystemExit,
                "private or generated artifact",
            ):
                build_addon.included_files(root)

    def test_private_content_inside_public_text_blocks_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            readme = root / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8")
                + "\nLocal artifact: /Users/private-person/collection.anki2\n",
                encoding="utf-8",
            )

            files = build_addon.included_files(root)
            with self.assertRaisesRegex(SystemExit, "local macOS home path"):
                build_addon.validate_sources(root, files)

    def test_compliance_documents_are_in_public_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)

            names = {
                path.relative_to(root).as_posix()
                for path in build_addon.included_files(root)
            }

            self.assertTrue(
                {
                    "DATA_SOURCES.md",
                    "PRIVACY.md",
                    "SECURITY.md",
                    "SUPPORT.md",
                    "THIRD_PARTY_NOTICES.md",
                }
                <= names
            )

    def test_release_version_mismatch_blocks_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            ui_package = root / "ui" / "__init__.py"
            ui_package.write_text(
                ui_package.read_text(encoding="utf-8").replace(
                    '__version__ = "1.0.24"',
                    '__version__ = "9.9.9"',
                ),
                encoding="utf-8",
            )

            files = build_addon.included_files(root)
            with self.assertRaisesRegex(
                SystemExit,
                "Release version mismatch in ui/__init__.py",
            ):
                build_addon.validate_sources(root, files)

    def test_default_output_path_uses_manifest_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)

            self.assertEqual(
                build_addon.default_output_path(root),
                root / "dist" / "Smart_Search_Medical_1.0.24.ankiaddon",
            )

    def test_manifest_covers_the_reviewed_anki_release_matrix(self) -> None:
        manifest = json.loads((PROJECT_ROOT / "manifest.json").read_text())

        self.assertEqual(manifest["min_point_version"], 241100)
        self.assertEqual(manifest["max_point_version"], 260800)
        for point_version in (
            241100,
            250207,
            250705,
            250904,
            250905,
            260500,
            260800,
        ):
            self.assertLessEqual(manifest["min_point_version"], point_version)
            self.assertGreaterEqual(manifest["max_point_version"], point_version)

    def test_unapproved_controlled_payload_blocks_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            extra_resource = root / "resources" / "unreviewed.bin"
            extra_resource.write_bytes(b"payload")

            with self.assertRaisesRegex(
                SystemExit,
                "Unapproved public files under resources",
            ):
                build_addon.included_files(root)

    def test_corrupted_pinned_wheel_blocks_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            relative_name = next(iter(build_addon.BUNDLED_WHEEL_SHA256))
            wheel = root / relative_name
            wheel.unlink()
            wheel.write_bytes(b"not the reviewed wheel")

            files = build_addon.included_files(root)
            with self.assertRaisesRegex(
                SystemExit,
                "Checksum mismatch for deliberately bundled wheel",
            ):
                build_addon.validate_sources(root, files)

    def test_tampered_tokenizers_notice_bundle_blocks_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            notice = root / "licenses" / "tokenizers-0.23.1-NOTICES.txt"
            notice.write_text("incomplete notice bundle", encoding="utf-8")

            files = build_addon.included_files(root)
            with self.assertRaisesRegex(
                SystemExit,
                "Checksum mismatch for the reviewed tokenizers notice bundle",
            ):
                build_addon.validate_sources(root, files)

    def test_tampered_python_runtime_notices_block_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            notice = (
                root
                / "licenses"
                / "python-build-standalone-20260728-NOTICES.txt"
            )
            notice.write_text("incomplete notice bundle", encoding="utf-8")

            files = build_addon.included_files(root)
            with self.assertRaisesRegex(
                SystemExit,
                "Checksum mismatch for the reviewed tokenizers notice bundle",
            ):
                build_addon.validate_sources(root, files)

    def test_runtime_manifest_size_drift_blocks_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            manifest = root / "semantic" / "manifest.py"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "WORKER_PYTHON_SIZE = 25_121_759",
                    "WORKER_PYTHON_SIZE = 25_121_758",
                ),
                encoding="utf-8",
            )

            files = build_addon.included_files(root)
            with self.assertRaisesRegex(
                SystemExit,
                "Bundled runtime size does not match semantic manifest",
            ):
                build_addon.validate_sources(root, files)

    def test_truncated_runtime_blocks_build_before_tar_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_public_source(root)
            relative_name = next(iter(build_addon.BUNDLED_RUNTIME_SHA256))
            runtime = root / relative_name
            runtime.unlink()
            runtime.write_bytes(b"not the reviewed runtime")

            files = build_addon.included_files(root)
            with self.assertRaisesRegex(SystemExit, "Byte-size mismatch"):
                build_addon.validate_sources(root, files)

    def test_runtime_archive_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "unsafe.tar.gz"
            with tarfile.open(runtime, "w:gz") as archive:
                payload = b"escape"
                member = tarfile.TarInfo("python/../../outside")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))

            with self.assertRaisesRegex(SystemExit, "unsafe archive path"):
                build_addon.validate_runtime_archive(runtime, "unsafe.tar.gz")

    def test_runtime_archive_rejects_escaping_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "unsafe-link.tar.gz"
            with tarfile.open(runtime, "w:gz") as archive:
                member = tarfile.TarInfo("python/bin/python3")
                member.type = tarfile.SYMTYPE
                member.linkname = "../../../outside"
                archive.addfile(member)

            with self.assertRaisesRegex(SystemExit, "escapes its archive root"):
                build_addon.validate_runtime_archive(runtime, "unsafe-link.tar.gz")

    def test_runtime_archive_rejects_dangling_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "dangling-link.tar.gz"
            with tarfile.open(runtime, "w:gz") as archive:
                member = tarfile.TarInfo("python/bin/python3")
                member.type = tarfile.SYMTYPE
                member.linkname = "python3.13"
                archive.addfile(member)

            with self.assertRaisesRegex(SystemExit, "dangling symlink"):
                build_addon.validate_runtime_archive(runtime, "dangling-link.tar.gz")

    def test_runtime_archive_rejects_special_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "unsafe-fifo.tar.gz"
            with tarfile.open(runtime, "w:gz") as archive:
                member = tarfile.TarInfo("python/run.pipe")
                member.type = tarfile.FIFOTYPE
                archive.addfile(member)

            with self.assertRaisesRegex(SystemExit, "special member"):
                build_addon.validate_runtime_archive(runtime, "unsafe-fifo.tar.gz")

    def test_build_is_slim_and_contains_only_reviewed_user_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            self._copy_public_source(root)
            local_model = root / "user_files" / "model" / "model.onnx"
            local_runtime = root / "user_files" / "runtime" / "runtime.so"
            local_model.parent.mkdir(parents=True)
            local_runtime.parent.mkdir(parents=True)
            local_model.write_bytes(b"must not ship")
            local_runtime.write_bytes(b"must not ship")
            destination = Path(directory) / "Smart_Search_Medical.ankiaddon"

            result = build_addon.build(root, destination)

            self.assertLess(result["bytes"], build_addon.MAX_PACKAGE_BYTES)
            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
            self.assertEqual(
                {name for name in names if name.startswith("user_files/")},
                {"user_files/README.txt"},
            )
            self.assertNotIn("user_files/model/model.onnx", names)
            self.assertNotIn("user_files/runtime/runtime.so", names)


if __name__ == "__main__":
    unittest.main()
