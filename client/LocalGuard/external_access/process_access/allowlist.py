"""검토가 끝난 정상 외부 프로세스의 정확한 이름+SHA-256 allowlist."""

import json
import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class ProcessAllowlistEntry:
    executable_name: str
    sha256: str
    note: Optional[str] = None
    executable_path: Optional[str] = None
    required_signature_status: Optional[str] = None
    publisher_contains: Optional[str] = None

    def matches(
        self,
        executable_name: str,
        sha256: str,
        *,
        executable_path: Optional[Path] = None,
        signature_status: Optional[str] = None,
        publisher: Optional[str] = None,
    ) -> bool:
        if self.executable_name.casefold() != executable_name.casefold():
            return False
        if self.sha256.casefold() != sha256.casefold():
            return False
        if self.executable_path is not None:
            if executable_path is None:
                return False
            if _normalize_windows_path(self.executable_path) != _normalize_windows_path(executable_path):
                return False
        if self.required_signature_status is not None:
            if (signature_status or "").casefold() != self.required_signature_status.casefold():
                return False
        if self.publisher_contains is not None:
            if self.publisher_contains.casefold() not in (publisher or "").casefold():
                return False
        return True


class ProcessAllowlist:
    def __init__(self, entries: Iterable[ProcessAllowlistEntry] = ()) -> None:
        self.entries = tuple(entries)

    @classmethod
    def from_json(cls, path: Path) -> "ProcessAllowlist":
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries: List[ProcessAllowlistEntry] = []
        for index, item in enumerate(raw.get("entries", [])):
            try:
                name = str(item["executable_name"]).strip()
                sha256 = str(item["sha256"]).strip().lower()
            except (KeyError, TypeError) as error:
                raise ValueError(f"allowlist entries[{index}] 형식 오류") from error
            if not name or len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
                raise ValueError(f"allowlist entries[{index}] 이름 또는 SHA-256 오류")
            executable_path = _optional_nonempty_string(item, "executable_path", index)
            signature_status = _optional_nonempty_string(item, "signature_status", index)
            publisher_contains = _optional_nonempty_string(item, "publisher_contains", index)
            entries.append(
                ProcessAllowlistEntry(
                    executable_name=name,
                    sha256=sha256,
                    note=item.get("note"),
                    executable_path=executable_path,
                    required_signature_status=signature_status,
                    publisher_contains=publisher_contains,
                )
            )
        return cls(entries)

    def find(
        self,
        executable_name: str,
        sha256: str,
        *,
        executable_path: Optional[Path] = None,
        signature_status: Optional[str] = None,
        publisher: Optional[str] = None,
    ) -> Optional[ProcessAllowlistEntry]:
        return next(
            (
                entry
                for entry in self.entries
                if entry.matches(
                    executable_name,
                    sha256,
                    executable_path=executable_path,
                    signature_status=signature_status,
                    publisher=publisher,
                )
            ),
            None,
        )


def _optional_nonempty_string(item: dict, key: str, index: int) -> Optional[str]:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"allowlist entries[{index}].{key} 형식 오류")
    return value.strip()


def _normalize_windows_path(path: object) -> str:
    """환경 변수를 확장한 뒤 Windows 경로를 대소문자와 구분자에 무관하게 비교한다."""
    expanded = os.path.expandvars(str(path)).replace("/", "\\")
    return ntpath.normcase(ntpath.normpath(expanded))
