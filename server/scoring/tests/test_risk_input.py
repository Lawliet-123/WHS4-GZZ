from __future__ import annotations

import unittest
from unittest.mock import patch

from server.scoring.correlation import CorrelationCandidate
from server.scoring.main import get_player_risk_input
from server.scoring.player_snapshot import ModulePolicySnapshot, PlayerPolicySnapshot
from server.scoring.policies.contract import PolicyAnnotations, PolicyEvaluation
from server.scoring.policy import SignalPreview
from server.scoring.risk_input import build_player_risk_input
from server.scoring.storage import ModuleState


def _module_snapshot(
    module: str,
    *,
    raw_score: float = 0.0,
    emission: str = "snapshot",
    policy_state: str = "RAW_FRACTION_ONLY",
    raw_fraction_pct: float | None = 0.0,
    entity_key: str | None = None,
    overlap_tags: tuple[str, ...] = (),
    issues: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    sequence: int = 1,
) -> ModulePolicySnapshot:
    state = ModuleState(
        session_id="session_1",
        player_id="player_1",
        module=module,
        timestamp_ms=1000 + sequence,
        sequence=sequence,
        event_id=f"00000000-0000-0000-0000-{sequence:012d}",
        raw_score=raw_score,
        evidence={},
        reasons=[],
    )
    evaluation = PolicyEvaluation(
        signal=SignalPreview(
            module=module,
            raw_score=raw_score,
            emission=emission,
            state=policy_state,
            raw_fraction_pct=raw_fraction_pct,
            issues=issues,
        ),
        annotations=PolicyAnnotations(
            entity_key=entity_key,
            overlap_tags=overlap_tags,
            notes=notes,
        ),
    )
    return ModulePolicySnapshot(state=state, evaluation=evaluation)


class RiskInputTests(unittest.TestCase):
    def test_preserves_snapshot_signal_without_aggregating_score(self) -> None:
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(
                _module_snapshot("noclip", raw_score=3.0, raw_fraction_pct=60.0),
                _module_snapshot(
                    "aimbot",
                    raw_score=4.0,
                    raw_fraction_pct=50.0,
                    sequence=2,
                ),
            ),
            correlation_candidates=(),
        )

        result = build_player_risk_input(snapshot)

        self.assertEqual(
            [item.raw_score for item in result.signals],
            [3.0, 4.0],
        )
        self.assertEqual(result.measurement_unavailable_modules, ())
        self.assertEqual(result.event_history_modules, ())

    def test_measurement_unavailable_zero_is_not_treated_as_normal(self) -> None:
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(
                _module_snapshot(
                    "aimbot",
                    raw_score=0.0,
                    policy_state="MEASUREMENT_UNAVAILABLE",
                    raw_fraction_pct=None,
                ),
            ),
            correlation_candidates=(),
        )

        result = build_player_risk_input(snapshot)

        self.assertFalse(result.signals[0].measurement_available)
        self.assertEqual(
            result.measurement_unavailable_modules,
            ("aimbot",),
        )

    def test_event_delta_requires_history(self) -> None:
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(
                _module_snapshot(
                    "godmode",
                    raw_score=2.0,
                    emission="event_delta",
                    policy_state="POLICY_NOT_CALIBRATED",
                    raw_fraction_pct=None,
                ),
            ),
            correlation_candidates=(),
        )

        result = build_player_risk_input(snapshot)

        self.assertTrue(result.signals[0].requires_event_history)
        self.assertEqual(result.event_history_modules, ("godmode",))
        # replay-v1에서 Godmode event threshold=2가 확정됐으므로
        # policy_state가 POLICY_NOT_CALIBRATED여도 calibration 계층에서는 unresolved가 아니다.
        # 다만 event_delta이므로 event history 요구는 그대로 유지한다.
        self.assertEqual(result.unresolved_policy_modules, ())

    def test_entity_scoped_positive_only_is_marked(self) -> None:
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(
                _module_snapshot(
                    "external_access",
                    emission="per_entity_positive_only",
                    policy_state="POLICY_NOT_CALIBRATED",
                    raw_fraction_pct=None,
                    entity_key="pid:1234",
                ),
            ),
            correlation_candidates=(),
        )

        result = build_player_risk_input(snapshot)

        self.assertTrue(result.signals[0].requires_entity_scope)
        self.assertEqual(
            result.entity_scoped_modules,
            ("external_access",),
        )
        self.assertEqual(result.signals[0].entity_key, "pid:1234")

    def test_entity_scoped_snapshot_is_marked(self) -> None:
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(
                _module_snapshot(
                    "localguard_yara",
                    emission="per_entity_snapshot",
                    policy_state="POLICY_NOT_CALIBRATED",
                    raw_fraction_pct=None,
                    entity_key="yara_pid:900:selected_local_process_memory",
                ),
            ),
            correlation_candidates=(),
        )

        result = build_player_risk_input(snapshot)

        self.assertTrue(result.signals[0].requires_entity_scope)
        self.assertEqual(
            result.entity_scoped_modules,
            ("localguard_yara",),
        )

    def test_noclip_runtime_calibration_is_resolved(self) -> None:
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(
                _module_snapshot(
                    "noclip_runtime",
                    raw_score=1.0,
                    policy_state="POLICY_NOT_CALIBRATED",
                    raw_fraction_pct=None,
                ),
            ),
            correlation_candidates=(),
        )

        result = build_player_risk_input(snapshot)

        signal = result.signals[0]
        self.assertEqual(signal.calibration_mode, "threshold")
        self.assertEqual(signal.calibration_threshold, 1.0)
        self.assertTrue(signal.threshold_met)
        self.assertEqual(result.unresolved_policy_modules, ())


    def test_unresolved_module_is_kept_instead_of_dropped(self) -> None:
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(
                _module_snapshot(
                    "new_detector",
                    emission="unknown",
                    policy_state="UNKNOWN_MODULE",
                    raw_fraction_pct=None,
                ),
            ),
            correlation_candidates=(),
        )

        result = build_player_risk_input(snapshot)

        self.assertEqual(len(result.signals), 1)
        self.assertEqual(
            result.unresolved_policy_modules,
            ("new_detector",),
        )

    def test_correlation_candidates_are_preserved(self) -> None:
        candidate = CorrelationCandidate(
            session_id="session_1",
            player_id="player_1",
            modules=("noclip", "noclip_runtime"),
            event_ids=(
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            ),
            sequences=(1, 2),
            overlap_tags=("noclip_behavior",),
            entity_keys=(None, None),
            time_distance_ms=200,
            reasons=("candidate",),
        )
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(),
            correlation_candidates=(candidate,),
        )

        result = build_player_risk_input(snapshot)

        self.assertEqual(
            result.correlation_candidates,
            (candidate,),
        )

    def test_public_main_wrapper_uses_player_policy_snapshot(self) -> None:
        snapshot = PlayerPolicySnapshot(
            session_id="session_1",
            player_id="player_1",
            modules=(),
            correlation_candidates=(),
        )

        with patch(
            "server.scoring.main.get_player_policy_snapshot",
            return_value=snapshot,
        ) as mocked:
            result = get_player_risk_input(
                "session_1",
                "player_1",
                max_time_distance_ms=5000,
            )

        mocked.assert_called_once_with(
            "session_1",
            "player_1",
            max_time_distance_ms=5000,
        )
        self.assertEqual(result.session_id, "session_1")
        self.assertEqual(result.player_id, "player_1")


if __name__ == "__main__":
    unittest.main()
