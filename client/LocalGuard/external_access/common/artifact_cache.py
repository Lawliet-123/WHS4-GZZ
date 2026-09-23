"""반복적인 해시·서명 조회 비용을 줄이는 메모리 캐시."""

from pathlib import Path
from threading import RLock
from typing import Dict, Tuple

from .artifact_inspector import ArtifactInspector
from .models import ArtifactInfo, FileFingerprint


class ArtifactCache:
    """경로·크기·수정 시각이 그대로일 때만 검사 결과를 재사용한다."""

    def __init__(self, inspector: ArtifactInspector) -> None:
        self._inspector = inspector
        self._entries: Dict[Path, Tuple[FileFingerprint, ArtifactInfo]] = {}
        self._lock = RLock()

    def inspect(self, path: Path) -> ArtifactInfo:
        fingerprint = file_fingerprint(path)
        with self._lock:
            cached = self._entries.get(fingerprint.path)
            if cached and cached[0] == fingerprint:
                return cached[1]
        inspected = self._inspector.inspect(fingerprint.path)
        with self._lock:
            self._entries[fingerprint.path] = (fingerprint, inspected)
        return inspected

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def file_fingerprint(path: Path) -> FileFingerprint:
    path = Path(path).expanduser().resolve()
    metadata = path.stat()
    return FileFingerprint(path, metadata.st_size, metadata.st_mtime_ns)
