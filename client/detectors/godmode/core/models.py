from dataclasses import dataclass, field
from typing import List


@dataclass
class PlayerSnapshot:
    """
    MECCHA CHAMELEON 플레이어의 특정 시점 상태.

    Sensor에서 수집한 게임 상태를
    GodModeDetector에 전달할 때 사용한다.
    """

    timestamp: float

    # 플레이어 상태
    health: float
    max_health: float

    dead: bool
    invincible: bool

    # 이전 Health 관련 값
    change_before_health: float = 0.0

    # 피격 이벤트
    damage_event: bool = False

    # Kill / Death 이벤트
    kill_event: bool = False
    death_event: bool = False

    # 정상 Heal 이벤트
    heal_event: bool = False

    # 정상 Respawn 이벤트
    respawn_event: bool = False


@dataclass
class DetectionResult:
    """
    Detector의 공통 결과 형식.

    score / reasons
        세션 시작 이후 누적 결과.

    new_score / new_reasons
        현재 Snapshot에서 새로 발생한 탐지 결과.

    ReplayAnalyzer가 Event를 만들 때는
    new_score / new_reasons를 사용한다.
    """

    name: str
    status: str

    # 세션 전체 누적 점수
    score: int

    # 세션 전체 누적 탐지 이유
    reasons: List[str] = field(
        default_factory=list
    )

    # 현재 Snapshot에서 새로 추가된 점수
    new_score: int = 0

    # 현재 Snapshot에서 새로 발생한 이유
    new_reasons: List[str] = field(
        default_factory=list
    )

    def to_dict(self):
        return {
            "name": self.name,
            "status": self.status,

            "score": self.score,
            "reasons": self.reasons,

            "new_score": self.new_score,
            "new_reasons": self.new_reasons,
        }
