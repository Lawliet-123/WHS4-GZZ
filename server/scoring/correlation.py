"""탐지기 간 중복/상관 가능성을 읽기 전용으로 찾는 보조 계층.

이 모듈은 이벤트를 삭제하거나 점수를 합치지 않는다. 서로 다른 탐지기가 같은
현상을 관측했을 가능성이 있는 경우에만 CorrelationCandidate를 만들어 후속 정책이
검토할 수 있게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from shared.schema import encode_event, validate_event_id

from .policies.contract import PolicyEvaluation


# 측정 실패/미확정 상태는 실제 관측 근거로 상관 후보를 만들지 않는다.
_UNUSABLE_STATES = frozenset({
    "MEASUREMENT_UNAVAILABLE",
    "UNKNOWN_MODULE",
    "AWAITING_DETECTOR",
})


@dataclass(frozen=True)
class CorrelationObservation:
    """공통 Event와 정책 평가를 상관 분석에 필요한 최소 정보로 고정한 값."""

    event_id: str
    sequence: int
    session_id: str
    player_id: str
    module: str
    timestamp_ms: int
    state: str
    entity_key: str | None
    overlap_tags: tuple[str, ...]
    reasons: tuple[str, ...]
    # 서로 다른 detector의 timestamp_ms를 비교할 수 있는지 판단하기 위한 기준.
    # None은 기존 producer처럼 별도 기준을 명시하지 않은 경우다.
    timestamp_basis: str | None = None


@dataclass(frozen=True)
class CorrelationCandidate:
    """두 탐지 결과가 같은 현상을 봤을 가능성이 있다는 읽기 전용 후보.

    후보 생성 자체는 중복 확정, 점수 감산, 최종 위험도 판정을 뜻하지 않는다.
    """

    session_id: str
    player_id: str
    modules: tuple[str, str]
    event_ids: tuple[str, str]
    sequences: tuple[int, int]
    overlap_tags: tuple[str, ...]
    entity_keys: tuple[str | None, str | None]
    time_distance_ms: int
    reasons: tuple[str, ...]


def observation_from_evaluation(
    event: Mapping[str, Any],
    *,
    event_id: str,
    sequence: int,
    evaluation: PolicyEvaluation,
) -> CorrelationObservation:
    """검증된 공통 Event와 PolicyEvaluation을 상관 분석용 관측값으로 변환한다."""
    # encode_event()로 7필드 스키마를 다시 확인하고 호출자 Mapping은 수정하지 않는다.
    encode_event(event)
    event_id = validate_event_id(event_id)
    if type(sequence) is not int or sequence <= 0:
        raise ValueError("sequence must be a positive integer")
    if evaluation.signal.module != event["module"]:
        raise ValueError("policy evaluation module does not match event module")

    timestamp_basis = event["evidence"].get("timestamp_basis")
    if not isinstance(timestamp_basis, str) or not timestamp_basis.strip():
        timestamp_basis = None

    return CorrelationObservation(
        event_id=event_id,
        sequence=sequence,
        session_id=event["session_id"],
        player_id=event["player_id"],
        module=event["module"],
        timestamp_ms=event["timestamp_ms"],
        state=evaluation.signal.state,
        entity_key=evaluation.annotations.entity_key,
        overlap_tags=evaluation.annotations.overlap_tags,
        reasons=tuple(event["reasons"]),
        timestamp_basis=timestamp_basis,
    )


def find_correlation_candidates(
    observations: Iterable[CorrelationObservation],
    *,
    max_time_distance_ms: int,
) -> list[CorrelationCandidate]:
    """같은 플레이어의 서로 다른 모듈 관측 중 상관 후보만 반환한다.

    현재 단계의 필수 조건은 같은 session/player, 공통 overlap_tag, 지정한 시간 창이다.
    entity_key는 모듈마다 PID/대상 등 의미가 다를 수 있어 일치 여부를 기록만 하고
    서로 다른 값이라는 이유만으로 후보를 제거하지 않는다.
    """
    if type(max_time_distance_ms) is not int or max_time_distance_ms < 0:
        raise ValueError("max_time_distance_ms must be a nonnegative integer")

    items = sorted(
        observations,
        key=lambda item: (
            item.session_id,
            item.player_id,
            item.timestamp_ms,
            item.sequence,
            item.event_id,
        ),
    )
    candidates: list[CorrelationCandidate] = []

    for left_index, left in enumerate(items):
        if left.state in _UNUSABLE_STATES or not left.overlap_tags:
            continue
        for right in items[left_index + 1:]:
            if right.state in _UNUSABLE_STATES or not right.overlap_tags:
                continue
            if left.session_id != right.session_id or left.player_id != right.player_id:
                continue
            # 같은 모듈의 재전송/반복 표본 처리는 event_id 및 저장 계층의 책임이다.
            if left.module == right.module:
                continue

            shared_tags = tuple(sorted(set(left.overlap_tags) & set(right.overlap_tags)))
            if not shared_tags:
                continue

            # Hide Anywhere는 현재 detector 자체 시작 시각을 timestamp_ms 원점으로 쓴다.
            # launcher/session 공통 원점의 다른 detector와 숫자를 직접 비교하면
            # 실제 동시 사건도 멀리 떨어진 것으로 오판할 수 있다.
            #
            # 공통 timebase 계약이 생기기 전까지는 local_session_start가 포함된
            # cross-module pair를 overlap 후보로 만들지 않는다.
            if (
                left.timestamp_basis == "local_session_start"
                or right.timestamp_basis == "local_session_start"
            ):
                continue

            distance = abs(left.timestamp_ms - right.timestamp_ms)
            if distance > max_time_distance_ms:
                continue

            entity_keys = (left.entity_key, right.entity_key)
            reasons = [
                "공통 overlap_tag가 있어 같은 현상 관측 가능성을 검토한다: "
                + ", ".join(shared_tags),
                f"이벤트 시간 차이 {distance}ms가 조회 시간 창 {max_time_distance_ms}ms 이내다.",
            ]
            if left.entity_key is not None and right.entity_key is not None:
                if left.entity_key == right.entity_key:
                    reasons.append("두 정책의 entity_key가 같다.")
                else:
                    reasons.append(
                        "entity_key 값은 다르지만 모듈 간 key namespace가 아직 합의되지 않아 후보를 자동 제외하지 않는다."
                    )
            else:
                reasons.append("한쪽 이상에 entity_key가 없어 엔터티 동일성은 아직 확정할 수 없다.")

            candidates.append(CorrelationCandidate(
                session_id=left.session_id,
                player_id=left.player_id,
                modules=(left.module, right.module),
                event_ids=(left.event_id, right.event_id),
                sequences=(left.sequence, right.sequence),
                overlap_tags=shared_tags,
                entity_keys=entity_keys,
                time_distance_ms=distance,
                reasons=tuple(reasons),
            ))

    return candidates
