from __future__ import annotations

import unittest

from server.scoring.external_access_summary import (
    summarize_external_access_history,
)
from server.scoring.storage import ExternalAccessEvent


def item(
    submodule: str,
    *,
    sequence: int,
    timestamp_ms: int,
    raw_score: float,
    status: str,
    measurement_valid=None,
):
    evidence = {
        "submodule": submodule,
        "status": status,
    }

    if measurement_valid is not None:
        evidence["measurement_valid"] = measurement_valid

    return ExternalAccessEvent(
        event_id=f"event-{sequence}",
        sequence=sequence,
        session_id="session_1",
        player_id="player_1",
        module="external_access",
        submodule=submodule,
        timestamp_ms=timestamp_ms,
        raw_score=raw_score,
        evidence=evidence,
        reasons=[],
    )


class ExternalAccessSummaryTests(unittest.TestCase):

    def test_module_integrity_normal_zero_does_not_erase_positive_history(self):
        summary = summarize_external_access_history(
            [
                item(
                    "module_integrity",
                    sequence=1,
                    timestamp_ms=1000,
                    raw_score=2,
                    status="SUSPICIOUS",
                ),
                item(
                    "module_integrity",
                    sequence=2,
                    timestamp_ms=2000,
                    raw_score=0,
                    status="NORMAL",
                ),
            ],
            session_id="session_1",
            player_id="player_1",
            submodule="module_integrity",
        )

        self.assertEqual(summary.positive_events, 1)
        self.assertEqual(summary.max_positive_raw_score, 2)
        self.assertTrue(summary.has_positive_history)

        self.assertEqual(summary.latest_raw_score, 0)
        self.assertEqual(summary.latest_status, "NORMAL")
        self.assertTrue(summary.latest_is_normal_zero)

    def test_same_scan_multiple_external_process_positives_keep_max(self):
        summary = summarize_external_access_history(
            [
                item(
                    "external_process",
                    sequence=1,
                    timestamp_ms=1000,
                    raw_score=8,
                    status="SUSPICIOUS",
                ),
                item(
                    "external_process",
                    sequence=2,
                    timestamp_ms=1000,
                    raw_score=3,
                    status="SUSPICIOUS",
                ),
            ],
            session_id="session_1",
            player_id="player_1",
            submodule="external_process",
        )

        # 같은 scan의 마지막 Event만 보면 3점이지만
        # history에는 두 건 모두 남고 최대 양수는 8점이다.
        self.assertEqual(summary.total_events, 2)
        self.assertEqual(summary.positive_events, 2)
        self.assertEqual(summary.max_positive_raw_score, 8)

        self.assertEqual(summary.latest_sequence, 2)
        self.assertEqual(summary.latest_raw_score, 3)

    def test_error_zero_is_not_normal_zero(self):
        summary = summarize_external_access_history(
            [
                item(
                    "external_process",
                    sequence=1,
                    timestamp_ms=1000,
                    raw_score=0,
                    status="ERROR",
                ),
            ],
            session_id="session_1",
            player_id="player_1",
            submodule="external_process",
        )

        self.assertEqual(summary.available_events, 0)
        self.assertEqual(summary.unavailable_events, 1)
        self.assertFalse(summary.latest_measurement_available)
        self.assertFalse(summary.latest_is_normal_zero)

    def test_newer_game_timestamp_wins_over_later_server_sequence(self):
        summary = summarize_external_access_history(
            [
                item(
                    "external_process",
                    sequence=1,
                    timestamp_ms=5000,
                    raw_score=8,
                    status="SUSPICIOUS",
                ),
                item(
                    "external_process",
                    sequence=2,
                    timestamp_ms=1000,
                    raw_score=0,
                    status="NORMAL",
                ),
            ],
            session_id="session_1",
            player_id="player_1",
            submodule="external_process",
        )

        self.assertEqual(summary.latest_sequence, 1)
        self.assertEqual(summary.latest_timestamp_ms, 5000)
        self.assertEqual(summary.latest_raw_score, 8)


if __name__ == "__main__":
    unittest.main()
