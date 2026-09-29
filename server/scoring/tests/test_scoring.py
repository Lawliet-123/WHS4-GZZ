from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from server.scoring.storage import ScoringStore


def event(*, timestamp_ms=1000, raw_score=3, module="noclip"):
    return {
        "session_id": "session_1",
        "player_id": "player_1",
        "module": module,
        "timestamp_ms": timestamp_ms,
        "evidence": {"sample": timestamp_ms},
        "reasons": ["test"] if raw_score else [],
        "raw_score": raw_score,
    }


def event_id() -> str:
    return str(uuid.uuid4())


class ScoringStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ScoringStore(Path(self.tmp.name) / "scoring.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_event_updates_current_module_state(self):
        key = event_id()
        receipt = self.store.process_event(event(), event_id=key, sequence=1)

        self.assertEqual(receipt.status, "processed")
        self.assertTrue(receipt.state_updated)
        state = self.store.get_module_state("session_1", "player_1", "noclip")
        self.assertIsNotNone(state)
        self.assertEqual(state.event_id, key)
        self.assertEqual(state.raw_score, 3)

    def test_retry_with_same_event_id_is_idempotent(self):
        key = event_id()
        self.store.process_event(event(), event_id=key, sequence=1)
        duplicate = self.store.process_event(event(), event_id=key, sequence=1)

        self.assertEqual(duplicate.status, "duplicate")
        self.assertFalse(duplicate.state_updated)
        state = self.store.get_module_state("session_1", "player_1", "noclip")
        self.assertEqual(state.sequence, 1)

    def test_same_event_id_with_different_input_fails_closed(self):
        key = event_id()
        self.store.process_event(event(raw_score=1), event_id=key, sequence=1)

        with self.assertRaises(RuntimeError):
            self.store.process_event(event(raw_score=3), event_id=key, sequence=1)

    def test_repeated_new_samples_replace_score_instead_of_accumulating(self):
        self.store.process_event(event(timestamp_ms=1000, raw_score=3), event_id=event_id(), sequence=1)
        self.store.process_event(event(timestamp_ms=2000, raw_score=3), event_id=event_id(), sequence=2)

        state = self.store.get_module_state("session_1", "player_1", "noclip")
        self.assertEqual(state.raw_score, 3)
        self.assertEqual(state.timestamp_ms, 2000)
        self.assertEqual(state.sequence, 2)

    def test_late_older_sample_does_not_overwrite_newer_state(self):
        newest = event_id()
        self.store.process_event(event(timestamp_ms=5000, raw_score=3), event_id=newest, sequence=1)
        late = self.store.process_event(event(timestamp_ms=1000, raw_score=0), event_id=event_id(), sequence=2)

        self.assertFalse(late.state_updated)
        state = self.store.get_module_state("session_1", "player_1", "noclip")
        self.assertEqual(state.event_id, newest)
        self.assertEqual(state.timestamp_ms, 5000)
        self.assertEqual(state.raw_score, 3)

    def test_modules_keep_independent_current_state(self):
        self.store.process_event(event(module="noclip", raw_score=3), event_id=event_id(), sequence=1)
        self.store.process_event(event(module="aimbot", raw_score=7), event_id=event_id(), sequence=2)

        snapshot = self.store.get_player_snapshot("session_1", "player_1")
        self.assertEqual([state.module for state in snapshot], ["aimbot", "noclip"])
        self.assertEqual([state.raw_score for state in snapshot], [7, 3])

    def test_live_processing_does_not_advance_recovery_cursor(self):
        self.store.process_event(event(), event_id=event_id(), sequence=7)
        self.assertEqual(self.store.get_recovery_cursor(), 0)

        self.store.advance_recovery_cursor(7)
        self.assertEqual(self.store.get_recovery_cursor(), 7)

    def test_recovery_cursor_cannot_advance_to_unprocessed_record(self):
        with self.assertRaises(RuntimeError):
            self.store.advance_recovery_cursor(1)

    def test_replay_pattern_is_safe_after_process_before_cursor_crash(self):
        key = event_id()
        self.store.process_event(event(), event_id=key, sequence=1)
        self.assertEqual(self.store.get_recovery_cursor(), 0)

        duplicate = self.store.process_event(event(), event_id=key, sequence=1)
        self.assertEqual(duplicate.status, "duplicate")
        self.store.advance_recovery_cursor(1)
        self.assertEqual(self.store.get_recovery_cursor(), 1)


if __name__ == "__main__":
    unittest.main()
