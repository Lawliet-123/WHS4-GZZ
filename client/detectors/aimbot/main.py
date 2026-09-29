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
# 첫 UE4SS 이벤트의 실제 관찰 시각으로 런처 타임라인을 고정하므로, 1초
# 폴링은 모듈 간 위치 오차가 너무 크다. JSONL의 새 줄만 읽으므로 100ms 폴링은
# 부담이 작고 타임라인 오차 상한도 약 100ms로 줄인다.
POLL_SECONDS = 0.1


def parse_args():
    parser = argparse.ArgumentParser(description="MECCHA aimbot telemetry detector")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="main.lua가 기록하는 meccha_aim_telemetry.jsonl 경로",
    )
    parser.add_argument(
        "--session-id",
        help="런처가 정한 공통 세션 ID. 미지정이면 UE4SS 원본 세션 ID를 유지한다.",
    )
    parser.add_argument(
        "--player-id",
        help="런처가 정한 짧은 PC 식별자. 원본 UE 액터 경로는 evidence로 보존한다.",
    )
    parser.add_argument(
        "--t0",
        type=float,
        help="런처 세션 시작 Unix epoch(초). 지정하면 출력 timestamp_ms를 이 시점 기준으로 맞춘다.",
    )
    return parser.parse_args()


class LauncherTimeline:
    """UE 게임 시계를 런처 세션 시계로 평행이동한다.

    첫 UE4SS 원본 이벤트가 실제로 관찰된 시각을 런처의 ``t0`` 기준으로
    고정한다. 이후에는 UE 시계의 밀리초 간격을 그대로 보존한다. UE4SS Lua는
    런처 CLI 인자를 직접 받을 수 없으므로 Python 경계에서 맞춘다.
    """

    def __init__(self, t0: float | None):
        self.t0 = t0
        self._offset_ms: int | None = None

    def align(self, event) -> None:
        if self.t0 is None:
            return

        source_ms = event.timestamp_ms
        if self._offset_ms is None:
            observed_ms = round((time.time() - self.t0) * 1000)
            self._offset_ms = observed_ms - source_ms

        event.source_timestamp_ms = source_ms
        event.timestamp_ms = source_ms + self._offset_ms


def make_outbound_result(result: dict, args) -> dict:
    """공통 7필드는 유지하며 런처 식별자와 UE 원본 식별자를 분리한다."""

    outbound = dict(result)
    evidence = dict(outbound["evidence"])
    if args.session_id:
        evidence["source_session_id"] = outbound["session_id"]
        outbound["session_id"] = args.session_id
    if args.player_id:
        evidence["source_attacker_id"] = outbound["player_id"]
        outbound["player_id"] = args.player_id
    outbound["evidence"] = evidence
    return outbound


def main():
    args = parse_args()
    print("=" * 50)
    print("MECCHA CHAMELEON - Aimbot Anti-Cheat")
    print("=" * 50)
    print(f"[INFO] Telemetry path: {args.log_path}")
    print("[INFO] Waiting for MECCHA telemetry...")

    sensor = MecchaAimTelemetrySensor(args.log_path)
    detector = AimbotDetector()
    timeline = LauncherTimeline(args.t0)

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
                timeline.align(event)
                result = detector.ingest_event(event)
                if result:
                    result = make_outbound_result(result, args)
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
