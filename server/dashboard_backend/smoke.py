"""Synthetic end-to-end check over a real loopback HTTP server."""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from shared.client import DetectionClient
from shared.config import ClientConfig
from .central_client import CentralDashboardClient, CentralQueryError


def main():
    with tempfile.TemporaryDirectory(prefix="dashboard-http-") as folder:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        tokens = {name: secrets.token_hex(24) for name in ("GZZ_TELEMETRY_TOKEN", "MECCHA_HEARTBEAT_TOKEN", "GZZ_DASHBOARD_TOKEN")}
        env = {**os.environ, **tokens,
               "GZZ_TELEMETRY_LOG_ROOT": str(Path(folder) / "detections"),
               "GZZ_SCORING_DB": str(Path(folder) / "scoring.sqlite3"),
               "MECCHA_HEARTBEAT_DB": str(Path(folder) / "heartbeat.sqlite3"),
               "GZZ_DASHBOARD_INDEX": str(Path(folder) / "dashboard.sqlite3")}
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
            cwd=Path(__file__).resolve().parents[2], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, token_name="GZZ_DASHBOARD_TOKEN", payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(url + path, data=data, headers={"Authorization": "Bearer " + tokens[token_name], "Content-Type": "application/json"})
            with opener.open(req, timeout=3) as response:
                return json.load(response)

        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise RuntimeError("local server failed to start")
                try:
                    request("/api/dashboard/overview")
                    break
                except urllib.error.URLError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("local server startup timed out")
                    time.sleep(0.1)

            config = ClientConfig(server_url=url, api_token=tokens["GZZ_TELEMETRY_TOKEN"], allow_insecure_loopback=True, outbox_path=Path(folder) / "outbox.sqlite3")
            with DetectionClient(config) as client:
                base = dict(session_id="smoke_session", player_id="smoke_pc", evidence={"synthetic": True}, reasons=["Synthetic integration input"])
                for i, score in enumerate((2, 3, 5, 3), 1):
                    client.send_detection({**base, "module": "godmode", "timestamp_ms": i * 1000, "raw_score": score})
                for i in range(2):
                    client.send_detection({**base, "module": "noclip", "timestamp_ms": 5000 + i, "raw_score": 3})
                client.send_detection({**base, "module": "selfdefense", "timestamp_ms": 6000, "raw_score": 0, "evidence": {"kind": "module_health", "synthetic": True}})
                if not client.flush(timeout=10):
                    raise RuntimeError("Shared delivery not acknowledged")
                client_status = client.status()

            heartbeat = {
                "schema_version": "meccha-heartbeat-3", "message_type": "heartbeat",
                "session_id": "smoke_session", "player_id": "smoke_pc", "client_id": "synthetic_scanner", "sequence": 1,
                "timestamp_ms": 7000, "sent_at_utc": "2026-10-03T00:00:00+00:00", "status": "healthy",
                "components": {"synthetic_component": {"status": "running", "required": True, "pid": None, "updated_at_ms": 6900, "stale_after_ms": 30000, "age_ms": 100, "details": {"synthetic": True}}},
                "transport": {"configured": True, "consecutive_failures": 0, "last_success_sequence": None, "last_error_type": None},
            }
            ack = request("/api/heartbeat", "MECCHA_HEARTBEAT_TOKEN", heartbeat)
            overview = request("/api/dashboard/overview")
            events = request("/api/dashboard/events")
            scope = "/api/dashboard/sessions/smoke_session/players/smoke_pc"
            snapshot, history, status = request(scope + "/snapshot"), request(scope + "/history"), request(scope + "/status")
            assert len(events["items"]) == 7
            assert len(history["items"]) == 4
            assert next(row for row in snapshot["modules"] if row["module"] == "noclip")["raw_score"] == 3
            assert snapshot["score"] is None and snapshot["status"] == "SUSPICIOUS"
            assert ack["accepted"] and status["sources"][0]["state"] == "healthy"
            assert overview["counts"]["operational_events"] == 1
            central = CentralDashboardClient(url, tokens["GZZ_DASHBOARD_TOKEN"])
            assert central.health() == {"status": "ok"}
            verdict = central.verdict("smoke_session", "smoke_pc")
            assert verdict == snapshot["final_verdict"]
            assert central.heartbeat("smoke_session", "synthetic_scanner")["payload"]["status"] == "healthy"
            for client, session, expected in (
                    (CentralDashboardClient(url, "wrong-synthetic-token"), "smoke_session", 401),
                    (central, "missing", 404)):
                try:
                    client.verdict(session, "smoke_pc")
                    raise AssertionError("expected query failure")
                except CentralQueryError as exc:
                    assert exc.status_code == expected
            print(json.dumps({"result": "PASS", "synthetic_data_only": True, "app": "C server.main + 8-B router", "transport": "real loopback HTTP", "stored_events": 7, "godmode_history_events": 4, "pending": client_status.pending, "failed": client_status.failed, "final_assessment": verdict["status"], "heartbeat": "accepted", "existing_C_endpoints": "PASS", "server_side_client": "PASS"}, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stderr:
                process.stderr.close()


if __name__ == "__main__":
    main()
