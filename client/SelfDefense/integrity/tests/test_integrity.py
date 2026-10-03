"""Isolated fixture tests; never change a deployed game or team module."""
from __future__ import annotations

import contextlib
import hashlib
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
if (ROOT / "client/SelfDefense/integrity/main.py").is_file():
    from client.SelfDefense.integrity import baseline, scanner, reporting
    from client.SelfDefense.integrity.build_baseline import main as build
    ENTRY = ROOT / "client/SelfDefense/integrity/main.py"
else:
    sys.path.insert(0, str(ROOT))
    import baseline, scanner, reporting
    from build_baseline import main as build
    ENTRY = ROOT / "main.py"
import shared
from shared.schema import decode_event
from shared.config import WriterConfig
from shared.storage import DetectionWriter

SHARED_ROOT = Path(shared.__file__).resolve().parent.parent


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="integrity-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "deployment"
        (self.root / "client/detector").mkdir(parents=True)
        (self.root / "shared").mkdir()
        self.first = self.root / "client/detector/main.py"
        self.second = self.root / "shared/config.py"
        self.first.write_text("trusted first\n", encoding="utf-8")
        self.second.write_text("trusted config\n", encoding="utf-8")
        self.names = ["client/detector/main.py", "shared/config.py"]
        self.manifest = self.base / "baseline.json"
        self.data = baseline.make_baseline(self.root, self.names, "test_v1", scope_dirs=["client", "shared"])
        self.manifest.write_bytes(self.data)
        self.pin = hashlib.sha256(self.data).hexdigest()

    def scan(self):
        return scanner.scan(self.root, self.manifest, self.pin)

    def log(self, **kwargs):
        return reporting.SessionLog(self.base / "logs", "test_001", "fixture", pin=self.pin,
                                    root=self.root, **kwargs)

    def cli(self, *args, session="cli_test"):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        return subprocess.run([sys.executable, str(ENTRY), "--session-id", session, "--player-id", "fixture",
                               "--root", str(self.root), "--baseline", str(self.manifest),
                               "--baseline-sha256", self.pin, "--shared-root", str(SHARED_ROOT),
                               "--output-dir", str(self.base / "cli-logs"), *args],
                              cwd=self.base, env=env, capture_output=True, text=True, timeout=15)

    def test_clean_scope(self):
        result = self.scan()
        self.assertEqual(result.summary()["status"], "NORMAL")
        self.assertEqual(result.summary()["matched_files"], 2)
        self.assertTrue(result.summary()["scan_complete"])
        self.assertEqual(result.reasons(), [])

    def test_modified_same_size_detected(self):
        self.first.write_bytes(b"x" * self.first.stat().st_size)
        result = self.scan()
        self.assertEqual(result.summary()["modified_files"], 1)
        self.assertEqual(result.summary()["status"], "DETECTED")

    def test_missing_file_detected_not_read_error(self):
        self.first.unlink()
        result = self.scan()
        self.assertEqual(result.summary()["missing_files"], 1)
        self.assertTrue(result.summary()["scan_complete"])

    def test_added_code_file_detected(self):
        (self.root / "client/extra.dll").write_bytes(b"fixture not executable")
        result = self.scan()
        self.assertEqual(result.summary()["unexpected_files"], 1)
        self.assertIn("UNEXPECTED_CODE_FILE", result.reasons())

    def test_logs_caches_docs_not_in_automatic_scope(self):
        for name in ("logs", "__pycache__", "docs", "tests"):
            folder = self.root / "client" / name
            folder.mkdir()
            (folder / "generated.py").write_text("generated", encoding="utf-8")
        (self.root / "client/settings.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.scan().summary()["status"], "NORMAL")

    def test_files_outside_scopes_not_claimed_as_checked(self):
        (self.root / "outside.exe").write_bytes(b"fixture")
        self.assertEqual(self.scan().summary()["status"], "NORMAL")

    def test_baseline_tamper_not_silently_accepted(self):
        self.manifest.write_bytes(self.data + b" ")
        result = self.scan()
        self.assertEqual(result.error, "BASELINE_PIN_MISMATCH")
        self.assertEqual(result.summary()["status"], "ERROR")
        self.assertIsNone(result.summary()["expected_files"])
        self.assertEqual(result.files, [])

    def test_missing_baseline_does_not_create_one(self):
        self.manifest.unlink()
        self.assertEqual(self.scan().error, "BASELINE_UNREADABLE")
        self.assertFalse(self.manifest.exists())

    def test_duplicate_json_keys_rejected_even_with_valid_pin(self):
        data = b'{"schema_version":1,"schema_version":1}'
        self.manifest.write_bytes(data)
        with self.assertRaisesRegex(baseline.IntegrityError, "DUPLICATE"):
            baseline.load_baseline(self.manifest, hashlib.sha256(data).hexdigest())

    def test_path_escapes_device_names_ads_rejected(self):
        for name in ("../secret", "/absolute", "C:/secret", "a\\b", "a//b", "a/./b", "NUL.py",
                     "client/a.py:stream", "client/a.", "client/a ", "", "client/\ud800.py"):
            with self.subTest(name=name), self.assertRaises(baseline.IntegrityError):
                baseline.relative_name(name)

    def test_manifest_schema_bounds_and_case_collision(self):
        original = json.loads(self.data)
        changes = [dict(files=[]), dict(schema_version=True), dict(algorithm="md5"),
                   dict(scope_dirs=["client", "client/sub"]), dict(scope_dirs=["../elsewhere"])]
        for delta in changes:
            with self.assertRaises(baseline.IntegrityError):
                baseline.validate_manifest({**original, **delta})
        original["files"].append({**original["files"][0], "path": original["files"][0]["path"].upper()})
        with self.assertRaisesRegex(baseline.IntegrityError, "DUPLICATE_FILE_PATH"):
            baseline.validate_manifest(original)

    def test_permission_failure_not_normal(self):
        with patch.object(scanner, "fingerprint", side_effect=PermissionError()):
            result = self.scan()
        self.assertEqual(result.summary()["status"], "ERROR")
        self.assertEqual(result.summary()["error_files"], 2)

    def test_mismatch_and_read_failure_both_preserved(self):
        self.first.unlink()
        real = scanner.fingerprint
        def check(root, name, **kwargs):
            if name.endswith("config.py"):
                raise PermissionError()
            return real(root, name, **kwargs)
        with patch.object(scanner, "fingerprint", side_effect=check):
            summary = self.scan().summary()
        self.assertEqual(summary["status"], "DETECTED")
        self.assertFalse(summary["scan_complete"])
        self.assertEqual((summary["missing_files"], summary["error_files"]), (1, 1))

    def test_root_unavailable_is_error(self):
        result = scanner.scan(self.base / "missing", self.manifest, self.pin)
        self.assertEqual(result.error, "ROOT_UNAVAILABLE")
        self.assertEqual(result.summary()["status"], "ERROR")

    def test_directory_replaces_file_is_error(self):
        self.first.unlink()
        self.first.mkdir()
        result = self.scan()
        self.assertIn("NOT_REGULAR_FILE", result.reasons())
        self.assertFalse(result.summary()["scan_complete"])

    def test_hard_link_rejected(self):
        os.link(self.first, self.root / "client/hardlink.py")
        self.assertIn("HARD_LINK_NOT_ALLOWED", self.scan().reasons())

    def test_reparse_attribute_rejected(self):
        class Info:
            st_mode = 0o100644
            st_file_attributes = 0x400
        with self.assertRaisesRegex(baseline.IntegrityError, "REPARSE"):
            baseline._reject_link(Info())

    def test_changed_during_read_is_error(self):
        with patch.object(baseline, "_identity", side_effect=[1, 1, 1, 2]):
            with self.assertRaisesRegex(baseline.IntegrityError, "FILE_CHANGED_DURING_READ"):
                baseline.fingerprint(self.root, self.names[0])

    def test_stat_fstat_ctime_semantics_can_differ(self):
        real = os.fstat
        def handle_stat(fd):
            info = real(fd)
            return SimpleNamespace(**{k: getattr(info, k) for k in
                                      ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")},
                                   st_ctime_ns=7)
        with patch.object(baseline.os, "fstat", side_effect=handle_stat):
            self.assertEqual(baseline.fingerprint(self.root, self.names[0])["sha256"],
                             hashlib.sha256(self.first.read_bytes()).hexdigest())

    def test_same_api_ctime_change_still_rejected(self):
        real = os.fstat
        calls = iter((7, 8))
        def handle_stat(fd):
            info = real(fd)
            return SimpleNamespace(**{k: getattr(info, k) for k in
                                      ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")},
                                   st_ctime_ns=next(calls))
        with patch.object(baseline.os, "fstat", side_effect=handle_stat):
            with self.assertRaisesRegex(baseline.IntegrityError, "FILE_CHANGED_DURING_READ"):
                baseline.fingerprint(self.root, self.names[0])

    def test_file_read_budget_is_bounded(self):
        with self.assertRaisesRegex(baseline.IntegrityError, "FILE_TOO_LARGE"):
            baseline.fingerprint(self.root, self.names[0], max_bytes=1)

    def test_cancel_is_not_clean(self):
        stop = threading.Event()
        stop.set()
        result = scanner.scan(self.root, self.manifest, self.pin, stop=stop)
        self.assertEqual(result.error, "SCAN_CANCELLED")
        self.assertFalse(result.summary()["scan_complete"])

    def test_baseline_builder_refuses_overwrite(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = build(["--root", str(self.root), "--include-dir", "client", "--include-dir", "shared",
                          "--release-id", "v1", "--output", str(self.manifest)])
        self.assertEqual(code, 2)
        self.assertEqual(self.manifest.read_bytes(), self.data)

    def test_baseline_builder_scoped_inventory(self):
        output = self.base / "new-baseline.json"
        with contextlib.redirect_stdout(io.StringIO()) as text:
            code = build(["--root", str(self.root), "--include-dir", "client", "--include-dir", "shared",
                          "--release-id", "v1", "--output", str(output)])
        self.assertEqual(code, 0)
        pin = json.loads(text.getvalue())["baseline_sha256"]
        value = baseline.load_baseline(output, pin)
        self.assertEqual(len(value["files"]), 2)
        self.assertEqual(value["scope_dirs"], ["client", "shared"])

    def test_event_seven_fields_zero_score_and_scan_times(self):
        log = self.log(synthetic=True)
        self.first.unlink()
        result = self.scan()
        event = log.event(result, 10, 20, 0)
        log.write(result, event)
        parsed = decode_event((log.directory / "events.jsonl").read_bytes().strip())
        self.assertEqual((len(parsed), parsed["module"], parsed["raw_score"], parsed["timestamp_ms"]),
                         (7, "selfdefense", 0, 20))
        self.assertEqual(parsed["evidence"]["status"], "DETECTED")
        self.assertEqual(parsed["evidence"]["kind"], "file_integrity")
        self.assertTrue(parsed["evidence"]["synthetic"])

    def test_findings_bounded_raw_keeps_all(self):
        for i in range(25):
            (self.root / "client" / f"new{i}.py").write_bytes(b"fixture")
        log, result = self.log(), self.scan()
        event = log.event(result, 0, 1, 0)
        log.write(result, event)
        self.assertEqual(len(event["evidence"]["findings"]), 20)
        self.assertTrue(event["evidence"]["findings_truncated"])
        raw = json.loads((log.directory / "raw/integrity.jsonl").read_text())
        self.assertEqual(len(raw["files"]), 27)

    def test_clock_reuse_run_separation_and_conflicting_baseline(self):
        start = time.time_ns() // 1_000_000 - 10000
        first, second = self.log(start_ms=start), self.log(start_ms=start)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertGreaterEqual(second.elapsed_ms(), 10000)
        with self.assertRaises(ValueError):
            reporting.SessionLog(self.base / "logs", "test_001", "fixture", pin="a" * 64, root=self.root)

    def test_cli_clean_modified_missing_exit_codes(self):
        first = self.cli("--once", "--synthetic")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.first.write_text("changed", encoding="utf-8")
        second = self.cli("--once", "--synthetic", session="changed")
        self.assertEqual(second.returncode, 1, second.stderr)
        self.first.unlink()
        third = self.cli("--once", "--synthetic", session="missing")
        self.assertEqual(third.returncode, 1, third.stderr)

    def test_cli_pin_failure_records_error_without_modifying_baseline(self):
        self.manifest.write_bytes(self.data + b" ")
        run = self.cli("--once")
        self.assertEqual(run.returncode, 2, run.stderr)
        event = json.loads(next((self.base / "cli-logs").rglob("events.jsonl")).read_text())
        self.assertEqual(event["evidence"]["error_code"], "BASELINE_PIN_MISMATCH")
        self.assertEqual(self.manifest.read_bytes(), self.data + b" ")

    def test_cli_t0_time_and_multiple_clean_events(self):
        start = time.time_ns() // 1_000_000 - 10000
        run = self.cli("--duration", "0.1", "--interval", "0.01", "--t0", f"{start // 1000}.{start % 1000:03d}")
        self.assertEqual(run.returncode, 0, run.stderr)
        events = [json.loads(x) for x in next((self.base / "cli-logs").rglob("events.jsonl")).read_text().splitlines()]
        self.assertGreater(len(events), 1)
        self.assertTrue(all(e["timestamp_ms"] >= 10000 and e["evidence"]["status"] == "NORMAL" for e in events))

    def test_cli_synthetic_cannot_send(self):
        run = self.cli("--synthetic", "--telemetry", "managed", "--once")
        self.assertEqual(run.returncode, 2)
        self.assertFalse((self.base / "cli-logs").exists())

    def test_cli_invalid_pin_and_clock_options_rejected(self):
        for options in (("--baseline-sha256", "invalid"), ("--t0", "nan"),
                        ("--t0", "1", "--session-start-unix-ms", "1000")):
            self.assertEqual(self.cli(*options).returncode, 2)

    def test_managed_sender_one_lifecycle_flush_failure_still_shutdown(self):
        sender = reporting.Sender("managed")
        with patch.dict(os.environ, {"GZZ_TELEMETRY_OUTBOX": str(self.base / "outbox.sqlite3")}), \
             patch.object(reporting.ClientConfig, "from_env"), \
             patch.object(reporting.logger, "configure_client") as configure, \
             patch.object(reporting.logger, "send_detection") as send, \
             patch.object(reporting.logger, "flush_client", side_effect=RuntimeError()) as flush, \
             patch.object(reporting.logger, "shutdown_client", return_value=True) as shutdown, \
             contextlib.redirect_stderr(io.StringIO()):
            sender.start()
            sender.send(self.log().event(self.scan(), 0, 1, 0))
            sender.close()
        for function in (configure, send, flush, shutdown):
            function.assert_called_once()

    def test_sender_requires_distinct_outbox_and_reports_enqueue_error(self):
        sender = reporting.Sender("managed")
        with patch.dict(os.environ, {}, clear=True), patch.object(reporting.logger, "configure_client") as configure, \
             contextlib.redirect_stderr(io.StringIO()):
            sender.start()
        configure.assert_not_called()
        self.assertFalse(sender.enabled)
        sender.enabled = True
        with patch.object(reporting.logger, "send_detection", side_effect=RuntimeError()), contextlib.redirect_stderr(io.StringIO()):
            sender.send({})
        self.assertEqual(sender.failed, 1)

    def test_installed_direct_and_module_entry_finds_shared(self):
        team = self.base / "installed"
        target = team / "client/SelfDefense/integrity"
        target.mkdir(parents=True)
        (team / "shared").mkdir()
        for file in ENTRY.parent.glob("*.py"):
            shutil.copyfile(file, target / file.name)
        for file in (SHARED_ROOT / "shared").glob("*.py"):
            shutil.copyfile(file, team / "shared" / file.name)
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        for prefix in ([str(target / "main.py")], ["-m", "client.SelfDefense.integrity.main"]):
            command = [sys.executable, *prefix, "--session-id", "installed", "--player-id", "fixture",
                       "--root", str(self.root), "--baseline", str(self.manifest), "--baseline-sha256", self.pin, "--once"]
            run = subprocess.run(command, cwd=team, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(len(list((target / "logs").rglob("manifest.json"))), 2)

    def test_actual_shared_forwarding_preserves_events_and_retries_same_id(self):
        writer = DetectionWriter(WriterConfig(self.base / "receiver"))
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                key = self.headers.get("Idempotency-Key")
                requests.append((key, payload, self.path, self.headers.get("Authorization")))
                if len(requests) == 1:
                    code, ack = 503, {"error": "fixture_retry"}
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
        env.update(GZZ_TELEMETRY_URL=f"http://127.0.0.1:{server.server_port}",
                   GZZ_TELEMETRY_TOKEN="fixture-token", GZZ_TELEMETRY_OUTBOX=str(self.base / "queue.sqlite3"),
                   GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK="true", GZZ_TELEMETRY_TIMEOUT_SECONDS="0.5",
                   GZZ_TELEMETRY_RETRY_BASE_SECONDS="0.01", GZZ_TELEMETRY_RETRY_MAX_SECONDS="0.02")
        try:
            with patch.dict(os.environ, env, clear=True):
                clean = self.cli("--once", "--telemetry", "managed", session="local_receiver_clean")
                self.first.write_text("fixture change", encoding="utf-8")
                changed = self.cli("--once", "--telemetry", "managed", session="local_receiver_changed")
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertEqual(changed.returncode, 1, changed.stderr)
            self.assertEqual(len(requests), 3)
            self.assertEqual(requests[0], requests[1])
            self.assertTrue(all(r[2:] == ("/api/detection", "Bearer fixture-token") for r in requests))
            local = [json.loads(p.read_text()) for p in (self.base / "cli-logs").rglob("events.jsonl")]
            delivered = [decode_event(r[1]) for r in requests[1:]]
            self.assertCountEqual(local, delivered)
            self.assertEqual([e["evidence"]["status"] for e in delivered], ["NORMAL", "DETECTED"])
            stored = [json.loads(line) for p in (self.base / "receiver").rglob("*.jsonl")
                      for line in p.read_text().splitlines()]
            self.assertCountEqual(stored, delivered)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    @unittest.skipUnless(os.name == "nt", "Windows console break lifecycle")
    def test_windows_ctrl_break_finalizes_manifest(self):
        command = [sys.executable, str(ENTRY), "--session-id", "break_test", "--player-id", "fixture",
                   "--root", str(self.root), "--baseline", str(self.manifest), "--baseline-sha256", self.pin,
                   "--shared-root", str(SHARED_ROOT), "--output-dir", str(self.base / "signal-logs"),
                   "--interval", "30", "--synthetic"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                events = list((self.base / "signal-logs").rglob("events.jsonl"))
                if events and events[0].stat().st_size:
                    break
                time.sleep(0.02)
            self.assertTrue(events and events[0].stat().st_size)
            process.send_signal(signal.CTRL_BREAK_EVENT)
            _, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, errors)
            manifest = json.loads(next((self.base / "signal-logs").rglob("manifest.json")).read_text())
            self.assertEqual((manifest["run_status"], manifest["stop_reason"]), ("STOPPED", "SIGBREAK"))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
