from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server.scoring.main as scoring_main
from server.scoring import (
    FusionEvidenceUnit,
    FusionPlan,
    get_player_fusion_plan,
)


class FusionPublicFunctionTests(unittest.TestCase):
    def test_types_are_exported(self):
        self.assertIsNotNone(FusionPlan)
        self.assertIsNotNone(FusionEvidenceUnit)

    def test_empty_player_returns_empty_fusion_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                result = get_player_fusion_plan(
                    "session-empty",
                    "player-empty",
                )

                self.assertIsInstance(result, FusionPlan)
                self.assertEqual(result.session_id, "session-empty")
                self.assertEqual(result.player_id, "player-empty")
                self.assertEqual(result.units, ())
                self.assertEqual(result.active_modules, ())
                self.assertEqual(result.clustered_modules, ())
                self.assertEqual(result.independent_modules, ())


if __name__ == "__main__":
    unittest.main()
