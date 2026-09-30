from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonlWriter:
    def __init__(self, path: Path, *, append: bool = False) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "x"
        self._stream = path.open(mode, encoding="utf-8", newline="\n")

    def write(self, value: Any) -> None:
        self._stream.write(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

