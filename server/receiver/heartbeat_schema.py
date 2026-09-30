"""Strict JSON decoding for the verified, HWID-free heartbeat v3 contract."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, validators


class HeartbeatValidationError(ValueError):
    """The heartbeat cannot be accepted as a v3 JSON message."""


# JSON Schema accepts 1.0 as an integer by default; the wire contract uses JSON
# integers, including for sequence. Do not coerce numbers or accept booleans.
_StrictValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int
    ),
)
_SCHEMA = json.loads(Path(__file__).with_name("heartbeat.schema.json").read_text(encoding="utf-8"))
_StrictValidator.check_schema(_SCHEMA)
_VALIDATOR = _StrictValidator(_SCHEMA)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
_COMPONENT_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_DATE_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HeartbeatValidationError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise HeartbeatValidationError("non-finite JSON number")


def _check_json(value: Any, depth: int = 0, remaining: list[int] | None = None) -> None:
    if remaining is None:
        remaining = [10000]
    remaining[0] -= 1
    if depth > 12 or remaining[0] < 0:
        raise HeartbeatValidationError("heartbeat JSON is too complex")
    if type(value) is float and not math.isfinite(value):
        raise HeartbeatValidationError("non-finite JSON number")
    if type(value) is dict:
        for item in value.values():
            _check_json(item, depth + 1, remaining)
    elif type(value) is list:
        for item in value:
            _check_json(item, depth + 1, remaining)


def decode_heartbeat(body: bytes) -> dict[str, Any]:
    """Validate without changing IDs, timestamps, or component values."""
    try:
        payload = json.loads(
            body.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        _check_json(payload)
        error = next(_VALIDATOR.iter_errors(payload), None)
        if error is not None:
            # Never include request values, paths, or details in error responses.
            raise HeartbeatValidationError("heartbeat does not match the v3 schema")
        # JSON Schema's '$' anchor can match before a final newline.
        if any(not _IDENTIFIER.fullmatch(payload[name]) for name in ("session_id", "client_id")):
            raise HeartbeatValidationError("invalid heartbeat identifier")
        if any(not _COMPONENT_NAME.fullmatch(name) for name in payload["components"]):
            raise HeartbeatValidationError("invalid heartbeat component name")
        timestamp = payload["sent_at_utc"]
        if not _DATE_TIME.fullmatch(timestamp):
            raise HeartbeatValidationError("sent_at_utc must be a timezone-aware date-time")
        datetime.fromisoformat(timestamp.upper().replace("Z", "+00:00"))
        canonical_heartbeat(payload).encode("utf-8")
        return payload
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, HeartbeatValidationError):
            raise
        raise HeartbeatValidationError("invalid heartbeat JSON") from exc


def canonical_heartbeat(payload: dict[str, Any]) -> str:
    """Key order and whitespace do not change duplicate request identity."""
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
