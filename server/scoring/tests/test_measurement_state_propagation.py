from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from server.scoring.aggregate import build_aggregate_evidence
from server.scoring.aggregate_risk import build_aggregate_risk
from server.scoring.final_verdict import build_final_verdict
from server.scoring.fusion import build_fusion_plan
from server.scoring.main import get_player_final_verdict
from server.scoring.player_snapshot import PlayerPolicySnapshot
from server.scoring.risk_input import build_player_risk_input
from server.scoring.storage import ModuleState


def final_from_snapshot(snapshot: PlayerPolicySnapshot):
    risk_input = build_player_risk_input(snapshot)
    aggregate = build_aggregate_evidence(risk_input)
    plan = build_fusion_plan(aggregate)
    risk = build_aggregate_risk(plan)
    verdict = build_final_verdict(risk)
    return risk_input, aggregate, plan, risk, verdict


class MeasurementStatePropagationTests(unittest.TestCase):
    def test_missing_measurement_reaches_final_verdict(self):
        snapshot = PlayerPolicySnapshot(
            session_id="s",
            player_id="p",
            modules=(),
            correlation_candidates=(),
            missing_modules=("noclip_runtime",),
            stale_modules=(),
        )

        risk_input, aggregate, plan, risk, verdict = final_from_snapshot(snapshot)

        self.assertEqual(risk_input.missing_modules, ("noclip_runtime",))
        self.assertEqual(aggregate.missing_modules, ("noclip_runtime",))
        self.assertEqual(plan.missing_modules, ("noclip_runtime",))
        self.assertEqual(risk.missing_modules, ("noclip_runtime",))
        self.assertEqual(verdict.missing_modules, ("noclip_runtime",))

        self.assertFalse(verdict.assessment_complete)
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("MISSING_MEASUREMENT", verdict.reason_codes)

    def test_stale_measurement_reaches_final_verdict(self):
        snapshot = PlayerPolicySnapshot(
            session_id="s",
            player_id="p",
            modules=(),
            correlation_candidates=(),
            missing_modules=(),
            stale_modules=("noclip",),
        )

        risk_input, aggregate, plan, risk, verdict = final_from_snapshot(snapshot)

        self.assertEqual(risk_input.stale_modules, ("noclip",))
        self.assertEqual(aggregate.stale_modules, ("noclip",))
        self.assertEqual(plan.stale_modules, ("noclip",))
        self.assertEqual(risk.stale_modules, ("noclip",))
        self.assertEqual(verdict.stale_modules, ("noclip",))

        self.assertFalse(verdict.assessment_complete)
        self.assertEqual(verdict.status, "INCONCLUSIVE")
        self.assertIn("STALE_MEASUREMENT", verdict.reason_codes)

    def test_public_final_verdict_reports_missing_measurement(self):
        with patch(
            "server.scoring.main.get_player_snapshot",
            return_value=[],
        ):
            verdict = get_player_final_verdict(
                "s",
                "p",
                expected_modules=("noclip_runtime",),
            )

        self.assertEqual(verdict.missing_modules, ("noclip_runtime",))
        self.assertEqual(verdict.status, "INCONCLUSIVE")

    def test_public_final_verdict_reports_stale_measurement(self):
        sample = ModuleState(
            session_id="s",
            player_id="p",
            module="noclip",
            timestamp_ms=1000,
            sequence=1,
            event_id=str(uuid.uuid4()),
            raw_score=0,
            evidence={"status": "NORMAL"},
            reasons=[],
        )

        with patch(
            "server.scoring.main.get_player_snapshot",
            return_value=[sample],
        ):
            verdict = get_player_final_verdict(
                "s",
                "p",
                expected_modules=("noclip",),
                observed_at_ms=5000,
                max_age_ms=2000,
            )

        self.assertEqual(verdict.stale_modules, ("noclip",))
        self.assertEqual(verdict.status, "INCONCLUSIVE")


    def test_selfdefense_measurement_state_is_excluded_from_cheat_risk(self):
        snapshot = PlayerPolicySnapshot(
            session_id="s",
            player_id="p",
            modules=(),
            correlation_candidates=(),
            missing_modules=("selfdefense", "noclip_runtime"),
            stale_modules=("selfdefense", "noclip"),
        )

        risk_input = build_player_risk_input(snapshot)

        self.assertEqual(risk_input.missing_modules, ("noclip_runtime",))
        self.assertEqual(risk_input.stale_modules, ("noclip",))


if __name__ == "__main__":
    unittest.main()
