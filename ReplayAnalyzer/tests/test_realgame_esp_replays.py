"""Validate checked-in, privacy-filtered real captures; never run a detector."""
import json
from pathlib import Path
import re
import unittest

from shared.schema import decode_event, EVENT_FIELDS


ROOT = Path(__file__).resolve().parents[1] / "replay-data" / "esp"
CAPTURES = {
    "normal_realgame_20261006_capture02": ("NORMAL", 63, None, None),
    "esp_realgame_20261006_capture01": ("CHEAT", 82, 5706, 80409),
    "esp_realgame_20261006_capture02": ("CHEAT", 67, 5189, 77984),
}
PRIVATE_KEYS = {"username", "user_name", "source_user", "target_user", "computer",
                "computer_name", "hostname", "window_title", "host_identity", "call_trace"}


def assert_public(test, value):
    if isinstance(value, dict):
        test.assertFalse(PRIVATE_KEYS.intersection(key.casefold() for key in value))
        for item in value.values():
            assert_public(test, item)
    elif isinstance(value, list):
        for item in value:
            assert_public(test, item)
    elif isinstance(value, str):
        test.assertIsNone(re.search(r"(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+\\)", value))


class ActualESPReplayTests(unittest.TestCase):
    def test_capture_labels_counts_timings_and_public_seven_fields(self):
        for session, (label, count, start, end) in CAPTURES.items():
            with self.subTest(session=session):
                folder = ROOT / session
                self.assertEqual({path.name for path in folder.iterdir()}, {"manifest.json", "events.jsonl"})
                manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                events = [decode_event(line) for line in (folder / "events.jsonl").read_bytes().splitlines() if line.strip()]
                self.assertEqual((manifest["session_id"], manifest["label"], manifest["event_count"]), (session, label, count))
                self.assertEqual((manifest["cheat_start_ms"], manifest["cheat_end_ms"]), (start, end))
                self.assertEqual(len(events), count)
                self.assertEqual(manifest["source"]["session_status"], "completed")
                for event in events:
                    self.assertEqual(set(event), set(EVENT_FIELDS))
                    self.assertEqual((event["session_id"], event["player_id"], event["module"]), (session, manifest["player_id"], "esp"))
                    self.assertIsNot(event["evidence"].get("synthetic"), True)
                    assert_public(self, event["evidence"])
                    assert_public(self, event["reasons"])

    def test_cheat_timings_are_observed_lifecycle_not_scheduled_constants(self):
        for session, (label, _, start, end) in CAPTURES.items():
            manifest = json.loads((ROOT / session / "manifest.json").read_text(encoding="utf-8"))
            proof = manifest["source"]["realgame_validation"]
            if label == "NORMAL":
                self.assertEqual(proof["kind"], "no_esp_spawned")
                self.assertIsNone(proof["first_read_ready_ms"])
                continue
            with self.subTest(session=session):
                self.assertEqual(proof["kind"], "observed_original_esp_lifecycle")
                self.assertLessEqual(start, proof["first_read_ready_ms"])
                self.assertLess(proof["first_read_ready_ms"], end)
                self.assertGreaterEqual(proof["fresh_read_heartbeat_count"], 2)
                self.assertLessEqual(proof["maximum_heartbeat_gap_seconds"], 2)
                self.assertFalse(proof["remote_rendering_claimed"])
                self.assertRegex(proof["source_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
