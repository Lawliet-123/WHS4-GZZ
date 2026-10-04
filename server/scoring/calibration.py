"""ReplayAnalyzer 결과로 확정한 Scoring calibration 설정.

이 파일은 detector 자체의 raw_score 의미를 바꾸지 않는다.
각 detector의 raw 점수를 중앙 Scoring에서 어떤 기준으로 해석할지
버전이 명시된 calibration 설정으로만 보존한다.

replay-v1:
- 현재 팀 ReplayAnalyzer 데이터로 확정한 첫 공식 calibration 버전
- 이후 추가 replay 결과로 기준이 변경되면 replay-v2 등 새 버전으로 갱신한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


CALIBRATION_VERSION = "replay-v1"

CalibrationMode = Literal[
    "threshold",
    "event_threshold",
    "advisory",
    "pending",
]


@dataclass(frozen=True)
class ModuleCalibration:
    """모듈 하나의 중앙 Scoring calibration 규칙."""

    module: str
    mode: CalibrationMode
    threshold: float | None
    version: str = CALIBRATION_VERSION
    note: str = ""

    def __post_init__(self) -> None:
        if not self.module:
            raise ValueError("module must not be empty")

        if self.mode in ("threshold", "event_threshold"):
            if (
                isinstance(self.threshold, bool)
                or not isinstance(self.threshold, (int, float))
                or not math.isfinite(float(self.threshold))
                or float(self.threshold) < 0
            ):
                raise ValueError(
                    "threshold calibration requires a finite nonnegative threshold"
                )
        elif self.threshold is not None:
            raise ValueError(
                "advisory/pending calibration must not define a threshold"
            )

    @property
    def calibrated(self) -> bool:
        return self.mode in ("threshold", "event_threshold")

    def meets_threshold(self, raw_score: float) -> bool | None:
        """raw_score가 이 calibration 기준을 충족하는지 반환한다.

        advisory/pending은 True/False로 강제 분류하지 않고 None을 반환한다.
        event_threshold도 여기서는 '사건 하나가 기준을 넘었는지'만 평가하며,
        사건 이력의 합산/유지시간은 Aggregate Risk 단계에서 처리한다.
        """
        if not self.calibrated:
            return None

        if (
            isinstance(raw_score, bool)
            or not isinstance(raw_score, (int, float))
            or not math.isfinite(float(raw_score))
            or float(raw_score) < 0
        ):
            raise ValueError("raw_score must be a finite nonnegative number")

        assert self.threshold is not None
        return float(raw_score) >= float(self.threshold)


_ITEMS = (
    # Replay calibration v1 확정값
    ModuleCalibration(
        "aimbot",
        "threshold",
        4,
        note="Replay v1: CHEAT 4 / NORMAL 4; threshold 4 keeps FP=0, FN=0.",
    ),
    ModuleCalibration(
        "autopaint",
        "threshold",
        23,
        note="Replay v1: CHEAT raw 23/32, NORMAL raw 0.",
    ),
    ModuleCalibration(
        "godmode",
        "event_threshold",
        2,
        note=(
            "Godmode is event_delta. raw_score >= 2 marks one meaningful incident; "
            "history handling is performed separately."
        ),
    ),
    ModuleCalibration(
        "hide_anywhere",
        "threshold",
        3,
        note="Replay v1: strong complete-pattern signal; NORMAL remained 0.",
    ),
    ModuleCalibration(
        "injection",
        "threshold",
        40,
        note="Replay v1: threshold 40 keeps current in-scope replay FP=0, FN=0.",
    ),
    ModuleCalibration(
        "value_tamper",
        "threshold",
        100,
        note="Replay v1: currently validated mainly against Hide Anywhere replay.",
    ),

    # 이 모듈은 정상 세션에서도 raw 100이 관측되어 단독 threshold로 쓰지 않는다.
    ModuleCalibration(
        "filesystem",
        "advisory",
        None,
        note="Advisory evidence only; NORMAL replay also produced raw_score 100.",
    ),

    # 추가 Replay 데이터가 도착하면 replay-v1 안에서 확정하거나
    # 필요하면 replay-v2로 승격한다.
    ModuleCalibration(
        "noclip",
        "threshold",
        3,
        note=(
            "Replay v1: CHEAT 3 / NORMAL 3; threshold 3 keeps "
            "FP=0, FN=0 with avg detection latency about 5.67s."
        ),
    ),
    ModuleCalibration("esp", "pending", None),
    ModuleCalibration("whistle", "pending", None),
    ModuleCalibration(
        "whistle_rpc",
        "pending",
        None,
        note="Threshold/TTL requires additional window-history replay data.",
    ),

    # 현재 중앙 risk calibration 근거가 없는 알려진 모듈들.
    ModuleCalibration("external_access", "pending", None),
    ModuleCalibration("localguard_executable_hash", "pending", None),
    ModuleCalibration("localguard_yara", "pending", None),
    ModuleCalibration("overlay_hook", "pending", None),
    ModuleCalibration("godmode_runtime", "pending", None),
    ModuleCalibration("noclip_runtime", "pending", None),
    ModuleCalibration("aimbot_runtime", "pending", None),
)


CALIBRATIONS: Mapping[str, ModuleCalibration] = MappingProxyType(
    {item.module: item for item in _ITEMS}
)


def get_calibration(module: str) -> ModuleCalibration | None:
    """등록된 module calibration을 반환한다.

    None은 '정상'이라는 뜻이 아니라 calibration config 자체에 없는 신규 module이다.
    """
    return CALIBRATIONS.get(module)


def require_calibration(module: str) -> ModuleCalibration:
    """반드시 등록되어 있어야 하는 module calibration을 반환한다."""
    calibration = get_calibration(module)
    if calibration is None:
        raise KeyError(f"no calibration configured for module: {module}")
    return calibration
