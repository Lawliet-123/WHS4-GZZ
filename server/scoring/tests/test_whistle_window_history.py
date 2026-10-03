"""whistle_rpc의 window 단위 이력·의미 중복 방지를 검증한다."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from server.scoring.storage import ScoringStore


def uid() -> str:
    return str(uuid.uuid4())


def rpc_event(
    *,
    window_id=0,
    sample_id=1,
    score=0,
    status="NORMAL",
    timestamp_ms=1000,
    session_id="rpc_session",
    player_id="player_1",
):
    evidence = {
        "status": status,
    }

    if window_id is not ...:
        evidence["window_id"] = window_id

    if sample_id is not ...:
        evidence["sample_id"] = sample_id

    return {
        "session_id": session_id,
        "player_id": player_id,
        "module": "whistle_rpc",
        "timestamp_ms": timestamp_ms,
        "evidence": evidence,
        "reasons": ["cooldown_violation"] if score else [],
        "raw_score": score,
    }


class WhistleWindowHistoryTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ScoringStore(Path(self.tmp.name) / "scoring.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_positive_then_normal_keeps_both_windows(self):
        self.store.process_event(
            rpc_event(
                window_id=10,
                score=40,
                status="SUSPICIOUS",
                timestamp_ms=1000,
            ),
            event_id=uid(),
            sequence=1,
        )

        self.store.process_event(
            rpc_event(
                window_id=11,
                score=0,
                status="NORMAL",
                timestamp_ms=2000,
            ),
            event_id=uid(),
            sequence=2,
        )

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(len(history), 2)

        self.assertEqual(history[0].window_id, 10)
        self.assertEqual(history[0].raw_score, 40)

        self.assertEqual(history[1].window_id, 11)
        self.assertEqual(history[1].raw_score, 0)

        # 최신 snapshot은 NORMAL 0이어도 과거 양수 window history는 남는다.
        latest = self.store.get_module_state(
            "rpc_session",
            "player_1",
            "whistle_rpc",
        )

        self.assertEqual(latest.raw_score, 0)
        self.assertEqual(latest.evidence["status"], "NORMAL")

    def test_error_and_offline_windows_are_preserved(self):
        self.store.process_event(
            rpc_event(
                window_id=20,
                status="ERROR",
                timestamp_ms=1000,
            ),
            event_id=uid(),
            sequence=1,
        )

        self.store.process_event(
            rpc_event(
                window_id=21,
                status="OFFLINE",
                timestamp_ms=2000,
            ),
            event_id=uid(),
            sequence=2,
        )

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(
            [item.evidence["status"] for item in history],
            ["ERROR", "OFFLINE"],
        )

    def test_same_event_id_retry_is_stored_once(self):
        event = rpc_event(
            window_id=30,
            score=40,
            status="SUSPICIOUS",
        )
        key = uid()

        first = self.store.process_event(
            event,
            event_id=key,
            sequence=1,
        )

        duplicate = self.store.process_event(
            event,
            event_id=key,
            sequence=1,
        )

        self.assertEqual(first.status, "processed")
        self.assertEqual(duplicate.status, "duplicate")

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].event_id, key)

    def test_same_semantic_window_with_new_event_id_is_not_reflected_twice(self):
        event = rpc_event(
            window_id=40,
            sample_id=1,
            score=40,
            status="SUSPICIOUS",
        )

        first_id = uid()

        self.store.process_event(
            event,
            event_id=first_id,
            sequence=1,
        )

        duplicate = self.store.process_event(
            event,
            event_id=uid(),
            sequence=2,
        )

        self.assertFalse(duplicate.state_updated)

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].event_id, first_id)

        latest = self.store.get_module_state(
            "rpc_session",
            "player_1",
            "whistle_rpc",
        )

        self.assertEqual(latest.event_id, first_id)
        self.assertEqual(latest.sequence, 1)

    def test_same_semantic_window_with_different_body_is_audited_without_overwrite(self):
        canonical_id = uid()

        self.store.process_event(
            rpc_event(
                window_id=50,
                sample_id=1,
                score=40,
                status="SUSPICIOUS",
            ),
            event_id=canonical_id,
            sequence=1,
        )

        conflict_id = uid()

        receipt = self.store.process_event(
            rpc_event(
                window_id=50,
                sample_id=1,
                score=0,
                status="NORMAL",
                timestamp_ms=2000,
            ),
            event_id=conflict_id,
            sequence=2,
        )

        self.assertEqual(receipt.status, "processed")
        self.assertFalse(receipt.state_updated)

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        # 최초 canonical 관측만 정상 window history로 유지한다.
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].event_id, canonical_id)
        self.assertEqual(history[0].raw_score, 40)

        latest = self.store.get_module_state(
            "rpc_session",
            "player_1",
            "whistle_rpc",
        )

        self.assertEqual(latest.event_id, canonical_id)
        self.assertEqual(latest.raw_score, 40)

        conflicts = self.store.get_window_conflicts(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            conflicts[0].canonical_event_id,
            canonical_id,
        )
        self.assertEqual(
            conflicts[0].conflict_event_id,
            conflict_id,
        )
        self.assertEqual(conflicts[0].raw_score, 0)


    def test_same_semantic_window_with_only_different_timestamp_is_duplicate(self):
        first_id = uid()

        self.store.process_event(
            rpc_event(
                window_id=55,
                sample_id=1,
                score=40,
                status="SUSPICIOUS",
                timestamp_ms=1000,
            ),
            event_id=first_id,
            sequence=1,
        )

        receipt = self.store.process_event(
            rpc_event(
                window_id=55,
                sample_id=1,
                score=40,
                status="SUSPICIOUS",
                timestamp_ms=9999,
            ),
            event_id=uid(),
            sequence=2,
        )

        self.assertFalse(receipt.state_updated)

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )
        conflicts = self.store.get_window_conflicts(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].event_id, first_id)
        self.assertEqual(conflicts, [])

    def test_semantic_key_is_scoped_by_session(self):
        self.store.process_event(
            rpc_event(
                session_id="session_a",
                window_id=0,
            ),
            event_id=uid(),
            sequence=1,
        )

        self.store.process_event(
            rpc_event(
                session_id="session_b",
                window_id=0,
            ),
            event_id=uid(),
            sequence=2,
        )

        self.assertEqual(
            len(self.store.get_window_history("session_a", "player_1")),
            1,
        )
        self.assertEqual(
            len(self.store.get_window_history("session_b", "player_1")),
            1,
        )

    def test_missing_window_identity_is_preserved_but_not_semantically_deduplicated(self):
        self.store.process_event(
            rpc_event(
                window_id=...,
                sample_id=1,
                score=40,
                status="SUSPICIOUS",
            ),
            event_id=uid(),
            sequence=1,
        )

        self.store.process_event(
            rpc_event(
                window_id=...,
                sample_id=1,
                score=40,
                status="SUSPICIOUS",
            ),
            event_id=uid(),
            sequence=2,
        )

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(len(history), 2)
        self.assertFalse(history[0].semantic_key_valid)
        self.assertFalse(history[1].semantic_key_valid)
        self.assertIsNone(history[0].window_id)

    def test_invalid_window_identity_is_not_used_as_semantic_key(self):
        self.store.process_event(
            rpc_event(
                window_id="10",
                sample_id=1,
            ),
            event_id=uid(),
            sequence=1,
        )

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(len(history), 1)
        self.assertFalse(history[0].semantic_key_valid)
        self.assertIsNone(history[0].window_id)

    def test_wrong_sample_id_is_not_used_as_semantic_key(self):
        self.store.process_event(
            rpc_event(
                window_id=70,
                sample_id=0,
            ),
            event_id=uid(),
            sequence=1,
        )

        self.store.process_event(
            rpc_event(
                window_id=70,
                sample_id=0,
            ),
            event_id=uid(),
            sequence=2,
        )

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        # whistle_rpc의 현재 sender 계약은 sample_id=1이다.
        # 잘못된 식별자는 의미 중복 키로 신뢰하지 않고 두 원본을 모두 보존한다.
        self.assertEqual(len(history), 2)
        self.assertFalse(history[0].semantic_key_valid)
        self.assertFalse(history[1].semantic_key_valid)
        self.assertIsNone(history[0].sample_id)

    def test_history_query_uses_server_sequence_pagination(self):
        for seq, window in enumerate((60, 61, 62), start=1):
            self.store.process_event(
                rpc_event(
                    window_id=window,
                    timestamp_ms=1000 + seq,
                ),
                event_id=uid(),
                sequence=seq,
            )

        page = self.store.get_window_history(
            "rpc_session",
            "player_1",
            after_sequence=1,
            limit=1,
        )

        self.assertEqual(len(page), 1)
        self.assertEqual(page[0].sequence, 2)
        self.assertEqual(page[0].window_id, 61)

    def test_static_whistle_is_not_written_to_rpc_window_history(self):
        event = {
            "session_id": "rpc_session",
            "player_id": "player_1",
            "module": "whistle",
            "timestamp_ms": 1000,
            "evidence": {
                "status": "NORMAL",
                "window_id": 1,
                "sample_id": 0,
            },
            "reasons": [],
            "raw_score": 0,
        }

        self.store.process_event(
            event,
            event_id=uid(),
            sequence=1,
        )

        history = self.store.get_window_history(
            "rpc_session",
            "player_1",
        )

        self.assertEqual(history, [])


if __name__ == "__main__":
    unittest.main()
