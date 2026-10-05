"""Receiver → Shared durable writer → Scoring 실제 HTTP 통합 테스트.

여기서는 개별 함수만 호출하지 않고 실제 /api/detection 요청이
Receiver 검증 → Shared 영구 저장 → Scoring SQLite까지 이어지는지 확인한다.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.scoring.main as scoring_main
from server.receiver import create_router
from server.receiver.router import bearer_token_verifier
from server.scoring.storage import ScoringStore
from shared.config import WriterConfig
from shared.storage import DetectionWriter


TOKEN = "test-e2e-token"


def uid() -> str:
    return str(uuid.uuid4())


def external_event(
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
            "change_type": "added",
        })

    return {
        "session_id": "e2e_session",
        "player_id": "player_1",
        "module": "external_access",
        "timestamp_ms": timestamp_ms,
        "evidence": evidence,
        "reasons": ["e2e"] if score else [],
        "raw_score": score,
    }


def whistle_rpc_event(
    *,
    window_id: int,
    sample_id: int = 1,
    score: int = 0,
    status: str = "NORMAL",
    timestamp_ms: int = 1000,
):
    return {
        "session_id": "e2e_session",
        "player_id": "player_1",
        "module": "whistle_rpc",
        "timestamp_ms": timestamp_ms,
        "evidence": {
            "window_id": window_id,
            "sample_id": sample_id,
            "status": status,
        },
        "reasons": ["cooldown_violation"] if score else [],
        "raw_score": score,
    }


def godmode_event(
    *,
    score: int = 2,
    timestamp_ms: int = 1000,
):
    return {
        "session_id": "e2e_session",
        "player_id": "player_1",
        "module": "godmode",
        "timestamp_ms": timestamp_ms,
        "evidence": {
            "invincible": True,
        },
        "reasons": [
            "godmode e2e incident",
        ] if score else [],
        "raw_score": score,
    }


def selfdefense_event(
    *,
    kind: str = "file_integrity",
    status: str = "ERROR",
    timestamp_ms: int = 1000,
):
    return {
        "session_id": "e2e_session",
        "player_id": "player_1",
        "module": "selfdefense",
        "timestamp_ms": timestamp_ms,
        "evidence": {
            "kind": kind,
            "status": status,
            "scan_complete": False,
        },
        "reasons": ["selfdefense operational error"],
        "raw_score": 0,
    }


class ReceiverScoringE2ETests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)

        # 실제 Shared 서버 writer.
        self.writer = DetectionWriter(
            WriterConfig(root=root / "detections")
        )

        # 실제 B Scoring SQLite.
        self.store = ScoringStore(
            root / "scoring.sqlite3"
        )

        # 테스트 동안 scoring_main.process()가 이 임시 DB를 사용한다.
        self.previous_store = scoring_main._store
        scoring_main._store = self.store

        app = FastAPI()
        app.include_router(
            create_router(
                self.writer.write_detection,
                scoring_main.process,
                verify_token=bearer_token_verifier(TOKEN),
            )
        )

        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        scoring_main._store = self.previous_store
        self.tmp.cleanup()

    def post(self, event, event_id):
        return self.client.post(
            "/api/detection",
            json=event,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Idempotency-Key": event_id,
                "X-GZZ-Protocol-Version": "1",
            },
        )

    def test_selfdefense_is_stored_but_excluded_from_cheat_scoring(self):
        """Operational errors remain in Shared without changing the cheat verdict."""
        event_id = uid()
        response = self.post(selfdefense_event(), event_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "stored")

        stored = self.writer.iter_stored()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].event_id, event_id)
        self.assertEqual(stored[0].result["evidence"]["status"], "ERROR")

        self.assertEqual(
            [state.module for state in self.store.get_player_snapshot("e2e_session", "player_1")],
            ["selfdefense"],
        )
        verdict = scoring_main.get_player_final_verdict(
            "e2e_session",
            "player_1",
        )
        self.assertEqual(verdict.status, "NO_ACTIVE_EVIDENCE")

    def test_selfdefense_recovery_advances_without_creating_cheat_state(self):
        """Restart recovery consumes operational events without scoring them."""
        event_id = uid()
        receipt = self.writer.write_detection(
            selfdefense_event(kind="debugger_presence", status="DETECTED"),
            event_id=event_id,
        )

        cursor = scoring_main.recover_from_writer(self.writer)

        self.assertEqual(cursor, receipt.sequence)
        self.assertEqual(self.store.get_recovery_cursor(), receipt.sequence)
        self.assertEqual(
            [state.module for state in self.store.get_player_snapshot("e2e_session", "player_1")],
            ["selfdefense"],
        )
        self.assertEqual(scoring_main.recover_from_writer(self.writer), cursor)

    def test_external_access_submodules_do_not_overwrite_through_receiver(self):
        first = self.post(
            external_event(
                "external_process",
                score=10,
                status="SUSPICIOUS",
                timestamp_ms=1000,
            ),
            uid(),
        )

        second = self.post(
            external_event(
                "module_integrity",
                score=0,
                status="NORMAL",
                timestamp_ms=2000,
            ),
            uid(),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["status"], "stored")
        self.assertEqual(second.json()["status"], "stored")

        process_state = self.store.get_scoped_module_state(
            "e2e_session",
            "player_1",
            "external_access",
            "external_process",
        )

        integrity_state = self.store.get_scoped_module_state(
            "e2e_session",
            "player_1",
            "external_access",
            "module_integrity",
        )

        self.assertIsNotNone(process_state)
        self.assertIsNotNone(integrity_state)

        self.assertEqual(process_state.raw_score, 10)
        self.assertEqual(integrity_state.raw_score, 0)

        aggregate = self.store.get_module_state(
            "e2e_session",
            "player_1",
            "external_access",
        )

        # 뒤에 온 NORMAL 0이 다른 submodule의 양수를 없애면 안 된다.
        self.assertEqual(aggregate.raw_score, 10)
        self.assertEqual(
            aggregate.evidence["status"],
            "SUSPICIOUS",
        )

        # Shared writer에도 두 원본 Event가 모두 남아 있어야 한다.
        self.assertEqual(
            len(self.writer.iter_stored()),
            2,
        )

    def test_whistle_positive_normal_and_error_windows_are_all_preserved(self):
        events = (
            whistle_rpc_event(
                window_id=10,
                score=40,
                status="SUSPICIOUS",
                timestamp_ms=1000,
            ),
            whistle_rpc_event(
                window_id=11,
                score=0,
                status="NORMAL",
                timestamp_ms=2000,
            ),
            whistle_rpc_event(
                window_id=12,
                score=0,
                status="ERROR",
                timestamp_ms=3000,
            ),
        )

        for event in events:
            response = self.post(event, uid())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["status"],
                "stored",
            )

        history = self.store.get_window_history(
            "e2e_session",
            "player_1",
        )

        self.assertEqual(len(history), 3)
        self.assertEqual(
            [item.window_id for item in history],
            [10, 11, 12],
        )
        self.assertEqual(
            [item.raw_score for item in history],
            [40, 0, 0],
        )
        self.assertEqual(
            [item.evidence["status"] for item in history],
            ["SUSPICIOUS", "NORMAL", "ERROR"],
        )

        # latest snapshot은 가장 최근 window이지만,
        # 과거 positive window는 history에 그대로 남아 있다.
        latest = self.store.get_module_state(
            "e2e_session",
            "player_1",
            "whistle_rpc",
        )

        self.assertEqual(latest.raw_score, 0)
        self.assertEqual(
            latest.evidence["status"],
            "ERROR",
        )

    def test_whistle_same_semantic_window_new_event_id_is_not_double_reflected(self):
        first_id = uid()
        second_id = uid()

        first = self.post(
            whistle_rpc_event(
                window_id=20,
                score=40,
                status="SUSPICIOUS",
                timestamp_ms=1000,
            ),
            first_id,
        )

        # 동일 의미 window지만 별도 event_id와 다른 생성 시각.
        second = self.post(
            whistle_rpc_event(
                window_id=20,
                score=40,
                status="SUSPICIOUS",
                timestamp_ms=9000,
            ),
            second_id,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        # event_id가 다르므로 Shared 입장에서는 둘 다 실제 저장된 요청이다.
        self.assertEqual(first.json()["status"], "stored")
        self.assertEqual(second.json()["status"], "stored")
        self.assertEqual(len(self.writer.iter_stored()), 2)

        # Scoring 의미 이력에는 같은 window가 한 번만 반영된다.
        history = self.store.get_window_history(
            "e2e_session",
            "player_1",
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].event_id, first_id)

        latest = self.store.get_module_state(
            "e2e_session",
            "player_1",
            "whistle_rpc",
        )

        self.assertEqual(latest.event_id, first_id)

        self.assertEqual(
            self.store.get_window_conflicts(
                "e2e_session",
                "player_1",
            ),
            [],
        )

    def test_whistle_conflicting_same_window_is_audited_without_overwrite(self):
        canonical_id = uid()
        conflict_id = uid()

        first = self.post(
            whistle_rpc_event(
                window_id=30,
                score=40,
                status="SUSPICIOUS",
                timestamp_ms=1000,
            ),
            canonical_id,
        )

        second = self.post(
            whistle_rpc_event(
                window_id=30,
                score=0,
                status="NORMAL",
                timestamp_ms=2000,
            ),
            conflict_id,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        # 두 네트워크 Event 자체는 모두 영구 저장된다.
        self.assertEqual(len(self.writer.iter_stored()), 2)

        history = self.store.get_window_history(
            "e2e_session",
            "player_1",
        )

        # 정상 의미 history는 최초 관측을 유지한다.
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0].event_id,
            canonical_id,
        )
        self.assertEqual(history[0].raw_score, 40)

        conflicts = self.store.get_window_conflicts(
            "e2e_session",
            "player_1",
        )

        # 상충하는 두 번째 Event는 버리지 않고 감사 기록에 남는다.
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            conflicts[0].canonical_event_id,
            canonical_id,
        )
        self.assertEqual(
            conflicts[0].conflict_event_id,
            conflict_id,
        )

        latest = self.store.get_module_state(
            "e2e_session",
            "player_1",
            "whistle_rpc",
        )

        # 모순된 두 번째 값으로 현재 상태를 덮어쓰지 않는다.
        self.assertEqual(latest.event_id, canonical_id)
        self.assertEqual(latest.raw_score, 40)

    def test_whistle_exact_event_retry_is_idempotent_end_to_end(self):
        event_id = uid()

        event = whistle_rpc_event(
            window_id=40,
            score=40,
            status="SUSPICIOUS",
        )

        first = self.post(event, event_id)
        retry = self.post(event, event_id)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.status_code, 200)

        self.assertEqual(
            first.json()["status"],
            "stored",
        )
        self.assertEqual(
            retry.json()["status"],
            "duplicate",
        )

        # Shared와 Scoring 양쪽 모두 한 의미 Event만 존재한다.
        self.assertEqual(
            len(self.writer.iter_stored()),
            1,
        )
        self.assertEqual(
            len(
                self.store.get_window_history(
                    "e2e_session",
                    "player_1",
                )
            ),
            1,
        )

    def test_whistle_scoring_failure_returns_503_and_retry_recovers(self):
        event_id = uid()
        event = whistle_rpc_event(
            window_id=50,
            score=40,
            status="SUSPICIOUS",
        )

        # Scoring history INSERT만 의도적으로 실패시킨다.
        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute(
                """CREATE TRIGGER reject_whistle_history
                   BEFORE INSERT ON whistle_window_history
                   BEGIN
                       SELECT RAISE(
                           FAIL,
                           'simulated whistle history failure'
                       );
                   END"""
            )

        failed = self.post(event, event_id)

        self.assertEqual(failed.status_code, 503)
        self.assertEqual(
            failed.json()["detail"],
            "scoring is unavailable",
        )

        # Receiver/Shared 영구 저장은 이미 완료된 상태다.
        stored = self.writer.iter_stored()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].event_id, event_id)

        # Scoring transaction은 전부 rollback되어 반쪽 상태가 없어야 한다.
        self.assertEqual(
            self.store.get_window_history(
                "e2e_session",
                "player_1",
            ),
            [],
        )
        self.assertIsNone(
            self.store.get_module_state(
                "e2e_session",
                "player_1",
                "whistle_rpc",
            )
        )

        with closing(sqlite3.connect(self.store.path)) as db:
            db.execute(
                "DROP TRIGGER reject_whistle_history"
            )

        # 같은 event_id 재전송: Shared는 duplicate지만 Scoring 누락분은 복구.
        retry = self.post(event, event_id)

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(
            retry.json()["status"],
            "duplicate",
        )

        history = self.store.get_window_history(
            "e2e_session",
            "player_1",
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].event_id, event_id)


    def test_receiver_shared_scoring_reaches_final_verdict(self):
        """HTTP 수신부터 B Final Verdict까지 실제 전체 경로를 검증한다."""

        first_id = uid()
        second_id = uid()

        first = self.post(
            godmode_event(
                score=2,
                timestamp_ms=1000,
            ),
            first_id,
        )

        second = self.post(
            godmode_event(
                score=3,
                timestamp_ms=2000,
            ),
            second_id,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        self.assertEqual(
            first.json()["status"],
            "stored",
        )
        self.assertEqual(
            second.json()["status"],
            "stored",
        )

        # A Shared 원본도 두 건 모두 보존되어야 한다.
        self.assertEqual(
            len(self.writer.iter_stored()),
            2,
        )

        # Godmode는 event_delta이므로 두 사건 모두 history에 남는다.
        history = self.store.get_event_delta_history(
            "e2e_session",
            "player_1",
        )

        self.assertEqual(len(history), 2)
        self.assertEqual(
            [item.raw_score for item in history],
            [2, 3],
        )

        # 최종 B pipeline:
        # policy -> calibration -> aggregate -> verdict
        verdict = scoring_main.get_player_final_verdict(
            "e2e_session",
            "player_1",
        )

        self.assertEqual(
            verdict.status,
            "SUSPICIOUS",
        )

        # 2 + 3 = 5로 합산하는 것이 아니라
        # Godmode라는 하나의 evidence unit으로 반영한다.
        self.assertEqual(
            verdict.evidence_unit_count,
            1,
        )

        self.assertEqual(
            verdict.active_module_count,
            1,
        )

        self.assertEqual(
            verdict.active_modules,
            ("godmode",),
        )

        self.assertIn(
            "CALIBRATED_ACTIVE_EVIDENCE",
            verdict.reason_codes,
        )

    def test_receiver_retry_does_not_duplicate_final_risk(self):
        """같은 HTTP Event 재전송이 Final Risk를 중복 증가시키지 않는다."""

        event_id = uid()

        event = godmode_event(
            score=2,
            timestamp_ms=1000,
        )

        first = self.post(
            event,
            event_id,
        )

        retry = self.post(
            event,
            event_id,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.status_code, 200)

        self.assertEqual(
            first.json()["status"],
            "stored",
        )

        self.assertEqual(
            retry.json()["status"],
            "duplicate",
        )

        history = self.store.get_event_delta_history(
            "e2e_session",
            "player_1",
        )

        self.assertEqual(
            len(history),
            1,
        )

        verdict = scoring_main.get_player_final_verdict(
            "e2e_session",
            "player_1",
        )

        self.assertEqual(
            verdict.status,
            "SUSPICIOUS",
        )

        self.assertEqual(
            verdict.evidence_unit_count,
            1,
        )


if __name__ == "__main__":
    unittest.main()
