"""Bounded, synthetic-only browser fixture using the real Receiver/Shared/B APIs.

Run from the repository root with the integration venv:
  python -m server.dashboard_backend.browser_smoke serve --port 8002 --duration 300
  python -m server.dashboard_backend.browser_smoke add --port 8002 --run-id <printed-id>

The printed tokens are public synthetic test literals, NEVER production credentials.
No game, detector, real store, .env file, browser, or production server is started.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from shared.client import DetectionClient
from shared.config import ClientConfig


PLAYER = "synthetic_player"
CASES = {"suspicious": "SUSPICIOUS", "inconclusive": "INCONCLUSIVE",
         "no_active": "NO_ACTIVE_EVIDENCE", "unknown": "UNKNOWN"}
CLIENT_ID = "launcher-1700000000001"
MAX_RESPONSE_BYTES = 1024 * 1024


def checked_run_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{12}", value):
        raise ValueError("run ID must be the 12 lowercase hex characters printed by serve")
    return value


def checked_port(value: int) -> int:
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError("port must be an integer in 1024..65535")
    return value


def session_id(run_id: str, case: str) -> str:
    if case not in CASES:
        raise ValueError("unknown synthetic case")
    return f"browser_{checked_run_id(run_id)}_{case}"


def test_tokens(run_id: str) -> dict[str, str]:
    checked_run_id(run_id)
    return {name: f"synthetic-browser-{kind}-{run_id}" for name, kind in (
        ("GZZ_TELEMETRY_TOKEN", "receiver"), ("MECCHA_HEARTBEAT_TOKEN", "heartbeat"),
        ("GZZ_DASHBOARD_TOKEN", "dashboard"))}


def child_environment(folder: Path, run_id: str, source=None) -> dict[str, str]:
    # Do not inherit live GZZ/MECCHA settings, Python injection paths, or proxies.
    source = os.environ if source is None else source
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC"}
    env = {key: value for key, value in source.items() if key.upper() in allowed}
    local_tmp = folder / "tmp"
    local_tmp.mkdir()
    env.update(test_tokens(run_id))
    env.update({"TEMP": str(local_tmp), "TMP": str(local_tmp),
                "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "GZZ_TELEMETRY_LOG_ROOT": str(folder / "detections"),
                "GZZ_SCORING_DB": str(folder / "scoring.sqlite3"),
                "MECCHA_HEARTBEAT_DB": str(folder / "heartbeat.sqlite3"),
                "GZZ_DASHBOARD_INDEX": str(folder / "dashboard.sqlite3"),
                "GZZ_DASHBOARD_STALE_AFTER_MS": "30000"})
    return env


def require_free_port(port: int) -> None:
    # Never kill another listener or post fixture data to an existing service.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", checked_port(port)))
        except OSError:
            raise RuntimeError("requested loopback port is occupied or unavailable") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FixtureHTTP:
    def __init__(self, port: int, run_id: str):
        self.url = f"http://127.0.0.1:{checked_port(port)}"
        self.run_id = checked_run_id(run_id)
        self.tokens = test_tokens(run_id)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, path: str, *, token="GZZ_DASHBOARD_TOKEN", payload=None):
        if not path.startswith("/api/") or path.startswith("//") or "\\" in path:
            raise ValueError("only local fixture API paths are allowed")
        data = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        request = urllib.request.Request(self.url + path, data=data, headers={
            "Authorization": "Bearer " + self.tokens[token], "Content-Type": "application/json"})
        with self.opener.open(request, timeout=2) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if (response.status != 200 or len(body) > MAX_RESPONSE_BYTES
                    or response.headers.get_content_type() != "application/json"):
                raise RuntimeError("invalid bounded fixture response")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise RuntimeError("invalid fixture response shape")
            return result

    def verify_fixture(self):
        overview = self.request("/api/dashboard/overview")
        expected = {(session_id(self.run_id, case), PLAYER) for case in CASES}
        pairs = {(row.get("session_id"), row.get("player_id"))
                 for row in overview.get("assessments", [])}
        if (overview.get("schema_version") != "dashboard-v0" or pairs != expected
                or len(overview["assessments"]) != len(expected)
                or overview.get("session_page", {}).get("has_more") is True):
            raise RuntimeError("refusing mutation: expected synthetic fixture subjects are absent")
        return overview


def seed_events(run_id: str) -> list[dict]:
    def event(case, module, score, timestamp, evidence):
        return {"session_id": session_id(run_id, case), "player_id": PLAYER, "module": module,
                "raw_score": score, "timestamp_ms": timestamp,
                "evidence": {"synthetic": True, "fixture": "browser_smoke_v1", **evidence},
                "reasons": ["Synthetic browser integration input; no game observation"]}

    events = [event("suspicious", "godmode", score, index * 1000, {"invincible": True})
              for index, score in enumerate((2, 3, 5), 1)]
    events += [event("suspicious", "noclip", 3, 4000,
                     {"status": "SUSPICIOUS", "collision": 0, "blocked_path": 1}),
               event("inconclusive", "esp", 1, 1000,
                     {"status": "SUSPICIOUS", "sensor_event_id": "synthetic-overlay-1",
                      "event_type": "window_overlap", "categories": ["overlay"]}),
               event("no_active", "noclip", 0, 1000,
                     {"status": "NORMAL", "collision": 1, "blocked_path": 0})]
    return events


def deliver(http: FixtureHTTP, events: list[dict]) -> None:
    with tempfile.TemporaryDirectory(prefix="dashboard-browser-outbox-") as folder:
        config = ClientConfig(server_url=http.url, api_token=http.tokens["GZZ_TELEMETRY_TOKEN"],
                              outbox_path=Path(folder) / "outbox.sqlite3", timeout_seconds=2,
                              max_attempts=3, retry_base_seconds=0.1, retry_max_seconds=0.2,
                              allow_insecure_loopback=True, use_environment_proxy=False)
        with DetectionClient(config) as client:
            for event in events:
                client.send_detection(event)
            if not client.flush(timeout=10):
                raise RuntimeError("synthetic Shared delivery was not acknowledged")


def heartbeat(run_id: str, case: str, sequence: int) -> dict:
    now = datetime.now(timezone.utc)
    timestamp = int(now.timestamp() * 1000)
    component = {"status": "running", "required": True, "pid": None,
                 "updated_at_ms": timestamp, "stale_after_ms": 30000, "age_ms": 0,
                 "details": {"synthetic": True, "phase": "running"}}
    module = "esp" if case == "inconclusive" else "noclip"
    components = {"launcher": component}
    # Heartbeat-only subject has no invented detector observation.
    if case != "unknown":
        components[module] = component.copy()
    return {"schema_version": "meccha-heartbeat-3", "message_type": "heartbeat",
            "session_id": session_id(run_id, case), "player_id": PLAYER, "client_id": CLIENT_ID,
            "sequence": sequence, "timestamp_ms": timestamp, "sent_at_utc": now.isoformat(),
            "status": "healthy", "components": components,
            "transport": {"configured": True, "consecutive_failures": 0,
                          "last_success_sequence": None, "last_error_type": None}}


def refresh_heartbeat(http: FixtureHTTP, case: str) -> None:
    path = f"/api/dashboard/heartbeat/{session_id(http.run_id, case)}/{CLIENT_ID}"
    # A separate add process can race the five-second keepalive; retry only conflicts.
    for attempt in range(3):
        try:
            sequence = http.request(path)["sequence"] + 1
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code != 404:
                raise RuntimeError("fixture heartbeat lookup failed") from None
            sequence = 1
        try:
            ack = http.request("/api/heartbeat", token="MECCHA_HEARTBEAT_TOKEN",
                               payload=heartbeat(http.run_id, case, sequence))
            if ack.get("accepted") is not True or ack.get("sequence") != sequence:
                raise RuntimeError("fixture heartbeat was not acknowledged")
            return
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code != 409 or attempt == 2:
                raise RuntimeError("fixture heartbeat update failed") from None


def terminate_child(process) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()  # Only the Popen child owned by this harness.
            process.wait(timeout=5)


def serve(port: int, duration: int) -> None:
    if type(duration) is not int or not 30 <= duration <= 3600:
        raise ValueError("duration must be an integer in 30..3600 seconds")
    require_free_port(port)  # Before temp stores, child creation, or any HTTP POST.
    run_id = uuid.uuid4().hex[:12]
    http = FixtureHTTP(port, run_id)
    with tempfile.TemporaryDirectory(prefix="dashboard-browser-fixture-") as folder:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "error", "--no-access-log"],
            cwd=Path(__file__).resolve().parents[2], env=child_environment(Path(folder), run_id),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise RuntimeError("owned fixture server failed to start")
                try:
                    initial = http.request("/api/dashboard/overview")
                    if initial.get("assessments") != [] or initial.get("counts", {}).get("events") != 0:
                        raise RuntimeError("refusing to seed a nonempty service")
                    break
                except urllib.error.URLError as exc:
                    if isinstance(exc, urllib.error.HTTPError):
                        exc.close()
                    if time.monotonic() >= deadline:
                        raise RuntimeError("owned fixture server startup timed out") from None
                    time.sleep(0.1)
            if process.poll() is not None:
                raise RuntimeError("owned fixture server exited before seeding")
            deliver(http, seed_events(run_id))
            for case in CASES:
                refresh_heartbeat(http, case)
            overview = http.verify_fixture()
            for row in overview["assessments"]:
                case = next(case for case in CASES if row["session_id"] == session_id(run_id, case))
                if (row.get("status") != CASES[case] or row.get("score") is not None
                        or row.get("confidence") is not None
                        or (case == "unknown" and row.get("final_verdict") is not None)):
                    raise RuntimeError("current real B result differs from fixture expectations")
            print(json.dumps({"result": "READY", "synthetic_testing_only": True,
                              "endpoint": http.url, "dashboard_token": http.tokens["GZZ_DASHBOARD_TOKEN"],
                              "run_id": run_id, "duration_seconds": duration, "heartbeat_every_seconds": 5,
                              "seeded_events": len(seed_events(run_id)), "expected_assessments": CASES}, indent=2), flush=True)
            stop_at, refresh_at = time.monotonic() + duration, time.monotonic() + 5
            while time.monotonic() < stop_at:
                if process.poll() is not None:
                    raise RuntimeError("owned fixture server exited unexpectedly")
                if time.monotonic() >= refresh_at:
                    for case in CASES:
                        refresh_heartbeat(http, case)
                    refresh_at = time.monotonic() + 5
                time.sleep(0.2)
        finally:
            terminate_child(process)
    print("Synthetic fixture stopped; owned child and temporary stores cleaned.", flush=True)


def add(port: int, run_id: str) -> None:
    http = FixtureHTTP(port, run_id)
    http.verify_fixture()  # No mutation until the exact per-run fixture is verified.
    event = next(event for event in seed_events(run_id) if event["session_id"] == session_id(run_id, "no_active"))
    event.update(raw_score=3, timestamp_ms=int(time.time() * 1000),
                 evidence={"synthetic": True, "fixture": "browser_smoke_v1", "status": "SUSPICIOUS",
                           "collision": 0, "blocked_path": 1})
    deliver(http, [event])
    refresh_heartbeat(http, "no_active")
    overview = http.verify_fixture()
    row = next(row for row in overview["assessments"] if row["session_id"] == event["session_id"])
    if row.get("status") != "SUSPICIOUS":
        raise RuntimeError("real B did not accept the synthetic polling transition")
    print(json.dumps({"result": "ADDED", "synthetic_testing_only": True, "added_events": 1,
                      "case": "no_active", "assessment": row["status"], "heartbeat": "accepted"}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("serve", help="own a temporary loopback fixture for a bounded duration")
    start.add_argument("--port", type=int, default=8002)
    start.add_argument("--duration", type=int, default=300)
    update = actions.add_parser("add", help="add one synthetic event + heartbeat to a verified fixture")
    update.add_argument("--port", type=int, default=8002)
    update.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        serve(args.port, args.duration) if args.action == "serve" else add(args.port, args.run_id)
        return 0
    except KeyboardInterrupt:
        print("Synthetic fixture interrupted; owned child cleanup completed.", flush=True)
        return 130
    except Exception as exc:
        # Never print HTTP bodies, inherited configuration, record payloads, or tracebacks.
        print(f"Synthetic fixture failed ({type(exc).__name__}); no response body or records printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
