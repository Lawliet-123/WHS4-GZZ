from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server.scoring.main as scoring_main
from server.scoring import (
    AggregateRisk,
    get_player_aggregate_risk,
)


class AggregateRiskPublicFunctionTests(unittest.TestCase):
    def test_type_is_exported(self):
        self.assertIsNotNone(AggregateRisk)

    def test_empty_player_returns_zero_complete_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                result = get_player_aggregate_risk(
                    "session-empty",
                    "player-empty",
                )

                self.assertIsInstance(result, AggregateRisk)

                self.assertEqual(
                    result.session_id,
                    "session-empty",
                )
                self.assertEqual(
                    result.player_id,
                    "player-empty",
                )

                self.assertEqual(
                    result.evidence_unit_count,
                    0,
                )
                self.assertEqual(
                    result.active_module_count,
                    0,
                )
                self.assertEqual(
                    result.overlap_adjustment_count,
                    0,
                )

                self.assertTrue(
                    result.assessment_complete
                )


if __name__ == "__main__":
    unittest.main()
