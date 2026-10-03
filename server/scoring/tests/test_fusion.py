from __future__ import annotations

import unittest

from server.scoring.aggregate import (
    AggregateEvidence,
    AggregateSignal,
)
from server.scoring.fusion import build_fusion_plan
from server.scoring.overlap import OverlapGroup


def signal(module: str, event_id: str) -> AggregateSignal:
    return AggregateSignal(
        module=module,
        event_id=event_id,
        status="ACTIVE",
        raw_score=1,
        calibration_version="replay-v1",
        calibration_mode="threshold",
        calibration_threshold=1,
        threshold_met=True,
        overlap_tags=(),
        entity_key=None,
    )


def evidence(
    *,
    active: tuple[str, ...],
    groups: tuple[OverlapGroup, ...] = (),
) -> AggregateEvidence:
    return AggregateEvidence(
        session_id="s",
        player_id="p",
        signals=tuple(
            signal(module, f"event-{module}")
            for module in active
        ),
        active_modules=active,
        inactive_modules=(),
        advisory_modules=(),
        deferred_modules=(),
        unresolved_modules=(),
        unavailable_modules=(),
        correlation_candidates=(),
        overlap_groups=groups,
    )


class FusionPlanTests(unittest.TestCase):
    def test_single_active_module_is_independent(self):
        result = build_fusion_plan(
            evidence(active=("aimbot",))
        )

        self.assertEqual(
            result.independent_modules,
            ("aimbot",),
        )

        self.assertEqual(len(result.units), 1)
        self.assertEqual(
            result.units[0].kind,
            "INDEPENDENT",
        )

    def test_overlap_group_becomes_cluster(self):
        group = OverlapGroup(
            modules=(
                "hide_anywhere",
                "injection",
                "value_tamper",
            ),
            overlap_tags=(
                "hide_config",
                "hide_injection",
            ),
            candidate_count=2,
            event_ids=("a", "b", "c"),
        )

        result = build_fusion_plan(
            evidence(
                active=(
                    "hide_anywhere",
                    "injection",
                    "value_tamper",
                ),
                groups=(group,),
            )
        )

        self.assertEqual(
            result.clustered_modules,
            (
                "hide_anywhere",
                "injection",
                "value_tamper",
            ),
        )

        self.assertEqual(
            result.independent_modules,
            (),
        )

        self.assertEqual(len(result.units), 1)

        unit = result.units[0]

        self.assertEqual(
            unit.kind,
            "OVERLAP_CLUSTER",
        )

        self.assertEqual(
            unit.modules,
            (
                "hide_anywhere",
                "injection",
                "value_tamper",
            ),
        )

        self.assertEqual(unit.candidate_count, 2)

    def test_cluster_and_independent_can_coexist(self):
        group = OverlapGroup(
            modules=("autopaint", "injection"),
            overlap_tags=("process_injection",),
            candidate_count=1,
            event_ids=("a", "b"),
        )

        result = build_fusion_plan(
            evidence(
                active=(
                    "aimbot",
                    "autopaint",
                    "injection",
                ),
                groups=(group,),
            )
        )

        self.assertEqual(
            result.clustered_modules,
            ("autopaint", "injection"),
        )

        self.assertEqual(
            result.independent_modules,
            ("aimbot",),
        )

        self.assertEqual(len(result.units), 2)

    def test_active_module_is_not_lost(self):
        group = OverlapGroup(
            modules=("a", "b"),
            overlap_tags=("x",),
            candidate_count=1,
            event_ids=("1", "2"),
        )

        result = build_fusion_plan(
            evidence(
                active=("a", "b", "c"),
                groups=(group,),
            )
        )

        represented = {
            module
            for unit in result.units
            for module in unit.modules
        }

        self.assertEqual(
            represented,
            {"a", "b", "c"},
        )

    def test_no_active_modules_returns_empty_plan(self):
        result = build_fusion_plan(
            evidence(active=())
        )

        self.assertEqual(result.units, ())
        self.assertEqual(result.active_modules, ())
        self.assertEqual(
            result.independent_modules,
            (),
        )


if __name__ == "__main__":
    unittest.main()
