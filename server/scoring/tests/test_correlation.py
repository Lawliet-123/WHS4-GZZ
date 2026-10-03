"""탐지기 간 correlation 후보 생성의 보수적 동작을 검증한다."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from server.scoring.correlation import (
    CorrelationObservation,
    find_correlation_candidates,
    observation_from_evaluation,
)
from server.scoring.main import get_player_correlation_candidates
from server.scoring.policies.contract import PolicyAnnotations, PolicyEvaluation
from server.scoring.policy import SignalPreview
from server.scoring.storage import ModuleState


def observation(
    module: str,
    *,
    timestamp_ms: int,
    tags: tuple[str, ...],
    entity_key: str | None = None,
    state: str = "RAW_FRACTION_ONLY",
    session_id: str = "s",
    player_id: str = "p",
    sequence: int = 1,
    timestamp_basis: str | None = None,
) -> CorrelationObservation:
    return CorrelationObservation(
        event_id=str(uuid.uuid4()),
        sequence=sequence,
        session_id=session_id,
        player_id=player_id,
        module=module,
        timestamp_ms=timestamp_ms,
        state=state,
        entity_key=entity_key,
        overlap_tags=tags,
        reasons=(),
        timestamp_basis=timestamp_basis,
    )


def evaluation(module: str, tags: tuple[str, ...], entity_key: str | None = None):
    return PolicyEvaluation(
        SignalPreview(module, 1.0, "snapshot", "RAW_FRACTION_ONLY", 20.0, ()),
        PolicyAnnotations(entity_key=entity_key, overlap_tags=tags),
    )


class CorrelationCandidateTests(unittest.TestCase):
    def test_shared_tag_same_player_and_time_window_creates_candidate(self):
        left = observation("noclip", timestamp_ms=1000, tags=("noclip_behavior",), sequence=1)
        right = observation("noclip_runtime", timestamp_ms=1300, tags=("noclip_behavior",), sequence=2)

        result = find_correlation_candidates([left, right], max_time_distance_ms=500)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].modules, ("noclip", "noclip_runtime"))
        self.assertEqual(result[0].overlap_tags, ("noclip_behavior",))
        self.assertEqual(result[0].time_distance_ms, 300)

    def test_local_session_start_is_not_compared_with_other_detector_clock(self):
        hide = observation(
            "hide_anywhere",
            timestamp_ms=1000,
            tags=("hide_anywhere_injection",),
            sequence=1,
            timestamp_basis="local_session_start",
        )
        injection = observation(
            "injection",
            timestamp_ms=1100,
            tags=("hide_anywhere_injection",),
            sequence=2,
        )

        self.assertEqual(
            find_correlation_candidates(
                [hide, injection],
                max_time_distance_ms=5000,
            ),
            [],
        )

    def test_different_player_tag_or_far_time_does_not_correlate(self):
        base = observation("noclip", timestamp_ms=1000, tags=("same",), sequence=1)
        cases = (
            observation("noclip_runtime", timestamp_ms=1100, tags=("same",), player_id="other", sequence=2),
            observation("noclip_runtime", timestamp_ms=1100, tags=("different",), sequence=2),
            observation("noclip_runtime", timestamp_ms=2001, tags=("same",), sequence=2),
        )
        for other in cases:
            with self.subTest(other=other):
                self.assertEqual(
                    find_correlation_candidates([base, other], max_time_distance_ms=1000),
                    [],
                )

    def test_unavailable_measurement_is_not_candidate(self):
        left = observation("noclip", timestamp_ms=1000, tags=("same",), sequence=1)
        failed = observation(
            "noclip_runtime",
            timestamp_ms=1100,
            tags=("same",),
            state="MEASUREMENT_UNAVAILABLE",
            sequence=2,
        )
        self.assertEqual(
            find_correlation_candidates([left, failed], max_time_distance_ms=1000),
            [],
        )

    def test_same_module_is_left_to_storage_dedup_not_correlation(self):
        left = observation("noclip", timestamp_ms=1000, tags=("same",), sequence=1)
        right = observation("noclip", timestamp_ms=1100, tags=("same",), sequence=2)
        self.assertEqual(
            find_correlation_candidates([left, right], max_time_distance_ms=1000),
            [],
        )

    def test_different_entity_keys_are_recorded_but_not_auto_rejected(self):
        left = observation(
            "autopaint", timestamp_ms=1000, tags=("process_injection",),
            entity_key="pid:10", sequence=1,
        )
        right = observation(
            "injection", timestamp_ms=1050, tags=("process_injection",),
            entity_key="target:game", sequence=2,
        )
        result = find_correlation_candidates([left, right], max_time_distance_ms=100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].entity_keys, ("pid:10", "target:game"))
        self.assertTrue(any("namespace" in reason for reason in result[0].reasons))

    def test_window_must_be_nonnegative_integer(self):
        item = observation("noclip", timestamp_ms=1000, tags=("same",))
        for bad in (-1, 1.5, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    find_correlation_candidates([item], max_time_distance_ms=bad)

    def test_observation_builder_checks_module_identity(self):
        event = {
            "session_id": "s", "player_id": "p", "module": "noclip",
            "timestamp_ms": 1000, "evidence": {}, "reasons": [], "raw_score": 0,
        }
        wrong = evaluation("aimbot", ())
        with self.assertRaises(ValueError):
            observation_from_evaluation(
                event,
                event_id=str(uuid.uuid4()),
                sequence=1,
                evaluation=wrong,
            )

    def test_observation_builder_preserves_timestamp_basis(self):
        event = {
            "session_id": "s",
            "player_id": "p",
            "module": "hide_anywhere",
            "timestamp_ms": 1000,
            "evidence": {"timestamp_basis": "local_session_start"},
            "reasons": [],
            "raw_score": 3,
        }

        result = observation_from_evaluation(
            event,
            event_id=str(uuid.uuid4()),
            sequence=1,
            evaluation=evaluation(
                "hide_anywhere",
                ("hide_anywhere_injection",),
            ),
        )

        self.assertEqual(
            result.timestamp_basis,
            "local_session_start",
        )

    @patch("server.scoring.main.evaluate_registered_policy")
    @patch("server.scoring.main.get_player_snapshot")
    def test_public_snapshot_api_uses_registry_annotations(self, get_snapshot, evaluate_policy):
        first_id = str(uuid.uuid4())
        second_id = str(uuid.uuid4())
        get_snapshot.return_value = [
            ModuleState("s", "p", "noclip", 1000, 10, first_id, 3.0, {}, ["a"]),
            ModuleState("s", "p", "noclip_runtime", 1200, 11, second_id, 1.0, {}, ["b"]),
        ]
        evaluate_policy.side_effect = [
            evaluation("noclip", ("noclip_behavior",)),
            evaluation("noclip_runtime", ("noclip_behavior",)),
        ]

        result = get_player_correlation_candidates(
            "s", "p", max_time_distance_ms=500
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].event_ids, (first_id, second_id))
        self.assertEqual(result[0].sequences, (10, 11))


if __name__ == "__main__":
    unittest.main()
