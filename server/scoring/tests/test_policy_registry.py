"""기본 Policy Registry와 Scoring 공개 평가 진입점 테스트."""

from __future__ import annotations

import unittest

from server.scoring import evaluate_event_policy
from server.scoring.policies.registry import build_default_registry


def event(module: str, raw_score: int = 0, *, evidence=None, reasons=None):
    return {
        "session_id": "s",
        "player_id": "p",
        "module": module,
        "timestamp_ms": 1000,
        "evidence": evidence or {},
        "reasons": reasons or [],
        "raw_score": raw_score,
    }


class DefaultPolicyRegistryTests(unittest.TestCase):
    def test_default_registry_routes_noclip(self):
        result = build_default_registry().evaluate(event(
            "noclip",
            3,
            evidence={"status": "SUSPICIOUS", "collision": 0, "blocked_path": 0},
            reasons=["Collision Disabled", "Collision Disabled Too Long"],
        ))
        self.assertEqual(result.signal.module, "noclip")
        self.assertIn("noclip_behavior", result.annotations.overlap_tags)
        self.assertIn("collision_disabled", result.annotations.overlap_tags)

    def test_default_registry_routes_aimbot(self):
        result = build_default_registry().evaluate(event(
            "aimbot",
            4,
            evidence={"status": "SUSPICIOUS", "round_scoped": True},
            reasons=["Consistent Target Convergence Before Confirmed Find"],
        ))
        self.assertEqual(result.signal.module, "aimbot")
        self.assertIn("aimbot_behavior", result.annotations.overlap_tags)
        self.assertIn("target_convergence", result.annotations.overlap_tags)

    def test_default_registry_routes_autopaint(self):
        result = build_default_registry().evaluate(event(
            "autopaint",
            10,
            evidence={
                "integrity_valid": 1,
                "behavior_valid": 0,
                "autopaint_marker_set": 1,
            },
        ))
        self.assertEqual(result.signal.module, "autopaint")
        self.assertIn("autopaint_artifact", result.annotations.overlap_tags)

    def test_scoring_public_entrypoint_uses_default_registry(self):
        result = evaluate_event_policy(event(
            "noclip",
            2,
            evidence={"status": "SUSPICIOUS", "collision": 1, "blocked_path": 1},
            reasons=["Blocked Path Detected"],
        ))
        self.assertEqual(result.signal.raw_score, 2)
        self.assertIn("blocked_path", result.annotations.overlap_tags)

    def test_unregistered_module_keeps_baseline_without_annotations(self):
        result = evaluate_event_policy(event("new_detector", 0))
        self.assertEqual(result.signal.state, "UNKNOWN_MODULE")
        self.assertEqual(result.annotations.overlap_tags, ())
        self.assertEqual(result.annotations.notes, ())

    def test_godmode_is_not_registered_prematurely(self):
        result = evaluate_event_policy(event("godmode", 1))
        self.assertEqual(result.signal.emission, "event_delta")
        self.assertEqual(result.annotations.overlap_tags, ())
        self.assertEqual(result.annotations.notes, ())


if __name__ == "__main__":
    unittest.main()
