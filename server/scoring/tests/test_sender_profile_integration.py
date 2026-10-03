"""PR #82/#84 sender 변경과 B Scoring Profile의 통합 계약을 검증한다."""

from __future__ import annotations

import unittest

from server.scoring.player_snapshot import ModulePolicySnapshot, PlayerPolicySnapshot
from server.scoring.policies.contract import PolicyAnnotations, PolicyEvaluation
from server.scoring.policy import SignalPreview, inspect_event
from server.scoring.risk_input import build_player_risk_input
from server.scoring.storage import ModuleState


def event(module, score=0, *, status="NORMAL"):
    return {
        "session_id": "sender_profile",
        "player_id": "player_1",
        "module": module,
        "timestamp_ms": 1000,
        "evidence": {"status": status},
        "reasons": [],
        "raw_score": score,
    }


class SenderProfileIntegrationTests(unittest.TestCase):

    def test_pr82_state_modules_are_snapshots(self):
        for module in (
            "filesystem",
            "injection",
            "value_tamper",
            "overlay_hook",
            "godmode_runtime",
            "noclip_runtime",
            "aimbot_runtime",
            "whistle",
        ):
            with self.subTest(module=module):
                preview = inspect_event(event(module))
                self.assertEqual(preview.emission, "snapshot")

    def test_pr82_error_zero_is_measurement_unavailable(self):
        for module in ("filesystem", "godmode_runtime", "whistle"):
            with self.subTest(module=module):
                preview = inspect_event(event(module, status="ERROR"))
                self.assertEqual(preview.state, "MEASUREMENT_UNAVAILABLE")
                self.assertIsNone(preview.raw_fraction_pct)

    def test_runtime_source_bounds_are_current(self):
        cases = {
            "godmode_runtime": (5, 6),
            "noclip_runtime": (1, 2),
            "aimbot_runtime": (1, 2),
        }

        for module, (valid, invalid) in cases.items():
            with self.subTest(module=module):
                self.assertNotEqual(
                    inspect_event(event(module, valid)).state,
                    "OUT_OF_AUDITED_RANGE",
                )
                self.assertEqual(
                    inspect_event(event(module, invalid)).state,
                    "OUT_OF_AUDITED_RANGE",
                )

    def test_hash_profile_tracks_pr84_zero_snapshots(self):
        self.assertEqual(
            inspect_event(event("localguard_executable_hash", 1)).emission,
            "snapshot",
        )
        self.assertEqual(inspect_event(event("localguard_executable_hash", 0)).emission, "snapshot")

    def test_yara_profile_stays_conservative_pending_scoped_state(self):
        self.assertEqual(
            inspect_event(event("localguard_yara", 3)).emission,
            "per_entity_positive_only",
        )
        from server.scoring.policy import PROFILES
        self.assertIn("sender includes measured zeros", PROFILES["localguard_yara"].note)
        self.assertIn("pending PID/scope state integration", PROFILES["localguard_yara"].note)

    def test_yara_custom_rule_score_remains_outside_current_audited_bound(self):
        preview = inspect_event(event("localguard_yara", 10))

        self.assertEqual(preview.state, "OUT_OF_AUDITED_RANGE")
        self.assertEqual(preview.raw_score, 10)

    def test_whistle_rpc_requires_window_history(self):
        preview = inspect_event(event("whistle_rpc", 0))

        self.assertEqual(preview.emission, "window_history")
        self.assertIn("window-scoped history", " ".join(preview.issues))

        state = ModuleState(
            session_id="sender_profile",
            player_id="player_1",
            module="whistle_rpc",
            timestamp_ms=1000,
            sequence=1,
            event_id="00000000-0000-0000-0000-000000000001",
            raw_score=0,
            evidence={"status": "NORMAL", "window_id": 1, "sample_id": 1},
            reasons=[],
        )

        evaluation = PolicyEvaluation(
            signal=SignalPreview(
                module="whistle_rpc",
                raw_score=0.0,
                emission="window_history",
                state=preview.state,
                raw_fraction_pct=None,
                issues=preview.issues,
            ),
            annotations=PolicyAnnotations(),
        )

        snapshot = PlayerPolicySnapshot(
            session_id="sender_profile",
            player_id="player_1",
            modules=(ModulePolicySnapshot(state=state, evaluation=evaluation),),
            correlation_candidates=(),
        )

        risk_input = build_player_risk_input(snapshot)

        self.assertTrue(risk_input.signals[0].requires_event_history)
        self.assertEqual(risk_input.event_history_modules, ("whistle_rpc",))


if __name__ == "__main__":
    unittest.main()
