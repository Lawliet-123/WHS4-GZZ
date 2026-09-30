"""A/B가 공통으로 사용하는 탐지기별 정책 *분석* 인터페이스.

B2a policy.inspect_event()가 이미 검증한 의미를 유지하면서 탐지기 담당자가
출처 식별자와 중복 가능성에 관한 메타데이터만 안전하게 추가할 수 있도록 한다.
최종 위험도, 확률, 가중치, 판정은 의도적으로 다루지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from shared.schema import encode_event, validate_identifier

from ..policy import SignalPreview, inspect_event


@dataclass(frozen=True)
class PolicyAnnotations:
    """탐지기 담당자가 보고하는 부가 정보. 이 자체로 중복/치트를 확정하지 않는다.

    entity_key: PID나 탐지 대상 구분자 등, 같은 모듈 안의 관측 대상을 구분할 때 사용.
    overlap_tags: 탐지 근거 간 중복 가능성을 검토하기 위한 *후보* 태그.
    notes: 특정 탐지기의 누락/전송 방식 등 담당자가 확인한 주의사항.
    """

    entity_key: str | None = None
    overlap_tags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyEvaluation:
    """기존 B2a 점검 결과와 탐지기별 주석을 분리하여 보존한다."""

    signal: SignalPreview
    annotations: PolicyAnnotations


# 함수의 첫 인자는 공통 7필드 Event, 둘째 인자는 B2a의 읽기 전용 점검 결과다.
# 담당자 파일은 동일한 형태의 함수를 export하면 된다.
PolicyHandler = Callable[[Mapping[str, Any], SignalPreview], PolicyAnnotations]


class PolicyRegistry:
    """모듈 이름과 담당자별 정책 함수를 연결하는 등록소.

    테스트/서버마다 독립 객체로 구성하므로 전역 등록 상태가 다른 테스트나
    서버 실행에 영향을 주지 않는다. 기존 Receiver/process() 경로는 건드리지 않는다.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, PolicyHandler] = {}

    def register(self, module: str, handler: PolicyHandler) -> None:
        """정확한 Shared module 이름으로 등록하고 중복 등록은 거부한다."""
        validate_identifier(module)
        if not callable(handler):
            raise TypeError("policy handler must be callable")
        if module in self._handlers:
            raise ValueError(f"policy handler is already registered: {module}")
        self._handlers[module] = handler

    def evaluate(self, event: Mapping[str, Any]) -> PolicyEvaluation:
        """입력을 검증한 후 등록된 담당자 함수를 실행한다.

        원본 이벤트를 JSON 복사하므로 담당자 함수가 중첩된 evidence를 바꿔도
        호출자의 원본 데이터와 기존 Scoring 저장 내용은 훼손하지 않는다.
        먼저 B2a를 실행하며 ERROR/OFFLINE/미확정 모듈은 그 상태를 보존한다.
        """
        # 공통 7필드 검사 및 깊은 복사. 바깥 Mapping만 읽기 전용으로 전달한다.
        detached = json.loads(encode_event(event))
        signal = inspect_event(detached)
        handler = self._handlers.get(signal.module)
        if handler is None:
            # 신규/미등록 탐지기를 '정상 0점'으로 치환하지 않는다.
            return PolicyEvaluation(signal, PolicyAnnotations())

        annotations = handler(MappingProxyType(detached), signal)
        if not isinstance(annotations, PolicyAnnotations):
            raise TypeError("policy handler must return PolicyAnnotations")
        self._validate_annotations(annotations)
        # B2a의 상태와 점수를 담당자가 임의 변경하지 못하도록 그대로 반환한다.
        return PolicyEvaluation(signal, annotations)

    @staticmethod
    def _validate_annotations(value: PolicyAnnotations) -> None:
        """담당자별 반환 형식의 오타와 지나치게 큰 메타데이터를 조기 탐지한다."""
        if value.entity_key is not None and (
            not isinstance(value.entity_key, str)
            or not 1 <= len(value.entity_key) <= 128
        ):
            raise ValueError("entity_key must be None or a nonempty string of at most 128 chars")
        if not isinstance(value.overlap_tags, tuple) or len(value.overlap_tags) > 32:
            raise ValueError("overlap_tags must be a tuple of up to 32 strings")
        if not all(isinstance(tag, str) and 1 <= len(tag) <= 128 for tag in value.overlap_tags):
            raise ValueError("each overlap_tag must be a nonempty string of at most 128 chars")
        if len(set(value.overlap_tags)) != len(value.overlap_tags):
            raise ValueError("overlap_tags cannot contain duplicates")
        if not isinstance(value.notes, tuple) or len(value.notes) > 32:
            raise ValueError("notes must be a tuple of up to 32 strings")
        if not all(isinstance(note, str) and 1 <= len(note) <= 1000 for note in value.notes):
            raise ValueError("each note must be a nonempty string of at most 1000 chars")
