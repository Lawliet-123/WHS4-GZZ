
import math
import time

from core.result import DetectorResult, Evidence
from core.process_memory import ProcessMemory
from core.pawn_locator import PawnLocator
from rules.aimbot.memory_rules import AimbotMemoryRules


PROCESS_NAME = "PenguinHotel-Win64-Shipping.exe"

CONTROL_ROTATION_OFFSET = 0x0320

SAMPLE_HZ = 60.0
SAMPLE_INTERVAL = 1.0 / SAMPLE_HZ

SCAN_SECONDS = 3.0

REASON_CODE = "control_rotation_pattern"

# 지속적인 ControlRotation 이상 패턴 탐지 시 부여하는 점수
AIMBOT_PATTERN_SCORE = 1


def _read_control_rotation(
    memory,
    controller_address,
):
    base = (
        controller_address
        + CONTROL_ROTATION_OFFSET
    )

    pitch = memory.read_double(
        base
    )

    yaw = memory.read_double(
        base + 0x08
    )

    roll = memory.read_double(
        base + 0x10
    )

    values = (
        float(pitch),
        float(yaw),
        float(roll),
    )

    if not all(
        math.isfinite(value)
        for value in values
    ):
        raise RuntimeError(
            "ControlRotation contains "
            "non-finite values"
        )

    return values


def _format_evidence(
    item,
    controller_address,
):
    value = (
        f"target={item.target}; "
        f"observed={item.observed}; "
        f"expected={item.expected}"
    )

    note = (
        f"{item.event_type}; "
        f"reason={item.reason}; "
        f"controller=0x{controller_address:X}"
    )

    if item.details:
        note += (
            f"; details={item.details}"
        )

    return Evidence(
        type="value",
        value=value,
        note=note,
    )


def scan():
    result = DetectorResult(
        detector="aimbot_runtime"
    )

    rules = AimbotMemoryRules()

    try:
        with ProcessMemory(
            PROCESS_NAME
        ) as memory:

            locator = PawnLocator(
                memory
            )

            controller_address = (
                locator.get_controller()
            )

            if not controller_address:
                return result.unavailable(
                    "Local PlayerController "
                    "is not available"
                )

            started_at = (
                time.perf_counter()
            )

            deadline = (
                started_at
                + SCAN_SECONDS
            )

            sample_count = 0
            valid_sample_count = 0

            detected_evidence = []

            while (
                time.perf_counter()
                < deadline
            ):
                loop_started = (
                    time.perf_counter()
                )

                sample_count += 1

                try:
                    pitch, yaw, roll = (
                        _read_control_rotation(
                            memory,
                            controller_address,
                        )
                    )

                except (
                    OSError,
                    RuntimeError,
                ):
                    pitch = None

                if pitch is not None:
                    timestamp = (
                        time.perf_counter()
                    )

                    valid_sample_count += 1

                    evidence_list = (
                        rules.evaluate(
                            timestamp,
                            pitch,
                            yaw,
                            roll,
                        )
                    )

                    if evidence_list:
                        detected_evidence.extend(
                            evidence_list
                        )

                elapsed = (
                    time.perf_counter()
                    - loop_started
                )

                sleep_time = (
                    SAMPLE_INTERVAL
                    - elapsed
                )

                if sleep_time > 0:
                    time.sleep(
                        sleep_time
                    )

            result.meta = {
                "source": "ReadProcessMemory",
                "controller_source": (
                    "direct_game_memory_chain"
                ),
                "controller_address": (
                    f"0x{controller_address:X}"
                ),
                "control_rotation_offset": (
                    f"0x{CONTROL_ROTATION_OFFSET:04X}"
                ),
                "sample_hz": SAMPLE_HZ,
                "scan_seconds": SCAN_SECONDS,
                "samples": sample_count,
                "valid_samples": (
                    valid_sample_count
                ),
                "rule_diagnostics": {
                    "pattern_matches": (
                        rules.last_pattern_matches
                    ),
                    "pattern_duration_sec": (
                        rules.last_pattern_duration
                    ),
                    "failed_conditions": list(
                        rules.last_failed_conditions
                    ),
                    "metrics": (
                        dict(rules.last_metrics)
                        if rules.last_metrics
                        is not None
                        else None
                    ),
                },
            }

            if (
                valid_sample_count
                < rules.MIN_WINDOW_SAMPLES
            ):
                return result.unavailable(
                    "Not enough valid "
                    "ControlRotation samples"
                )

            if not detected_evidence:
                return result

            for item in detected_evidence:
                if (
                    REASON_CODE
                    not in result.reasons
                ):
                    result.reasons.append(
                        REASON_CODE
                    )

                    # 한 번의 검사에서 같은 탐지 사유는 한 번만 채점
                    result.score = min(
                        100,
                        result.score + AIMBOT_PATTERN_SCORE,
                    )

                result.evidence.append(
                    _format_evidence(
                        item,
                        controller_address,
                    )
                )

            result.detail = (
                f"{len(detected_evidence)} "
                "Aimbot ControlRotation "
                "pattern evidence item(s) detected"
            )

            return result

    except Exception as error:
        return result.fail(
            f"{type(error).__name__}: "
            f"{error}"
        )


if __name__ == "__main__":
    print(
        scan().to_dict()
    )
