"""Local-first, seven-field operational events and the unchanged shared API."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import uuid

from shared import logger
from shared.config import ClientConfig
from shared.schema import encode_event, validate_identifier


def diagnostic(message):
    print("[AntiDebug] " + message, file=sys.stderr, flush=True)


class SessionLog:
    def __init__(self, directory: Path, session, player, *, registry_path=None, start_ms=None, synthetic=False):
        self.session, self.player = validate_identifier(session), validate_identifier(player)
        self.synthetic, self.run_id = synthetic, uuid.uuid4().hex
        now = time.time_ns() // 1_000_000
        if start_ms is not None and (type(start_ms) is not int or not 0 <= start_ms <= now):
            raise ValueError("invalid time origin")
        session_root = directory.resolve() / session
        session_root.mkdir(parents=True, exist_ok=True)
        clock_path = session_root / "session-clock.json"
        identity = {"session_id": session, "player_id": player, "synthetic": synthetic,
                    "registry_path": None if registry_path is None else str(registry_path.resolve())}
        clock = {**identity, "start_unix_ms": now if start_ms is None else start_ms,
                 "timestamp_basis": "local_session_start" if start_ms is None else "launcher_session_start"}
        try:
            with clock_path.open("x", encoding="utf-8") as stream:
                json.dump(clock, stream)
        except FileExistsError:
            pass
        clock = json.loads(clock_path.read_text(encoding="utf-8"))
        if (type(clock) is not dict or any(clock.get(k) != v for k, v in identity.items())
                or type(clock.get("synthetic")) is not bool
                or type(clock.get("start_unix_ms")) is not int or not 0 <= clock["start_unix_ms"] <= now
                or clock.get("timestamp_basis") not in {"local_session_start", "launcher_session_start"}
                or (start_ms is not None and clock["start_unix_ms"] != start_ms)):
            raise ValueError("session identity conflict")
        self.basis = clock["timestamp_basis"]
        self.base_ms, self.base_ns = now - clock["start_unix_ms"], time.perf_counter_ns()
        self.directory = session_root / "runs" / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / "raw").mkdir()
        (self.directory / "events.jsonl").touch(exist_ok=False)
        (self.directory / "raw/anti_debug.jsonl").touch(exist_ok=False)
        self.manifest = {**clock, "module": "selfdefense", "kind": "debugger_presence", "run_id": self.run_id,
                         "collector_version": "0.1.0", "run_status": "RUNNING",
                         "label": "SYNTHETIC" if synthetic else "UNKNOWN", "cheat_intervals": []}
        self.save_manifest()

    def elapsed_ms(self):
        return self.base_ms + (time.perf_counter_ns() - self.base_ns) // 1_000_000

    def save_manifest(self):
        temporary = self.directory / "manifest.tmp"
        temporary.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.directory / "manifest.json")

    def finish(self, code, reason):
        self.manifest.update(run_status="STOPPED" if code in (0, 1) else "ERROR", exit_code=code,
                             stop_reason=reason, run_ended_timestamp_ms=self.elapsed_ms())
        self.save_manifest()

    def event(self, result, start, end, sample):
        return {"session_id": self.session, "player_id": self.player, "module": "selfdefense",
                "timestamp_ms": end, "raw_score": 0, "reasons": result.reasons(),
                "evidence": {**result.summary(), "kind": "debugger_presence", "component": "anti_debug",
                             "method": "CheckRemoteDebuggerPresent", "run_id": self.run_id,
                             "sample_id": sample, "synthetic": self.synthetic, "timestamp_basis": self.basis,
                             "scan_start_ms": start, "scan_end_ms": end, "targets": result.targets}}

    def write(self, event):
        # Validation and local durability precede enqueue; a local write failure is visible.
        payload = encode_event(event)
        raw = {"session_id": self.session, "player_id": self.player, "run_id": self.run_id,
               "timestamp_ms": event["timestamp_ms"], **event["evidence"]}
        with (self.directory / "raw/anti_debug.jsonl").open("ab") as stream:
            stream.write(json.dumps(raw, ensure_ascii=False).encode("utf-8") + b"\n")
        with (self.directory / "events.jsonl").open("ab") as stream:
            stream.write(payload + b"\n")


class Sender:
    def __init__(self, mode):
        self.mode, self.owned, self.enabled = mode, False, False
        self.failed, self.last_problem = 0, None

    def start(self):
        if self.mode == "off":
            diagnostic("telemetry=off; local records only")
            return
        try:
            if self.mode == "managed":
                if not os.environ.get("GZZ_TELEMETRY_OUTBOX"):
                    raise ValueError("explicit module outbox required")
                logger.configure_client(ClientConfig.from_env())
                self.owned = True
            else:
                status = logger.get_client_status()
                if status.closed or not status.worker_alive:
                    raise ValueError("external sender unavailable")
            self.enabled = True
        except Exception as exc:
            diagnostic(f"TELEMETRY_UNAVAILABLE {type(exc).__name__}; fix URL/token/outbox and restart; local monitoring continues")

    def send(self, event):
        if not self.enabled:
            return
        try:
            logger.send_detection(event)
        except Exception as exc:
            self.failed += 1
            diagnostic(f"ENQUEUE_FAILED {type(exc).__name__}; local event retained, not automatically replayed")
        try:
            state = logger.get_client_status()
            problem = (state.failed, state.worker_alive, state.closed, state.last_code)
            if state.failed or not state.worker_alive or state.closed:
                if self.last_problem != problem:
                    diagnostic(f"DELIVERY_PROBLEM failed={state.failed} worker_alive={state.worker_alive} code={state.last_code}")
                self.last_problem = problem
            else:
                self.last_problem = None
        except Exception as exc:
            if self.last_problem != type(exc).__name__:
                diagnostic(f"DELIVERY_STATUS_UNAVAILABLE {type(exc).__name__}")
            self.last_problem = type(exc).__name__

    def close(self):
        if not self.owned:
            return
        try:
            diagnostic(f"flushed={logger.flush_client(timeout=3)}; queued is not delivered")
            state = logger.get_client_status()
            diagnostic(f"pending={state.pending} failed={state.failed} delivery={state.last_delivery}")
        except (Exception, KeyboardInterrupt) as exc:
            diagnostic(f"FLUSH_FAILED {type(exc).__name__}")
        finally:
            try:
                diagnostic(f"sender_stopped={logger.shutdown_client(timeout=5)}")
            except (Exception, KeyboardInterrupt) as exc:
                diagnostic(f"SHUTDOWN_FAILED {type(exc).__name__}")
