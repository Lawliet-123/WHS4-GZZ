from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from gzz_anticheat.detector import AutoPaintDetector


FIXTURES = Path(__file__).parent / "fixtures"
EVENT_KEYS = {
    "session_id",
    "player_id",
    "module",
    "timestamp_ms",
    "evidence",
    "reasons",
    "raw_score",
}


def load_first(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as stream:
        return json.loads(next(line for line in stream if line.strip()))


class AutoPaintDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = AutoPaintDetector()

    def test_normal_snapshot_has_zero_score_and_exact_contract(self) -> None:
        snapshot = load_first("normal_001.raw.jsonl")
        event = self.detector.evaluate(
            snapshot,
            session_id=snapshot["session_id"],
            player_id=snapshot["player_id"],
        ).to_dict()
        self.assertEqual(set(event), EVENT_KEYS)
        self.assertEqual(event["module"], "autopaint")
        self.assertEqual(event["raw_score"], 0)
        self.assertEqual(event["reasons"], [])
        self.assertTrue(all(value == 0 for value in event["evidence"].values()))

    def test_current_autopaint_fixture_is_detected(self) -> None:
        snapshot = load_first("autopaint_001.raw.jsonl")
        event = self.detector.evaluate(
            snapshot,
            session_id=snapshot["session_id"],
            player_id=snapshot["player_id"],
        ).to_dict()
        expected = load_first("autopaint_001.events.jsonl")
        self.assertEqual(event, expected)
        self.assertGreaterEqual(event["raw_score"], 10)
        self.assertIn("Known AutoPaint Bridge Loaded", event["reasons"])

    def test_renamed_rebuilt_bridge_is_found_by_marker_set(self) -> None:
        snapshot = {
            "timestamp_ms": 1000,
            "modules": [
                {
                    "path": r"C:\Temp\random.dll",
                    "sha256": "not-a-known-hash",
                    "markers": [
                        "paint_full_route",
                        "f10_mesh_first_paint",
                        "ServerPaintBatch",
                    ],
                    "signature": "untrusted",
                    "user_writable_path": True,
                    "autopaint_runtime_path": False,
                }
            ],
            "threads": [],
            "tcp_endpoints": [],
            "processes": [],
            "artifacts": [],
        }
        event = self.detector.evaluate(
            snapshot, session_id="autopaint_rebuilt_001", player_id="player_042"
        )
        self.assertGreaterEqual(event.raw_score, 5)
        self.assertEqual(event.evidence["known_bridge_hash"], 0)
        self.assertEqual(event.evidence["autopaint_marker_set"], 1)

    def test_loopback_endpoint_alone_does_not_raise_score(self) -> None:
        snapshot = {
            "timestamp_ms": 1000,
            "modules": [],
            "threads": [],
            "tcp_endpoints": [
                {"local_ip": "127.0.0.1", "local_port": 43123, "state": 2}
            ],
            "processes": [],
            "artifacts": [],
        }
        event = self.detector.evaluate(
            snapshot, session_id="normal_tcp_001", player_id="player_042"
        )
        self.assertEqual(event.raw_score, 0)
        self.assertEqual(event.evidence["loopback_tcp_endpoint"], 1)


@unittest.skipUnless(os.name == "nt", "Windows-only live sensor")
class WindowsSensorSmokeTests(unittest.TestCase):
    def test_current_python_process_can_be_scanned(self) -> None:
        from gzz_anticheat.windows_sensor import WindowsClientSensor

        sensor = WindowsClientSensor("python.exe", pid=os.getpid())
        snapshot = sensor.collect()
        self.assertTrue(snapshot["target"]["found"])
        self.assertTrue(snapshot["sensor_healthy"])
        self.assertGreater(snapshot["module_count"], 0)


if __name__ == "__main__":
    unittest.main()

