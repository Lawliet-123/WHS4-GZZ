"""Seven-field Event boundary. No scoring, labels, heartbeat or game dependencies."""
from __future__ import annotations

import json
import math
import re
import uuid
from typing import Any, Mapping

from .errors import ValidationError

EVENT_FIELDS = frozenset({"session_id", "player_id", "module", "timestamp_ms", "evidence", "reasons", "raw_score"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def validate_identifier(value: Any) -> str:
    if (not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or value in {".", ".."}
            or value.endswith(".") or value.split(".")[0].upper() in _RESERVED):
        raise ValidationError("IDs must be 1..128 safe ASCII letters/digits/underscore/dot/hyphen, not reserved names")
    return value


def validate_event_id(value: Any) -> str:
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValidationError("event_id must be a canonical lowercase UUID") from exc
    return value


def _finite_number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _json_value(value: Any, depth: int, remaining: list[int]) -> None:
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 12:
        raise ValidationError("Event JSON is too deeply nested or contains too many values")
    if value is None or type(value) in (str, bool):
        return
    if type(value) in (int, float):
        if not _finite_number(value):
            raise ValidationError("non-finite or unrepresentable number")
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("JSON object keys must be strings")
            _json_value(item, depth + 1, remaining)
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1, remaining)
        return
    raise ValidationError("Event contains a non-JSON value")


def encode_event(result: Mapping[str, Any], *, max_bytes: int = 256 * 1024) -> bytes:
    """Return detached, canonical UTF-8 JSON; preserve values and elapsed timestamp."""
    if not isinstance(result, Mapping) or set(result) != EVENT_FIELDS:
        raise ValidationError("Event must contain exactly the seven agreed root fields")
    event = dict(result)
    for name in ("session_id", "player_id", "module"):
        validate_identifier(event[name])
    if type(event["timestamp_ms"]) is not int or not 0 <= event["timestamp_ms"] <= 2**63 - 1:
        raise ValidationError("timestamp_ms must be a nonnegative 64-bit integer")
    if not _finite_number(event["raw_score"]) or event["raw_score"] < 0:
        raise ValidationError("raw_score must be a finite nonnegative number, not a boolean")
    if type(event["evidence"]) is not dict:
        raise ValidationError("evidence must be an object")
    if type(event["reasons"]) is not list or not all(isinstance(x, str) for x in event["reasons"]):
        raise ValidationError("reasons must be an array of strings")
    _json_value(event, 0, [10000])
    try:
        payload = json.dumps(event, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError("Event cannot be encoded as UTF-8 JSON") from exc
    if len(payload) > max_bytes:
        raise ValidationError("Event exceeds configured byte limit")
    return payload


def decode_event(payload: bytes, *, max_bytes: int = 256 * 1024) -> dict:
    """Receiver helper: rejects duplicate JSON keys as well as invalid Event fields."""
    if len(payload) > max_bytes:
        raise ValidationError("Event exceeds configured byte limit")

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValidationError("duplicate JSON key")
            value[key] = item
        return value

    try:
        event = json.loads(payload.decode("utf-8"), object_pairs_hook=unique)
        encode_event(event, max_bytes=max_bytes)
        return event
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError("invalid Event JSON") from exc
