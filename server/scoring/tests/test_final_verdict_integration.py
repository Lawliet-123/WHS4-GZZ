from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import server.scoring.main as scoring_main
from server.scoring import (
    FinalVerdict,
    get_player_final_verdict,
)


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


class FinalVerdictIntegrationTests(unittest.TestCase):
    def test_godmode_reaches_suspicious_final_verdict(self):
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

                result = get_player_final_verdict(
                    "session_a",
                    "player_a",
                )

                self.assertIsInstance(
                    result,
                    FinalVerdict,
                )

                self.assertEqual(
                    result.status,
                    "SUSPICIOUS",
                )

                # Godmode event 3건의 점수를 합산하지 않고
                # 최종 evidence unit 하나로 반영한다.
                self.assertEqual(
                    result.evidence_unit_count,
                    1,
                )

                self.assertEqual(
                    result.active_modules,
                    ("godmode",),
                )

    def test_empty_player_is_not_labeled_cheat(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(scoring_main, "_store", None):
                scoring_main.configure_scoring(
                    Path(tmp) / "scoring.sqlite3"
                )

                result = get_player_final_verdict(
                    "empty-session",
                    "empty-player",
                )

                self.assertEqual(
                    result.status,
                    "NO_ACTIVE_EVIDENCE",
                )


if __name__ == "__main__":
    unittest.main()
