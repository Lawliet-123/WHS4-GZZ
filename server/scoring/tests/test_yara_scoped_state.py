"""localguard_yara의 PID+scope별 최신 상태 보존을 검증한다."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from server.scoring.storage import ScoringStore


def uid() -> str:
    return str(uuid.uuid4())


def yara_event(
    pid: int,
    *,
    score: int,
    timestamp_ms: int,
    scope: str = "selected_local_process_memory",
):
    return {
        "session_id": "yara_session",
        "player_id": "player_1",
        "module": "localguard_yara",
        "timestamp_ms": timestamp_ms,
        "evidence": {
            "pid": pid,
            "scope": scope,
            "measurement_valid": True,
            "status": "SUSPICIOUS" if score > 0 else "NORMAL",
            "matched_rules": ["rule_a"] if score > 0 else [],
            "test_rule_match": False,
        },
        "reasons": ["yara_rule_match"] if score > 0 else [],
        "raw_score": score,
    }


class YaraScopedStateTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ScoringStore(
            Path(self.tmp.name) / "scoring.sqlite3"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_other_pid_zero_does_not_clear_positive_entity(self):
        self.store.process_event(
            yara_event(900, score=3, timestamp_ms=1000),
            event_id=uid(),
            sequence=1,
        )

        self.store.process_event(
            yara_event(901, score=0, timestamp_ms=2000),
            event_id=uid(),
            sequence=2,
        )

        positive = self.store.get_scoped_module_state(
            "yara_session",
            "player_1",
            "localguard_yara",
            "yara_pid:900:selected_local_process_memory",
        )
        normal = self.store.get_scoped_module_state(
            "yara_session",
            "player_1",
            "localguard_yara",
            "yara_pid:901:selected_local_process_memory",
        )

        self.assertIsNotNone(positive)
        self.assertIsNotNone(normal)

        self.assertEqual(positive.raw_score, 3)
        self.assertEqual(normal.raw_score, 0)

        # PID 901의 NORMAL 0이 PID 900의 양수 상태를
        # module-level 대표 상태에서 지우면 안 된다.
        aggregate = self.store.get_module_state(
            "yara_session",
            "player_1",
            "localguard_yara",
        )

        self.assertIsNotNone(aggregate)
        self.assertEqual(aggregate.raw_score, 3)

    def test_zero_clears_only_the_same_pid_and_scope(self):
        self.store.process_event(
            yara_event(900, score=3, timestamp_ms=1000),
            event_id=uid(),
            sequence=1,
        )

        self.store.process_event(
            yara_event(901, score=0, timestamp_ms=1500),
            event_id=uid(),
            sequence=2,
        )

        self.store.process_event(
            yara_event(900, score=0, timestamp_ms=2000),
            event_id=uid(),
            sequence=3,
        )

        pid900 = self.store.get_scoped_module_state(
            "yara_session",
            "player_1",
            "localguard_yara",
            "yara_pid:900:selected_local_process_memory",
        )

        self.assertIsNotNone(pid900)
        self.assertEqual(pid900.raw_score, 0)

        aggregate = self.store.get_module_state(
            "yara_session",
            "player_1",
            "localguard_yara",
        )

        self.assertIsNotNone(aggregate)
        self.assertEqual(aggregate.raw_score, 0)


if __name__ == "__main__":
    unittest.main()
