"""Synthetic contract tests; no real Launcher, game, process kills or central server."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "client" / "SelfDefense" / "watchdog" / "main.py").is_file():
    from client.SelfDefense.watchdog.launcher_adapter import LauncherRegistry, RegistryError
    from client.SelfDefense.watchdog.monitor import Watchdog, Observation
    from client.SelfDefense.watchdog.reporting import SessionLog, Reporter
    ENTRY = ROOT / "client" / "SelfDefense" / "watchdog" / "main.py"
else:
    sys.path.insert(0, str(ROOT))  # ZIP root or installed watchdog/tests.
    from launcher_adapter import LauncherRegistry, RegistryError
    from monitor import Watchdog, Observation
    from reporting import SessionLog, Reporter
    ENTRY = ROOT / "main.py"
import shared
from shared.schema import decode_event

REPORTING = Reporter.__module__
SHARED_ROOT = Path(shared.__file__).resolve().parent.parent


class FakeRegistry:
    def __init__(self, names=("module_a",), status="alive", pid=123):
        self.names, self.status, self.pid = names, status, pid
        self.calls = []

    def restartable_names(self):
        return self.names

    def restart_if_dead(self, name, *, by):
        self.calls.append((name, by))
        return self.status, self.pid, {"untrusted_detail": "never logged"}


class WatchdogTests(unittest.TestCase):
    def test_delegates_only_registry_targets_excludes_self_and_autopaint(self):
        registry = FakeRegistry(("module_a", "SelfDefense", "autopaint"))
        result = Watchdog(registry).poll()
        self.assertEqual(registry.calls, [("module_a", "watchdog")])
        self.assertEqual([x.status for x in result], ["alive", "alive", "skip", "skip"])
        self.assertFalse(any(x.report for x in result))

    def test_no_oneshot_discovery_or_spawning_outside_registry(self):
        registry = FakeRegistry(())
        Watchdog(registry).poll()
        self.assertEqual(registry.calls, [])

    def test_all_registry_statuses_and_error_transition_deduplication(self):
        registry = FakeRegistry()
        watchdog = Watchdog(registry)
        for state in ("alive", "backoff", "stopping", "skip"):
            registry.status = state
            self.assertFalse(watchdog.poll()[-1].report)
        for state in ("gave_up", "orphaned"):
            registry.status, registry.pid = state, None
            self.assertTrue(watchdog.poll()[-1].report)
            self.assertFalse(watchdog.poll()[-1].report)
        registry.status, registry.pid = "alive", 234
        self.assertTrue(watchdog.poll()[-1].report)  # Recovery.
        self.assertFalse(watchdog.poll()[-1].report)

    def test_restart_action_is_reported_each_time_not_suppressed_as_stable_state(self):
        watchdog = Watchdog(FakeRegistry(status="restarted"))
        self.assertTrue(watchdog.poll()[-1].report)
        self.assertTrue(watchdog.poll()[-1].report)

    def test_invalid_names_result_in_no_restart_calls(self):
        for names in ("module_a", {"a": 1}, ["a", "A"], [None], [""]):
            registry = FakeRegistry(names)
            result = Watchdog(registry).poll()
            self.assertEqual(result[0].error_code, "REGISTRY_INVALID_NAMES")
            self.assertEqual(registry.calls, [])

    def test_invalid_result_is_not_normal(self):
        for answer in (("surprise", 1, None), ("alive", None, None), ("alive", True, None), ("alive", 1)):
            registry = FakeRegistry()
            registry.restart_if_dead = lambda *a, **k: answer
            record = Watchdog(registry).poll()[-1]
            self.assertEqual((record.status, record.error_code), ("error", "REGISTRY_INVALID_RESULT"))

    def test_per_module_exception_does_not_stop_other_checks_or_leak_text(self):
        registry = FakeRegistry(("bad", "good"))
        def restart(name, **kwargs):
            if name == "bad":
                raise RuntimeError("SECRET")
            return "alive", 3, None
        registry.restart_if_dead = restart
        result = Watchdog(registry).poll()
        self.assertEqual(result[-1].status, "alive")
        self.assertEqual(result[1].error_code, "REGISTRY_CALL_FAILED")
        self.assertNotIn("SECRET", str(result))

    def test_missing_registry_is_reported_once_and_recovers(self):
        registry = FakeRegistry()
        watchdog = Watchdog(registry)
        with patch.object(registry, "restartable_names", side_effect=RegistryError("REGISTRY_UNAVAILABLE")):
            self.assertTrue(watchdog.poll()[0].report)
            self.assertFalse(watchdog.poll()[0].report)
        self.assertTrue(watchdog.poll()[0].report)


class SelfDefenseFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="selfdefense-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def log(self, **kwargs):
        return SessionLog(self.root, "normal_001", "player_042", **kwargs)

    def test_clock_and_logs_survive_same_session_restart_without_overwrite(self):
        start = time.time_ns() // 1_000_000 - 10000
        first = self.log(start_unix_ms=start)
        first.write_event(first.event(Observation("a", "orphaned", report=True), first.elapsed_ms()))
        content = (first.directory / "events.jsonl").read_bytes()
        second = self.log(start_unix_ms=start)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertGreaterEqual(second.elapsed_ms(), 10000)
        self.assertEqual((first.directory / "events.jsonl").read_bytes(), content)
        self.assertEqual(second.basis, "launcher_session_start")

    def test_local_clock_is_reused_but_conflicting_identity_clock_or_demo_is_rejected(self):
        first = self.log()
        second = self.log()
        self.assertGreaterEqual(second.elapsed_ms(), first._base_ms)
        with self.assertRaises(ValueError):
            self.log(start_unix_ms=1)
        with self.assertRaises(ValueError):
            SessionLog(self.root, "normal_001", "different_player")
        with self.assertRaises(ValueError):
            self.log(synthetic=True)

    def test_corrupt_clock_is_not_overwritten(self):
        self.log()
        clock = self.root / "normal_001" / "session-clock.json"
        clock.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.log()
        self.assertEqual(clock.read_text(), "broken")

    def test_events_are_seven_field_operational_not_detection_scores(self):
        log = self.log(synthetic=True)
        reporter = Reporter(log, "off")
        with contextlib.redirect_stderr(io.StringIO()):
            reporter.start()
            reporter.record(Observation("a", "alive"))
            reporter.record(Observation("a", "orphaned", report=True))
            reporter.close()
        values = [decode_event(line) for line in (log.directory / "events.jsonl").read_bytes().splitlines()]
        self.assertEqual(len(values), 1)
        value = values[0]
        self.assertEqual((value["module"], value["raw_score"]), ("selfdefense", 0))
        self.assertEqual(value["evidence"]["status"], "ERROR")
        self.assertTrue(value["evidence"]["synthetic"])
        self.assertEqual(len((log.directory / "raw" / "watchdog.jsonl").read_text().splitlines()), 2)

    def test_shared_failure_does_not_stop_local_watchdog_reporting(self):
        log = self.log()
        reporter = Reporter(log, "managed")
        with patch(f"{REPORTING}.logger.configure_client", side_effect=RuntimeError("secret")), \
                patch(f"{REPORTING}.ClientConfig.from_env"), contextlib.redirect_stderr(io.StringIO()) as out:
            reporter.start()
            reporter.record(Observation("a", "gave_up", report=True))
            reporter.close()
        self.assertFalse(reporter.enabled)
        self.assertNotIn("secret", out.getvalue())
        self.assertTrue((log.directory / "events.jsonl").stat().st_size)

    def test_local_write_precedes_enqueue_and_flush_shutdown_called_once(self):
        log = self.log()
        reporter = Reporter(log, "managed")
        def send(value):
            self.assertTrue((log.directory / "events.jsonl").stat().st_size)
        with patch(f"{REPORTING}.ClientConfig.from_env"), \
                patch(f"{REPORTING}.logger.configure_client") as configure, \
                patch(f"{REPORTING}.logger.send_detection", side_effect=send) as enqueue, \
                patch(f"{REPORTING}.logger.flush_client", return_value=False) as flush, \
                patch(f"{REPORTING}.logger.shutdown_client", return_value=True) as shutdown, \
                contextlib.redirect_stderr(io.StringIO()):
            reporter.start()
            reporter.record(Observation("a", "restarted", 45, report=True))
            reporter.close()
        for method in (configure, enqueue, flush, shutdown):
            method.assert_called_once()

    def test_enqueue_failure_keeps_event_and_shutdown_runs_even_if_flush_fails(self):
        log = self.log()
        reporter = Reporter(log, "managed")
        reporter.enabled = reporter.owned = True
        with patch(f"{REPORTING}.logger.send_detection", side_effect=RuntimeError()), \
                patch(f"{REPORTING}.logger.flush_client", side_effect=RuntimeError()), \
                patch(f"{REPORTING}.logger.shutdown_client", return_value=True) as shutdown, \
                contextlib.redirect_stderr(io.StringIO()):
            reporter.record(Observation("a", "restarted", 45, report=True))
            reporter.close()
        self.assertEqual(reporter.enqueue_failed, 1)
        self.assertEqual(reporter.queued, 0)
        self.assertTrue((log.directory / "events.jsonl").stat().st_size)
        shutdown.assert_called_once()

    def test_adapter_absent_and_import_conflict_never_fall_back(self):
        with self.assertRaisesRegex(RegistryError, "REGISTRY_UNAVAILABLE"):
            LauncherRegistry(self.root).restartable_names()
        (self.root / "registry.py").write_text("raise AssertionError('must not execute')", encoding="utf-8")
        fake = types.SimpleNamespace(__file__=str(self.root / "other.py"))
        with patch.dict(sys.modules, {"registry": fake}):
            with self.assertRaisesRegex(RegistryError, "REGISTRY_IMPORT_CONFLICT"):
                LauncherRegistry(self.root).restartable_names()

    def test_adapter_uses_exact_file_and_documented_call_contract(self):
        fixture = "def restartable_names(): return ['test']\ndef restart_if_dead(name, *, by):\n assert by == 'watchdog'\n return 'alive', 123, None\n"
        (self.root / "registry.py").write_text(fixture, encoding="utf-8")
        with patch.dict(sys.modules), patch.object(sys, "path", list(sys.path)):
            sys.modules.pop("registry", None)
            adapter = LauncherRegistry(self.root)
            self.assertEqual(adapter.restartable_names(), ["test"])
            self.assertEqual(adapter.restart_if_dead("test", by="watchdog"), ("alive", 123, None))

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        return subprocess.run([sys.executable, str(ENTRY), "--session-id", "cli_test", "--player-id", "player_test",
                               "--output-dir", str(self.root), "--shared-root", str(SHARED_ROOT), *args], cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=15)

    def test_direct_entry_from_other_cwd_demo_is_synthetic_and_local_only(self):
        run = self.run_cli("--demo", "--telemetry", "off", "--duration", "0.2", "--interval", "0.01")
        self.assertEqual(run.returncode, 0, run.stderr)
        manifests = list(self.root.rglob("manifest.json"))
        self.assertEqual(len(manifests), 1)
        self.assertTrue(json.loads(manifests[0].read_text())["synthetic"])
        lines = (manifests[0].parent / "events.jsonl").read_bytes().splitlines()
        self.assertTrue(any(decode_event(x)["evidence"]["registry_status"] == "restarted" for x in lines))

    def test_no_launcher_reports_unavailable_not_alive(self):
        run = self.run_cli("--launcher-dir", str(self.root / "missing"), "--telemetry", "off", "--duration", "0.1", "--interval", "0.02")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("REGISTRY_UNAVAILABLE", run.stderr)
        lines = next(self.root.rglob("events.jsonl")).read_bytes().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(decode_event(lines[0])["evidence"]["status"], "ERROR")

    def test_demo_cannot_send_synthetic_events_to_server(self):
        run = self.run_cli("--demo", "--telemetry", "managed")
        self.assertEqual(run.returncode, 2)
        self.assertFalse(list(self.root.rglob("manifest.json")))

    def test_launcher_t0_and_graceful_manifest(self):
        start_ms = time.time_ns() // 1_000_000 - 10000
        t0 = f"{start_ms // 1000}.{start_ms % 1000:03d}"
        run = self.run_cli("--demo", "--telemetry", "off", "--duration", "0.05", "--t0", t0)
        self.assertEqual(run.returncode, 0, run.stderr)
        manifest = json.loads(next(self.root.rglob("manifest.json")).read_text())
        self.assertEqual(manifest["start_unix_ms"], start_ms)
        self.assertEqual(manifest["timestamp_basis"], "launcher_session_start")
        self.assertEqual(manifest["run_status"], "STOPPED")
        self.assertEqual(manifest["stop_reason"], "DURATION_ELAPSED")
        self.assertGreaterEqual(manifest["run_ended_timestamp_ms"], 10000)

    def test_invalid_t0_never_creates_session(self):
        for value in ("nan", "inf", "-1", "invalid", str(time.time() + 3600)):
            run = self.run_cli("--t0", value, "--telemetry", "off")
            self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(list(self.root.rglob("manifest.json")))

    def test_two_origin_options_are_rejected(self):
        run = self.run_cli("--t0", "1", "--session-start-unix-ms", "1000")
        self.assertEqual(run.returncode, 2)
        self.assertFalse(list(self.root.rglob("manifest.json")))

    def guarded_registry(self, **changes):
        state = dict(session_id="expected", stopping=False, entries={},
                     launcher_pid=12, launcher_create_time=123)
        state.update(changes)
        module = types.SimpleNamespace(load=lambda: state, is_alive=lambda *a: True,
                                       restartable_names=lambda: ["module_a"],
                                       restart_if_dead=lambda *a, **k: ("alive", 42, None))
        adapter = LauncherRegistry(self.root, expected_session="expected")
        adapter._module = module
        return adapter, module

    def test_session_guard_valid_and_wrong_session(self):
        adapter, module = self.guarded_registry()
        self.assertEqual(adapter.restartable_names(), ["module_a"])
        self.assertEqual(adapter.restart_if_dead("module_a", by="watchdog"), ("alive", 42, None))
        module.load = lambda: dict(session_id="other")
        with patch.object(module, "restart_if_dead") as restart:
            result = Watchdog(adapter).poll()
            self.assertEqual(result[0].error_code, "REGISTRY_SESSION_MISMATCH")
            restart.assert_not_called()

    def test_missing_or_invalid_owner_never_restarts(self):
        for changes in ({"launcher_pid": None}, {"launcher_create_time": 0},
                        {"launcher_pid": True}, {"stopping": "false"}, {"entries": []}):
            adapter, module = self.guarded_registry(**changes)
            with patch.object(module, "restart_if_dead") as restart:
                self.assertEqual(Watchdog(adapter).poll()[0].status, "error")
                restart.assert_not_called()
        adapter, module = self.guarded_registry()
        module.load = lambda: {}
        self.assertEqual(Watchdog(adapter).poll()[0].error_code, "REGISTRY_SESSION_UNAVAILABLE")

    def test_orphaned_launcher_reported_even_without_restart_targets(self):
        adapter, module = self.guarded_registry()
        module.is_alive = lambda *a: False
        module.restartable_names = lambda: []
        watchdog = Watchdog(adapter)
        first = watchdog.poll()[0]
        self.assertEqual((first.status, first.scope, first.error_code),
                         ("orphaned", "registry", "LAUNCHER_ORPHANED"))
        self.assertTrue(first.report)
        self.assertFalse(watchdog.poll()[0].report)

    def test_stopping_with_cleared_owner_is_not_orphaned(self):
        adapter, module = self.guarded_registry(stopping=True, launcher_pid=None, launcher_create_time=None)
        module.restart_if_dead = lambda *a, **k: ("stopping", None, None)
        result = Watchdog(adapter).poll()
        self.assertEqual(result[-1].status, "stopping")

    def test_current_and_legacy_self_names_are_both_excluded(self):
        registry = FakeRegistry(("self_defense", "selfdefense", "module_a"))
        Watchdog(registry).poll()
        self.assertEqual(registry.calls, [("module_a", "watchdog")])

    def install_nested_fixture(self):
        team = self.root / "team"
        watchdog = team / "client/SelfDefense/watchdog"
        watchdog.mkdir(parents=True)
        for file in ENTRY.parent.glob("*.py"):
            shutil.copyfile(file, watchdog / file.name)
        (team / "shared").mkdir()
        for file in (SHARED_ROOT / "shared").glob("*.py"):
            shutil.copyfile(file, team / "shared" / file.name)
        launcher = team / "client/Launcher"
        launcher.mkdir()
        (launcher / "registry.py").write_text(
            "def load(): return dict(session_id='nested_test', stopping=False, entries={}, "
            "launcher_pid=123, launcher_create_time=456)\n"
            "def is_alive(*args): return True\n"
            "def restartable_names(): return ['module_a']\n"
            "def restart_if_dead(name, *, by): return 'alive', 123, None\n",
            encoding="utf-8")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        return team, watchdog, env

    def test_nested_entry_finds_default_shared_launcher_and_log_directory(self):
        _, watchdog, env = self.install_nested_fixture()
        run = subprocess.run(
            [sys.executable, str(watchdog / "main.py"), "--session-id", "nested_test",
             "--player-id", "fixture", "--telemetry", "off", "--duration", "0.05"],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        manifest = next((watchdog / "logs").rglob("manifest.json"))
        self.assertEqual(json.loads(manifest.read_text())["collector_version"], "0.2.1")
        raw = [json.loads(line) for line in (manifest.parent / "raw/watchdog.jsonl").read_bytes().splitlines()]
        self.assertTrue(any(x["target"] == "module_a" and x["status"] == "alive" for x in raw))
        self.assertFalse(any(x["status"] == "error" for x in raw))
        self.assertFalse((watchdog.parent / "logs").exists())

    def test_nested_package_module_entry_works_without_pythonpath(self):
        team, watchdog, env = self.install_nested_fixture()
        run = subprocess.run(
            [sys.executable, "-m", "client.SelfDefense.watchdog.main", "--session-id", "module_test",
             "--player-id", "fixture", "--demo", "--telemetry", "off", "--duration", "0.05"],
            cwd=team, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        manifest = json.loads(next((watchdog / "logs").rglob("manifest.json")).read_text())
        self.assertEqual((manifest["run_status"], manifest["synthetic"]), ("STOPPED", True))


if __name__ == "__main__":
    unittest.main()
