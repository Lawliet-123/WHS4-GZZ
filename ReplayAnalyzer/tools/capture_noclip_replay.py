import argparse
import ctypes
import csv
import json
import signal
import subprocess
import sys
import time
from pathlib import Path


VK_F8 = 0x77
VK_F10 = 0x79


def parse_args():
    parser = argparse.ArgumentParser(
        description="Capture Noclip replay data with F8 toggle and F10 stop."
    )

    parser.add_argument(
        "--session-id",
        required=True,
        help="Replay session id, e.g. noclip_004",
    )

    parser.add_argument(
        "--player-id",
        default="player_001",
        help="Player id (default: player_001)",
    )

    return parser.parse_args()


def latest_timestamp(event_file: Path):
    if not event_file.exists():
        return None

    try:
        lines = event_file.read_text(encoding="utf-8").splitlines()

        for line in reversed(lines):
            if not line.strip():
                continue

            event = json.loads(line)
            timestamp = event.get("timestamp_ms")

            if isinstance(timestamp, (int, float)):
                return int(timestamp)

    except Exception:
        return None

    return None


def stop_detector(proc):
    if proc.poll() is not None:
        return

    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        proc.wait(timeout=3)

    except Exception:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


def trim_raw(event_file: Path, log_file: Path, raw_output: Path):
    sample_ids = set()

    with event_file.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue

            event = json.loads(line)

            sample_id = event.get(
                "evidence", {}
            ).get("sample_id")

            if isinstance(sample_id, int):
                sample_ids.add(sample_id)

    with log_file.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames

        rows = [
            row
            for row in reader
            if int(row["sample_id"]) in sample_ids
        ]

    with raw_output.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    return len(sample_ids), len(rows)


def main():
    args = parse_args()

    session_id = args.session_id
    player_id = args.player_id

    repo = Path.cwd()

    noclip_dir = Path(
        r"C:\Program Files (x86)\Steam\steamapps\common\MECCHA CHAMELEON"
        r"\Chameleon\Binaries\Win64\Mods\NoclipLogger"
    )

    log_file = noclip_dir / "noclip_log.csv"

    out_dir = (
        repo
        / "ReplayAnalyzer"
        / "replay-data"
        / "noclip"
        / session_id
    )

    raw_dir = out_dir / "raw"

    event_file = out_dir / "events.jsonl"
    manifest_file = out_dir / "manifest.json"
    raw_output = raw_dir / "noclip_log.csv"

    result_file = (
        noclip_dir
        / f"{session_id}_detection_results.csv"
    )

    detector = (
        repo
        / "client"
        / "detectors"
        / "noclip"
        / "main.py"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if event_file.exists():
        raise SystemExit(
            f"{session_id} events.jsonl already exists."
        )

    if not log_file.exists():
        raise SystemExit(
            "noclip_log.csv not found. "
            "Enter the game room first."
        )

    command = [
        sys.executable,
        str(detector),
        "--session-id",
        session_id,
        "--player-id",
        player_id,
        "--log-file",
        str(log_file),
        "--event-file",
        str(event_file),
        "--result-file",
        str(result_file),
        "--from-end",
    ]

    print()
    print("========================================")
    print(" Noclip Replay Capture")
    print("========================================")
    print(f"Session : {session_id}")
    print(f"Player  : {player_id}")
    print()
    print("F8  -> Noclip ON / OFF")
    print("F10 -> Stop and save")
    print("========================================")
    print()

    proc = subprocess.Popen(
        command,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    user32 = ctypes.windll.user32

    f8_prev = False
    f10_prev = False

    cheat_start = None
    cheat_end = None

    try:
        while proc.poll() is None:
            f8 = bool(
                user32.GetAsyncKeyState(VK_F8)
                & 0x8000
            )

            f10 = bool(
                user32.GetAsyncKeyState(VK_F10)
                & 0x8000
            )

            if f8 and not f8_prev:
                timestamp = latest_timestamp(
                    event_file
                )

                if timestamp is None:
                    print(
                        "[Replay] No sample yet."
                    )

                elif cheat_start is None:
                    cheat_start = timestamp
                    print(
                        f"\n[Replay] "
                        f"Noclip ON : "
                        f"{timestamp} ms\n"
                    )

                elif cheat_end is None:
                    cheat_end = timestamp
                    print(
                        f"\n[Replay] "
                        f"Noclip OFF: "
                        f"{timestamp} ms\n"
                    )

            if f10 and not f10_prev:
                print(
                    "\n[Replay] "
                    "F10 stop detected."
                )
                break

            f8_prev = f8
            f10_prev = f10

            time.sleep(0.03)

    finally:
        stop_detector(proc)

    if cheat_start is None or cheat_end is None:
        raise SystemExit(
            "Both Noclip ON and OFF "
            "must be recorded."
        )

    manifest = {
        "label": "CHEAT",
        "player_id": player_id,
        "session_id": session_id,
        "cheat_start_ms": cheat_start,
        "cheat_type": "NOCLIP",
        "cheat_end_ms": cheat_end,
    }

    manifest_file.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=4,
        ),
        encoding="utf-8",
    )

    event_count, raw_count = trim_raw(
        event_file,
        log_file,
        raw_output,
    )

    print()
    print("========================================")
    print(" Capture complete")
    print("========================================")
    print(f"ON     : {cheat_start} ms")
    print(f"OFF    : {cheat_end} ms")
    print(f"events : {event_count}")
    print(f"raw    : {raw_count}")
    print("========================================")


if __name__ == "__main__":
    main()
