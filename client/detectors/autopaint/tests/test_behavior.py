"""Synthetic rule tests, not additional normal/cheat play sessions."""
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from gzz_anticheat import cli, replay
from gzz_anticheat.behavior import PaintBehaviorDetector, REQUIRED_HOOKS
from gzz_anticheat.behavior_replay import replay_records
from gzz_anticheat.detector import AutoPaintDetector
from gzz_anticheat.session import PaintTail


class Stream:
    def __init__(self):
        self.sequence = 0
        self.records = []

    def add(self, kind, timestamp, **extra):
        self.sequence += 1
        item = dict(kind=kind, timestamp_ms=timestamp, clock_resolution_ms=1,
                    session_id="test", player_id="p", session_token="a" * 32,
                    sequence=self.sequence, **extra)
        self.records.append(item)
        return item

    def health(self, timestamp, **extra):
        values = dict(local_pawn="pawn", network={"world": "world"}, dropped=0,
                      write_errors=0, callback_errors=0,
                      hooks=[dict(function_name=n, registered=True, available=True) for n in sorted(REQUIRED_HOOKS)])
        values.update(extra)
        return self.add("health", timestamp, **values)

    def ready(self):
        self.add("collector_start", 0)
        for timestamp in (0, 1000, 2000, 3000):
            self.health(timestamp)
        return self

    def call(self, timestamp, name="PaintAtUVWithBrush", *, mode=True, local=True, **extra):
        context = dict(world="world", local_pawn="pawn", owner="pawn", object="component",
                       owner_is_local_pawn=local, owner_locally_controlled=local,
                       object_valid=True, local_is_paint_mode=mode, local_is_brushing=False)
        context.update(extra)
        return self.add("call", timestamp, function_name=name, context=context,
                        parameters={"stroke_count": 50})

    def burst(self, *, mode=True, local=True):
        for i in range(8):
            self.call(3100 + i * 5, mode=mode, local=local)
        return self

    def engine(self):
        engine = PaintBehaviorDetector("test", "p")
        for record in self.records:
            engine.consume(record)
        return engine


class BehaviorTests(unittest.TestCase):
    def test_local_uv_detects_without_rpc_or_dll(self):
        result = Stream().ready().burst().engine().evaluate(3500)
        self.assertTrue(result["valid"])
        self.assertTrue(result["detected"])
        self.assertEqual(result["score"], 10)

    def test_mode_off_and_batch_are_additional_not_required(self):
        stream = Stream().ready().burst(mode=False)
        stream.call(3140, "ServerPaintBatch")
        stream.call(3145, "ServerPaintBatch")
        self.assertEqual(stream.engine().evaluate(3500)["score"], 15)

    def test_remote_and_unknown_ownership_do_not_score(self):
        for local in (False, None):
            with self.subTest(local=local):
                self.assertEqual(Stream().ready().burst(mode=False, local=local).engine().evaluate(3500)["score"], 0)

    def test_owner_flag_without_identity_does_not_score(self):
        stream = Stream().ready().burst()
        for r in stream.records:
            if r["kind"] == "call":
                r["context"]["owner"] = "remote"
        self.assertEqual(stream.engine().evaluate(3500)["score"], 0)

    def test_screen_matching_respects_settle_period(self):
        stream = Stream().ready().burst()
        stream.call(3200, "PaintAtScreenPosition")
        result = stream.engine().evaluate(3500)
        self.assertEqual(result["score"], 6)
        self.assertEqual(result["evidence"]["behavior_unmatched_uv_calls"], 0)

    def test_unrelated_component_screen_does_not_explain_uv(self):
        stream = Stream().ready().burst()
        stream.call(3200, "PaintAtScreenPosition", object="other_component")
        self.assertEqual(stream.engine().evaluate(3500)["score"], 10)

    def test_different_components_do_not_pool_their_counts(self):
        stream = Stream().ready().burst()
        calls = [r for r in stream.records if r["kind"] == "call"]
        for r in calls[:4]:
            r["context"]["object"] = "other_component"
        self.assertEqual(stream.engine().evaluate(3500)["score"], 0)

    def test_small_clock_skew_does_not_reset_a_healthy_window(self):
        stream = Stream().ready().burst()
        stream.health(3501)
        self.assertTrue(stream.engine().evaluate(3500)["detected"])

    def test_begin_stroke_alone_is_not_an_exemption(self):
        stream = Stream().ready()
        stream.call(3050, "BeginStroke")
        stream.burst()
        result = stream.engine().evaluate(3500)
        self.assertEqual(result["score"], 10)
        self.assertEqual(result["evidence"]["behavior_uv_during_stroke"], 8)

    def test_normal_calls_and_brushing_false_alone_score_zero(self):
        stream = Stream().ready()
        for i in range(40):
            stream.call(3100 + i, "PaintAtScreenPosition")
        self.assertEqual(stream.engine().evaluate(3500)["score"], 0)

    def test_one_call_is_not_a_detection(self):
        stream = Stream().ready()
        stream.call(3100, mode=False)
        self.assertEqual(stream.engine().evaluate(3500)["score"], 0)

    def test_missing_hook_prevents_absence_based_verdict(self):
        stream = Stream().ready().burst()
        stream.health(3200, hooks=[dict(function_name="PaintAtUVWithBrush", registered=True, available=True)])
        result = stream.engine().evaluate(3500)
        self.assertFalse(result["valid"])
        self.assertEqual(result["score"], 0)

    def test_ia_hook_is_not_required(self):
        self.assertTrue(Stream().ready().burst().engine().evaluate(3500)["valid"])

    def test_sequence_gap_and_duplicate_hold_judgment(self):
        for delta in (-1, 1):
            stream = Stream().ready().burst()
            stream.records[-1]["sequence"] += delta
            self.assertFalse(stream.engine().evaluate(3500)["valid"])

    def test_stale_or_stopped_collector_not_normal(self):
        stream = Stream().ready().burst()
        self.assertFalse(stream.engine().evaluate(7000)["valid"])
        stream.add("collector_stop", 3300)
        result = stream.engine().evaluate(3500)
        self.assertEqual(result["state"], "STOPPED")
        self.assertFalse(result["valid"])

    def test_clock_or_session_mismatch_hold_judgment(self):
        for change in ({"clock_resolution_ms": 1000}, {"session_id": "other"},
                       {"timestamp_ms": 2000}, {"session_token": "foreign"}, {"timestamp_ms": True}):
            stream = Stream().ready().burst()
            stream.records[-1].update(change)
            self.assertFalse(stream.engine().evaluate(3500)["valid"])
        self.assertFalse(Stream().ready().burst().engine().evaluate(3500, clock_valid=False)["valid"])

    def test_malformed_hook_and_function_names_do_not_crash(self):
        stream = Stream().ready().burst()
        stream.records[-1]["function_name"] = ["bad"]
        self.assertFalse(stream.engine().evaluate(3500)["valid"])
        stream = Stream().ready().burst()
        stream.health(3200, hooks=[{"function_name": [], "registered": True, "available": True}])
        self.assertFalse(stream.engine().evaluate(3500)["valid"])

    def test_loss_and_restart_clear_old_evidence(self):
        stream = Stream().ready().burst()
        stream.health(3200, dropped=1)
        self.assertFalse(stream.engine().evaluate(3500)["valid"])
        stream.sequence = 0
        stream.add("collector_start", 3300)
        stream.health(3310)
        self.assertFalse(stream.engine().evaluate(3500)["valid"])

    def test_score_expires_but_detection_history_remains(self):
        stream = Stream().ready().burst()
        engine = stream.engine()
        self.assertTrue(engine.evaluate(3500)["detected"])
        for t in (4000, 5000, 6000):
            engine.consume(stream.health(t))
        result = engine.evaluate(6500)
        self.assertTrue(result["valid"])
        self.assertEqual(result["score"], 0)
        self.assertTrue(result["ever_detected"])
        self.assertEqual(result["first_detected_ms"], 3500)

    def test_window_limit_fails_closed_without_scoring(self):
        engine = PaintBehaviorDetector("test", "p")
        engine.config = replace(engine.config, maximum_records=4)
        for r in Stream().ready().burst().records:
            engine.consume(r)
        self.assertFalse(engine.evaluate(3500)["valid"])
        self.assertLessEqual(len(engine.observations), 4)

    def test_unresolved_screen_context_cannot_establish_absence(self):
        stream = Stream().ready().burst()
        stream.call(3150, "PaintAtScreenPosition", local=None)
        self.assertFalse(stream.engine().evaluate(3500)["valid"])

    def test_new_pawn_cannot_inherit_previous_window(self):
        stream = Stream().ready().burst()
        stream.health(3200, local_pawn="new_pawn")
        self.assertFalse(stream.engine().evaluate(3500)["valid"])

    def test_stale_gap_requires_full_clean_window_after_recovery(self):
        stream = Stream().ready().burst()
        engine = stream.engine()
        self.assertFalse(engine.evaluate(7000)["valid"])
        engine.consume(stream.health(7100))
        self.assertFalse(engine.evaluate(7300)["valid"])
        engine.consume(stream.health(8100))
        engine.consume(stream.health(9100))
        self.assertTrue(engine.evaluate(9600)["valid"])

    def test_future_records_and_warmup_do_not_score(self):
        engine = Stream().ready().burst().engine()
        self.assertFalse(engine.evaluate(3100)["valid"])
        stream = Stream()
        stream.add("collector_start", 0)
        stream.health(0)
        stream.burst()
        self.assertFalse(stream.engine().evaluate(3500)["valid"])

    def test_transport_gap_clears_evidence(self):
        engine = Stream().ready().burst().engine()
        engine.transport(parse_errors=1, stream_resets=0, backlogged=False)
        self.assertFalse(engine.evaluate(3500)["valid"])

    def test_backlogged_reader_does_not_score_partial_window(self):
        engine = Stream().ready().burst().engine()
        engine.transport(parse_errors=0, stream_resets=0, backlogged=True)
        self.assertFalse(engine.evaluate(3500)["valid"])

    def test_fusion_is_max_not_sum_and_keeps_contract(self):
        result = Stream().ready().burst().engine().evaluate(3500)
        snapshot = dict(behavior_assessment=result, timestamp_ms=3500,
                        processes=[dict(pid=1, name="meccha-chameleon-litev2.exe")])
        event = AutoPaintDetector().evaluate(snapshot, session_id="test", player_id="p")
        self.assertEqual(event.raw_score, 10)
        self.assertEqual(event.evidence["integrity_score"], 2)
        self.assertEqual(set(event.to_dict()), {"session_id", "player_id", "module", "timestamp_ms", "evidence", "reasons", "raw_score"})
        snapshot["sensor_healthy"] = False
        event = AutoPaintDetector().evaluate(snapshot, session_id="test", player_id="p")
        self.assertEqual(event.raw_score, 10)
        self.assertEqual(event.evidence["integrity_valid"], 0)

    def test_replay_ignores_label_and_manual_markers(self):
        stream = Stream().ready().burst()
        for t in (4000, 5000):
            stream.health(t)
        manifest = dict(session_id="test", player_id="p", duration_ms=6000, clock_alignment_valid=True)
        lines = [json.dumps(r) + "\n" for r in stream.records]
        outputs = []
        first = replay_records(manifest, lines, on_sample=lambda a, e: outputs.append((a, e)))
        manifest.update(label="CHEAT", cheat_start_ms=0, cheat_end_ms=1)
        second = replay_records(manifest, lines)
        self.assertEqual(first["score_distribution"], second["score_distribution"])
        self.assertEqual(first["maximum_score"], 10)
        self.assertTrue(all(e is None for a, e in outputs if not a["valid"]))
        self.assertIn("0", first["score_distribution"])

    def test_replay_rejects_incomplete_final_jsonl_line(self):
        stream = Stream().ready().burst()
        lines = [json.dumps(r) + "\n" for r in stream.records]
        lines[-1] = lines[-1].rstrip("\n")
        manifest = dict(session_id="test", player_id="p", duration_ms=4000, clock_alignment_valid=True)
        result = replay_records(manifest, lines)
        self.assertEqual(result["parse_errors"], 1)
        self.assertEqual(result["detected_samples"], 0)

    def test_incremental_tail_delivers_records_once_and_handles_partial_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paint_calls.jsonl"
            stream = Stream().ready().burst()
            received = []
            tail = PaintTail(path, received.append)
            text = "".join(json.dumps(r) + "\n" for r in stream.records)
            path.write_text(text[:-1], encoding="utf-8")
            tail.poll(3400)
            self.assertEqual(len(received), len(stream.records) - 1)
            with path.open("a", encoding="utf-8") as out:
                out.write("\n")
            tail.poll(3500)
            tail.poll(3600)
            self.assertEqual(received, stream.records)
            path.write_text('{}\n', encoding="utf-8")
            self.assertEqual(tail.poll(3700)["stream_resets"], 1)

    def test_live_cli_uses_calls_and_replay_preserves_every_event(self):
        for healthy in (True, False):
            with self.subTest(integrity_healthy=healthy), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                mod = root / "mod"
                (mod / "Scripts").mkdir(parents=True)
                (mod / "Scripts/main.lua").touch()
                args = cli.build_parser().parse_args(["--session-id", "test", "--player-id", "p",
                    "--lua-mod-dir", str(mod), "--output-dir", str(root / "logs"), "--once", "--telemetry", "off"])
                class FakeSensor:
                    def __init__(self, *a, **k): pass
                    def collect(self):
                        lines = [json.dumps(r) for r in Stream().ready().burst().records]
                        (root / "logs/test/raw/paint_calls.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
                        return {"target": {"found": True}, "sensor_healthy": healthy}
                class FakeControl:
                    token = "a" * 32
                    def __init__(self, *a, **k): pass
                    def pulse(self): pass
                    def close(self): pass
                with patch.dict(sys.modules, {"gzz_anticheat.windows_sensor": types.SimpleNamespace(WindowsClientSensor=FakeSensor)}), \
                     patch("gzz_anticheat.cli.LuaControl", FakeControl), \
                     patch("gzz_anticheat.session.Session.elapsed_ms", return_value=3500), \
                     patch("gzz_anticheat.session.Session.clock_drift_ms", return_value=0), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.run(args), 0)
                folder = root / "logs/test"
                events = [json.loads(x) for x in (folder / "events.jsonl").read_text().splitlines()]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["raw_score"], 10)
                self.assertEqual(events[0]["evidence"]["integrity_valid"], int(healthy))
                self.assertEqual(events[0]["evidence"]["behavior_detected"], 1)
                self.assertTrue(json.loads((folder / "manifest.json").read_text())["behavior_summary"]["ever_detected"])
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    replay.main([str(folder / "raw/integrity.jsonl")])
                self.assertEqual(events, [json.loads(x) for x in output.getvalue().splitlines()])


if __name__ == "__main__":
    unittest.main()
