"""발사 시도·확정 결과·조준 궤적을 함께 사용하는 에임봇 탐지기.

MECCHA CHAMELEON에서는 한 번 술래를 찾으면 대상이 탈락하거나 Hunter로
전환된다. 따라서 일반 FPS처럼 같은 피해자를 연속 명중했는지보다, 실제 술래를
찾기 직전 화면이 그 위치에 비정상적으로 붙었는지를 우선 판단한다.
"""

from dataclasses import dataclass, field
from math import atan2, degrees, sqrt
from statistics import mean
from typing import Dict, List, Optional, Set, Tuple

from core.models import AimSample, HitEvent, ShotEvent, TelemetryEvent, Vec3

Key = Tuple[str, str, str]  # (session_id, attacker_id, round_id)


def distance(a: Vec3, b: Vec3) -> float:
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


def normalized_angle(angle: float) -> float:
    """degree 차이를 -180..180으로 정규화한다."""
    return (angle + 180.0) % 360.0 - 180.0


@dataclass
class Engagement:
    attempts: List[ShotEvent] = field(default_factory=list)
    outcomes: List[HitEvent] = field(default_factory=list)
    successful_attempt_indices: Set[int] = field(default_factory=set)
    # outcomes index -> attempts index. 조준 궤적은 이 매칭된 발사에서 가져온다.
    outcome_attempt_pairs: Dict[int, int] = field(default_factory=dict)
    unpaired_outcomes: int = 0
    last_timestamp_ms: Optional[int] = None


class AimbotDetector:
    """신호 5(조준 궤적)·7(보조 통계)·12(LOS)의 raw_score를 계산한다."""

    # 정상/핵 로그가 쌓이면 ReplayAnalyzer 결과로 교정할 초기 안전값이다.
    OUTCOME_MATCH_WINDOW_MS = 1500
    # 자체 제작 핵은 F8을 누른 동안 pymem으로 ControlRotation을 목표까지 직접
    # 수렴시킨다. UE4SS LoopAsync의 실제 표본 간격은 20~70ms 이상으로 흔들려
    # "deg/s가 일정한가"는 신뢰할 수 없었다. 그래서 실전 로그에서 재현되는
    # 큰 각도·지속 수렴·역방향 보정 부재를 기준으로 삼는다.
    CONVERGENCE_START_ERROR_DEG = 15.0
    # 실제 정상 수동 처치 3회는 최종 오차가 0.798° / 1.844° / 3.506°였고,
    # F8 처치는 0.000~0.001°였다. "5도 안쪽"은 사람의 정상 조준과 완전히
    # 겹쳤으므로, 이 PoC의 직접 ControlRotation 쓰기 특성인 거의 0도 고정을
    # 요구한다. 정상/핵 로그가 더 쌓이면 ReplayAnalyzer로 다시 교정한다.
    CONVERGENCE_FINAL_ERROR_DEG = 0.1
    MIN_ERROR_REDUCTION_DEG = 10.0
    MIN_CONVERGENCE_SAMPLES = 6
    # 실제 F8 로그에서는 후보가 이동하거나 카메라 기준이 갱신되는 초반 표본에서
    # 오차가 잠시 커진 뒤 수렴했다(84.3%). 수동 불규칙 조준 테스트(66.7%)와
    # 구분되는 80%를 초기 기준으로 둔다.
    MIN_IMPROVING_STEP_RATIO = 0.80
    ERROR_INCREASE_TOLERANCE_DEG = 0.35
    MIN_ROTATION_STEP_DEG = 0.5
    MIN_ROTATION_STEPS = 6
    # 신호 5 보조 근거: 목표에 붙은 뒤 바로 쏘는 F8 행동은 마지막 0.3초에도
    # 오차가 작고 흔들림이 거의 없다. 단, 이 값만으로는 사람이 멈춰 조준한
    # 경우와 겹치므로 아래의 "큰 오차부터 지속 수렴" 조건을 통과한 결과에만
    # 추가 점수를 준다.
    TERMINAL_LOCK_WINDOW_MS = 300
    MAX_TERMINAL_LOCK_ERROR_DEG = 0.1
    MAX_TERMINAL_LOCK_SPREAD_DEG = 0.1
    MIN_TERMINAL_LOCK_SAMPLES = 5
    # 신호 12: 벽 뒤의 같은 술래를 정확히 겨냥한 실패 발사가 반복되는지 본다.
    MAX_BLIND_TARGET_ERROR_DEG = 3.0
    MIN_BLIND_TRACKING_MISSES = 3
    # UE 좌표는 대체로 100 unit = 1 m다. 후보 위치의 미세한 표본 흔들림을
    # 이동으로 잘못 세지 않도록, 적어도 약 1.5 m의 연속 위치 변화만 쓴다.
    MIN_BLIND_TARGET_MOVEMENT_UNITS = 150.0

    def __init__(self, outcome_match_window_ms: int = OUTCOME_MATCH_WINDOW_MS):
        self.outcome_match_window_ms = outcome_match_window_ms
        self._windows: Dict[Key, Engagement] = {}

    def ingest_event(self, event: TelemetryEvent) -> dict:
        if isinstance(event, ShotEvent):
            return self.ingest_shot(event)
        return self.ingest_outcome(event)

    def ingest_shot(self, event: ShotEvent) -> dict:
        key, window = self._window_for(event)
        window.attempts.append(event)
        return self._next_snapshot(window)

    def ingest_outcome(self, event: HitEvent) -> dict:
        _, window = self._window_for(event)
        outcome_index = len(window.outcomes)
        window.outcomes.append(event)

        # KillPlayer는 SpawnShotEffect(Local) 직후 발생하는 것이 실측됐다.
        # 가장 최근의 아직 매칭되지 않은 발사를 확정 성공과 연결한다.
        for attempt_index in range(len(window.attempts) - 1, -1, -1):
            attempt = window.attempts[attempt_index]
            elapsed = event.timestamp_ms - attempt.timestamp_ms
            if elapsed > self.outcome_match_window_ms:
                break
            if elapsed >= 0 and attempt_index not in window.successful_attempt_indices:
                window.successful_attempt_indices.add(attempt_index)
                window.outcome_attempt_pairs[outcome_index] = attempt_index
                break
        else:
            window.unpaired_outcomes += 1
        return self._next_snapshot(window)

    # 기존 호출부와 과거 테스트의 이름 호환용 별칭.
    def ingest_hit(self, event: HitEvent) -> dict:
        return self.ingest_outcome(event)

    def flush(self, attacker_id: str, session_id: Optional[str] = None) -> Optional[dict]:
        matches = [
            key for key in self._windows
            if key[1] == attacker_id and (session_id is None or key[0] == session_id)
        ]
        window = self._windows[matches[-1]] if matches else None
        if window is None or window.last_timestamp_ms is None:
            return None
        return self._next_snapshot(window)

    @staticmethod
    def _round_id(event: TelemetryEvent) -> str:
        # 라운드가 없으면 표본은 남기되, 반복 횟수를 요구하는 신호는 점수화하지 않는다.
        return event.round_id or "unscoped-session"

    def _window_for(self, event: TelemetryEvent) -> Tuple[Key, Engagement]:
        key = (event.session_id, event.attacker_id, self._round_id(event))
        window = self._windows.get(key)
        if window is None:
            window = Engagement()
            self._windows[key] = window
        window.last_timestamp_ms = event.timestamp_ms
        return key, window

    def _next_snapshot(self, window: Engagement) -> dict:
        return self._evaluate_engagement(window)

    def _evaluate_engagement(self, window: Engagement) -> dict:
        attempts = len(window.attempts)
        matched_successes = len(window.successful_attempt_indices)
        outcomes = len(window.outcomes)
        misses = attempts - matched_successes
        success_rate = matched_successes / attempts if attempts else None
        latest = window.outcomes[-1] if window.outcomes else window.attempts[-1]
        round_scoped = latest.round_id is not None

        reasons: List[str] = []
        evidence: Dict[str, object] = {
            # 결과 품질 지표다. 단독으로 에임봇 점수에는 반영하지 않는다.
            "shot_attempt_count": attempts,
            "confirmed_outcome_count": outcomes,
            "matched_success_count": matched_successes,
            "unpaired_outcome_count": window.unpaired_outcomes,
            "missed_shot_count": misses,
            "success_rate": round(success_rate, 3) if success_rate is not None else None,
            "max_success_streak": self._max_success_streak(window),
        }
        raw_score = 0

        # 신호 5: F8 자체 제작 핵은 ControlRotation을 마우스 입력 대신 pymem으로
        # 목표 방향까지 직접 갱신한다. 확정 결과 직전 한 번의 조준 궤적에서
        # "큰 오차 -> 지속 감소 -> 낮은 오차"와 충분한 회전 표본을 만족하면
        # 점수화한다. UE4SS 표본 간격 지터 때문에 속도 균일성은 쓰지 않는다.
        aim_metrics = self._aim_trace_metrics(window)
        evidence.update(aim_metrics)
        if round_scoped and aim_metrics["target_convergence_outcome_count"] > 0:
            reasons.append("Consistent Target Convergence Before Confirmed Find")
            raw_score += 3
        if round_scoped and aim_metrics["target_convergence_lock_outcome_count"] > 0:
            reasons.append("Target Lock Maintained Before Confirmed Find")
            raw_score += 1

        # 신호 7: 거리 우선 선택은 게임 규칙·우연으로도 충분히 생길 수 있어
        # evidence만 남긴다. 다른 독립 신호 없이 raw_score에 더하지 않는다.
        switches, evaluable_switches, distance_priority = self._target_switch_stats(window.outcomes)
        evidence["target_switches"] = switches
        evidence["distance_evaluable_switches"] = evaluable_switches
        evidence["distance_priority_switches"] = distance_priority

        # 신호 12: "처치 순간 LOS 없음"은 게임 판정과 카메라 LineTrace가 다를
        # 수 있어 점수화하지 않는다. 대신 벽 뒤의 같은 살아 있는 술래 방향을
        # 매우 정확히 겨냥했지만 실패한 발사가 반복되는지를 사용한다.
        no_los_outcomes = sum(1 for outcome in window.outcomes if outcome.los_clear is False)
        checked_outcomes = sum(1 for outcome in window.outcomes if outcome.los_clear is not None)
        evidence["outcome_no_los_count"] = no_los_outcomes
        evidence["outcome_los_checked_count"] = checked_outcomes
        blind_metrics = self._blind_target_tracking_metrics(window)
        evidence.update(blind_metrics)
        if round_scoped and blind_metrics["max_same_target_blind_miss_count"] >= self.MIN_BLIND_TRACKING_MISSES:
            reasons.append("Repeated Precise Shots Toward Hidden Target")
            raw_score += 3
        if round_scoped and blind_metrics["blind_tracking_moving_target_id"] is not None:
            reasons.append("Precise Tracking Maintained While Hidden Target Moved")
            raw_score += 1

        return {
            "session_id": latest.session_id,
            "player_id": latest.attacker_id,
            "module": "aimbot",
            "timestamp_ms": latest.timestamp_ms,
            "evidence": evidence,
            "reasons": reasons,
            "raw_score": raw_score,
        }

    def _aim_trace_metrics(self, window: Engagement) -> Dict[str, object]:
        sample_count = matched_trace_count = convergence_count = lock_count = 0
        final_errors: List[float] = []
        convergence_start_errors: List[float] = []
        convergence_improvement_ratios: List[float] = []
        convergence_turn_degrees: List[float] = []
        convergence_rotation_steps: List[int] = []
        terminal_lock_errors: List[float] = []
        terminal_lock_spreads: List[float] = []
        terminal_lock_durations: List[int] = []

        for outcome_index, attempt_index in window.outcome_attempt_pairs.items():
            outcome = window.outcomes[outcome_index]
            trace = window.attempts[attempt_index].aim_trace
            sample_count += len(trace)
            errors = self._target_errors(trace, outcome.victim_id)
            if not errors:
                continue
            matched_trace_count += 1
            final_errors.append(errors[-1][1])

            convergence = self._target_convergence(errors)
            if convergence is not None:
                convergence_count += 1
                convergence_start_errors.append(convergence["start_error_deg"])
                convergence_improvement_ratios.append(convergence["improving_step_ratio"])
                convergence_turn_degrees.append(convergence["control_turn_deg"])
                convergence_rotation_steps.append(int(convergence["rotation_step_count"]))
                lock = self._terminal_target_lock(errors)
                if lock is not None:
                    lock_count += 1
                    terminal_lock_errors.append(lock["mean_error_deg"])
                    terminal_lock_spreads.append(lock["error_spread_deg"])
                    terminal_lock_durations.append(int(lock["duration_ms"]))

        return {
            "aim_sample_count": sample_count,
            "target_matched_aim_trace_count": matched_trace_count,
            "target_convergence_outcome_count": convergence_count,
            "target_convergence_lock_outcome_count": lock_count,
            "mean_final_aim_error_deg": round(sum(final_errors) / len(final_errors), 3) if final_errors else None,
            "mean_convergence_start_error_deg": round(mean(convergence_start_errors), 3) if convergence_start_errors else None,
            "mean_convergence_improving_step_ratio": round(mean(convergence_improvement_ratios), 3) if convergence_improvement_ratios else None,
            "mean_convergence_control_turn_deg": round(mean(convergence_turn_degrees), 3) if convergence_turn_degrees else None,
            "mean_convergence_rotation_step_count": round(mean(convergence_rotation_steps), 3) if convergence_rotation_steps else None,
            "mean_terminal_lock_error_deg": round(mean(terminal_lock_errors), 3) if terminal_lock_errors else None,
            "mean_terminal_lock_spread_deg": round(mean(terminal_lock_spreads), 3) if terminal_lock_spreads else None,
            "mean_terminal_lock_duration_ms": round(mean(terminal_lock_durations), 3) if terminal_lock_durations else None,
        }

    def _blind_target_tracking_metrics(self, window: Engagement) -> Dict[str, object]:
        """같은 벽 뒤 후보를 정밀 조준한 미매칭 발사의 반복을 센다.

        SpawnShotEffect의 IsHit은 쓰지 않는다. KillPlayer와 매칭된 발사는
        제외하므로, 실제 술래를 찾은 발사가 '벽 뒤 실패'로 누적되지 않는다.
        """
        counts: Dict[str, int] = {}
        positions: Dict[str, List[Vec3]] = {}
        precise_blocked_attempts = 0
        for index, attempt in enumerate(window.attempts):
            if index in window.successful_attempt_indices:
                continue
            if not (
                attempt.aimed_candidate_id
                and attempt.aimed_candidate_los_clear is False
                and attempt.aimed_candidate_error_deg is not None
                and attempt.aimed_candidate_error_deg <= self.MAX_BLIND_TARGET_ERROR_DEG
            ):
                continue
            precise_blocked_attempts += 1
            counts[attempt.aimed_candidate_id] = counts.get(attempt.aimed_candidate_id, 0) + 1
            position = self._aimed_candidate_position(attempt)
            if position is not None:
                positions.setdefault(attempt.aimed_candidate_id, []).append(position)

        if counts:
            target_id, max_count = max(counts.items(), key=lambda item: item[1])
        else:
            target_id, max_count = None, 0
        moving_target_id = None
        movement_step_count = 0
        max_movement_step_units = 0.0
        if max_count >= self.MIN_BLIND_TRACKING_MISSES and target_id is not None:
            target_positions = positions.get(target_id, [])
            steps = [
                distance(previous, current)
                for previous, current in zip(target_positions, target_positions[1:])
            ]
            moving_steps = [step for step in steps if step >= self.MIN_BLIND_TARGET_MOVEMENT_UNITS]
            movement_step_count = len(moving_steps)
            max_movement_step_units = max(steps, default=0.0)
            if moving_steps:
                moving_target_id = target_id

        return {
            "precise_blocked_shot_count": precise_blocked_attempts,
            "blind_tracking_target_id": target_id,
            "max_same_target_blind_miss_count": max_count,
            "blind_tracking_moving_target_id": moving_target_id,
            "blind_tracking_movement_step_count": movement_step_count,
            "blind_tracking_max_movement_step_units": round(max_movement_step_units, 3),
        }

    @staticmethod
    def _aimed_candidate_position(attempt: ShotEvent) -> Optional[Vec3]:
        """발사에 가장 가까운 조준 표본에서 숨은 후보의 위치를 꺼낸다."""
        if attempt.aimed_candidate_id is None:
            return None
        for sample in reversed(attempt.aim_trace):
            position = sample.candidates.get(attempt.aimed_candidate_id)
            if position is not None:
                return position
        return None

    @staticmethod
    def _target_errors(trace: List[AimSample], victim_id: str) -> List[Tuple[AimSample, float]]:
        errors: List[Tuple[AimSample, float]] = []
        for sample in trace:
            target = sample.candidates.get(victim_id)
            if target is not None:
                errors.append((sample, AimbotDetector._angular_error(sample, target)))
        return errors

    @staticmethod
    def _angular_error(sample: AimSample, target: Vec3) -> float:
        dx = target[0] - sample.view_pos[0]
        dy = target[1] - sample.view_pos[1]
        dz = target[2] - sample.view_pos[2]
        horizontal = sqrt(dx * dx + dy * dy)
        target_yaw = degrees(atan2(dy, dx))
        target_pitch = degrees(atan2(dz, horizontal))
        # UE FRotator Pitch는 -각도를 360도 기준 값(예: -73° -> 287°)으로
        # 내보낼 수 있다. Yaw와 마찬가지로 정규화해야 정상 조준을 큰 오차로
        # 잘못 계산하지 않는다.
        pitch_delta = normalized_angle(target_pitch - sample.control_rotation[0])
        yaw_delta = normalized_angle(target_yaw - sample.control_rotation[1])
        return sqrt(pitch_delta * pitch_delta + yaw_delta * yaw_delta)

    def _target_convergence(self, errors: List[Tuple[AimSample, float]]) -> Optional[Dict[str, float]]:
        """자체 F8 핵의 목표 수렴 궤적인지 판정한다.

        대상이 이동할 수 있으므로 오차가 한 표본에서 0.35도까지 커지는 것은
        허용한다. UE4SS 표본 간격이 일정하지 않아 각속도 조건은 사용하지 않고,
        실제 ControlRotation 변화가 충분히 여러 표본에서 일어났는지만 확인한다.
        단순히 잘 조준한 사람에게 점수를 주지 않도록 큰 시작 오차와 충분한
        감소량도 요구한다.
        """
        if len(errors) < self.MIN_CONVERGENCE_SAMPLES:
            return None

        samples = [sample for sample, _ in errors]
        values = [error for _, error in errors]
        start_error, final_error = values[0], values[-1]
        error_reduction = start_error - final_error
        improving_steps = sum(
            later <= earlier + self.ERROR_INCREASE_TOLERANCE_DEG
            for earlier, later in zip(values, values[1:])
        )
        improving_ratio = improving_steps / (len(values) - 1)

        turn_degrees: List[float] = []
        for previous, current in zip(samples, samples[1:]):
            pitch_delta = normalized_angle(current.control_rotation[0] - previous.control_rotation[0])
            yaw_delta = normalized_angle(current.control_rotation[1] - previous.control_rotation[1])
            turn_degrees.append(sqrt(pitch_delta * pitch_delta + yaw_delta * yaw_delta))
        rotation_step_count = sum(turn >= self.MIN_ROTATION_STEP_DEG for turn in turn_degrees)
        control_turn_degrees = sum(turn_degrees)
        if not (
            start_error >= self.CONVERGENCE_START_ERROR_DEG
            and final_error <= self.CONVERGENCE_FINAL_ERROR_DEG
            and error_reduction >= self.MIN_ERROR_REDUCTION_DEG
            and improving_ratio >= self.MIN_IMPROVING_STEP_RATIO
            and rotation_step_count >= self.MIN_ROTATION_STEPS
        ):
            return None

        return {
            "start_error_deg": start_error,
            "final_error_deg": final_error,
            "error_reduction_deg": error_reduction,
            "improving_step_ratio": improving_ratio,
            "control_turn_deg": control_turn_degrees,
            "rotation_step_count": rotation_step_count,
        }

    def _terminal_target_lock(self, errors: List[Tuple[AimSample, float]]) -> Optional[Dict[str, float]]:
        """발사 직전 0.3초 동안 목표에 붙은 채 유지됐는지 확인한다.

        마지막 조준 표본을 기준으로 시간 창을 잡는다. 표본 수가 충분하고 모든
        오차가 0.1도 이하이며 그 범위가 0.1도 이하일 때만 lock으로 본다.
        이 함수는 단독으로 호출해 점수화하지 않고, _target_convergence를 통과한
        같은 결과의 보조 근거로만 사용된다.
        """
        last_timestamp = errors[-1][0].timestamp_ms
        terminal = [
            (sample, error)
            for sample, error in errors
            if last_timestamp - sample.timestamp_ms <= self.TERMINAL_LOCK_WINDOW_MS
        ]
        if len(terminal) < self.MIN_TERMINAL_LOCK_SAMPLES:
            return None

        values = [error for _, error in terminal]
        error_spread = max(values) - min(values)
        if (
            max(values) > self.MAX_TERMINAL_LOCK_ERROR_DEG
            or error_spread > self.MAX_TERMINAL_LOCK_SPREAD_DEG
        ):
            return None

        return {
            "mean_error_deg": mean(values),
            "error_spread_deg": error_spread,
            "duration_ms": last_timestamp - terminal[0][0].timestamp_ms,
        }

    @staticmethod
    def _max_success_streak(window: Engagement) -> int:
        best = current = 0
        for index in range(len(window.attempts)):
            if index in window.successful_attempt_indices:
                current += 1
                best = max(best, current)
            else:
                current = 0
        return best

    @staticmethod
    def _is_usable_distance_candidate(player_id: str) -> bool:
        return "bp_spectatepawn" not in player_id.lower()

    @staticmethod
    def _target_switch_stats(outcomes: List[HitEvent]) -> Tuple[int, int, int]:
        switches = evaluable_switches = distance_priority = 0
        for index in range(1, len(outcomes)):
            previous, current = outcomes[index - 1], outcomes[index]
            if current.victim_id == previous.victim_id:
                continue
            switches += 1
            candidates = {
                player_id: position
                for player_id, position in current.other_candidates.items()
                if player_id != current.victim_id and AimbotDetector._is_usable_distance_candidate(player_id)
            }
            if not candidates:
                continue
            evaluable_switches += 1
            candidates[current.victim_id] = current.victim_pos
            nearest_id = min(candidates, key=lambda player_id: distance(current.attacker_pos, candidates[player_id]))
            if nearest_id == current.victim_id:
                distance_priority += 1
        return switches, evaluable_switches, distance_priority
