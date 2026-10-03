from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import server.scoring.main as scoring_main
from server.scoring import get_godmode_history_summary


def sample(raw_score: float, timestamp_ms: int, reason: str):
    return {
        "session_id": "session_a",
        "player_id": "player_a",
        "module": "godmode",
        "timestamp_ms": timestamp_ms,
        "evidence": {"invincible": True},
        "reasons": [reason],
        "raw_score": raw_score,
    }


class GodmodeHistorySummaryPublicApiTests(unittest.TestCase):
    def test_reads_all_pages_without_summing_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                for sequence, (score, timestamp, reason) in enumerate(
                    (
                        (2, 1000, "a"),
                        (3, 2000, "b"),
                        (5, 3000, "c"),
                    ),
                    1,
                ):
                    scoring_main.process(
                        sample(score, timestamp, reason),
                        event_id=str(uuid.uuid4()),
                        sequence=sequence,
                    )

                # batch_size=1로 강제로 여러 페이지를 타게 한다.
                result = get_godmode_history_summary(
                    "session_a",
                    "player_a",
                    batch_size=1,
                )

                self.assertEqual(result.total_events, 3)
                self.assertEqual(result.qualifying_events, 3)
                self.assertEqual(result.max_raw_score, 5.0)

                # 2 + 3 + 5 누적값은 API 어디에도 만들지 않는다.
                self.assertFalse(hasattr(result, "total_score"))

    def test_empty_player_returns_empty_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                result = get_godmode_history_summary(
                    "no_session",
                    "no_player",
                )

                self.assertEqual(result.total_events, 0)
                self.assertEqual(result.qualifying_events, 0)
                self.assertIsNone(result.max_raw_score)


if __name__ == "__main__":
    unittest.main()
