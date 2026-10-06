"""Pure safety/provenance tests; no game, Sysmon, Qt or real replay is run."""
from dataclasses import dataclass, replace
import importlib.util
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
