"""Pure safety/provenance tests; no game, Sysmon, Qt or real replay is run."""
from dataclasses import dataclass, replace
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
for directory in (ROOT, REPO):
    sys.path.insert(0, str(directory))
spec = importlib.util.spec_from_file_location("realgame_replay_test", ROOT / "scripts/realgame_replay.py")
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


@dataclass(frozen=True)
class Camera:
    loc: tuple = (1., 2., 3.)
    rot: tuple = (0., 0., 0.)
    fov: float = 90.


@dataclass(frozen=True)
class Player:
    is_local: bool = True
    position: tuple = (1., 2., 3.)


@dataclass(frozen=True)
class Frame:
    sequence: int = 7
    started_at: float = 10.
    collection_ms: float = 10.
    camera: object = Camera()
    players: tuple = (Player(),)
    stats: tuple = (("collection_valid", True),)
    error: object = None


def manifest():
    return {"schema_version": "meccha.telemetry-session.v1", "status": "completed", "failure_reason": None,
        "session_id": "test_only", "player_id": "test_player", "game_executable": capture.GAME_EXE,
        "producer": {"name": "meccha-esp-localguard", "observation_contract": "meccha.esp-observation.v1"},
        "test_metadata": {"scenario": "unvalidated_realgame", "requested_scenario": "esp", "cheat_on_ms": None, "cheat_off_ms": None}, "event_count": 1,
        "observation_summary": {"schema_version": "meccha.esp-observation.v1", "session_id": "test_only",
            "player_id": "test_player", "poll_count": 2, "healthy_poll_count": 2, "insufficient_poll_count": 0,
            "timestamp_regression_count": 0, "game_instance_changed": False, "game_instance_missing_poll_count": 0,
            "game_instance_sha256": capture.hashlib.sha256(b"test_only\00019064\00090.000000000").hexdigest(),
            "last_status": "LOW", "minimum_observation_confidence": 60,
            "min_observation_confidence": 100, "last_observation_confidence": 100, "first_poll_ms": 0, "last_poll_ms": 1000,
            "required_sensors": {name: {"poll_count": 2, "online_poll_count": 2, "online_observed_count": 1,
                                      "observed_count": 1, "last_status": "online"} for name in capture.SENSORS}}}


def ready():
    return {"kind": "esp_read_ready", "source_sha256": capture.ESP_SHA256, "pid": 500, "game_pid": 19064,
        "observed_at_epoch": 104.123, "frame_sequence": 7, "local_player_count": 1, "remote_player_count": 0,
        "camera_valid": True, "overlay_visible": True, "collection_ms": 10.}


def handle_open():
    return {"kind": "esp_handle_open", "source_sha256": capture.ESP_SHA256, "pid": 500, "game_pid": 19064,
        "game_created_at": 90., "birth_verified": True, "source_config_enabled": True,
        "opening_started_epoch": 100.39, "observed_at_epoch": 100.4}


class RealgameReplayTests(unittest.TestCase):
    def diagnostic_fixture(self, frame=Frame(), *, visible=True, enabled=True):
        module = SimpleNamespace(FrameRenderSnapshot=Frame, CameraSnapshot=Camera,
                                 PlayerRenderSnapshot=Player)
        overlay = SimpleNamespace(_snapshots=SimpleNamespace(latest=lambda: frame),
            config=SimpleNamespace(enabled=enabled), STALE_AFTER_SECONDS=2., isVisible=lambda: visible)
        return module, overlay

    def test_readiness_diagnostic_reports_only_scalars_and_not_memory_geometry(self):
        frame = replace(Frame(), players=(Player(), Player(is_local=False)))
        module, overlay = self.diagnostic_fixture(frame)
        facts = capture.readiness_diagnostic(module, overlay, 10.25)
        self.assertEqual(facts["reason"], "READY")
        self.assertEqual(facts["local_player_count"], 1)
        self.assertEqual(facts["remote_player_count"], 1)
        self.assertEqual(facts["frame_age_ms"], 250)
        self.assertTrue(facts["collection_valid"])
        self.assertEqual(capture.worker_diagnostic(facts), facts)
        self.assertTrue(all(value is None or type(value) in (str, int, float, bool) for value in facts.values()))
        for forbidden in ("position", "loc", "rot", "fov", "sequence", "address", "game_pid", "path"):
            self.assertNotIn(forbidden, facts)
        self.assertIsNotNone(capture.verified_frame(module, overlay, 10.25))

    def test_readiness_diagnostic_explains_missing_camera_local_and_collection(self):
        cases = ((replace(Frame(), camera=None), "CAMERA_MISSING"),
                 (replace(Frame(), players=(Player(is_local=False),)), "NO_LOCAL_PLAYER"),
                 (replace(Frame(), stats=(("collection_valid", False),)), "COLLECTION_INVALID"),
                 (replace(Frame(), players=()), "NO_LOCAL_PLAYER"),
                 (replace(Frame(), camera=replace(Camera(), fov=0.)), "CAMERA_INVALID"),
                 (replace(Frame(), players=(replace(Player(), position=(float("nan"), 2., 3.)),)), "PLAYERS_INVALID"))
        for frame, reason in cases:
            with self.subTest(reason=reason):
                module, overlay = self.diagnostic_fixture(frame)
                self.assertEqual(capture.readiness_diagnostic(module, overlay, 10.25)["reason"], reason)
                self.assertIsNone(capture.verified_frame(module, overlay, 10.25))

    def test_readiness_diagnostic_distinguishes_missing_stale_and_slow_frame(self):
        cases = ((None, 10.25, "NO_FRAME"), (Frame(), 12., "FRAME_NOT_FRESH"),
                 (Frame(), 9.9, "FRAME_NOT_FRESH"),
                 (replace(Frame(), collection_ms=2000.), 10.25, "COLLECTION_SLOW"))
        for frame, now, reason in cases:
            with self.subTest(reason=reason, now=now):
                module, overlay = self.diagnostic_fixture(frame)
                self.assertEqual(capture.readiness_diagnostic(module, overlay, now)["reason"], reason)
                self.assertIsNone(capture.verified_frame(module, overlay, now))

    def test_readiness_diagnostic_preserves_hidden_and_disabled_rejection(self):
        for visible, enabled, reason in ((False, True, "OVERLAY_HIDDEN"), (True, False, "ESP_DISABLED")):
            with self.subTest(reason=reason):
                module, overlay = self.diagnostic_fixture(visible=visible, enabled=enabled)
                self.assertEqual(capture.readiness_diagnostic(module, overlay, 10.25)["reason"], reason)
                self.assertIsNone(capture.verified_frame(module, overlay, 10.25))

    def test_readiness_diagnostic_sanitizes_error_text_and_custom_exception_types(self):
        class PrivateException(Exception):
            pass
        private = r"C:\Users\private\secret: Bearer sample-not-a-real-token address=0x1234"
        for error, expected in (("MemoryReadError: " + private, "MemoryReadError"),
                                (private, "str"), (ValueError(private), "ValueError"),
                                (PrivateException(private), "Other")):
            with self.subTest(error_type=expected):
                module, overlay = self.diagnostic_fixture(replace(Frame(), error=error))
                facts = capture.readiness_diagnostic(module, overlay, 10.25)
                self.assertEqual(facts["reason"], "FRAME_ERROR")
                self.assertEqual(facts["frame_error_type"], expected)
                self.assertNotIn(private, json.dumps(facts))
                self.assertEqual(capture.worker_diagnostic(facts), facts)
                self.assertIsNone(capture.verified_frame(module, overlay, 10.25))

    def test_readiness_diagnostic_catches_snapshot_failure_without_exception_body(self):
        module, overlay = self.diagnostic_fixture()
        overlay._snapshots.latest = Mock(side_effect=RuntimeError("private-native-error-address"))
        facts = capture.readiness_diagnostic(module, overlay, 10.25)
        self.assertEqual(facts["reason"], "DIAGNOSTIC_ERROR")
        self.assertEqual(facts["frame_error_type"], "RuntimeError")
        self.assertNotIn("private-native-error-address", json.dumps(facts))

    def test_readiness_relay_ignores_private_fields_and_rejects_bad_scalars(self):
        module, overlay = self.diagnostic_fixture()
        facts = capture.readiness_diagnostic(module, overlay, 10.25)
        record = {**facts, "username": "private", "exception": "private", "camera_position": [1, 2, 3]}
        self.assertEqual(capture.worker_diagnostic(record), facts)
        for key, value in (("local_player_count", True), ("remote_player_count", -1),
                           ("collection_ms", float("nan")), ("frame_age_ms", float("inf")),
                           ("frame_fresh", "True"), ("reason", "C:\\private"),
                           ("frame_error_type", "private-native-error")):
            with self.subTest(key=key):
                self.assertIsNone(capture.worker_diagnostic({**facts, key: value}))

    def test_constructor_stage_and_readiness_cross_hidden_worker_pipe_safely(self):
        module, overlay = self.diagnostic_fixture()
        facts = capture.readiness_diagnostic(module, overlay, 10.25)
        output = Mock()
        stage = {"kind": "esp_initialization_stage", "stage": "original_constructor_started", "elapsed_ms": 0}
        lines = [json.dumps({**stage, "path": "C:\\private"}) + "\n", json.dumps(facts) + "\n"]
        result = capture.relay_worker_diagnostics(io.StringIO("".join(lines)), output=output)
        self.assertEqual(result, {"forwarded": 2, "ignored": 0})
        self.assertEqual(output.call_args_list[0].kwargs, stage)
        self.assertEqual(output.call_args_list[1].kwargs, facts)
        for update in ({"stage": "C:\\private"}, {"elapsed_ms": True}, {"elapsed_ms": -1},
                       {"elapsed_ms": 3600001}):
            self.assertIsNone(capture.worker_diagnostic({**stage, **update}))

    def test_warmup_budget_is_finite_bounded_and_does_not_change_capture_duration(self):
        self.assertEqual(capture.warmup_budget(90), 90)
        args = capture.parser().parse_args(["--game-pid", "1"])
        self.assertEqual(args.warmup_timeout, 90)
        self.assertEqual(args.duration, 120)
        for value in (True, 0, 181, float("nan"), float("inf"), "90"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                capture.warmup_budget(value)

    def test_warmup_primes_past_records_but_keeps_new_production_records(self):
        from anti_esp.sysmon import SysmonPoller
        from client.detectors.esp.tests.test_sysmon import _FakeEventLog, event_xml
        fake = _FakeEventLog([[event_xml(2), event_xml(1)], [event_xml(3), event_xml(2), event_xml(1)]])
        poller = capture.warmup_poller(lambda **options: SysmonPoller(evt_module=fake, **options))
        self.assertFalse(poller.include_existing)
        self.assertEqual(poller.poll().events, ())
        self.assertEqual([record.record_id for record in poller.poll().events], [3])
        self.assertEqual(poller.last_record_id, 3)

    def test_partial_prime_is_not_accepted_as_healthy_observation(self):
        from anti_esp.sysmon import SysmonPoller
        from client.detectors.esp.tests.test_sysmon import _FakeEventLog, event_xml
        poller = capture.warmup_poller(lambda **options: SysmonPoller(evt_module=_FakeEventLog(
            [[event_xml(3), event_xml(2), event_xml(1)]]), max_events_per_poll=2, **options))
        result = poller.poll()
        self.assertTrue(result.truncated)
        self.assertEqual(result.events, ())
        states = {name: {"status": "online"} for name in capture.SENSORS}
        states["sysmon"]["truncated"] = True
        controller = SimpleNamespace(snapshot=lambda: {"status": "LOW", "observation_confidence": 100},
                                     sensor_status=lambda: states)
        self.assertFalse(capture.safe_health(controller)["healthy"])

    def test_successful_resource_close_is_not_repeated_after_publication(self):
        resource, proof = SimpleNamespace(close=Mock()), {}
        capture.close_capture_resource(resource, proof, "collector_closed")
        resource.close.side_effect = ValueError("Second close would fail")
        capture.close_capture_resource(resource, proof, "collector_closed")
        resource.close.assert_called_once()
        self.assertTrue(proof["collector_closed"])

    def test_failed_close_is_not_marked_complete_and_may_be_retried(self):
        resource, proof = SimpleNamespace(close=Mock(side_effect=[TimeoutError(), None])), {}
        with self.assertRaises(TimeoutError):
            capture.close_capture_resource(resource, proof, "shared_sender_closed")
        self.assertNotIn("shared_sender_closed", proof)
        capture.close_capture_resource(resource, proof, "shared_sender_closed")
        self.assertTrue(proof["shared_sender_closed"])

    def test_worker_failure_diagnostic_does_not_relay_body_paths_or_tokens(self):
        record = {"kind": "capture_failed", "error_type": "TimeoutError", "failure_stage": "warmup",
                  "reason": "private native error C:\\Users\\private\\file; Bearer sensitive",
                  "token": "private", "username": "private"}
        self.assertEqual(capture.worker_diagnostic(record),
                         {"kind": "capture_failed", "error_type": "TimeoutError", "failure_stage": "warmup"})
        record["failure_stage"] = "C:\\private"
        self.assertIsNone(capture.worker_diagnostic(record))

    def test_hidden_worker_pipe_is_drained_with_only_allowlisted_json_forwarded(self):
        output = Mock()
        lines = ["Traceback private native message\n", "x" * 20000 + "\n",
                 json.dumps({"kind": "capture_rejected", "error_type": "ValueError", "failure_stage": "warmup",
                             "private": "not-forwarded"}) + "\n",
                 json.dumps({"kind": "replay_exported", "event_count": 2, "attributable_esp_event_count": 1,
                             "output": "C:\\private", "session_id": "private-scope"}) + "\n"]
        self.assertEqual(capture.relay_worker_diagnostics(io.StringIO("".join(lines)), output=output),
                         {"forwarded": 2, "ignored": 2})
        self.assertEqual(output.call_args_list[0].kwargs,
                         {"kind": "capture_rejected", "error_type": "ValueError", "failure_stage": "warmup"})
        self.assertNotIn("C:\\private", repr(output.call_args_list))

    def test_diagnostic_rejects_invalid_scalars_and_sanitizes_sensor_status(self):
        self.assertIsNone(capture.worker_diagnostic({"kind": "esp_verified_off", "timestamp_ms": True}))
        self.assertIsNone(capture.worker_diagnostic({"kind": "unknown", "token": "private"}))
        record = {"kind": "collector_health", "healthy": False, "status": "INSUFFICIENT",
                  "observation_confidence": 40, "sensors": {"sysmon": "unavailable", "secret": "private"}}
        result = capture.worker_diagnostic(record)
        self.assertEqual(result["status"], "INSUFFICIENT")
        self.assertEqual(result["sensors"], {"sysmon": "unavailable"})
    @unittest.skipUnless(capture.os.name == "nt" and sys.prefix != sys.base_prefix, "Windows venv probe only")
    def test_windows_venv_child_pid_is_actual_python_pid(self):
        # Owned stdlib-only process; no Qt/game/Sysmon, API or inherited settings.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = capture.isolated_environment(root, capture.os.environ.get("SystemRoot", r"C:\Windows"), Path(sys.executable))
            code = "import os,sys,json; print(json.dumps({'pid':os.getpid(),'venv':sys.prefix!=sys.base_prefix,'marker_consumed':'__PYVENV_LAUNCHER__' not in os.environ}))"
            command = capture.direct_python_command([sys.executable, "-I", "-B", "-c", code], env)
            child = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                output, _ = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0)
                facts = json.loads(output)
                self.assertEqual(facts["pid"], child.pid)
                self.assertTrue(facts["venv"])
                self.assertTrue(facts["marker_consumed"])
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=6)

    def test_direct_venv_python_preserves_packages_without_pid_redirector(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "base-python.exe"
            base.write_bytes(b"unit-test-placeholder-not-executed")
            runtime = SimpleNamespace(prefix="fixture-venv", base_prefix="fixture-base",
                executable="fixture-venv/python.exe", _base_executable=str(base))
            env = {"OWNED": "fixture"}
            with patch.object(capture, "sys", runtime), patch.object(capture.os, "name", "nt"):
                command = capture.direct_python_command([runtime.executable, "-I", "-B", "fixture.py"], env)
                self.assertEqual(command[0], str(base))
                self.assertEqual(env["__PYVENV_LAUNCHER__"], runtime.executable)
                with self.assertRaises(ValueError):
                    capture.direct_python_command(["unrelated.exe"], {})
            self.assertEqual(env["OWNED"], "fixture")

    def test_direct_python_fails_closed_if_venv_base_is_unavailable(self):
        runtime = SimpleNamespace(prefix="fixture-venv", base_prefix="fixture-base",
            executable="fixture-venv/python.exe", _base_executable="")
        with patch.object(capture, "sys", runtime), patch.object(capture.os, "name", "nt"), self.assertRaises(ValueError):
            capture.direct_python_command([runtime.executable, "fixture.py"], {})

    def test_export_enrichment_failure_never_publishes_partial_labeled_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, run, public = root / "fixture_session", root / "owned-run", root / "public"
            source.mkdir()
            run.mkdir()
            (source / "manifest.json").write_text(json.dumps({"test_metadata": {
                "timing_provenance": {"fixture": True}, "calibration_observation": {"fixture": True,
                    "server_evidence_sha256": capture.hashlib.sha256(b'{\n  "fixture": true\n}\n').hexdigest()}}}), encoding="utf-8")
            def fixture_exporter(session_dir, staging):
                candidate = staging / session_dir.name
                candidate.mkdir()
                (candidate / "manifest.json").write_text(json.dumps({"label": "CHEAT", "source": {}}), encoding="utf-8")
                return candidate
            with patch.object(capture, "write_json", side_effect=OSError("fixture write failure")), self.assertRaises(OSError):
                capture.publish_replay(source, public, run, {"fixture": True}, exporter=fixture_exporter)
            self.assertFalse((public / source.name).exists())
            self.assertTrue((run / "replay-staging" / source.name).exists())

    def test_publication_contains_all_enrichments_and_never_overwrites_existing_session(self):
        for collision in (False, True):
            with self.subTest(collision=collision), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, run, public = root / "fixture_session", root / "owned-run", root / "public"
                source.mkdir()
                run.mkdir()
                (source / "manifest.json").write_text(json.dumps({"test_metadata": {
                    "timing_provenance": {"fixture": True}, "calibration_observation": {"fixture": True,
                        "server_evidence_sha256": capture.hashlib.sha256(b'{\n  "fixture": true\n}\n').hexdigest()}}}), encoding="utf-8")
                def fixture_exporter(session_dir, staging):
                    candidate = staging / session_dir.name
                    candidate.mkdir()
                    (candidate / "manifest.json").write_text(json.dumps({"source": {}}), encoding="utf-8")
                    return candidate
                if collision:
                    existing = public / source.name
                    existing.mkdir(parents=True)
                    (existing / "sentinel.txt").write_text("user fixture preserved", encoding="utf-8")
                    with self.assertRaises(ValueError):
                        capture.publish_replay(source, public, run, {"fixture": True}, exporter=fixture_exporter)
                    self.assertEqual((existing / "sentinel.txt").read_text("utf-8"), "user fixture preserved")
                else:
                    result = capture.publish_replay(source, public, run, {"fixture": True}, exporter=fixture_exporter)
                    self.assertEqual(result, public / source.name)
                    self.assertTrue((result / "calibration-evidence.json").is_file())
                    metadata = json.loads((result / "manifest.json").read_text("utf-8"))
                    annotation = metadata["source"]["calibration_observation"]
                    self.assertTrue(annotation["fixture"])
                    self.assertEqual(annotation["server_evidence_sha256"],
                        capture.hashlib.sha256((result / "calibration-evidence.json").read_bytes()).hexdigest())

    def test_public_server_evidence_hash_mismatch_rejects_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, run, public = root / "fixture_session", root / "owned-run", root / "public"
            source.mkdir()
            run.mkdir()
            (source / "manifest.json").write_text(json.dumps({"test_metadata": {
                "timing_provenance": {}, "calibration_observation": {"server_evidence_sha256": "a" * 64}}}), encoding="utf-8")
            def fixture_exporter(session_dir, staging):
                candidate = staging / session_dir.name
                candidate.mkdir()
                (candidate / "manifest.json").write_text(json.dumps({"source": {}}), encoding="utf-8")
                return candidate
            with self.assertRaises(ValueError):
                capture.publish_replay(source, public, run, {"fixture": True}, exporter=fixture_exporter)
            self.assertFalse((public / source.name).exists())

    def test_failed_owned_proof_replaces_previous_completed_not_skip_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            capture.save_owned_proof(run, {"status": "completed", "fixture": True})
            capture.save_owned_proof(run, {"status": "failed", "fixture": True})
            self.assertEqual(json.loads((run / "lifecycle-proof.json").read_text("utf-8"))["status"], "failed")
            self.assertEqual(len(list(run.iterdir())), 1)

    def test_annotation_rejects_off_after_acquisition_end_and_invalid_spawn_bounds(self):
        proof = {"session_t0_epoch": 100, "handle_open": handle_open(), "ready": ready(), "esp_exit_epoch": 165.777,
                 "status": "validated", "scenario": "esp", "acquisition_end_epoch": 170.,
                 "program_spawn_before_epoch": 100.1, "esp_spawn_epoch": 100.6, "central_telemetry": "off"}
        with tempfile.TemporaryDirectory() as temporary:
            # Child may open before parent Popen returns; do not confuse that bound with ON.
            capture.annotate_completed(Path(temporary), manifest(), proof)
        for update in ({"acquisition_end_epoch": 165}, {"acquisition_end_epoch": float("nan")},
                       {"program_spawn_before_epoch": 101}, {"esp_spawn_epoch": 180}):
            with self.subTest(update=update), tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                capture.annotate_completed(Path(temporary), manifest(), dict(proof, **update))

    def test_remote_geometry_requires_completed_remote_draw_not_local_ready(self):
        record = dict(ready(), kind="esp_remote_paint", completed_paint=True, remote_player_count=1,
            remote_box_lines=4, remote_skeleton_lines=0, remote_player_draw_count=1)
        facts = capture.validate_remote_paint(record, 500, 19064, 100, 105)
        self.assertEqual(facts["remote_box_lines"], 4)
        for updates in ({"completed_paint": False}, {"remote_player_count": 0},
                        {"remote_box_lines": 0}, {"remote_player_draw_count": 0},
                        {"remote_box_lines": True}, {"remote_skeleton_lines": -1},
                        {"pid": 501}, {"observed_at_epoch": float("nan")}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                capture.validate_remote_paint(dict(record, **updates), 500, 19064, 100, 105)

    def test_remote_activity_deduplicates_cached_sequence_and_has_honest_bounds(self):
        proof = {}
        facts = {"observed_at_epoch": 110., "frame_sequence": 7, "remote_box_lines": 4, "remote_skeleton_lines": 2}
        capture.record_remote_activity(proof, facts)
        capture.record_remote_activity(proof, dict(facts, observed_at_epoch=110.5))
        self.assertEqual(proof["remote_activity"]["unique_frame_count"], 1)
        capture.record_remote_activity(proof, dict(facts, frame_sequence=8, observed_at_epoch=111.))
        activity = proof["remote_activity"]
        self.assertEqual((activity["first_observed_epoch"], activity["last_observed_epoch"]), (110., 111.))
        self.assertEqual((activity["unique_frame_count"], activity["remote_box_lines"]), (2, 8))
        self.assertFalse(activity["continuous_visibility_claimed"])
        self.assertFalse(activity["screenshot_verified"])
        self.assertIsNone(activity["user_behavior_start_ms"])
        for update in ({"frame_sequence": 6}, {"frame_sequence": 9, "observed_at_epoch": 109}):
            with self.assertRaises(ValueError):
                capture.record_remote_activity(proof, dict(facts, **update))

    def test_queue_receipt_is_not_ack_and_uuid_is_stable(self):
        event = {"session_id": "test_only", "player_id": "test_player", "module": "esp", "timestamp_ms": 4200,
                 "evidence": {"source_pid": 500}, "reasons": ["Test fixture only"], "raw_score": 2}
        client = SimpleNamespace(send_detection=Mock(), flush=Mock(return_value=True), close=Mock(return_value=True),
            status=Mock(return_value=SimpleNamespace(pending=0, failed=0, acknowledged_this_run=0)))
        with tempfile.TemporaryDirectory() as temporary:
            sink = capture.CalibrationSink({"endpoint": "http://127.0.0.1:8002", "detection_token": "test-fixture-only"},
                Path(temporary), client_factory=lambda config: client)
            self.assertTrue(sink.queue(event, "stable-local-outbox"))
            self.assertTrue(sink.queue(event, "stable-local-outbox"))
            self.assertEqual(len(sink.expected_events), 1)
            self.assertEqual(client.send_detection.call_args_list[0].kwargs, client.send_detection.call_args_list[1].kwargs)
            with self.assertRaises(ValueError): sink.verify_delivery()
            client.status.return_value.acknowledged_this_run = 1
            self.assertEqual(sink.verify_delivery()["queued_unique_events"], 1)
            event["reasons"].append("mutated caller fixture")
            self.assertEqual(next(iter(sink.expected_events.values()))["reasons"], ["Test fixture only"])
            with self.assertRaises(ValueError): sink.queue(event, "stable-local-outbox")
            sink.close()

    def test_delivery_rejects_pending_failed_and_unflushed_without_erasing_outbox(self):
        for pending, failed, flushed in ((1, 0, True), (0, 1, True), (0, 0, False)):
            with self.subTest(pending=pending, failed=failed, flushed=flushed), tempfile.TemporaryDirectory() as temporary:
                client = SimpleNamespace(flush=Mock(return_value=flushed),
                    status=Mock(return_value=SimpleNamespace(pending=pending, failed=failed, acknowledged_this_run=0)))
                sink = capture.CalibrationSink({"endpoint": "http://127.0.0.1:8002", "detection_token": "test-fixture-only"},
                    Path(temporary), client_factory=lambda config: client)
                with self.assertRaises(ValueError): sink.verify_delivery()

    def test_healthy_normal_empty_ledger_does_not_create_zero_event(self):
        client = SimpleNamespace(send_detection=Mock(), flush=Mock(return_value=True),
            status=Mock(return_value=SimpleNamespace(pending=0, failed=0, acknowledged_this_run=0)))
        with tempfile.TemporaryDirectory() as temporary:
            sink = capture.CalibrationSink({"endpoint": "http://127.0.0.1:8002", "detection_token": "test-fixture-only"},
                Path(temporary), client_factory=lambda config: client)
            self.assertEqual(sink.verify_delivery()["queued_unique_events"], 0)
            client.send_detection.assert_not_called()

    def test_failed_enqueue_not_added_to_expected_server_ledger(self):
        from shared.errors import SharedError
        client = SimpleNamespace(send_detection=Mock(side_effect=SharedError("fixture failure")))
        event = {"session_id": "test_only", "player_id": "test_player", "module": "esp", "timestamp_ms": 4200,
                 "evidence": {}, "reasons": ["Test fixture only"], "raw_score": 2}
        with tempfile.TemporaryDirectory() as temporary:
            sink = capture.CalibrationSink({"endpoint": "http://127.0.0.1:8002", "detection_token": "test-fixture-only"},
                Path(temporary), client_factory=lambda config: client)
            self.assertFalse(sink.queue(event, "failed-fixture"))
            self.assertEqual(sink.expected_events, {})

    def test_module_delta_is_not_an_injection_or_integrity_claim(self):
        before = {"status": "OBSERVED", "identities": ["a" * 64]}
        same = capture.module_residue(before, before)
        self.assertEqual(same["status"], "UNCHANGED")
        self.assertEqual(same["attribution"], "NOT_ASSESSED")
        changed = capture.module_residue(before, {"status": "OBSERVED", "identities": ["b" * 64]})
        self.assertEqual(changed["added_identity_sha256"], ["b" * 64])
        self.assertEqual(changed["removed_identity_sha256"], ["a" * 64])
        self.assertEqual(capture.module_residue(before, {"status": "UNKNOWN"})["status"], "UNKNOWN")

    def test_exact_owned_process_residue_never_trusts_reused_pid(self):
        child = SimpleNamespace(pid=500, poll=lambda: 0)
        self.assertEqual(capture.exact_process_residue(child, 90, creation_time_provider=lambda pid: 91)["status"],
                         "PID_REUSED_NOT_OWNED")
        self.assertEqual(capture.exact_process_residue(child, 90, creation_time_provider=lambda pid: None)["status"], "ABSENT")
        self.assertEqual(capture.exact_process_residue(child, 90, creation_time_provider=lambda pid: 90)["status"], "ABSENT")
        child.poll = lambda: None
        self.assertEqual(capture.exact_process_residue(child, 90, creation_time_provider=lambda pid: None)["status"], "UNKNOWN")
        self.assertEqual(capture.exact_process_residue(child, 90, creation_time_provider=lambda pid: 90)["status"], "PRESENT")

    def test_central_annotation_requires_actual_readback_not_queued_flag(self):
        proof = {"session_t0_epoch": 100, "handle_open": handle_open(), "ready": ready(), "esp_exit_epoch": 165.777,
                 "status": "validated", "scenario": "esp", "central_telemetry": "disposable_loopback"}
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            capture.annotate_completed(Path(temporary), manifest(), proof)

    def test_required_remote_annotation_cannot_promote_local_read_only(self):
        proof = {"session_t0_epoch": 100, "handle_open": handle_open(), "ready": ready(), "esp_exit_epoch": 165.777,
                 "status": "validated", "scenario": "esp", "remote_rendering_required": True}
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            capture.annotate_completed(Path(temporary), manifest(), proof)

    def test_calibration_annotation_preserves_utc_and_distinct_activity_bounds(self):
        document = manifest()
        proof = {"session_t0_epoch": 100, "handle_open": handle_open(), "ready": ready(), "esp_exit_epoch": 165.777,
                 "status": "validated", "scenario": "esp", "acquisition_end_epoch": 170.,
                 "program_spawn_before_epoch": 100.1, "esp_spawn_epoch": 100.2,
                 "central_telemetry": "off", "remote_rendering_required": True}
        capture.record_remote_activity(proof, {"observed_at_epoch": 110., "frame_sequence": 7,
            "remote_box_lines": 4, "remote_skeleton_lines": 0})
        capture.record_remote_activity(proof, {"observed_at_epoch": 111., "frame_sequence": 8,
            "remote_box_lines": 4, "remote_skeleton_lines": 0})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            capture.annotate_completed(directory, document, proof)
            meta = json.loads((directory / "manifest.json").read_text("utf-8"))["test_metadata"]
            self.assertEqual(meta["calibration_observation"]["program_spawn_bounds_ms"], [100, 200])
            self.assertEqual(meta["calibration_observation"]["actual_remote_geometry"]["first_observed_ms"], 10000)
            self.assertEqual(meta["calibration_observation"]["actual_remote_geometry"]["last_observed_ms"], 11000)
            self.assertEqual(meta["cheat_off_ms"], 65777)
            self.assertTrue(meta["calibration_observation"]["session_start_utc"].endswith("Z"))
            self.assertIsNone(meta["calibration_observation"]["human_behavior_start_ms"])
            self.assertEqual(meta["calibration_observation"]["preexisting_poc_absence"], "NOT_ASSESSED")

    def test_missing_native_preconditions_reject_before_output_spawn_or_network(self):
        args = ["--game-pid", "19064", "--approved-private-test-room", "--server-setup", "never-read.json"]
        for problem in ("game absent", "administrator absent", "Sysmon absent"):
            with self.subTest(problem=problem), patch.object(capture, "platform_preflight", autospec=True, side_effect=ValueError(problem)), \
                    patch.object(capture, "write_json") as writes, patch.object(capture.subprocess, "Popen") as spawn, \
                    patch("server.dashboard_backend.calibration_export.create_http") as connect:
                with self.assertRaises(ValueError): capture.main(args)
                writes.assert_not_called()
                spawn.assert_not_called()
                connect.assert_not_called()

    def test_environment_contains_only_new_profile_and_os_basics(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(capture.os.environ, {"LIVE_SECRET": "do-not-use", "HTTP_PROXY": "remote", "GZZ_TELEMETRY_TOKEN": "old"}):
                result = capture.isolated_environment(Path(temporary), str(Path(temporary).resolve()), Path(sys.executable))
            self.assertNotIn("LIVE_SECRET", result)
            self.assertNotIn("HTTP_PROXY", result)
            self.assertNotIn("GZZ_TELEMETRY_TOKEN", result)
            self.assertEqual(Path(result["USERPROFILE"]), Path(temporary) / "profile")
            self.assertTrue(Path(result["TEMP"]).is_dir())

    def test_game_guard_rejects_multiple_wrong_or_reused_processes(self):
        game = SimpleNamespace(pid=19064, image_path=capture.GAME_EXE, created_at=100.)
        self.assertIs(capture.select_game([game], 19064, 100), game)
        for values, pid, birth in (([], 19064, 100), ([game, game], 19064, 100), ([game], 8, 100), ([game], 19064, 101)):
            with self.subTest(values=values, pid=pid, birth=birth), self.assertRaises(ValueError):
                capture.select_game(values, pid, birth)

    def test_readiness_requires_actual_typed_fresh_local_frame_and_visible_overlay(self):
        module = SimpleNamespace(FrameRenderSnapshot=Frame, CameraSnapshot=Camera, PlayerRenderSnapshot=Player)
        def facts(frame, visible=True):
            overlay = SimpleNamespace(_snapshots=SimpleNamespace(latest=lambda: frame), isVisible=lambda: visible,
                config=SimpleNamespace(enabled=True), STALE_AFTER_SECONDS=0.5)
            return capture.verified_frame(module, overlay, 10.02)
        self.assertEqual(facts(Frame())["remote_player_count"], 0)
        for frame in (object(), replace(Frame(), players=()), replace(Frame(), camera=None),
                      replace(Frame(), started_at=9.), replace(Frame(), error="read failed"),
                      replace(Frame(), stats=(("collection_valid", False),)),
                      replace(Frame(), camera=Camera(fov=float("nan")))):
            self.assertIsNone(facts(frame))
        self.assertIsNone(facts(Frame(), visible=False))

    def test_handshake_rejects_process_only_or_fabricated_values(self):
        self.assertEqual(capture.validate_ready(ready(), 500, 19064, 100, 105)["local_player_count"], 1)
        for updates in ({"local_player_count": 0}, {"local_player_count": True}, {"camera_valid": False},
                        {"overlay_visible": False}, {"pid": 501}, {"source_sha256": "b" * 64},
                        {"observed_at_epoch": 99}, {"collection_ms": float("nan")}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                capture.validate_ready(dict(ready(), **updates), 500, 19064, 100, 105)

    def test_timing_uses_actual_observed_values_and_rejects_bad_order(self):
        self.assertEqual(capture.measured_timings(100, 104.123, 165.777), (4123, 65777))
        for values in ((100, 99, 165), (100, 105, 104), (100, float("nan"), 165)):
            with self.assertRaises(ValueError):
                capture.measured_timings(*values)

    def test_onset_is_successful_handle_opening_separate_from_first_read_ready(self):
        proof = {"session_t0_epoch": 100, "handle_open": handle_open(), "ready": ready(), "esp_exit_epoch": 165.777}
        self.assertEqual(capture.lifecycle_timings(proof), (400, 4123, 65777))
        with self.assertRaises(ValueError):
            capture.lifecycle_timings(dict(proof, handle_open=dict(handle_open(), observed_at_epoch=105)))
        with self.assertRaises(KeyError):
            capture.lifecycle_timings({key: value for key, value in proof.items() if key != "handle_open"})

    def test_handle_open_requires_exact_identity_success_and_enabled_original_config(self):
        self.assertEqual(capture.validate_handle_open(handle_open(), 500, 19064, 90, 100, 105)["observed_at_epoch"], 100.4)
        for changes in ({"pid": 501}, {"game_pid": 5}, {"game_created_at": 91}, {"birth_verified": False},
                        {"source_config_enabled": False}, {"opening_started_epoch": 100.5},
                        {"observed_at_epoch": float("nan")}, {"source_sha256": "b" * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                capture.validate_handle_open(dict(handle_open(), **changes), 500, 19064, 90, 100, 105)

    def test_cleanup_waits_only_own_child_and_falls_back_if_needed(self):
        class Child:
            def __init__(self): self.waits, self.kills = 0, 0
            def wait(self, timeout):
                self.waits += 1
                if self.waits == 1: raise subprocess.TimeoutExpired("owned", timeout)
            def kill(self): self.kills += 1
        with tempfile.TemporaryDirectory() as temporary:
            child = Child()
            _, forced = capture.stop_own_child(child, Path(temporary) / "stop")
            self.assertTrue(forced)
            self.assertEqual((child.waits, child.kills), (2, 1))

    def test_job_creation_failure_still_terminates_exact_spawned_worker(self):
        child = SimpleNamespace(pid=999, kill=Mock(), wait=Mock(return_value=0))
        api = SimpleNamespace(CloseHandle=Mock())
        job = SimpleNamespace(CreateJobObject=Mock(side_effect=TypeError("invalid name")))
        with patch.dict(sys.modules, {"win32api": api, "win32job": job}), self.assertRaises(TypeError):
            capture.own_process_job(child)
        job.CreateJobObject.assert_called_once_with(None, "")
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=6)
        api.CloseHandle.assert_not_called()

    def test_job_assignment_failure_closes_handles_and_terminates_own_worker(self):
        child = SimpleNamespace(pid=999, kill=Mock(), wait=Mock(return_value=0))
        api = SimpleNamespace(CloseHandle=Mock(), OpenProcess=Mock(return_value="owned-process"))
        job = SimpleNamespace(CreateJobObject=Mock(return_value="owned-job"), JobObjectExtendedLimitInformation=9,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0x2000,
            QueryInformationJobObject=Mock(return_value={"BasicLimitInformation": {"LimitFlags": 0}}),
            SetInformationJobObject=Mock(), AssignProcessToJobObject=Mock(side_effect=OSError("assignment failed")))
        with patch.dict(sys.modules, {"win32api": api, "win32job": job}), self.assertRaises(OSError):
            capture.own_process_job(child)
        self.assertEqual([call.args for call in api.CloseHandle.call_args_list], [("owned-process",), ("owned-job",)])
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=6)

    def test_warmup_settings_align_actual_controller_and_writer_identity(self):
        from anti_esp.config import Settings, TelemetrySettings
        from anti_esp.controller import AntiEspController
        from anti_esp.core.events import SensorBatch, SensorEvent
        from anti_esp.core.session import SessionTelemetryWriter
        from anti_esp.windows_api import ProcessInfo
        class FixtureSensor:
            def poll(self, context):
                event = SensorEvent(session_id=context.session_id, sensor_id="contract_fixture",
                    event_type="contract_fixture", subject_id="fixture_only", payload={"synthetic": True}, timestamp_ms=100000)
                return SensorBatch("contract_fixture", "online", (event,))
            def reset_cooldowns(self): pass
            def reset_baseline(self): pass
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            settings = Settings(database_path=directory / "candidate.sqlite3", telemetry=TelemetrySettings(
                enabled=True, root=directory / "sessions", session_id="candidate_only", player_id="fixture_player"))
            warmup_settings = capture.warmup_settings(settings, directory, "candidate_only_warmup")
            self.assertEqual(settings.telemetry.session_id, "candidate_only")
            writer = SessionTelemetryWriter(directory / "warmup", session_id="candidate_only_warmup",
                game_executable=capture.GAME_EXE, player_id="fixture_player", test_metadata={"scenario": "unvalidated_warmup"})
            with patch.dict(capture.os.environ, {"LOCALAPPDATA": str(directory / "profile")}, clear=True):
                controller = AntiEspController(warmup_settings, telemetry_writer=writer, poller=SimpleNamespace(),
                    process_access_sensor=FixtureSensor(), module_sensor=FixtureSensor(), window_sensor=FixtureSensor(),
                    handle_sensor=FixtureSensor(), elevation_provider=lambda: True, anticheat_pid_provider=lambda: (),
                    process_provider=lambda _: [ProcessInfo(19064, capture.GAME_EXE, 90.)], clock=lambda: 100.)
                try:
                    self.assertEqual(controller.session_id, writer.session_id)
                    controller.poll_once()  # Real persistence rejects mismatched session IDs here.
                finally:
                    controller.close()
            document = json.loads(writer.manifest_path.read_text("utf-8"))
            self.assertEqual(document["session_id"], "candidate_only_warmup")
            self.assertEqual(document["raw_event_count"], 2)
            self.assertEqual(document["test_metadata"]["scenario"], "unvalidated_warmup")

    def test_capture_requires_healthy_coverage_and_attributed_genuine_event(self):
        document = manifest()
        event = {"session_id": "test_only", "player_id": "test_player", "module": "esp", "timestamp_ms": 4200,
            "evidence": {"source_pid": 500}, "reasons": ["Test fixture only"], "raw_score": 2}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            def validate():
                (directory / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
                (directory / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
                return capture.validate_capture(directory, "test_only", "test_player", "esp", 500, 19064, 90.)
            self.assertEqual(validate()[1], 1)
            original_instance = document["observation_summary"]["game_instance_sha256"]
            document["observation_summary"]["game_instance_sha256"] = "a" * 64
            with self.assertRaises(ValueError): validate()
            document["observation_summary"]["game_instance_sha256"] = original_instance
            event["evidence"]["source_pid"] = 501
            with self.assertRaises(ValueError): validate()
            event["evidence"]["source_pid"] = 500
            event["evidence"]["synthetic"] = True
            with self.assertRaises(ValueError): validate()
            event["evidence"].pop("synthetic")
            document["observation_summary"]["required_sensors"]["handles"]["online_observed_count"] = 0
            with self.assertRaises(ValueError): validate()

    def test_completed_annotation_changes_only_metadata_and_retains_event_bytes(self):
        document = manifest()
        proof = {"session_t0_epoch": 100, "handle_open": handle_open(), "ready": ready(), "esp_exit_epoch": 165.777,
                 "status": "validated", "scenario": "esp"}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
            events = b"fixture-bytes-never-replay-data\n"
            (directory / "events.jsonl").write_bytes(events)
            capture.annotate_completed(directory, document, proof)
            result = json.loads((directory / "manifest.json").read_text("utf-8"))
            self.assertEqual(result["test_metadata"]["cheat_on_ms"], 400)
            self.assertEqual(result["test_metadata"]["first_read_ready_ms"], 4123)
            self.assertEqual(result["test_metadata"]["cheat_off_ms"], 65777)
            self.assertFalse(result["test_metadata"]["timing_provenance"]["remote_rendering_claimed"])
            self.assertEqual(result["test_metadata"]["scenario"], "esp")
            self.assertEqual((directory / "events.jsonl").read_bytes(), events)
            for key in document:
                if key != "test_metadata": self.assertEqual(result[key], document[key])
            with self.assertRaises(ValueError):
                capture.annotate_completed(directory, dict(document, status="failed"), proof)

    def test_normal_annotation_cannot_contain_esp_lifecycle(self):
        document = manifest()
        document["test_metadata"]["requested_scenario"] = "normal"
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            capture.annotate_completed(Path(temporary), document, {"ready": ready(), "esp_exit_epoch": 165, "session_t0_epoch": 100,
                                                                  "status": "validated", "scenario": "normal"})

    def test_pending_or_failed_proof_cannot_promote_candidate_to_cheat(self):
        with tempfile.TemporaryDirectory() as temporary:
            for status in (None, "failed", "completed"):
                with self.subTest(status=status), self.assertRaises(ValueError):
                    capture.annotate_completed(Path(temporary), manifest(), {"status": status})

    def test_private_room_ack_is_required_before_preflight_or_writes(self):
        with patch.object(capture, "platform_preflight") as preflight, patch.object(capture, "write_json") as writes:
            for role in ([], ["--worker"], ["--esp-child"]):
                with self.subTest(role=role), self.assertRaises(ValueError):
                    capture.main(["--game-pid", "19064", *role])
            preflight.assert_not_called()
            writes.assert_not_called()

    def test_stage_diagnostic_contains_only_function_name(self):
        def fixture_function():
            raise OSError("sensitive-native-path-must-not-be-used")
        with self.assertRaises(OSError) as error:
            capture.checked_call(fixture_function)
        self.assertEqual(error.exception.capture_failure_stage, "fixture_function")


if __name__ == "__main__":
    unittest.main()
