"""플레이어 현재 Policy snapshot의 조립 동작을 검증한다."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from server.scoring import get_player_policy_snapshot
from server.scoring.player_snapshot import build_player_policy_snapshot
from server.scoring.storage import ModuleState


def state(
    module: str,
    *,
    raw_score: float,
    timestamp_ms: int,
    sequence: int,
    evidence=None,
    reasons=None,
    session_id: str = "s",
    player_id: str = "p",
) -> ModuleState:
    return ModuleState(
        session_id=session_id,
        player_id=player_id,
        module=module,
        timestamp_ms=timestamp_ms,
        sequence=sequence,
        event_id=str(uuid.uuid4()),
        raw_score=raw_score,
        evidence=evidence or {},
        reasons=reasons or [],
    )


class PlayerPolicySnapshotTests(unittest.TestCase):
    def test_builds_current_policy_view_for_each_module(self):
        states = [
            state(
                "noclip", raw_score=3, timestamp_ms=1000, sequence=10,
                evidence={"status": "SUSPICIOUS", "collision": 0, "blocked_path": 0},
                reasons=["Collision Disabled"],
            ),
            state(
                "aimbot", raw_score=4, timestamp_ms=1100, sequence=11,
                evidence={"status": "SUSPICIOUS", "round_scoped": True},
                reasons=["Consistent Target Convergence Before Confirmed Find"],
            ),
            state(
                "autopaint", raw_score=0, timestamp_ms=1200, sequence=12,
                evidence={"integrity_valid": 1, "behavior_valid": 1},
            ),
        ]

        result = build_player_policy_snapshot(
            states, session_id="s", player_id="p"
        )

        self.assertEqual(result.session_id, "s")
        self.assertEqual(result.player_id, "p")
        self.assertEqual(
            tuple(item.state.module for item in result.modules),
            ("aimbot", "autopaint", "noclip"),
        )
        self.assertEqual(result.correlation_candidates, ())
        by_module = {item.state.module: item for item in result.modules}
        self.assertEqual(by_module["noclip"].evaluation.signal.raw_score, 3.0)
        self.assertIn(
            "noclip_behavior",
            by_module["noclip"].evaluation.annotations.overlap_tags,
        )
        self.assertEqual(by_module["aimbot"].evaluation.signal.raw_score, 4.0)
        self.assertEqual(by_module["autopaint"].evaluation.signal.raw_score, 0.0)

    def test_unregistered_module_is_preserved_as_unknown_not_clean(self):
        result = build_player_policy_snapshot(
            [state("future_detector", raw_score=0, timestamp_ms=1000, sequence=1)],
            session_id="s",
            player_id="p",
        )

        self.assertEqual(len(result.modules), 1)
        self.assertEqual(result.modules[0].evaluation.signal.state, "UNKNOWN_MODULE")
        self.assertEqual(result.modules[0].evaluation.annotations.overlap_tags, ())

    def test_optional_correlation_reuses_current_policy_annotations(self):
        # 현재 B 정책끼리는 보통 겹치지 않으므로, 향후 A 정책 합류 상황을 흉내내기 위해
        # Registry 평가 결과를 패치하여 공통 overlap_tag를 반환한다.
        first = state("noclip", raw_score=3, timestamp_ms=1000, sequence=10)
        second = state("noclip_runtime", raw_score=1, timestamp_ms=1200, sequence=11)

        from server.scoring.policies.contract import PolicyAnnotations, PolicyEvaluation
        from server.scoring.policy import SignalPreview

        evaluations = [
            PolicyEvaluation(
                SignalPreview("noclip", 3.0, "snapshot", "RAW_FRACTION_ONLY", 60.0, ()),
                PolicyAnnotations(overlap_tags=("noclip_behavior",)),
            ),
            PolicyEvaluation(
                SignalPreview("noclip_runtime", 1.0, "snapshot", "POLICY_NOT_CALIBRATED", None, ()),
                PolicyAnnotations(overlap_tags=("noclip_behavior",)),
            ),
        ]

        with patch(
            "server.scoring.player_snapshot.evaluate_registered_policy",
            side_effect=evaluations,
        ):
            result = build_player_policy_snapshot(
                [first, second],
                session_id="s",
                player_id="p",
                max_time_distance_ms=500,
            )

        self.assertEqual(len(result.correlation_candidates), 1)
        self.assertEqual(
            result.correlation_candidates[0].modules,
            ("noclip", "noclip_runtime"),
        )

    def test_does_not_invent_default_correlation_window(self):
        first = state("noclip", raw_score=1, timestamp_ms=1000, sequence=1)
        result = build_player_policy_snapshot(
            [first], session_id="s", player_id="p", max_time_distance_ms=None
        )
        self.assertEqual(result.correlation_candidates, ())

    def test_rejects_state_from_different_player(self):
        wrong = state(
            "noclip", raw_score=1, timestamp_ms=1000, sequence=1, player_id="other"
        )
        with self.assertRaises(ValueError):
            build_player_policy_snapshot(
                [wrong], session_id="s", player_id="p"
            )

    def test_correlation_window_validation(self):
        for bad in (-1, 1.5, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    build_player_policy_snapshot(
                        [],
                        session_id="s",
                        player_id="p",
                        max_time_distance_ms=bad,
                    )

    @patch("server.scoring.main.get_player_snapshot")
    def test_public_api_reads_latest_state_once(self, get_snapshot):
        get_snapshot.return_value = [
            state(
                "noclip", raw_score=2, timestamp_ms=1000, sequence=4,
                evidence={"status": "SUSPICIOUS", "collision": 1, "blocked_path": 1},
                reasons=["Blocked Path Detected"],
            )
        ]

        result = get_player_policy_snapshot("s", "p")

        get_snapshot.assert_called_once_with("s", "p")
        self.assertEqual(len(result.modules), 1)
        self.assertEqual(result.modules[0].state.module, "noclip")
        self.assertIn(
            "blocked_path",
            result.modules[0].evaluation.annotations.overlap_tags,
        )


if __name__ == "__main__":
    unittest.main()
