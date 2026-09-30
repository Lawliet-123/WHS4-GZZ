"""Summarize observed calls and coverage, without inventing a behavioral verdict."""
import argparse
import collections
import json
from pathlib import Path


def summarize(folder: Path) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    counts, local_inputs = collections.Counter(), collections.Counter()
    batch_sizes = collections.Counter()
    first_last: dict = {}
    health, warnings = {}, []
    stop_seen = False
    clock_resolutions = set()
    starts = 0
    path = folder / "raw" / "paint_calls.jsonl"
    if not path.exists():
        warnings.append("Lua log missing: check UE4SS installation and --lua-mod-dir")
    else:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("not an object")
                except ValueError:
                    warnings.append(f"Invalid or incomplete JSONL line {line_number}")
                    continue
                clock_resolutions.add(value.get("clock_resolution_ms"))
                if value.get("kind") == "collector_start":
                    starts += 1
                elif value.get("kind") == "collector_stop":
                    stop_seen = True
                    if value.get("dropped") or value.get("write_errors"):
                        warnings.append("Collector reported data loss at shutdown")
                elif value.get("kind") == "health":
                    health = value
                    if value.get("dropped") or value.get("write_errors") or value.get("callback_errors"):
                        warnings.append("Collector reported dropped records or errors")
                elif value.get("kind") == "call":
                    function = value.get("function_name", "unknown")
                    counts[function] += 1
                    timestamp = value.get("timestamp_ms")
                    first_last.setdefault(function, {"first_ms": timestamp})["last_ms"] = timestamp
                    if value.get("category") == "input" and value.get("context", {}).get("context_is_local_pawn") is True:
                        local_inputs[function] += 1
                    size = value.get("parameters", {}).get("stroke_count")
                    if isinstance(size, int):
                        batch_sizes[f"{function}:{size}"] += 1
    if not health:
        warnings.append("No hook health record; zero calls cannot establish absence")
    if starts != 1:
        warnings.append(f"Collector starts={starts}; require one game instance, no hot reload per test")
    if not stop_seen:
        warnings.append("No collector_stop; final buffered records may be missing")
    if clock_resolutions != {1}:
        warnings.append("Fine timing unavailable for some/all records")
    if not manifest.get("clock_alignment_valid", False):
        warnings.append("Clock alignment invalid or unknown")
    # The two unavailable IA hooks are not part of behavior-v1 prerequisites.
    missing = [h["function_name"] for h in health.get("hooks", []) if not h.get("registered") or not h.get("available")]
    behavior = manifest.get("behavior_summary")
    if not manifest.get("behavioral_scoring_enabled"):
        verdict = "NOT_ENABLED_IN_SOURCE_SESSION"
    elif behavior is None:
        verdict = "UNKNOWN"
    else:
        verdict = "DETECTED" if behavior.get("ever_detected") or behavior.get("detected_samples", 0) else "NO_DETECTION_RECORDED"
    return {
        "session_id": manifest["session_id"], "label": manifest.get("label"),
        "network_role_reported": manifest.get("network_role_reported"),
        "last_network_observation": health.get("network"),
        "behavioral_verdict": verdict, "behavior_summary": behavior, "calls_by_function": dict(counts),
        "observed_local_inputs": dict(local_inputs), "first_last_calls": first_last,
        "batch_size_histogram": dict(batch_sizes), "last_hook_health": health.get("hooks", []),
        "unavailable_hooks_at_end": missing,
        "cheat_intervals": manifest.get("cheat_intervals", []),
        "warnings": sorted(set(warnings + manifest.get("annotation_warnings", []))),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paint observer coverage and function-count summary (not detection)")
    parser.add_argument("session_folders", nargs="+", type=Path)
    args = parser.parse_args(argv)
    for folder in args.session_folders:
        print(json.dumps(summarize(folder), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
