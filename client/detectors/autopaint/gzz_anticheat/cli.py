from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Sequence

from .detector import AutoPaintDetector
from .behavior import PaintBehaviorDetector
from .logging_io import JsonlWriter
from .models import assessment_from_score
from .session import LuaControl, PaintTail, Session
from .telemetry import TelemetryForwarder


SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _safe_id(value: str) -> str:
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if not SAFE_ID.fullmatch(value) or value in {".", ".."} or value.endswith(".") or value.split(".")[0].upper() in reserved:
        raise argparse.ArgumentTypeError(
            "use only letters, digits, underscore, dot, and hyphen"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Client-side AutoPaint integrity and Lua behavior detector"
    )
    parser.add_argument("--session-id", required=True, type=_safe_id)
    parser.add_argument("--player-id", default="player_local", type=_safe_id)
    parser.add_argument(
        "--process-name", default="PenguinHotel-Win64-Shipping.exe"
    )
    parser.add_argument("--pid", type=int)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--event-heartbeat-ms", type=int, default=5000)
    parser.add_argument("--output-dir", type=Path, default=Path("logs"))
    parser.add_argument("--label", choices=("NORMAL", "CHEAT", "UNKNOWN"), default="UNKNOWN")
    parser.add_argument("--network-role", choices=("host", "client", "unknown"), default="unknown")
    parser.add_argument("--lua-mod-dir", type=Path, help="Installed UE4SS Mods/GZZPaintObserver folder")
    parser.add_argument("--telemetry", choices=("managed", "external", "off"), default="managed",
                        help="managed: own shared sender; external: use app-owned sender; off: local-only")
    return parser


def run(args: argparse.Namespace) -> int:
    if not math.isfinite(args.interval) or args.interval <= 0:
        raise ValueError("--interval must be greater than zero")
    if args.event_heartbeat_ms <= 0:
        raise ValueError("--event-heartbeat-ms must be greater than zero")
    if not math.isfinite(args.duration) or args.duration < 0:
        raise ValueError("--duration must be finite and nonnegative")
    if args.lua_mod_dir and not (args.lua_mod_dir / "Scripts" / "main.lua").is_file():
        raise ValueError("--lua-mod-dir must point to the installed GZZPaintObserver folder")

    with TelemetryForwarder(args.telemetry) as telemetry:
        return _run(args, telemetry)


def _run(args: argparse.Namespace, telemetry: TelemetryForwarder) -> int:
    from .windows_sensor import WindowsClientSensor

    sensor = WindowsClientSensor(args.process_name, pid=args.pid)
    session = Session(args)
    raw_path = session.root / "raw" / "integrity.jsonl"
    event_path = session.root / "events.jsonl"
    behavior = PaintBehaviorDetector(args.session_id, args.player_id) if args.lua_mod_dir else None
    if behavior:
        session.manifest["behavior_policy"] = behavior.policy()
        session.manifest["score_policy"] = "max(integrity_score, valid_behavior_score); threshold=10"
    tail = PaintTail(session.root / "raw" / "paint_calls.jsonl", behavior.consume if behavior else None)
    control = None
    detector = AutoPaintDetector()
    last_event_key: str | None = None
    last_event_ms = -args.event_heartbeat_ms

    print(
        f"[gzz] session={args.session_id} target={args.process_name} "
        f"raw={raw_path} events={event_path}",
        file=sys.stderr,
    )

    status = "COMPLETED"
    try:
        if args.lua_mod_dir:
            control = LuaControl(args.lua_mod_dir, session)
            behavior.expected_token = control.token
        with JsonlWriter(raw_path) as raw_writer, JsonlWriter(event_path) as event_writer:
            while True:
                loop_started = time.monotonic()
                if control:
                    control.pulse()
                snapshot = sensor.collect()
                snapshot["timestamp_ms"] = session.elapsed_ms()
                snapshot["session_id"] = args.session_id
                snapshot["player_id"] = args.player_id
                snapshot["clock_drift_ms"] = session.clock_drift_ms()
                snapshot["clock_alignment_valid"] = session.clock_valid
                paint = tail.poll(snapshot["timestamp_ms"])
                snapshot["paint_observer"] = paint
                # Timestamp after reading: newly flushed records must not look like future input.
                snapshot["timestamp_ms"] = session.elapsed_ms()
                behavior_result = None
                if behavior:
                    behavior.transport(parse_errors=paint["parse_errors"],
                                       stream_resets=paint["stream_resets"], backlogged=paint["backlogged"])
                    behavior_result = behavior.evaluate(snapshot["timestamp_ms"], clock_valid=session.clock_valid)
                    snapshot["behavior_assessment"] = behavior_result
                    session.manifest["behavior_summary"] = {
                        "ever_detected": behavior.ever_detected, "first_detected_ms": behavior.first_detected_ms,
                    }

                target_found = bool(snapshot["target"]["found"])
                sensor_healthy = bool(snapshot.get("sensor_healthy", target_found))
                if (target_found and sensor_healthy) or (behavior_result is not None and behavior_result["valid"]):
                    event = detector.evaluate(
                        snapshot,
                        session_id=args.session_id,
                        player_id=args.player_id,
                    )
                    status_name = assessment_from_score(event.raw_score)
                    if event.raw_score < 10 and (not sensor_healthy or not target_found
                            or (behavior_result is not None and not behavior_result["valid"])):
                        status_name = "INCOMPLETE"
                    snapshot["assessment"] = {
                        "status": status_name,
                        "raw_score": event.raw_score,
                        "reasons": event.reasons,
                    }
                    event_value = event.to_dict()
                    # The team's ReplayAnalyzer needs every scoring sample, including zero.
                    event_writer.write(event_value)
                    telemetry.send(event_value)
                    event_key = json.dumps(
                        {
                            "evidence": event.evidence,
                            "reasons": event.reasons,
                            "raw_score": event.raw_score,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    should_emit = (
                        event_key != last_event_key
                        or event.timestamp_ms - last_event_ms >= args.event_heartbeat_ms
                    )
                    if should_emit:
                        print(
                            json.dumps(
                                event_value,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            flush=True,
                        )
                        print(
                            f"[gzz] {status_name} score={event.raw_score} t={event.timestamp_ms}ms"
                            + (f" Integrity={event.evidence['integrity_score']} "
                               f"Behavior={behavior_result['score']} BehaviorState={behavior_result['state']}"
                               if behavior_result is not None else ""),
                            file=sys.stderr,
                        )
                        last_event_key = event_key
                        last_event_ms = event.timestamp_ms
                elif not target_found:
                    snapshot["assessment"] = {"status": "TARGET_NOT_FOUND"}
                else:
                    snapshot["assessment"] = {"status": "SENSOR_ERROR"}

                raw_writer.write(snapshot)
                session.save()
                if control:
                    print(f"[gzz] Lua={paint['state']} calls={paint['calls']} hooks={paint['health'].get('registered_hooks', 0)}", file=sys.stderr)

                if args.once:
                    break
                if args.duration > 0 and snapshot["timestamp_ms"] >= args.duration * 1000:
                    break
                delay = args.interval - (time.monotonic() - loop_started)
                deadline = time.monotonic() + max(0, delay)
                while time.monotonic() < deadline:
                    if control:
                        control.pulse()
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        status = "STOPPED"
        print("\n[gzz] stopped", file=sys.stderr)
    except BaseException:
        status = "ERROR"
        raise
    finally:
        try:
            if control:
                control.close()
                # Allow one short collector flush; never synthesize missing callbacks.
                time.sleep(0.3)
        finally:
            # Drain the final collector acknowledgement without inventing another scoring sample.
            session.manifest["lua_summary"] = tail.poll(session.elapsed_ms())
            session.clock_drift_ms()
            session.save(status=status)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n[gzz] stopped", file=sys.stderr)
        return 130
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        print(f"[gzz] error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
