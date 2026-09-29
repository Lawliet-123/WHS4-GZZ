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
import signal
import sys
import time
from pathlib import Path

# When launched as ``python client/detectors/aimbot/main.py``, Python puts
# this script's directory on sys.path, not the repository root where shared/ lives.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from detector.aimbot_detector import AimbotDetector
from sensors.meccha_aim_telemetry_sensor import MecchaAimTelemetrySensor
from shared.config import ClientConfig
from shared.errors import SharedError
from shared.logger import (
    configure_client,
    flush_client,
    send_detection,
    shutdown_client,
)

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
    # 런처는 끌 때 Ctrl+Break를 보낸다. 윈도 기본 처리는 즉시 종료라 아래 finally의
    # flush/shutdown이 안 돈다. KeyboardInterrupt로 바꿔 둔다.
    # 참고: client/Launcher/README.md "끌 때 정리 코드가 돌게 하려면 — 한 줄"
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    args = parse_args()
    print("=" * 50)
    print("MECCHA CHAMELEON - Aimbot Anti-Cheat")
    print("=" * 50)
    print(f"[INFO] Telemetry path: {args.log_path}")
    print("[INFO] Waiting for MECCHA telemetry...")

    sensor = MecchaAimTelemetrySensor(args.log_path)
    detector = AimbotDetector()

    client_configured = False
    try:
        configure_client(ClientConfig.from_env())
        client_configured = True
        print("[INFO] Shared telemetry client configured.")
    except SharedError as exc:
        # 중앙 전송 설정이 없거나 잘못돼도 로컬 탐지는 계속한다.
        print(
            f"[WARNING] Shared telemetry is unavailable: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

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
                    if client_configured:
                        try:
                            receipt = send_detection(result)
                            # 'queued'는 로컬 outbox에 저장됐다는 뜻이며,
                            # 중앙 서버가 수신했다는 확인 응답은 아니다.
                            print(f"[INFO] Shared telemetry queued: {receipt.event_id}")
                        except SharedError as exc:
                            print(
                                f"[WARNING] Shared telemetry rejected locally: "
                                f"{type(exc).__name__}: {exc}",
                                file=sys.stderr,
                            )
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n종료.")
    finally:
        if client_configured:
            try:
                flushed = flush_client(timeout=3)
                if not flushed:
                    print(
                        "[WARNING] Shared telemetry flush incomplete; pending or failed events remain.",
                        file=sys.stderr,
                    )
            except SharedError as exc:
                print(
                    f"[WARNING] Shared telemetry flush failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )

            try:
                stopped = shutdown_client(timeout=5)
                if not stopped:
                    print(
                        "[WARNING] Shared telemetry sender did not stop before timeout; "
                        "queued data remains in the outbox.",
                        file=sys.stderr,
                    )
            except SharedError as exc:
                print(
                    f"[WARNING] Shared telemetry shutdown failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
