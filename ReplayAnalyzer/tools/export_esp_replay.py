"""Export a completed ESP capture as a sanitized, provenance-linked replay.

This exports existing detector results. It does not collect a new session, rerun
the current detector, infer cheat timing, or copy local raw sensor logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ESP_ROOT = REPOSITORY_ROOT / "client" / "detectors" / "esp"
for import_root in (REPOSITORY_ROOT, ESP_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from anti_esp.team_format import _PATH_EVIDENCE_KEYS, _allowed_evidence_keys, _public_evidence
from shared.errors import ValidationError
from shared.schema import decode_event, encode_event, validate_identifier


CAPTURE_SCHEMA = "meccha.telemetry-session.v1"
CAPTURE_PRODUCER = "meccha-esp-localguard"
_FINGERPRINT = re.compile(r"sensor-sha256:[0-9a-f]{64}\Z")
_PATH_FINGERPRINT = re.compile(r"[0-9a-fA-F]{64}\Z")
_COMMON_EVIDENCE_KEYS = frozenset(
    {"sensor_event_id", "event_type", "categories", "status", "severity",
     "scan_start_ms", "scan_end_ms", "window_id", "sample_id", "timestamp_basis"}
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manifest contains duplicate JSON keys")
        result[key] = value
    return result


def _timing(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
        raise ValueError(f"test_metadata.{key} must be a nonnegative integer or null")
    return value


def _fingerprint_sensor_ids(value: Any, session_id: str) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = "".join(char for char in key.casefold() if char.isalnum())
            if normalized == "sensoreventid":
                if isinstance(item, str) and _FINGERPRINT.fullmatch(item):
                    result[key] = item
                else:
                    material = json.dumps(item, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"))
                    digest = hashlib.sha256((session_id + "\0" + material).encode("utf-8"))
                    result[key] = "sensor-sha256:" + digest.hexdigest()
            else:
                result[key] = _fingerprint_sensor_ids(item, session_id)
        return result
    if isinstance(value, list):
        return [_fingerprint_sensor_ids(item, session_id) for item in value]
    return value


def _restore_path_fingerprints(original: Any, sanitized: Any) -> None:
    """Keep a public basename's existing full-path hash across exports."""
    if isinstance(original, dict) and isinstance(sanitized, dict):
        for key, item in original.items():
            if key not in sanitized:
                continue
            normalized = "".join(char for char in key.casefold() if char.isalnum())
            if normalized in _PATH_EVIDENCE_KEYS and isinstance(item, str):
                hash_key = f"{key}_path_sha256"
                existing = original.get(hash_key)
                # A full path must always be rehashed. Only a valid hash paired
                # with an already public basename can retain its correlation.
                basename_only = bool(item) and item == item.strip() and not re.search(r"[\\/:]", item)
                if basename_only and isinstance(existing, str) and _PATH_FINGERPRINT.fullmatch(existing):
                    sanitized[hash_key] = existing
                else:
                    sanitized[hash_key] = _public_evidence({key: item})[hash_key]
            _restore_path_fingerprints(item, sanitized[key])
    elif isinstance(original, list) and isinstance(sanitized, list):
        for before, after in zip(original, sanitized):
            _restore_path_fingerprints(before, after)


def _export_evidence(evidence: dict[str, Any], session_id: str) -> dict[str, Any]:
    # Older captures contain arbitrary sensor payload fields. Use today's
    # telemetry allowlist before applying the same recursive privacy transform.
    event_type = evidence.get("event_type")
    allowed = _allowed_evidence_keys(event_type) if isinstance(event_type, str) else frozenset()
    selected = {key: item for key, item in evidence.items()
                if key in allowed or key in _COMMON_EVIDENCE_KEYS}
    # Valid paired path hashes are needed only as input to the idempotence fix;
    # arbitrary unpaired legacy fields still stay outside the export boundary.
    for key in tuple(selected):
        normalized = "".join(char for char in key.casefold() if char.isalnum())
        hash_key = f"{key}_path_sha256"
        if normalized in _PATH_EVIDENCE_KEYS and hash_key in evidence:
            selected[hash_key] = evidence[hash_key]
    sanitized = _public_evidence(selected)
    _restore_path_fingerprints(selected, sanitized)
    return _fingerprint_sensor_ids(sanitized, session_id)


def _capture(source: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    manifest_path = source / "manifest.json"
    events_path = source / "events.jsonl"
    if manifest_path.is_symlink() or events_path.is_symlink():
        raise ValueError("source manifest and events must be regular capture files")
    manifest_bytes = manifest_path.read_bytes()
    events_bytes = events_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as error:
        raise ValueError("source manifest is not valid UTF-8 JSON") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != CAPTURE_SCHEMA:
        raise ValueError("source must use the ESP capture manifest schema")
    if manifest.get("status") != "completed" or manifest.get("failure_reason") is not None:
        raise ValueError("source session must be completed without a failure")
    session_id = validate_identifier(manifest.get("session_id"))
    producer = manifest.get("producer")
    if not isinstance(producer, dict) or producer.get("name") != CAPTURE_PRODUCER:
        raise ValueError("source must identify the ESP capture producer")
    validate_identifier(producer.get("version"))
    revision = producer.get("revision")
    if revision is not None:
        validate_identifier(revision)
    metadata = manifest.get("test_metadata")
    if not isinstance(metadata, dict) or metadata.get("scenario") not in ("normal", "esp"):
        raise ValueError("test_metadata.scenario must be normal or esp")
    start = _timing(metadata, "cheat_on_ms")
    end = _timing(metadata, "cheat_off_ms")
    if start is not None and end is not None and end < start:
        raise ValueError("cheat_off_ms must not precede cheat_on_ms")
    if metadata["scenario"] == "normal" and (start is not None or end is not None):
        raise ValueError("NORMAL captures must not contain cheat ON/OFF timing")
    events = []
    for line_number, line in enumerate(events_bytes.removeprefix(b"\xef\xbb\xbf").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = decode_event(line)
        except ValidationError as error:
            raise ValueError(f"source event line {line_number} violates the shared seven-field schema") from error
        if event["session_id"] != session_id:
            raise ValueError(f"source event line {line_number} has a mismatched session_id")
        if event["module"] != "esp":
            raise ValueError(f"source event line {line_number} is not an ESP event")
        if event["evidence"].get("synthetic") is True:
            raise ValueError("source must contain captured results, not synthetic events")
        events.append(event)
    if not events:
        raise ValueError("source must contain ESP events with an identifiable player")
    if type(manifest.get("event_count")) is not int or manifest["event_count"] != len(events):
        raise ValueError("source manifest event_count must match events.jsonl")
    if len({event["player_id"] for event in events}) != 1:
        raise ValueError("source session must contain exactly one player_id")
    unavailable = {"ERROR", "OFFLINE", "INSUFFICIENT", "INSUFFICIENT_OBSERVATION"}
    if all(str(event["evidence"].get("status", "")).upper() in unavailable for event in events):
        raise ValueError("source must contain usable ESP observations")
    hashes = {"manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
              "events_sha256": hashlib.sha256(events_bytes).hexdigest()}
    return manifest, events, hashes


def export_session(source: str | Path, output_root: str | Path) -> Path:
    """Create output_root/session_id once; source artifacts remain untouched."""
    source = Path(source).resolve()
    output_root = Path(output_root).resolve()
    if source == output_root or source in output_root.parents:
        raise ValueError("output_root must be outside the source capture")
    capture, events, hashes = _capture(source)
    session_id = capture["session_id"]
    destination = output_root / session_id
    if destination == source:
        raise ValueError("destination must differ from the source capture")
    metadata = capture["test_metadata"]
    cheat = metadata["scenario"] == "esp"
    replay_manifest = {
        "session_id": session_id,
        "player_id": events[0]["player_id"],
        "label": "CHEAT" if cheat else "NORMAL",
        "cheat_type": "ESP" if cheat else None,
        "cheat_start_ms": metadata.get("cheat_on_ms"),
        "cheat_end_ms": metadata.get("cheat_off_ms"),
        "event_count": len(events),
        "source": {
            "kind": "existing_esp_capture",
            "schema_version": capture["schema_version"],
            "session_status": capture["status"],
            "producer": {"name": capture["producer"]["name"],
                         "version": capture["producer"]["version"],
                         "revision": capture["producer"].get("revision")},
            **hashes,
            "notes": [
                "Existing captured results; this export is not a new test run.",
                "Scores and timestamps were not recomputed with the current detector.",
                "ON/OFF timing is supplied test metadata; missing timing remains null.",
                "Evidence is privacy-filtered; original raw sensor logs are omitted.",
                "NORMAL labels remain NORMAL even when captured scores cross a threshold.",
            ],
        },
    }
    payloads = []
    for event in events:
        exported = dict(event)
        exported["evidence"] = _export_evidence(event["evidence"], session_id)
        payloads.append(encode_event(exported) + b"\n")
    files = {
        "events.jsonl": b"".join(payloads),
        "manifest.json": (json.dumps(replay_manifest, ensure_ascii=False, allow_nan=False,
                                    indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    # Validation and serialization finish before any destination is created.
    output_root.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    created: list[Path] = []
    try:
        for name, contents in files.items():
            path = destination / name
            with path.open("xb") as stream:
                created.append(path)
                stream.write(contents)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        destination.rmdir()
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Completed local ESP session folder")
    parser.add_argument("--output-root", required=True, type=Path, help="Parent of the new replay session folder")
    args = parser.parse_args()
    try:
        destination = export_session(args.source, args.output_root)
    except (ValueError, OSError) as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        parser.exit(2, f"ESP replay export failed: {message}\n")
    print(f"Exported existing ESP session {destination.name}; local raw logs omitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
