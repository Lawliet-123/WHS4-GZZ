"""Team-friendly ``manifest.json`` / ``events.jsonl`` session output."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .events import DetectorDecision, SensorEvent, TeamDetectionEvent


SESSION_SCHEMA_VERSION = "meccha.telemetry-session.v1"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _utc_text(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else float(timestamp)
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _safe_session_name(value: str) -> str:
    candidate = _SAFE_NAME.sub("_", str(value).strip()).strip("._")
    if not candidate:
        raise ValueError("session_id does not contain a usable file name")
    return candidate[:128]


class SessionTelemetryWriter:
    """Append normalized records and maintain an atomic session manifest."""

    def __init__(
        self,
        root: str | Path,
        *,
        session_id: str,
        game_executable: str,
        producer_name: str = "meccha-esp-localguard",
        producer_version: str = "0.2.0",
        host_identity: Mapping[str, Any] | None = None,
        test_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.session_id = str(session_id).strip()
        if not self.session_id:
            raise ValueError("session_id must be non-empty")
        self.root = Path(root).expanduser().resolve()
        self.session_dir = self.root / _safe_session_name(self.session_id)
        self.raw_dir = self.session_dir / "raw"
        self.events_path = self.session_dir / "events.jsonl"
        self.manifest_path = self.session_dir / "manifest.json"
        # One session id represents exactly one test run. Reusing a directory
        # would silently mix old and new JSONL while resetting manifest counts.
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.session_dir.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise FileExistsError(
                f"telemetry session already exists: {self.session_dir}; "
                "choose a new --session-id"
            ) from exc
        self.raw_dir.mkdir(exist_ok=False)
        self._lock = threading.RLock()
        self._closed = False
        self._event_count = 0
        self._raw_event_count = 0
        self._raw_counts: dict[str, int] = {}
        self._written_team_event_ids: set[str] = set()
        self._manifest: dict[str, Any] = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": self.session_id,
            "status": "running",
            "failure_reason": None,
            "started_at_utc": _utc_text(),
            "ended_at_utc": None,
            "game_executable": str(game_executable),
            "producer": {
                "name": str(producer_name),
                "version": str(producer_version),
                "pid": os.getpid(),
            },
            "host_identity": dict(host_identity or {}),
            "test_metadata": dict(test_metadata or {}),
            "files": {"events": "events.jsonl", "raw": "raw/"},
            "event_count": 0,
            "raw_event_count": 0,
            "common_event_contract": {
                "fields": [
                    "session_id",
                    "player_id",
                    "module",
                    "timestamp_ms",
                    "evidence",
                    "reasons",
                    "raw_score",
                ],
                "timestamp_ms": "elapsed milliseconds from session start",
            },
            "format_note": (
                "events.jsonl follows the team result payload supplied on "
                "2026-09-19; raw sensor records remain under raw/."
            ),
        }
        self.events_path.touch(exist_ok=True)
        self._write_manifest_locked()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("session telemetry writer is closed")

    def _write_manifest_locked(self) -> None:
        temporary = self.manifest_path.with_name(
            f".{self.manifest_path.name}.{uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(
                    self._manifest,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.manifest_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _validate_session(self, record: object) -> None:
        if not hasattr(record, "session_id"):
            raise TypeError("record must contain session_id")
        if record.session_id != self.session_id:
            raise ValueError("record session_id does not match writer session")

    def append_team_event(
        self,
        record: TeamDetectionEvent,
        *,
        idempotency_key: str | None = None,
    ) -> bool:
        """Append one exact team-wide detector result to ``events.jsonl``."""

        if not isinstance(record, TeamDetectionEvent):
            raise TypeError("record must be TeamDetectionEvent")
        self._validate_session(record)
        line = record.to_json()
        event_key = idempotency_key or sha256(line.encode("utf-8")).hexdigest()
        if not isinstance(event_key, str) or not event_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        with self._lock:
            self._ensure_open()
            if event_key in self._written_team_event_ids:
                return False
            # Rewrite through a sibling temp file.  This is more expensive than
            # plain append, but team detection events are sparse and an atomic
            # replace avoids leaving a partial JSON line after an I/O failure.
            temporary = self.events_path.with_name(
                f".{self.events_path.name}.{uuid4().hex}.tmp"
            )
            try:
                previous = self.events_path.read_bytes()
                with temporary.open("wb") as handle:
                    handle.write(previous)
                    if previous and not previous.endswith(b"\n"):
                        handle.write(b"\n")
                    handle.write(line.encode("utf-8"))
                    handle.write(b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(self.events_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            self._written_team_event_ids.add(event_key)
            self._event_count += 1
            return True

    def append_raw_event(self, record: SensorEvent) -> Path:
        """Append one factual sensor record to a sensor-specific raw JSONL."""

        if not isinstance(record, SensorEvent):
            raise TypeError("record must be SensorEvent")
        self._validate_session(record)
        safe_sensor = _safe_session_name(record.sensor_id)
        destination = (self.raw_dir / f"{safe_sensor}.jsonl").resolve()
        if destination.parent != self.raw_dir.resolve():
            raise ValueError("raw event path must stay inside raw directory")
        with self._lock:
            self._ensure_open()
            with destination.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(record.to_json())
                handle.write("\n")
                handle.flush()
            self._raw_event_count += 1
            self._raw_counts[safe_sensor] = self._raw_counts.get(safe_sensor, 0) + 1
        return destination

    def append(self, record: SensorEvent | DetectorDecision | TeamDetectionEvent) -> None:
        """Compatibility dispatcher.

        New code should call :meth:`append_raw_event` or
        :meth:`append_team_event` explicitly.  DetectorDecision is retained as
        an internal migration record and is written to ``raw/decisions.jsonl``.
        """

        if isinstance(record, SensorEvent):
            self.append_raw_event(record)
            return
        if isinstance(record, TeamDetectionEvent):
            self.append_team_event(record)
            return
        if not isinstance(record, DetectorDecision):
            raise TypeError(
                "record must be SensorEvent, DetectorDecision, or TeamDetectionEvent"
            )
        self._validate_session(record)
        destination = self.raw_dir / "decisions.jsonl"
        with self._lock:
            self._ensure_open()
            with destination.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(record.to_json())
                handle.write("\n")
                handle.flush()
            self._raw_event_count += 1
            self._raw_counts["decisions"] = self._raw_counts.get("decisions", 0) + 1

    def write_raw_json(self, name: str, value: Mapping[str, Any]) -> Path:
        safe_name = _safe_session_name(name)
        if not safe_name.lower().endswith(".json"):
            safe_name += ".json"
        destination = (self.raw_dir / safe_name).resolve()
        if destination.parent != self.raw_dir.resolve():
            raise ValueError("raw artifact must stay inside the session raw directory")
        encoded = json.dumps(
            dict(value), ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
        )
        with self._lock:
            self._ensure_open()
            destination.write_text(encoded + "\n", encoding="utf-8")
        return destination

    def close(
        self,
        *,
        status: str = "completed",
        failure_reason: str | None = None,
    ) -> None:
        with self._lock:
            if self._closed:
                return
            self._manifest["status"] = str(status)
            self._manifest["failure_reason"] = (
                str(failure_reason) if failure_reason else None
            )
            self._manifest["ended_at_utc"] = _utc_text()
            self._manifest["event_count"] = self._event_count
            self._manifest["raw_event_count"] = self._raw_event_count
            self._manifest["raw_counts"] = dict(sorted(self._raw_counts.items()))
            self._write_manifest_locked()
            self._closed = True

    def __enter__(self) -> "SessionTelemetryWriter":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close(
            status="failed" if exc_type is not None else "completed",
            failure_reason=str(exc) if exc is not None else None,
        )


__all__ = ["SESSION_SCHEMA_VERSION", "SessionTelemetryWriter"]
