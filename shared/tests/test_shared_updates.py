"""0.2 retry policy and optional evidence regression tests."""
import json
import os
import ssl
import threading
import time
from unittest.mock import patch

if __package__:
    from .test_shared import SharedCase, LocalReceiver, event
else:
    from test_shared import SharedCase, LocalReceiver, event
from shared.config import ClientConfig
from shared.errors import ConfigurationError, QueueFullError, ValidationError
from shared.schema import encode_event, decode_event
from shared.transport import DeliveryOutcome, HttpTransport


def wait_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class SharedUpdateTests(SharedCase):
    def test_retry_mode_default_validation_and_env(self):
        self.assertEqual(self.config().retry_mode, "bounded")
        for value in ("unknown", None, True):
            with self.assertRaises(ConfigurationError):
                self.config(retry_mode=value)
        with patch.dict(os.environ, {"GZZ_TELEMETRY_URL": "https://example.test",
                                     "GZZ_TELEMETRY_TOKEN": "test-only",
                                     "GZZ_TELEMETRY_RETRY_MODE": "persistent"}, clear=True):
            self.assertEqual(ClientConfig.from_env().retry_mode, "persistent")

    def test_optional_metadata_round_trip_without_changing_root_or_score(self):
        source = event()
        source["evidence"].update(status="OFFLINE", severity="INFO", window_id=1, sample_id=0,
                                   scan_start_ms=1200, scan_end_ms=1234)
        self.assertEqual(decode_event(encode_event(source)), source)
        self.assertEqual(decode_event(encode_event(event())), event())
        with self.assertRaises(ValidationError):
            encode_event({**source, "status": "OFFLINE"})

    def test_persistent_recovers_beyond_attempt_limit_same_id_and_payload(self):
        calls = []
        class Flaky:
            def send(self, payload, event_id):
                calls.append((payload, event_id))
                return DeliveryOutcome("retry", "http_503") if len(calls) <= 10 else DeliveryOutcome("accepted", "stored")
        client = self.client(self.config(max_attempts=2, retry_mode="persistent"), transport=Flaky())
        source = event(15, 9876)
        receipt = client.send_detection(source)
        self.assertTrue(client.flush(4))
        self.assertEqual(len(calls), 11)
        self.assertEqual({key for _, key in calls}, {receipt.event_id})
        self.assertTrue(all(json.loads(body) == source for body, _ in calls))

    def test_persistent_real_local_http_retries_without_duplicate_storage(self):
        with LocalReceiver(self.root / "receiver", ["503"] * 4 + ["store_then_503"]) as server:
            client = self.client(self.config(server.url, max_attempts=1, retry_mode="persistent"))
            client.send_detection(event())
            self.assertTrue(client.flush(3))
            self.assertEqual(len(server.requests), 6)
            self.assertEqual(len(server.writer.iter_stored()), 1)

    def test_new_events_do_not_bypass_sender_cooldown(self):
        calls = []
        class Offline:
            def send(self, payload, event_id):
                calls.append(event_id)
                return DeliveryOutcome("retry", "network_error")
        client = self.client(self.config(retry_mode="persistent", retry_base_seconds=10, retry_max_seconds=10),
                             transport=Offline())
        client.send_detection(event())
        wait_until(lambda: client.status().last_delivery == "RETRYING")
        for n in range(10):
            client.send_detection(event(timestamp=n))
        time.sleep(0.15)
        self.assertEqual(len(calls), 1)
        self.assertEqual(client.status().pending, 11)
        self.assertTrue(client.close(1))

    def test_persistent_cooldown_and_queue_survive_restart(self):
        class Offline:
            def send(self, payload, event_id):
                return DeliveryOutcome("retry", "network_error")
        config = self.config(retry_mode="persistent", max_attempts=1, retry_base_seconds=0.4, retry_max_seconds=0.4)
        client = self.client(config, transport=Offline())
        receipt = client.send_detection(event(15, 4567))
        wait_until(lambda: client.status().last_delivery == "RETRYING")
        self.assertTrue(client.close())
        self.assertGreater(client._outbox.retry_delay(), 0)
        calls = []
        class Online:
            def send(self, payload, event_id):
                calls.append((json.loads(payload), event_id))
                return DeliveryOutcome("accepted", "stored")
        recovered = self.client(config, transport=Online())
        self.assertTrue(recovered.flush(3))
        self.assertEqual(calls, [(event(15, 4567), receipt.event_id)])

    def test_persistent_never_retries_rejected_or_revives_old_failed(self):
        for code in ("http_401", "http_422", "tls_verification_failed"):
            with self.subTest(code=code):
                calls = []
                class Reject:
                    def send(self, payload, event_id):
                        calls.append(event_id)
                        return DeliveryOutcome("rejected", code)
                config = self.config(outbox_path=self.root / (code + ".sqlite3"), retry_mode="persistent")
                client = self.client(config, transport=Reject())
                client.send_detection(event())
                self.assertFalse(client.flush(2))
                self.assertEqual(len(calls), 1)
                client.close()
                recovered = self.client(config, transport=Reject())
                self.assertFalse(recovered.flush(0.1))
                self.assertEqual(len(calls), 1)
                self.assertEqual(recovered.status().failed, 1)

    def test_persistent_queue_limit_is_still_enforced(self):
        entered, release = threading.Event(), threading.Event()
        class Slow:
            def send(self, payload, event_id):
                entered.set()
                release.wait(2)
                return DeliveryOutcome("retry", "network_error")
        client = self.client(self.config(retry_mode="persistent", max_queue_events=1), transport=Slow())
        self.addCleanup(release.set)
        client.send_detection(event())
        self.assertTrue(entered.wait(2))
        with self.assertRaises(QueueFullError):
            client.send_detection(event(timestamp=999))
        self.assertEqual(client.status().pending, 1)
        release.set()

    def test_direct_certificate_error_is_not_transient(self):
        transport = HttpTransport(self.config("https://example.test"))
        with patch.object(transport._opener, "open", side_effect=ssl.SSLCertVerificationError("test")):
            result = transport.send(b"{}", "test-id")
        self.assertEqual((result.disposition, result.code), ("rejected", "tls_verification_failed"))
