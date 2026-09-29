from typing import List, Optional

from core.models import PlayerSnapshot, DetectionResult


class GodModeDetector:
    """
    MECCHA CHAMELEON GodMode Detector.

    주요 탐지 항목:
    1. Invincible 비정상 지속
    2. 피격 이벤트 발생 중 Invincible 활성
    3. Kill 이후 Death 상태 미진입
    4. Kill 이후 계속 생존
    5. Heal 없이 Health 증가
    6. Health / ChangeBeforeHealth 강제 MaxHealth 복구
    7. Respawn 없이 Dead -> Alive 전환

    score / reasons:
        세션 전체 누적 결과

    new_score / new_reasons:
        현재 Snapshot에서 새로 발생한 결과
    """

    NORMAL_MAX_SCORE = 4
    SUSPICIOUS_MAX_SCORE = 9

    INVINCIBLE_DURATION_THRESHOLD = 1.5
    DEATH_GRACE_PERIOD = 0.5
    HEALTH_EPSILON = 0.01

    def __init__(self):
        self.previous: Optional[PlayerSnapshot] = None

        # 세션 전체 누적
        self.score = 0
        self.reasons: List[str] = []

        # 현재 Snapshot 신규 탐지
        self.new_score = 0
        self.new_reasons: List[str] = []

        # Invincible 지속 검사
        self.invincible_start: Optional[float] = None
        self.invincible_flagged = False

        # Kill 이후 사망 여부 검사
        self.kill_pending = False
        self.kill_time: Optional[float] = None

        self.kill_no_death_flagged = False
        self.kill_survival_flagged = False

        # 중복 탐지 시간 관리
        self.last_abnormal_heal_time: Optional[float] = None
        self.last_forced_restore_time: Optional[float] = None

    def reset(self):
        self.__init__()

    def _reset_current_event(self):
        """
        새로운 Snapshot을 처리하기 전에
        현재 Event 결과만 초기화한다.

        누적 score / reasons는 유지한다.
        """

        self.new_score = 0
        self.new_reasons = []

    def _add_score(
        self,
        points: int,
        reason: str
    ):
        """
        새로운 탐지 근거가 발생했을 때만 점수를 추가한다.

        동일 reason은 한 세션에서 한 번만 누적한다.
        현재 Snapshot의 신규 결과도 함께 기록한다.
        """

        if reason in self.reasons:
            return

        # 세션 누적 결과
        self.score += points
        self.reasons.append(reason)

        # 현재 Snapshot 신규 결과
        self.new_score += points
        self.new_reasons.append(reason)

    def _is_close(
        self,
        value_a: float,
        value_b: float
    ) -> bool:
        return (
            abs(value_a - value_b)
            <= self.HEALTH_EPSILON
        )

    def _check_invincible(
        self,
        snapshot: PlayerSnapshot
    ):
        """
        Invincible 값이 일정 시간 이상
        True로 유지되는지 검사한다.
        """

        # 정상 Respawn 구간은 제외
        if snapshot.respawn_event:
            self.invincible_start = None
            self.invincible_flagged = False
            return

        if snapshot.invincible:

            if self.invincible_start is None:
                self.invincible_start = (
                    snapshot.timestamp
                )

            duration = (
                snapshot.timestamp
                - self.invincible_start
            )

            if (
                duration
                >= self.INVINCIBLE_DURATION_THRESHOLD
                and not self.invincible_flagged
            ):
                self._add_score(
                    2,
                    "Invincible abnormal persistence"
                )

                self.invincible_flagged = True

        else:
            self.invincible_start = None
            self.invincible_flagged = False

    def _check_damage_while_invincible(
        self,
        snapshot: PlayerSnapshot
    ):
        """
        실제 피격 이벤트가 발생했는데
        플레이어가 Invincible 상태로 살아 있는지 검사한다.

        단순 피격은 정상 플레이에서도 발생하므로 점수를 주지 않는다.
        damage_event + invincible + alive 조합일 때만
        GodMode 의심 근거로 사용한다.
        """

        if not snapshot.damage_event:
            return

        if snapshot.respawn_event:
            return

        if snapshot.dead:
            return

        if not snapshot.invincible:
            return

        self._add_score(
            3,
            "Damage received while Invincible was active"
        )

    def _check_health_restore(
        self,
        snapshot: PlayerSnapshot
    ):
        """
        Heal / Respawn 없이 실제 Health가
        증가했는지 검사한다.

        예:
        70 -> 100 : 증가
        100 -> 100 : 증가 아님
        """

        if self.previous is None:
            return

        health_increased = (
            snapshot.health
            > self.previous.health
            + self.HEALTH_EPSILON
        )

        if not health_increased:
            return

        if snapshot.heal_event:
            return

        if snapshot.respawn_event:
            return

        if (
            self.last_abnormal_heal_time
            is not None
        ):
            elapsed = (
                snapshot.timestamp
                - self.last_abnormal_heal_time
            )

            if elapsed < 0.3:
                return

        self._add_score(
            3,
            "Health restored without heal event"
        )

        self.last_abnormal_heal_time = (
            snapshot.timestamp
        )

    def _check_forced_max_health_restore(
        self,
        snapshot: PlayerSnapshot
    ):
        """
        이전 Snapshot에서 피해 상태였는데

        Health = MaxHealth
        ChangeBeforeHealth = MaxHealth

        로 동시에 복구되는 경우를 검사한다.
        """

        if self.previous is None:
            return

        if snapshot.max_health <= 0:
            return

        if (
            snapshot.heal_event
            or snapshot.respawn_event
        ):
            return

        previous_was_damaged = (
            self.previous.health
            < self.previous.max_health
            - self.HEALTH_EPSILON
        )

        if not previous_was_damaged:
            return

        health_is_max = self._is_close(
            snapshot.health,
            snapshot.max_health
        )

        change_before_is_max = self._is_close(
            snapshot.change_before_health,
            snapshot.max_health
        )

        if not (
            health_is_max
            and change_before_is_max
        ):
            return

        if (
            self.last_forced_restore_time
            is not None
        ):
            elapsed = (
                snapshot.timestamp
                - self.last_forced_restore_time
            )

            if elapsed < 0.3:
                return

        self._add_score(
            2,
            "Health and ChangeBeforeHealth forced to MaxHealth"
        )

        self.last_forced_restore_time = (
            snapshot.timestamp
        )

    def _check_health_range(
        self,
        snapshot: PlayerSnapshot
    ):
        """
        Health가 MaxHealth를 초과하는지 검사한다.
        """

        if snapshot.max_health <= 0:
            return

        if (
            snapshot.health
            > snapshot.max_health
            + self.HEALTH_EPSILON
        ):
            self._add_score(
                3,
                "Health exceeded MaxHealth"
            )

    def _check_kill_flow(
        self,
        snapshot: PlayerSnapshot
    ):
        """
        Kill 이벤트 이후 정상적인 Death 상태가
        발생하는지 검사한다.
        """

        if snapshot.kill_event:
            self.kill_pending = True
            self.kill_time = snapshot.timestamp

            self.kill_no_death_flagged = False
            self.kill_survival_flagged = False

        if not self.kill_pending:
            return

        if (
            snapshot.death_event
            or snapshot.dead
        ):
            self.kill_pending = False
            self.kill_time = None
            return

        if self.kill_time is None:
            return

        elapsed = (
            snapshot.timestamp
            - self.kill_time
        )

        if elapsed < self.DEATH_GRACE_PERIOD:
            return

        if not self.kill_no_death_flagged:
            self._add_score(
                4,
                "Death state was not reached after kill event"
            )

            self.kill_no_death_flagged = True

        if (
            not snapshot.dead
            and snapshot.health
            > self.HEALTH_EPSILON
            and not self.kill_survival_flagged
        ):
            self._add_score(
                5,
                "Player survived expected death event"
            )

            self.kill_survival_flagged = True

    def _check_dead_transition(
        self,
        snapshot: PlayerSnapshot
    ):
        """
        Respawn 이벤트 없이 Dead -> Alive 상태로
        전환되는지 검사한다.
        """

        if self.previous is None:
            return

        was_dead = self.previous.dead
        now_alive = not snapshot.dead

        if (
            was_dead
            and now_alive
        ):
            if snapshot.respawn_event:
                return

            self._add_score(
                4,
                "Dead state cleared without respawn"
            )

    def process(
        self,
        snapshot: PlayerSnapshot
    ) -> DetectionResult:

        # 현재 Snapshot의 신규 탐지 결과만 초기화
        self._reset_current_event()

        self._check_invincible(snapshot)
        self._check_damage_while_invincible(snapshot)
        self._check_health_restore(snapshot)

        self._check_forced_max_health_restore(
            snapshot
        )

        self._check_health_range(snapshot)
        self._check_kill_flow(snapshot)
        self._check_dead_transition(snapshot)

        self.previous = snapshot

        return self.get_result()

    def get_result(
        self
    ) -> DetectionResult:

        if self.score <= self.NORMAL_MAX_SCORE:
            status = "NORMAL"

        elif self.score <= self.SUSPICIOUS_MAX_SCORE:
            status = "SUSPICIOUS"

        else:
            status = "DETECTED"

        return DetectionResult(
            name="godmode",
            status=status,

            # 세션 전체 누적
            score=self.score,
            reasons=self.reasons.copy(),

            # 이번 Snapshot에서 새로 발생
            new_score=self.new_score,
            new_reasons=self.new_reasons.copy(),
        )
