"""B2b-1: Godmode 신규 사건 이력 보존과 B1 호환성 테스트.

이 테스트는 사건 이력을 보존하는지 검증한다. 최종 위험도 점수나 판정 공식의
타당성을 검사하는 것이 아니다.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
import tempfile
import unittest
import uuid
from pathlib import Path

import server.scoring.main as scoring_main
from server.scoring.storage import ScoringStore
from shared.config import WriterConfig
from shared.storage import DetectionWriter
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.receiver import create_router
from server.receiver.router import bearer_token_verifier


def sample(*, module="godmode", raw_score=2, timestamp_ms=1000, reason="Invincible 지속"):
    """Godmode 실제 공통 7필드와 같은 모양의 테스트 이벤트를 만든다."""
    return {
        "session_id": "session_a",
        "player_id": "player_a",
        "module": module,
        "timestamp_ms": timestamp_ms,
        "evidence": {"invincible": True},
        "reasons": [reason] if raw_score else [],
        "raw_score": raw_score,
    }


def key():
    return str(uuid.uuid4())


class DeltaHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "score.sqlite3"
        self.store = ScoringStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def history(self, **kwargs):
        return self.store.get_event_delta_history("session_a", "player_a", **kwargs)

    def test_distinct_godmode_events_kept_without_changing_latest_policy(self):
        """같은 점수가 두 번 와도 사건 두 건을 보존하고, B1 최근 점수는 누적하지 않는다."""
        first, second = key(), key()
        self.store.process_event(sample(), event_id=first, sequence=1)
        self.store.process_event(sample(timestamp_ms=2000, reason="Heal 없이 회복"),
                                 event_id=second, sequence=2)
        rows = self.history()
        self.assertEqual([r.event_id for r in rows], [first, second])
        self.assertEqual([r.raw_score for r in rows], [2, 2])
        self.assertEqual(rows[1].reasons, ["Heal 없이 회복"])
        self.assertEqual(self.store.get_module_state("session_a", "player_a", "godmode").raw_score, 2)

    def test_transport_retry_does_not_duplicate_incident(self):
        """Shared가 같은 event_id를 재전송해도 사건 이력이 한 건인지 확인한다."""
        event, event_id = sample(), key()
        self.store.process_event(event, event_id=event_id, sequence=1)
        again = self.store.process_event(event, event_id=event_id, sequence=1)
        self.assertEqual(again.status, "duplicate")
        self.assertFalse(again.state_updated)
        self.assertEqual(len(self.history()), 1)

    def test_same_reason_with_new_id_is_retained_but_not_called_independent(self):
        """새 ID지만 같은 실제 근거인 경우를 임의로 삭제하거나 독립 사건으로 판정하지 않는다."""
        self.store.process_event(sample(), event_id=key(), sequence=1)
        self.store.process_event(sample(timestamp_ms=2000), event_id=key(), sequence=2)
        self.assertEqual(len(self.history()), 2)
        self.assertEqual([r.reasons for r in self.history()], [["Invincible 지속"]] * 2)
        # 추후 사건 고유 ID가 합의되기 전까지 조회 API는 위험도 합산값을 제공하지 않는다.

    def test_older_event_is_kept_but_does_not_replace_newer_module_state(self):
        """게임 시간 역순 도착도 기록하되 B1 최신 상태는 늦은 과거 표본에 덮이지 않는다."""
        newest = key()
        self.store.process_event(sample(raw_score=4, timestamp_ms=5000),
                                 event_id=newest, sequence=1)
        old = self.store.process_event(sample(raw_score=2, timestamp_ms=1000),
                                       event_id=key(), sequence=2)
        self.assertFalse(old.state_updated)
        self.assertEqual([r.timestamp_ms for r in self.history()], [5000, 1000])
        self.assertEqual(self.store.get_module_state("session_a", "player_a", "godmode").event_id,
                         newest)

    def test_snapshot_modules_do_not_create_delta_incidents(self):
        """Noclip, 미등록/ESP 같은 다른 모듈은 임의로 사건형 이력에 넣지 않는다."""
        for sequence, module in enumerate(("noclip", "aimbot", "esp"), 1):
            self.store.process_event(sample(module=module), event_id=key(), sequence=sequence)
        self.assertEqual(self.history(), [])
        self.assertEqual(len(self.store.get_player_snapshot("session_a", "player_a")), 3)

    def test_history_survives_database_reopen(self):
        """프로세스가 재시작한 것처럼 새 저장소 객체로 다시 열어도 사건이 남아야 한다."""
        event_id = key()
        self.store.process_event(sample(), event_id=event_id, sequence=1)
        reopened = ScoringStore(self.path)
        self.assertEqual(reopened.get_event_delta_history("session_a", "player_a")[0].event_id,
                         event_id)

    def test_history_sequence_pagination_and_invalid_arguments(self):
        """반환 건수와 서버 순서 커서를 적용하고 잘못된 요청을 거부한다."""
        for sequence in range(1, 4):
            self.store.process_event(sample(timestamp_ms=sequence * 1000),
                                     event_id=key(), sequence=sequence)
        first = self.history(limit=2)
        self.assertEqual([r.sequence for r in first], [1, 2])
        self.assertEqual([r.sequence for r in self.history(after_sequence=2)], [3])
        with self.assertRaises(ValueError):
            self.history(module="noclip")
        with self.assertRaises(ValueError):
            self.history(limit=0)
        with self.assertRaises(ValueError):
            self.history(after_sequence=-1)

    def test_mismatched_duplicate_fails_and_history_is_unchanged(self):
        """같은 ID로 다른 본문이나 sequence가 오면 과거 기록을 오염시키지 않는다."""
        event_id = key()
        self.store.process_event(sample(), event_id=event_id, sequence=1)
        with self.assertRaises(RuntimeError):
            self.store.process_event(sample(raw_score=5), event_id=event_id, sequence=1)
        with self.assertRaises(RuntimeError):
            self.store.process_event(sample(), event_id=event_id, sequence=2)
        self.assertEqual([r.raw_score for r in self.history()], [2])

    def test_legacy_b1_db_is_extended_and_old_godmode_events_can_be_backfilled(self):
        """기존 B1 DB에 새 테이블만 추가하고, 이미 처리한 과거 이벤트를 복원한다."""
        writer = DetectionWriter(WriterConfig(root=Path(self.temp.name) / "durable"))
        god_a = writer.write_detection(sample(), event_id=key())
        writer.write_detection(sample(module="noclip"), event_id=key())
        god_b = writer.write_detection(sample(raw_score=4, timestamp_ms=2000, reason="다른 새 사건"),
                                       event_id=key())
        for record in writer.iter_stored(after_sequence=0):
            self.store.process_event(record.result, event_id=record.event_id,
                                     sequence=record.sequence)

        # 마이그레이션 전 B1 DB를 모사: 기존 처리 이력은 남고 B2 사건 테이블만 없는 상태.
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("DROP TABLE event_delta_history")
        ScoringStore(self.path)  # B2 시작 때 기존 DB에 새 사건 테이블을 추가한다.
        self.assertEqual(self.history(), [])

        previous = scoring_main._store
        try:
            scoring_main._store = self.store
            self.assertEqual(scoring_main.backfill_event_delta_history_from_writer(writer, batch_size=2), 3)
            self.assertEqual([r.event_id for r in self.history()], [god_a.event_id, god_b.event_id])
            # 재실행해도 B1 상태와 사건 건수는 동일하다.
            before = self.store.get_player_snapshot("session_a", "player_a")
            self.assertEqual(scoring_main.backfill_event_delta_history_from_writer(writer, batch_size=2), 3)
            self.assertEqual(len(self.history()), 2)
            self.assertEqual(self.store.get_player_snapshot("session_a", "player_a"), before)
        finally:
            scoring_main._store = previous

    def test_failed_history_write_rolls_back_b1_processing_too(self):
        """새 사건 이력 쓰기가 실패하면 processed_events와 latest_state도 저장하지 않는다."""
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""CREATE TRIGGER reject_incident BEFORE INSERT ON event_delta_history
                          BEGIN SELECT RAISE(FAIL, 'simulated disk failure'); END""")
        with self.assertRaises(RuntimeError):
            self.store.process_event(sample(), event_id=key(), sequence=1)
        self.assertEqual(self.history(), [])
        self.assertIsNone(self.store.get_module_state("session_a", "player_a", "godmode"))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0], 0)

    def test_real_shared_feed_recovers_lost_scoring(self):
        """A Shared에는 기록됐지만 B 호출 전에 종료된 경우 복구가 사건 이력도 생성한다."""
        writer = DetectionWriter(WriterConfig(root=Path(self.temp.name) / "receiver"))
        event_id = key()
        writer.write_detection(sample(), event_id=event_id)
        previous = scoring_main._store
        try:
            scoring_main._store = self.store
            self.assertEqual(scoring_main.recover_from_writer(writer), 1)
            self.assertEqual(self.history()[0].event_id, event_id)
            self.assertEqual(scoring_main.recover_from_writer(writer), 1)
            self.assertEqual(len(self.history()), 1)
            self.assertEqual(self.store.get_recovery_cursor(), 1)
        finally:
            scoring_main._store = previous


class ReceiverDeltaIntegrationTests(unittest.TestCase):
    """Receiver → Shared 영구 저장 → B Scoring을 실제 로컬 HTTP 요청으로 연결한다."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.writer = DetectionWriter(WriterConfig(root=root / "detections"))
        self.store = ScoringStore(root / "scoring.sqlite3")
        self.previous = scoring_main._store
        scoring_main._store = self.store
        app = FastAPI()
        app.include_router(create_router(
            self.writer.write_detection,
            scoring_main.process,
            verify_token=bearer_token_verifier("test-local-token"),
        ))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        scoring_main._store = self.previous
        self.temp.cleanup()

    def post(self, event, event_id):
        return self.client.post(
            "/api/detection", json=event,
            headers={
                "Authorization": "Bearer test-local-token",
                "Idempotency-Key": event_id,
                "X-GZZ-Protocol-Version": "1",
            },
        )

    def test_actual_receiver_retry_has_only_one_incident(self):
        """HTTP 동일 요청 재전송 시 A 저장과 B 사건 이력이 모두 한 건이다."""
        event_id = key()
        first = self.post(sample(), event_id)
        second = self.post(sample(), event_id)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "stored")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "duplicate")
        self.assertEqual(len(self.store.get_event_delta_history("session_a", "player_a")), 1)

    def test_scoring_failure_returns_503_then_same_request_is_recoverable(self):
        """A 저장 뒤 B 오류가 나면 503, 같은 ID로 재전송하여 누락 이력을 복구한다."""
        event_id = key()
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute("""CREATE TRIGGER reject_delta BEFORE INSERT ON event_delta_history
                          BEGIN SELECT RAISE(FAIL, 'simulated history write failure'); END""")
        failed = self.post(sample(), event_id)
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["detail"], "scoring is unavailable")
        self.assertEqual(self.writer.iter_stored()[0].event_id, event_id)
        self.assertEqual(self.store.get_event_delta_history("session_a", "player_a"), [])
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute("DROP TRIGGER reject_delta")
        retry = self.post(sample(), event_id)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["status"], "duplicate")
        self.assertEqual(len(self.store.get_event_delta_history("session_a", "player_a")), 1)


if __name__ == "__main__":
    unittest.main()
