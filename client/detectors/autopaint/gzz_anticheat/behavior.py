"""Event-time AutoPaint rules. Observation only; no gameplay calls or enforcement."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Mapping


RULE_VERSION = "autopaint-behavior-1"
REQUIRED_HOOKS = {"PaintAtUVWithBrush", "PaintAtScreenPosition", "BeginStroke", "EndStroke"}


@dataclass(frozen=True)
class BehaviorConfig:
    window_ms: int = 2000
    settle_ms: int = 200
    screen_match_ms: int = 150
    minimum_uv_calls: int = 8
    minimum_batch_calls: int = 2
    minimum_batch_strokes: int = 16
    stale_ms: int = 3000
    clock_tolerance_ms: int = 10
    maximum_records: int = 16000
    maximum_objects: int = 64
    threshold: int = 10


@dataclass(frozen=True)
class Observation:
    timestamp_ms: int
    target: str
    function: str
    mode_off: bool
    stroke_open: bool
    batch_size: int


def integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class PaintBehaviorDetector:
    """Bounded per-session state, shared by live collection and offline replay.

    Labels, ON/OFF annotations, role claims and DLL evidence are never inputs.
    Scores describe an observed window, not a probability or a permanent verdict.
    """

    def __init__(self, session_id: str, player_id: str, *, token: str | None = None):
        self.config = BehaviorConfig()
        self.session_id, self.player_id, self.expected_token = session_id, player_id, token
        self.active_token: str | None = None
        self.sequence = 0
        self.last_record_ms: int | None = None
        self.last_health_ms: int | None = None
        self.coverage_start_ms: int | None = None
        self.health_good = False
        self.active = False
        self.identity: tuple[str, str] | None = None
        self.observations: deque[Observation] = deque()
        self.strokes: dict[str, bool] = {}
        self.counters: tuple[int, ...] | None = None
        self.transport_counters = (0, 0)
        self.issue = "MISSING"
        self.rejected_records = 0
        self.resets = 0
        self.ever_detected = False
        self.first_detected_ms: int | None = None

    def policy(self) -> dict:
        return {"version": RULE_VERSION, **asdict(self.config),
                "weights": {"repeated_uv": 6, "unmatched_uv": 4, "mode_off": 3, "batch": 2}}

    def _invalidate(self, reason: str) -> None:
        self.observations.clear()
        self.strokes.clear()
        self.coverage_start_ms = None
        self.issue = reason
        self.resets += 1

    def transport(self, *, parse_errors: int, stream_resets: int, backlogged: bool) -> None:
        counters = (parse_errors, stream_resets)
        if counters != self.transport_counters:
            self._invalidate("TRANSPORT_GAP")
        self.transport_counters = counters
        if backlogged:
            self._invalidate("TRANSPORT_BACKLOG")

    def _trim(self, now_ms: int) -> None:
        oldest = (now_ms - self.config.window_ms - self.config.settle_ms
                  - self.config.screen_match_ms - self.config.clock_tolerance_ms)
        while self.observations and self.observations[0].timestamp_ms < oldest:
            self.observations.popleft()

    def consume(self, record: Mapping[str, Any]) -> None:
        timestamp, seq = record.get("timestamp_ms"), record.get("sequence")
        token, kind = record.get("session_token"), record.get("kind")
        if (record.get("session_id") != self.session_id or record.get("player_id") != self.player_id
                or not isinstance(token, str) or not token
                or (self.expected_token is not None and token != self.expected_token)):
            self.rejected_records += 1
            self._invalidate("SESSION_MISMATCH")
            return
        if (not integer(timestamp) or timestamp < 0 or not integer(seq) or seq < 1
                or not integer(record.get("clock_resolution_ms")) or record.get("clock_resolution_ms") != 1
                or record.get("clock_timestamp_clamped")):
            self.rejected_records += 1
            self._invalidate("INVALID_RECORD_CLOCK")
            return
        if kind == "collector_start":
            self._invalidate("WARMING_UP")
            self.active, self.active_token = True, token
            self.sequence, self.last_record_ms = seq, timestamp
            self.last_health_ms, self.identity, self.counters = None, None, None
            self.health_good = False
            return
        if not self.active or token != self.active_token:
            self.rejected_records += 1
            self._invalidate("MISSING_COLLECTOR_START")
            return
        if self.last_record_ms is not None and timestamp < self.last_record_ms:
            self.rejected_records += 1
            self._invalidate("CLOCK_OR_RECORD_ORDER")
            self.sequence = seq
            return
        if seq != self.sequence + 1:
            self._invalidate("SEQUENCE_GAP")
            if seq <= self.sequence:
                self.rejected_records += 1
                return
        self.sequence, self.last_record_ms = seq, timestamp
        self._trim(timestamp)
        if self.last_health_ms is not None and timestamp - self.last_health_ms > self.config.stale_ms:
            self._invalidate("STALE_HEALTH")
            self.health_good = False
        if kind == "collector_stop":
            self.active = False
            self._invalidate("STOPPED")
            return
        if kind == "health":
            hooks = record.get("hooks")
            ready = {h.get("function_name") for h in hooks if isinstance(h, dict) and isinstance(h.get("function_name"), str)
                     and h.get("registered") is True and h.get("available") is True} if isinstance(hooks, list) else set()
            counters = tuple(record.get(k) for k in ("dropped", "write_errors", "callback_errors"))
            if not all(integer(n) and n >= 0 for n in counters):
                self.health_good = False
                self._invalidate("INVALID_HEALTH")
                return
            if self.counters is not None and counters != self.counters:
                self._invalidate("COLLECTOR_DATA_LOSS")
            self.counters = counters
            network = record.get("network")
            world = network.get("world") if isinstance(network, dict) else None
            pawn = record.get("local_pawn")
            identity = (world, pawn) if isinstance(world, str) and world and isinstance(pawn, str) and pawn else None
            if identity != self.identity:
                self._invalidate("LOCAL_IDENTITY_CHANGED")
                self.identity = identity
            self.last_health_ms = timestamp
            self.health_good = REQUIRED_HOOKS.issubset(ready) and identity is not None
            if not self.health_good:
                self._invalidate("REQUIRED_HOOKS_OR_LOCAL_PAWN_MISSING")
            elif self.coverage_start_ms is None:
                self.coverage_start_ms = timestamp
            return
        if kind != "call" or not self.health_good or self.coverage_start_ms is None:
            return
        function = record.get("function_name")
        if not isinstance(function, str):
            self._invalidate("INVALID_CALL_SCHEMA")
            return
        if function not in REQUIRED_HOOKS | {"ServerPaintBatch"}:
            return
        context = record.get("context")
        if not isinstance(context, dict) or not self.identity:
            self._invalidate("LOCAL_CONTEXT_UNRESOLVED")
            return
        # Do not infer a local actor from player_id, a remote object's owner, or a relay.
        if context.get("owner_is_local_pawn") is False:
            return
        if (context.get("object_valid") is not True or context.get("owner_is_local_pawn") is not True
                or context.get("owner_locally_controlled") is not True
                or (context.get("world"), context.get("local_pawn")) != self.identity
                or context.get("owner") != self.identity[1]):
            self._invalidate("LOCAL_CONTEXT_UNRESOLVED")
            return
        target = context.get("object")
        if not isinstance(target, str) or not target:
            self._invalidate("LOCAL_CONTEXT_UNRESOLVED")
            return
        if target not in self.strokes and len(self.strokes) >= self.config.maximum_objects:
            self._invalidate("OBJECT_LIMIT")
            return
        if function == "BeginStroke":
            self.strokes[target] = True
        elif function == "EndStroke":
            self.strokes[target] = False
        else:
            self.strokes.setdefault(target, False)
        parameters = record.get("parameters")
        size = parameters.get("stroke_count", 0) if isinstance(parameters, dict) else 0
        size = size if integer(size) and size >= 0 else 0
        if len(self.observations) >= self.config.maximum_records:
            self._invalidate("WINDOW_RECORD_LIMIT")
            return
        self.observations.append(Observation(timestamp, target, function,
                                             context.get("local_is_paint_mode") is False,
                                             self.strokes[target], size))

    def evaluate(self, now_ms: int, *, clock_valid: bool = True) -> dict:
        self._trim(now_ms)
        end = now_ms - self.config.settle_ms
        start = end - self.config.window_ms
        state = "READY"
        if not clock_valid:
            self._invalidate("CLOCK_ALIGNMENT_INVALID")
            state = self.issue
        elif not self.active:
            state = self.issue
        elif self.last_record_ms is not None and self.last_record_ms > now_ms + self.config.clock_tolerance_ms:
            self._invalidate("FUTURE_RECORD")
            state = self.issue
        elif self.last_health_ms is None or not self.health_good:
            state = self.issue
        elif now_ms - self.last_health_ms > self.config.stale_ms:
            self._invalidate("STALE_HEALTH")
            self.health_good = False
            state = self.issue
        elif self.coverage_start_ms is None:
            state = self.issue
        elif self.coverage_start_ms > start - self.config.screen_match_ms:
            state = "WARMING_UP"
        valid = state == "READY"
        score, reasons, selected_target = 0, [], None
        features = {"behavior_uv_calls": 0, "behavior_unmatched_uv_calls": 0,
                    "behavior_mode_off_uv_calls": 0, "behavior_batch_calls": 0,
                    "behavior_batch_strokes": 0, "behavior_uv_during_stroke": 0}
        if valid:
            targets: dict[str, list[Observation]] = {}
            for item in self.observations:
                targets.setdefault(item.target, []).append(item)
            for target, items in targets.items():
                uv = [r for r in items if start <= r.timestamp_ms <= end and r.function == "PaintAtUVWithBrush"]
                screens = [r.timestamp_ms for r in items if r.function == "PaintAtScreenPosition"]
                # Sorted event times permit a linear nearest-screen match, not a quadratic scan.
                unmatched, cursor = 0, 0
                for r in uv:
                    while cursor < len(screens) and screens[cursor] < r.timestamp_ms - self.config.screen_match_ms:
                        cursor += 1
                    if cursor == len(screens) or screens[cursor] > r.timestamp_ms + self.config.screen_match_ms:
                        unmatched += 1
                batches = [r for r in items if start <= r.timestamp_ms <= end and r.function == "ServerPaintBatch"]
                mode_off = sum(r.mode_off for r in uv)
                current_score, current_reasons = 0, []
                if len(uv) >= self.config.minimum_uv_calls:
                    current_score = 6
                    current_reasons.append("Behavior: Repeated Local UV Painting")
                    if unmatched >= self.config.minimum_uv_calls:
                        current_score += 4
                        current_reasons.append("Behavior: UV Painting Without Matching Screen Calls")
                    if mode_off >= self.config.minimum_uv_calls:
                        current_score += 3
                        current_reasons.append("Behavior: Local UV Painting Outside Paint Mode")
                    if len(batches) >= self.config.minimum_batch_calls and sum(r.batch_size for r in batches) >= self.config.minimum_batch_strokes:
                        current_score += 2
                        current_reasons.append("Behavior: Correlated Local Batch Painting")
                if current_score > score or (current_score == score and len(uv) > features["behavior_uv_calls"]):
                    score, reasons, selected_target = current_score, current_reasons, target
                    features = {"behavior_uv_calls": len(uv), "behavior_unmatched_uv_calls": unmatched,
                                "behavior_mode_off_uv_calls": mode_off, "behavior_batch_calls": len(batches),
                                "behavior_batch_strokes": sum(r.batch_size for r in batches),
                                "behavior_uv_during_stroke": sum(r.stroke_open for r in uv)}
        detected = valid and score >= self.config.threshold
        if detected:
            self.ever_detected = True
            if self.first_detected_ms is None:
                self.first_detected_ms = now_ms
        evidence = {"behavioral_scoring_enabled": 1, "behavior_valid": int(valid),
                    "behavior_score": score, "behavior_detected": int(detected),
                    "behavior_threshold": self.config.threshold, "behavior_rule_version": 1,
                    "behavior_window_ms": self.config.window_ms, **features}
        return {"rule_version": RULE_VERSION, "timestamp_ms": now_ms, "valid": valid,
                "state": state, "score": score, "detected": detected,
                "window_start_ms": max(0, start), "window_end_ms": max(0, end),
                "target": selected_target, "reasons": reasons, "evidence": evidence,
                "ever_detected": self.ever_detected, "first_detected_ms": self.first_detected_ms,
                "rejected_records": self.rejected_records, "resets": self.resets}
