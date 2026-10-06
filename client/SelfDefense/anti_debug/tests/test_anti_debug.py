"""Read-only monitor tests. All files/processes are isolated fixtures."""
from __future__ import annotations

import contextlib
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "client/SelfDefense/anti_debug/main.py").is_file():
    from client.SelfDefense.anti_debug import probe, registry_reader as registry, monitor, reporting
    ENTRY = ROOT / "client/SelfDefense/anti_debug/main.py"
else:
    sys.path.insert(0, str(ROOT))
    import probe, registry_reader as registry, monitor, reporting
    ENTRY = ROOT / "main.py"
import shared
from shared.schema import decode_event
from shared.config import WriterConfig
from shared.storage import DetectionWriter

SHARED_ROOT = Path(shared.__file__).resolve().parent.parent


def state():
    return {"session_id": "fixture_001", "stopping": False, "launcher_pid": 111,
            "launcher_create_time": 123, "entries": {
                "autopaint": {"pid": 222, "create_time": 456, "restartable": False},
                "oneshot": {"pid": 333, "create_time": 789, "restartable": False}}}


class FakeAPI:
    def __init__(self):
        self.open_result, self.time_result = (99, 0), (123, 0)
        self.alive_results, self.debug_result = [(True, 0), (True, 0)], (False, 0)
        self.closed, self.debug_calls = [], 0

    def open(self, pid):
        return self.open_result

    def close(self, handle):
        self.closed.append(handle)

    def created(self, handle):
        return self.time_result

    def alive(self, handle):
        return self.alive_results.pop(0)

    def debugger(self, handle):
        self.debug_calls += 1
        return self.debug_result


class ProbeTests(unittest.TestCase):
    def test_api_success_not_debugger_presence(self):
        api = probe.WindowsAPI.__new__(probe.WindowsAPI)
        api.k32 = SimpleNamespace(CheckRemoteDebuggerPresent=lambda handle, out: 1)
        self.assertEqual(api.debugger(99), (False, 0))

    def test_positive_out_parameter(self):
        def debugger(handle, out):
            out._obj.value = True
            return 1
        api = probe.WindowsAPI.__new__(probe.WindowsAPI)
        api.k32 = SimpleNamespace(CheckRemoteDebuggerPresent=debugger)
        self.assertEqual(api.debugger(99), (True, 0))

    def test_clear_and_present_close_handles(self):
        for present in (False, True):
            api = FakeAPI()
            api.debug_result = present, 0
            result = probe.Probe(api).inspect(1234, 123)
            self.assertIs(result.debugger_present, present)
            self.assertEqual(result.state, "DEBUGGER_PRESENT" if present else "CLEAR")
            self.assertEqual(api.closed, [99])

    def test_open_errors_not_false_clean(self):
        for error, code, status in ((5, "ACCESS_DENIED", "ERROR"), (87, "TARGET_NOT_RUNNING", "EXITED"),
                                    (123, "OPEN_PROCESS_FAILED", "ERROR")):
            api = FakeAPI()
            api.open_result = None, error
            result = probe.Probe(api).inspect(1234, 123)
            self.assertEqual((result.state, result.error_code), (status, code))
            self.assertIsNone(result.debugger_present)
            self.assertEqual(api.closed, [])

    def test_creation_mismatch_never_queries_debugger(self):
        api = FakeAPI()
        result = probe.Probe(api).inspect(1234, 321)
        self.assertEqual(result.error_code, "PROCESS_IDENTITY_MISMATCH")
        self.assertEqual(api.debug_calls, 0)
        self.assertEqual(api.closed, [99])

    def test_identity_query_failure(self):
        api = FakeAPI()
        api.time_result = None, 5
        self.assertEqual(probe.Probe(api).inspect(1234, 123).error_code, "IDENTITY_QUERY_FAILED")
        self.assertEqual(api.closed, [99])

    def test_debugger_query_failure_not_false(self):
        api = FakeAPI()
        api.debug_result = None, 5
        result = probe.Probe(api).inspect(1234, 123)
        self.assertEqual(result.error_code, "DEBUGGER_QUERY_FAILED")
        self.assertIsNone(result.debugger_present)

    def test_exit_before_query(self):
        api = FakeAPI()
        api.alive_results = [(False, 0)]
        self.assertEqual(probe.Probe(api).inspect(1234, 123).state, "EXITED")
        self.assertEqual(api.debug_calls, 0)

    def test_exit_after_negative_query_is_not_clean(self):
        api = FakeAPI()
        api.alive_results = [(True, 0), (False, 0)]
        result = probe.Probe(api).inspect(1234, 123)
        self.assertEqual(result.error_code, "TARGET_EXITED_DURING_CHECK")
        self.assertIsNone(result.debugger_present)

    def test_wait_failure_not_exit(self):
        api = FakeAPI()
        api.alive_results = [(None, 6)]
        self.assertEqual(probe.Probe(api).inspect(1234, 123).error_code, "LIVENESS_QUERY_FAILED")

    def test_external_target_requires_valid_creation_time(self):
        for pid, created in ((0, 123), (True, 123), (1234, 0), (1234, True), (2**32, 123)):
            self.assertEqual(probe.Probe(FakeAPI()).inspect(pid, created).state, "ERROR")
        self.assertEqual(probe.Probe(FakeAPI()).inspect(1234).error_code, "EXPECTED_IDENTITY_REQUIRED")

    def test_only_query_rights_are_requested(self):
        self.assertEqual(probe.QUERY_ACCESS, 0x00100400)
        self.assertFalse(probe.QUERY_ACCESS & (0x0001 | 0x0002 | 0x0008 | 0x0010 | 0x0020))


class RegistryAndMonitorTests(unittest.TestCase):
    def parsed(self, value=None):
        return registry.parse(json.dumps(state() if value is None else value).encode(), "fixture_001")

    def make_monitor(self, outcomes=None, snapshot=None):
        outcomes = outcomes or {}
        calls = []
        def inspect(pid, created):
            calls.append((pid, created))
            answer = outcomes.get(pid, probe.Observation("CLEAR", False, created or 777))
            if isinstance(answer, Exception):
                raise answer
            return answer
        return monitor.Monitor(SimpleNamespace(inspect=inspect),
                               SimpleNamespace(load=lambda: snapshot or self.parsed())), calls

    def test_all_entries_including_nonrestartable(self):
        result = self.parsed()
        self.assertEqual([t.name for t in result.targets], ["autopaint", "oneshot"])

    def test_no_legacy_pid_only_fallback(self):
        value = state()
        value.pop("entries")
        value["modules"] = {"autopaint": 222}
        with self.assertRaisesRegex(registry.RegistryError, "INVALID_STATE"):
            self.parsed(value)

    def test_wrong_session_and_invalid_types(self):
        for key, value in (("session_id", "old"), ("stopping", 0), ("entries", []),
                           ("launcher_pid", True), ("launcher_create_time", 0)):
            with self.assertRaises(registry.RegistryError):
                self.parsed({**state(), key: value})

    def test_entry_bounds_and_duplicate_keys(self):
        for data in (b'{"a":1,"a":2}', b'{', b'\xff', b'x' * (registry.MAX_REGISTRY_BYTES + 1)):
            with self.assertRaises(registry.RegistryError):
                registry.parse(data, "fixture_001")
        value = state()
        value["entries"] = {str(i): {"pid": 222, "create_time": 123} for i in range(129)}
        with self.assertRaisesRegex(registry.RegistryError, "TOO_MANY"):
            self.parsed(value)

    def test_invalid_target_name_or_identity(self):
        for entries in ({"../bad": {"pid": 1, "create_time": 2}}, {"a": {"pid": 2}}, {"a": None}):
            with self.assertRaisesRegex(registry.RegistryError, "INVALID_TARGET"):
                self.parsed({**state(), "entries": entries})

    def test_normal_and_detection_scope(self):
        subject, calls = self.make_monitor({222: probe.Observation("DEBUGGER_PRESENT", True, 456)})
        result = subject.poll()
        self.assertEqual(result.summary()["status"], "DETECTED")
        self.assertTrue(result.summary()["scan_complete"])
        self.assertEqual(result.summary()["checked_targets"], 4)
        self.assertEqual(calls, [(os.getpid(), None), (111, 123), (222, 456), (333, 789)])

    def test_missing_registry_still_self_checks_but_not_whole_clean(self):
        subject, calls = self.make_monitor()
        subject.registry.load = lambda: (_ for _ in ()).throw(registry.RegistryError("REGISTRY_UNAVAILABLE"))
        result = subject.poll()
        self.assertEqual(result.summary()["status"], "ERROR")
        self.assertFalse(result.summary()["scan_complete"])
        self.assertEqual(len(calls), 1)

    def test_unverified_owner_prevents_stale_child_checks(self):
        subject, calls = self.make_monitor({111: probe.Observation("ERROR", error_code="ACCESS_DENIED")})
        result = subject.poll()
        self.assertEqual(result.error_code, "LAUNCHER_IDENTITY_UNVERIFIED")
        self.assertEqual(result.skipped_registered_targets, 2)
        self.assertEqual(len(calls), 2)

    def test_exited_owner_is_not_debugger_detection(self):
        subject, _ = self.make_monitor({111: probe.Observation("EXITED", error_code="TARGET_EXITED")})
        result = subject.poll()
        self.assertEqual((result.summary()["status"], result.error_code), ("ERROR", "LAUNCHER_NOT_RUNNING"))

    def test_stopping_not_alarm_or_full_clean(self):
        snapshot = self.parsed({**state(), "stopping": True, "launcher_pid": None, "launcher_create_time": None})
        subject, calls = self.make_monitor(snapshot=snapshot)
        result = subject.poll()
        self.assertEqual((result.registry_state, result.summary()["status"]), ("STOPPING", "NORMAL"))
        self.assertFalse(result.summary()["scan_complete"])
        self.assertEqual(len(calls), 1)

    def test_exited_oneshot_is_recorded_not_cheat(self):
        subject, _ = self.make_monitor({333: probe.Observation("EXITED", error_code="TARGET_EXITED")})
        result = subject.poll()
        self.assertEqual(result.summary()["status"], "NORMAL")
        self.assertEqual(result.summary()["exited_targets"], 1)
        self.assertIsNone(result.targets[-1]["debugger_present"])

    def test_partial_failure_keeps_positive_evidence(self):
        subject, _ = self.make_monitor({222: probe.Observation("DEBUGGER_PRESENT", True, 456),
                                       333: probe.Observation("ERROR", error_code="ACCESS_DENIED")})
        result = subject.poll()
        self.assertEqual(result.summary()["status"], "DETECTED")
        self.assertFalse(result.summary()["scan_complete"])

    def test_exception_redacted_and_other_targets_still_checked(self):
        subject, calls = self.make_monitor({222: RuntimeError("SECRET argv token")})
        result = subject.poll()
        self.assertEqual(len(calls), 4)
        self.assertNotIn("SECRET", str(result))
        self.assertIn("PROBE_FAILED", result.reasons())

    def test_invalid_probe_result_never_counts_as_clean(self):
        for value in (None, probe.Observation("UNKNOWN"), probe.Observation("CLEAR", None),
                      probe.Observation("DEBUGGER_PRESENT", False), probe.Observation("ERROR", False)):
            subject, _ = self.make_monitor({222: value})
            result = subject.poll()
            self.assertEqual(result.summary()["status"], "ERROR")
            self.assertIn("PROBE_FAILED", result.reasons())

    def test_cancellation_not_complete(self):
        stop = threading.Event()
        stop.set()
        subject, _ = self.make_monitor()
        result = subject.poll(stop)
        self.assertEqual(result.error_code, "SCAN_CANCELLED")
        self.assertEqual(result.skipped_registered_targets, 2)

    def test_filetime_string_for_browser_precision(self):
        created = 134051234567890123
        subject, _ = self.make_monitor({222: probe.Observation("CLEAR", False, created)})
        result = subject.poll()
        self.assertEqual(result.targets[2]["observed_create_time"], str(created))


class FixtureCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gzz-antidebug-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)


class FileTests(FixtureCase):
    def log(self, **kwargs):
        return reporting.SessionLog(self.root / "logs", "fixture_001", "fixture_player", **kwargs)

    def event(self):
        scan = monitor.Scan("self_only", targets=[{"target_module": "fixture", "role": "self", "pid": 222,
            "state": "CLEAR", "debugger_present": False, "observed_create_time": "123", "error_code": None}])
        return self.log(synthetic=True).event(scan, 10, 20, 0)

    def test_reader_missing_corrupt_readonly(self):
        path = self.root / "anticheat_pids.json"
        reader = registry.RegistryReader(path, "fixture_001")
        with self.assertRaisesRegex(registry.RegistryError, "UNAVAILABLE"):
            reader.load()
        self.assertFalse(path.exists())
        data = json.dumps(state()).encode()
        path.write_bytes(data)
        self.assertEqual(len(reader.load().targets), 2)
        self.assertEqual(path.read_bytes(), data)
        path.write_bytes(b'{')
        with self.assertRaisesRegex(registry.RegistryError, "INVALID_JSON"):
            reader.load()

    def test_permission_retry_bounded(self):
        reader = registry.RegistryReader(self.root / "unused", "fixture_001")
        with patch.object(Path, "open", side_effect=PermissionError()), patch.object(registry.time, "sleep") as sleep:
            with self.assertRaisesRegex(registry.RegistryError, "ACCESS_DENIED"):
                reader.load()
        self.assertEqual(sleep.call_count, 2)

    def test_seven_fields_local_logs_normal_zero(self):
        log = self.log(synthetic=True)
        event = self.event()
        log.write(event)
        decoded = decode_event((log.directory / "events.jsonl").read_bytes().strip())
        self.assertEqual((len(decoded), decoded["raw_score"], decoded["timestamp_ms"]), (7, 0, 20))
        self.assertEqual(decoded["evidence"]["kind"], "debugger_presence")
        self.assertEqual(json.loads((log.directory / "raw/anti_debug.jsonl").read_text())["timestamp_ms"], 20)

    def test_clock_reuse_conflict_and_finalization(self):
        first, second = self.log(), self.log()
        self.assertNotEqual(first.run_id, second.run_id)
        first.finish(1, "SINGLE_SCAN_COMPLETED")
        self.assertEqual(json.loads((first.directory / "manifest.json").read_text())["run_status"], "STOPPED")
        with self.assertRaises(ValueError):
            self.log(synthetic=True)
        with self.assertRaises(ValueError):
            self.log(registry_path=self.root / "different.json")

    def test_corrupt_clock_not_replaced(self):
        self.log()
        clock = self.root / "logs/fixture_001/session-clock.json"
        clock.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.log()
        self.assertEqual(clock.read_text(), "{")

    def test_sender_managed_once_and_cleanup_after_flush_error(self):
        sender = reporting.Sender("managed")
        with patch.dict(os.environ, {"GZZ_TELEMETRY_OUTBOX": str(self.root / "outbox.sqlite3")}), \
             patch.object(reporting.ClientConfig, "from_env"), \
             patch.object(reporting.logger, "configure_client") as configure, \
             patch.object(reporting.logger, "send_detection") as send, \
             patch.object(reporting.logger, "get_client_status", return_value=SimpleNamespace(
                 failed=0, worker_alive=True, closed=False, last_code=None)), \
             patch.object(reporting.logger, "flush_client", side_effect=RuntimeError()) as flush, \
             patch.object(reporting.logger, "shutdown_client", return_value=True) as shutdown, \
             contextlib.redirect_stderr(io.StringIO()):
            sender.start()
            sender.send(self.event())
            sender.close()
        for function in (configure, send, flush, shutdown):
            function.assert_called_once()

    def test_sender_requires_outbox_and_external_not_owned(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            sender = reporting.Sender("managed")
            sender.start()
            self.assertFalse(sender.enabled)
        with patch.object(reporting.logger, "get_client_status", return_value=SimpleNamespace(closed=False, worker_alive=True)), \
             patch.object(reporting.logger, "shutdown_client") as shutdown:
            sender = reporting.Sender("external")
            sender.start()
            sender.close()
            self.assertTrue(sender.enabled)
            shutdown.assert_not_called()

    def test_enqueue_failure_retains_local_event(self):
        log = self.log(synthetic=True)
        event = self.event()
        log.write(event)
        sender = reporting.Sender("off")
        sender.enabled = True
        with patch.object(reporting.logger, "send_detection", side_effect=RuntimeError("SECRET")), \
             contextlib.redirect_stderr(io.StringIO()) as output:
            sender.send(event)
        self.assertEqual(sender.failed, 1)
        self.assertNotIn("SECRET", output.getvalue())
        self.assertEqual(decode_event((log.directory / "events.jsonl").read_bytes().strip()), event)


@unittest.skipUnless(os.name == "nt", "Windows API")
class CLITests(FixtureCase):
    def cli(self, *args, session="fixture_001", env=None):
        return subprocess.run([sys.executable, str(ENTRY), "--session-id", session, "--player-id", "fixture_player",
                               "--shared-root", str(SHARED_ROOT), "--output-dir", str(self.root / "cli-logs"), *args],
                              cwd=self.root, env=env, capture_output=True, text=True, timeout=15)

    def live_registry(self):
        own = probe.Probe().inspect(os.getpid())
        self.assertEqual(own.state, "CLEAR")
        value = state()
        value.update(launcher_pid=os.getpid(), launcher_create_time=own.observed_create_time)
        value["entries"] = {"fixture_owner": {"pid": os.getpid(), "create_time": own.observed_create_time,
                                               "restartable": False}}
        path = self.root / "anticheat_pids.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_native_self_and_registry_query_readonly(self):
        path = self.live_registry()
        original = path.read_bytes()
        run = self.cli("--once", "--registry-path", str(path))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(path.read_bytes(), original)
        event = json.loads(next((self.root / "cli-logs").rglob("events.jsonl")).read_text())
        self.assertEqual(event["evidence"]["checked_targets"], 3)
        self.assertEqual(event["evidence"]["scope"], "launcher_registered")

    def test_absent_registry_logs_error_not_clean_self(self):
        run = self.cli("--once", "--registry-path", str(self.root / "missing.json"))
        self.assertEqual(run.returncode, 2, run.stderr)
        event = json.loads(next((self.root / "cli-logs").rglob("events.jsonl")).read_text())
        self.assertEqual(event["evidence"]["status"], "ERROR")
        self.assertEqual(event["evidence"]["checked_targets"], 1)

    def test_time_origin_and_every_normal_poll(self):
        t0 = time.time_ns() // 1_000_000 - 5000
        run = self.cli("--self-only", "--duration", "0.1", "--interval", "0.01", "--t0", f"{t0 // 1000}.{t0 % 1000:03d}")
        self.assertEqual(run.returncode, 0, run.stderr)
        events = [json.loads(x) for x in next((self.root / "cli-logs").rglob("events.jsonl")).read_text().splitlines()]
        self.assertGreater(len(events), 1)
        self.assertTrue(all(e["timestamp_ms"] >= 5000 and e["raw_score"] == 0 for e in events))

    def test_test_only_modes_cannot_send_and_bad_arguments_rejected(self):
        for args in (("--self-only", "--telemetry", "managed"), ("--synthetic", "--telemetry", "external"),
                     ("--interval", "nan"), ("--t0", "nan"), ("--t0", "1", "--session-start-unix-ms", "1000")):
            self.assertEqual(self.cli(*args).returncode, 2)
        self.assertFalse((self.root / "cli-logs").exists())

    def test_native_ctrl_break_finalizes(self):
        proc = subprocess.Popen([sys.executable, str(ENTRY), "--session-id", "fixture_001", "--player-id", "fixture_player",
                                 "--shared-root", str(SHARED_ROOT), "--self-only", "--interval", "30",
                                 "--output-dir", str(self.root / "signal-logs")],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            deadline = time.monotonic() + 5
            events = []
            while time.monotonic() < deadline:
                events = list((self.root / "signal-logs").rglob("events.jsonl"))
                if events and events[0].stat().st_size:
                    break
                time.sleep(0.02)
            self.assertTrue(events and events[0].stat().st_size)
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            _, errors = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, errors)
            manifest = json.loads(next((self.root / "signal-logs").rglob("manifest.json")).read_text())
            self.assertEqual((manifest["run_status"], manifest["stop_reason"]), ("STOPPED", "SIGBREAK"))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)

    def test_installed_entries_find_shared_without_pythonpath(self):
        team = self.root / "installed"
        target = team / "client/SelfDefense/anti_debug"
        target.mkdir(parents=True)
        (team / "shared").mkdir()
        for file in ENTRY.parent.glob("*.py"):
            shutil.copyfile(file, target / file.name)
        for file in (SHARED_ROOT / "shared").glob("*.py"):
            shutil.copyfile(file, team / "shared" / file.name)
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        for command in ([str(target / "main.py")], ["-m", "client.SelfDefense.anti_debug.main"]):
            run = subprocess.run([sys.executable, *command, "--session-id", "installed", "--player-id", "fixture_player",
                                  "--self-only", "--once"], cwd=team, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(list((target / "logs").rglob("manifest.json"))), 2)

    def test_real_shared_delivery_retry_and_zero_score(self):
        path = self.live_registry()
        writer = DetectionWriter(WriterConfig(self.root / "receiver"))
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                key = self.headers.get("Idempotency-Key")
                requests.append((key, payload, self.path, self.headers.get("Authorization")))
                if len(requests) == 1:
                    code, ack = 503, {"error": "fixture"}
                else:
                    code, ack = 200, writer.write_detection(decode_event(payload), event_id=key).to_ack()
                body = json.dumps(ack).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GZZ_TELEMETRY_")}
        env.update(GZZ_TELEMETRY_URL=f"http://127.0.0.1:{server.server_port}", GZZ_TELEMETRY_TOKEN="fixture-token",
                   GZZ_TELEMETRY_OUTBOX=str(self.root / "outbox.sqlite3"), GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK="true",
                   GZZ_TELEMETRY_TIMEOUT_SECONDS="0.5", GZZ_TELEMETRY_RETRY_BASE_SECONDS="0.01",
                   GZZ_TELEMETRY_RETRY_MAX_SECONDS="0.02")
        try:
            run = self.cli("--once", "--registry-path", str(path), "--telemetry", "managed", env=env)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0], requests[1])
            self.assertEqual(requests[0][2:], ("/api/detection", "Bearer fixture-token"))
            event = json.loads(next((self.root / "cli-logs").rglob("events.jsonl")).read_text())
            self.assertEqual(decode_event(requests[0][1]), event)
            stored = [json.loads(line) for p in (self.root / "receiver").rglob("*.jsonl") for line in p.read_text().splitlines()]
            self.assertEqual(stored, [event])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
