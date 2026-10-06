from __future__ import annotations

import unittest
import uuid

from server.scoring.player_snapshot import build_player_policy_snapshot
from server.scoring.storage import ModuleState


def state(
    module: str,
    *,
    timestamp_ms: int,
    raw_score: float = 0,
    sequence: int = 1,
    evidence=None,
) -> ModuleState:
    return ModuleState(
        session_id="s",
        player_id="p",
        module=module,
        timestamp_ms=timestamp_ms,
        sequence=sequence,
        event_id=str(uuid.uuid4()),
        raw_score=raw_score,
        evidence=evidence or {"status": "NORMAL"},
        reasons=[],
    )


class MeasurementStateSemanticsTests(unittest.TestCase):
    def test_missing_expected_module_is_reported_not_invented_as_normal(self):
        result = build_player_policy_snapshot(
            [
                state(
                    "noclip",
                    timestamp_ms=1000,
                )
            ],
            session_id="s",
            player_id="p",
            expected_modules=("noclip", "noclip_runtime"),
        )

        self.assertEqual(result.missing_modules, ("noclip_runtime",))
        self.assertEqual(result.stale_modules, ())

    def test_stale_module_is_reported_separately_from_missing(self):
        result = build_player_policy_snapshot(
            [
                state(
                    "noclip",
                    timestamp_ms=1000,
                )
            ],
            session_id="s",
            player_id="p",
            expected_modules=("noclip",),
            observed_at_ms=5000,
            max_age_ms=2000,
        )

        self.assertEqual(result.missing_modules, ())
        self.assertEqual(result.stale_modules, ("noclip",))

    def test_fresh_module_is_not_stale(self):
        result = build_player_policy_snapshot(
            [
                state(
                    "noclip",
                    timestamp_ms=4000,
                )
            ],
            session_id="s",
            player_id="p",
            expected_modules=("noclip",),
            observed_at_ms=5000,
            max_age_ms=2000,
        )

        self.assertEqual(result.missing_modules, ())
        self.assertEqual(result.stale_modules, ())


if __name__ == "__main__":
    unittest.main()
