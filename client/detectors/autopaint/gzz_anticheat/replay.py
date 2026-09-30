from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .detector import AutoPaintDetector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay raw AutoPaint sensor JSONL")
    parser.add_argument("raw_log", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    detector = AutoPaintDetector()
    destination = args.output.open("x", encoding="utf-8", newline="\n") if args.output else None
    try:
        with args.raw_log.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                snapshot = json.loads(line)
                integrity_valid = snapshot.get("target", {}).get("found") and snapshot.get("sensor_healthy", True)
                if not integrity_valid and not snapshot.get("behavior_assessment", {}).get("valid"):
                    continue
                event = detector.evaluate(
                    snapshot,
                    session_id=str(snapshot["session_id"]),
                    player_id=str(snapshot.get("player_id", "player_local")),
                ).to_dict()
                encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                print(encoded)
                if destination:
                    destination.write(encoded + "\n")
    finally:
        if destination:
            destination.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
