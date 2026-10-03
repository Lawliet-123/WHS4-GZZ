from __future__ import annotations

import unittest

from server.scoring.player_snapshot import (
    ModulePolicySnapshot,
    PlayerPolicySnapshot,
)
from server.scoring.policies.contract import (
    PolicyAnnotations,
    PolicyEvaluation,
)
from server.scoring.policy import SignalPreview
from server.scoring.risk_input import build_player_risk_input
from server.scoring.storage import ModuleState


def entry(
    module: str,
    *,
    raw_score: float,
    emission: str,
    policy_state: str,
) -> ModulePolicySnapshot:
    state = ModuleState(
        session_id="s",
        player_id="p",
        module=module,
        timestamp_ms=1000,
        sequence=1,
        event_id="00000000-0000-0000-0000-000000000001",
        raw_score=raw_score,
        evidence={},
        reasons=[],
    )

    evaluation = PolicyEvaluation(
        signal=SignalPreview(
            module,
            float(raw_score),
            emission,
            policy_state,
            None,
            (),
        ),
        annotations=PolicyAnnotations(
            entity_key=None,
            overlap_tags=(),
            notes=(),
        ),
    )

    return ModulePolicySnapshot(
        state=state,
        evaluation=evaluation,
    )


def build(item: ModulePolicySnapshot):
    return build_player_risk_input(
        PlayerPolicySnapshot(
            session_id="s",
            player_id="p",
            modules=(item,),
            correlation_candidates=(),
        )
    )


class RiskCalibrationIntegrationTests(unittest.TestCase):
    def test_aimbot_threshold_is_attached(self):
        result = build(
            entry(
                "aimbot",
                raw_score=4,
                emission="snapshot",
                policy_state="RAW_FRACTION_ONLY",
            )
        )

        signal = result.signals[0]

        self.assertEqual(signal.calibration_version, "replay-v1")
        self.assertEqual(signal.calibration_mode, "threshold")
        self.assertEqual(signal.calibration_threshold, 4.0)
        self.assertTrue(signal.threshold_met)
        self.assertEqual(result.unresolved_policy_modules, ())

    def test_aimbot_below_threshold_is_resolved_negative(self):
        result = build(
            entry(
                "aimbot",
                raw_score=3,
                emission="snapshot",
                policy_state="RAW_FRACTION_ONLY",
            )
        )

        signal = result.signals[0]

        self.assertFalse(signal.threshold_met)
        self.assertEqual(result.unresolved_policy_modules, ())

    def test_godmode_calibration_resolves_policy_but_keeps_history_requirement(self):
        result = build(
            entry(
                "godmode",
                raw_score=2,
                emission="event_delta",
                policy_state="POLICY_NOT_CALIBRATED",
            )
        )

        signal = result.signals[0]

        self.assertEqual(signal.calibration_mode, "event_threshold")
        self.assertEqual(signal.calibration_threshold, 2.0)
        self.assertTrue(signal.threshold_met)

        self.assertEqual(result.unresolved_policy_modules, ())
        self.assertEqual(result.event_history_modules, ("godmode",))
        self.assertTrue(signal.requires_event_history)

    def test_injection_replay_threshold_is_applied(self):
        below = build(
            entry(
                "injection",
                raw_score=39,
                emission="snapshot",
                policy_state="POLICY_NOT_CALIBRATED",
            )
        )

        at_threshold = build(
            entry(
                "injection",
                raw_score=40,
                emission="snapshot",
                policy_state="POLICY_NOT_CALIBRATED",
            )
        )

        self.assertFalse(below.signals[0].threshold_met)
        self.assertTrue(at_threshold.signals[0].threshold_met)

        self.assertEqual(
            below.unresolved_policy_modules,
            (),
        )

    def test_filesystem_is_resolved_as_advisory_not_positive_threshold(self):
        result = build(
            entry(
                "filesystem",
                raw_score=100,
                emission="snapshot",
                policy_state="POLICY_NOT_CALIBRATED",
            )
        )

        signal = result.signals[0]

        self.assertEqual(signal.calibration_mode, "advisory")
        self.assertIsNone(signal.calibration_threshold)
        self.assertIsNone(signal.threshold_met)

        # 알려진 advisory라는 의미가 확정됐으므로 unresolved는 아니다.
        self.assertEqual(result.unresolved_policy_modules, ())

    def test_noclip_remains_pending(self):
        result = build(
            entry(
                "noclip",
                raw_score=3,
                emission="snapshot",
                policy_state="RAW_FRACTION_ONLY",
            )
        )

        signal = result.signals[0]

        self.assertEqual(signal.calibration_version, "replay-v1")
        self.assertEqual(signal.calibration_mode, "pending")
        self.assertIsNone(signal.threshold_met)

        self.assertEqual(
            result.unresolved_policy_modules,
            ("noclip",),
        )

    def test_out_of_audited_range_cannot_be_resolved_by_threshold(self):
        result = build(
            entry(
                "aimbot",
                raw_score=9,
                emission="snapshot",
                policy_state="OUT_OF_AUDITED_RANGE",
            )
        )

        signal = result.signals[0]

        self.assertIsNone(signal.threshold_met)
        self.assertEqual(
            result.unresolved_policy_modules,
            ("aimbot",),
        )

    def test_measurement_failure_is_not_clean_threshold_result(self):
        result = build(
            entry(
                "injection",
                raw_score=0,
                emission="snapshot",
                policy_state="MEASUREMENT_UNAVAILABLE",
            )
        )

        signal = result.signals[0]

        self.assertFalse(signal.measurement_available)
        self.assertIsNone(signal.threshold_met)

        self.assertEqual(
            result.measurement_unavailable_modules,
            ("injection",),
        )


if __name__ == "__main__":
    unittest.main()
