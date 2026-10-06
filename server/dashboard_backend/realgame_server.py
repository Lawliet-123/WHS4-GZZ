"""Empty, disposable loopback API for an explicitly authorized real-game run.

  python -m server.dashboard_backend.realgame_server serve --port 8002 \
      --duration 1800 --output <new-or-empty-directory>

This helper never runs a detector, game, native lab or synthetic producer. Its
owned_server starts with empty temporary stores and explicit disposable test
tokens. Only setup metadata and an allowlisted API observation summary survive.
Create the printed stop_request_path to finish early and clean up gracefully.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
import urllib.error
from urllib.parse import quote, urlencode
import uuid

from shared.schema import EVENT_FIELDS, validate_event_id

from .browser_smoke import FixtureHTTP, checked_port, checked_run_id
from .module_integrity_e2e import owned_server


SCHEMA = "realgame-local-runtime-v1"
MAX_PAGES = 50
MAX_DETAILS = 2000
CAPTURE_SECONDS = 90
MODULES = frozenset({
    "launcher", "selfdefense", "self_defense", "kernel_watcher", "external_access",
    "external_process", "module_integrity", "input_signature", "memory_integrity",
    "whistle_spoofing", "whistle", "whistle_rpc", "aimbot", "esp", "godmode",
    "noclip", "autopaint", "hide_anywhere", "yara", "localguard_input_signature",
    "localguard_yara", "localguard_executable_hash", "filesystem", "injection",
    "value_tamper", "overlay_hook", "godmode_runtime", "noclip_runtime", "aimbot_runtime",
})
STATES = frozenset({
    "NORMAL", "SUSPICIOUS", "ERROR", "UNKNOWN", "INCONCLUSIVE", "NO_ACTIVE_EVIDENCE",
    "DETECTED", "healthy", "running", "starting", "stopping", "stopped", "failed",
    "degraded", "stale", "unknown", "online", "unavailable", "alive", "exited",
})
VERDICTS = frozenset({"SUSPICIOUS", "INCONCLUSIVE", "NO_ACTIVE_EVIDENCE"})


def label(value, allowed=MODULES):
    """Never echo an unrecognized client-controlled string into retained logs."""
    return value if isinstance(value, str) and value in allowed else "other"


def count(value):
    return value if type(value) is int and value >= 0 else None


class APIObservation:
    """GET only; responses and exception bodies remain in memory, never in logs."""

    def __init__(self, http):
        self.http = http
        self.deadline = time.monotonic() + CAPTURE_SECONDS
        self.requests = Counter()
        self.issues = set()

    def get(self, category, path, *, missing_ok=False):
        if time.monotonic() >= self.deadline:
            self.issues.add("capture_time_budget_exceeded")
            return None
        self.requests[category + "_attempted"] += 1
        try:
            value = self.http.request(path)
            self.requests[category + "_succeeded"] += 1
            return value
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if missing_ok and status == 404:
                self.requests[category + "_not_found"] += 1
                return None
            self.issues.add(category + "_request_failed")
        except Exception:
            self.issues.add(category + "_request_failed")
        return None

    def events(self):
        rows, cursor, through = [], None, None
        complete = False
        for _ in range(MAX_PAGES):
            query = {"limit": 200}
            if cursor:
                query["cursor"] = cursor
            page = self.get("events", "/api/dashboard/events?" + urlencode(query))
            if not page or not isinstance(page.get("items"), list):
                self.issues.add("invalid_events_page")
                break
            watermark = count(page.get("through_sequence"))
            if watermark is None or (through is not None and watermark != through):
                self.issues.add("events_watermark_changed")
                break
            through = watermark
            rows.extend(page["items"])
            if page.get("has_more") is False:
                complete = True
                break
            next_cursor = page.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                self.issues.add("invalid_events_cursor")
                break
            cursor = next_cursor
        if not complete:
            self.issues.add("events_pagination_incomplete")
        return rows, through, complete

    def overview(self):
        pairs, after = set(), ""
        complete = False
        first = None
        for _ in range(MAX_PAGES):
            page = self.get("overview", "/api/dashboard/overview?" + urlencode({
                "limit": 200, "after_session": after,
            }))
            if not page or page.get("schema_version") != "dashboard-v0":
                self.issues.add("invalid_overview")
                break
            first = first or page
            assessments = page.get("assessments")
            if not isinstance(assessments, list):
                self.issues.add("invalid_overview_assessments")
                break
            for row in assessments:
                if (isinstance(row, dict) and isinstance(row.get("session_id"), str)
                        and isinstance(row.get("player_id"), str)):
                    pairs.add((row["session_id"], row["player_id"]))
                else:
                    self.issues.add("invalid_subject_identity")
            pagination = page.get("session_page", {})
            if pagination.get("has_more") is False:
                complete = True
                break
            next_after = pagination.get("next_after_session")
            if not isinstance(next_after, str) or next_after <= after:
                self.issues.add("invalid_session_cursor")
                break
            after = next_after
        if not complete:
            self.issues.add("session_pagination_incomplete")
        return first or {}, pairs, complete


def collect_summary(http: FixtureHTTP) -> dict:
    """Verify real API projections without persisting payloads or subject IDs."""
    api = APIObservation(http)
    overview, pairs, sessions_complete = api.overview()
    rows, watermark, events_complete = api.events()
    module_counts, submodule_counts, event_states, event_kinds = (Counter() for _ in range(4))
    ids, sequences = set(), set()
    detail_matches = 0
    valid_rows = []
    for row in rows:
        if not isinstance(row, dict) or not EVENT_FIELDS.issubset(row):
            api.issues.add("invalid_event_shape")
            continue
        valid_rows.append(row)
        pair = (row["session_id"], row["player_id"])
        if not all(isinstance(value, str) for value in pair):
            api.issues.add("invalid_subject_identity")
            continue
        pairs.add(pair)
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        module_counts[label(row["module"])] += 1
        submodule_counts[label(evidence.get("submodule"))] += 1
        event_states[label(evidence.get("status"), STATES)] += 1
        event_kinds[label(row.get("event_kind"), {"operational", "detection"})] += 1
        try:
            validate_event_id(row.get("id"))
        except Exception:
            api.issues.add("invalid_event_identity")
            continue
        sequence = count(row.get("sequence"))
        if (sequence is None or sequence == 0 or row["id"] in ids or sequence in sequences
                or (sequences and sequence <= max(sequences))):
            api.issues.add("invalid_event_sequence")
            continue
        ids.add(row["id"])
        sequences.add(sequence)
        if len(ids) <= MAX_DETAILS:
            detail = api.get("detail", "/api/dashboard/events/" + quote(row["id"], safe=""))
            if (detail and detail.get("id") == row["id"] and detail.get("sequence") == sequence
                    and all(detail.get(field) == row[field] for field in EVENT_FIELDS)):
                detail_matches += 1
            else:
                api.issues.add("event_list_detail_mismatch")
    if len(rows) > MAX_DETAILS:
        api.issues.add("detail_check_limit_exceeded")

    subjects, launcher_sources = [], 0
    verdict_counts = Counter()
    for index, (session, player) in enumerate(sorted(pairs), 1):
        scope = "/api/dashboard/sessions/" + quote(session, safe="") + "/players/" + quote(player, safe="")
        snapshot = api.get("snapshot", scope + "/snapshot")
        verdict = api.get("verdict", "/api/dashboard/verdict/" + quote(session, safe="")
                          + "/" + quote(player, safe=""), missing_ok=True)
        status = api.get("status", scope + "/status")
        snapshot = snapshot or {}
        status = status or {}
        snapshot_modules = snapshot.get("modules", [])
        has_scoring = isinstance(snapshot_modules, list) and bool(snapshot_modules)
        identity_matches = (snapshot.get("session_id") == session and snapshot.get("player_id") == player
                            and status.get("session_id") == session and status.get("player_id") == player)
        verdict_matches = bool(verdict and verdict.get("session_id") == session
                               and verdict.get("player_id") == player
                               and snapshot.get("final_verdict") == verdict
                               and snapshot.get("status") == verdict.get("status")
                               and verdict.get("status") in VERDICTS
                               and type(verdict.get("assessment_complete")) is bool)
        if not identity_matches:
            api.issues.add("snapshot_status_identity_mismatch")
        if has_scoring and not verdict_matches:
            api.issues.add("snapshot_verdict_mismatch")
        if not has_scoring and (verdict is not None or snapshot.get("final_verdict") is not None):
            api.issues.add("verdict_without_scoring_state")
        if snapshot.get("score") is not None or snapshot.get("confidence") is not None:
            api.issues.add("unexpected_dashboard_numeric_assessment")
        verdict_status = label(verdict.get("status"), VERDICTS) if verdict else "UNAVAILABLE"
        verdict_counts[verdict_status] += 1
        sources = status.get("sources", [])
        source_summaries = []
        for source_index, source in enumerate(sources if isinstance(sources, list) else [], 1):
            if not isinstance(source, dict) or not isinstance(source.get("client_id"), str):
                api.issues.add("invalid_heartbeat_source")
                continue
            heartbeat = api.get("heartbeat", "/api/dashboard/heartbeat/" + quote(session, safe="")
                                + "/" + quote(source["client_id"], safe=""))
            heartbeat_matches = bool(heartbeat and heartbeat.get("session_id") == session
                                     and heartbeat.get("client_id") == source["client_id"]
                                     and heartbeat.get("payload", {}).get("player_id") == player
                                     and heartbeat.get("sequence") == source.get("sequence"))
            if not heartbeat_matches:
                # A live Launcher may advance between the status and heartbeat GETs.
                api.issues.add("heartbeat_status_sequence_changed_or_mismatched")
            is_launcher = source.get("role") == "launcher"
            launcher_sources += int(is_launcher)
            components = []
            for component in source.get("components", []):
                if isinstance(component, dict):
                    components.append({"module": label(component.get("id")),
                                       "state": label(component.get("state"), STATES),
                                       "required": component.get("required") is True})
            source_summaries.append({"source": "source_" + str(source_index),
                                     "role": "launcher" if is_launcher else "component",
                                     "state": label(source.get("state"), STATES),
                                     "sequence": count(source.get("sequence")),
                                     "heartbeat_query_matches_status": heartbeat_matches,
                                     "components": components})
        subjects.append({
            "subject": "subject_" + str(index), "event_count": sum(
                (row["session_id"], row["player_id"]) == (session, player) for row in valid_rows),
            "snapshot_modules": sorted({label(row.get("module")) for row in snapshot_modules
                                        if isinstance(row, dict)}) if isinstance(snapshot_modules, list) else [],
            "snapshot_status": label(snapshot.get("status"), STATES),
            "verdict_status": verdict_status, "snapshot_verdict_match": verdict_matches if has_scoring else None,
            "assessment_complete": verdict.get("assessment_complete") if verdict
                and type(verdict.get("assessment_complete")) is bool else None,
            "evidence_unit_count": count(verdict.get("evidence_unit_count")) if verdict else None,
            "active_modules": sorted({label(value) for value in verdict.get("active_modules", [])}) if verdict else [],
            "unresolved_modules": sorted({label(value) for value in verdict.get("unresolved_modules", [])}) if verdict else [],
            "deferred_modules": sorted({label(value) for value in verdict.get("deferred_modules", [])}) if verdict else [],
            "unavailable_modules": sorted({label(value) for value in verdict.get("unavailable_modules", [])}) if verdict else [],
            "launcher_state": label(status.get("launcher", {}).get("state"), STATES),
            "sources_truncated": status.get("has_more_sources") is True,
            "heartbeat_sources": source_summaries,
        })
        if status.get("has_more_sources") is True:
            api.issues.add("heartbeat_sources_truncated")

    final = api.get("overview", "/api/dashboard/overview?limit=200") or {}
    indexed_count = count(final.get("counts", {}).get("events"))
    count_matches = indexed_count == len(rows)
    if not count_matches:
        api.issues.add("event_count_changed_or_incomplete")
    if final.get("index", {}).get("catching_up") is not False:
        api.issues.add("dashboard_index_incomplete")
    checks = {
        "events_pagination_complete": events_complete,
        "session_pagination_complete": sessions_complete,
        "list_detail_payloads_match": detail_matches == len(rows),
        "indexed_event_count_matches_enumeration": count_matches,
        "launcher_heartbeat_observed": launcher_sources > 0,
    }
    observed = bool(rows or launcher_sources)
    consistent = bool(observed and not api.issues and all(checks.values()))
    return {
        "schema_version": SCHEMA, "result": "OBSERVED" if observed else "NO_OBSERVATIONS",
        "api_consistency": "VERIFIED" if consistent else "INCOMPLETE",
        "provenance": "API observation; game and producer identity require separate Launcher evidence",
        "seeded_events": 0, "run_id": http.run_id,
        "event_count": len(rows), "event_detail_checked_count": detail_matches,
        "event_watermark": watermark,
        "session_count": len({session for session, _ in pairs}),
        "subject_count": len(pairs), "module_event_counts": dict(sorted(module_counts.items())),
        "submodule_event_counts": dict(sorted(submodule_counts.items())),
        "event_status_counts": dict(sorted(event_states.items())),
        "event_kind_counts": dict(sorted(event_kinds.items())),
        "verdict_status_counts": dict(sorted(verdict_counts.items())),
        "connection_states": {key: label(final.get("connection", {}).get(key, {}).get("state"), STATES)
                              for key in ("Receiver", "Scoring", "Launcher")},
        "checks": checks, "issues": sorted(api.issues), "requests": dict(sorted(api.requests.items())),
        "subjects": subjects, "payloads_retained": False, "private_stores_retained": False,
    }


def write_json(path: Path, value: dict) -> None:
    # The output directory belongs to this invocation; replace only its own file.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def serve(port: int, duration: int, output: str | Path) -> dict:
    port = checked_port(port)
    if type(duration) is not int or not 30 <= duration <= 3600:
        raise ValueError("duration must be an integer in 30..3600 seconds")
    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("output must be a new or empty directory")
    run_id = checked_run_id(uuid.uuid4().hex[:12])
    setup_path, summary_path, stop_path = (output / name for name in (
        "setup.json", "summary.json", "stop.request"))
    reason, summary = "duration_elapsed", None
    with owned_server(port, run_id) as (http, _private_root):
        output.mkdir(parents=True, exist_ok=True)
        setup = {
            "schema_version": SCHEMA, "result": "READY", "endpoint": http.url,
            "port": port, "run_id": run_id, "duration_seconds": duration,
            "test_tokens": http.tokens, "tokens_are_disposable_test_literals": True,
            "empty_start_verified": True, "seeded_events": 0,
            "setup_path": str(setup_path), "summary_path": str(summary_path),
            "stop_request_path": str(stop_path),
        }
        write_json(setup_path, setup)
        print(json.dumps(setup, indent=2), flush=True)
        stop_at = time.monotonic() + duration
        health_at = time.monotonic() + 5
        try:
            while time.monotonic() < stop_at:
                if stop_path.exists():
                    reason = "stop_requested"
                    break
                if time.monotonic() >= health_at:
                    try:
                        # Read only; do not refresh or fabricate Launcher heartbeats.
                        http.request("/api/dashboard/overview?limit=1")
                    except Exception:
                        reason = "owned_api_unavailable"
                        break
                    health_at = time.monotonic() + 5
                time.sleep(0.2)
        except KeyboardInterrupt:
            reason = "interrupted"
        # Capture before owned_server removes its fresh stores. No direct DB reads.
        summary = collect_summary(http)
        summary.update({"stop_reason": reason, "owned_listener_stopped": False,
                        "setup_path": str(setup_path), "summary_path": str(summary_path)})
        write_json(summary_path, summary)
    summary["owned_listener_stopped"] = True
    write_json(summary_path, summary)
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("serve", help="start an empty owned loopback API for actual Launcher input")
    start.add_argument("--port", type=int, default=8002)
    start.add_argument("--duration", type=int, default=1800)
    start.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        summary = serve(args.port, args.duration, args.output)
        return 0 if summary["api_consistency"] == "VERIFIED" else 2
    except KeyboardInterrupt:
        print("Real-game API helper interrupted; owned cleanup completed.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Real-game API helper failed ({type(error).__name__}); no response body, payload or inherited settings printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
