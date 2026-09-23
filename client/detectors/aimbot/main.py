"""
main.py
Sensor -> Detector -> 화면 출력까지 실제로 연결해서 돌리는 진입점.

흐름:
  MECCHA Telemetry (UE4SS가 남긴 JSONL)
        -> MecchaAimTelemetrySensor
        -> ShotEvent / confirmed outcome
        -> AimbotDetector.ingest_event()
        -> DetectionResult(dict)
        -> main.py가 화면에 출력
"""

import argparse
import json
import time
from pathlib import Path

from detector.aimbot_detector import AimbotDetector
from sensors.meccha_aim_telemetry_sensor import MecchaAimTelemetrySensor

DEFAULT_LOG_PATH = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\MECCHA CHAMELEON"
    r"\Chameleon\Binaries\Win64\ue4ss\Mods\DamageLogger"
    r"\meccha_aim_telemetry.jsonl"
)
POLL_SECONDS = 1.0


def parse_args():
    parser = argparse.ArgumentParser(description="MECCHA aimbot telemetry detector")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="main.lua가 기록하는 meccha_aim_telemetry.jsonl 경로",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print("=" * 50)
    print("MECCHA CHAMELEON - Aimbot Anti-Cheat")
    print("=" * 50)
    print(f"[INFO] Telemetry path: {args.log_path}")
    print("[INFO] Waiting for MECCHA telemetry...")

    sensor = MecchaAimTelemetrySensor(args.log_path)
    detector = AimbotDetector()

    print("[INFO] Telemetry connected.")
    print("[INFO] Aimbot detection started.")
    print("[INFO] Press Ctrl+C to stop.\n")

    try:
        while True:
            for event in sensor.read_events():
                result = detector.ingest_event(event)
                if result:
                    print("-" * 60)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n종료.")


if __name__ == "__main__":
    main()

