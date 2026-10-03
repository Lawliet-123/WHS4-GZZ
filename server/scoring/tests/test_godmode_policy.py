from __future__ import annotations

import unittest

from server.scoring.player_snapshot import build_player_policy_snapshot
from server.scoring.policies.registry import evaluate_registered_policy
from server.scoring.risk_input import build_player_risk_input
from server.scoring.storage import ModuleState


def event(raw_score=5, *, evidence=None, reasons=None):
    return {
        "session_id": "s",
        "player_id": "p",
        "module": "godmode",
        "timestamp_ms": 1000,
        "evidence": evidence or {},
        "reasons": reasons or [],
        "raw_score": raw_score,
    }


class GodmodePolicyTests(unittest.TestCase):
    def test_registered_godmode_is_reviewed_event_delta(self):
        result = evaluate_registered_policy(event(
            5,
            evidence={
                "invincible": True,
                "health": 100,
                "max_health": 100,
                "change_before_health": 75,
            },
            reasons=["GodMode evidence"],
        ))

        self.assertEqual(result.signal.emission, "event_delta")
        self.assertEqual(
            result.signal.state,
            "POLICY_NOT_CALIBRATED",
        )
        self.assertIsNone(result.signal.raw_fraction_pct)
        self.assertIn(
            "requires idempotent history",
            " ".join(result.signal.issues),
        )
        self.assertIn(
            "event_delta_history",
            " ".join(result.annotations.notes),
        )

    def test_positive_godmode_event_adds_candidate_tags_only(self):
        result = evaluate_registered_policy(event(
            5,
            evidence={
                "invincible": True,
                "change_before_health": 80,
                "kill_event": False,
                "death_event": False,
                "heal_event": False,
                "respawn_event": False,
            },
            reasons=["new godmode incident"],
        ))

        self.assertIn(
            "godmode_behavior",
            result.annotations.overlap_tags,
        )
        self.assertIn(
            "invincibility_state",
            result.annotations.overlap_tags,
        )
        self.assertIn(
            "health_state_transition",
            result.annotations.overlap_tags,
        )

    def test_manual_zero_event_does_not_create_behavior_overlap(self):
        result = evaluate_registered_policy(event(
            0,
            evidence={
                "invincible": False,
                "change_before_health": None,
                "kill_event": False,
                "death_event": False,
                "heal_event": False,
                "respawn_event": False,
            },
            reasons=[],
        ))

        self.assertEqual(
            result.signal.state,
            "POLICY_NOT_CALIBRATED",
        )
        self.assertEqual(result.annotations.overlap_tags, ())

    def test_risk_input_requires_history_but_is_not_unresolved(self):
        state = ModuleState(
            session_id="s",
            player_id="p",
            module="godmode",
            timestamp_ms=1000,
            sequence=1,
            event_id="00000000-0000-0000-0000-000000000001",
            raw_score=5,
            evidence={
                "invincible": True,
                "health": 100,
                "max_health": 100,
                "change_before_health": 75,
            },
            reasons=["new godmode incident"],
        )

        snapshot = build_player_policy_snapshot(
            [state],
            session_id="s",
            player_id="p",
        )
        risk_input = build_player_risk_input(snapshot)

        self.assertEqual(
            risk_input.event_history_modules,
            ("godmode",),
        )
        self.assertEqual(
            risk_input.unresolved_policy_modules,
            (),
        )
        self.assertTrue(
            risk_input.signals[0].requires_event_history,
        )
        self.assertEqual(
            risk_input.signals[0].policy_state,
            "POLICY_NOT_CALIBRATED",
        )


if __name__ == "__main__":
    unittest.main()
