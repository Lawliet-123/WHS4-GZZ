from __future__ import annotations

import unittest

from server.scoring.aggregate_risk import AggregateRisk
from server.scoring.final_verdict import (
    FINAL_VERDICT_VERSION,
    build_final_verdict,
)


def risk(
    *,
    units: int = 0,
    active_modules: tuple[str, ...] = (),
    complete: bool = True,
    overlap_adjustment: int = 0,
    advisory: tuple[str, ...] = (),
    unresolved: tuple[str, ...] = (),
    deferred: tuple[str, ...] = (),
    unavailable: tuple[str, ...] = (),
) -> AggregateRisk:
    return AggregateRisk(
        version="evidence-units-v1",
        session_id="s",
        player_id="p",
        evidence_units=(),
        evidence_unit_count=units,
        active_module_count=len(active_modules),
        independent_unit_count=units,
        overlap_cluster_count=0,
        overlap_adjustment_count=overlap_adjustment,
        assessment_complete=complete,
        active_modules=active_modules,
        independent_modules=active_modules,
        clustered_modules=(),
        advisory_modules=advisory,
        unresolved_modules=unresolved,
        deferred_modules=deferred,
        unavailable_modules=unavailable,
    )


class FinalVerdictTests(unittest.TestCase):
    def test_complete_without_active_evidence_is_no_active_evidence(self):
        result = build_final_verdict(
            risk()
        )

        self.assertEqual(
            result.version,
            FINAL_VERDICT_VERSION,
        )

        self.assertEqual(
            result.status,
            "NO_ACTIVE_EVIDENCE",
        )

        self.assertTrue(result.assessment_complete)

        self.assertEqual(
            result.reason_codes,
            ("NO_ACTIVE_EVIDENCE",),
        )

    def test_incomplete_without_active_evidence_is_inconclusive(self):
        result = build_final_verdict(
            risk(
                complete=False,
                unresolved=("esp",),
            )
        )

        self.assertEqual(
            result.status,
            "INCONCLUSIVE",
        )

        self.assertFalse(result.assessment_complete)

        self.assertEqual(
            result.unresolved_modules,
            ("esp",),
        )

        self.assertIn(
            "ASSESSMENT_INCOMPLETE",
            result.reason_codes,
        )

    def test_active_calibrated_evidence_is_suspicious(self):
        result = build_final_verdict(
            risk(
                units=1,
                active_modules=("aimbot",),
            )
        )

        self.assertEqual(
            result.status,
            "SUSPICIOUS",
        )

        self.assertEqual(
            result.evidence_unit_count,
            1,
        )

        self.assertIn(
            "CALIBRATED_ACTIVE_EVIDENCE",
            result.reason_codes,
        )

    def test_positive_evidence_survives_incomplete_assessment(self):
        result = build_final_verdict(
            risk(
                units=1,
                active_modules=("godmode",),
                complete=False,
                unresolved=("esp", "noclip"),
            )
        )

        # pending 모듈 때문에 이미 확인된 positive evidence를 지우면 안 된다.
        self.assertEqual(
            result.status,
            "SUSPICIOUS",
        )

        self.assertFalse(
            result.assessment_complete,
        )

        self.assertIn(
            "CALIBRATED_ACTIVE_EVIDENCE",
            result.reason_codes,
        )

        self.assertIn(
            "ASSESSMENT_INCOMPLETE",
            result.reason_codes,
        )

    def test_advisory_alone_does_not_become_suspicious(self):
        result = build_final_verdict(
            risk(
                advisory=("filesystem",),
            )
        )

        self.assertEqual(
            result.status,
            "NO_ACTIVE_EVIDENCE",
        )

        self.assertIn(
            "ADVISORY_EVIDENCE_PRESENT",
            result.reason_codes,
        )

    def test_unavailable_without_active_evidence_is_inconclusive(self):
        result = build_final_verdict(
            risk(
                complete=False,
                unavailable=("injection",),
            )
        )

        self.assertEqual(
            result.status,
            "INCONCLUSIVE",
        )

    def test_verdict_does_not_expose_cheat_or_normal_status(self):
        observed = {
            build_final_verdict(risk()).status,
            build_final_verdict(
                risk(
                    units=1,
                    active_modules=("aimbot",),
                )
            ).status,
            build_final_verdict(
                risk(
                    complete=False,
                    unresolved=("esp",),
                )
            ).status,
        }

        self.assertNotIn("CHEAT", observed)
        self.assertNotIn("NORMAL", observed)


if __name__ == "__main__":
    unittest.main()
