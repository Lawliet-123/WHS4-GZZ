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
        "evidence": {"invincible": True},
        "reasons": ["incident"],
        "raw_score": score,
    }


class AggregateGodmodeIntegrationTests(unittest.TestCase):
    def test_godmode_history_resolves_deferred_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                for sequence, score in enumerate((2, 3, 5), 1):
                    scoring_main.process(
                        godmode(score, sequence * 1000),
                        event_id=str(uuid.uuid4()),
                        sequence=sequence,
                    )

                result = scoring_main.get_player_aggregate_evidence(
                    "session_a",
                    "player_a",
                )

                self.assertEqual(result.active_modules, ("godmode",))
                self.assertEqual(result.deferred_modules, ())

                signal = result.signals[0]

                self.assertTrue(signal.history_resolved)
                self.assertEqual(signal.history_total_events, 3)
                self.assertEqual(signal.history_qualifying_events, 3)
                self.assertEqual(signal.history_max_raw_score, 5.0)


if __name__ == "__main__":
    unittest.main()
