from __future__ import annotations

import unittest

from server.scoring.history_summary import summarize_godmode_history
from server.scoring.storage import DeltaEvent


def event(
    sequence: int,
    raw_score: float,
    timestamp_ms: int,
    *,
    reason: str,
    session_id: str = "session_a",
    player_id: str = "player_a",
    module: str = "godmode",
) -> DeltaEvent:
    return DeltaEvent(
        event_id=f"00000000-0000-0000-0000-{sequence:012d}",
        sequence=sequence,
        session_id=session_id,
        player_id=player_id,
        module=module,
        timestamp_ms=timestamp_ms,
        raw_score=raw_score,
        evidence={"invincible": True},
        reasons=[reason],
    )


class GodmodeHistorySummaryTests(unittest.TestCase):
    def test_empty_history(self):
        result = summarize_godmode_history(
            [],
            session_id="session_a",
            player_id="player_a",
        )

        self.assertEqual(result.total_events, 0)
        self.assertEqual(result.qualifying_events, 0)
        self.assertEqual(result.threshold, 2.0)
        self.assertIsNone(result.max_raw_score)
        self.assertIsNone(result.first_timestamp_ms)
        self.assertIsNone(result.last_timestamp_ms)

    def test_2_3_5_are_not_summed(self):
        result = summarize_godmode_history(
            [
                event(1, 2, 1000, reason="abnormal persistence"),
                event(2, 3, 2000, reason="extended persistence"),
                event(3, 5, 3000, reason="extreme persistence"),
            ],
            session_id="session_a",
            player_id="player_a",
        )

        self.assertEqual(result.total_events, 3)
        self.assertEqual(result.qualifying_events, 3)

        # 2 + 3 + 5 = 10 같은 누적 점수는 만들지 않는다.
        self.assertEqual(result.max_raw_score, 5.0)

        self.assertEqual(result.first_timestamp_ms, 1000)
        self.assertEqual(result.last_timestamp_ms, 3000)
        self.assertEqual(result.first_sequence, 1)
        self.assertEqual(result.last_sequence, 3)

    def test_below_threshold_event_is_preserved_but_not_qualified(self):
        result = summarize_godmode_history(
            [
                event(1, 1, 1000, reason="weak"),
                event(2, 2, 2000, reason="qualified"),
            ],
            session_id="session_a",
            player_id="player_a",
        )

        self.assertEqual(result.total_events, 2)
        self.assertEqual(result.qualifying_events, 1)
        self.assertEqual(result.max_raw_score, 2.0)

    def test_timestamp_order_is_independent_from_server_sequence(self):
        result = summarize_godmode_history(
            [
                event(1, 2, 5000, reason="newer game time"),
                event(2, 3, 1000, reason="late old event"),
            ],
            session_id="session_a",
            player_id="player_a",
        )

        self.assertEqual(result.first_sequence, 1)
        self.assertEqual(result.last_sequence, 2)

        self.assertEqual(result.first_timestamp_ms, 1000)
        self.assertEqual(result.last_timestamp_ms, 5000)

    def test_reasons_are_deduplicated(self):
        result = summarize_godmode_history(
            [
                event(1, 2, 1000, reason="same"),
                event(2, 2, 2000, reason="same"),
                event(3, 3, 3000, reason="other"),
            ],
            session_id="session_a",
            player_id="player_a",
        )

        self.assertEqual(result.reasons, ("other", "same"))

    def test_rejects_other_module(self):
        with self.assertRaises(ValueError):
            summarize_godmode_history(
                [
                    event(
                        1,
                        2,
                        1000,
                        reason="wrong",
                        module="noclip",
                    )
                ],
                session_id="session_a",
                player_id="player_a",
            )

    def test_rejects_other_player(self):
        with self.assertRaises(ValueError):
            summarize_godmode_history(
                [
                    event(
                        1,
                        2,
                        1000,
                        reason="wrong player",
                        player_id="other",
                    )
                ],
                session_id="session_a",
                player_id="player_a",
            )


if __name__ == "__main__":
    unittest.main()
