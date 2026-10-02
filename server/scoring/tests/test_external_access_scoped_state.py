"""external_access 두 submodule의 독립 최신 상태와 파생 module 상태를 검증한다."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from server.scoring.storage import ScoringStore


def uid() -> str:
    return str(uuid.uuid4())


def event(
    submodule: str,
    *,
    score: int = 0,
    status: str = "NORMAL",
    timestamp_ms: int = 1000,
):
    evidence = {
        "submodule": submodule,
        "status": status,
    }

    if submodule == "external_process":
        evidence.update({
            "source_pid": 900,
            "target_pid": 500,
        })
    elif submodule == "module_integrity":
        evidence.update({
            "target_pid": 500,
            "module_path": "C:/Game/example.dll",
        })

    return {
        "session_id": "external_session",
        "player_id": "player_1",
        "module": "external_access",
        "timestamp_ms": timestamp_ms,
        "evidence": evidence,
        "reasons": ["test"] if score else [],
        "raw_score": score,
    }


class ExternalAccessScopedStateTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ScoringStore(Path(self.tmp.name) / "scoring.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_submodules_do_not_overwrite_each_other(self):
        self.store.process_event(
            event(
                "external_process",
                score=10,
                status="SUSPICIOUS",
                timestamp_ms=1000,
            ),
            event_id=uid(),
            sequence=1,
        )

        self.store.process_event(
            event(
                "module_integrity",
                score=0,
                status="NORMAL",
                timestamp_ms=2000,
            ),
            event_id=uid(),
            sequence=2,
        )

        process_state = self.store.get_scoped_module_state(
            "external_session",
            "player_1",
            "external_access",
            "external_process",
        )
        integrity_state = self.store.get_scoped_module_state(
            "external_session",
            "player_1",
            "external_access",
            "module_integrity",
        )

        self.assertIsNotNone(process_state)
        self.assertIsNotNone(integrity_state)

        self.assertEqual(process_state.raw_score, 10)
        self.assertEqual(integrity_state.raw_score, 0)

        # 더 늦게 온 module_integrity NORMAL 0이
        # external_process의 양수 상태를 없애면 안 된다.
        aggregate = self.store.get_module_state(
            "external_session",
            "player_1",
            "external_access",
        )

        self.assertEqual(aggregate.raw_score, 10)
        self.assertEqual(aggregate.evidence["submodule"], "aggregate")
        self.assertEqual(aggregate.evidence["status"], "SUSPICIOUS")

    def test_both_normal_zero_make_aggregate_normal(self):
        self.store.process_event(
            event("external_process", timestamp_ms=1000),
            event_id=uid(),
            sequence=1,
        )
        self.store.process_event(
            event("module_integrity", timestamp_ms=2000),
            event_id=uid(),
            sequence=2,
        )

        aggregate = self.store.get_module_state(
            "external_session",
            "player_1",
            "external_access",
        )

        self.assertEqual(aggregate.raw_score, 0)
        self.assertEqual(aggregate.evidence["status"], "NORMAL")
        self.assertTrue(aggregate.evidence["coverage_complete"])

    def test_one_error_is_not_full_normal(self):
        self.store.process_event(
            event("external_process", status="NORMAL", timestamp_ms=1000),
            event_id=uid(),
            sequence=1,
        )
        self.store.process_event(
            event("module_integrity", status="ERROR", timestamp_ms=2000),
            event_id=uid(),
            sequence=2,
        )

        aggregate = self.store.get_module_state(
            "external_session",
            "player_1",
            "external_access",
        )

        self.assertEqual(aggregate.raw_score, 0)
        self.assertEqual(aggregate.evidence["status"], "WARNING")
        self.assertFalse(aggregate.evidence["coverage_complete"])
        self.assertIn(
            "module_integrity",
            aggregate.evidence["unavailable_submodules"],
        )

    def test_missing_channel_is_partial_not_normal(self):
        self.store.process_event(
            event("external_process", status="NORMAL"),
            event_id=uid(),
            sequence=1,
        )

        aggregate = self.store.get_module_state(
            "external_session",
            "player_1",
            "external_access",
        )

        self.assertEqual(aggregate.evidence["status"], "WARNING")
        self.assertFalse(aggregate.evidence["coverage_complete"])
        self.assertIn(
            "module_integrity",
            aggregate.evidence["missing_submodules"],
        )

    def test_late_old_sample_does_not_replace_scoped_state(self):
        newest = uid()

        self.store.process_event(
            event(
                "external_process",
                score=10,
                status="SUSPICIOUS",
                timestamp_ms=5000,
            ),
            event_id=newest,
            sequence=1,
        )

        result = self.store.process_event(
            event(
                "external_process",
                score=0,
                status="NORMAL",
                timestamp_ms=1000,
            ),
            event_id=uid(),
            sequence=2,
        )

        scoped = self.store.get_scoped_module_state(
            "external_session",
            "player_1",
            "external_access",
            "external_process",
        )

        self.assertEqual(scoped.event_id, newest)
        self.assertEqual(scoped.raw_score, 10)

        aggregate = self.store.get_module_state(
            "external_session",
            "player_1",
            "external_access",
        )
        self.assertEqual(aggregate.raw_score, 10)

    def test_other_modules_keep_existing_latest_state_behavior(self):
        normal_event = {
            "session_id": "external_session",
            "player_id": "player_1",
            "module": "noclip",
            "timestamp_ms": 1000,
            "evidence": {},
            "reasons": ["test"],
            "raw_score": 3,
        }

        self.store.process_event(
            normal_event,
            event_id=uid(),
            sequence=1,
        )

        state = self.store.get_module_state(
            "external_session",
            "player_1",
            "noclip",
        )

        self.assertEqual(state.raw_score, 3)


if __name__ == "__main__":
    unittest.main()
