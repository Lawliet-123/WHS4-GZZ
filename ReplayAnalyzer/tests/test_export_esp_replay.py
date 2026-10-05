"""Exporter checks use explicit fixtures, never real captures or live sensors."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ReplayAnalyzer.tools.export_esp_replay import export_session
from shared.schema import decode_event


class EspReplayExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "capture"
        self.source.mkdir()
        self.output = self.root / "replays"
        self.manifest = {
            "schema_version": "meccha.telemetry-session.v1",
            "session_id": "normal_fixture_001",
            "status": "completed",
            "failure_reason": None,
            "producer": {"name": "meccha-esp-localguard", "version": "0.2.0", "pid": 1234},
            "event_count": 2,
            "test_metadata": {"scenario": "normal", "cheat_on_ms": None, "cheat_off_ms": None},
            "host_identity": {"pseudonym": "private-endpoint"},
        }
        self.events = [
            {"session_id": "normal_fixture_001", "player_id": "player_fixture_001", "module": "esp",
             "timestamp_ms": 60163, "raw_score": 3, "reasons": ["fixture evidence"],
             "evidence": {"event_type": "process_access", "categories": ["process_tamper"],
                          "source_image": r"C:\Users\private-person\tools\tool.exe",
                          "target_image": r"C:\Users\private-person\game\game.exe",
                          "sensor_event_id": r"private-host:C:\Users\private-person\source-001",
                          "user": "private-person", "computer": "private-host",
                          "password": "private-password", "data": {"RuleName": "private-rule"},
                          "granted_access": 0x1FFFFF}},
            {"session_id": "normal_fixture_001", "player_id": "player_fixture_001", "module": "esp",
             "timestamp_ms": 120073, "raw_score": 1.25, "reasons": [],
             "evidence": {"event_type": "window_overlap", "categories": ["overlay"],
                          "process_path": r"C:\Users\private-person\overlay\overlay.exe",
                          "title": "private-window", "window_rect": {
                              "left": 1, "top": 2, "user_name": "private-person",
                              "items": [{"title": "private-window",
                                         "module_path": r"C:\Users\private-person\nested\nested.dll",
                                         "sensor_event_id": "private-host:nested-id"}]}}},
        ]
        self.write_source()

    def write_source(self):
        (self.source / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.source / "events.jsonl").write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in self.events), encoding="utf-8")

    def read_export(self, path):
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        events = [decode_event(line) for line in (path / "events.jsonl").read_bytes().splitlines()]
        return manifest, events

    def test_privacy_filter_recursive_and_no_raw_or_source_path(self):
        raw = self.source / "raw"
        raw.mkdir()
        (raw / "private.jsonl").write_text("private-local-raw", encoding="utf-8")
        before = {name: (self.source / name).read_bytes() for name in ("manifest.json", "events.jsonl")}
        exported = export_session(self.source, self.output)
        manifest, events = self.read_export(exported)
        text = "".join(path.read_text(encoding="utf-8") for path in exported.iterdir())
        for private in ("private-person", "private-host", "private-password", "private-rule",
                        "private-window", "private-endpoint", str(self.source)):
            self.assertNotIn(private, text)
        self.assertEqual(set(path.name for path in exported.iterdir()), {"manifest.json", "events.jsonl"})
        self.assertEqual(events[0]["evidence"]["source_image"], "tool.exe")
        self.assertEqual(events[1]["evidence"]["process_path"], "overlay.exe")
        nested = events[1]["evidence"]["window_rect"]["items"][0]
        self.assertEqual(nested["module_path"], "nested.dll")
        self.assertRegex(nested["sensor_event_id"], r"^sensor-sha256:[0-9a-f]{64}$")
        self.assertRegex(events[0]["evidence"]["sensor_event_id"], r"^sensor-sha256:[0-9a-f]{64}$")
        self.assertNotIn("signature_message", events[0]["evidence"])
        self.assertEqual(manifest["source"]["producer"],
                         {"name": "meccha-esp-localguard", "version": "0.2.0", "revision": None})
        for name, contents in before.items():
            self.assertEqual((self.source / name).read_bytes(), contents)
            hash_key = "manifest_sha256" if name == "manifest.json" else "events_sha256"
            self.assertEqual(manifest["source"][hash_key], hashlib.sha256(contents).hexdigest())

    def test_preserves_scores_timestamps_reasons_and_ids(self):
        original = deepcopy(self.events)
        manifest, events = self.read_export(export_session(self.source, self.output))
        for before, after in zip(original, events):
            for key in ("timestamp_ms", "raw_score", "session_id", "player_id", "module", "reasons"):
                self.assertEqual(after[key], before[key])
                self.assertIs(type(after[key]), type(before[key]))
        self.assertEqual(manifest["label"], "NORMAL")
        self.assertIsNone(manifest["cheat_type"])
        self.assertEqual(manifest["event_count"], 2)
        self.assertIn("not a new test run", " ".join(manifest["source"]["notes"]))

    def test_cheat_uses_supplied_timing_and_does_not_infer_missing_on_off(self):
        self.manifest["test_metadata"] = {"scenario": "esp", "cheat_on_ms": 30000, "cheat_off_ms": 70000}
        self.write_source()
        manifest, _ = self.read_export(export_session(self.source, self.output))
        self.assertEqual((manifest["label"], manifest["cheat_type"]), ("CHEAT", "ESP"))
        self.assertEqual((manifest["cheat_start_ms"], manifest["cheat_end_ms"]), (30000, 70000))
        self.manifest["test_metadata"] = {"scenario": "esp"}
        self.write_source()
        manifest, _ = self.read_export(export_session(self.source, self.root / "second-export"))
        self.assertIsNone(manifest["cheat_start_ms"])
        self.assertIsNone(manifest["cheat_end_ms"])

    def test_preserves_existing_sensor_fingerprint(self):
        fingerprint = "sensor-sha256:" + "a" * 64
        self.events[0]["evidence"]["sensor_event_id"] = fingerprint
        self.write_source()
        _, events = self.read_export(export_session(self.source, self.output))
        self.assertEqual(events[0]["evidence"]["sensor_event_id"], fingerprint)

    def test_preserves_public_basename_path_hash_and_rehashes_full_paths(self):
        original_hash = "a" * 64
        self.events[0]["evidence"]["source_image"] = "tool.exe"
        self.events[0]["evidence"]["source_image_path_sha256"] = original_hash
        self.events[0]["evidence"]["target_image_path_sha256"] = "b" * 64
        nested = self.events[1]["evidence"]["window_rect"]["items"][0]
        nested["module_path"] = "nested.dll"
        nested["module_path_path_sha256"] = original_hash
        self.write_source()
        _, events = self.read_export(export_session(self.source, self.output))
        self.assertEqual(events[0]["evidence"]["source_image_path_sha256"], original_hash)
        self.assertEqual(events[1]["evidence"]["window_rect"]["items"][0]["module_path_path_sha256"],
                         original_hash)
        self.assertNotEqual(events[0]["evidence"]["target_image_path_sha256"], "b" * 64)
        self.assertRegex(events[0]["evidence"]["target_image_path_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("private-person", json.dumps(events))

    def assert_rejected_without_output(self, message):
        self.write_source()
        with self.assertRaisesRegex(ValueError, message):
            export_session(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_running_failed_empty_and_unobserved_sessions(self):
        for status in ("running", "failed"):
            with self.subTest(status=status):
                self.manifest["status"] = status
                self.assert_rejected_without_output("completed")
        self.manifest["status"] = "completed"
        self.manifest["failure_reason"] = "capture failed"
        self.assert_rejected_without_output("completed")
        self.manifest["failure_reason"] = None
        original = deepcopy(self.events)
        self.events = []
        self.manifest["event_count"] = 0
        self.assert_rejected_without_output("identifiable player")
        self.events = original
        self.manifest["event_count"] = 2
        for status in ("ERROR", "OFFLINE", "INSUFFICIENT", "INSUFFICIENT_OBSERVATION"):
            with self.subTest(unavailable_status=status):
                for event in self.events:
                    event["evidence"]["status"] = status
                self.assert_rejected_without_output("usable ESP observations")

    def test_rejects_mismatched_session_player_and_module(self):
        cases = (("session_id", "different_session", "mismatched session"),
                 ("player_id", "different_player", "exactly one player"),
                 ("module", "godmode", "not an ESP"))
        original = deepcopy(self.events)
        for key, value, message in cases:
            with self.subTest(field=key):
                self.events = deepcopy(original)
                self.events[1][key] = value
                self.assert_rejected_without_output(message)

    def test_rejects_schema_extras_bad_scores_counts_and_synthetic_events(self):
        self.events[0]["status"] = "NORMAL"
        self.assert_rejected_without_output("seven-field schema")
        del self.events[0]["status"]
        self.events[0]["raw_score"] = True
        self.assert_rejected_without_output("seven-field schema")
        self.events[0]["raw_score"] = 3
        self.manifest["event_count"] = 99
        self.assert_rejected_without_output("event_count")
        self.manifest["event_count"] = 2
        self.events[0]["evidence"]["synthetic"] = True
        self.assert_rejected_without_output("synthetic")

    def test_rejects_unknown_scenario_and_invalid_timing(self):
        self.manifest["test_metadata"]["scenario"] = "unknown"
        self.assert_rejected_without_output("scenario")
        for metadata in ({"scenario": "esp", "cheat_on_ms": True},
                         {"scenario": "esp", "cheat_on_ms": 10, "cheat_off_ms": 9},
                         {"scenario": "normal", "cheat_on_ms": 10}):
            with self.subTest(metadata=metadata):
                self.manifest["test_metadata"] = metadata
                self.assert_rejected_without_output("cheat_|NORMAL")

    def test_existing_destination_fails_closed(self):
        exported = export_session(self.source, self.output)
        original = {path.name: path.read_bytes() for path in exported.iterdir()}
        with self.assertRaises(FileExistsError):
            export_session(self.source, self.output)
        self.assertEqual({path.name: path.read_bytes() for path in exported.iterdir()}, original)

    def test_rejects_output_inside_capture(self):
        with self.assertRaisesRegex(ValueError, "outside the source"):
            export_session(self.source, self.source / "exports")
        self.assertFalse((self.source / "exports").exists())


if __name__ == "__main__":
    unittest.main()
