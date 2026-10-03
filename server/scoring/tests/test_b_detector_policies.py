"""B 담당 Noclip/Aimbot/AutoPaint 정책 주석 테스트.

최종 위험도나 가중치는 검증하지 않는다. 최신 Shared Event의 의미를 잃지 않고
중복 후보/주의사항만 추가하는지 확인한다.
"""

from __future__ import annotations

import unittest

from server.scoring.policies import aimbot, autopaint, noclip
from server.scoring.policies.contract import PolicyRegistry


def event(module, raw_score=0, *, evidence=None, reasons=None):
    return {
        "session_id": "s",
        "player_id": "p",
        "module": module,
        "timestamp_ms": 1000,
        "evidence": evidence or {},
        "reasons": reasons or [],
        "raw_score": raw_score,
    }


def registry() -> PolicyRegistry:
    value = PolicyRegistry()
    value.register("noclip", noclip.evaluate)
    value.register("aimbot", aimbot.evaluate)
    value.register("autopaint", autopaint.evaluate)
    return value


class BDetectorPolicyTests(unittest.TestCase):
    def test_noclip_normal_zero_is_snapshot_not_positive_only(self):
        result = registry().evaluate(event(
            "noclip",
            0,
            evidence={
                "status": "NORMAL",
                "collision": 1,
                "blocked_path": 0,
                "measurement_complete": True,
                "detection_hold_active": False,
            },
        ))
        self.assertEqual(result.signal.emission, "snapshot")
        self.assertEqual(result.signal.raw_fraction_pct, 0)
        self.assertEqual(result.annotations.overlap_tags, ())

    def test_noclip_error_zero_is_not_clean(self):
        result = registry().evaluate(event(
            "noclip",
            0,
            evidence={
                "status": "ERROR",
                "collision": -1,
                "blocked_path": 0,
                "measurement_complete": False,
            },
        ))
        self.assertEqual(result.signal.state, "MEASUREMENT_UNAVAILABLE")
        self.assertIsNone(result.signal.raw_fraction_pct)
        self.assertIn("정상 0점", " ".join(result.annotations.notes))

    def test_noclip_positive_tags_are_candidates_only(self):
        result = registry().evaluate(event(
            "noclip",
            3,
            evidence={"status": "SUSPICIOUS", "collision": 0, "blocked_path": 0},
            reasons=["Collision Disabled", "Collision Disabled Too Long"],
        ))
        self.assertEqual(result.signal.raw_fraction_pct, 60)
        self.assertIn("noclip_behavior", result.annotations.overlap_tags)
        self.assertIn("collision_disabled", result.annotations.overlap_tags)

    def test_aimbot_normal_zero_is_valid_snapshot(self):
        result = registry().evaluate(event(
            "aimbot", 0, evidence={"status": "NORMAL", "round_scoped": True}
        ))
        self.assertEqual(result.signal.emission, "snapshot")
        self.assertEqual(result.signal.raw_fraction_pct, 0)
        self.assertIn("스냅샷", " ".join(result.annotations.notes))

    def test_aimbot_round_scope_error_is_unavailable(self):
        result = registry().evaluate(event(
            "aimbot",
            0,
            evidence={
                "status": "ERROR",
                "round_scoped": False,
                "error_code": "ROUND_SCOPE_UNAVAILABLE",
            },
        ))
        self.assertEqual(result.signal.state, "MEASUREMENT_UNAVAILABLE")
        self.assertIn("정상 스냅샷", " ".join(result.annotations.notes))

    def test_aimbot_reason_tags(self):
        result = registry().evaluate(event(
            "aimbot",
            4,
            evidence={"status": "SUSPICIOUS", "round_scoped": True},
            reasons=[
                "Consistent Target Convergence Before Confirmed Find",
                "Target Lock Maintained Before Confirmed Find",
            ],
        ))
        self.assertEqual(result.signal.raw_fraction_pct, 50)
        self.assertIn("aimbot_behavior", result.annotations.overlap_tags)
        self.assertIn("target_convergence", result.annotations.overlap_tags)

    def test_autopaint_raw_is_max_not_sum_and_partial_channel_is_allowed(self):
        result = registry().evaluate(event(
            "autopaint",
            10,
            evidence={
                "integrity_valid": 1,
                "behavior_valid": 0,
                "autopaint_marker_set": 1,
                "injector_process": 1,
            },
        ))
        self.assertEqual(result.signal.state, "RAW_FRACTION_ONLY")
        self.assertIn("autopaint_artifact", result.annotations.overlap_tags)
        self.assertIn("process_injection", result.annotations.overlap_tags)
        self.assertIn("최댓값", " ".join(result.annotations.notes))
        self.assertIn("일부 채널", " ".join(result.annotations.notes))

    def test_autopaint_both_invalid_stays_unavailable(self):
        result = registry().evaluate(event(
            "autopaint",
            0,
            evidence={"integrity_valid": 0, "behavior_valid": 0},
        ))
        self.assertEqual(result.signal.state, "MEASUREMENT_UNAVAILABLE")
        self.assertIsNone(result.signal.raw_fraction_pct)


if __name__ == "__main__":
    unittest.main()
