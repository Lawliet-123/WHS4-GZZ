"""Dashboard projections preserve raw data and never create final verdicts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

from server.scoring.player_snapshot import build_player_policy_snapshot
from shared.schema import validate_identifier
from .central_client import CentralQueryError
from .evidence_projection import build_evidence_projection
from .index import DashboardIndex


class DashboardService:
    def __init__(self, *, writer, scoring, index_path, cursor_secret: str, heartbeat_store=None, verdict_provider=None, stale_after_ms: int = 30000, clock=None, sync_budget: int = 2000):
        if not cursor_secret or stale_after_ms <= 0 or sync_budget < 1:
            raise ValueError("invalid Dashboard configuration")
        self.index = DashboardIndex(index_path, writer, sync_budget=sync_budget)
        self.scoring = scoring
        self.heartbeat_store = heartbeat_store
        self.verdict_provider = verdict_provider
        self.secret = cursor_secret.encode()
        self.stale_after_ms = stale_after_ms
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _encode_cursor(self, value):
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self.secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(signature + payload).decode()

    def _decode_cursor(self, cursor, filters):
        try:
            raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.new(self.secret, payload, hashlib.sha256).digest()):
                raise ValueError()
            value = json.loads(payload)
            if value["filters"] != filters or type(value["after"]) is not int or type(value["through"]) is not int or not 0 <= value["after"] <= value["through"]:
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError):
            raise ValueError("invalid cursor or changed filters") from None

    def events(self, filters, *, cursor=None, limit=100, after_sequence=0):
        sync = self.index.sync()
        if cursor and after_sequence:
            raise ValueError("use cursor or after_sequence")
        if after_sequence > sync["through_sequence"]:
            raise ValueError("after_sequence exceeds current index watermark")
        position = self._decode_cursor(cursor, filters) if cursor else {"after": after_sequence, "through": sync["through_sequence"], "filters": filters}
        items, more = self.index.events(filters, after=position["after"], through=position["through"], limit=limit)
        next_cursor = self._encode_cursor({**position, "after": items[-1]["sequence"]}) if more else None
        return {"items": items, "next_cursor": next_cursor, "has_more": more, "through_sequence": position["through"], "index": sync}

    def overview(self, *, after_session="", limit=100, session_id=None, player_id=None):
        if (session_id is None) != (player_id is None):
            raise ValueError("session_id and player_id must be supplied together")
        if session_id is not None:
            validate_identifier(session_id)
            validate_identifier(player_id)
        sync = self.index.sync()
        heartbeat_sessions = self.heartbeat_store.session_inventory(after_session=after_session, limit=limit + 1) if self.heartbeat_store else []
        overview = self.index.overview(after_session=after_session, limit=limit, heartbeat_sessions=heartbeat_sessions)
        for assessment in overview["assessments"]:
            assessment.update(self.assessment(assessment["session_id"], assessment["player_id"]))
        pairs = [(session_id, player_id)] if session_id is not None else [
            (row["session_id"], row["player_id"]) for row in overview["assessments"]
        ]
        launcher_statuses, module_statuses, selfdefense_statuses = [], [], []
        for session, player in pairs:
            status = self.status(session, player)
            for item in self.index.selfdefense_statuses(session, player):
                selfdefense_statuses.append({
                    **item,
                    "session_id": session,
                    "player_id": player,
                })
            launcher = status["launcher"]
            launcher_statuses.append({"session_id": session, "player_id": player, **launcher})
            if launcher["source"] is not None:
                source = launcher["source"]
                for component in source["components"]:
                    module_statuses.append({
                        **component, "label": component["id"],
                        "status_id": f"{session}:{player}:{source['client_id']}:{component['id']}",
                        "session_id": session, "player_id": player,
                        "client_id": source["client_id"], "last_seen_at": source["received_at_utc"],
                    })
        launcher_connection = self._launcher_connection(launcher_statuses)
        launcher_connection["scope"] = "selected_session_player" if session_id is not None else "returned_session_page"
        checked_at = self.clock().isoformat()
        # Feed sync just verified Shared storage; this is local readiness,
        # not proof that every remote detector can reach POST /api/detection.
        receiver_connection = {"state": "online", "scope": "local_detection_storage", "checked_at_utc": checked_at}
        try:
            self.scoring.get_player_snapshot("dashboard_health_probe", "dashboard_health_probe")
            scoring_connection = {"state": "online", "scope": "local_scoring_read", "checked_at_utc": checked_at}
        except (RuntimeError, OSError, sqlite3.Error):
            scoring_connection = {"state": "unavailable", "scope": "local_scoring_read", "checked_at_utc": checked_at}
        return {
            "schema_version": "dashboard-v0", "generated_at_utc": self.clock().isoformat(),
            "capabilities": {"final_assessment": self.verdict_provider is not None, "launcher_heartbeat": self.heartbeat_store is not None, "evidence_images": False, "heartbeat_query": self.heartbeat_store is not None},
            "connection": {"Receiver": receiver_connection, "Scoring": scoring_connection, "Launcher": launcher_connection},
            **overview,
            "events": [],
            "module_statuses": module_statuses,
            "launcher_statuses": launcher_statuses,
            "selfdefense_statuses": selfdefense_statuses,
            "events_endpoint": "/api/dashboard/events",
            "index": sync,
        }

    def detail(self, event_id):
        sync = self.index.sync()
        value = self.index.detail(event_id)
        if value is None and sync["catching_up"]:
            raise RuntimeError("Dashboard index is catching up")
        return value

    def snapshot(self, session_id, player_id):
        states = self.scoring.get_player_snapshot(session_id, player_id)
        policy = build_player_policy_snapshot(states, session_id=session_id, player_id=player_id)
        explanation = build_evidence_projection(self.scoring, policy)
        return {"session_id": session_id, "player_id": player_id, **self.assessment(session_id, player_id), "modules": [asdict(state) for state in states], "policy": {**asdict(policy), **explanation}}

    def assessment(self, session_id, player_id):
        empty = {"status": "UNKNOWN", "score": None, "confidence": None, "assessment_available": False, "final_verdict": None, "reason_codes": [], "data_state": "not_connected"}
        if self.verdict_provider is None:
            return empty
        # Match C's 404 semantics, not B's empty-input NO_ACTIVE_EVIDENCE.
        if not self.scoring.get_player_snapshot(session_id, player_id):
            return {**empty, "data_state": "missing"}
        try:
            value = self.verdict_provider(session_id, player_id)
        except CentralQueryError as exc:
            if exc.status_code == 404:
                return {**empty, "data_state": "missing"}
            raise
        verdict = asdict(value) if hasattr(value, "__dataclass_fields__") else value
        if (not isinstance(verdict, dict) or verdict.get("status") not in ("SUSPICIOUS", "INCONCLUSIVE", "NO_ACTIVE_EVIDENCE")
                or verdict.get("session_id") != session_id or verdict.get("player_id") != player_id
                or not isinstance(verdict.get("reason_codes"), (list, tuple))):
            raise RuntimeError("invalid final verdict response")
        return {**empty, "status": verdict["status"], "assessment_available": True,
                "final_verdict": verdict, "reason_codes": verdict["reason_codes"], "data_state": "available"}

    def history(self, session_id, player_id, *, module, after_sequence, limit):
        items = self.scoring.get_event_delta_history(session_id, player_id, module=module, after_sequence=after_sequence, limit=limit + 1)
        more = len(items) > limit
        visible = items[:limit]
        return {"items": [asdict(item) for item in visible], "has_more": more, "next_after_sequence": visible[-1].sequence if more else None, "final_assessment": False}

    def status(self, session_id, player_id):
        if self.heartbeat_store is None:
            return {"session_id": session_id, "player_id": player_id, "state": "unknown", "sources": [], "reason": "heartbeat storage not connected",
                    "launcher": {"state": "unknown", "connected": False, "source": None, "reason": "heartbeat storage not connected"}}
        # 101 detects truncation instead of claiming a complete aggregated state.
        latest = self.heartbeat_store.list_latest(session_id, player_id, limit=101)
        sources = []
        now = self.clock()
        for record in latest[:100]:
            payload = record["payload"]
            age = max(0, int((now - datetime.fromisoformat(record["received_at_utc"])).total_seconds() * 1000))
            reported = payload["status"]
            state = "stopped" if reported == "stopped" else "stale" if age >= self.stale_after_ms else reported
            components = []
            for name, component in payload["components"].items():
                observed_age = component["age_ms"] + age
                effective = component["status"]
                if effective != "stopped" and (state == "stale" or observed_age >= component["stale_after_ms"]):
                    effective = "stale"
                components.append({"id": name, **component, "reported_status": component["status"], "state": effective, "effective_age_ms": observed_age})
            # Current Launcher contract: launcher-<t0 milliseconds> plus a
            # dedicated launcher component. A scanner is not session authority.
            is_launcher = bool(re.fullmatch(r"launcher-[0-9]+", payload["client_id"])) and "launcher" in payload["components"]
            sources.append({"client_id": payload["client_id"], "role": "launcher" if is_launcher else "component",
                            "sequence": record["sequence"], "received_at_utc": record["received_at_utc"], "age_ms": age,
                            "state": state, "reported_status": reported, "components": components, "transport": payload["transport"]})
        launcher = self._launcher_status(sources, complete=len(latest) <= 100)
        return {"session_id": session_id, "player_id": player_id, "state": launcher["state"], "sources": sources,
                "launcher": launcher, "has_more_sources": len(latest) > 100, "reason": launcher["reason"]}

    @staticmethod
    def _launcher_status(sources, *, complete):
        empty = {"state": "unknown", "connected": False, "source": None}
        if not complete:
            return {**empty, "reason": "source list truncated"}
        launchers = [source for source in sources if source["role"] == "launcher"]
        if not launchers:
            return {**empty, "reason": "no Launcher heartbeat"}
        # Choose the latest Launcher execution, not a delayed packet from an
        # older execution that happens to have a later server receipt time.
        source = max(launchers, key=lambda item: (int(item["client_id"].split("-", 1)[1]), item["received_at_utc"]))
        launcher = next(component for component in source["components"] if component["id"] == "launcher")
        state = source["state"]
        required = [component["state"] for component in source["components"] if component["required"]]
        if state not in ("stale", "stopped", "failed"):
            if launcher["state"] == "stale":
                state = "stale"
            elif launcher["state"] == "stopped":
                state = "stopped"
            elif any(value in ("failed", "degraded", "stale", "unknown") for value in required):
                state = "degraded"
            elif "starting" in required:
                state = "starting"
            elif launcher["details"].get("phase") == "stopping":
                state = "stopping"
        return {"state": state, "connected": state not in ("unknown", "stale", "stopped"),
                "source": source, "reason": "latest Launcher execution; server receipt and required component freshness"}

    @staticmethod
    def _launcher_connection(launchers):
        counts = Counter(item["state"] for item in launchers)
        states = set(counts)
        if not states or states == {"unknown"}:
            state = "unknown"
        elif len(states) == 1:
            value = next(iter(states))
            state = {"healthy": "online", "failed": "degraded"}.get(value, value)
        elif states <= {"stale", "stopped", "unknown"} and "stale" in states:
            state = "stale"
        else:
            state = "degraded"
        return {"state": state, "state_counts": dict(counts),
                "observed_pairs": len(launchers), "connected_pairs": sum(item["connected"] for item in launchers)}
