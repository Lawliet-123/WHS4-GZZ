from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Sequence
from .cli import _safe_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mark cheat ON/OFF in a test session")
    parser.add_argument("--session-id", required=True, type=_safe_id)
    parser.add_argument("--state", required=True, choices=("ON", "OFF", "IN_ROOM", "INJECTED", "PAINT_DONE", "BRIDGE_STOP"))
    parser.add_argument("--label", default="AUTOPAINT", type=_safe_id)
    parser.add_argument("--output-dir", type=Path, default=Path("logs"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.output_dir / args.session_id
    metadata_path = root / "manifest.json"
    legacy = not metadata_path.exists()
    if legacy:
        metadata_path = args.output_dir / f"{args.session_id}.meta.json"
    if not metadata_path.is_file():
        raise SystemExit(f"metadata not found: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not legacy and metadata.get("status") != "RUNNING":
        raise SystemExit("session has stopped; do not mark a new time after the test")
    if legacy:
        timestamp_ms = max(0, time.time_ns() // 1_000_000 - int(metadata["started_at_unix_ms"]))
    else:
        timestamp_ms = max(0, (time.perf_counter_ns() - int(metadata["started_at_perf_counter_ns"])) // 1_000_000)
    value = {
        "session_id": args.session_id,
        "module": "autopaint",
        "label": args.label,
        "state": args.state,
        "timestamp_ms": timestamp_ms,
    }
    annotation_path = args.output_dir / f"{args.session_id}.annotations.jsonl" if legacy else root / "raw" / "annotations.jsonl"
    with annotation_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
