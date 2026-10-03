from __future__ import annotations

import unittest

from server.scoring.aggregate_risk import (
    AGGREGATE_RISK_VERSION,
    build_aggregate_risk,
)
from server.scoring.fusion import FusionEvidenceUnit, FusionPlan


def plan(
    *,
    units=(),
    active=(),
    clustered=(),
    independent=(),
    unresolved=(),
    deferred=(),
    unavailable=(),
    advisory=(),
):
    return FusionPlan(
        session_id="s",
        player_id="p",
        units=tuple(units),
        active_modules=tuple(active),
        clustered_modules=tuple(clustered),
        independent_modules=tuple(independent),
        unresolved_modules=tuple(unresolved),
        deferred_modules=tuple(deferred),
        unavailable_modules=tuple(unavailable),
        advisory_modules=tuple(advisory),
    )


class AggregateRiskTests(unittest.TestCase):
    def test_empty_complete_player_has_zero_evidence_units(self):
        result = build_aggregate_risk(plan())

        self.assertEqual(
            result.version,
            AGGREGATE_RISK_VERSION,
        )
        self.assertEqual(result.evidence_unit_count, 0)
        self.assertEqual(result.active_module_count, 0)
        self.assertEqual(result.overlap_adjustment_count, 0)
        self.assertTrue(result.assessment_complete)

    def test_one_independent_signal_is_one_unit(self):
        unit = FusionEvidenceUnit(
            kind="INDEPENDENT",
            modules=("aimbot",),
            event_ids=("a",),
        )

        result = build_aggregate_risk(
            plan(
                units=(unit,),
                active=("aimbot",),
                independent=("aimbot",),
            )
        )

        self.assertEqual(result.active_module_count, 1)
        self.assertEqual(result.evidence_unit_count, 1)
        self.assertEqual(result.independent_unit_count, 1)
        self.assertEqual(result.overlap_cluster_count, 0)
        self.assertEqual(result.overlap_adjustment_count, 0)

    def test_three_overlapping_modules_count_as_one_unit(self):
        unit = FusionEvidenceUnit(
            kind="OVERLAP_CLUSTER",
            modules=(
                "hide_anywhere",
                "injection",
                "value_tamper",
            ),
            overlap_tags=("hide_config",),
            event_ids=("a", "b", "c"),
            candidate_count=2,
        )

        result = build_aggregate_risk(
            plan(
                units=(unit,),
                active=(
                    "hide_anywhere",
                    "injection",
                    "value_tamper",
                ),
                clustered=(
                    "hide_anywhere",
                    "injection",
                    "value_tamper",
                ),
            )
        )

        self.assertEqual(result.active_module_count, 3)
        self.assertEqual(result.evidence_unit_count, 1)
        self.assertEqual(result.overlap_cluster_count, 1)

        # 세 module 중 추가 2개를 독립 증거로 중복 세지 않는다.
        self.assertEqual(result.overlap_adjustment_count, 2)

    def test_cluster_and_independent_are_two_units(self):
        cluster = FusionEvidenceUnit(
            kind="OVERLAP_CLUSTER",
            modules=("autopaint", "injection"),
            overlap_tags=("process_injection",),
            event_ids=("a", "b"),
            candidate_count=1,
        )

        independent = FusionEvidenceUnit(
            kind="INDEPENDENT",
            modules=("aimbot",),
            event_ids=("c",),
        )

        result = build_aggregate_risk(
            plan(
                units=(cluster, independent),
                active=("aimbot", "autopaint", "injection"),
                clustered=("autopaint", "injection"),
                independent=("aimbot",),
            )
        )

        self.assertEqual(result.active_module_count, 3)
        self.assertEqual(result.evidence_unit_count, 2)
        self.assertEqual(result.overlap_adjustment_count, 1)
        self.assertEqual(result.independent_unit_count, 1)
        self.assertEqual(result.overlap_cluster_count, 1)

    def test_unresolved_module_makes_assessment_incomplete(self):
        result = build_aggregate_risk(
            plan(unresolved=("esp",))
        )

        self.assertFalse(result.assessment_complete)
        self.assertEqual(result.evidence_unit_count, 0)

    def test_unavailable_module_makes_assessment_incomplete(self):
        result = build_aggregate_risk(
            plan(unavailable=("injection",))
        )

        self.assertFalse(result.assessment_complete)

    def test_advisory_does_not_make_assessment_incomplete(self):
        result = build_aggregate_risk(
            plan(advisory=("filesystem",))
        )

        self.assertTrue(result.assessment_complete)
        self.assertEqual(
            result.advisory_modules,
            ("filesystem",),
        )

    def test_duplicate_representation_is_rejected(self):
        a = FusionEvidenceUnit(
            kind="INDEPENDENT",
            modules=("aimbot",),
        )

        b = FusionEvidenceUnit(
            kind="OVERLAP_CLUSTER",
            modules=("aimbot", "injection"),
        )

        with self.assertRaises(ValueError):
            build_aggregate_risk(
                plan(
                    units=(a, b),
                    active=("aimbot", "injection"),
                )
            )


if __name__ == "__main__":
    unittest.main()
