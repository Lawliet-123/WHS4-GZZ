"""Capture-wrapper validation only; fixtures are temporary, never Replay data."""

import ast
import base64
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ESP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ESP_ROOT.parents[2]
SCRIPT = ESP_ROOT / "scripts" / "collect_replay.ps1"


class CollectReplayScriptTests(unittest.TestCase):
    def setUp(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.blocks = dict(re.findall(r"\$(\w+) = @'\n(.*?)\n'@", source, re.S))
        temporary = tempfile.TemporaryDirectory(prefix="gzz-capture-validation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "raw").mkdir()
        self.session = "synthetic_validation_only"
        self.player = "synthetic_player"
        self.manifest = {
            "schema_version": "meccha.telemetry-session.v1",
            "status": "completed", "failure_reason": None,
            "session_id": self.session,
            "game_executable": "PenguinHotel-Win64-Shipping.exe",
            "producer": {"name": "meccha-esp-localguard"},
            "test_metadata": {"scenario": "normal"},
            "event_count": 1, "raw_event_count": 0, "raw_counts": {},
        }
        self.event = {
            "session_id": self.session, "player_id": self.player,
            "module": "esp", "timestamp_ms": 1000, "evidence": {},
            "reasons": ["Validation fixture, not a real observation"], "raw_score": 1,
        }
        self.health = {
            "schema_version": "meccha.capture-health.v1", "sample_count": 2,
            "healthy_sample_count": 1, "insufficient_sample_count": 1,
            "last_status": "LOW", "last_observation_confidence": 65,
        }

    def validate(self, *, events=None):
        events = [self.event] if events is None else events
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.root / "events.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
        encoded = base64.b64encode(json.dumps(self.health).encode()).decode("ascii")
        return subprocess.run(
            [sys.executable, "-B", "-c", self.blocks["verifyCapture"],
             str(self.root), str(REPO_ROOT), self.session, self.player, "normal",
             "-", "-", "500", encoded],
            capture_output=True, text=True, timeout=10,
        )

    def test_all_embedded_python_blocks_have_valid_syntax(self):
        self.assertEqual(set(self.blocks), {"pythonCheck", "channelCheck", "verifyCapture"})
        for name, source in self.blocks.items():
            with self.subTest(name=name):
                ast.parse(source)

    def test_valid_local_capture_keeps_whitelisted_health_diagnostic(self):
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"event_count": 1, "raw_event_count": 0})
        diagnostic = json.loads((self.root / "capture_health.json").read_text("utf-8"))
        self.assertEqual(diagnostic, self.health)

    def test_missing_healthy_observation_is_rejected(self):
        self.health.update(healthy_sample_count=0, last_status="INSUFFICIENT")
        self.assertEqual(self.validate().returncode, 2)

    def test_unavailable_at_shutdown_is_rejected_even_after_healthy_sample(self):
        self.health["last_status"] = "INSUFFICIENT"
        self.assertEqual(self.validate().returncode, 2)

    def test_player_or_module_mismatch_is_rejected(self):
        for key, wrong in (("player_id", "other_player"), ("module", "noclip")):
            with self.subTest(key=key):
                wrong_event = dict(self.event, **{key: wrong})
                self.assertEqual(self.validate(events=[wrong_event]).returncode, 2)

    def test_failed_or_wrong_count_manifest_is_rejected(self):
        self.manifest["status"] = "failed"
        self.assertEqual(self.validate().returncode, 2)
        self.manifest.update(status="completed", event_count=2)
        self.assertEqual(self.validate().returncode, 2)

    def test_raw_target_must_be_the_selected_game_pid(self):
        raw = {"session_id": self.session, "payload": {"target_pid": 999}}
        (self.root / "raw" / "fixture.jsonl").write_text(json.dumps(raw) + "\n", encoding="utf-8")
        self.manifest.update(raw_event_count=1, raw_counts={"fixture": 1})
        self.assertEqual(self.validate().returncode, 2)


if __name__ == "__main__":
    unittest.main()
