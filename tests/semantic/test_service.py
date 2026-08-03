from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from semantic.errors import SemanticRuntimeError, SemanticWorkerError
from semantic.service import (
    SemanticDocument,
    SemanticService,
    semantic_text_hash,
)


class _FakeWorker:
    def __init__(self) -> None:
        self.running = False
        self.embedded: list[tuple[tuple[str, ...], bool]] = []
        self.terminated = 0
        self.waited = 0

    def embed(self, texts, *, background, cancel_check=None):
        if cancel_check is not None:
            cancel_check()
        self.running = True
        self.embedded.append((tuple(texts), bool(background)))
        return [[1.0] for _text in texts]

    def warmup(self) -> None:
        self.running = True

    def terminate_now(self) -> bool:
        existed = self.running
        self.running = False
        self.terminated += 1
        return existed

    def terminate_and_wait(self) -> bool:
        existed = self.running
        self.running = False
        self.waited += 1
        return existed


class SemanticServiceTests(unittest.TestCase):
    def _service(self, root: Path) -> tuple[SemanticService, MagicMock]:
        with patch("semantic.service.ModelManager") as manager_type:
            manager = MagicMock()
            manager_type.return_value = manager
            service = SemanticService(
                data_root=root / "data",
                bundle_root=root / "bundle",
                profile_key="profile",
            )
        service._worker = _FakeWorker()
        return service, manager

    def test_semantic_hash_is_text_only_and_stable(self) -> None:
        document = SemanticDocument.from_text(42, "bupropion")

        self.assertEqual(document.content_hash, semantic_text_hash("bupropion"))
        self.assertEqual(document.content_hash, semantic_text_hash("bupropion"))
        self.assertNotEqual(document.content_hash, semantic_text_hash("Bupropion"))

    def test_service_constructs_one_isolated_worker_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("semantic.service.ModelManager") as manager_type, patch(
                "semantic.service.SemanticWorkerClient"
            ) as worker_type:
                manager = MagicMock()
                manager_type.return_value = manager
                service = SemanticService(
                    data_root=root / "data",
                    bundle_root=root / "bundle",
                    profile_key="profile",
                )

            worker_type.assert_called_once_with(manager, root / "bundle")
            self.assertIs(service._worker, worker_type.return_value)

    def test_noop_reindex_clears_a_previous_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))
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
            service, _manager = self._service(Path(directory))
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

    def test_unload_terminates_an_idle_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))
            service._worker.running = True

            self.assertTrue(service.model_loaded)
            self.assertTrue(service.unload())
            self.assertFalse(service.model_loaded)
            self.assertFalse(service.unload())

    def test_close_waits_for_worker_reaping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))
            service._worker.running = True

            self.assertTrue(service.close())
            self.assertEqual(service._worker.waited, 1)
            self.assertFalse(service.model_loaded)

    def test_indexing_embeds_only_stale_documents_in_small_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, manager = self._service(Path(directory))
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

            tracking_index = TrackingIndex()
            service.index = tracking_index
            progress = []

            changed = service.index_documents(
                documents,
                batch_size=16,
                progress=lambda done, total: progress.append((done, total)),
            )

            self.assertEqual(changed, 34)
            self.assertEqual(
                [len(batch[0]) for batch in service._worker.embedded],
                [16, 16, 2],
            )
            self.assertTrue(all(background for _batch, background in service._worker.embedded))
            self.assertEqual(
                [len(batch) for batch in tracking_index.upserts],
                [16, 16, 2],
            )
            self.assertEqual(progress, [(16, 34), (32, 34), (34, 34)])
            self.assertEqual(manager.activate_vector_runtime.call_count, 3)

    def test_cancellation_after_inference_prevents_vector_upsert_and_error_state(
        self,
    ) -> None:
        class Cancelled(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))
            document = SemanticDocument.from_text(9, "pulmonary embolism")
            cancelled = threading.Event()
            upserts = []

            service.index = MagicMock()
            service.index.known_hashes.return_value = {}
            service.index.upsert_many.side_effect = lambda *args: upserts.append(args)

            class CancelAfterInference(_FakeWorker):
                def embed(self, texts, *, background, cancel_check=None):
                    del texts, background, cancel_check
                    cancelled.set()
                    return [[1.0]]

            service._worker = CancelAfterInference()

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

    def test_search_uses_interactive_worker_then_existing_vector_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, manager = self._service(Path(directory))
            service.index = MagicMock()
            service.index.count.return_value = 1
            service.index.search.return_value = [
                MagicMock(note_id=42, score=0.75)
            ]

            hits = service.search("heart failure", limit=7, allowed_note_ids={42})

            self.assertEqual([(hit.note_id, hit.score) for hit in hits], [(42, 0.75)])
            self.assertEqual(
                service._worker.embedded,
                [(('heart failure',), False)],
            )
            manager.activate_vector_runtime.assert_called_once_with()
            service.index.search.assert_called_once()

    def test_repair_install_reaps_worker_before_runtime_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, manager = self._service(Path(directory))
            service._worker.running = True

            service.install(repair=True)

            self.assertEqual(service._worker.waited, 1)
            manager.install_all.assert_called_once_with(
                None,
                repair_runtime=True,
                cancel_check=None,
            )
            self.assertFalse(service.restart_required)

    def test_runtime_only_upgrade_never_checks_or_downloads_model_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, manager = self._service(Path(directory))
            service._worker.running = True

            service.install_runtime()

            self.assertEqual(service._worker.waited, 1)
            manager.install_runtime.assert_called_once_with(cancel_check=None)
            manager.install_all.assert_not_called()
            manager.install_model.assert_not_called()

    def test_cancelled_install_does_not_persist_a_false_model_error(self) -> None:
        class Cancelled(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))

            def checkpoint() -> None:
                raise Cancelled()

            with self.assertRaises(Cancelled):
                service.install(cancel_check=checkpoint)

            self.assertIsNone(service._last_error)
            self.assertIsNone(service._last_error_kind)

    def test_cancelled_worker_disconnect_does_not_poison_semantic_status(self) -> None:
        class Cancelled(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))
            service.index = MagicMock()
            service.index.count.return_value = 1
            cancelled = threading.Event()

            class DisconnectingWorker(_FakeWorker):
                def embed(self, texts, *, background, cancel_check=None):
                    del texts, background, cancel_check
                    cancelled.set()
                    raise SemanticWorkerError("worker stopped")

            service._worker = DisconnectingWorker()

            def checkpoint() -> None:
                if cancelled.is_set():
                    raise Cancelled()

            with self.assertRaises(Cancelled):
                service.search("heart failure", cancel_check=checkpoint)

            self.assertIsNone(service._last_error)
            self.assertIsNone(service._last_error_kind)

    def test_transient_worker_crash_is_retryable_without_repair_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))
            service.index = MagicMock()
            service.index.count.return_value = 1
            service._worker.embed = MagicMock(
                side_effect=SemanticWorkerError("worker crashed")
            )

            with self.assertRaisesRegex(SemanticWorkerError, "worker crashed"):
                service.search("heart failure")

            self.assertIsNone(service._last_error)
            self.assertIsNone(service._last_error_kind)

    def test_transient_worker_crash_during_index_is_retryable_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))
            document = SemanticDocument.from_text(42, "heart failure")
            service._worker.embed = MagicMock(
                side_effect=SemanticWorkerError("worker crashed")
            )

            with self.assertRaisesRegex(SemanticWorkerError, "worker crashed"):
                service.index_documents([document])

            self.assertIsNone(service._last_error)
            self.assertIsNone(service._last_error_kind)

    def test_verified_runtime_failure_requests_model_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _manager = self._service(Path(directory))
            service.index = MagicMock()
            service.index.count.return_value = 1
            service._worker.embed = MagicMock(
                side_effect=SemanticRuntimeError("model could not load")
            )

            self.assertEqual(service.search("heart failure"), [])

            self.assertEqual(service._last_error, "model could not load")
            self.assertEqual(service._last_error_kind, "model")

    def test_failed_repair_never_requires_restarting_anki(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, manager = self._service(Path(directory))
            manager.install_all.side_effect = RuntimeError("model failed")

            with self.assertRaisesRegex(RuntimeError, "model failed"):
                service.install(repair=True)

            self.assertFalse(service.restart_required)


if __name__ == "__main__":
    unittest.main()
