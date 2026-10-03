from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server.scoring.main as scoring_main
from server.scoring import (
    AggregateEvidence,
    AggregateSignal,
    get_player_aggregate_evidence,
)


class AggregatePublicApiTests(unittest.TestCase):
    def test_public_types_are_exported(self):
        self.assertIsNotNone(AggregateEvidence)
        self.assertIsNotNone(AggregateSignal)

    def test_public_function_uses_risk_input_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                result = get_player_aggregate_evidence(
                    "session-empty",
                    "player-empty",
                )

                self.assertIsInstance(result, AggregateEvidence)
                self.assertEqual(result.session_id, "session-empty")
                self.assertEqual(result.player_id, "player-empty")
                self.assertEqual(result.signals, ())
                self.assertEqual(result.active_modules, ())
                self.assertEqual(result.unresolved_modules, ())


if __name__ == "__main__":
    unittest.main()
