"""실행 파일 경로가 사용자 쓰기 가능 영역에 있는지 분류한다."""

import ntpath
import os
from pathlib import Path
from typing import Mapping, Optional


def classify_suspicious_path(
    path: Path,
    environment: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """명확한 사용자 쓰기 가능 경로면 안정된 위험 분류명을 반환한다.

    경로만으로 치트를 확정하지 않는다. 위험한 게임 handle이 이미 관찰된 경우에만
    판정기가 이 값을 약한 보조 근거로 사용한다.
    """
    env = os.environ if environment is None else environment
    candidate = _normalize_windows_path(path)
    roots = []

    for key in ("TEMP", "TMP", "LOCALAPPDATA", "APPDATA"):
        value = env.get(key)
        if value:
            roots.append(value)

    user_profile = env.get("USERPROFILE")
    if user_profile:
        roots.extend(
            (
                ntpath.join(user_profile, "Desktop"),
                ntpath.join(user_profile, "Downloads"),
            )
        )

    if any(_is_same_or_child(candidate, _normalize_windows_path(root)) for root in roots):
        return "user_writable_location"
    return None


def _normalize_windows_path(path: object) -> str:
    expanded = os.path.expandvars(str(path)).replace("/", "\\")
    return ntpath.normcase(ntpath.normpath(expanded))


def _is_same_or_child(candidate: str, root: str) -> bool:
    try:
        return ntpath.commonpath((candidate, root)) == root
    except ValueError:
        # 드라이브가 다르거나 형식이 잘못된 경로는 이 규칙으로 분류하지 않는다.
        return False
