"""Coverage expansion contracts; synthetic observations, no external processes."""
from dataclasses import replace
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from test_selfdefense import (LauncherRegistry, RegistryError, Snapshot, Target, ProcessState,
                             ProcessProbe, Watchdog, SessionLog)


def target(name="input_signature", state="alive", mode="continuous", restart=False, code=None, error=None):
    return Target(name, 42, mode, restart, ProcessState(state, code, error), "133000000000000000")


class CoverageTests(unittest.TestCase):
    def registry(self, *items, stopping=False):
        return types.SimpleNamespace(inspect=Mock(return_value=Snapshot(stopping, items)),
                                     restart_if_dead=Mock(return_value=("restarted", 99, None)))

    def test_localguard_all_four_processes_observed(self):
        registry = self.registry(target("external_access", restart=True), target("module_integrity", restart=True),
                                 target("input_signature"), target("memory_integrity", mode="oneshot"))
        rows = Watchdog(registry).poll()[1:]
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(x.status == "alive" for x in rows))
        registry.restart_if_dead.assert_not_called()

    def test_restart_false_death_reported_without_restart(self):
        registry = self.registry(target(state="exited", code=0))
        monitor = Watchdog(registry)
        row = monitor.poll()[-1]
        self.assertEqual((row.status, row.exit_code, row.restart_allowed), ("exited", 0, False))
        self.assertTrue(row.report)
        self.assertFalse(monitor.poll()[-1].report)
        registry.restart_if_dead.assert_not_called()

    def test_autopaint_and_self_are_observed_but_not_restarted(self):
        registry = self.registry(*(target(name, state="exited", restart=True) for name in ("autopaint", "self_defense", "selfdefense")))
        rows = Watchdog(registry).poll()[1:]
        self.assertTrue(all(x.status == "exited" and not x.restart_allowed for x in rows))
        registry.restart_if_dead.assert_not_called()

    def test_oneshot_codes_agree_with_launcher(self):
        for code, status in ((0, "completed"), (1, "completed"), (2, "scan_failed"), (3, "crashed"), (259, "crashed")):
            with self.subTest(code=code):
                registry = self.registry(target("memory_integrity", "exited", "oneshot", code=code))
                row = Watchdog(registry).poll()[-1]
                self.assertEqual((row.status, row.exit_code), (status, code))
                registry.restart_if_dead.assert_not_called()

    def test_missed_short_oneshot_is_unknown_not_completed(self):
        for state in ("missing", "identity_mismatch"):
            registry = self.registry(target("memory_integrity", state, "oneshot"))
            row = Watchdog(registry).poll()[-1]
            self.assertEqual((row.status, row.error_code), ("unknown", "ONESHOT_EXIT_UNOBSERVED"))
            registry.restart_if_dead.assert_not_called()

    def test_access_denied_is_not_alive_and_never_restarted(self):
        registry = self.registry(target("external_access", "error", restart=True, error="PROCESS_ACCESS_DENIED"))
        row = Watchdog(registry).poll()[-1]
        self.assertEqual((row.status, row.error_code), ("error", "PROCESS_ACCESS_DENIED"))
        registry.restart_if_dead.assert_not_called()

    def test_restart_keeps_policy_but_does_not_mix_old_identity(self):
        registry = self.registry(target("external_access", "exited", restart=True, code=5))
        row = Watchdog(registry).poll()[-1]
        self.assertEqual((row.status, row.pid, row.exit_code, row.create_time), ("restarted", 99, None, None))
        registry.restart_if_dead.assert_called_once_with("external_access", by="watchdog")

    def test_stopping_suppresses_all_errors_and_restarts(self):
        registry = self.registry(target(state="exited", restart=True), stopping=True)
        row = Watchdog(registry).poll()[-1]
        self.assertEqual((row.status, row.error_code), ("stopping", None))
        registry.restart_if_dead.assert_not_called()

    def test_removed_registration_is_reported_not_silently_forgotten(self):
        registry = self.registry(target())
        monitor = Watchdog(registry)
        monitor.poll()
        registry.inspect.return_value = Snapshot(False, ())
        row = monitor.poll()[-1]
        self.assertEqual((row.target, row.status), ("input_signature", "unregistered"))
        self.assertTrue(row.report)
        self.assertFalse(monitor.poll()[-1].report)
        registry.inspect.return_value = Snapshot(False, (target(),))
        self.assertTrue(monitor.poll()[-1].report)

    def test_no_registration_yet_is_not_false_death(self):
        registry = self.registry()
        self.assertEqual(len(Watchdog(registry).poll()), 1)

    def test_dynamic_name_history_is_bounded(self):
        monitor = Watchdog(self.registry(target()))
        monitor._seen = {str(i) for i in range(4096)}
        self.assertEqual(monitor.poll()[0].error_code, "REGISTRY_NAME_HISTORY_LIMIT")

    def test_state_change_and_new_process_reported_stable_state_deduplicated(self):
        registry = self.registry(target("memory_integrity", "exited", "oneshot", code=1))
        monitor = Watchdog(registry)
        self.assertTrue(monitor.poll()[-1].report)
        self.assertFalse(monitor.poll()[-1].report)
        registry.inspect.return_value = Snapshot(False, (replace(target("memory_integrity", "exited", "oneshot", code=1), create_time="133000000000000001"),))
        self.assertTrue(monitor.poll()[-1].report)

    def test_operational_schema_and_unknown_not_clean(self):
        with tempfile.TemporaryDirectory() as folder:
            log = SessionLog(Path(folder), "coverage", "fixture")
            for state, code in (("missing", None), ("exited", 1), ("exited", 2), ("exited", 3)):
                row = Watchdog(self.registry(target("memory_integrity", state, "oneshot", code=code))).poll()[-1]
                event = log.event(row, 500)
                self.assertEqual(len(event), 7)
                self.assertEqual(event["raw_score"], 0)
                self.assertEqual(event["evidence"]["status"], "NORMAL" if code == 1 else "ERROR")
                self.assertFalse(event["evidence"]["functional_health_checked"])


class AdapterCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.probe = types.SimpleNamespace(read=Mock(return_value=ProcessState("alive")), retain=Mock(), close=Mock())
        self.state = dict(session_id="fixture", stopping=False, launcher_pid=1, launcher_create_time=11,
                          entries={"a": dict(pid=2, create_time=22, restartable=False)})
        self.module = types.SimpleNamespace(load=lambda: self.state, is_alive=lambda *a: True,
                                            restart_if_dead=Mock(return_value=("restarted", 3, None)))
        self.adapter = LauncherRegistry(Path(self.temp.name), expected_session="fixture", probe=self.probe)
        self.adapter._module = self.module
        self.adapter._modes = {"a": ("continuous", True)}

    def test_inspects_restart_false(self):
        item = self.adapter.inspect().targets[0]
        self.assertEqual((item.name, item.restartable, item.mode), ("a", False, "continuous"))
        self.probe.read.assert_any_call("a", 2, 22)

    def test_invalid_names_do_not_create_locks_or_restart(self):
        for entries in ({"a": {}, "A": {}}, {"../escape": {}}, {None: {}}, {"": {}}, {"a" * 129: {}}):
            self.state["entries"] = entries
            with self.assertRaisesRegex(RegistryError, "REGISTRY_INVALID_NAMES"):
                self.adapter.inspect()
        self.module.restart_if_dead.assert_not_called()

    def test_invalid_entry_does_not_prevent_other_observations(self):
        self.state["entries"]["bad"] = {"pid": True}
        rows = self.adapter.inspect().targets
        self.assertEqual([r.process.status for r in rows], ["alive", "error"])

    def test_runtime_and_definition_must_both_allow_restart(self):
        for configured, runtime, mode in ((True, False, "continuous"), (False, True, "continuous"), (True, True, "oneshot")):
            self.adapter._modes = {"a": (mode, configured)}
            self.state["entries"]["a"]["restartable"] = runtime
            self.assertFalse(self.adapter.inspect().targets[0].restartable)
            self.assertEqual(self.adapter.restart_if_dead("a", by="watchdog")[0], "skip")
        self.module.restart_if_dead.assert_not_called()

    def test_unknown_definition_is_observed_without_restart(self):
        self.adapter._modes = {}
        self.state["entries"]["a"]["restartable"] = True
        row = self.adapter.inspect().targets[0]
        self.assertEqual((row.mode, row.restartable), ("unknown", False))

    def test_unreadable_owner_is_not_healthy_or_restartable(self):
        self.probe.read.return_value = ProcessState("error", error_code="PROCESS_ACCESS_DENIED")
        with self.assertRaisesRegex(RegistryError, "LAUNCHER_PROCESS_ACCESS_DENIED"):
            self.adapter.inspect()
        with self.assertRaises(RegistryError):
            self.adapter.restart_if_dead("a", by="watchdog")
        self.module.restart_if_dead.assert_not_called()

    def test_restart_preflight_rechecks_stopping_and_permissions(self):
        self.state["entries"]["a"]["restartable"] = True
        self.adapter.inspect()
        self.state["stopping"] = True
        self.assertEqual(self.adapter.restart_if_dead("a", by="watchdog")[0], "stopping")
        self.state["stopping"] = False
        self.probe.read.side_effect = lambda n, *a: ProcessState("alive") if n == "@launcher" else ProcessState("error", error_code="PROCESS_ACCESS_DENIED")
        with self.assertRaisesRegex(RegistryError, "PROCESS_ACCESS_DENIED"):
            self.adapter.restart_if_dead("a", by="watchdog")
        self.module.restart_if_dead.assert_not_called()

    def test_close_releases_probe_handles(self):
        self.adapter.close()
        self.probe.close.assert_called_once()

    def test_target_probe_exception_does_not_stop_other_targets_or_leak(self):
        self.state["entries"]["b"] = dict(pid=3, create_time=33, restartable=False)
        def probe(name, *args):
            if name == "a":
                raise OSError("SECRET")
            return ProcessState("alive")
        self.probe.read.side_effect = probe
        rows = self.adapter.inspect().targets
        self.assertEqual(rows[0].process.error_code, "PROCESS_PROBE_FAILED")
        self.assertEqual(rows[1].process.status, "alive")
        self.assertNotIn("SECRET", str(rows))

    def test_definition_file_is_explicit_and_rejects_bad_contract(self):
        self.adapter._modes = None
        with self.assertRaisesRegex(RegistryError, "MODULE_DEFINITIONS_UNAVAILABLE"):
            self.adapter.inspect()
        path = Path(self.temp.name) / "modules.py"
        path.write_text("MODULES = 'bad'", encoding="utf-8")
        with patch.dict(__import__("sys").modules):
            __import__("sys").modules.pop("_gzz_watchdog_launcher_definitions", None)
            with self.assertRaisesRegex(RegistryError, "MODULE_DEFINITIONS_INVALID"):
                self.adapter.inspect()


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.api = types.SimpleNamespace(open=Mock(return_value=(99, 0)), created=Mock(return_value=123),
                                         wait=Mock(return_value=0x102), exit_code=Mock(return_value=0), close=Mock())
        self.probe = ProcessProbe(self.api)

    def test_live_to_exited_keeps_same_handle_and_259_is_not_alive(self):
        self.assertEqual(self.probe.read("a", 42, 123).status, "alive")
        self.api.wait.return_value, self.api.exit_code.return_value = 0, 259
        self.assertEqual(self.probe.read("a", 42, 123), ProcessState("exited", 259))
        self.api.open.assert_called_once()

    def test_pid_reuse_rejected_and_handle_closed(self):
        self.api.created.return_value = 124
        self.assertEqual(self.probe.read("a", 42, 123).status, "identity_mismatch")
        self.api.close.assert_called_once_with(99)
        self.api.wait.assert_not_called()

    def test_replacement_and_pruning_close_handles(self):
        self.probe.read("a", 42, 123)
        self.probe.read("a", 43, 123)
        self.api.close.assert_called_once_with(99)
        self.probe.retain(set())
        self.assertEqual(self.api.close.call_count, 2)
        self.probe.close()
        self.assertEqual(self.api.close.call_count, 2)

    def test_missing_denied_and_unexpected_open_error_are_distinct(self):
        for error, status in ((87, "missing"), (5, "error"), (1234, "error")):
            self.api.open.return_value = (None, error)
            self.assertEqual(self.probe.read("a", 42, 123).status, status)

    def test_api_failures_are_errors_not_exit_or_alive(self):
        self.api.created.return_value = None
        self.assertEqual(self.probe.read("a", 42, 123).error_code, "PROCESS_TIME_FAILED")
        self.api.created.return_value, self.api.wait.return_value = 123, 0xFFFFFFFF
        self.assertEqual(self.probe.read("a", 42, 123).error_code, "PROCESS_WAIT_FAILED")
        self.api.wait.return_value, self.api.exit_code.return_value = 0, None
        self.assertEqual(self.probe.read("a", 42, 123).error_code, "PROCESS_EXIT_CODE_FAILED")

    def test_invalid_identity_never_opens_a_process(self):
        for pid, created in ((True, 123), (0, 123), (42, 0), (42, True), (2**32, 123)):
            self.assertEqual(self.probe.read("a", pid, created).status, "error")
        self.api.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
