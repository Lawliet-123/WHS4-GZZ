"""
core/models.py
데이터 규격 -> Sensor와 Detector가 주고받는 값의 형태를 여기서 하나로 정의한다.
Sensor 구현체가 바뀌어도(pymem이든 UE4SS든 로그 재생이든) Detector는
이 형태만 알면 되므로, 서로 직접 의존하지 않는다.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

Vec3 = Tuple[float, float, float]
Rotator = Tuple[float, float]  # (pitch, yaw), degree


@dataclass
class AimSample:
    """발사 직전의 로컬 조준 상태 하나.

    UE4SS가 로컬 PlayerController의 ControlRotation을 약 30Hz로 읽어 짧은
    ring buffer에 보관한다. 모든 프레임을 파일에 계속 쓰지 않고, 실제 발사
    이벤트가 생겼을 때 직전 표본만 ShotEvent에 포함한다.

    이 프로젝트의 판정 대상은 F8 뒤 pymem으로 ControlRotation을 일정 속도로
    직접 덮어쓰는 자체 제작 에임봇이다. 따라서 이 모델은 원시 입력을 요구하지
    않고, 회전값·카메라 위치·당시 후보 위치만으로 조준 궤적을 재구성한다.
    """
    timestamp_ms: int
    view_pos: Vec3
    control_rotation: Rotator
    candidates: Dict[str, Vec3] = field(default_factory=dict)


@dataclass
class ShotEvent:
    """헌터의 발사 시도 하나.

    SpawnShotEffect(Local)는 빈 공간·벽 충돌도 IsHit=true로 줄 수 있으므로, 그
    값은 저장하거나 판정하지 않는다. 이 이벤트 자체가 분모(전체 발사 수)다.
    """
    session_id: str
    timestamp_ms: int
    attacker_id: str
    attacker_pos: Vec3
    aim_trace: List[AimSample] = field(default_factory=list)
    timestamp_source: Optional[str] = None
    round_id: Optional[str] = None
    # 발사 순간 조준선에 가장 가까웠던 살아 있는 술래 후보 한 명의 정보.
    # Lua가 이 후보 하나에만 LineTrace를 수행해 벽 뒤 정밀 발사를 판정한다.
    aimed_candidate_id: Optional[str] = None
    aimed_candidate_error_deg: Optional[float] = None
    aimed_candidate_los_clear: Optional[bool] = None


@dataclass
class HitEvent:
    """실제 술래 탈락 또는 헌터 전환이 확정된 결과 이벤트.

    호환성을 위해 기존 이름 HitEvent를 유지한다. 새 JSONL에서는
    event_type="confirmed_outcome"으로 기록되며, HitSuccess만으로는 만들지 않는다.
    """
    session_id: str
    timestamp_ms: int
    attacker_id: str
    victim_id: str
    attacker_pos: Vec3
    victim_pos: Vec3
    # 그 시점에 살아있던 다른 후보들의 위치 (거리우선 전환 판정용)
    other_candidates: Dict[str, Vec3] = field(default_factory=dict)
    # 명중 순간 시야가 확보돼 있었는지 (Lua에서 라인트레이스로 직접 계산해 실어보냄).
    # None = 게임 쪽에서 확인 못 함(구버전 로그거나 트레이스 에러) -> 판단 보류
    los_clear: Optional[bool] = None
    timestamp_source: Optional[str] = None
    round_id: Optional[str] = None


TelemetryEvent = Union[ShotEvent, HitEvent]
