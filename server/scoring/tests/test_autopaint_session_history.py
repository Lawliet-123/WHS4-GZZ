from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.scoring.aggregate import build_aggregate_evidence
from server.scoring.aggregate_risk import build_aggregate_risk
from server.scoring.autopaint_history import summarize_autopaint_history
from server.scoring.final_verdict import build_final_verdict
from server.scoring.fusion import build_fusion_plan
from server.scoring.risk_input import PlayerRiskInput, RiskSignalInput
from server.scoring.storage import ScoringStore, SnapshotEvent


def snapshot(
    *,
    event_id: str,
    sequence: int,
    timestamp_ms: int,
    raw_score: float,
    valid: bool = True,
) -> SnapshotEvent:
    return SnapshotEvent(
        event_id=event_id,
        sequence=sequence,
        session_id="session_1",
        player_id="player_1",
        module="autopaint",
        timestamp_ms=timestamp_ms,
        raw_score=raw_score,
        evidence={
            "status": "DETECTED" if raw_score >= 23 else "NORMAL",
            "integrity_valid": 1 if valid else 0,
            "behavior_valid": 1 if valid else 0,
        },
        reasons=[],
    )


def current_signal(raw_score: float) -> RiskSignalInput:
    return RiskSignalInput(
        module="autopaint",
        event_id="00000000-0000-0000-0000-000000000003",
        raw_score=raw_score,
        emission="snapshot",
        policy_state="RAW_FRACTION_ONLY",
        raw_fraction_pct=None,
        measurement_available=True,
        requires_event_history=False,
        requires_entity_scope=False,
        entity_key=None,
        overlap_tags=(),
        issues=(),
        notes=(),
        calibration_version="replay-v1",
        calibration_mode="threshold",
        calibration_threshold=23.0,
        threshold_met=raw_score >= 23,
    )


def player(raw_score: float) -> PlayerRiskInput:
    return PlayerRiskInput(
        session_id="session_1",
        player_id="player_1",
        signals=(current_signal(raw_score),),
        measurement_unavailable_modules=(),
        unresolved_policy_modules=(),
        event_history_modules=(),
        entity_scoped_modules=(),
        correlation_candidates=(),
    )


class AutoPaintSessionHistoryTests(unittest.TestCase):
    def test_summary_keeps_session_positive_after_latest_score_drops(self):
        summary = summarize_autopaint_history(
            (
                snapshot(
                    event_id="event-1",
                    sequence=1,
                    timestamp_ms=1000,
                    raw_score=0,
                ),
                snapshot(
                    event_id="event-2",
                    sequence=2,
                    timestamp_ms=2000,
                    raw_score=32,
                ),
                snapshot(
                    event_id="event-3",
                    sequence=3,
                    timestamp_ms=3000,
                    raw_score=21,
                ),
            ),
            session_id="session_1",
            player_id="player_1",
        )

        self.assertEqual(summary.max_raw_score, 32)
        self.assertEqual(summary.latest_raw_score, 21)
        self.assertEqual(summary.qualifying_events, 1)
        self.assertTrue(summary.positive_seen)
        self.assertFalse(summary.latest_threshold_met)

    def test_invalid_high_score_does_not_create_positive_history(self):
        summary = summarize_autopaint_history(
            (
                snapshot(
                    event_id="event-1",
                    sequence=1,
                    timestamp_ms=1000,
                    raw_score=32,
                    valid=False,
                ),
                snapshot(
                    event_id="event-2",
                    sequence=2,
                    timestamp_ms=2000,
                    raw_score=21,
                ),
            ),
            session_id="session_1",
            player_id="player_1",
        )

        self.assertEqual(summary.available_events, 1)
        self.assertEqual(summary.max_raw_score, 21)
        self.assertEqual(summary.qualifying_events, 0)
        self.assertFalse(summary.positive_seen)

    def test_aggregate_session_active_but_latest_snapshot_inactive(self):
        summary = summarize_autopaint_history(
            (
                snapshot(
                    event_id="event-1",
                    sequence=1,
                    timestamp_ms=1000,
                    raw_score=32,
                ),
                snapshot(
                    event_id="event-2",
                    sequence=2,
                    timestamp_ms=2000,
                    raw_score=21,
                ),
            ),
            session_id="session_1",
            player_id="player_1",
        )

        evidence = build_aggregate_evidence(
            player(21),
            autopaint_history=summary,
        )

        self.assertEqual(evidence.active_modules, ("autopaint",))
        self.assertEqual(evidence.inactive_modules, ())

        signal = evidence.signals[0]

        # 세션 판정은 과거 확정 positive 때문에 ACTIVE.
        self.assertEqual(signal.status, "ACTIVE")
        self.assertTrue(signal.history_resolved)
        self.assertEqual(signal.history_max_raw_score, 32)
        self.assertEqual(signal.history_qualifying_events, 1)

        # 현재 순간은 latest snapshot 그대로 21 < 23.
        self.assertEqual(signal.raw_score, 21)
        self.assertFalse(signal.threshold_met)

        verdict = build_final_verdict(
            build_aggregate_risk(
                build_fusion_plan(evidence)
            )
        )

        self.assertEqual(verdict.status, "SUSPICIOUS")
        self.assertEqual(verdict.active_modules, ("autopaint",))

    def test_no_session_positive_keeps_latest_inactive(self):
        summary = summarize_autopaint_history(
            (
                snapshot(
                    event_id="event-1",
                    sequence=1,
                    timestamp_ms=1000,
                    raw_score=10,
                ),
                snapshot(
                    event_id="event-2",
                    sequence=2,
                    timestamp_ms=2000,
                    raw_score=21,
                ),
            ),
            session_id="session_1",
            player_id="player_1",
        )

        evidence = build_aggregate_evidence(
            player(21),
            autopaint_history=summary,
        )

        self.assertEqual(evidence.active_modules, ())
        self.assertEqual(evidence.inactive_modules, ("autopaint",))

    def test_storage_keeps_multiple_autopaint_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScoringStore(Path(tmp) / "scoring.sqlite3")

            def payload(timestamp_ms: int, raw_score: float):
                return {
                    "session_id": "session_1",
                    "player_id": "player_1",
                    "module": "autopaint",
                    "timestamp_ms": timestamp_ms,
                    "evidence": {
                        "status": (
                            "DETECTED"
                            if raw_score >= 23
                            else "NORMAL"
                        ),
                        "integrity_valid": 1,
                        "behavior_valid": 1,
                    },
                    "reasons": [],
                    "raw_score": raw_score,
                }

            store.process_event(
                payload(1000, 32),
                event_id="00000000-0000-0000-0000-000000000001",
                sequence=1,
            )
            store.process_event(
                payload(2000, 21),
                event_id="00000000-0000-0000-0000-000000000002",
                sequence=2,
            )

            history = store.get_snapshot_history(
                "session_1",
                "player_1",
                module="autopaint",
            )

            self.assertEqual(
                [event.raw_score for event in history],
                [32, 21],
            )

            latest = store.get_module_state(
                "session_1",
                "player_1",
                "autopaint",
            )

            self.assertIsNotNone(latest)
            self.assertEqual(latest.raw_score, 21)


if __name__ == "__main__":
    unittest.main()
