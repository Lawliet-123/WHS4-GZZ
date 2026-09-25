"""외부 프로세스 접근 분석에만 필요한 관찰값."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..common.models import ArtifactInfo


@dataclass(frozen=True)
class ExternalHandleObservation:
    """한 외부 프로세스가 게임 프로세스에 보유한 핸들 관찰값."""

    source_pid: int
    source_name: str
    source_path: Optional[Path]
    granted_access: int
    artifact: Optional[ArtifactInfo] = None


@dataclass(frozen=True)
class ScanContext:
    """모든 탐지 결과에 공통으로 들어갈 호출 시점 정보."""

    session_id: str
    player_id: str
    timestamp_ms: int
