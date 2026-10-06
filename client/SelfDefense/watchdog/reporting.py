"""Local operational records and optional shared forwarding; never computes cheat scores."""
from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from shared import logger
from shared.config import ClientConfig
from shared.schema import encode_event, validate_identifier


def diagnostic(message):
    print("[SelfDefense] " + message, file=sys.stderr, flush=True)


class SessionLog:
    def __init__(self, root: Path, session_id: str, player_id: str, *, start_unix_ms=None, synthetic=False):
        self.session_id = validate_identifier(session_id)
        self.player_id = validate_identifier(player_id)
        self.synthetic = synthetic
        self.run_id = uuid.uuid4().hex
        now = time.time_ns() // 1_000_000
        if start_unix_ms is not None and (type(start_unix_ms) is not int or not 0 <= start_unix_ms <= now):
            raise ValueError("session start must be an elapsed-time origin in the past")
        session_root = root.resolve() / session_id
        session_root.mkdir(parents=True, exist_ok=True)
        clock_path = session_root / "session-clock.json"
        initial = {"session_id": session_id, "player_id": player_id, "synthetic": synthetic,
                   "start_unix_ms": now if start_unix_ms is None else start_unix_ms,
                   "timestamp_basis": "local_session_start" if start_unix_ms is None else "launcher_session_start"}
        try:
            with clock_path.open("x", encoding="utf-8") as stream:
                json.dump(initial, stream, ensure_ascii=False)
        except FileExistsError:
            pass
        # Corrupt/partially written clocks cause a visible startup error, never a silent reset.
        clock = json.loads(clock_path.read_text(encoding="utf-8"))
        if (clock.get("session_id") != session_id or clock.get("player_id") != player_id
                or clock.get("synthetic") is not synthetic
                or type(clock.get("start_unix_ms")) is not int or not 0 <= clock["start_unix_ms"] <= now
                or clock.get("timestamp_basis") not in {"local_session_start", "launcher_session_start"}
                or (start_unix_ms is not None and clock["start_unix_ms"] != start_unix_ms)):
            raise ValueError("session clock conflicts; use the original identity/time or a new session ID")
        self.basis = clock["timestamp_basis"]
        self._base_ms = now - clock["start_unix_ms"]
        self._base_ns = time.perf_counter_ns()
        self.directory = session_root / "runs" / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / "raw").mkdir()
        (self.directory / "events.jsonl").touch(exist_ok=False)
        (self.directory / "raw" / "watchdog.jsonl").touch(exist_ok=False)
        manifest = {**clock, "module": "selfdefense", "run_id": self.run_id,
                    "label": "SYNTHETIC" if synthetic else "UNKNOWN", "kind": "module_health",
                    "cheat_intervals": [], "run_started_unix_ms": now,
                    "collector_version": "0.3.0", "run_status": "RUNNING"}
        self.manifest = manifest
        self._write_manifest()

    def _write_manifest(self):
        temporary = self.directory / "manifest.tmp"
        temporary.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        temporary.replace(self.directory / "manifest.json")

    def finish(self, exit_code, reason):
        # Forced termination cannot run this path: RUNNING is not proof of a live process.
        self.manifest.update(run_status="STOPPED" if exit_code == 0 else "ERROR",
                             exit_code=exit_code, stop_reason=reason,
                             run_ended_timestamp_ms=self.elapsed_ms())
        self._write_manifest()

    def elapsed_ms(self):
        return self._base_ms + (time.perf_counter_ns() - self._base_ns) // 1_000_000

    @staticmethod
    def _append(path, data):
        with path.open("ab") as stream:
            stream.write(data + b"\n")

    def raw(self, observation, timestamp):
        value = {**asdict(observation), "timestamp_ms": timestamp, "run_id": self.run_id,
                 "session_id": self.session_id, "player_id": self.player_id, "synthetic": self.synthetic}
        self._append(self.directory / "raw" / "watchdog.jsonl", json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def event(self, observation, timestamp):
        failed = observation.status in {"gave_up", "orphaned", "error", "exited", "scan_failed", "crashed", "unknown", "unregistered"}
        return {"session_id": self.session_id, "player_id": self.player_id, "module": "selfdefense",
                "timestamp_ms": timestamp, "raw_score": 0,
                "evidence": {"kind": "module_health", "status": "ERROR" if failed else "NORMAL",
                             "registry_status": observation.status, "target_module": observation.target,
                             "scope": observation.scope, "pid": observation.pid, "error_code": observation.error_code,
                             "mode": observation.mode, "restart_allowed": observation.restart_allowed,
                             "exit_code": observation.exit_code, "create_time": observation.create_time,
                             "health_scope": "process_liveness", "functional_health_checked": False,
                             "collector_version": "0.3.0",
                             "run_id": self.run_id, "synthetic": self.synthetic, "timestamp_basis": self.basis},
                "reasons": [observation.error_code or "Module " + observation.status]}

    def write_event(self, event):
        self._append(self.directory / "events.jsonl", encode_event(event))


class Reporter:
    def __init__(self, log: SessionLog, mode: str):
        self.log, self.mode = log, mode
        self.enabled = self.owned = False
        self.queued = self.enqueue_failed = self.local_errors = 0
        self._last_delivery_problem = None

    def start(self):
        if self.mode == "off":
            diagnostic("telemetry=off; local records only")
            return
        try:
            if self.mode == "managed":
                logger.configure_client(ClientConfig.from_env())
                self.owned = True
            else:
                state = logger.get_client_status()
                if state.closed or not state.worker_alive:
                    raise RuntimeError("external sender unavailable")
            self.enabled = True
        except Exception as exc:
            diagnostic(f"TELEMETRY_UNAVAILABLE {type(exc).__name__}; watchdog continues locally; fix settings and restart")

    def record(self, observation):
        timestamp = self.log.elapsed_ms()
        try:
            self.log.raw(observation, timestamp)
        except Exception as exc:
            self.local_errors += 1
            diagnostic(f"RAW_WRITE_FAILED {type(exc).__name__}")
        if not observation.report:
            return
        event = self.log.event(observation, timestamp)
        try:
            self.log.write_event(event)
        except Exception as exc:
            self.local_errors += 1
            diagnostic(f"EVENT_WRITE_FAILED {type(exc).__name__}")
        diagnostic(f"t={timestamp}ms target={observation.target!r} state={observation.status} code={observation.error_code}")
        if self.enabled:
            try:
                logger.send_detection(event)
                self.queued += 1
            except Exception as exc:
                self.enqueue_failed += 1
                diagnostic(f"ENQUEUE_FAILED {type(exc).__name__}; inspect local records; not automatically replayed")

    def check_delivery(self):
        if not self.enabled:
            return
        try:
            state = logger.get_client_status()
            problem = (state.failed, state.worker_alive, state.closed, state.last_code)
            if state.failed or not state.worker_alive or state.closed:
                if problem != self._last_delivery_problem:
                    diagnostic(f"DELIVERY_PROBLEM failed={state.failed} worker_alive={state.worker_alive} code={state.last_code}")
                self._last_delivery_problem = problem
            else:
                self._last_delivery_problem = None
        except Exception as exc:
            problem = type(exc).__name__
            if problem != self._last_delivery_problem:
                diagnostic("DELIVERY_STATUS_UNAVAILABLE " + problem)
            self._last_delivery_problem = problem

    def close(self):
        if self.owned:
            try:
                delivered = logger.flush_client(timeout=3)
                diagnostic(f"flushed={delivered}; false means records are still pending/failed")
            except (Exception, KeyboardInterrupt) as exc:
                diagnostic(f"FLUSH_FAILED {type(exc).__name__}")
            finally:
                try:
                    diagnostic(f"sender_stopped={logger.shutdown_client(timeout=5)}")
                except (Exception, KeyboardInterrupt) as exc:
                    diagnostic(f"SHUTDOWN_FAILED {type(exc).__name__}")
        diagnostic(f"queued={self.queued} enqueue_failed={self.enqueue_failed} local_errors={self.local_errors}; queued is not delivered")
