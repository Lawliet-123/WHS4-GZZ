"""외부 process handle 수집·판정·로컬 JSONL 기록을 연결하는 실행기."""

import argparse
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..common import (
    ArtifactCache,
    ArtifactInspector,
    ProcessLocator,
    append_detection_jsonl,
)
from .allowlist import ProcessAllowlist
from .detector import ProcessAccessDetector
from .handle_sensor import ExternalHandleSensor, HandleSensorUnavailable
from .models import ExternalHandleObservation, ScanContext

Writer = Callable[[Path, Dict[str, Any]], None]


@dataclass(frozen=True)
class ScanReport:
    game_found: bool
    observed_processes: int
    allowed_processes: int
    emitted_detections: int
    duration_ms: int
    error: Optional[str] = None


class ProcessAccessRunner:
    """한 번의 scan 또는 반복 scan을 수행하는 조립 계층.

    이 클래스는 차단·종료를 하지 않는다. 시스템 관찰값을 결과 JSON으로 바꿔
    로컬 파일에 추가하는 것만 담당한다.
    """

    def __init__(
        self,
        *,
        game_executable_name: str,
        session_id: str,
        player_id: str,
        output_path: Path,
        locator: Optional[ProcessLocator] = None,
        sensor: Optional[ExternalHandleSensor] = None,
        artifact_cache: Optional[ArtifactCache] = None,
        detector: Optional[ProcessAccessDetector] = None,
        allowlist: Optional[ProcessAllowlist] = None,
        writer: Writer = append_detection_jsonl,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._locator = locator or ProcessLocator(game_executable_name)
        self._sensor = sensor or ExternalHandleSensor()
        self._artifact_cache = artifact_cache or ArtifactCache(ArtifactInspector())
        self._detector = detector or ProcessAccessDetector()
        self._allowlist = allowlist or ProcessAllowlist()
        self._session_id = session_id
        self._player_id = player_id
        self._output_path = Path(output_path)
        self._writer = writer
        self._clock = clock
        self._started_at = clock()

    def scan_once(self) -> ScanReport:
        scan_started = self._clock()
        game = self._locator.find()
        if game is None:
            return ScanReport(False, 0, 0, 0, _elapsed_ms(scan_started, self._clock()))

        try:
            observations = self._sensor.scan(game)
        except HandleSensorUnavailable as error:
            return ScanReport(True, 0, 0, 0, _elapsed_ms(scan_started, self._clock()), str(error))

        timestamp_ms = _elapsed_ms(self._started_at, self._clock())
        context = ScanContext(self._session_id, self._player_id, timestamp_ms)
        emitted = 0
        allowed = 0
        for observation in observations:
            enriched = self._enrich_artifact(observation)
            if self._is_allowed(enriched):
                allowed += 1
                continue
            result = self._detector.evaluate(enriched, context)
            if result is None:
                continue
            # 스캔 성능 측정값은 판정 근거가 아니라 분석용 보조 정보다.
            result["evidence"]["scan_duration_ms"] = _elapsed_ms(scan_started, self._clock())
            self._writer(self._output_path, result)
            emitted += 1

        return ScanReport(
            game_found=True,
            observed_processes=len(observations),
            allowed_processes=allowed,
            emitted_detections=emitted,
            duration_ms=_elapsed_ms(scan_started, self._clock()),
        )

    def _enrich_artifact(self, observation: ExternalHandleObservation) -> ExternalHandleObservation:
        if observation.source_path is None:
            return observation
        try:
            artifact = self._artifact_cache.inspect(observation.source_path)
        except OSError:
            # 삭제 경쟁·권한 부족으로 EXE를 읽지 못해도 위험 handle 관찰은 남긴다.
            return observation
        return replace(observation, artifact=artifact)

    def _is_allowed(self, observation: ExternalHandleObservation) -> bool:
        if observation.artifact is None or observation.artifact.sha256 is None:
            return False
        artifact = observation.artifact
        return (
            self._allowlist.find(
                observation.source_name,
                artifact.sha256,
                executable_path=artifact.path,
                signature_status=artifact.signature_status,
                publisher=artifact.publisher,
            )
            is not None
        )


def _elapsed_ms(started_at: float, now: float) -> int:
    return max(0, round((now - started_at) * 1000))


def main() -> None:
    parser = argparse.ArgumentParser(description="LocalGuard 외부 process handle 관찰기")
    parser.add_argument("--game-exe", required=True, help="예: PenguinHotel-Win64-Shipping.exe")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("logs/external_access.jsonl"))
    parser.add_argument("--interval-ms", type=int, default=3000, help="반복 scan 주기 (기본 3000ms)")
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).with_name("allowlist.json"),
        help="검토된 정상 프로세스 이름+SHA-256 목록",
    )
    parser.add_argument("--once", action="store_true", help="한 번만 scan하고 종료")
    args = parser.parse_args()

    if args.interval_ms <= 0:
        raise SystemExit("--interval-ms는 0보다 커야 함")

    runner = ProcessAccessRunner(
        game_executable_name=args.game_exe,
        session_id=args.session_id,
        player_id=args.player_id,
        output_path=args.output,
        allowlist=ProcessAllowlist.from_json(args.allowlist),
    )
    while True:
        started_at = time.monotonic()
        report = runner.scan_once()
        print(
            f"game_found={report.game_found} observed={report.observed_processes} "
            f"allowed={report.allowed_processes} "
            f"emitted={report.emitted_detections} duration_ms={report.duration_ms}"
            + (f" error={report.error}" if report.error else "")
        )
        if args.once:
            return
        time.sleep(max(0, args.interval_ms / 1000 - (time.monotonic() - started_at)))


if __name__ == "__main__":
    main()
