from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from semantic.service import SemanticDocument, SemanticService


class SemanticServiceTests(unittest.TestCase):
    def test_embedder_is_constructed_once_across_concurrent_callers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager"):
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )

            constructor_started = threading.Event()
            release_constructor = threading.Event()
            constructed = []

            class HeldEmbedder:
                def __init__(self, _manager) -> None:
                    constructed.append(self)
                    constructor_started.set()
                    if not release_constructor.wait(timeout=2):
                        raise RuntimeError("embedder constructor test timed out")

            results = []
            errors = []

            def load() -> None:
                try:
                    results.append(service._get_embedder())
                except Exception as error:
                    errors.append(error)

            with patch("semantic.service.OnnxMedicalEmbedder", HeldEmbedder):
                first = threading.Thread(target=load)
                second = threading.Thread(target=load)
                first.start()
                self.assertTrue(constructor_started.wait(timeout=1))
                second.start()
                self.assertEqual(len(constructed), 1)
                release_constructor.set()
                first.join(timeout=1)
                second.join(timeout=1)

            self.assertEqual(errors, [])
            self.assertEqual(len(constructed), 1)
            self.assertEqual(results, [constructed[0], constructed[0]])

    def test_noop_reindex_clears_a_previous_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager"):
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )
            document = SemanticDocument.from_text(42, "bupropion")
            service.index.known_hashes = lambda _ids=None: {
                document.note_id: document.content_hash
            }
            service._last_error = "old runtime error"
            service._last_error_kind = "model"

            changed = service.index_documents([document])

            self.assertEqual(changed, 0)
            self.assertIsNone(service._last_error)
            self.assertIsNone(service._last_error_kind)

    def test_repair_install_forces_runtime_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager") as manager_type:
                manager = MagicMock()
                manager_type.return_value = manager
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )

            service.install(repair=True)

            manager.install_all.assert_called_once_with(
                None,
                repair_runtime=True,
            )


if __name__ == "__main__":
    unittest.main()
