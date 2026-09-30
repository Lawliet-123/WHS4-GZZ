"""Recalculate behavior from an existing session or ZIP, without DLL evidence."""
from __future__ import annotations

import argparse
import io
import json
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from .behavior import PaintBehaviorDetector, integer
from .cli import _safe_id
from .detector import AutoPaintDetector
from .logging_io import JsonlWriter
from .session import atomic_json


@contextmanager
def open_session(path: Path):
    if path.is_dir():
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        with (path / "raw" / "paint_calls.jsonl").open(encoding="utf-8") as records:
            yield manifest, records
    else:
        # Read only exact entries; never extract archive paths onto the filesystem.
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n == "manifest.json" or n.endswith("/manifest.json")]
            if len(names) != 1:
                raise ValueError("expected exactly one session manifest in ZIP")
            prefix = names[0][:-len("manifest.json")]
            manifest = json.loads(archive.read(names[0]))
            with archive.open(prefix + "raw/paint_calls.jsonl") as binary, io.TextIOWrapper(binary, encoding="utf-8") as records:
                yield manifest, records


def replay_records(manifest: dict, lines, *, interval_ms: int = 1000, on_sample=None) -> dict:
    """Regular event-time sampling. No future records or label-based rule selection."""
    duration = manifest.get("duration_ms")
    if not integer(duration) or duration < 0 or not integer(interval_ms) or interval_ms <= 0:
        raise ValueError("duration_ms and positive interval_ms must be integers")
    engine = PaintBehaviorDetector(str(manifest["session_id"]), str(manifest["player_id"]))
    composer = AutoPaintDetector()
    source = iter(lines)
    pending = None
    exhausted = False
    parse_errors = 0
    states, scores = Counter(), Counter()
    first_uv = None
    first_detection = None
    maximum_score = detected_samples = samples = 0
    now = 0
    while True:
        while True:
            if pending is None and not exhausted:
                try:
                    line = next(source)
                except StopIteration:
                    exhausted = True
                    break
                try:
                    if not line.endswith("\n"):
                        raise ValueError("incomplete final JSONL line")
                    pending = json.loads(line)
                    if not isinstance(pending, dict):
                        raise ValueError("not an object")
                except (ValueError, UnicodeError):
                    parse_errors += 1
                    engine.transport(parse_errors=parse_errors, stream_resets=0, backlogged=False)
                    pending = None
                    continue
            if pending is None:
                break
            timestamp = pending.get("timestamp_ms")
            if integer(timestamp) and timestamp > now:
                break
            if (first_uv is None and pending.get("function_name") == "PaintAtUVWithBrush"
                    and isinstance(pending.get("context"), dict)
                    and pending["context"].get("owner_is_local_pawn") is True
                    and pending.get("session_id") == engine.session_id):
                first_uv = timestamp
            engine.consume(pending)
            pending = None
        assessment = engine.evaluate(now, clock_valid=manifest.get("clock_alignment_valid") is True)
        states[assessment["state"]] += 1
        event = None
        if assessment["valid"]:
            event = composer.evaluate({"sensor_healthy": False, "behavior_assessment": assessment,
                                       "timestamp_ms": now}, session_id=engine.session_id,
                                      player_id=engine.player_id).to_dict()
            scores[str(event["raw_score"])] += 1
            maximum_score = max(maximum_score, event["raw_score"])
            if assessment["detected"]:
                detected_samples += 1
                if first_detection is None:
                    first_detection = now
        if on_sample:
            on_sample(assessment, event)
        samples += 1
        if now == duration:
            break
        now = min(now + interval_ms, duration)
    return {"session_id": engine.session_id, "analysis_kind": "behavior_only_replay",
            "label_for_comparison_only": manifest.get("label"),
            "network_role_reported": manifest.get("network_role_reported"),
            "policy": engine.policy(), "interval_ms": interval_ms, "samples": samples,
            "valid_samples": sum(scores.values()), "states": dict(states), "score_distribution": dict(scores),
            "detected_samples": detected_samples, "maximum_score": maximum_score,
            "first_detected_ms": first_detection, "first_observed_local_uv_ms": first_uv,
            "observed_uv_to_detection_ms": first_detection - first_uv if first_detection is not None and integer(first_uv) else None,
            "parse_errors": parse_errors, "rejected_records": engine.rejected_records,
            "note": "Derived replay, not a new play test. Latency is from an observed call, not cheat ON."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--output-dir", type=Path, help="New derived session folders; never overwrite")
    args = parser.parse_args(argv)
    if args.interval_ms <= 0:
        parser.error("--interval-ms must be positive")
    for path in args.sessions:
        with open_session(path) as (manifest, lines):
            if args.output_dir is None:
                summary = replay_records(manifest, lines, interval_ms=args.interval_ms)
            else:
                session_id = _safe_id(str(manifest["session_id"]))
                destination = args.output_dir / session_id
                destination.mkdir(parents=True, exist_ok=False)
                (destination / "raw").mkdir()
                derived = dict(manifest)
                derived.update(analysis_kind="behavior_only_replay", source=str(path.resolve()),
                               source_is_original_raw=True, output_is_new_play_test=False,
                               detector_version="0.3.0", status="ANALYZING")
                atomic_json(destination / "manifest.json", derived)
                try:
                    with JsonlWriter(destination / "events.jsonl") as events, JsonlWriter(destination / "raw/behavior.jsonl") as diagnostics:
                        def sample(assessment, event):
                            diagnostics.write(assessment)
                            if event is not None:
                                events.write(event)
                        summary = replay_records(manifest, lines, interval_ms=args.interval_ms, on_sample=sample)
                except BaseException:
                    derived["status"] = "ANALYSIS_ERROR"
                    atomic_json(destination / "manifest.json", derived)
                    raise
                derived.update(status="ANALYZED", behavioral_scoring_enabled=True,
                               score_scope="behavior_only", behavior_summary=summary,
                               raw_note="Original raw data remains at source; raw/behavior.jsonl is derived diagnostics.")
                atomic_json(destination / "manifest.json", derived)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
