"""Dashboard projections preserve raw data and never create final verdicts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import asdict
from datetime import datetime, timezone

from server.scoring.player_snapshot import build_player_policy_snapshot
from .central_client import CentralQueryError
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

    def overview(self, *, after_session="", limit=100):
        sync = self.index.sync()
        heartbeat_sessions = self.heartbeat_store.session_inventory(after_session=after_session, limit=limit + 1) if self.heartbeat_store else []
        overview = self.index.overview(after_session=after_session, limit=limit, heartbeat_sessions=heartbeat_sessions)
        for assessment in overview["assessments"]:
            assessment.update(self.assessment(assessment["session_id"], assessment["player_id"]))
        return {
            "schema_version": "dashboard-v0", "generated_at_utc": self.clock().isoformat(),
            "capabilities": {"final_assessment": self.verdict_provider is not None, "launcher_heartbeat": False, "evidence_images": False, "heartbeat_query": self.heartbeat_store is not None},
            # Registering a router is not proof of component health.
            "connection": {name: {"state": "unknown"} for name in ("Receiver", "Scoring", "Launcher")},
            **overview,
            "events": [], "module_statuses": [], "events_endpoint": "/api/dashboard/events", "index": sync,
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
        return {"session_id": session_id, "player_id": player_id, **self.assessment(session_id, player_id), "modules": [asdict(state) for state in states], "policy": asdict(policy)}

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
            return {"session_id": session_id, "player_id": player_id, "state": "unknown", "sources": [], "reason": "heartbeat storage not connected"}
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
            sources.append({"client_id": payload["client_id"], "sequence": record["sequence"], "received_at_utc": record["received_at_utc"], "age_ms": age, "state": state, "reported_status": reported, "components": components, "transport": payload["transport"]})
        return {"session_id": session_id, "player_id": player_id, "state": "unknown", "sources": sources, "has_more_sources": len(latest) > 100, "reason": "source scopes are not yet agreed; no Launcher-wide aggregation"}
