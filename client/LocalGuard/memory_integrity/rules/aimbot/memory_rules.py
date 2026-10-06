from collections import deque
from dataclasses import dataclass
import math
from typing import Any, Deque, Dict, List, Optional


@dataclass
class AimbotEvidence:
    module: str
    event_type: str
    target: str
    reason: str
    observed: Any
    expected: Any
    details: Dict[str, Any]


@dataclass
class RotationSample:
    timestamp: float
    pitch: float
    yaw: float
    roll: float
    delta_angle: float
    angular_speed: float


class AimbotMemoryRules:
    """
    MECCHA CHAMELEON 4.0.2
    Aimbot ControlRotation pattern rules.

    Verified target:

        AController::ControlRotation
        offset = 0x0320

    Normal mouse input also changes ControlRotation.

    Therefore a single rotation change is not treated
    as Aimbot evidence.

    LocalGuard watches a rolling window and generates
    VALUE_PATTERN evidence only when the measured
    low-amplitude correction pattern persists.
    """

    MODULE_NAME = "aimbot"
    TARGET = "AController.ControlRotation"

    CONTROL_ROTATION_OFFSET = 0x0320

    WINDOW_SECONDS = 2.0

    MIN_WINDOW_SAMPLES = 50
    MIN_MOVING_SAMPLES = 10

    MOVE_EPSILON = 0.01

    SMALL_STEP_DEGREES = 2.0

    MAX_MEDIAN_DELTA = 1.8
    MAX_P95_DELTA = 5.0

    MIN_SMALL_STEP_RATIO = 0.55

    MAX_P95_SPEED = 200.0

    PATTERN_PERSISTENCE_SECONDS = 1.0

    CLEAR_SECONDS = 3.0

    def __init__(self):
        self.samples: Deque[RotationSample] = deque()

        self.previous_timestamp: Optional[float] = None
        self.previous_pitch: Optional[float] = None
        self.previous_yaw: Optional[float] = None

        self.pattern_start: Optional[float] = None
        self.clear_start: Optional[float] = None

        self.episode_reported = False

        # Diagnostic-only state. These fields do not affect detection.
        self.last_metrics = None
        self.last_pattern_matches = False
        self.last_pattern_duration = 0.0
        self.last_failed_conditions = []

    def reset(self):
        self.__init__()

    @staticmethod
    def _angle_delta(
        current: float,
        previous: float,
    ) -> float:

        delta = float(current) - float(previous)

        while delta > 180.0:
            delta -= 360.0

        while delta < -180.0:
            delta += 360.0

        return delta

    @staticmethod
    def _median(
        values: List[float],
    ) -> float:

        if not values:
            return 0.0

        ordered = sorted(values)

        count = len(ordered)
        middle = count // 2

        if count % 2 == 1:
            return ordered[middle]

        return (
            ordered[middle - 1]
            + ordered[middle]
        ) / 2.0

    @staticmethod
    def _percentile(
        values: List[float],
        percentile: float,
    ) -> float:

        if not values:
            return 0.0

        ordered = sorted(values)

        if len(ordered) == 1:
            return ordered[0]

        position = (
            (len(ordered) - 1)
            * percentile
        )

        low_index = int(
            math.floor(position)
        )

        high_index = int(
            math.ceil(position)
        )

        if low_index == high_index:
            return ordered[low_index]

        fraction = (
            position - low_index
        )

        low_value = ordered[low_index]
        high_value = ordered[high_index]

        return (
            low_value
            + (
                high_value
                - low_value
            )
            * fraction
        )

    def _trim_window(
        self,
        timestamp: float,
    ):
        minimum_time = (
            timestamp
            - self.WINDOW_SECONDS
        )

        while (
            self.samples
            and self.samples[0].timestamp
            < minimum_time
        ):
            self.samples.popleft()

    def _calculate_metrics(
        self,
    ) -> Optional[Dict[str, float]]:

        if (
            len(self.samples)
            < self.MIN_WINDOW_SAMPLES
        ):
            return None

        moving_samples = [
            sample
            for sample in self.samples
            if sample.delta_angle
            > self.MOVE_EPSILON
        ]

        if (
            len(moving_samples)
            < self.MIN_MOVING_SAMPLES
        ):
            return None

        delta_values = [
            sample.delta_angle
            for sample in moving_samples
        ]

        speed_values = [
            sample.angular_speed
            for sample in moving_samples
        ]

        median_delta = self._median(
            delta_values
        )

        p95_delta = self._percentile(
            delta_values,
            0.95,
        )

        p95_speed = self._percentile(
            speed_values,
            0.95,
        )

        small_count = sum(
            1
            for value in delta_values
            if value <= self.SMALL_STEP_DEGREES
        )

        small_step_ratio = (
            small_count
            / len(delta_values)
        )

        movement_ratio = (
            len(moving_samples)
            / len(self.samples)
        )

        return {
            "window_samples": len(
                self.samples
            ),
            "moving_samples": len(
                moving_samples
            ),
            "movement_ratio": movement_ratio,
            "median_delta": median_delta,
            "p95_delta": p95_delta,
            "small_step_ratio": small_step_ratio,
            "p95_speed": p95_speed,
        }

    def _matches_pattern(
        self,
        metrics: Dict[str, float],
    ) -> bool:

        return (
            metrics["median_delta"]
            <= self.MAX_MEDIAN_DELTA

            and metrics["p95_delta"]
            <= self.MAX_P95_DELTA

            and metrics["small_step_ratio"]
            >= self.MIN_SMALL_STEP_RATIO

            and metrics["p95_speed"]
            <= self.MAX_P95_SPEED
        )

    def evaluate(
        self,
        timestamp: float,
        pitch: float,
        yaw: float,
        roll: float,
    ) -> List[AimbotEvidence]:

        evidence: List[AimbotEvidence] = []

        timestamp = float(timestamp)
        pitch = float(pitch)
        yaw = float(yaw)
        roll = float(roll)

        delta_angle = 0.0
        angular_speed = 0.0

        if (
            self.previous_timestamp is not None
            and self.previous_pitch is not None
            and self.previous_yaw is not None
        ):
            delta_pitch = self._angle_delta(
                pitch,
                self.previous_pitch,
            )

            delta_yaw = self._angle_delta(
                yaw,
                self.previous_yaw,
            )

            delta_angle = math.sqrt(
                delta_pitch ** 2
                + delta_yaw ** 2
            )

            elapsed = (
                timestamp
                - self.previous_timestamp
            )

            if elapsed > 0:
                angular_speed = (
                    delta_angle
                    / elapsed
                )

        self.previous_timestamp = timestamp
        self.previous_pitch = pitch
        self.previous_yaw = yaw

        self.samples.append(
            RotationSample(
                timestamp=timestamp,
                pitch=pitch,
                yaw=yaw,
                roll=roll,
                delta_angle=delta_angle,
                angular_speed=angular_speed,
            )
        )

        self._trim_window(
            timestamp
        )

        metrics = self._calculate_metrics()

        pattern_matches = False

        if metrics is not None:
            pattern_matches = self._matches_pattern(
                metrics
            )

        self.last_metrics = (
            dict(metrics)
            if metrics is not None
            else None
        )
        self.last_pattern_matches = pattern_matches
        self.last_pattern_duration = 0.0

        failed_conditions = []

        if metrics is None:
            if len(self.samples) < self.MIN_WINDOW_SAMPLES:
                failed_conditions.append(
                    "insufficient_window_samples"
                )
            else:
                failed_conditions.append(
                    "insufficient_moving_samples"
                )
        else:
            if metrics["median_delta"] > self.MAX_MEDIAN_DELTA:
                failed_conditions.append(
                    "median_delta_above_max"
                )

            if metrics["p95_delta"] > self.MAX_P95_DELTA:
                failed_conditions.append(
                    "p95_delta_above_max"
                )

            if metrics["small_step_ratio"] < self.MIN_SMALL_STEP_RATIO:
                failed_conditions.append(
                    "small_step_ratio_below_min"
                )

            if metrics["p95_speed"] > self.MAX_P95_SPEED:
                failed_conditions.append(
                    "p95_speed_above_max"
                )

        self.last_failed_conditions = failed_conditions

        if pattern_matches:
            self.clear_start = None

            if self.pattern_start is None:
                self.pattern_start = timestamp

            pattern_duration = (
                timestamp
                - self.pattern_start
            )

            self.last_pattern_duration = (
                pattern_duration
            )

            if (
                pattern_duration
                < self.PATTERN_PERSISTENCE_SECONDS
            ):
                self.last_failed_conditions = [
                    "persistence_not_met"
                ]

            if (
                pattern_duration
                >= self.PATTERN_PERSISTENCE_SECONDS
                and not self.episode_reported
            ):
                self.episode_reported = True

                evidence.append(
                    AimbotEvidence(
                        module=self.MODULE_NAME,
                        event_type="VALUE_PATTERN",
                        target=self.TARGET,

                        reason=(
                            "Sustained low-amplitude "
                            "ControlRotation correction "
                            "pattern matched measured "
                            "Aimbot behavior"
                        ),

                        observed={
                            "median_delta_deg": (
                                metrics[
                                    "median_delta"
                                ]
                            ),
                            "p95_delta_deg": (
                                metrics[
                                    "p95_delta"
                                ]
                            ),
                            "small_step_ratio": (
                                metrics[
                                    "small_step_ratio"
                                ]
                            ),
                            "p95_speed_deg_sec": (
                                metrics[
                                    "p95_speed"
                                ]
                            ),
                            "movement_ratio": (
                                metrics[
                                    "movement_ratio"
                                ]
                            ),
                            "pattern_duration_sec": (
                                pattern_duration
                            ),
                        },

                        expected=(
                            "ControlRotation should not "
                            "show a sustained correction "
                            "pattern matching the "
                            "measured Aimbot trace"
                        ),

                        details={
                            "offset": (
                                f"0x"
                                f"{self.CONTROL_ROTATION_OFFSET:04X}"
                            ),
                            "window_seconds": (
                                self.WINDOW_SECONDS
                            ),
                            "window_samples": (
                                metrics[
                                    "window_samples"
                                ]
                            ),
                            "moving_samples": (
                                metrics[
                                    "moving_samples"
                                ]
                            ),
                            "thresholds": {
                                "max_median_delta_deg": (
                                    self.MAX_MEDIAN_DELTA
                                ),
                                "max_p95_delta_deg": (
                                    self.MAX_P95_DELTA
                                ),
                                "small_step_deg": (
                                    self.SMALL_STEP_DEGREES
                                ),
                                "min_small_step_ratio": (
                                    self.MIN_SMALL_STEP_RATIO
                                ),
                                "max_p95_speed_deg_sec": (
                                    self.MAX_P95_SPEED
                                ),
                                "persistence_seconds": (
                                    self.PATTERN_PERSISTENCE_SECONDS
                                ),
                            },
                        },
                    )
                )

        else:
            self.pattern_start = None

            if self.episode_reported:
                if self.clear_start is None:
                    self.clear_start = timestamp

                clear_duration = (
                    timestamp
                    - self.clear_start
                )

                if (
                    clear_duration
                    >= self.CLEAR_SECONDS
                ):
                    self.episode_reported = False
                    self.clear_start = None

        return evidence