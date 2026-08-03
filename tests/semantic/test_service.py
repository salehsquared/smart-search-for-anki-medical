from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

from semantic.service import (
    SemanticDocument,
    SemanticService,
    semantic_text_hash,
)
import semantic.service as service_module


class SemanticServiceTests(unittest.TestCase):
    def test_semantic_hash_is_text_only_and_stable(self) -> None:
        document = SemanticDocument.from_text(42, "bupropion")

        self.assertEqual(document.content_hash, semantic_text_hash("bupropion"))
        self.assertEqual(document.content_hash, semantic_text_hash("bupropion"))
        self.assertNotEqual(document.content_hash, semantic_text_hash("Bupropion"))

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
            service.index.known_hashes = lambda _ids=None, **_kwargs: {
                document.note_id: document.content_hash
            }
            service._last_error = "old runtime error"
            service._last_error_kind = "model"

            changed = service.index_documents([document])

            self.assertEqual(changed, 0)
            self.assertIsNone(service._last_error)
            self.assertIsNone(service._last_error_kind)

    def test_freshness_queries_are_bounded_and_empty_ids_do_not_read_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager"):
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )
            documents = [
                SemanticDocument.from_text(note_id, f"note {note_id}")
                for note_id in range(1, 1_902)
            ]
            requested_sizes = []
            original = service.index.known_hashes

            def known_hashes(note_ids=None, **_kwargs):
                requested_sizes.append(None if note_ids is None else len(note_ids))
                return original(note_ids)

            service.index.known_hashes = known_hashes

            self.assertEqual(service.index.known_hashes([]), {})
            self.assertEqual(len(service.stale_documents(documents)), len(documents))
            self.assertEqual(requested_sizes[0], 0)
            self.assertEqual(requested_sizes[1:], [900, 900, 101])

    def test_unload_releases_an_idle_embedder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager"):
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )
            service._embedder = MagicMock()

            self.assertTrue(service.model_loaded)
            self.assertTrue(service.unload())
            self.assertFalse(service.model_loaded)
            self.assertFalse(service.unload())

    def test_indexing_embeds_only_stale_documents_in_small_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager"):
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )
            documents = [
                SemanticDocument.from_text(note_id, f"note {note_id}")
                for note_id in range(1, 36)
            ]

            class TrackingIndex:
                def __init__(self) -> None:
                    self.hashes = {1: documents[0].content_hash}
                    self.upserts = []

                def known_hashes(self, note_ids=None, **_kwargs):
                    return {
                        note_id: self.hashes[note_id]
                        for note_id in (note_ids or ())
                        if note_id in self.hashes
                    }

                def upsert_many(self, note_ids, content_hashes, _vectors):
                    self.upserts.append(tuple(note_ids))
                    self.hashes.update(zip(note_ids, content_hashes))

            class TrackingEmbedder:
                def __init__(self) -> None:
                    self.batches = []

                def embed(self, texts, *, cancel_check=None):
                    if cancel_check is not None:
                        cancel_check()
                    self.batches.append(tuple(texts))
                    return [object() for _text in texts]

            tracking_index = TrackingIndex()
            tracking_embedder = TrackingEmbedder()
            service.index = tracking_index
            service._embedder = tracking_embedder
            progress = []

            changed = service.index_documents(
                documents,
                batch_size=16,
                progress=lambda done, total: progress.append((done, total)),
            )

            self.assertEqual(changed, 34)
            self.assertEqual(
                [len(batch) for batch in tracking_embedder.batches],
                [16, 16, 2],
            )
            self.assertEqual(
                [len(batch) for batch in tracking_index.upserts],
                [16, 16, 2],
            )
            self.assertEqual(progress, [(16, 34), (32, 34), (34, 34)])

    def test_cancellation_after_inference_prevents_vector_upsert_and_error_state(
        self,
    ) -> None:
        class Cancelled(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager"):
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )
            document = SemanticDocument.from_text(9, "pulmonary embolism")
            cancelled = threading.Event()
            upserts = []

            service.index = MagicMock()
            service.index.known_hashes.return_value = {}
            service.index.upsert_many.side_effect = (
                lambda *args: upserts.append(args)
            )

            class CancelAfterInference:
                def embed(self, texts, *, cancel_check=None):
                    del texts, cancel_check
                    cancelled.set()
                    return [object()]

            service._embedder = CancelAfterInference()

            def checkpoint() -> None:
                if cancelled.is_set():
                    raise Cancelled()

            with self.assertRaises(Cancelled):
                service.index_documents(
                    [document],
                    cancel_check=checkpoint,
                )

            self.assertEqual(upserts, [])
            self.assertIsNone(service._last_error)
            self.assertIsNone(service._last_error_kind)

    def test_unload_waits_for_active_inference_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager"):
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )

            started = threading.Event()
            release = threading.Event()

            class BlockingEmbedder:
                def embed(self, _texts):
                    started.set()
                    if not release.wait(timeout=2):
                        raise RuntimeError("embedder lease test timed out")
                    return []

            service._embedder = BlockingEmbedder()
            worker = threading.Thread(target=service.warmup)
            worker.start()
            self.assertTrue(started.wait(timeout=1))

            self.assertTrue(service.unload())
            self.assertTrue(service.model_loaded)
            release.set()
            worker.join(timeout=1)

            self.assertFalse(worker.is_alive())
            self.assertFalse(service.model_loaded)

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
                cancel_check=None,
            )

    def test_loaded_runtime_repair_requires_restart_across_profiles(self) -> None:
        service_module._RUNTIME_RESTART_REQUIRED.clear()
        self.addCleanup(service_module._RUNTIME_RESTART_REQUIRED.clear)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager") as manager_type:
                manager_type.return_value = MagicMock()
                first = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="first",
                )
                second = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="second",
                )

            with patch.dict(
                sys.modules,
                {"onnxruntime": types.ModuleType("onnxruntime")},
            ):
                first.install(repair=True)

            self.assertTrue(first.restart_required)
            self.assertTrue(second.restart_required)

    def test_failed_loaded_runtime_repair_still_requires_restart(self) -> None:
        service_module._RUNTIME_RESTART_REQUIRED.clear()
        self.addCleanup(service_module._RUNTIME_RESTART_REQUIRED.clear)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager") as manager_type:
                manager = MagicMock()
                manager.install_all.side_effect = RuntimeError("model failed")
                manager_type.return_value = manager
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )

            with patch.dict(
                sys.modules,
                {"onnxruntime": types.ModuleType("onnxruntime")},
            ), self.assertRaisesRegex(RuntimeError, "model failed"):
                service.install(repair=True)

            self.assertTrue(service.restart_required)


if __name__ == "__main__":
    unittest.main()
