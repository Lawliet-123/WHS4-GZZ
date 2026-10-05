"""Opt-in Windows x64 test: debug ONLY a new disposable child owned by this script.

The monitor itself never attaches/detaches/kills. This test harness creates and
cleans up its own debuggee. No existing application, game or team process is touched.
Uses a copied, pinned Launcher registry and a NEW private directory.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE))
from probe import Probe, WindowsAPI
from snapshot_contract import verify_snapshot


class ExceptionRecord(ctypes.Structure):
    _fields_ = [("code", w.DWORD), ("flags", w.DWORD), ("record", w.LPVOID), ("address", w.LPVOID),
                ("parameters", w.DWORD), ("information", ctypes.c_size_t * 15)]


class ExceptionInfo(ctypes.Structure):
    _fields_ = [("record", ExceptionRecord), ("first_chance", w.DWORD)]


class CreateInfo(ctypes.Structure):
    _fields_ = [("file", w.HANDLE), ("process", w.HANDLE), ("thread", w.HANDLE), ("base", w.LPVOID),
                ("offset", w.DWORD), ("size", w.DWORD), ("tls", w.LPVOID), ("start", w.LPVOID),
                ("image", w.LPVOID), ("unicode", w.WORD)]


class LoadInfo(ctypes.Structure):
    _fields_ = [("file", w.HANDLE), ("base", w.LPVOID), ("offset", w.DWORD), ("size", w.DWORD),
                ("image", w.LPVOID), ("unicode", w.WORD)]


class Details(ctypes.Union):
    _fields_ = [("exception", ExceptionInfo), ("create", CreateInfo), ("dll", LoadInfo)]


class DebugEvent(ctypes.Structure):
    _fields_ = [("code", w.DWORD), ("pid", w.DWORD), ("tid", w.DWORD), ("details", Details)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher-dir", type=Path, required=True)
    parser.add_argument("--shared-root", type=Path, required=True)
    parser.add_argument("--reference-commit", required=True, help="Full reviewed Launcher commit SHA")
    parser.add_argument("--registry-sha256", required=True, help="Approved registry.py SHA-256; never auto-trusted")
    parser.add_argument("--output-dir", type=Path, help="Must not already exist")
    args = parser.parse_args()
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
        parser.error("this test harness requires Windows x64 Python")
    source = args.launcher_dir / "registry.py"
    try:
        registry_pin = verify_snapshot(source, args.registry_sha256, args.reference_commit)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.output_dir:
        base = args.output_dir.resolve()
        base.mkdir(parents=True, exist_ok=False)
    else:
        base = Path(tempfile.mkdtemp(prefix="gzz-antidebug-native-"))
    (base / "launcher").mkdir()
    copied = base / "launcher/registry.py"
    shutil.copyfile(source, copied)
    verify_snapshot(copied, registry_pin, args.reference_commit)
    old_log_dir = os.environ.get("AC_LAUNCHER_LOG_DIR")
    os.environ["AC_LAUNCHER_LOG_DIR"] = str(base / "registry")
    spec = importlib.util.spec_from_file_location("anti_debug_fixture_registry", copied)
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)
    session = "anti_debug_native_001"
    registry.begin_session(session)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, argtypes in {
        "WaitForDebugEvent": [ctypes.POINTER(DebugEvent), w.DWORD],
        "ContinueDebugEvent": [w.DWORD, w.DWORD, w.DWORD],
        "DebugActiveProcessStop": [w.DWORD],
        "DebugSetProcessKillOnExit": [w.BOOL],
        "CloseHandle": [w.HANDLE],
    }.items():
        function = getattr(k32, name)
        function.argtypes, function.restype = argtypes, w.BOOL

    def pump(timeout=100):
        event = DebugEvent()
        if not k32.WaitForDebugEvent(ctypes.byref(event), timeout):
            error = ctypes.get_last_error()
            if error not in (121, 0):
                raise OSError(error, "fixture debug wait failed")
            return False
        if event.code in (3, 6):
            handle = event.details.create.file if event.code == 3 else event.details.dll.file
            if handle:
                k32.CloseHandle(handle)
        status = 0x00010002  # DBG_CONTINUE, including the initial loader breakpoint.
        if event.code == 1 and event.details.exception.record.code not in (0x80000003, 0x80000004):
            status = 0x80010001  # DBG_EXCEPTION_NOT_HANDLED; do not suppress unexpected faults.
        if not k32.ContinueDebugEvent(event.pid, event.tid, status):
            raise OSError(ctypes.get_last_error(), "fixture debug continue failed")
        return True

    ready = base / "worker-ready"
    # DEBUG_ONLY_THIS_PROCESS must attach to the actual fixture, not a venv redirector.
    # The production monitor below still runs with sys.executable (including a venv).
    debuggee_python = getattr(sys, "_base_executable", sys.executable)
    worker = subprocess.Popen([debuggee_python, "-c",
        "import os,sys,time,json; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid()})); time.sleep(45)", str(ready)],
        creationflags=0x00000002, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # DEBUG_ONLY_THIS_PROCESS
    attached, cases, events = True, [], []
    t0 = time.time_ns() // 1_000_000
    try:
        if not k32.DebugSetProcessKillOnExit(True):
            raise OSError(ctypes.get_last_error(), "fixture cleanup guard failed")
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            pump()
        if not ready.exists():
            raise RuntimeError("fixture child did not become ready")
        if json.loads(ready.read_text())["pid"] != worker.pid:
            raise RuntimeError("debuggee is a redirector, not a direct owned Python process")
        registry.register("fixture_probe", worker, by="launcher", restartable=False,
                          argv=["fixture", "NOT_FOR_TELEMETRY"], cwd=str(base), log=str(base / "unused.log"))
        created = registry.entry("fixture_probe")["create_time"]

        def scan(name, code, target_state=None, registry_state="VALID"):
            before = Path(registry.PID_FILE).read_bytes()
            existing = set((base / "logs").rglob("events.jsonl"))
            command = [sys.executable, str(MODULE / "main.py"), "--session-id", session,
                       "--player-id", "fixture_player", "--registry-path", registry.PID_FILE,
                       "--shared-root", str(args.shared_root.resolve()), "--output-dir", str(base / "logs"),
                       "--session-start-unix-ms", str(t0), "--once", "--synthetic", "--telemetry", "off"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            if result.returncode != code:
                raise RuntimeError(name + ": " + result.stderr)
            if Path(registry.PID_FILE).read_bytes() != before:
                raise RuntimeError("monitor changed the registry")
            fresh = set((base / "logs").rglob("events.jsonl")) - existing
            if len(fresh) != 1:
                raise RuntimeError("missing unique run")
            event = json.loads(fresh.pop().read_text(encoding="utf-8"))
            found = next((t for t in event["evidence"]["targets"] if t["target_module"] == "fixture_probe"), None)
            if (event["evidence"]["registry_state"] != registry_state
                    or (target_state is not None and (found is None or found["state"] != target_state))):
                raise RuntimeError(name + ": unexpected observation " + str(event))
            if len(event) != 7 or event["raw_score"] != 0 or "NOT_FOR_TELEMETRY" in str(event):
                raise RuntimeError("event contract or redaction failed")
            events.append(event)
            cases.append({"case": name, "status": event["evidence"]["status"], "exit_code": code,
                          "target_state": target_state, "scan_complete": event["evidence"]["scan_complete"]})

        scan("native_debugger_connected", 1, "DEBUGGER_PRESENT")
        while pump(0):
            pass
        if not k32.DebugActiveProcessStop(worker.pid):
            raise OSError(ctypes.get_last_error(), "fixture detach failed")
        attached = False
        scan("native_debugger_detached", 0, "CLEAR")
        with registry.edit() as value:
            value["entries"]["fixture_probe"]["create_time"] = created + 1
        scan("wrong_creation_time", 2, "ERROR")
        with registry.edit() as value:
            value["entries"]["fixture_probe"]["create_time"] = created
        worker.terminate()  # ONLY this harness's disposable Popen child, not a monitor response.
        worker.wait(timeout=5)
        scan("target_exited", 0, "EXITED")
        registry.set_stopping()
        registry.end_session()
        scan("launcher_stopping", 0, registry_state="STOPPING")
    finally:
        if worker.poll() is None:
            if attached and k32.DebugActiveProcessStop(worker.pid):
                attached = False
            worker.terminate()
            if attached:
                deadline = time.monotonic() + 3
                while worker.poll() is None and time.monotonic() < deadline:
                    pump(50)
            worker.wait(timeout=5)
        if old_log_dir is None:
            os.environ.pop("AC_LAUNCHER_LOG_DIR", None)
        else:
            os.environ["AC_LAUNCHER_LOG_DIR"] = old_log_dir
    report = {"status": "PASS", "synthetic_fixture": True, "native_debugger_tested": True,
              "monitor_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in MODULE.glob("*.py")},
              "harness_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                 for name in ("native_integration.py", "snapshot_contract.py")},
              "cases": cases, "owned_child_pid": worker.pid, "owned_child_stopped": worker.poll() is not None,
              "reference_commit": args.reference_commit, "registry_sha256": registry_pin,
              "launcher_source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == registry_pin,
              "copied_registry_unchanged": hashlib.sha256(copied.read_bytes()).hexdigest() == registry_pin,
              "monitor_executable": sys.executable, "monitor_is_venv": sys.prefix != sys.base_prefix,
              "debuggee_executable": debuggee_python, "debuggee_is_direct_process": True,
              "python": sys.version.split()[0], "real_game_tested": False, "central_https_tested": False}
    (base / "native-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (base / "recorded_events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    print(json.dumps({"output": str(base), **report}, indent=2))


if __name__ == "__main__":
    main()
