"""Fail closed when the production server environment is unsafe or incomplete."""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Mapping


SECRET_NAMES = (
    "GZZ_TELEMETRY_TOKEN",
    "MECCHA_HEARTBEAT_TOKEN",
    "GZZ_DASHBOARD_TOKEN",
    "GZZ_DASHBOARD_CURSOR_SECRET",
)
PATH_NAMES = (
    "GZZ_TELEMETRY_LOG_ROOT",
    "GZZ_SCORING_DB",
    "MECCHA_HEARTBEAT_DB",
    "GZZ_DASHBOARD_INDEX",
)
STATE_ROOT = PurePosixPath("/var/lib/meccha-anticheat")


def validate_environment(environment: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    secrets: dict[str, str] = {}

    for name in SECRET_NAMES:
        value = environment.get(name, "").strip()
        secrets[name] = value
        if not value:
            errors.append(f"{name} is required")
        elif "REPLACE_" in value.upper() or "<" in value or ">" in value:
            errors.append(f"{name} still contains an example placeholder")
        elif len(value) < 32:
            errors.append(f"{name} must contain at least 32 characters")

    populated = [value for value in secrets.values() if value]
    if len(populated) != len(set(populated)):
        errors.append("server secrets must use independent values")

    for name in PATH_NAMES:
        raw_value = environment.get(name, "").strip()
        if not raw_value:
            errors.append(f"{name} is required")
            continue

        configured = PurePosixPath(raw_value)
        if not configured.is_absolute():
            errors.append(f"{name} must be an absolute path")
            continue

        try:
            configured.relative_to(STATE_ROOT)
        except ValueError:
            errors.append(f"{name} must stay under {STATE_ROOT}")

    return errors


def main() -> int:
    errors = validate_environment(os.environ)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("PASS: production server environment is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
