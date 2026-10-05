from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import server.scoring.main as scoring_main


def selfdefense_event(
    *,
    kind: str,
    status: str,
    timestamp_ms: int,
):
    return {
        "session_id": "session_sd",
        "player_id": "player_sd",
        "module": "selfdefense",
        "timestamp_ms": timestamp_ms,
        "evidence": {
            "kind": kind,
            "status": status,
            "scan_complete": status != "ERROR",
        },
        "reasons": [f"selfdefense operational status: {status}"],
        "raw_score": 0,
    }


def godmode_event(score: float, timestamp_ms: int):
    return {
        "session_id": "session_sd",
        "player_id": "player_sd",
        "module": "godmode",
        "timestamp_ms": timestamp_ms,
        "evidence": {
            "invincible": True,
        },
        "reasons": ["godmode incident"],
        "raw_score": score,
    }


def unknown_event(timestamp_ms: int):
    return {
        "session_id": "session_unknown",
        "player_id": "player_unknown",
        "module": "unknown_detector",
        "timestamp_ms": timestamp_ms,
        "evidence": {},
        "reasons": [],
        "raw_score": 0,
    }


class SelfDefenseOperationalExclusionTests(unittest.TestCase):
    def test_selfdefense_is_stored_but_excluded_from_cheat_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                scoring_main.process(
                    selfdefense_event(
                        kind="file_integrity",
                        status="ERROR",
                        timestamp_ms=1000,
                    ),
                    event_id=str(uuid.uuid4()),
                    sequence=1,
                )

                snapshot = scoring_main.get_player_snapshot(
                    "session_sd",
                    "player_sd",
                )

                self.assertEqual(
                    tuple(item.module for item in snapshot),
                    ("selfdefense",),
                )

                risk_input = scoring_main.get_player_risk_input(
                    "session_sd",
                    "player_sd",
                )

                self.assertEqual(risk_input.signals, ())
                self.assertEqual(
                    risk_input.unresolved_policy_modules,
                    (),
                )
                self.assertEqual(
                    risk_input.measurement_unavailable_modules,
                    (),
                )

                verdict = scoring_main.get_player_final_verdict(
                    "session_sd",
                    "player_sd",
                )

                self.assertEqual(
                    verdict.status,
                    "NO_ACTIVE_EVIDENCE",
                )
                self.assertTrue(verdict.assessment_complete)
                self.assertEqual(
                    verdict.evidence_unit_count,
                    0,
                )
                self.assertEqual(
                    verdict.unresolved_modules,
                    (),
                )

    def test_selfdefense_does_not_change_real_active_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                scoring_main.process(
                    selfdefense_event(
                        kind="debugger_presence",
                        status="DETECTED",
                        timestamp_ms=1000,
                    ),
                    event_id=str(uuid.uuid4()),
                    sequence=1,
                )

                scoring_main.process(
                    godmode_event(2, 2000),
                    event_id=str(uuid.uuid4()),
                    sequence=2,
                )

                verdict = scoring_main.get_player_final_verdict(
                    "session_sd",
                    "player_sd",
                )

                self.assertEqual(
                    verdict.status,
                    "SUSPICIOUS",
                )
                self.assertTrue(verdict.assessment_complete)
                self.assertEqual(
                    verdict.evidence_unit_count,
                    1,
                )
                self.assertEqual(
                    verdict.active_modules,
                    ("godmode",),
                )
                self.assertNotIn(
                    "selfdefense",
                    verdict.unresolved_modules,
                )
                self.assertNotIn(
                    "selfdefense",
                    verdict.active_modules,
                )

    def test_other_unknown_modules_remain_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                scoring_main.process(
                    unknown_event(1000),
                    event_id=str(uuid.uuid4()),
                    sequence=1,
                )

                verdict = scoring_main.get_player_final_verdict(
                    "session_unknown",
                    "player_unknown",
                )

                self.assertEqual(
                    verdict.status,
                    "INCONCLUSIVE",
                )
                self.assertFalse(verdict.assessment_complete)
                self.assertEqual(
                    verdict.unresolved_modules,
                    ("unknown_detector",),
                )


if __name__ == "__main__":
    unittest.main()
