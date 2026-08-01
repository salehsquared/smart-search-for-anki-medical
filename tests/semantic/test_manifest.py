from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from semantic.manifest import (
    DARWIN_ARM64_PY39,
    MODEL_ARTIFACTS,
    MODEL_BASE_URL,
    MODEL_NAME,
    MODEL_REPOSITORY,
    MODEL_REVISION,
    RUNTIME_WHEELS,
)
from semantic.model_manager import ModelManager


REPOSITORY = "medbrevia/medembed-small-v0.1-onnx-int8"
REVISION = "6cbe4664f1e0067da935f5abc24e4f8b5406b13f"
MODEL_DIRECTORY = "MedEmbed-small-v0.1-int8-medbrevia-6cbe4664"
PROVENANCE_URL = (
    "https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8/"
    "blob/6cbe4664f1e0067da935f5abc24e4f8b5406b13f/PROVENANCE.json"
)
LEGACY_CONVERTER = "Rome" + "lianism"
LEGACY_REVISION_PREFIX = "eef8" + "8ec4"
EXPECTED_ARTIFACTS = (
    (
        "onnx/model_int8.onnx",
        "model_int8.onnx",
        "accb5b9e914356d01190c9f208ac822b345b24daeee4aa0fb345dfcc89a871d5",
        34_057_273,
    ),
    (
        "tokenizer.json",
        "tokenizer.json",
        "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        711_396,
    ),
    (
        "tokenizer_config.json",
        "tokenizer_config.json",
        "0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53",
        1_242,
    ),
    (
        "special_tokens_map.json",
        "special_tokens_map.json",
        "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
        695,
    ),
    (
        "vocab.txt",
        "vocab.txt",
        "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
        231_508,
    ),
    (
        "config.json",
        "config.json",
        "86d5a871bc41599d2e04d7518c6f09cef47800befbe8dace4888f5fa7a9b4333",
        711,
    ),
)


class PublishedModelManifestTests(unittest.TestCase):
    def test_python39_apple_silicon_runtime_is_pinned(self) -> None:
        self.assertIs(RUNTIME_WHEELS["darwin-arm64-py39"], DARWIN_ARM64_PY39)
        self.assertEqual(
            tuple((wheel.filename, wheel.sha256) for wheel in DARWIN_ARM64_PY39),
            (
                (
                    "onnxruntime-1.19.2-cp39-cp39-macosx_11_0_universal2.whl",
                    "006c8d326835c017a9e9f74c9c77ebb570a71174a1e89fe078b29a557d9c3848",
                ),
                (
                    "numpy-2.0.2-cp39-cp39-macosx_14_0_arm64.whl",
                    "2b2955fa6f11907cf7a70dab0d0755159bca87755e831e47932367fc8f2f2d0b",
                ),
                (
                    "tokenizers-0.20.3-cp39-cp39-macosx_11_0_arm64.whl",
                    "f4cb0c614b0135e781de96c2af87e73da0389ac1458e2a97562ed26e29490d8d",
                ),
                (
                    "flatbuffers-24.3.25-py2.py3-none-any.whl",
                    "8dbdec58f935f3765e4f7f3cf635ac3a77f83568138d6a2311f524ec96364812",
                ),
            ),
        )

    def test_manifest_is_pinned_to_the_published_immutable_revision(self) -> None:
        self.assertEqual(MODEL_REPOSITORY, REPOSITORY)
        self.assertEqual(MODEL_REVISION, REVISION)
        self.assertEqual(MODEL_NAME, MODEL_DIRECTORY)
        self.assertEqual(
            MODEL_BASE_URL,
            f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}",
        )

    def test_every_download_matches_the_published_metadata(self) -> None:
        actual = tuple(
            (
                artifact.remote_path,
                artifact.local_path,
                artifact.sha256,
                artifact.size,
            )
            for artifact in MODEL_ARTIFACTS
        )
        self.assertEqual(actual, EXPECTED_ARTIFACTS)
        self.assertEqual(sum(item.size for item in MODEL_ARTIFACTS), 35_002_825)
        for artifact in MODEL_ARTIFACTS:
            self.assertEqual(
                artifact.url,
                f"https://huggingface.co/{REPOSITORY}/resolve/"
                f"{REVISION}/{artifact.remote_path}",
            )

    def test_old_prerelease_assets_cannot_make_the_new_model_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "user_files"
            old_model_dir = data_root / "model" / "MedEmbed-small-v0.1-int8"
            old_model_dir.mkdir(parents=True)
            for artifact in MODEL_ARTIFACTS:
                (old_model_dir / artifact.local_path).write_bytes(b"old")

            manager = ModelManager(data_root, Path(directory) / "bundle")

            self.assertEqual(manager.model_dir.name, MODEL_DIRECTORY)
            self.assertNotEqual(manager.model_dir, old_model_dir)
            self.assertFalse(manager.model_ready())

    def test_compliance_docs_use_the_public_provenance_record(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for filename in ("PRIVACY.md", "DATA_SOURCES.md", "THIRD_PARTY_NOTICES.md"):
            text = (root / filename).read_text(encoding="utf-8")
            self.assertIn(REPOSITORY, text, filename)
            self.assertIn(REVISION, text, filename)
            self.assertIn(PROVENANCE_URL, text, filename)
            self.assertNotIn(LEGACY_CONVERTER, text, filename)
            self.assertNotIn(LEGACY_REVISION_PREFIX, text, filename)

        sources = (root / "DATA_SOURCES.md").read_text(encoding="utf-8")
        notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        for text in (sources, notices):
            self.assertIn(
                "40a5850d046cfdb56154e332b4d7099b63e8d50e",
                text,
            )
        self.assertIn(EXPECTED_ARTIFACTS[0][2], sources)


if __name__ == "__main__":
    unittest.main()
