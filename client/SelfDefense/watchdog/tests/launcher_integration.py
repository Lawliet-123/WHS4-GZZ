"""Opt-in Windows live-process test against an unchanged downloaded Launcher.

Does not run Launcher.main, games or real detectors. Copies only supplied Launcher
sources, SelfDefense and shared into a NEW temp repository. All PIDs/registry files
belong to that temp test. Leaves evidence there, and stops test-owned children.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from fixture_protocol import control_path, matches_process


def wait_for(predicate, label, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        answer = predicate()
        if answer:
            return answer
        time.sleep(0.04)
    raise AssertionError("timeout: " + label)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def json_lines(root, name):
    values = []
    for path in root.rglob(name):
        for line in path.read_bytes().splitlines():
            try:
                values.append(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                pass  # Concurrent writer may not have completed its final line yet.
    return values


def ensure_test_console():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetConsoleWindow.restype = wintypes.HWND
    if not k32.GetConsoleCP():
        if not k32.AllocConsole():
            raise OSError(ctypes.get_last_error(), "cannot allocate isolated test console")
        # The test helper never needs a visible window.
        window = k32.GetConsoleWindow()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindow(window, 0)


class LocalSink:
    def __init__(self, root):
        from shared.config import WriterConfig
        from shared.schema import decode_event
        from shared.storage import DetectionWriter
        self.writer = DetectionWriter(WriterConfig(root))
        self.online = False
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                status, body = 503, {"error": "fixture_offline"}
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 262144:
                        raise ValueError("body size")
                    data = self.rfile.read(length)
                    if self.path != "/api/detection" or self.headers.get("Authorization") != "Bearer local-fixture":
                        status, body = 401, {"error": "fixture_credentials"}
                    elif parent.online:
                        receipt = parent.writer.write_detection(decode_event(data), event_id=self.headers["Idempotency-Key"])
                        status, body = 200, receipt.to_ack()
                except Exception:
                    status, body = 422, {"error": "fixture_invalid_event"}
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher-dir", type=Path, required=True)
    parser.add_argument("--watchdog-dir", "--selfdefense-dir", dest="watchdog_dir", type=Path,
                        default=Path(__file__).resolve().parents[1], help="Directory containing watchdog/main.py")
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reference-commit", required=True, help="Commit identifying the supplied snapshot")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows integration test only")
    if sys.flags.optimize:
        parser.error("assertions are required; do not use -O or PYTHONOPTIMIZE")
    if args.report.exists():
        parser.error("report exists; use a new filename")
    root = Path(tempfile.mkdtemp(prefix="gzz-selfdefense-live-"))
    dest = root / "client/Launcher"
    dest.mkdir(parents=True)
    hashes = {}
    for source in args.launcher_dir.glob("*"):
        if source.is_file() and source.suffix in (".py", ".md"):
            hashes[source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
            shutil.copyfile(source, dest / source.name)
    for folder, source in (("client/SelfDefense/watchdog", args.watchdog_dir), ("shared", args.shared_root / "shared")):
        (root / folder).mkdir(parents=True)
        for file in source.glob("*.py"):
            shutil.copyfile(file, root / folder / file.name)
    fixture = root / "launcher_fixture.py"
    shutil.copyfile(Path(__file__).with_name("launcher_fixture.py"), fixture)
    shutil.copyfile(Path(__file__).with_name("fixture_protocol.py"), root / "fixture_protocol.py")
    artifacts = root / "artifacts"
    artifacts.mkdir()
    print("test workspace: " + str(root), flush=True)
    os.environ["AC_LAUNCHER_LOG_DIR"] = str(artifacts / "launcher")
    os.environ["PYTHONPATH"] = str(root)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    sys.path[:0] = [str(dest), str(root)]
    import registry
    from modules import Module, ONESHOT
    from process_manager import ProcessManager
    ensure_test_console()
    sink = LocalSink(artifacts / "receiver")
    os.environ.update(GZZ_TELEMETRY_URL=f"http://127.0.0.1:{sink.server.server_port}",
                      GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK="true", GZZ_TELEMETRY_TOKEN="local-fixture",
                      GZZ_TELEMETRY_RETRY_MODE="persistent", GZZ_TELEMETRY_RETRY_BASE_SECONDS="0.1",
                      GZZ_TELEMETRY_RETRY_MAX_SECONDS="0.3")
    checks, pm, owner, orphan_entries = [], None, None, {}
    # Reference only: caller may supply another revision. Hashes identify files actually tested.
    report = {"reference_commit": args.reference_commit,
              "python": sys.version.split()[0], "executable": sys.executable,
              "is_venv": sys.prefix != sys.base_prefix, "workspace": str(root), "launcher_sha256": hashes,
              "checks": checks, "status": "RUNNING", "central_server_tested": False, "game_tested": False}
    report["watchdog_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in args.watchdog_dir.glob("*.py")}
    report["shared_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (args.shared_root / "shared").glob("*.py")}
    report["harness_sha256"] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                for name in ("launcher_integration.py", "launcher_fixture.py", "fixture_protocol.py")}
    def passed(name, **details):
        checks.append({"name": name, "status": "PASS", **details})
        print("PASS " + name, flush=True)
    def worker(name, **options):
        argv = [sys.executable, str(fixture), "worker", "--root", str(root), "--name", name]
        if options.get("mode") == ONESHOT:
            argv.append("--once")
        return Module(name=name, owner="integration fixture", argv=argv, **options)
    def raw():
        return json_lines(artifacts / "watchdog", "watchdog.jsonl")
    def events():
        return json_lines(artifacts / "watchdog", "events.jsonl")
    def kill_entry(name):
        entry = registry.entry(name)
        assert entry and str(root) in entry["argv"], "not our fixture"
        assert registry.kill(entry["pid"], entry["create_time"]), "fixture kill failed"
        return entry
    def new_target(old):
        entry = registry.entry("external_access")
        if entry and entry["pid"] != old["pid"] and registry.is_alive(entry["pid"], entry["create_time"]):
            return entry
    try:
        t0 = time.time() - 10
        wd = Module(name="self_defense", owner="4번", needs_game=False, stop_grace_s=10,
                    argv=[sys.executable, str(root / "client/SelfDefense/watchdog/main.py"),
                          "--session-id", "{session}", "--player-id", "{player}", "--t0", "{t0}",
                          "--telemetry", "{telemetry}", "--interval", "0.05",
                          "--output-dir", str(artifacts / "watchdog")])
        pm = ProcessManager([wd, worker("external_access"), worker("input_signature", restart=False),
                             worker("memory_integrity", mode=ONESHOT), worker("module_integrity")],
                            "integration_001", "fixture_player", t0, say=lambda text: print(text, flush=True))
        for name in pm.states:
            assert pm.start(name), name
        wait_for(lambda: all(any(x["target"] == name and x["status"] == "alive" for x in raw())
                             for name in pm.states), "all registered processes observed")
        wait_for(lambda: len(list((artifacts / "workers").rglob("*.json"))) == 4, "worker records")
        manifests = [read_json(p) for p in (artifacts / "watchdog").rglob("manifest.json")]
        expected_start = int(__import__("decimal").Decimal(f"{t0:.3f}") * 1000)
        assert manifests[0]["start_unix_ms"] == expected_start
        assert manifests[0]["timestamp_basis"] == "launcher_session_start"
        assert any(x["target"] == "self_defense" and x["status"] == "alive" and not x["restart_allowed"] for x in raw())
        passed("launcher_start_identity_clock_and_self_exclusion")

        old = kill_entry("external_access")
        revived = wait_for(lambda: new_target(old), "watchdog restart")
        assert revived["started_by"] == "watchdog" and len(revived["restarts"]) == 1
        wait_for(lambda: len(list((artifacts / "workers/external_access").glob("*.json"))) == 2,
                 "first replacement worker ready before next kill")
        wait_for(lambda: any(e["evidence"]["registry_status"] == "restarted" for e in events()), "local event")
        assert not sink.writer.iter_stored(), "offline fixture unexpectedly accepted event"
        sink.online = True
        wait_for(lambda: sink.writer.iter_stored(), "retry delivers to local receiver")
        pm.poll()
        assert pm.states["external_access"].adopted_pid == revived["pid"]
        passed("watchdog_restart_launcher_adoption_and_local_receiver_retry")

        old = kill_entry("external_access")
        def raced():
            pm.poll()
            return new_target(old)
        revived = wait_for(raced, "concurrent launcher/watchdog restart")
        assert len(revived["restarts"]) == 2
        wait_for(lambda: len(list((artifacts / "workers/external_access").glob("*.json"))) >= 3, "restart record")
        assert len(list((artifacts / "workers/external_access").glob("*.json"))) == 3
        passed("concurrent_restart_no_duplicate", restart_owner=revived["started_by"])

        first_wd_pid = pm.states["self_defense"].proc.pid
        pm.states["self_defense"].proc.kill()
        pm.states["self_defense"].proc.wait(5)
        pm.poll()
        assert pm.states["self_defense"].proc.pid != first_wd_pid
        def second_run_ready():
            paths = list((artifacts / "watchdog").rglob("manifest.json"))
            return paths if len(paths) == 2 and all(read_json(p) for p in paths) else None
        paths = wait_for(second_run_ready, "SelfDefense restored by Launcher")
        assert {read_json(p)["start_unix_ms"] for p in paths} == {expected_start}
        passed("launcher_recovers_selfdefense_same_session_keeps_two_runs")

        for count in (3, 4, 5):
            old = kill_entry("external_access")
            revived = wait_for(lambda: new_target(old), f"restart {count} with real backoff", seconds=22)
            assert len(revived["restarts"]) == count
            wait_for(lambda: len(list((artifacts / "workers/external_access").glob("*.json"))) == count + 1, "single worker record")
        kill_entry("external_access")
        wait_for(lambda: any(e["evidence"]["registry_status"] == "gave_up" for e in events()), "restart limit")
        assert len(list((artifacts / "workers/external_access").glob("*.json"))) == 6
        assert any(x["target"] == "external_access" and x["status"] == "backoff" for x in raw())
        passed("real_five_restart_limit_and_backoff")

        kill_entry("input_signature")
        wait_for(lambda: any(e["evidence"]["target_module"] == "input_signature" and
                             e["evidence"]["registry_status"] == "exited" for e in events()), "restart=False death report")
        assert len(list((artifacts / "workers/input_signature").glob("*.json"))) == 1
        assert registry.entry("input_signature")["restarts"] == []
        passed("restart_disabled_is_monitored_and_not_restarted")

        observed_codes = []
        for code, expected in ((0, "completed"), (1, "completed"), (2, "scan_failed"), (3, "crashed")):
            started = pm.start("memory_integrity")
            assert started
            proc = pm.states["memory_integrity"].proc
            created = registry.popen_create_time(proc)
            def ready_worker():
                records = [read_json(p) for p in (artifacts / "workers/memory_integrity").glob("*.json")]
                candidates = [r for r in records if matches_process(r, proc.pid, created)
                              and registry.is_alive(r["pid"], r["create_time"])]
                assert len(candidates) <= 1, "multiple workers for one owned Popen"
                return candidates[0] if candidates else None
            ready = wait_for(ready_worker, "oneshot worker ready with exact process identity")
            wait_for(lambda: any(x["target"] == "memory_integrity" and x["pid"] == proc.pid and
                                 x["status"] == "alive" for x in raw()), "oneshot handle acquired")
            release = control_path(root, ready["control_id"])
            release.parent.mkdir(parents=True, exist_ok=True)
            release.write_text(json.dumps({"exit_code": code}), encoding="utf-8")
            assert proc.wait(5) == code
            wait_for(lambda: not registry.is_alive(ready["pid"], ready["create_time"]), "oneshot worker exited")
            pm.poll()  # Drops Launcher's Popen handle; watchdog must retain its own handle.
            wait_for(lambda: any(e["evidence"]["target_module"] == "memory_integrity" and
                                 e["evidence"]["pid"] == proc.pid and e["evidence"]["registry_status"] == expected
                                 and e["evidence"]["exit_code"] == code for e in events()), "oneshot classification")
            assert registry.entry("memory_integrity")["restarts"] == []
            observed_codes.append({"exit_code": code, "watchdog": expected,
                                   "popen_pid": proc.pid, "worker_pid": ready["pid"],
                                   "worker_create_time": str(ready["create_time"]),
                                   "launcher": pm.states["memory_integrity"].status})
        assert len(list((artifacts / "workers/memory_integrity").glob("*.json"))) == 4
        passed("oneshot_completion_findings_scan_failure_crash", observations=observed_codes)

        # Same session, registered identity mismatch must not mistake another PID for alive.
        saved = registry.entry("input_signature")
        with registry.edit() as d:
            d["entries"]["input_signature"]["pid"] = os.getpid()
            d["entries"]["input_signature"]["create_time"] = registry.create_time(os.getpid()) + 1
        wait_for(lambda: any(x["target"] == "input_signature" and x["pid"] == os.getpid()
                             and x["status"] == "exited" for x in raw()), "stale identity not alive")
        with registry.edit() as d:
            d["entries"]["input_signature"] = saved
        passed("pid_identity_mismatch_not_reported_alive")

        registry.set_stopping()
        kill_entry("module_integrity")
        wait_for(lambda: any(x["target"] == "module_integrity" and x["status"] == "stopping" for x in raw()), "stopping observed")
        assert len(list((artifacts / "workers/module_integrity").glob("*.json"))) == 1
        result = pm.stop_all()
        assert "self_defense" in result["graceful"] and "self_defense" not in result.get("defaulted", [])
        assert not result["forced"] and not result["unsignaled"], result
        finished = [read_json(p) for p in paths if read_json(p)["run_status"] == "STOPPED"]
        assert len(finished) == 1 and finished[0]["stop_reason"] == "SIGBREAK" and finished[0]["exit_code"] == 0
        passed("stopping_suppresses_restart_and_ctrl_break_flushes", shutdown=result)
        received = sink.writer.iter_stored()
        assert received and all(len(r.result) == 7 and r.result["module"] == "selfdefense"
                                and r.result["raw_score"] == 0 for r in received)
        assert len({r.event_id for r in received}) == len(received)
        passed("operational_events_received_separately_from_cheat_scores", stored_events=len(received))

        orphan_env = dict(os.environ, AC_LAUNCHER_LOG_DIR=str(artifacts / "orphan-launcher"))
        with (artifacts / "orphan-owner.log").open("w", encoding="utf-8") as stream:
            owner = subprocess.Popen([sys.executable, str(fixture), "orphan-owner", "--root", str(root)],
                                     cwd=root, env=orphan_env, stdout=stream, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        state = wait_for(lambda: read_json(artifacts / "orphan-ready.json"), "orphan fixture ready")
        orphan_entries = state["entries"]
        wait_for(lambda: json_lines(artifacts / "orphan-watchdog", "watchdog.jsonl"), "orphan watchdog alive")
        owner.kill()
        owner.wait(5)
        target = orphan_entries["orphan_probe"]
        registry.kill(target["pid"], target["create_time"])
        wait_for(lambda: any(e["evidence"]["error_code"] == "LAUNCHER_ORPHANED"
                             for e in json_lines(artifacts / "orphan-watchdog", "events.jsonl")), "owner death report")
        assert len(list((artifacts / "workers/orphan_probe").glob("*.json"))) <= 1
        orphan_wd = orphan_entries["self_defense"]
        assert registry.request_stop(orphan_wd["pid"], orphan_wd["create_time"])
        wait_for(lambda: not registry.is_alive(orphan_wd["pid"], orphan_wd["create_time"]), "orphan watchdog cleanup")
        passed("launcher_death_reported_without_resurrection")
        assert all(hashlib.sha256((dest / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
        assert all(hashlib.sha256((args.launcher_dir / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
        passed("downloaded_launcher_files_unchanged")
        report["status"] = "PASS"
    except BaseException as exc:
        report.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if owner and owner.poll() is None:
            owner.kill()
            owner.wait(5)
        for entry in orphan_entries.values():
            registry.kill(entry["pid"], entry["create_time"])
        try:
            if pm:
                pm.stop_all(grace_s=2)
        except Exception as exc:
            report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            report["status"] = "FAIL"
        finally:
            # Even a diagnostic/output error must not leave test-owned children running.
            for entry in registry.load().get("entries", {}).values():
                if entry.get("cwd") == str(root):
                    registry.kill(entry["pid"], entry["create_time"])
            workers = [read_json(p) for p in (artifacts / "workers").rglob("*.json")]
            still_alive = [w["pid"] for w in workers if w and registry.is_alive(w["pid"], w["create_time"])]
            report["unexpected_live_workers_before_cleanup"] = still_alive
            if still_alive:
                report["status"] = "FAIL"
                # Only ready records under our private temp root, checked against creation time.
                for worker in workers:
                    if worker and worker["pid"] in still_alive:
                        registry.kill(worker["pid"], worker["create_time"])
            report["test_workers_still_alive"] = [w["pid"] for w in workers
                if w and registry.is_alive(w["pid"], w["create_time"])]
            sink.close()
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print("report: " + str(args.report), flush=True)
    if report["status"] != "PASS":
        raise RuntimeError("integration cleanup failed; see report")


if __name__ == "__main__":
    main()
