from __future__ import annotations

import unittest

from server.scoring.aggregate import build_aggregate_evidence
from server.scoring.risk_input import PlayerRiskInput, RiskSignalInput


def signal(
    module: str,
    *,
    threshold_met: bool | None,
    calibration_mode: str | None = "threshold",
    measurement_available: bool = True,
    requires_event_history: bool = False,
) -> RiskSignalInput:
    return RiskSignalInput(
        module=module,
        event_id="00000000-0000-0000-0000-000000000001",
        raw_score=0,
        emission="snapshot",
        policy_state="POLICY_NOT_CALIBRATED",
        raw_fraction_pct=None,
        measurement_available=measurement_available,
        requires_event_history=requires_event_history,
        requires_entity_scope=False,
        entity_key=None,
        overlap_tags=(),
        issues=(),
        notes=(),
        calibration_version="replay-v1",
        calibration_mode=calibration_mode,
        calibration_threshold=1,
        threshold_met=threshold_met,
    )


def player(
    *signals: RiskSignalInput,
    unresolved: tuple[str, ...] = (),
) -> PlayerRiskInput:
    return PlayerRiskInput(
        session_id="s",
        player_id="p",
        signals=tuple(signals),
        measurement_unavailable_modules=tuple(
            s.module for s in signals if not s.measurement_available
        ),
        unresolved_policy_modules=unresolved,
        event_history_modules=tuple(
            s.module for s in signals if s.requires_event_history
        ),
        entity_scoped_modules=(),
        correlation_candidates=(),
    )


class AggregateEvidenceTests(unittest.TestCase):
    def test_threshold_true_is_active(self):
        result = build_aggregate_evidence(
            player(signal("aimbot", threshold_met=True))
        )

        self.assertEqual(result.active_modules, ("aimbot",))
        self.assertEqual(result.signals[0].status, "ACTIVE")

    def test_threshold_false_is_inactive(self):
        result = build_aggregate_evidence(
            player(signal("aimbot", threshold_met=False))
        )

        self.assertEqual(result.inactive_modules, ("aimbot",))
        self.assertEqual(result.signals[0].status, "INACTIVE")

    def test_advisory_is_not_active(self):
        result = build_aggregate_evidence(
            player(
                signal(
                    "filesystem",
                    threshold_met=None,
                    calibration_mode="advisory",
                )
            )
        )

        self.assertEqual(result.advisory_modules, ("filesystem",))
        self.assertEqual(result.active_modules, ())

    def test_pending_module_is_unresolved(self):
        item = signal(
            "esp",
            threshold_met=None,
            calibration_mode="pending",
        )

        result = build_aggregate_evidence(
            player(item, unresolved=("esp",))
        )

        self.assertEqual(result.unresolved_modules, ("esp",))

    def test_event_history_module_is_deferred(self):
        item = signal(
            "godmode",
            threshold_met=True,
            calibration_mode="event_threshold",
            requires_event_history=True,
        )

        result = build_aggregate_evidence(player(item))

        self.assertEqual(result.deferred_modules, ("godmode",))
        self.assertEqual(result.active_modules, ())

    def test_unavailable_has_highest_priority(self):
        item = signal(
            "injection",
            threshold_met=None,
            measurement_available=False,
        )

        result = build_aggregate_evidence(player(item))

        self.assertEqual(result.unavailable_modules, ("injection",))
        self.assertEqual(result.active_modules, ())
        self.assertEqual(result.inactive_modules, ())


if __name__ == "__main__":
    unittest.main()
