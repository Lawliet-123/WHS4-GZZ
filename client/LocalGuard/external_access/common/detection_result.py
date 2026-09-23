"""확정된 탐지 결과 JSON을 만들고 검증한다.

중앙 서버 전송은 이 모듈의 범위가 아니다. 반환한 dict는 JSONL에 기록하거나
나중에 TelemetryServer 전송 모듈에 그대로 넘긴다.
"""

from collections.abc import Mapping
from numbers import Real
from typing import Any, Dict, Iterable


REQUIRED_FIELDS = (
    "session_id", "player_id", "module", "timestamp_ms",
    "evidence", "reasons", "raw_score",
)


def build_detection_result(*, session_id: str, player_id: str, module: str,
                           timestamp_ms: int, evidence: Mapping[str, Any],
                           reasons: Iterable[str], raw_score: Real) -> Dict[str, Any]:
    """합의한 7개 필드를 모두 갖춘 탐지 결과 dict를 반환한다."""
    result = {
        "session_id": session_id,
        "player_id": player_id,
        "module": module,
        "timestamp_ms": timestamp_ms,
        "evidence": dict(evidence),
        "reasons": list(reasons),
        "raw_score": raw_score,
    }
    validate_detection_result(result)
    return result


def validate_detection_result(result: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in result]
    if missing:
        raise ValueError("탐지 결과 필수 필드 누락: " + ", ".join(missing))
    for field in ("session_id", "player_id", "module"):
        if not isinstance(result[field], str) or not result[field].strip():
            raise ValueError(f"{field}는 비어 있지 않은 문자열이어야 함")
    timestamp_ms = result["timestamp_ms"]
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        raise ValueError("timestamp_ms는 0 이상의 정수여야 함")
    if not isinstance(result["evidence"], Mapping):
        raise ValueError("evidence는 JSON 객체(dict)여야 함")
    reasons = result["reasons"]
    if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
        raise ValueError("reasons는 문자열 배열이어야 함")
    raw_score = result["raw_score"]
    if isinstance(raw_score, bool) or not isinstance(raw_score, Real):
        raise ValueError("raw_score는 숫자여야 함")
