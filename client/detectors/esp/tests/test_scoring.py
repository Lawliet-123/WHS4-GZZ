import json
import unittest

from anti_esp.models import EvidenceEvent, ScoreSnapshot
from anti_esp.scoring import CategoryPolicy, SuspicionEngine


class EvidenceModelTests(unittest.TestCase):
    def test_event_json_round_trip_and_confidence_alias(self) -> None:
        event = EvidenceEvent(
            category="memory_read",
            strength=0.8,
            confidence=0.95,
            source="sysmon",
            timestamp=1234.5,
            reason="game process opened",
            details={"pid": 42, "access": "0x1fffff", "label": "테스트"},
            dedup_key="42:game",
            session_id="match-1",
            subject_id="player-1",
        )

        restored = EvidenceEvent.from_json(event.to_json())

        self.assertEqual(restored.to_dict(), event.to_dict())
        self.assertEqual(restored.reliability, 0.95)
        self.assertEqual(restored.confidence, 0.95)
        json.dumps(restored.to_dict(), allow_nan=False)

    def test_event_rejects_non_json_details(self) -> None:
        with self.assertRaises(TypeError):
            EvidenceEvent(category="overlay", details={"bad": {1, 2}})

    def test_snapshot_alias_and_round_trip(self) -> None:
        snapshot = ScoreSnapshot(
            evaluated_at=100.0,
            suspicion=42.5,
            observation_confidence=80.0,
            status="REVIEW",
            category_scores={"memory_read": 42.5},
            reasons=("reason",),
        )
        restored = ScoreSnapshot.from_json(snapshot.to_json())
        self.assertEqual(restored.suspicion_score, 42.5)
        self.assertEqual(restored.to_dict(), snapshot.to_dict())


class SuspicionEngineTests(unittest.TestCase):
    def make_engine(self) -> SuspicionEngine:
        return SuspicionEngine(
            {"memory_read": CategoryPolicy(20.0, 35.0, 60.0, 10.0, 10)},
            minimum_observation_confidence=60.0,
            review_threshold=35.0,
            high_review_threshold=70.0,
        )

    def test_deduplication_cap_and_time_window_are_applied(self) -> None:
        engine = self.make_engine()
        engine.add_events(
            [
                EvidenceEvent(
                    "memory_read", timestamp=0.0, source="sysmon", dedup_key="old"
                ),
                EvidenceEvent(
                    "memory_read", timestamp=100.0, source="sysmon", dedup_key="a"
                ),
                EvidenceEvent(
                    "memory_read", timestamp=105.0, source="sysmon", dedup_key="a"
                ),
                EvidenceEvent(
                    "memory_read", timestamp=120.0, source="sysmon", dedup_key="a"
                ),
                EvidenceEvent(
                    "memory_read", timestamp=125.0, source="sysmon", dedup_key="b"
                ),
            ]
        )

        snapshot = engine.score(now=130.0, sensor_coverage={"sysmon": True})

        self.assertEqual(snapshot.suspicion, 35.0)
        self.assertEqual(snapshot.category_scores, {"memory_read": 35.0})
        self.assertEqual(snapshot.active_event_count, 4)
        self.assertEqual(snapshot.accepted_event_count, 3)
        self.assertEqual(snapshot.suppressed_event_count, 1)
        self.assertEqual(snapshot.expired_event_count, 1)
        self.assertEqual(snapshot.status, "REVIEW")

    def test_event_reliability_changes_points_not_sensor_coverage(self) -> None:
        engine = self.make_engine()
        engine.add_event(
            EvidenceEvent(
                "memory_read",
                strength=0.5,
                reliability=0.5,
                timestamp=10.0,
                source="sysmon",
            )
        )

        snapshot = engine.score(now=10.0, sensor_coverage={"sysmon": True})

        self.assertEqual(snapshot.suspicion, 5.0)
        self.assertEqual(snapshot.observation_confidence, 100.0)
        self.assertEqual(snapshot.status, "LOW")

    def test_default_exact_memory_read_reaches_review_not_high(self) -> None:
        engine = SuspicionEngine()
        engine.add_event(
            EvidenceEvent(
                "memory_read",
                strength=0.88,
                reliability=0.95,
                timestamp=10.0,
                source="sysmon:event-10",
            )
        )

        snapshot = engine.score(now=10.0, sensor_coverage={"sysmon": True})

        self.assertGreaterEqual(snapshot.suspicion, engine.review_threshold)
        self.assertLess(snapshot.suspicion, engine.high_review_threshold)
        self.assertEqual(snapshot.status, "REVIEW")

    def test_low_observation_confidence_forces_insufficient_status(self) -> None:
        engine = self.make_engine()
        engine.add_events(
            [
                EvidenceEvent(
                    "memory_read", timestamp=10.0, source="sysmon", dedup_key="a"
                ),
                EvidenceEvent(
                    "memory_read", timestamp=20.0, source="sysmon", dedup_key="b"
                ),
            ]
        )

        snapshot = engine.score(
            now=20.0,
            sensor_coverage={"sysmon": True, "overlay": False},
        )

        self.assertEqual(snapshot.suspicion, 35.0)
        self.assertEqual(snapshot.observation_confidence, 50.0)
        self.assertEqual(snapshot.status, "INSUFFICIENT")

    def test_sensor_state_becomes_stale(self) -> None:
        engine = SuspicionEngine(
            expected_sensors=("sysmon", "overlay"), sensor_ttl_seconds=10.0
        )
        engine.set_sensor_state("sysmon", True, timestamp=100.0)
        engine.set_sensor_state("overlay", 0.8, timestamp=100.0)

        fresh = engine.score(now=105.0)
        stale = engine.score(now=111.0)

        self.assertEqual(fresh.observation_confidence, 90.0)
        self.assertEqual(fresh.status, "LOW")
        self.assertEqual(stale.observation_confidence, 0.0)
        self.assertEqual(stale.status, "INSUFFICIENT")

    def test_unknown_category_is_excluded_transparently(self) -> None:
        engine = self.make_engine()
        engine.add_event(EvidenceEvent("unknown", timestamp=10.0))

        snapshot = engine.snapshot(now=10.0, observation_confidence=100.0)

        self.assertEqual(snapshot.suspicion, 0.0)
        self.assertTrue(any("unsupported" in reason for reason in snapshot.reasons))

    def test_score_prunes_expired_and_unsupported_events_from_memory(self) -> None:
        engine = self.make_engine()
        engine.add_events(
            [
                EvidenceEvent(
                    "memory_read", timestamp=0.0, source="sysmon", dedup_key="old"
                ),
                EvidenceEvent(
                    "unknown", timestamp=100.0, source="test", dedup_key="unknown"
                ),
                EvidenceEvent(
                    "memory_read", timestamp=100.0, source="sysmon", dedup_key="live"
                ),
            ]
        )

        snapshot = engine.score(now=120.0, observation_confidence=100.0)

        self.assertEqual(snapshot.expired_event_count, 1)
        self.assertEqual(len(engine.events()), 1)
        self.assertEqual(engine.events()[0].dedup_key, "live")

    def test_active_event_buffer_is_bounded_before_scoring(self) -> None:
        engine = SuspicionEngine()
        engine.add_events(
            EvidenceEvent(
                "memory_read",
                timestamp=float(index),
                source="sysmon",
                dedup_key=f"source-{index}",
            )
            for index in range(1_000)
        )

        buffered = engine.events()
        self.assertLessEqual(len(buffered), 80)
        self.assertEqual(buffered[-1].timestamp, 999.0)

    def test_coverage_and_confidence_cannot_both_be_provided(self) -> None:
        engine = self.make_engine()
        with self.assertRaises(ValueError):
            engine.score(
                now=1.0,
                sensor_coverage={"sysmon": True},
                observation_confidence=100.0,
            )


if __name__ == "__main__":
    unittest.main()
