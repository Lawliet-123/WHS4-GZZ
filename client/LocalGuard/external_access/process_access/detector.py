"""외부 핸들 관찰값을 팀 공통 탐지 결과 JSON으로 변환한다."""

from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from ..common.detection_result import build_detection_result
from .access_rights import describe_access_mask, risky_access_score
from .models import ExternalHandleObservation, ScanContext
from .path_trust import classify_suspicious_path

SIGNATURE_WEIGHTS = {
    # unsigned는 위험 핸들과 결합할 때만 보조 근거가 된다.
    "unsigned": 1,
    # 변조/신뢰 실패는 단순 미서명보다 강한 보조 근거다.
    "invalid": 2,
    # 조회 실패 자체로 치트를 확정하지 않고, 위험 handle과 결합할 때만 약하게 반영한다.
    "unknown": 1,
}

SUSPICIOUS_PATH_WEIGHT = 1


class ProcessAccessDetector:
    """관찰된 위험 핸들을 raw_score와 설명 가능한 evidence로 바꾼다."""

    module_name = "localguard"
    submodule_name = "external_process"

    def __init__(self, environment: Optional[Mapping[str, str]] = None) -> None:
        self._environment = environment

    def evaluate(self, observation: ExternalHandleObservation, context: ScanContext) -> Optional[Dict[str, Any]]:
        access_score = risky_access_score(observation.granted_access)
        # VM_READ 단독 등 위험 권한이 없는 관찰은 로그를 만들지 않는다.
        if access_score == 0:
            return None

        rights = describe_access_mask(observation.granted_access)
        evidence: Dict[str, Any] = {
            "submodule": self.submodule_name,
            "source_pid": observation.source_pid,
            "source_process": observation.source_name,
            "source_path": str(observation.source_path) if observation.source_path else None,
            "access_mask": f"0x{observation.granted_access:08X}",
            "access_rights": rights,
        }
        reasons = [f"External process opened {right} handle" for right in rights if right != "PROCESS_VM_READ"]
        score = access_score

        if observation.artifact:
            artifact = observation.artifact
            evidence.update(
                {
                    "sha256": artifact.sha256,
                    "signature_status": artifact.signature_status,
                    "publisher": artifact.publisher,
                }
            )
            signature_score = SIGNATURE_WEIGHTS.get(artifact.signature_status, 0)
            if signature_score:
                score += signature_score
                reasons.append(f"Process executable signature is {artifact.signature_status}")

            path_risk = classify_suspicious_path(artifact.path, self._environment)
            if path_risk:
                evidence["path_risk"] = path_risk
                score += SUSPICIOUS_PATH_WEIGHT
                reasons.append("Process executable is located in a user-writable directory")
        else:
            # 접근 주체가 보호 프로세스라 경로를 읽지 못할 수도 있다. 신뢰정보 없음은
            # 위험 handle과 결합한 경우에만 약한 보조 근거로 반영한다.
            evidence["signature_status"] = "unknown"
            score += SIGNATURE_WEIGHTS["unknown"]
            reasons.append("Process executable trust information is unavailable")

        return build_detection_result(
            session_id=context.session_id,
            player_id=context.player_id,
            module=self.module_name,
            timestamp_ms=context.timestamp_ms,
            evidence=evidence,
            reasons=reasons,
            raw_score=score,
        )
