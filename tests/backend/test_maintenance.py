from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from backend.maintenance import (
    CardManifest,
    DirtyReason,
    FacetManifest,
    FacetManifestKey,
    MaintenanceJournal,
    NoteManifest,
    SCHEMA_VERSION,
    UnsafeMaintenancePath,
)


def manifest(
    note_id: int,
    *,
    modified: int = 10,
    usn: int = 0,
    note_type: int = 100,
    fields_length: int = 20,
    tags_length: int = 5,
    checksum: int = 30,
    fields_digest: bytes = b"",
    tags_digest: bytes = b"",
) -> NoteManifest:
    return NoteManifest(
        note_id=note_id,
        modified_seconds=modified,
        usn=usn,
        note_type_id=note_type,
        fields_length=fields_length,
        tags_length=tags_length,
        field_checksum=checksum,
        fields_digest=fields_digest,
        tags_digest=tags_digest,
    )


def card_manifest(
    card_id: int,
    *,
    note_id: int = 1,
    home_deck_id: int = 10,
) -> CardManifest:
    return CardManifest(
        card_id=card_id,
        note_id=note_id,
        home_deck_id=home_deck_id,
    )


def facet_manifest(
    facet: str,
    object_id: int,
    signature: str,
) -> FacetManifest:
    return FacetManifest(
        facet=facet,
        object_id=object_id,
        signature=signature,
    )


class MaintenanceJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "maintenance.sqlite3"
        self.journal = MaintenanceJournal(self.path)

    def tearDown(self) -> None:
        self.journal.close()
        self.temporary.cleanup()

    def test_uses_wal_and_full_synchronous_mode(self) -> None:
        self.assertEqual(
            str(self.journal.connection.execute("PRAGMA journal_mode").fetchone()[0]),
            "wal",
        )
        self.assertEqual(
            int(self.journal.connection.execute("PRAGMA synchronous").fetchone()[0]),
            2,
        )

    def test_enqueue_merges_reasons_and_advances_sequence(self) -> None:
        first = self.journal.enqueue(
            [2, 1, 1], DirtyReason.LEXICAL, queued_at=100.0
        )
        second = self.journal.enqueue(
            [1], DirtyReason.SEMANTIC, queued_at=200.0
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertGreater(second, first)
        pending = {entry.note_id: entry for entry in self.journal.pending()}
        self.assertEqual(pending[1].sequence, second)
        self.assertEqual(pending[1].queued_at, 200.0)
        self.assertEqual(
            pending[1].reasons,
            DirtyReason.LEXICAL | DirtyReason.SEMANTIC,
        )
        self.assertEqual(pending[2].sequence, first)
        self.assertEqual(self.journal.pending_count(), 2)

    def test_gui_readiness_probe_never_waits_behind_writer_lock(self) -> None:
        acquired = threading.Event()
        release = threading.Event()

        def hold_writer_lane() -> None:
            with self.journal._lock:
                acquired.set()
                release.wait(timeout=2)

        worker = threading.Thread(target=hold_writer_lane)
        worker.start()
        self.assertTrue(acquired.wait(timeout=1))
        started = time.perf_counter()
        try:
            self.assertIsNone(self.journal.try_work_state())
            self.assertIsNone(self.journal.try_pending(limit=10))
            available, request = self.journal.try_audit_request()
            self.assertFalse(available)
            self.assertIsNone(request)
            self.assertLess(time.perf_counter() - started, 0.05)
        finally:
            release.set()
            worker.join(timeout=1)

    def test_cancelled_manifest_replace_rolls_back_atomically(self) -> None:
        self.journal.replace_manifest([manifest(1)])

        class Cancelled(RuntimeError):
            pass

        with self.assertRaises(Cancelled):
            self.journal.replace_manifest(
                [manifest(2), manifest(3)],
                cancel_check=lambda: (_ for _ in ()).throw(Cancelled()),
            )

        self.assertEqual(self.journal.manifest_rows(), (manifest(1),))

    def test_targeted_card_manifest_refresh_removes_missing_siblings(self) -> None:
        self.journal.replace_card_manifest(
            [
                card_manifest(101, note_id=1),
                card_manifest(102, note_id=1),
                card_manifest(201, note_id=2),
            ]
        )

        changed = self.journal.replace_card_manifest_for_notes(
            [1],
            [card_manifest(102, note_id=1, home_deck_id=20)],
        )

        self.assertEqual(changed, 1)
        self.assertEqual(
            self.journal.card_manifest_rows(),
            (
                card_manifest(102, note_id=1, home_deck_id=20),
                card_manifest(201, note_id=2),
            ),
        )

    def test_empty_enqueue_does_not_consume_sequence(self) -> None:
        self.assertIsNone(self.journal.enqueue([], DirtyReason.LEXICAL))
        self.assertEqual(self.journal.enqueue([1], DirtyReason.LEXICAL), 1)

    def test_old_acknowledgement_cannot_erase_newer_enqueue(self) -> None:
        first = self.journal.enqueue([1], DirtyReason.LEXICAL)
        snapshot = self.journal.pending()[0]
        self.assertEqual(snapshot.sequence, first)
        second = self.journal.enqueue([1], DirtyReason.SEMANTIC)

        self.assertFalse(self.journal.acknowledge(1, snapshot.sequence))
        remaining = self.journal.pending()[0]
        self.assertEqual(remaining.sequence, second)
        self.assertEqual(
            remaining.reasons,
            DirtyReason.LEXICAL | DirtyReason.SEMANTIC,
        )

    def test_reason_specific_acknowledgement_keeps_other_work(self) -> None:
        sequence = self.journal.enqueue(
            [1], DirtyReason.LEXICAL | DirtyReason.SEMANTIC
        )
        self.assertTrue(
            self.journal.acknowledge(
                1, sequence, reasons=DirtyReason.LEXICAL
            )
        )
        self.assertEqual(self.journal.pending()[0].reasons, DirtyReason.SEMANTIC)
        self.assertTrue(
            self.journal.acknowledge(
                1, sequence, reasons=DirtyReason.SEMANTIC
            )
        )
        self.assertEqual(self.journal.pending(), ())

    def test_batch_acknowledgement_is_sequence_guarded_and_atomic(self) -> None:
        first_sequence = self.journal.enqueue(
            [1, 2], DirtyReason.NOTE_CONTENT
        )
        assert first_sequence is not None
        newer_sequence = self.journal.enqueue([2], DirtyReason.SEMANTIC)
        assert newer_sequence is not None

        self.assertEqual(
            self.journal.acknowledge_many(
                ((1, first_sequence), (2, first_sequence))
            ),
            1,
        )
        self.assertEqual(
            tuple((row.note_id, row.sequence) for row in self.journal.pending()),
            ((2, newer_sequence),),
        )

        self.journal.enqueue([3], DirtyReason.LEXICAL)
        snapshot = tuple(
            (row.note_id, row.sequence) for row in self.journal.pending()
        )

        class Cancelled(RuntimeError):
            pass

        with self.assertRaises(Cancelled):
            self.journal.acknowledge_many(
                snapshot,
                cancel_check=lambda: (_ for _ in ()).throw(Cancelled()),
            )
        self.assertEqual(
            tuple((row.note_id, row.sequence) for row in self.journal.pending()),
            snapshot,
        )

    def test_attempt_counter_is_guarded_by_sequence_and_resets_on_new_work(self) -> None:
        first = self.journal.enqueue([1], DirtyReason.LEXICAL)
        self.assertTrue(self.journal.mark_attempted(1, first))
        self.assertEqual(self.journal.pending()[0].attempts, 1)
        second = self.journal.enqueue([1], DirtyReason.SEMANTIC)
        self.assertFalse(self.journal.mark_attempted(1, first))
        self.assertEqual(self.journal.pending()[0].attempts, 0)
        self.assertTrue(self.journal.mark_attempted(1, second))

    def test_queue_is_persistent_across_close_and_reopen(self) -> None:
        sequence = self.journal.enqueue(
            [1], DirtyReason.NOTE_CONTENT, queued_at=123.0
        )
        self.journal.close()
        self.journal = MaintenanceJournal(self.path)

        entry = self.journal.pending()[0]
        self.assertEqual(entry.sequence, sequence)
        self.assertEqual(entry.reasons, DirtyReason.NOTE_CONTENT)
        self.assertEqual(entry.queued_at, 123.0)
        self.assertGreater(
            self.journal.enqueue([2], DirtyReason.LEXICAL), sequence
        )

    def test_concurrent_enqueues_are_serialized_and_lossless(self) -> None:
        reasons = (
            DirtyReason.LEXICAL,
            DirtyReason.SEMANTIC,
            DirtyReason.VOCABULARY,
            DirtyReason.CARD_METADATA,
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            sequences = tuple(
                executor.map(
                    lambda reason: self.journal.enqueue([1], reason),
                    reasons,
                )
            )

        self.assertEqual(len(set(sequences)), len(reasons))
        entry = self.journal.pending()[0]
        self.assertEqual(entry.sequence, max(sequences))
        self.assertEqual(
            entry.reasons,
            DirtyReason.LEXICAL
            | DirtyReason.SEMANTIC
            | DirtyReason.VOCABULARY
            | DirtyReason.CARD_METADATA,
        )

    def test_audit_completion_cannot_clear_newer_request(self) -> None:
        first = self.journal.request_audit("sync")
        self.journal.set_meta("force_canonical_hydration", "1")
        self.assertTrue(self.journal.audit_needed)
        self.assertEqual(self.journal.audit_request().reason, "sync")
        second = self.journal.request_audit("undo")

        self.assertFalse(
            self.journal.complete_audit(
                first,
                clear_meta_keys=("force_canonical_hydration",),
            )
        )
        self.assertEqual(self.journal.audit_request().sequence, second)
        self.assertEqual(self.journal.audit_request().reason, "undo")
        self.assertEqual(
            self.journal.get_meta("force_canonical_hydration"),
            "1",
        )
        self.assertTrue(
            self.journal.complete_audit(
                second,
                clear_meta_keys=("force_canonical_hydration",),
            )
        )
        self.assertFalse(self.journal.audit_needed)
        self.assertIsNone(self.journal.get_meta("force_canonical_hydration"))

    def test_audit_and_custom_metadata_persist(self) -> None:
        sequence = self.journal.request_audit("import")
        self.journal.set_meta("last_collection_id", "abc")
        self.journal.close()
        self.journal = MaintenanceJournal(self.path)

        self.assertEqual(self.journal.audit_request().sequence, sequence)
        self.assertEqual(self.journal.audit_request().reason, "import")
        self.assertEqual(self.journal.get_meta("last_collection_id"), "abc")
        self.assertTrue(self.journal.delete_meta("last_collection_id"))
        self.assertEqual(self.journal.get_meta("last_collection_id", "missing"), "missing")

    def test_manifest_diff_finds_add_change_delete_without_mutating_saved_rows(self) -> None:
        self.assertEqual(
            self.journal.replace_manifest(
                [manifest(1), manifest(2), manifest(3)]
            ),
            3,
        )
        current = (
            row
            for row in (
                manifest(1),
                manifest(2, tags_length=99),
                manifest(4),
            )
        )

        difference = self.journal.diff_manifest(current)

        self.assertEqual(difference.added, (4,))
        self.assertEqual(difference.changed, (2,))
        self.assertEqual(difference.deleted, (3,))
        self.assertEqual(difference.unchanged, 1)
        self.assertEqual(difference.candidate_note_ids, (2, 4))
        self.assertEqual(
            tuple(row.note_id for row in self.journal.manifest_rows()),
            (1, 2, 3),
        )

    def test_cancelled_manifest_diffs_roll_back_and_clear_sqlite_interrupts(
        self,
    ) -> None:
        self.journal.replace_manifest([manifest(1)])
        self.journal.replace_card_manifest([card_manifest(101)])
        self.journal.replace_facet_manifest(
            [facet_manifest("deck", 10, "Medicine")]
        )

        operations = (
            (
                lambda cancel: self.journal.diff_manifest(
                    [manifest(1)], cancel_check=cancel
                ),
                lambda: self.journal.diff_manifest([manifest(1)]).unchanged,
            ),
            (
                lambda cancel: self.journal.diff_card_manifest(
                    [card_manifest(101)], cancel_check=cancel
                ),
                lambda: self.journal.diff_card_manifest(
                    [card_manifest(101)]
                ).unchanged,
            ),
            (
                lambda cancel: self.journal.diff_facet_manifest(
                    [facet_manifest("deck", 10, "Medicine")],
                    cancel_check=cancel,
                ),
                lambda: self.journal.diff_facet_manifest(
                    [facet_manifest("deck", 10, "Medicine")]
                ).unchanged,
            ),
        )

        for cancelled_diff, successful_diff in operations:
            checkpoints = 0

            def cancel() -> None:
                nonlocal checkpoints
                checkpoints += 1
                if checkpoints == 2:
                    raise RuntimeError("review started")

            with self.subTest(operation=cancelled_diff):
                with self.assertRaisesRegex(RuntimeError, "review started"):
                    cancelled_diff(cancel)
                # A successful second call proves both the temp-table write
                # rolled back and SQLite's progress handler was removed.
                self.assertEqual(successful_diff(), 1)

    def test_every_manifest_fingerprint_column_detects_a_change(self) -> None:
        original = manifest(1)
        self.journal.replace_manifest([original])
        variants = (
            manifest(1, modified=11),
            manifest(1, usn=-1),
            manifest(1, note_type=101),
            manifest(1, fields_length=21),
            manifest(1, tags_length=6),
            manifest(1, checksum=31),
            manifest(1, fields_digest=b"changed fields"),
            manifest(1, tags_digest=b"changed tags"),
        )
        for variant in variants:
            with self.subTest(variant=variant):
                difference = self.journal.diff_manifest([variant])
                self.assertEqual(difference.changed, (1,))

    def test_manifest_can_be_incrementally_updated_and_deleted(self) -> None:
        self.journal.replace_manifest([manifest(1), manifest(2)])
        self.assertEqual(
            self.journal.upsert_manifest(
                [manifest(2, modified=20), manifest(3)]
            ),
            2,
        )
        self.assertEqual(self.journal.delete_manifest([1, 9]), 1)
        self.assertEqual(
            self.journal.manifest_rows(),
            (manifest(2, modified=20), manifest(3)),
        )

    def test_failed_duplicate_manifest_input_is_atomic(self) -> None:
        self.journal.replace_manifest([manifest(1)])
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.replace_manifest([manifest(2), manifest(2)])
        self.assertEqual(self.journal.manifest_rows(), (manifest(1),))

    def test_card_manifest_diff_reports_old_and_new_note_owners(self) -> None:
        self.journal.replace_card_manifest(
            [
                card_manifest(101, note_id=1, home_deck_id=10),
                card_manifest(102, note_id=2, home_deck_id=20),
                card_manifest(103, note_id=3, home_deck_id=30),
            ]
        )

        difference = self.journal.diff_card_manifest(
            [
                # A home-deck change affects the existing owner.
                card_manifest(101, note_id=1, home_deck_id=11),
                # Defensively include both owners if card ownership changes.
                card_manifest(102, note_id=4, home_deck_id=20),
                card_manifest(104, note_id=5, home_deck_id=40),
            ]
        )

        self.assertEqual(difference.added, (104,))
        self.assertEqual(difference.changed, (101, 102))
        self.assertEqual(difference.deleted, (103,))
        self.assertEqual(difference.added_note_ids, (5,))
        self.assertEqual(difference.changed_note_ids, (1, 2, 4))
        self.assertEqual(difference.deleted_note_ids, (3,))
        self.assertEqual(difference.affected_note_ids, (1, 2, 3, 4, 5))
        self.assertEqual(difference.unchanged, 0)
        self.assertEqual(
            self.journal.card_manifest_rows(),
            (
                card_manifest(101, note_id=1, home_deck_id=10),
                card_manifest(102, note_id=2, home_deck_id=20),
                card_manifest(103, note_id=3, home_deck_id=30),
            ),
        )

    def test_card_manifest_can_be_incrementally_updated_and_deleted(self) -> None:
        self.assertEqual(
            self.journal.replace_card_manifest(
                [card_manifest(101), card_manifest(102, note_id=2)]
            ),
            2,
        )
        self.assertEqual(
            self.journal.upsert_card_manifest(
                [
                    card_manifest(102, note_id=2, home_deck_id=99),
                    card_manifest(103, note_id=3),
                ]
            ),
            2,
        )
        self.assertEqual(self.journal.delete_card_manifest([101, 999]), 1)
        self.assertEqual(self.journal.card_manifest_count(), 2)
        self.assertEqual(
            self.journal.card_manifest_rows(),
            (
                card_manifest(102, note_id=2, home_deck_id=99),
                card_manifest(103, note_id=3),
            ),
        )

    def test_failed_duplicate_card_manifest_input_is_atomic(self) -> None:
        self.journal.replace_card_manifest([card_manifest(101)])
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.replace_card_manifest(
                [card_manifest(102), card_manifest(102, note_id=2)]
            )
        self.assertEqual(
            self.journal.card_manifest_rows(), (card_manifest(101),)
        )

    def test_facet_manifest_diff_tracks_deck_and_notetype_signatures(self) -> None:
        self.journal.replace_facet_manifest(
            [
                facet_manifest("deck", 10, "Internal Medicine"),
                facet_manifest("notetype", 20, "Basic|Front|Back"),
                facet_manifest("deck", 30, "Deleted deck"),
            ]
        )

        difference = self.journal.diff_facet_manifest(
            [
                facet_manifest("DECK", 10, "Medicine"),
                facet_manifest("notetype", 20, "Basic|Front|Back"),
                facet_manifest("deck", 40, "New deck"),
            ]
        )

        self.assertEqual(
            difference.added, (FacetManifestKey("deck", 40),)
        )
        self.assertEqual(
            difference.changed, (FacetManifestKey("deck", 10),)
        )
        self.assertEqual(
            difference.deleted, (FacetManifestKey("deck", 30),)
        )
        self.assertEqual(difference.unchanged, 1)
        self.assertEqual(
            self.journal.facet_manifest_rows(facet="DECK"),
            (
                facet_manifest("deck", 10, "Internal Medicine"),
                facet_manifest("deck", 30, "Deleted deck"),
            ),
        )

    def test_facet_manifest_can_be_incrementally_updated_and_deleted(self) -> None:
        self.assertEqual(
            self.journal.replace_facet_manifest(
                [facet_manifest("deck", 10, "A")]
            ),
            1,
        )
        self.assertEqual(
            self.journal.upsert_facet_manifest(
                [
                    facet_manifest("DECK", 10, "B"),
                    facet_manifest("notetype", 20, "C"),
                ]
            ),
            2,
        )
        self.assertEqual(
            self.journal.delete_facet_manifest(
                [FacetManifestKey("deck", 10), FacetManifestKey("deck", 99)]
            ),
            1,
        )
        self.assertEqual(self.journal.facet_manifest_count(), 1)
        self.assertEqual(self.journal.facet_manifest_count(facet="deck"), 0)
        self.assertEqual(
            self.journal.facet_manifest_rows(),
            (facet_manifest("notetype", 20, "C"),),
        )

    def test_schema_one_journal_migrates_in_place_without_losing_state(self) -> None:
        self.journal.close()
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            DROP TABLE IF EXISTS journal_meta;
            DROP TABLE IF EXISTS dirty_notes;
            DROP TABLE IF EXISTS note_manifest;
            DROP TABLE IF EXISTS card_manifest;
            DROP TABLE IF EXISTS facet_manifest;
            CREATE TABLE journal_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE dirty_notes (
                note_id INTEGER PRIMARY KEY,
                reasons INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                queued_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE note_manifest (
                note_id INTEGER PRIMARY KEY,
                modified_seconds INTEGER NOT NULL,
                usn INTEGER NOT NULL,
                note_type_id INTEGER NOT NULL,
                fields_length INTEGER NOT NULL,
                tags_length INTEGER NOT NULL,
                field_checksum INTEGER NOT NULL
            );
            INSERT INTO journal_meta(key, value) VALUES
                ('schema_version', '1'),
                ('next_sequence', '7'),
                ('audit_needed', '0');
            INSERT INTO dirty_notes(
                note_id, reasons, sequence, queued_at, attempts
            ) VALUES (9, 1, 7, 123.0, 0);
            INSERT INTO note_manifest(
                note_id, modified_seconds, usn, note_type_id,
                fields_length, tags_length, field_checksum
            ) VALUES (9, 10, 0, 100, 20, 5, 30);
            """
        )
        connection.close()

        self.journal = MaintenanceJournal(self.path)

        self.assertEqual(
            self.journal.get_meta("schema_version"), str(SCHEMA_VERSION)
        )
        self.assertEqual(self.journal.pending()[0].note_id, 9)
        self.assertEqual(self.journal.manifest_rows(), (manifest(9),))
        self.assertEqual(
            self.journal.replace_card_manifest([card_manifest(101)]), 1
        )
        self.assertEqual(
            self.journal.replace_facet_manifest(
                [facet_manifest("deck", 10, "A")]
            ),
            1,
        )

    def test_schema_three_digest_upgrade_invalidates_old_manifest_seed(self) -> None:
        self.journal.close()
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            UPDATE journal_meta SET value = '3' WHERE key = 'schema_version';
            INSERT OR REPLACE INTO journal_meta(key, value)
                VALUES ('manifest_initialized', '1');
            ALTER TABLE note_manifest RENAME TO note_manifest_v4;
            CREATE TABLE note_manifest (
                note_id INTEGER PRIMARY KEY,
                modified_seconds INTEGER NOT NULL,
                usn INTEGER NOT NULL,
                note_type_id INTEGER NOT NULL,
                fields_length INTEGER NOT NULL,
                tags_length INTEGER NOT NULL,
                field_checksum INTEGER NOT NULL,
                fields_digest BLOB
            );
            INSERT INTO note_manifest(
                note_id, modified_seconds, usn, note_type_id,
                fields_length, tags_length, field_checksum, fields_digest
            ) VALUES (9, 10, 0, 100, 20, 5, 30, X'0102');
            DROP TABLE note_manifest_v4;
            """
        )
        connection.close()

        self.journal = MaintenanceJournal(self.path)

        self.assertEqual(
            self.journal.get_meta("schema_version"),
            str(SCHEMA_VERSION),
        )
        self.assertEqual(
            self.journal.get_meta("manifest_initialized"),
            "0",
        )
        [row] = self.journal.manifest_rows()
        self.assertEqual(row.fields_digest, b"\x01\x02")
        self.assertEqual(row.tags_digest, b"")

    def test_explicit_close_is_idempotent(self) -> None:
        self.journal.close()
        self.journal.close()
        self.assertTrue(self.journal.closed)
        with self.assertRaises(RuntimeError):
            self.journal.pending()

    def test_refuses_an_anki_collection_database_path(self) -> None:
        with self.assertRaises(UnsafeMaintenancePath):
            MaintenanceJournal(
                Path(self.temporary.name) / "collection.anki2"
            )


if __name__ == "__main__":
    unittest.main()
