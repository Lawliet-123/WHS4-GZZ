from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from server.scoring.aggregate import build_aggregate_evidence
from server.scoring.fusion import build_fusion_plan
from server.scoring.player_snapshot import build_player_policy_snapshot
from server.scoring.risk_input import build_player_risk_input
from server.scoring.storage import ScoringStore


class FilesystemRepeatAdvisoryTests(unittest.TestCase):
    def test_repeated_dumper7_observations_do_not_become_independent_incidents(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ScoringStore(Path(tmp) / "scoring.sqlite3")

            # 같은 Dumper-7 흔적이 30점으로 네 번 반복 관측되는 상황.
            for sequence in range(1, 5):
                payload = {
                    "session_id": "session_dumper7",
                    "player_id": "player_1",
                    "module": "filesystem",
                    "timestamp_ms": sequence * 1000,
                    "evidence": {
                        "path": r"C:\Dumper-7",
                    },
                    "reasons": ["Dumper-7 artifact observed"],
                    "raw_score": 30,
                }

                store.process_event(
                    payload,
                    event_id=str(uuid.uuid4()),
                    sequence=sequence,
                )

            # 반복 30점이 120점으로 합산되지 않는다.
            states = store.get_player_snapshot(
                "session_dumper7",
                "player_1",
            )

            self.assertEqual(len(states), 1)
            self.assertEqual(states[0].module, "filesystem")
            self.assertEqual(states[0].raw_score, 30)
            self.assertEqual(states[0].sequence, 4)

            # 중앙 Scoring에서도 filesystem은 advisory 1개로만 해석한다.
            snapshot = build_player_policy_snapshot(
                states,
                session_id="session_dumper7",
                player_id="player_1",
            )
            risk_input = build_player_risk_input(snapshot)

            self.assertEqual(len(risk_input.signals), 1)
            self.assertEqual(
                risk_input.signals[0].calibration_mode,
                "advisory",
            )
            self.assertIsNone(
                risk_input.signals[0].threshold_met,
            )

            aggregate = build_aggregate_evidence(risk_input)

            self.assertEqual(
                aggregate.advisory_modules,
                ("filesystem",),
            )
            self.assertEqual(aggregate.active_modules, ())

            # 따라서 반복 관측이 독립 ACTIVE evidence unit들로 늘어나지 않는다.
            fusion = build_fusion_plan(aggregate)

            self.assertEqual(fusion.active_modules, ())
            self.assertEqual(fusion.independent_modules, ())
            self.assertEqual(fusion.units, ())


if __name__ == "__main__":
    unittest.main()
