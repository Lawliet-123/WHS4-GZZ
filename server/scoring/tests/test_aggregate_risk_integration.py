from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import server.scoring.main as scoring_main


def godmode(score: float, timestamp_ms: int):
    return {
        "session_id": "session_a",
        "player_id": "player_a",
        "module": "godmode",
        "timestamp_ms": timestamp_ms,
        "evidence": {
            "invincible": True,
        },
        "reasons": [
            "godmode incident",
        ],
        "raw_score": score,
    }


class AggregateRiskIntegrationTests(unittest.TestCase):
    def test_godmode_history_reaches_aggregate_risk_as_one_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                for sequence, score in enumerate(
                    (2, 3, 5),
                    1,
                ):
                    scoring_main.process(
                        godmode(
                            score,
                            sequence * 1000,
                        ),
                        event_id=str(uuid.uuid4()),
                        sequence=sequence,
                    )

                result = (
                    scoring_main.get_player_aggregate_risk(
                        "session_a",
                        "player_a",
                    )
                )

                # 사건 3건의 raw_score를 합산하지 않는다.
                self.assertEqual(
                    result.active_module_count,
                    1,
                )

                self.assertEqual(
                    result.evidence_unit_count,
                    1,
                )

                self.assertEqual(
                    result.independent_unit_count,
                    1,
                )

                self.assertEqual(
                    result.overlap_adjustment_count,
                    0,
                )

                self.assertTrue(
                    result.assessment_complete
                )

                self.assertEqual(
                    result.active_modules,
                    ("godmode",),
                )


if __name__ == "__main__":
    unittest.main()
