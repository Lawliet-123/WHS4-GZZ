from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from anti_esp.models import EvidenceEvent
from anti_esp.store import SQLiteEvidenceStore


class SQLiteEvidenceStoreTests(unittest.TestCase):
    def test_append_duplicate_and_recent_filters(self) -> None:
        with SQLiteEvidenceStore() as store:
            first = EvidenceEvent(
                "memory_read",
                timestamp=10.0,
                source="sysmon",
                session_id="match-a",
                subject_id="player-a",
                details={"pid": 10, "text": "한글"},
            )
            second = EvidenceEvent(
                "overlay",
                timestamp=20.0,
                source="window-sensor",
                session_id="match-b",
                subject_id="player-b",
            )

            self.assertTrue(store.append(first))
            self.assertFalse(store.append(first))
            self.assertEqual(store.append_many([second]), 1)
            self.assertEqual(store.count(), 2)

            recent = store.recent(limit=10)
            self.assertEqual([event.event_id for event in recent], [second.event_id, first.event_id])
            self.assertEqual(recent[1].details, first.details)
            self.assertEqual(
                [event.event_id for event in store.recent(10, category="overlay")],
                [second.event_id],
            )
            self.assertEqual(
                [event.event_id for event in store.recent(10, session_id="match-a")],
                [first.event_id],
            )
            self.assertEqual(
                [event.event_id for event in store.recent(10, since=15.0)],
                [second.event_id],
            )

    def test_jsonl_export_is_chronological_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "evidence.db"
            output = Path(directory) / "export" / "events.jsonl"
            with SQLiteEvidenceStore(database) as store:
                later = EvidenceEvent("overlay", timestamp=20.0, source="window")
                earlier = EvidenceEvent(
                    "memory_read", timestamp=10.0, source="sysmon", reason="opened"
                )
                store.append_many([later, earlier])

                exported = store.export_jsonl(output)

            self.assertEqual(exported, 2)
            lines = output.read_text(encoding="utf-8").splitlines()
            events = [EvidenceEvent.from_dict(json.loads(line)) for line in lines]
            self.assertEqual([event.event_id for event in events], [earlier.event_id, later.event_id])

    def test_one_store_instance_is_safe_across_threads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteEvidenceStore(Path(directory) / "threaded.db") as store:
                events = [
                    EvidenceEvent(
                        "memory_read",
                        timestamp=float(index),
                        source="sysmon",
                        details={"pid": index},
                    )
                    for index in range(100)
                ]
                with ThreadPoolExecutor(max_workers=8) as executor:
                    inserted = list(executor.map(store.append, events))

                self.assertTrue(all(inserted))
                self.assertEqual(store.count(), 100)
                self.assertEqual(len(store.recent(limit=100)), 100)

    def test_closed_store_rejects_operations(self) -> None:
        store = SQLiteEvidenceStore()
        store.close()
        with self.assertRaises(RuntimeError):
            store.count()

    def test_outbox_factory_failure_rolls_back_staged_evidence(self) -> None:
        event = EvidenceEvent(
            "memory_read", source="sysmon", session_id="esp_001"
        )
        with SQLiteEvidenceStore() as store:
            def fail(_events):
                raise ValueError("could not build team event")

            with self.assertRaisesRegex(ValueError, "could not build team event"):
                store.append_many_with_outbox((event,), fail)

            self.assertEqual(store.count(), 0)

            payload = json.dumps(
                {
                    "session_id": "esp_001",
                    "player_id": "player_042",
                    "module": "esp",
                    "timestamp_ms": 1,
                    "evidence": {},
                    "reasons": ["test"],
                    "raw_score": 1,
                }
            )
            inserted, outbox_id = store.append_many_with_outbox(
                (event,), lambda events: ("esp_001", payload) if events else None
            )
            self.assertEqual(inserted, (event,))
            self.assertIsNotNone(outbox_id)
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.pending_team_event_count(session_id="esp_001"), 1)
            pending = store.pending_team_events(session_id="esp_001")
            self.assertEqual(pending, [(outbox_id, payload)])
            self.assertTrue(store.mark_team_event_delivered(outbox_id))
            self.assertEqual(store.pending_team_event_count(session_id="esp_001"), 0)


if __name__ == "__main__":
    unittest.main()
