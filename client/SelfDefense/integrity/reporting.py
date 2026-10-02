"""Operational integrity records and optional shared forwarding, not cheat scoring."""
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
    print("[Integrity] " + message, file=sys.stderr, flush=True)


class SessionLog:
    def __init__(self, directory, session, player, *, start_ms=None, pin, root, synthetic=False):
        self.session, self.player = validate_identifier(session), validate_identifier(player)
        self.pin, self.synthetic = pin, synthetic
        self.run_id = uuid.uuid4().hex
        now = time.time_ns() // 1_000_000
        if start_ms is not None and (type(start_ms) is not int or not 0 <= start_ms <= now):
            raise ValueError("invalid time origin")
        session_root = directory.resolve() / session
        session_root.mkdir(parents=True, exist_ok=True)
        clock_path = session_root / "session-clock.json"
        identity = dict(session_id=session, player_id=player, baseline_sha256=pin,
                        protected_root=str(root), synthetic=synthetic)
        clock = {**identity, "start_unix_ms": now if start_ms is None else start_ms,
                 "timestamp_basis": "local_session_start" if start_ms is None else "launcher_session_start"}
        try:
            with clock_path.open("x", encoding="utf-8") as stream:
                json.dump(clock, stream)
        except FileExistsError:
            pass
        clock = json.loads(clock_path.read_text(encoding="utf-8"))
        if (any(clock.get(k) != v for k, v in identity.items())
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
        (self.directory / "raw/integrity.jsonl").touch(exist_ok=False)
        self.manifest = {**clock, "module": "selfdefense", "kind": "file_integrity", "run_id": self.run_id,
                         "collector_version": "0.1.0", "run_status": "RUNNING",
                         "label": "SYNTHETIC" if synthetic else "UNKNOWN", "cheat_intervals": []}
        self.save_manifest()

    def elapsed_ms(self):
        return self.base_ms + (time.perf_counter_ns() - self.base_ns) // 1_000_000

    def save_manifest(self):
        path = self.directory / "manifest.tmp"
        path.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        path.replace(self.directory / "manifest.json")

    def finish(self, code, reason):
        self.manifest.update(run_status="STOPPED" if code in (0, 1) else "ERROR", exit_code=code,
                             stop_reason=reason, run_ended_timestamp_ms=self.elapsed_ms())
        self.save_manifest()

    def event(self, scan, start, end, sample_id):
        summary = scan.summary()
        findings = [x for x in scan.files if x["state"] != "MATCH"]
        return {"session_id": self.session, "player_id": self.player, "module": "selfdefense",
                "timestamp_ms": end, "raw_score": 0, "reasons": scan.reasons(),
                "evidence": {**summary, "kind": "file_integrity", "component": "integrity",
                             "scan_start_ms": start, "scan_end_ms": end, "sample_id": sample_id,
                             "run_id": self.run_id, "synthetic": self.synthetic, "timestamp_basis": self.basis,
                             "baseline_sha256": self.pin, "baseline_trust": "external_sha256_pin",
                             "findings": findings[:20], "findings_truncated": len(findings) > 20}}

    def write(self, scan, event):
        raw = {"session_id": self.session, "player_id": self.player, "run_id": self.run_id,
               "timestamp_ms": event["timestamp_ms"], "evidence": event["evidence"], "files": scan.files}
        with (self.directory / "raw/integrity.jsonl").open("ab") as stream:
            stream.write(json.dumps(raw, ensure_ascii=False).encode("utf-8") + b"\n")
        with (self.directory / "events.jsonl").open("ab") as stream:
            stream.write(encode_event(event) + b"\n")


class Sender:
    def __init__(self, mode):
        self.mode, self.owned, self.enabled = mode, False, False
        self.failed = 0

    def start(self):
        if self.mode == "off":
            diagnostic("telemetry=off; local records only")
            return
        try:
            if self.mode == "managed":
                # Separate processes must never accidentally share the default outbox.
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
            diagnostic(f"TELEMETRY_UNAVAILABLE {type(exc).__name__}; check URL/token/outbox and restart; local scans continue")

    def send(self, event):
        if self.enabled:
            try:
                logger.send_detection(event)
            except Exception as exc:
                self.failed += 1
                diagnostic(f"ENQUEUE_FAILED {type(exc).__name__}; local event retained, not automatically replayed")

    def close(self):
        if not self.owned:
            return
        try:
            diagnostic(f"flushed={logger.flush_client(timeout=3)}; queued is not delivered")
            status = logger.get_client_status()
            diagnostic(f"pending={status.pending} failed={status.failed} "
                       f"delivery={status.last_delivery} worker_alive={status.worker_alive}")
        except (Exception, KeyboardInterrupt) as exc:
            diagnostic(f"FLUSH_FAILED {type(exc).__name__}")
        finally:
            try:
                diagnostic(f"sender_stopped={logger.shutdown_client(timeout=5)}")
            except (Exception, KeyboardInterrupt) as exc:
                diagnostic(f"SHUTDOWN_FAILED {type(exc).__name__}")
