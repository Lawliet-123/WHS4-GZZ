import unittest
from types import SimpleNamespace
from unittest.mock import patch

from anti_esp.shared_transport import SharedEventSink, validate_common_event


class SharedTransportTests(unittest.TestCase):
    @staticmethod
    def event():
        return {
            "session_id": "esp_001",
            "player_id": "player_042",
            "module": "esp",
            "timestamp_ms": 507000,
            "evidence": {"sensor_event_id": "sensor_1"},
            "reasons": ["External process obtained VM_READ access"],
            "raw_score": 2,
        }

    def test_common_event_is_validated_by_shared_02_schema(self):
        event = self.event()
        self.assertEqual(validate_common_event(event), event)

    def test_content_outbox_id_maps_to_stable_shared_uuid(self):
        sent_ids = []

        def fake_send(payload, *, event_id):
            self.assertEqual(payload, self.event())
            sent_ids.append(event_id)
            return SimpleNamespace(event_id=event_id, status="queued")

        with patch("anti_esp.shared_transport.send_detection", side_effect=fake_send):
            sink = SharedEventSink()
            self.assertTrue(sink.queue(self.event(), "a" * 64))
            self.assertTrue(sink.queue(self.event(), "a" * 64))

        self.assertEqual(sent_ids[0], sent_ids[1])
        self.assertRegex(
            sent_ids[0],
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        )


if __name__ == "__main__":
    unittest.main()
