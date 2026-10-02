import json
import tempfile
import unittest
from pathlib import Path

from anti_esp.core.events import (
    DetectorDecision,
    SensorBatch,
    SensorEvent,
    TeamDetectionEvent,
)
from anti_esp.core.session import SessionTelemetryWriter


class PipelineContractTests(unittest.TestCase):
    def test_team_event_matches_supplied_common_format_exactly(self):
        event = TeamDetectionEvent(
            session_id="esp_001",
            player_id="player_042",
            module="ESP",
            timestamp_ms=507_000,
            evidence={"granted_access": "0x00000010"},
            reasons=("External VM_READ access",),
            raw_score=2,
        )
        self.assertEqual(
            set(event.to_dict()),
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
        self.assertEqual(event.to_dict()["module"], "esp")
        self.assertEqual(TeamDetectionEvent.from_dict(event.to_dict()).to_dict(), event.to_dict())

    def test_sensor_event_round_trip(self):
        event = SensorEvent(
            session_id="session-1",
            sensor_id="sysmon_process_access",
            event_type="process_access",
            subject_id="game:777",
            timestamp_ms=123_456,
            sequence=4,
            payload={"source_pid": 10, "access_labels": ["VM_READ"]},
        )

        restored = SensorEvent.from_dict(json.loads(event.to_json()))

        self.assertEqual(restored.to_dict(), event.to_dict())

    def test_detector_decision_references_sensor_evidence(self):
        decision = DetectorDecision(
            session_id="session-1",
            detector_id="esp_detector",
            subject_id="game:777",
            status="review",
            score=37.5,
            reasons=("Untrusted VM_READ access",),
            evidence_event_ids=("event-1",),
            timestamp_ms=123_500,
        )

        restored = DetectorDecision.from_dict(json.loads(decision.to_json()))

        self.assertEqual(restored.status, "REVIEW")
        self.assertEqual(restored.to_dict(), decision.to_dict())

    def test_sensor_batch_keeps_health_separate_from_events(self):
        event = SensorEvent(
            session_id="session-1",
            sensor_id="overlay_window",
            event_type="window_overlap",
            subject_id="game:777",
        )
        batch = SensorBatch(
            sensor_id="overlay_window",
            status="online",
            events=(event,),
            details={"game_window_found": True},
        )

        self.assertEqual(batch.events, (event,))
        self.assertTrue(batch.details["game_window_found"])

    def test_session_writer_creates_manifest_events_and_raw_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            event = SensorEvent(
                session_id="session-1",
                sensor_id="module_snapshot",
                event_type="module_added",
                subject_id="game:777",
                timestamp_ms=10,
                payload={"path": "C:\\Game\\example.dll"},
            )
            decision = DetectorDecision(
                session_id="session-1",
                detector_id="esp_detector",
                subject_id="game:777",
                status="NORMAL",
                score=0,
                evidence_event_ids=(event.event_id,),
                timestamp_ms=11,
            )
            with SessionTelemetryWriter(
                directory,
                session_id="session-1",
                game_executable="Game.exe",
                host_identity={"scope": "installation", "id_hash": "abc"},
            ) as writer:
                writer.append(event)
                writer.append(decision)
                writer.append_team_event(
                    TeamDetectionEvent(
                        session_id="session-1",
                        player_id="player-1",
                        module="esp",
                        timestamp_ms=12,
                        evidence={"sensor_event_id": event.event_id},
                        reasons=("New module observed",),
                        raw_score=1,
                    )
                )
                raw_path = writer.write_raw_json("module-baseline", {"modules": []})

            session_dir = Path(directory) / "session-1"
            manifest = json.loads((session_dir / "manifest.json").read_text("utf-8"))
            lines = (session_dir / "events.jsonl").read_text("utf-8").splitlines()

            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["event_count"], 1)
            self.assertEqual(manifest["raw_event_count"], 2)
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["module"], "esp")
            self.assertTrue((session_dir / "raw" / "module_snapshot.jsonl").exists())
            self.assertTrue(raw_path.exists())

    def test_writer_rejects_cross_session_record(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = SessionTelemetryWriter(
                directory, session_id="one", game_executable="Game.exe"
            )
            try:
                with self.assertRaises(ValueError):
                    writer.append(
                        SensorEvent(
                            session_id="two",
                            sensor_id="test",
                            event_type="test",
                            subject_id="game:1",
                        )
                    )
            finally:
                writer.close()

    def test_writer_refuses_to_mix_two_runs_with_the_same_session_id(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = SessionTelemetryWriter(
                directory, session_id="normal_001", game_executable="Game.exe"
            )
            writer.close()

            with self.assertRaisesRegex(FileExistsError, "choose a new --session-id"):
                SessionTelemetryWriter(
                    directory,
                    session_id="normal_001",
                    game_executable="Game.exe",
                )

    def test_team_event_idempotency_key_prevents_duplicate_jsonl_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = SessionTelemetryWriter(
                directory, session_id="esp_001", game_executable="Game.exe"
            )
            event = TeamDetectionEvent(
                session_id="esp_001",
                player_id="player_042",
                module="esp",
                timestamp_ms=1000,
                evidence={"sensor_event_id": "sensor-1"},
                reasons=("test",),
                raw_score=1,
            )
            self.assertTrue(
                writer.append_team_event(event, idempotency_key="outbox-1")
            )
            self.assertFalse(
                writer.append_team_event(event, idempotency_key="outbox-1")
            )
            writer.close()

            event_lines = (
                Path(directory) / "esp_001" / "events.jsonl"
            ).read_text("utf-8").splitlines()
            manifest = json.loads(
                (Path(directory) / "esp_001" / "manifest.json").read_text("utf-8")
            )
            self.assertEqual(len(event_lines), 1)
            self.assertEqual(manifest["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
