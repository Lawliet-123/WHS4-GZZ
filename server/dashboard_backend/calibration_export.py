"""Bounded readback of one fresh ESP session through the actual server APIs.

This module does not produce Events, start a server/game or evaluate calibration.
It accepts only the disposable loopback setup from ``realgame_server``. The
caller's acknowledged Shared UUID -> seven-field payload ledger is compared
against every retained Server Event and Scoring's actual current response.
An empty healthy NORMAL ledger is valid; it never creates a zero-score Event.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import urllib.error
from urllib.parse import quote, urlencode, urlsplit

from client.Launcher.realgame_e2e import read_setup
from shared.schema import EVENT_FIELDS, encode_event, validate_event_id, validate_identifier

from .browser_smoke import FixtureHTTP, checked_port, checked_run_id
from .index import DashboardIndex


SCHEMA = "meccha.esp-server-calibration-readback.v1"
SETUP_SCHEMA = "realgame-local-runtime-v1"
MAX_PAGES = 50
MAX_DETAILS = 2000
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_ROW_FIELDS = EVENT_FIELDS | {"id", "sequence", "received_at_utc", "event_kind",
                            "time_basis", "observed_at_utc", "evidence_image", "log_excerpt"}
_SNAPSHOT_FIELDS = {"session_id", "player_id", "modules", "policy", "status", "score",
                    "confidence", "assessment_available", "final_verdict", "reason_codes", "data_state"}
_VERDICT_FIELDS = {"version", "session_id", "player_id", "status", "assessment_complete",
                   "evidence_unit_count", "active_module_count", "overlap_adjustment_count",
                   "active_modules", "advisory_modules", "unresolved_modules", "deferred_modules",
                   "unavailable_modules", "reason_codes", "missing_modules", "stale_modules"}
_PRIVATE_KEYS = {"username", "user", "computername", "hostname", "windowtitle", "accountname",
                 "machinename", "deviceid", "password", "token", "testtokens", "apitoken",
                 "authorization", "cookie", "credential", "credentials", "secret", "privatekey",
                 "raw", "rawpayload", "private", "environment", "env"}
_PRIVATE_STRING = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|(?:^|\s)(?:/[A-Za-z0-9_.-]+){2,}|Bearer\s+|"
    r"synthetic-browser-(?:receiver|heartbeat|dashboard)-|-----BEGIN [^-]*PRIVATE KEY-----|https?://)",
    re.IGNORECASE,
)


class CalibrationExportError(ValueError):
    """An intentionally fixed error message, never an API body or supplied value."""


def _reject() -> None:
    raise CalibrationExportError("calibration readback rejected")


def _scope(session_id, player_id) -> tuple[str, str]:
    for value in (session_id, player_id):
        validate_identifier(value)
        if len(value) > 80:
            _reject()
    return session_id, player_id


def _privacy(value, *, depth=0, budget=None) -> None:
    """Reject, rather than rewrite, identifying fields or paths in public APIs."""
    budget = [200000] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or depth > 20:
        _reject()
    if isinstance(value, str):
        if len(value) > 256 * 1024 or _PRIVATE_STRING.search(value):
            _reject()
    elif type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                _reject()
            normalized = "".join(char for char in key.casefold() if char.isalnum())
            if normalized in _PRIVATE_KEYS or normalized.endswith(("password", "token", "apikey", "secret")):
                _reject()
            if normalized.endswith("path") and isinstance(item, str) and any(separator in item for separator in ("/", "\\")):
                _reject()
            _privacy(item, depth=depth + 1, budget=budget)
    elif type(value) is list:
        for item in value:
            _privacy(item, depth=depth + 1, budget=budget)
    elif value is not None and type(value) not in (bool, int, float):
        _reject()


def _detach(value) -> dict:
    if type(value) is not dict:
        _reject()
    _privacy(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_DOCUMENT_BYTES:
        _reject()
    return json.loads(encoded)


def create_http(setup_path: str | Path) -> FixtureHTTP:
    """Validate one explicitly supplied disposable setup; never read live env."""
    try:
        path = Path(setup_path).resolve()
        setup = read_setup(path)
        if path.stat().st_size > 64 * 1024:
            _reject()
        document = json.loads(path.read_text(encoding="utf-8"))
        if (type(document) is not dict or document.get("schema_version") != SETUP_SCHEMA
                or (document.get("port"), document.get("run_id"), document.get("endpoint"))
                != (setup["port"], setup["run_id"], setup["endpoint"])):
            _reject()
        return FixtureHTTP(setup["port"], setup["run_id"])
    except Exception as error:
        raise CalibrationExportError("calibration setup rejected: " + type(error).__name__) from None


def _validate_http(http) -> str:
    run_id = checked_run_id(http.run_id)
    parsed = urlsplit(http.url)
    port = checked_port(parsed.port)
    if http.url != f"http://127.0.0.1:{port}" or parsed.hostname != "127.0.0.1":
        _reject()
    return run_id


def _utc(value, *, nullable=False) -> datetime | None:
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value)):
        _reject()
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(stamp):
        _reject()
    return stamp


def _payload(value, session_id, player_id) -> dict:
    if type(value) is not dict or not EVENT_FIELDS.issubset(value):
        _reject()
    detached = json.loads(encode_event({field: value[field] for field in EVENT_FIELDS}))
    if (detached["session_id"], detached["player_id"], detached["module"]) != (session_id, player_id, "esp"):
        _reject()
    _privacy(detached)
    return detached


def _row(value, session_id, player_id) -> dict:
    if type(value) is not dict or not set(value).issubset(_ROW_FIELDS):
        _reject()
    original = _payload(value, session_id, player_id)
    validate_event_id(value.get("id"))
    if type(value.get("sequence")) is not int or value["sequence"] <= 0:
        _reject()
    # A nullable old receipt is retained, never reconstructed from observation.
    _utc(value.get("received_at_utc"), nullable=True)
    if value.get("event_kind") != "detection" or value.get("time_basis") not in {
        "session_relative", "unix_epoch_ms", "unknown",
    }:
        _reject()
    # Reuse the API's pure projector so no PID, magnitude or module-name
    # heuristic can reinterpret an unknown or conflicting producer clock.
    declared = DashboardIndex.event_clock(original)
    if value["time_basis"] != declared["time_basis"]:
        _reject()
    observed = _utc(value.get("observed_at_utc"), nullable=True)
    expected_observed = _utc(declared.get("observed_at_utc"), nullable=True)
    if observed != expected_observed:
        _reject()
    if value.get("evidence_image") is not None or value.get("log_excerpt") is not None:
        _reject()
    return _detach(value)


def _verdict(value, session_id, player_id) -> dict:
    if type(value) is not dict or not set(value).issubset(_VERDICT_FIELDS):
        _reject()
    if (value.get("session_id"), value.get("player_id")) != (session_id, player_id):
        _reject()
    if value.get("status") not in {"SUSPICIOUS", "INCONCLUSIVE", "NO_ACTIVE_EVIDENCE"}:
        _reject()
    if type(value.get("assessment_complete")) is not bool:
        _reject()
    if type(value.get("evidence_unit_count")) is not int or value["evidence_unit_count"] < 0:
        _reject()
    for field in ("active_modules", "reason_codes"):
        if type(value.get(field)) is not list or not all(isinstance(item, str) for item in value[field]):
            _reject()
    return _detach(value)


def collect_evidence(http, session_id: str, player_id: str, *, expected_events: Mapping,
                     max_pages: int = MAX_PAGES, max_details: int = MAX_DETAILS,
                     time_budget_seconds: float = 90) -> dict:
    """GET only. An expected UUID ledger is mandatory and must match exactly.

    The caller must separately prove healthy capture, actual PoC/behaviour timings
    and Shared HTTP acknowledgement. This readback never claims those facts.
    Snapshot/verdict are current queries, not an atomic event-watermark snapshot.
    """
    try:
        run_id = _validate_http(http)
        session_id, player_id = _scope(session_id, player_id)
        if (type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES
                or type(max_details) is not int or not 1 <= max_details <= MAX_DETAILS
                or type(time_budget_seconds) not in (int, float) or not 1 <= time_budget_seconds <= 90
                or not isinstance(expected_events, Mapping) or len(expected_events) > max_details):
            _reject()
        expected = {}
        for event_id, event in expected_events.items():
            validate_event_id(event_id)
            if type(event) is not dict or set(event) != EVENT_FIELDS:
                _reject()
            expected[event_id] = _payload(event, session_id, player_id)
        deadline = time.monotonic() + time_budget_seconds
        requests = 0

        def get(path, *, missing_ok=False):
            nonlocal requests
            if time.monotonic() >= deadline:
                _reject()
            requests += 1
            try:
                return http.request(path)
            except urllib.error.HTTPError as error:
                code = error.code
                error.close()
                if missing_ok and code == 404:
                    return None
                raise

        filters = {"session_id": session_id, "player_id": player_id, "module": "esp", "limit": 200}
        cursor = None
        seen_cursors = set()
        watermark = None
        rows = []
        ids = set()
        last_sequence = 0
        for _ in range(max_pages):
            query = dict(filters)
            if cursor is not None:
                query["cursor"] = cursor
            page = get("/api/dashboard/events?" + urlencode(query))
            if type(page) is not dict or type(page.get("items")) is not list or type(page.get("has_more")) is not bool:
                _reject()
            through = page.get("through_sequence")
            if type(through) is not int or through < 0 or (watermark is not None and through != watermark):
                _reject()
            watermark = through
            if page.get("index", {}).get("catching_up") is not False or len(page["items"]) > 200:
                _reject()
            for value in page["items"]:
                row = _row(value, session_id, player_id)
                if row["id"] in ids or not last_sequence < row["sequence"] <= through or len(rows) >= max_details:
                    _reject()
                if row["id"] not in expected or _payload(row, session_id, player_id) != expected[row["id"]]:
                    _reject()
                detail = _row(get("/api/dashboard/events/" + quote(row["id"], safe="")), session_id, player_id)
                if detail != row:
                    _reject()
                rows.append(row)
                ids.add(row["id"])
                last_sequence = row["sequence"]
            if not page["has_more"]:
                if page.get("next_cursor") is not None:
                    _reject()
                break
            next_cursor = page.get("next_cursor")
            if (not page["items"] or not isinstance(next_cursor, str) or not 1 <= len(next_cursor) <= 4096
                    or next_cursor in seen_cursors):
                _reject()
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            _reject()
        if ids != set(expected):
            _reject()
        scoped = "/api/dashboard/sessions/" + quote(session_id, safe="") + "/players/" + quote(player_id, safe="")
        snapshot = get(scoped + "/snapshot")
        if type(snapshot) is not dict or not set(snapshot).issubset(_SNAPSHOT_FIELDS):
            _reject()
        if not {"modules", "status", "score", "confidence", "assessment_available", "final_verdict"}.issubset(snapshot):
            _reject()
        if (snapshot.get("session_id"), snapshot.get("player_id")) != (session_id, player_id):
            _reject()
        modules = snapshot.get("modules")
        if type(modules) is not list or len(modules) != int(bool(rows)):
            _reject()
        if snapshot.get("score") is not None or snapshot.get("confidence") is not None:
            _reject()
        if rows:
            state = modules[0]
            if type(state) is not dict or set(state) != EVENT_FIELDS | {"event_id", "sequence"}:
                _reject()
            if state.get("event_id") not in expected or _payload(state, session_id, player_id) != expected[state["event_id"]]:
                _reject()
            matching = next(row for row in rows if row["id"] == state["event_id"])
            if state["sequence"] != matching["sequence"]:
                _reject()
        verdict = get("/api/dashboard/verdict/" + quote(session_id, safe="") + "/" + quote(player_id, safe=""),
                      missing_ok=not rows)
        if verdict is None:
            if (rows or snapshot.get("final_verdict") is not None or snapshot.get("status") != "UNKNOWN"
                    or snapshot.get("assessment_available") is not False):
                _reject()
        else:
            verdict = _verdict(verdict, session_id, player_id)
            if (snapshot.get("final_verdict") != verdict or snapshot.get("status") != verdict["status"]
                    or snapshot.get("assessment_available") is not True):
                _reject()
        return _detach({
            "schema_version": SCHEMA, "result": "SERVER_READBACK_VERIFIED", "run_id": run_id,
            "session_id": session_id, "player_id": player_id, "module": "esp",
            "event_count": len(rows), "through_sequence": watermark,
            "events": rows, "snapshot": snapshot, "final_verdict": verdict,
            "checks": {"exact_expected_event_set": True, "list_detail_equal": True,
                       "event_pagination_complete": True, "snapshot_verdict_equal": True,
                       "declared_clock_metadata_equal": True,
                       "received_at_metadata_complete": all(row.get("received_at_utc") is not None for row in rows)},
            "request_count": requests, "synthetic_events_created": 0,
            "capture_provenance": "NOT_ASSESSED", "shared_ack_provenance": "NOT_ASSESSED",
            "snapshot_watermark_atomic": False, "receipt_time_basis": "server_acceptance_utc",
        })
    except Exception as error:
        raise CalibrationExportError("calibration readback rejected: " + type(error).__name__) from None


def export_evidence(document: dict, fresh_dir: str | Path) -> Path:
    """Write one validated readback to a new caller-owned directory exclusively."""
    try:
        detached = _detach(document)
        if detached.get("schema_version") != SCHEMA or detached.get("result") != "SERVER_READBACK_VERIFIED":
            _reject()
        output = Path(fresh_dir).resolve()
        if output.exists() or not output.parent.is_dir():
            _reject()
        encoded = json.dumps(detached, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
        if len(encoded) > MAX_DOCUMENT_BYTES:
            _reject()
        output.mkdir(exist_ok=False)
        destination = output / "calibration-evidence.json"
        with destination.open("xb") as stream:
            stream.write(encoded)
        return destination
    except Exception as error:
        raise CalibrationExportError("calibration export rejected: " + type(error).__name__) from None


__all__ = ["CalibrationExportError", "create_http", "collect_evidence", "export_evidence"]
