import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from client.detectors.noclip import main as noclip
from shared.schema import decode_event, encode_event


class NoclipShared02Tests(unittest.TestCase):
    def setUp(self):
        # detect_noclip()은 지속시간/hold 상태를 모듈 전역에 보존하므로
        # 각 테스트가 서로 영향을 주지 않도록 초기화한다.
        noclip.collision_off_start = None
        noclip.suspicious_until = 0

    def test_normal_zero_sample_is_valid_normal_measurement(self):
        result = noclip.detect_noclip(
            {"elapsed_ms": "1000", "collision": "1", "blocked_path": "0"}
        )
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["status"], "NORMAL")
        self.assertTrue(result["measurement_complete"])
        self.assertIsNone(result["error_code"])

    def test_unavailable_sensor_is_not_misclassified_as_normal_zero(self):
        result = noclip.detect_noclip(
            {"elapsed_ms": "1000", "collision": "-1", "blocked_path": "-1"}
        )
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["status"], "ERROR")
        self.assertFalse(result["measurement_complete"])
        self.assertEqual(result["error_code"], "NOCLIP_OBSERVATION_UNAVAILABLE")

    def test_partial_measurement_preserves_valid_suspicious_signal(self):
        # collision OFF가 5초 지속되면 +1 +2 = 3점이다.
        noclip.detect_noclip(
            {"elapsed_ms": "1000", "collision": "0", "blocked_path": "-1"}
        )
        result = noclip.detect_noclip(
            {"elapsed_ms": "6000", "collision": "0", "blocked_path": "-1"}
        )
        self.assertEqual(result["score"], 3)
        self.assertEqual(result["status"], "SUSPICIOUS")
        self.assertFalse(result["measurement_complete"])
        self.assertEqual(result["error_code"], "NOCLIP_BLOCKED_PATH_UNAVAILABLE")

    def test_common_event_keeps_exact_seven_root_fields(self):
        args = SimpleNamespace(session_id="session_001", player_id="player_001")
        row = {
            "sample_id": "7",
            "elapsed_ms": "1000",
            "collision": "1",
            "blocked_path": "0",
        }
        result = noclip.detect_noclip(row)
        event = noclip.build_common_event(args, row, result, 1234)

        self.assertEqual(
            set(event),
            {
                "session_id",
                "player_id",
                "module",
                "timestamp_ms",
                "evidence",
                "reasons",
                "raw_score",
            },
        )
        self.assertEqual(event["module"], "noclip")
        self.assertEqual(event["raw_score"], 0)
        self.assertEqual(event["evidence"]["status"], "NORMAL")
        self.assertEqual(event["evidence"]["sample_id"], 7)

        # Shared 0.2.0의 실제 schema로 round-trip 검증한다.
        self.assertEqual(decode_event(encode_event(event)), event)

    def test_zero_score_is_written_before_it_is_queued(self):
        args = SimpleNamespace(session_id="session_001", player_id="player_001")
        row = {
            "sample_id": "1",
            "elapsed_ms": "1000",
            "collision": "1",
            "blocked_path": "0",
        }
        event = noclip.build_common_event(args, row, noclip.detect_noclip(row), 0)

        calls = []
        receipt = SimpleNamespace(event_id="00000000-0000-0000-0000-000000000001", status="queued")

        with tempfile.TemporaryDirectory() as tmp:
            event_file = Path(tmp) / "events.jsonl"

            def fake_send(payload):
                self.assertTrue(event_file.exists())
                saved = json.loads(event_file.read_text(encoding="utf-8").splitlines()[-1])
                self.assertEqual(saved, payload)
                calls.append(payload)
                return receipt

            with patch.object(noclip, "send_detection", side_effect=fake_send):
                self.assertTrue(
                    noclip.persist_and_send_common_event(
                        event_file, event, telemetry_enabled=True
                    )
                )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["raw_score"], 0)

    def test_local_write_failure_skips_shared_queue(self):
        event = {
            "session_id": "session_001",
            "player_id": "player_001",
            "module": "noclip",
            "timestamp_ms": 0,
            "evidence": {"status": "NORMAL"},
            "reasons": [],
            "raw_score": 0,
        }

        with patch.object(noclip, "write_common_event", side_effect=OSError("disk full")):
            with patch.object(noclip, "send_detection") as send:
                self.assertFalse(
                    noclip.persist_and_send_common_event(
                        Path("unused.jsonl"), event, telemetry_enabled=True
                    )
                )
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
