from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

import numpy as np

from semantic.vector_index import VECTOR_SCAN_BATCH_SIZE, VectorIndex


class VectorIndexTests(unittest.TestCase):
    def test_scan_batches_bound_host_allocator_high_water(self) -> None:
        self.assertEqual(VECTOR_SCAN_BATCH_SIZE, 512)

    def test_upsert_search_filter_delete_and_slot_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = VectorIndex(Path(directory), dimension=3)
            index.upsert_many(
                [10, 20, 30],
                ["a", "b", "c"],
                np.asarray(
                    [
                        [1.0, 0.0, 0.0],
                        [0.9, 0.1, 0.0],
                        [0.0, 1.0, 0.0],
                    ],
                    dtype=np.float32,
                ),
            )
            self.assertEqual(index.count(), 3)
            self.assertEqual([hit.note_id for hit in index.search([1, 0, 0])], [10, 20, 30])
            self.assertEqual(
                [hit.note_id for hit in index.search([1, 0, 0], allowed_note_ids={20, 30})],
                [20, 30],
            )

            index.delete([10])
            self.assertEqual(index.count(), 2)
            index.upsert_many([40], ["d"], np.asarray([[1, 0, 0]], dtype=np.float32))
            self.assertEqual(index.search([1, 0, 0], limit=1)[0].note_id, 40)

    def test_rejects_bad_shapes_and_zero_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = VectorIndex(Path(directory), dimension=3)
            with self.assertRaises(ValueError):
                index.upsert_many([1], ["hash"], np.asarray([[1, 2]], dtype=np.float32))
            with self.assertRaises(ValueError):
                index.upsert_many([1], ["hash"], np.asarray([[0, 0, 0]], dtype=np.float32))

    def test_large_id_sets_are_chunked_for_portable_sqlite_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = VectorIndex(Path(directory), dimension=2)
            count = 1_050
            note_ids = list(range(1, count + 1))
            vectors = np.tile(
                np.asarray([[1.0, 0.0]], dtype=np.float32),
                (count, 1),
            )
            index.upsert_many(
                note_ids,
                [f"hash-{note_id}" for note_id in note_ids],
                vectors,
            )

            known = index.known_hashes(note_ids)
            self.assertEqual(len(known), count)
            self.assertEqual(known[count], f"hash-{count}")
            self.assertEqual(index.known_hashes([]), {})

            index.delete(note_ids)
            self.assertEqual(index.count(), 0)

    def test_hash_and_delete_batches_honor_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = VectorIndex(Path(directory), dimension=2)
            count = 1_050
            note_ids = list(range(1, count + 1))
            index.upsert_many(
                note_ids,
                [f"hash-{note_id}" for note_id in note_ids],
                np.tile(
                    np.asarray([[1.0, 0.0]], dtype=np.float32),
                    (count, 1),
                ),
            )

            checkpoints = 0

            def cancel_hashes() -> None:
                nonlocal checkpoints
                checkpoints += 1
                if checkpoints == 2:
                    raise RuntimeError("cancelled hashes")

            with self.assertRaisesRegex(RuntimeError, "cancelled hashes"):
                index.known_hashes(
                    note_ids,
                    cancel_check=cancel_hashes,
                )

            delete_checkpoints = 0

            def cancel_delete() -> None:
                nonlocal delete_checkpoints
                delete_checkpoints += 1
                if delete_checkpoints == 3:
                    raise RuntimeError("cancelled delete")

            with self.assertRaisesRegex(RuntimeError, "cancelled delete"):
                index.delete(note_ids, cancel_check=cancel_delete)
            self.assertEqual(index.count(), count)

    def test_top_k_is_global_across_scan_chunks_and_ties_use_note_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = VectorIndex(Path(directory), dimension=2)
            count = 4_100
            note_ids = list(range(count, 0, -1))
            vectors = np.tile(
                np.asarray([[1.0, 0.0]], dtype=np.float32),
                (count, 1),
            )
            index.upsert_many(
                note_ids,
                [f"hash-{note_id}" for note_id in note_ids],
                vectors,
            )

            hits = index.search([1.0, 0.0], limit=3)

            self.assertEqual([hit.note_id for hit in hits], [1, 2, 3])

    def test_large_allowed_id_filter_is_queried_in_portable_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = VectorIndex(Path(directory), dimension=2)
            count = 1_050
            note_ids = list(range(1, count + 1))
            vectors = np.tile(
                np.asarray([[1.0, 0.0]], dtype=np.float32),
                (count, 1),
            )
            index.upsert_many(
                note_ids,
                [f"hash-{note_id}" for note_id in note_ids],
                vectors,
            )

            allowed = set(range(901, count + 1)) | set(range(20_000, 21_000))
            hits = index.search(
                [1.0, 0.0],
                limit=5,
                allowed_note_ids=allowed,
            )

            self.assertEqual([hit.note_id for hit in hits], [901, 902, 903, 904, 905])

    def test_large_vector_scan_honors_cancellation_between_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = VectorIndex(Path(directory), dimension=2)
            count = 5_000
            note_ids = list(range(1, count + 1))
            vectors = np.tile(
                np.asarray([[1.0, 0.0]], dtype=np.float32),
                (count, 1),
            )
            index.upsert_many(
                note_ids,
                [f"hash-{note_id}" for note_id in note_ids],
                vectors,
            )
            checkpoints = 0

            def cancel() -> None:
                nonlocal checkpoints
                checkpoints += 1
                if checkpoints == 3:
                    raise RuntimeError("cancelled")

            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                index.search(
                    [1.0, 0.0],
                    limit=100,
                    cancel_check=cancel,
                )

    def test_source_generation_persists_and_vector_mutations_clear_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = VectorIndex(root, dimension=2)
            self.assertIsNone(index.source_generation)

            index.mark_source_generation(7)
            self.assertEqual(index.source_generation, 7)
            self.assertEqual(VectorIndex(root, dimension=2).source_generation, 7)

            index.upsert_many(
                [10],
                ["hash-10"],
                np.asarray([[1.0, 0.0]], dtype=np.float32),
            )
            self.assertIsNone(index.source_generation)

            index.mark_source_generation(8)
            index.delete([10])
            self.assertIsNone(index.source_generation)

            with self.assertRaises(ValueError):
                index.mark_source_generation(-1)

    def test_constructor_rejects_malformed_source_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = VectorIndex(root, dimension=2)
            index.mark_source_generation(7)
            with closing(sqlite3.connect(index.db_path)) as connection:
                connection.execute(
                    "UPDATE meta SET value = 'not-a-generation' "
                    "WHERE key = 'source_generation'"
                )
                connection.commit()

            with self.assertRaisesRegex(RuntimeError, "source_generation"):
                VectorIndex(root, dimension=2)

    def test_constructor_rejects_missing_vector_file_with_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = VectorIndex(root, dimension=2)
            index.upsert_many(
                [10],
                ["hash-10"],
                np.asarray([[1.0, 0.0]], dtype=np.float32),
            )
            index.vector_path.unlink()

            with self.assertRaisesRegex(RuntimeError, "vector file is missing"):
                VectorIndex(root, dimension=2)

    def test_constructor_rejects_truncated_vector_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = VectorIndex(root, dimension=2)
            index.upsert_many(
                [10],
                ["hash-10"],
                np.asarray([[1.0, 0.0]], dtype=np.float32),
            )
            expected_size = index.vector_path.stat().st_size
            with index.vector_path.open("r+b") as handle:
                handle.truncate(expected_size - 2)

            with self.assertRaisesRegex(RuntimeError, "size does not match"):
                VectorIndex(root, dimension=2)

    def test_constructor_rejects_negative_or_excessive_next_slot(self) -> None:
        for invalid in (-1, 1_025):
            with self.subTest(next_slot=invalid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                index = VectorIndex(root, dimension=2)
                index.upsert_many(
                    [10],
                    ["hash-10"],
                    np.asarray([[1.0, 0.0]], dtype=np.float32),
                )
                with closing(sqlite3.connect(index.db_path)) as connection:
                    connection.execute(
                        "UPDATE meta SET value = ? WHERE key = 'next_slot'",
                        (str(invalid),),
                    )
                    connection.commit()

                with self.assertRaisesRegex(RuntimeError, "next_slot"):
                    VectorIndex(root, dimension=2)

    def test_constructor_rejects_out_of_range_occupied_slots(self) -> None:
        for invalid in (-1, 1):
            with self.subTest(slot=invalid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                index = VectorIndex(root, dimension=2)
                index.upsert_many(
                    [10],
                    ["hash-10"],
                    np.asarray([[1.0, 0.0]], dtype=np.float32),
                )
                with closing(sqlite3.connect(index.db_path)) as connection:
                    connection.execute(
                        "UPDATE vectors SET slot = ? WHERE note_id = 10",
                        (invalid,),
                    )
                    connection.commit()

                with self.assertRaisesRegex(RuntimeError, "occupied slots"):
                    VectorIndex(root, dimension=2)

    def test_constructor_rejects_out_of_range_or_overlapping_free_slots(self) -> None:
        for invalid, message in ((-1, "invalid free slots"), (1, "invalid free slots"), (0, "overlapping")):
            with self.subTest(slot=invalid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                index = VectorIndex(root, dimension=2)
                index.upsert_many(
                    [10],
                    ["hash-10"],
                    np.asarray([[1.0, 0.0]], dtype=np.float32),
                )
                with closing(sqlite3.connect(index.db_path)) as connection:
                    connection.execute(
                        "INSERT INTO free_slots(slot) VALUES (?)",
                        (invalid,),
                    )
                    connection.commit()

                with self.assertRaisesRegex(RuntimeError, message):
                    VectorIndex(root, dimension=2)


if __name__ == "__main__":
    unittest.main()
