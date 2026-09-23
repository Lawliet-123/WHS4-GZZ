"""전송 없이 탐지 결과를 로컬 JSONL 파일에 남기는 출력 도구."""

import json
from pathlib import Path
from typing import Any, Mapping

from .detection_result import validate_detection_result


def append_detection_jsonl(path: Path, result: Mapping[str, Any]) -> None:
    """검증한 탐지 결과 하나를 UTF-8 JSONL 한 줄로 추가한다."""
    validate_detection_result(result)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(dict(result), ensure_ascii=False, separators=(",", ":")))
        output.write("\n")
