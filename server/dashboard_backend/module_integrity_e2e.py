"""Owned native DLL lab -> Shared -> loopback Receiver/Scoring/Dashboard.

Run with 64-bit Windows Python from the repository root:
  python -m server.dashboard_backend.module_integrity_e2e
  python -m server.dashboard_backend.module_integrity_e2e serve --port 8002 --duration 300

Only a newly created Python helper loads a Windows system DLL. No game, injector,
existing logs, .env, production credentials, or persistent stores are used.
Launcher is absent from this lab; its coverage is not fabricated.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import uuid

from shared.config import ClientConfig
from shared.logger import configure_client, flush_client, get_client_status, shutdown_client
from shared.schema import EVENT_FIELDS

from .browser_smoke import (
    FixtureHTTP, checked_port, checked_run_id, child_environment,
    require_free_port, terminate_child,
)


PLAYER = "native_lab_player"
EXPECTED_ASSESSMENT = "INCONCLUSIVE"


def require_native_platform() -> None:
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise RuntimeError("native DLL lab requires 64-bit Windows Python")


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def owned_server(port: int | None = None, run_id: str | None = None):
    """Yield (bounded loopback HTTP client, owned temporary root), then clean up."""
    port = checked_port(available_port() if port is None else port)
    run_id = checked_run_id(uuid.uuid4().hex[:12] if run_id is None else run_id)
    require_free_port(port)  # Refuse occupied ports before creating stores or children.
    http = FixtureHTTP(port, run_id)
    with tempfile.TemporaryDirectory(prefix="module-integrity-http-") as directory:
        root = Path(directory)
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "error", "--no-access-log"],
            cwd=Path(__file__).resolve().parents[2], env=child_environment(root, run_id),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise RuntimeError("owned loopback server failed to start")
                try:
                    overview = http.request("/api/dashboard/overview")
                    if (overview.get("schema_version") != "dashboard-v0"
                            or overview.get("assessments") != []
                            or overview.get("counts", {}).get("events") != 0):
                        raise RuntimeError("refusing to use a nonempty or unrelated service")
                    break
                except urllib.error.URLError as error:
                    if isinstance(error, urllib.error.HTTPError):
                        error.close()
                    if time.monotonic() >= deadline:
                        raise RuntimeError("owned loopback server startup timed out") from None
                    time.sleep(0.1)
            if process.poll() is not None:
                raise RuntimeError("owned server exited before lab input")
            yield http, root
        finally:
            terminate_child(process)


def session_id(run_id: str) -> str:
    return "native_module_" + checked_run_id(run_id)


def acknowledged() -> None:
    if not flush_client(timeout=10):
        raise RuntimeError("Shared delivery was not acknowledged")
    status = get_client_status()
    if status.pending or status.failed:
        raise RuntimeError("Shared has undelivered lab events")


def verify_events(http: FixtureHTTP, local_events: list[dict]) -> list[dict]:
    """Compare every original seven-field payload against real API list/detail."""
    response = http.request("/api/dashboard/events?limit=200")
    rows = response.get("items", [])
    if response.get("has_more") or len(rows) != len(local_events):
        raise RuntimeError("Receiver count differs from locally written lab events")
    ids, sequences = set(), set()
    for row, original in zip(rows, local_events):
        if any(row.get(field) != original[field] for field in EVENT_FIELDS):
            raise RuntimeError("Receiver payload differs from the native producer")
        event_id, sequence = row.get("id"), row.get("sequence")
        try:
            uuid.UUID(event_id)
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("Receiver event ID is invalid") from None
        if (event_id in ids or type(sequence) is not int or sequence <= 0
                or sequence in sequences):
            raise RuntimeError("Receiver event identity is duplicated or invalid")
        ids.add(event_id)
        sequences.add(sequence)
        detail = http.request("/api/dashboard/events/" + event_id)
        if detail.get("id") != event_id or detail.get("sequence") != sequence:
            raise RuntimeError("Dashboard detail identity differs from Receiver")
        if any(detail.get(field) != original[field] for field in EVENT_FIELDS):
            raise RuntimeError("Dashboard detail changed the original lab event")
    if get_client_status().acknowledged_this_run != len(local_events):
        raise RuntimeError("Shared ACK count differs from Receiver event count")
    return rows


def verify_scoring(http: FixtureHTTP, root: Path, local_events: list[dict]) -> dict:
    # Inspect only the fresh store created by owned_server, never a configured path.
    from server.scoring.storage import ScoringStore
    store = ScoringStore(root / "scoring.sqlite3")
    subject = session_id(http.run_id)
    history = store.get_external_access_history(subject, PLAYER, "module_integrity")
    if len(history) != len(local_events):
        raise RuntimeError("Scoring did not retain every native observation")
    for row, original in zip(history, local_events):
        if (row.raw_score != original["raw_score"] or row.evidence != original["evidence"]
                or row.timestamp_ms != original["timestamp_ms"]):
            raise RuntimeError("Scoring history changed native observation evidence")
    state = store.get_scoped_module_state(subject, PLAYER, "external_access", "module_integrity")
    if state is None or state.raw_score != 0 or state.evidence.get("status") != "NORMAL":
        raise RuntimeError("repeat NORMAL did not update its own scoped state")
    scope = f"/api/dashboard/sessions/{subject}/players/{PLAYER}"
    snapshot = http.request(scope + "/snapshot")
    verdict = http.request(f"/api/dashboard/verdict/{subject}/{PLAYER}")
    if (snapshot.get("status") != EXPECTED_ASSESSMENT
            or snapshot.get("final_verdict") != verdict
            or verdict.get("assessment_complete") is not False
            or verdict.get("evidence_unit_count") != 0
            or "external_access" not in verdict.get("unresolved_modules", [])
            or snapshot.get("score") is not None or snapshot.get("confidence") is not None):
        # The trusted DLL is below event_threshold=2. This DLL-only lab has no
        # external_process observation, so current B preserves incomplete coverage.
        raise RuntimeError("current B verdict differs from the DLL-only incomplete-coverage contract")
    overview = http.request("/api/dashboard/overview")
    rows = overview.get("assessments", [])
    if (len(rows) != 1 or rows[0].get("session_id") != subject
            or rows[0].get("player_id") != PLAYER
            or rows[0].get("status") != snapshot["status"]
            or overview.get("counts", {}).get("events") != len(local_events)):
        raise RuntimeError("Dashboard overview differs from verified native lab scope")
    modules = snapshot.get("modules", [])
    if (len(modules) != 1 or modules[0].get("module") != "external_access"
            or modules[0].get("raw_score") != 0):
        raise RuntimeError("Dashboard snapshot lost the module/submodule contract")
    return verdict


def native_observation(http: FixtureHTTP, root: Path) -> tuple[list[dict], str, int]:
    from client.LocalGuard.external_access.common.artifact_inspector import calculate_sha256
    from client.LocalGuard.external_access.module_integrity import smoke_test as native
    from client.LocalGuard.external_access.module_integrity.runner import (
        ModuleIntegrityRunner, _write_local_and_send,
    )
    local_events: list[dict] = []
    helper = subprocess.Popen(
        [sys.executable, "-I", "-u", "-c", native._HELPER_CODE],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        # Helper needs only system process variables; omit every parent app setting.
        env={key: value for key, value in os.environ.items()
             if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC"}},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        target_pid = native._ready_pid(native._read_reply(helper))
        dll = native._pick_unloaded_system_dll(target_pid)
        original_hash = calculate_sha256(dll)

        def write(path, event):
            # Exercise production durable write-first/queue delivery, including
            # its persistent handoff ledger, then independently check HTTP ACKs.
            with redirect_stdout(io.StringIO()):
                _write_local_and_send(path, event)
            local_events.append(deepcopy(event))

        runner = ModuleIntegrityRunner(
            game_executable_name=Path(sys.executable).name, game_pid=target_pid,
            session_id=session_id(http.run_id), player_id=PLAYER,
            output_path=root / "native-module.jsonl", writer=write,
            audit_initial_snapshot=False, emit_status_events=True,
        )
        baseline = runner.scan_once()
        if baseline.error or not baseline.game_found or not baseline.baseline_created:
            raise RuntimeError("native baseline did not succeed")
        acknowledged()
        verify_events(http, local_events)
        if (len(local_events) != 1 or local_events[0]["raw_score"] != 0
                or local_events[0]["evidence"].get("status") != "NORMAL"):
            raise RuntimeError("native baseline did not emit its NORMAL sample")
        native._send(helper, "LOAD\t" + str(dll))
        if native._read_reply(helper) != "LOADED":
            raise RuntimeError("owned helper did not load the selected system DLL")
        detection = runner.scan_once()
        if detection.error or detection.emitted_detections < 1:
            raise RuntimeError("native DLL addition was not detected")
        additions = local_events[1:]
        matches = [event for event in additions
                   if event["evidence"].get("module_name", "").casefold() == dll.name.casefold()]
        if len(matches) != 1:
            raise RuntimeError("selected system DLL was not detected exactly once")
        selected = matches[0]["evidence"]
        if (Path(selected.get("module_path", "")).resolve() != dll.resolve()
                or selected.get("sha256") != original_hash):
            raise RuntimeError("selected system DLL path/hash differs from the observed native artifact")
        for event in additions:
            evidence = event["evidence"]
            if (event["module"] != "external_access" or event["raw_score"] != 1
                    or evidence.get("submodule") != "module_integrity"
                    or evidence.get("status") != "SUSPICIOUS"
                    or evidence.get("change_type") != "added"
                    or evidence.get("target_pid") != target_pid
                    or evidence.get("signature_status") != "trusted"
                    or not isinstance(evidence.get("sha256"), str)
                    or len(evidence["sha256"]) != 64):
                raise RuntimeError("DLL trust/evidence differs from the native system-DLL lab contract")
        acknowledged()
        before_repeat = verify_events(http, local_events)
        repeat = runner.scan_once()
        if repeat.error or repeat.emitted_detections:
            raise RuntimeError("unchanged DLL was detected again")
        acknowledged()
        after_repeat = verify_events(http, local_events)
        if (len(after_repeat) != len(before_repeat) + 1
                or local_events[-1]["raw_score"] != 0
                or local_events[-1]["evidence"].get("status") != "NORMAL"
                or sum(row["evidence"].get("change_type") == "added" for row in after_repeat)
                != len(additions)):
            raise RuntimeError("repeat scan duplicated additions instead of appending only NORMAL")
        if native._new_events(root / "native-module.jsonl", 0) != local_events:
            raise RuntimeError("owned local JSONL differs from acknowledged Receiver records")
        return local_events, dll.name, len(additions)
    finally:
        try:
            native._stop_helper(helper)
        finally:
            for stream in (helper.stdin, helper.stdout):
                if stream is not None:
                    stream.close()


def run_check(port: int | None = None, *, duration: int = 0) -> dict:
    if type(duration) is not int or (duration != 0 and not 30 <= duration <= 3600):
        raise ValueError("duration must be zero for check or 30..3600 seconds for serve")
    require_native_platform()
    with owned_server(port) as (http, root):
        config = ClientConfig(
            server_url=http.url, api_token=http.tokens["GZZ_TELEMETRY_TOKEN"],
            outbox_path=root / "outbox.sqlite3", timeout_seconds=2,
            max_attempts=3, retry_base_seconds=0.1, retry_max_seconds=0.2,
            allow_insecure_loopback=True, use_environment_proxy=False,
        )
        configure_client(config)  # Explicit owned settings; never ClientConfig.from_env().
        try:
            local_events, dll_name, added_count = native_observation(http, root)
            verdict = verify_scoring(http, root, local_events)
            summary = {"result": "PASS", "native_lab_only": True, "game_used": False,
                       "transport": "real loopback HTTP", "detected_system_dll": dll_name,
                       "added_events": added_count, "normal_events": 2,
                       "stored_events": len(local_events), "acknowledged_events": len(local_events),
                       "selected_dll_detected_once": True, "repeat_added_events": 0,
                       "signature_status": "trusted", "raw_score": 1,
                       "central_assessment": verdict["status"], "scoring_history_preserved": True,
                       "assessment_complete": False,
                       "coverage_note": "external_process unobserved; trusted DLL raw 1 is below event threshold 2",
                       "launcher_used": False}
            if duration:
                print(json.dumps({**summary, "result": "READY", "endpoint": http.url,
                                  "run_id": http.run_id,
                                  "dashboard_token": http.tokens["GZZ_DASHBOARD_TOKEN"],
                                  "duration_seconds": duration}, indent=2), flush=True)
                stop_at, refresh_at = time.monotonic() + duration, time.monotonic() + 5
                while time.monotonic() < stop_at:
                    if time.monotonic() >= refresh_at:
                        verify_events(http, local_events)
                        verify_scoring(http, root, local_events)
                        refresh_at = time.monotonic() + 5
                    time.sleep(0.2)
            return summary
        finally:
            if not shutdown_client(timeout=5):
                raise RuntimeError("owned Shared sender did not stop")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    check = commands.add_parser("check", help="complete the native loopback check and clean up")
    check.add_argument("--port", type=int)
    serve = commands.add_parser("serve", help="keep the verified lab API available for browser inspection")
    serve.add_argument("--port", type=int, default=8002)
    serve.add_argument("--duration", type=int, default=300)
    args = parser.parse_args()
    try:
        result = run_check(getattr(args, "port", None),
                           duration=args.duration if args.command == "serve" else 0)
        print(json.dumps(result, indent=2))
        return 0
    except KeyboardInterrupt:
        print("Native lab interrupted; owned helper, server, sender and temporary stores cleaned.")
        return 130
    except Exception as error:
        print(f"Native lab failed ({type(error).__name__}); no payload, path or response body printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
