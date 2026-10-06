"""Hardware-free safety/lifecycle tests for the real-game Launcher wrapper."""
import importlib.util
import json
import os
import sys
from pathlib import Path
import tempfile
from hashlib import sha256
from dataclasses import dataclass, field
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("realgame_e2e_under_test", Path(__file__).parents[1] / "realgame_e2e.py")
e2e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e2e)
GAME = {"pid": 19064, "create_time": int((11_644_473_600 + 100) * 10_000_000)}


def manifest_document():
    return {"schema_version": "meccha.telemetry-session.v1", "status": "completed", "failure_reason": None,
            "session_id": "normal_realgame_test", "player_id": "local_e2e", "game_executable": e2e.GAME_EXE,
            "producer": {"name": "meccha-esp-localguard", "version": "0.2.0", "pid": 102,
                         "observation_contract": "meccha.esp-observation.v1"},
            "observation_summary": {"schema_version": "meccha.esp-observation.v1",
                "session_id": "normal_realgame_test", "player_id": "local_e2e", "poll_count": 2,
                "healthy_poll_count": 2, "insufficient_poll_count": 0, "last_status": "LOW",
                "first_poll_ms": 0, "last_poll_ms": 1000, "minimum_observation_confidence": 60.0,
                "min_observation_confidence": 100.0, "last_observation_confidence": 100.0,
                "game_instance_sha256": sha256(b"normal_realgame_test\00019064\000100.000000000").hexdigest(),
                "game_instance_changed": False, "game_instance_missing_poll_count": 0, "timestamp_regression_count": 0,
                "required_sensors": {name: {"poll_count": 2, "online_poll_count": 2, "observed_count": 1,
                                            "online_observed_count": 1, "last_status": "online"}
                                     for name in e2e.ESP_SENSORS}}}


def coverage(path):
    return e2e.observation_result(path, session="normal_realgame_test", player="local_e2e", game=GAME, esp_pid=102)


def setup_document():
    run_id = "123456abcdef"
    return {"result": "READY", "run_id": run_id, "port": 8002, "endpoint": "http://127.0.0.1:8002",
            "tokens_are_disposable_test_literals": True, "empty_start_verified": True, "seeded_events": 0,
            "test_tokens": {name: f"synthetic-browser-{kind}-{run_id}" for name, kind in (
                ("GZZ_TELEMETRY_TOKEN", "receiver"), ("MECCHA_HEARTBEAT_TOKEN", "heartbeat"),
                ("GZZ_DASHBOARD_TOKEN", "dashboard"))}}


@dataclass
class FakeModule:
    name: str
    argv: list
    session_log_dir: str = "old"
    env: dict = field(default_factory=dict)
    optional_paths: list = field(default_factory=list)


def catalog():
    return [FakeModule("module_integrity", ["python", "-m", "module.runner", "--output", "old.jsonl"]),
            FakeModule("external_access", ["python", "-m", "access.runner", "--output", "old.jsonl"]),
            FakeModule("esp", ["python", "esp/run.py", "--headless"]),
            FakeModule("hide_anywhere", ["python", "hide/mecha_logger.py", "--out", "old"], env={"PYTHONPATH": "repo"}),
            FakeModule("unselected", ["python", "never.py"])]


class RealgameSafetyTests(unittest.TestCase):
    def test_setup_rejects_remote_origin_live_tokens_and_seeded_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "setup.json"
            for mutate in (
                lambda value: value.update(endpoint="https://remote.example:8002"),
                lambda value: value["test_tokens"].update(GZZ_TELEMETRY_TOKEN="live-secret"),
                lambda value: value.update(seeded_events=1),
                lambda value: value.update(port=8003),
            ):
                value = setup_document()
                mutate(value)
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    e2e.read_setup(path)

    def test_environment_uses_only_windows_basics_and_disposable_test_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "setup.json"
            path.write_text(json.dumps(setup_document()), encoding="utf-8")
            environment = e2e.isolated_environment(Path(directory), e2e.read_setup(path),
                {"SystemRoot": "Windows", "PATH": "tools", "GZZ_TELEMETRY_TOKEN": "private",
                 "GZZ_TELEMETRY_URL": "https://remote.example", "HTTP_PROXY": "private-proxy",
                 "PYTHONPATH": "injection", "MECCHA_GUARD_IDENTITY_PEPPER": "private"})
            self.assertEqual(environment["GZZ_TELEMETRY_URL"], "http://127.0.0.1:8002")
            self.assertNotIn("private", environment.values())
            self.assertNotIn("HTTP_PROXY", environment)
            self.assertNotIn("PYTHONPATH", environment)
            self.assertNotIn("MECCHA_GUARD_IDENTITY_PEPPER", environment)
            self.assertNotIn("GZZ_DASHBOARD_TOKEN", environment)
            self.assertEqual(environment["LOCALAPPDATA"], str(Path(directory) / "profile" / "localappdata"))
            self.assertEqual(environment["USERPROFILE"], str(Path(directory) / "profile"))

    def test_disabled_production_identity_constructs_under_owned_profile_without_collecting(self):
        module_name = "realgame_disabled_identity_under_test"
        identity_spec = importlib.util.spec_from_file_location(module_name,
            e2e.REPO / "client/detectors/esp/anti_esp/sensors/identity.py")
        identity = importlib.util.module_from_spec(identity_spec)
        sys.modules[module_name] = identity
        try:
            identity_spec.loader.exec_module(identity)
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                environment = e2e.isolated_environment(output, {"endpoint": "http://127.0.0.1:8002",
                    "detection_token": "test", "heartbeat_token": "test"}, source={})
                with patch.object(identity.os, "environ", environment), \
                        patch.object(identity.Path, "home", side_effect=RuntimeError("no live home")), \
                        patch.object(identity.FileInstallationSecret, "get", side_effect=AssertionError("secret accessed")), \
                        patch.object(identity.WindowsEndpointIdentifierProvider, "collect", side_effect=AssertionError("hardware accessed")):
                    observation = identity.PseudonymousIdentity(enabled=False).generate()
                    self.assertEqual(observation.status, "disabled")
                    self.assertEqual(identity.default_installation_secret_path(),
                        output / "profile/localappdata/MecchaLocalGuard/identity.secret")
                self.assertFalse((output / "profile").exists())
        finally:
            sys.modules.pop(module_name, None)

    def test_module_copies_override_only_new_runtime_paths(self):
        original = catalog()
        selected = e2e.selected_modules(original, Path("C:/new-owned-runtime"))
        self.assertEqual([module.name for module in selected], list(e2e.MODULE_NAMES))
        self.assertEqual(original[0].argv[-1], "old.jsonl")
        self.assertEqual(original[3].argv[-1], "old")
        self.assertIn("--sysmon", selected[3].argv)
        self.assertIn("--config", selected[2].argv)
        self.assertEqual(selected[2].argv[selected[2].argv.index("--scenario") + 1], "normal")
        for module in selected:
            self.assertIn("new-owned-runtime", module.session_log_dir)

    def test_failed_preflight_leaves_output_absent_and_never_imports_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            setup = root / "setup.json"
            setup.write_text(json.dumps(setup_document()), encoding="utf-8")
            output = root / "must-not-exist"
            with patch.object(e2e.importlib, "import_module", side_effect=AssertionError("Launcher imported")):
                code = e2e.main(["--setup", str(setup), "--output", str(output),
                                 "--session", "normal_realgame_test", "--game-pid", "19064"],
                                preflight=lambda _pid: (_ for _ in ()).throw(ValueError("not elevated")))
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())

    def test_existing_output_is_never_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(e2e, "read_setup", side_effect=AssertionError("read setup")):
                self.assertEqual(e2e.main(["--setup", "unused", "--output", directory,
                                           "--session", "normal_realgame_test", "--game-pid", "19064"]), 2)

    def test_modern_filetime_fingerprint_uses_production_float_conversion(self):
        game = {"pid": 19064, "create_time": 134040288141234567}
        created_at = game["create_time"] / 10_000_000.0 - 11_644_473_600.0
        self.assertNotEqual(created_at, game["create_time"] / 10_000_000 - 11_644_473_600)
        value = manifest_document()
        value["observation_summary"]["game_instance_sha256"] = sha256(
            f"normal_realgame_test\0{game['pid']}\0{created_at:.9f}".encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            proof = e2e.observation_result(path, session="normal_realgame_test",
                player="local_e2e", game=game, esp_pid=102)
            self.assertTrue(proof["profile_identity_valid"])
            self.assertEqual(proof["coverage"], "HEALTHY")

    def test_insufficient_esp_is_never_reported_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps({"observation_summary": {"healthy_poll_count": 1,
                "insufficient_poll_count": 0, "last_status": "INSUFFICIENT", "game_instance_changed": False,
                "game_instance_missing_poll_count": 0, "timestamp_regression_count": 0}}), encoding="utf-8")
            self.assertEqual(coverage(path)["coverage"], "INSUFFICIENT")

    def test_coverage_requires_exact_profile_identity_confidence_and_observed_sensors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for mutate in (
                lambda value: value.update(player_id="different"),
                lambda value: value["producer"].update(pid=999),
                lambda value: value["observation_summary"].update(game_instance_sha256="0" * 64),
                lambda value: value["observation_summary"].update(min_observation_confidence=59),
                lambda value: value["observation_summary"]["required_sensors"]["sysmon"].update(online_observed_count=0),
                lambda value: value["observation_summary"]["required_sensors"]["privilege"].update(last_status="unavailable"),
            ):
                value = manifest_document()
                mutate(value)
                path.write_text(json.dumps(value), encoding="utf-8")
                self.assertEqual(coverage(path)["coverage"], "INSUFFICIENT")

    def test_manifest_summary_does_not_copy_arbitrary_fields_or_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            value = manifest_document()
            value["observation_summary"].update(raw_private="private-marker", last_status=["private-marker"])
            value["observation_summary"]["required_sensors"]["sysmon"].update(observed_count=None, raw="private-marker")
            path.write_text(json.dumps(value), encoding="utf-8")
            result = coverage(path)
            self.assertEqual(result["coverage"], "INSUFFICIENT")
            self.assertNotIn("private-marker", json.dumps(result))

    def test_completed_requires_observed_running_collectors_and_clean_zero_exits(self):
        def ready():
            return {"launcher_exit_code": 0, "collectors_remaining": [], "game_after": {"same_instance": True},
                    "all_four_running_snapshot_count": 1, "stop_reason": "duration_elapsed", "esp": {"coverage": "HEALTHY"},
                    "cleanup": {"graceful": list(e2e.MODULE_NAMES), "forced": [], "unsignaled": []},
                    "terminal_modules": [{"name": name, "status": "STOPPED", "last_code": 0}
                                         for name in e2e.MODULE_NAMES]}
        self.assertTrue(e2e.lifecycle_complete(ready()))
        for mutate in (
            lambda value: value.update(all_four_running_snapshot_count=0),
            lambda value: value["terminal_modules"][0].update(status="FAILED"),
            lambda value: value["terminal_modules"][0].update(last_code=1),
            lambda value: value["terminal_modules"].pop(),
            lambda value: value["cleanup"].update(forced=["esp"]),
            lambda value: value["esp"].update(coverage="INSUFFICIENT"),
        ):
            value = ready()
            mutate(value)
            self.assertFalse(e2e.lifecycle_complete(value))

    def test_duration_uses_production_main_finally_and_captures_registry_before_cleanup(self):
        state = {"session_id": "normal_realgame_test", "launcher_pid": 9, "launcher_create_time": 90,
                 "entries": {name: {"pid": index + 100, "create_time": index + 1000}
                             for index, name in enumerate(e2e.MODULE_NAMES)}}
        cleaned = []

        class Manager:
            def __init__(self, modules):
                self.modules = modules
                self.stopped = False
                self.states = {module.name: SimpleNamespace(pid=index + 100)
                               for index, module in enumerate(modules)}
            def poll(self):
                pass
            def snapshot(self):
                return [{"name": module.name, "status": "STOPPED" if self.stopped else "RUNNING",
                         "last_code": 0 if self.stopped else None} for module in self.modules]
            def stop_all(self):
                cleaned.append(True)
                state["entries"] = {}
                self.stopped = True
                return {"graceful": list(e2e.MODULE_NAMES), "forced": [], "unsignaled": []}

        launcher = SimpleNamespace(MODULES=catalog(), ProcessManager=Manager, _prepare_ue4ss=lambda: "never",
            registry=SimpleNamespace(load=lambda: state, create_time=lambda pid: GAME["create_time"] if pid == 19064 else pid + 900,
                                     is_alive=lambda *_: bool(state["entries"])),
            game_launcher=SimpleNamespace(find_game_pid=lambda: 19064))

        def production_main(arguments):
            self.assertIn("--no-launch-game", arguments)
            self.assertIn("--no-game-path-prompt", arguments)
            self.assertIsNone(launcher._prepare_ue4ss())
            manager = launcher.ProcessManager(launcher.MODULES)
            try:
                manager.poll()
            except KeyboardInterrupt:
                pass
            finally:
                manager.stop_all()
            return 0

        launcher.main = production_main
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = output / "esp/sessions/normal_realgame_test/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps(manifest_document()), encoding="utf-8")
            args = SimpleNamespace(duration=1, session="normal_realgame_test", player="local_e2e", game_pid=19064)
            with patch.object(e2e.importlib, "import_module", return_value=launcher), \
                    patch.object(e2e.time, "monotonic", side_effect=[0, 2]):
                code = e2e.run_launcher(args, {"run_id": "123456abcdef"}, output, GAME)
            self.assertEqual(code, 0)
            self.assertEqual(cleaned, [True])
            proof = json.loads((output / "registry-before-stop.json").read_text(encoding="utf-8"))
            self.assertEqual(set(proof["entries"]), set(e2e.MODULE_NAMES))
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["result"], "LIFECYCLE_VERIFIED")
            self.assertEqual(summary["result_scope"], "launcher_lifecycle_and_esp_observation")
            self.assertEqual(summary["all_detector_functional_e2e"], "NOT_ASSESSED")
            self.assertEqual(summary["stop_reason"], "duration_elapsed")
            self.assertEqual(summary["cleanup"]["forced"], [])


if __name__ == "__main__":
    unittest.main()
