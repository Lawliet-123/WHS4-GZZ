"""Session metadata and the opt-in, non-executable UE4SS control file."""
from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path


def replace_with_retry(temporary: Path, path: Path) -> None:
    # Windows readers may briefly deny replacement while holding an open handle.
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    replace_with_retry(temporary, path)


def read_annotations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if not line.endswith("\n"):  # Concurrent writer may not have completed its line.
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        except ValueError:
            continue
    return result


class Session:
    def __init__(self, args):
        self.root = (args.output_dir / args.session_id).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        (self.root / "raw").mkdir()
        self.start_ns = time.perf_counter_ns()
        self.start_unix_ms = time.time_ns() // 1_000_000
        self.clock_valid = True
        self.manifest = {
            "session_id": args.session_id, "player_id": args.player_id,
            "label": args.label, "cheat_type": "AUTOPAINT" if args.label == "CHEAT" else None,
            "cheat_start_ms": None, "cheat_end_ms": None,
            "schema_version": 2, "module": "autopaint", "collector_version": "0.4.1",
            "process_name": args.process_name, "requested_pid": args.pid,
            "network_role_reported": args.network_role,
            "started_at_unix_ms": self.start_unix_ms,
            "started_at_perf_counter_ns": self.start_ns,
            "timestamp_basis": "elapsed_ms_since_session_start",
            "lua_clock_basis": "same_machine_UTC_minus_started_at_unix_ms",
            "status": "RUNNING", "behavioral_scoring_enabled": args.lua_mod_dir is not None,
            "lua_requested": args.lua_mod_dir is not None,
            "annotations": [], "cheat_intervals": [], "annotation_warnings": [],
        }
        self.save()

    def elapsed_ms(self) -> int:
        return (time.perf_counter_ns() - self.start_ns) // 1_000_000

    def clock_drift_ms(self) -> int:
        drift = time.time_ns() // 1_000_000 - self.start_unix_ms - self.elapsed_ms()
        if abs(drift) > 250:
            self.clock_valid = False  # Sticky: a clock jump invalidates fine time comparisons.
        return drift

    def save(self, *, status: str | None = None) -> None:
        annotations = read_annotations(self.root / "raw" / "annotations.jsonl")
        intervals, warnings, opened = [], [], None
        for item in annotations:
            state, timestamp = item.get("state"), item.get("timestamp_ms")
            if not isinstance(timestamp, int) or timestamp < 0:
                warnings.append("Invalid annotation timestamp")
                continue
            if state == "ON":
                if opened is not None:
                    warnings.append("Duplicate ON without OFF")
                else:
                    opened = timestamp
            elif state == "OFF":
                if opened is None or timestamp < opened:
                    warnings.append("OFF without matching earlier ON")
                else:
                    intervals.append({"start_ms": opened, "end_ms": timestamp})
                    opened = None
        if opened is not None:
            intervals.append({"start_ms": opened, "end_ms": None})
            warnings.append("ON has no OFF; end time is unknown")
        if self.manifest["label"] == "CHEAT" and not intervals:
            warnings.append("Cheat ON/OFF times have not been marked")
        if self.manifest["label"] == "NORMAL" and intervals:
            warnings.append("NORMAL label conflicts with ON/OFF annotations")
        if len(intervals) > 1:
            warnings.append("Multiple intervals: use cheat_intervals, not the scalar fields")
        self.manifest.update(
            annotations=annotations, cheat_intervals=intervals, annotation_warnings=warnings,
            cheat_start_ms=intervals[0]["start_ms"] if len(intervals) == 1 else None,
            cheat_end_ms=intervals[0]["end_ms"] if len(intervals) == 1 else None,
            clock_alignment_valid=self.clock_valid,
        )
        if status:
            self.manifest.update(status=status, duration_ms=self.elapsed_ms())
        atomic_json(self.root / "manifest.json", self.manifest)


class LuaControl:
    """One game + one collector session per installed mod. Not a security boundary."""
    def __init__(self, mod_dir: Path, session: Session):
        self.path = mod_dir.resolve() / "session.control"
        if not (mod_dir / "Scripts" / "main.lua").is_file():
            raise ValueError("--lua-mod-dir must point to the installed GZZPaintObserver folder")
        if self.path.exists():
            old = dict(line.split("=", 1) for line in self.path.read_text(encoding="utf-8").splitlines() if "=" in line)
            if old.get("active") == "1" and abs(time.time() - float(old.get("heartbeat_unix_ms", "0")) / 1000) < 10:
                raise RuntimeError("another session is using this Lua mod; stop it first")
        self.session = session
        self.token = uuid.uuid4().hex
        self.last_pulse = 0.0
        self.pulse(force=True)

    def pulse(self, *, active: bool = True, force: bool = False) -> None:
        if not force and time.monotonic() - self.last_pulse < 0.5:
            return
        data = {
            "protocol": 1, "active": int(active), "token": self.token,
            "session_id": self.session.manifest["session_id"],
            "player_id": self.session.manifest["player_id"],
            "start_unix_ms": self.session.start_unix_ms,
            "heartbeat_unix_ms": time.time_ns() // 1_000_000,
            "output": (self.session.root / "raw" / "paint_calls.jsonl").as_posix(),
        }
        if any("\n" in str(v) or "\r" in str(v) for v in data.values()):
            raise ValueError("control values must not contain newlines")
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text("".join(f"{k}={v}\n" for k, v in data.items()), encoding="utf-8")
        replace_with_retry(temporary, self.path)
        self.last_pulse = time.monotonic()

    def close(self) -> None:
        self.pulse(active=False, force=True)


class PaintTail:
    """Bounded incremental reader; a missing Lua collector never means normal behavior."""
    def __init__(self, path: Path, on_record: Callable[[dict], None] | None = None):
        self.path, self.offset = path, 0
        self.on_record = on_record
        self.calls = self.errors = self.records = 0
        self.health: dict = {}
        self.last_seen_ms: int | None = None
        self.stopped = False
        self.stream_resets = 0
        self.file_identity = None
        self.discarding_line = False

    def poll(self, now_ms: int) -> dict:
        backlogged = False
        if self.path.exists():
            with self.path.open("rb") as source:
                stat = os.fstat(source.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.file_identity is not None and (identity != self.file_identity or stat.st_size < self.offset):
                    self.offset, self.health, self.last_seen_ms = 0, {}, None
                    self.stream_resets += 1
                    self.discarding_line = False
                self.file_identity = identity
                source.seek(self.offset)
                for _ in range(20000):
                    line = source.readline(65537)
                    if self.discarding_line:
                        self.offset = source.tell()
                        self.discarding_line = bool(line) and not line.endswith(b"\n")
                        if not line:
                            self.discarding_line = True
                            break
                        continue
                    if len(line) > 65536:
                        self.errors += 1
                        self.offset = source.tell()
                        self.discarding_line = not line.endswith(b"\n")
                        break
                    if not line or not line.endswith(b"\n"):
                        break
                    self.offset = source.tell()
                    try:
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise ValueError("not an object")
                        self.records += 1
                        self.last_seen_ms = now_ms
                        if value.get("kind") == "call":
                            self.calls += 1
                        if value.get("kind") == "health":
                            self.health = value
                        if value.get("kind") == "collector_stop":
                            self.stopped = True
                        elif value.get("kind") == "collector_start":
                            self.stopped = False
                    except (ValueError, UnicodeError):
                        self.errors += 1
                        continue
                    # Keep observer failures visible; do not count them as malformed JSON.
                    if self.on_record is not None:
                        self.on_record(value)
                else:
                    backlogged = source.tell() < os.fstat(source.fileno()).st_size
        return {
            "state": "MISSING" if self.last_seen_ms is None else ("STALE" if now_ms - self.last_seen_ms > 3000 else "RECEIVING"),
            "records": self.records, "calls": self.calls, "parse_errors": self.errors,
            "health": self.health,
            "stop_acknowledged": self.stopped,
            "stream_resets": self.stream_resets, "backlogged": backlogged,
        }
