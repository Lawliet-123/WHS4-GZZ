from __future__ import annotations

import unittest

from server.scoring.aggregate import build_aggregate_evidence
from server.scoring.correlation import CorrelationCandidate
from server.scoring.risk_input import PlayerRiskInput, RiskSignalInput


def active_signal(module: str, event_id: str) -> RiskSignalInput:
    return RiskSignalInput(
        module=module,
        event_id=event_id,
        raw_score=100,
        emission="snapshot",
        policy_state="POLICY_NOT_CALIBRATED",
        raw_fraction_pct=None,
        measurement_available=True,
        requires_event_history=False,
        requires_entity_scope=False,
        entity_key=None,
        overlap_tags=("shared_tag",),
        issues=(),
        notes=(),
        calibration_version="replay-v1",
        calibration_mode="threshold",
        calibration_threshold=1,
        threshold_met=True,
    )


class AggregateOverlapIntegrationTests(unittest.TestCase):
    def test_active_correlation_becomes_overlap_group(self):
        candidate = CorrelationCandidate(
            session_id="session_a",
            player_id="player_a",
            modules=("autopaint", "injection"),
            event_ids=("event-a", "event-b"),
            sequences=(1, 2),
            overlap_tags=("process_injection",),
            entity_keys=(None, None),
            time_distance_ms=100,
            reasons=("candidate",),
        )

        risk_input = PlayerRiskInput(
            session_id="session_a",
            player_id="player_a",
            signals=(
                active_signal("autopaint", "event-a"),
                active_signal("injection", "event-b"),
            ),
            measurement_unavailable_modules=(),
            unresolved_policy_modules=(),
            event_history_modules=(),
            entity_scoped_modules=(),
            correlation_candidates=(candidate,),
        )

        result = build_aggregate_evidence(risk_input)

        self.assertEqual(
            result.active_modules,
            ("autopaint", "injection"),
        )

        self.assertEqual(len(result.overlap_groups), 1)

        group = result.overlap_groups[0]

        self.assertEqual(
            group.modules,
            ("autopaint", "injection"),
        )

        self.assertEqual(
            group.overlap_tags,
            ("process_injection",),
        )

    def test_inactive_signal_does_not_enter_overlap_group(self):
        candidate = CorrelationCandidate(
            session_id="session_a",
            player_id="player_a",
            modules=("autopaint", "injection"),
            event_ids=("event-a", "event-b"),
            sequences=(1, 2),
            overlap_tags=("process_injection",),
            entity_keys=(None, None),
            time_distance_ms=100,
            reasons=("candidate",),
        )

        inactive = active_signal("injection", "event-b")

        inactive = RiskSignalInput(
            **{
                **inactive.__dict__,
                "threshold_met": False,
            }
        )

        risk_input = PlayerRiskInput(
            session_id="session_a",
            player_id="player_a",
            signals=(
                active_signal("autopaint", "event-a"),
                inactive,
            ),
            measurement_unavailable_modules=(),
            unresolved_policy_modules=(),
            event_history_modules=(),
            entity_scoped_modules=(),
            correlation_candidates=(candidate,),
        )

        result = build_aggregate_evidence(risk_input)

        self.assertEqual(
            result.active_modules,
            ("autopaint",),
        )

        self.assertEqual(
            result.overlap_groups,
            (),
        )


if __name__ == "__main__":
    unittest.main()
