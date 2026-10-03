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


class AggregateGodmodeHistoryTests(unittest.TestCase):
    def _summary(
        self,
        *,
        total: int,
        qualifying: int,
        maximum: float | None,
    ):
        from server.scoring.history_summary import GodmodeHistorySummary

        return GodmodeHistorySummary(
            session_id="s",
            player_id="p",
            calibration_version="replay-v1",
            threshold=2.0,
            total_events=total,
            qualifying_events=qualifying,
            max_raw_score=maximum,
            first_timestamp_ms=1000 if total else None,
            last_timestamp_ms=2000 if total else None,
            first_sequence=1 if total else None,
            last_sequence=total if total else None,
            reasons=("godmode",) if total else (),
        )

    def test_godmode_history_with_qualifying_event_becomes_active(self):
        item = signal(
            "godmode",
            threshold_met=True,
            calibration_mode="event_threshold",
            requires_event_history=True,
        )

        result = build_aggregate_evidence(
            player(item),
            godmode_history=self._summary(
                total=3,
                qualifying=3,
                maximum=5.0,
            ),
        )

        self.assertEqual(result.active_modules, ("godmode",))
        self.assertEqual(result.deferred_modules, ())

        resolved = result.signals[0]
        self.assertTrue(resolved.history_resolved)
        self.assertEqual(resolved.history_total_events, 3)
        self.assertEqual(resolved.history_qualifying_events, 3)
        self.assertEqual(resolved.history_max_raw_score, 5.0)

    def test_godmode_history_without_qualifying_event_becomes_inactive(self):
        item = signal(
            "godmode",
            threshold_met=False,
            calibration_mode="event_threshold",
            requires_event_history=True,
        )

        result = build_aggregate_evidence(
            player(item),
            godmode_history=self._summary(
                total=2,
                qualifying=0,
                maximum=1.0,
            ),
        )

        self.assertEqual(result.inactive_modules, ("godmode",))
        self.assertEqual(result.deferred_modules, ())

    def test_empty_godmode_history_remains_deferred(self):
        item = signal(
            "godmode",
            threshold_met=True,
            calibration_mode="event_threshold",
            requires_event_history=True,
        )

        result = build_aggregate_evidence(
            player(item),
            godmode_history=self._summary(
                total=0,
                qualifying=0,
                maximum=None,
            ),
        )

        self.assertEqual(result.deferred_modules, ("godmode",))
        self.assertFalse(result.signals[0].history_resolved)

    def test_rejects_history_for_different_player(self):
        from server.scoring.history_summary import GodmodeHistorySummary

        item = signal(
            "godmode",
            threshold_met=True,
            calibration_mode="event_threshold",
            requires_event_history=True,
        )

        wrong = GodmodeHistorySummary(
            session_id="s",
            player_id="other",
            calibration_version="replay-v1",
            threshold=2.0,
            total_events=1,
            qualifying_events=1,
            max_raw_score=2.0,
            first_timestamp_ms=1000,
            last_timestamp_ms=1000,
            first_sequence=1,
            last_sequence=1,
            reasons=("x",),
        )

        with self.assertRaises(ValueError):
            build_aggregate_evidence(
                player(item),
                godmode_history=wrong,
            )
