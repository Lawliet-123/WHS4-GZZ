from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from gzz_anticheat import cli, mark, replay
from gzz_anticheat.detector import AutoPaintDetector
from gzz_anticheat.session import LuaControl, PaintTail, Session


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = cli.build_parser().parse_args([
            "--session-id", "normal_test", "--label", "NORMAL", "--output-dir", str(self.root), "--telemetry", "off",
        ])

    def test_ids_cannot_escape_session_directory(self):
        for value in (".", "..", "../oops", "a/b", "a\\b", "CON", "aux.txt", "foo."):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                cli._safe_id(value)

    def test_session_and_existing_logs_not_overwritten(self):
        session = Session(self.args)
        self.assertEqual(session.manifest["label"], "NORMAL")
        self.assertIsNone(session.manifest["cheat_start_ms"])
        with self.assertRaises(FileExistsError):
            Session(self.args)

    def test_marks_are_elapsed_time_and_manifest_owns_intervals(self):
        self.args.label = "CHEAT"
        session = Session(self.args)
        for state in ("IN_ROOM", "ON", "PAINT_DONE", "OFF"):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mark.main(["--session-id", "normal_test", "--output-dir", str(self.root), "--state", state]), 0)
        session.save(status="STOPPED")
        manifest = json.loads((session.root / "manifest.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(manifest["cheat_end_ms"], manifest["cheat_start_ms"])
        self.assertLess(manifest["cheat_end_ms"], 5000)
        self.assertEqual(len(manifest["annotations"]), 4)
        with self.assertRaises(SystemExit):
            mark.main(["--session-id", "normal_test", "--output-dir", str(self.root), "--state", "OFF"])

    def test_clock_jump_is_sticky_invalid(self):
        session = Session(self.args)
        with patch("gzz_anticheat.session.time.time_ns", return_value=(session.start_unix_ms + 5000) * 1000000):
            session.clock_drift_ms()
        session.clock_drift_ms()
        self.assertFalse(session.clock_valid)

    def test_partial_jsonl_and_missing_sensor_are_not_normal(self):
        path = self.root / "paint_calls.jsonl"
        tail = PaintTail(path)
        self.assertEqual(tail.poll(0)["state"], "MISSING")
        path.write_bytes(b'{"kind":"call"')
        self.assertEqual(tail.poll(1)["calls"], 0)
        with path.open("ab") as out:
            out.write(b'}\nnot-json\n{"kind":"health","registered_hooks":3}\n')
        result = tail.poll(2)
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["parse_errors"], 1)
        self.assertEqual(result["health"]["registered_hooks"], 3)
        self.assertEqual(tail.poll(4000)["state"], "STALE")

    def test_control_is_text_and_rejects_second_live_session(self):
        session = Session(self.args)
        mod = self.root / "GZZPaintObserver"
        (mod / "Scripts").mkdir(parents=True)
        (mod / "Scripts" / "main.lua").touch()
        control = LuaControl(mod, session)
        with self.assertRaises(RuntimeError):
            LuaControl(mod, session)
        self.assertIn("active=1", control.path.read_text(encoding="utf-8"))
        control.close()
        self.assertIn("active=0", control.path.read_text(encoding="utf-8"))

    def test_artifact_on_disk_alone_no_longer_scores(self):
        event = AutoPaintDetector().evaluate({"artifacts": [{"path": "old.dll", "kind": "autopaint_runtime_file"}]}, session_id="normal", player_id="player")
        self.assertEqual(event.raw_score, 0)
        self.assertEqual(event.evidence["runtime_artifact"], 1)

    def test_cli_records_every_score_and_replay_matches(self):
        class FakeSensor:
            def __init__(self, *a, **k): pass
            def collect(self): return {"target": {"found": True}, "sensor_healthy": True}
        fake = types.SimpleNamespace(WindowsClientSensor=FakeSensor)
        args = cli.build_parser().parse_args(["--session-id", "normal_cli", "--output-dir", str(self.root), "--duration", "0.05", "--interval", "0.01", "--telemetry", "off"])
        with patch.dict(sys.modules, {"gzz_anticheat.windows_sensor": fake}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.run(args), 0)
        folder = self.root / "normal_cli"
        raw = [json.loads(x) for x in (folder / "raw" / "integrity.jsonl").read_text().splitlines()]
        events = [json.loads(x) for x in (folder / "events.jsonl").read_text().splitlines()]
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(len(raw), len(events))
        self.assertTrue(all(e["raw_score"] == 0 for e in events))
        self.assertEqual(events[0]["evidence"]["behavioral_scoring_enabled"], 0)
        self.assertEqual(set(events[0]), {"session_id", "player_id", "module", "timestamp_ms", "evidence", "reasons", "raw_score"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            replay.main([str(folder / "raw" / "integrity.jsonl")])
        self.assertEqual(events, [json.loads(x) for x in output.getvalue().splitlines()])

    def test_unhealthy_sensor_does_not_emit_zero_score(self):
        class FakeSensor:
            def __init__(self, *a, **k): pass
            def collect(self): return {"target": {"found": True}, "sensor_healthy": False}
        self.args.once = True
        with patch.dict(sys.modules, {"gzz_anticheat.windows_sensor": types.SimpleNamespace(WindowsClientSensor=FakeSensor)}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.run(self.args)
        self.assertEqual((self.root / "normal_test" / "events.jsonl").read_text(), "")
