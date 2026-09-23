"""EXE/DLL 파일의 SHA-256과 Windows Authenticode 정보를 조회한다."""

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional, Tuple

from .models import ArtifactInfo

SignatureReader = Callable[[Path], Tuple[str, Optional[str]]]


class ArtifactInspector:
    """파일 검사를 한 곳에 모아 EXE와 DLL이 같은 기준을 쓰게 한다."""

    def __init__(self, signature_reader: Optional[SignatureReader] = None) -> None:
        self._signature_reader = signature_reader or read_windows_authenticode

    def inspect(self, path: Path) -> ArtifactInfo:
        path = Path(path).expanduser().resolve()
        signature_status, publisher = self._signature_reader(path)
        return ArtifactInfo(
            path=path,
            sha256=calculate_sha256(path),
            signature_status=signature_status,
            publisher=publisher,
        )


def calculate_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """대용량 DLL도 한 번에 메모리에 올리지 않고 SHA-256을 계산한다."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_windows_authenticode(path: Path) -> Tuple[str, Optional[str]]:
    """Windows 서명 상태와 게시자를 조회한다.

    서명 조회 불가 자체는 치트 근거가 아니므로, 오류는 unknown으로 돌린다.
    """
    if os.name != "nt":
        return "unknown", None
    script = (
        "& { $sig = Get-AuthenticodeSignature -LiteralPath $env:LOCALGUARD_ARTIFACT_PATH; "
        "Write-Output ([string]$sig.Status); "
        "if ($sig.SignerCertificate) { Write-Output ([string]$sig.SignerCertificate.Subject) } }"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False, capture_output=True, text=True, encoding="utf-8", timeout=15,
            env={**os.environ, "LOCALGUARD_ARTIFACT_PATH": str(path)},
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", None
    if completed.returncode != 0 or not completed.stdout.strip():
        return "unknown", None
    lines = completed.stdout.splitlines()
    status = lines[0].strip() if lines else "UnknownError"
    publisher = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None
    if status == "Valid":
        return "trusted", publisher
    if status == "NotSigned":
        return "unsigned", None
    if status in {"HashMismatch", "NotTrusted"}:
        return "invalid", publisher
    return "unknown", publisher
