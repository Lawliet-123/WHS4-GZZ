"""Portable shared contract tests; no game, UE4SS, cloud service or real key needed."""
from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from shared import logger
from shared.client import DetectionClient
from shared.config import ClientConfig, WriterConfig
from shared.errors import (ClientClosedError, ConfigurationError, EventAlreadyFailedError, IdempotencyConflict,
                           QueueFullError, ResourceBusyError, StorageError, ValidationError)
from shared.schema import EVENT_FIELDS, decode_event, encode_event
from shared.storage import DetectionWriter
from shared.transport import DeliveryOutcome, HttpTransport, _retry_after


def event(score=0, timestamp=1234):
    return {"session_id": "normal_001", "player_id": "player_042", "module": "autopaint",
            "timestamp_ms": timestamp, "evidence": {"behavior_score": score, "behavior_valid": 1},
            "reasons": [] if score == 0 else ["Test signal"], "raw_score": score}


class LocalReceiver:
    """Test adapter only. Production auth, identities and scoring belong to A/B."""
    def __init__(self, root, actions=()):
        self.writer = DetectionWriter(WriterConfig(root))
        self.actions = list(actions)
        self.requests = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                key = self.headers.get("Idempotency-Key")
                parent.requests.append((key, data, dict(self.headers)))
                action = parent.actions.pop(0) if parent.actions else "normal"
                status, headers, ack = 200, {}, None
                if self.headers.get("Authorization") != "Bearer test-token":
                    status = 401
                elif self.path != "/api/detection":
                    status = 404
                elif action == "redirect":
                    status, headers = 307, {"Location": "/api/detection"}
                elif action in {"401", "422", "429", "503"}:
                    status = int(action)
                    headers["Retry-After"] = "0"
                elif action == "wrong_ack":
                    ack = {"status": "stored", "event_id": str(uuid.uuid4())}
                else:
                    try:
                        ack = parent.writer.write_detection(decode_event(data), event_id=key).to_ack()
                    except IdempotencyConflict:
                        status = 409
                    except ValidationError:
                        status = 422
                    if action == "store_then_503":
                        status = 503
                    if action == "slow":
                        time.sleep(0.12)
                body = json.dumps(ack or {"error": "test_error"}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class SharedCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gzz-shared-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def config(self, url="http://127.0.0.1:9", **changes):
        values = dict(server_url=url, api_token="test-token", outbox_path=self.root / "queue.sqlite3",
                      allow_insecure_loopback=True, retry_base_seconds=0.01, retry_max_seconds=0.02,
                      retry_jitter_ratio=0, max_attempts=3, timeout_seconds=0.3)
        values.update(changes)
        return ClientConfig(**values)

    def client(self, config=None, **kwargs):
        client = DetectionClient(config or self.config(), **kwargs)
        self.addCleanup(client.close)
        return client


class SchemaTests(SharedCase):
    def test_preserves_seven_fields_zero_and_timestamp(self):
        source = event()
        result = decode_event(encode_event(source))
        self.assertEqual(source, result)
        self.assertEqual(set(result), EVENT_FIELDS)

    def test_nested_evidence_and_unicode_are_allowed(self):
        source = event(15)
        source["evidence"] = {"설명": "검증", "flag": False, "nested": [None, 1.25, {"x": 0}]}
        self.assertEqual(decode_event(encode_event(source)), source)

    def test_schema_rejects_invalid_fields_types_numbers_and_paths(self):
        cases = []
        for field, value in (("session_id", "../oops"), ("player_id", "CON"), ("module", "abc."),
                             ("module", "a/b"), ("timestamp_ms", True), ("timestamp_ms", -1),
                             ("timestamp_ms", 1.5), ("raw_score", float("nan")),
                             ("raw_score", float("inf")), ("raw_score", True),
                             ("raw_score", -1), ("evidence", []), ("reasons", "text"),
                             ("reasons", [1])):
            item = event()
            item[field] = value
            cases.append(item)
        extra = event()
        extra["window_id"] = 0
        cases.append(extra)
        missing = event()
        missing.pop("module")
        cases.append(missing)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValidationError):
                encode_event(item)

    def test_rejects_duplicate_keys_and_truncated_json(self):
        for payload in (b'{"module":"a","module":"b"}', b'{"session_id":', b'\xff'):
            with self.assertRaises(ValidationError):
                decode_event(payload)

    def test_rejects_oversized_deep_and_cyclic_evidence(self):
        with self.assertRaises(ValidationError):
            encode_event(event(), max_bytes=10)
        item = event()
        item["evidence"]["cycle"] = item
        with self.assertRaises(ValidationError):
            encode_event(item)


class ConfigTests(SharedCase):
    def test_https_default_and_explicit_loopback_exception(self):
        for url in ("http://example.com", "http://192.168.0.2", "file:///tmp/x", "https://u:p@example.com",
                    "https://example.com/?key=a", "https://example.com/prefix", "https://example.com:bad"):
            with self.subTest(url=url), self.assertRaises(ConfigurationError):
                self.config(url)
        with self.assertRaises(ConfigurationError):
            self.config(allow_insecure_loopback=False)
        self.assertEqual(self.config("https://example.com").endpoint, "https://example.com/api/detection")

    def test_invalid_limits_token_header_injection(self):
        for changes in (dict(max_attempts=0), dict(timeout_seconds=float("nan")),
                        dict(max_queue_events=True), dict(retry_jitter_ratio=float("nan")),
                        dict(api_token=""), dict(api_token="secret\r\nX-Bad: yes"),
                        dict(detection_path="//evil.test/api")):
            with self.subTest(changes=changes), self.assertRaises(ConfigurationError):
                self.config(**changes)

    def test_environment_explicit_and_token_not_in_repr(self):
        with patch.dict(os.environ, {"GZZ_TELEMETRY_URL": "https://example.com", "GZZ_TELEMETRY_TOKEN": "never-log-me"}, clear=True):
            config = ClientConfig.from_env()
            self.assertNotIn("never-log-me", repr(config))
            self.assertEqual(config.api_token, "never-log-me")

    def test_import_has_no_files_network_threads_or_environment_requirements(self):
        package_root = str(Path(logger.__file__).resolve().parents[1])
        program = "import os,threading; before=set(os.listdir('.')); import shared.logger; assert set(os.listdir('.')) == before; assert len(threading.enumerate()) == 1"
        result = subprocess.run([sys.executable, "-c", program], cwd=self.root,
                                env={**os.environ, "PYTHONPATH": package_root, "PYTHONDONTWRITEBYTECODE": "1"},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class WriterTests(SharedCase):
    def writer(self):
        return DetectionWriter(WriterConfig(self.root / "records"))

    def test_store_zero_duplicate_and_conflict(self):
        writer, key = self.writer(), str(uuid.uuid4())
        receipt = writer.write_detection(event(), event_id=key)
        self.assertEqual(receipt.status, "stored")
        duplicate = writer.write_detection(event(), event_id=key)
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(receipt.sequence, duplicate.sequence)
        self.assertEqual(len(receipt.path.read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(json.loads(receipt.path.read_text(encoding="utf-8")), event())
        with self.assertRaises(IdempotencyConflict):
            writer.write_detection(event(15), event_id=key)

    def test_identical_score_at_different_times_is_not_deduplicated(self):
        writer = self.writer()
        for timestamp in (1000, 2000, 3000):
            writer.write_detection(event(15, timestamp), event_id=str(uuid.uuid4()))
        feed = writer.iter_stored()
        self.assertEqual([x.result["timestamp_ms"] for x in feed], [1000, 2000, 3000])
        self.assertEqual(len(writer.iter_stored(after_sequence=feed[0].sequence)), 2)

    def test_restart_preserves_idempotency(self):
        key = str(uuid.uuid4())
        self.writer().write_detection(event(), event_id=key)
        self.assertEqual(self.writer().write_detection(event(), event_id=key).status, "duplicate")

    def test_crash_before_append_recovers(self):
        writer, key = self.writer(), str(uuid.uuid4())
        with patch.object(writer, "_append_pending", side_effect=OSError("disk failure")):
            with self.assertRaises(StorageError):
                writer.write_detection(event(), event_id=key)
        recovered = self.writer()
        self.assertEqual(recovered.write_detection(event(), event_id=key).status, "duplicate")
        self.assertEqual(len(recovered.iter_stored()), 1)

    def test_partial_tail_after_crash_is_completed_without_second_line(self):
        writer, key = self.writer(), str(uuid.uuid4())
        def partial(row):
            path = writer.root / row["relative_path"]
            path.parent.mkdir(parents=True)
            path.write_bytes(bytes(row["payload"])[:30])
            raise OSError("simulated interrupted append")
        with patch.object(writer, "_append_pending", side_effect=partial):
            with self.assertRaises(StorageError):
                writer.write_detection(event(), event_id=key)
        receipt = self.writer().write_detection(event(), event_id=key)
        self.assertEqual(receipt.path.read_bytes(), encode_event(event()) + b"\n")

    def test_crash_after_append_before_commit_is_recovered(self):
        writer, key = self.writer(), str(uuid.uuid4())
        def append_then_crash(row):
            path = writer.root / row["relative_path"]
            path.parent.mkdir(parents=True)
            path.write_bytes(bytes(row["payload"]) + b"\n")
            raise OSError("simulated crash before ledger commit")
        with patch.object(writer, "_append_pending", side_effect=append_then_crash):
            with self.assertRaises(StorageError):
                writer.write_detection(event(), event_id=key)
        receipt = self.writer().write_detection(event(), event_id=key)
        self.assertEqual(receipt.status, "duplicate")
        self.assertEqual(len(receipt.path.read_bytes().splitlines()), 1)

    def test_unrelated_file_change_fails_closed(self):
        writer, key = self.writer(), str(uuid.uuid4())
        receipt = writer.write_detection(event(), event_id=key)
        original = receipt.path.read_bytes()
        receipt.path.write_bytes(b"unrelated data")
        with self.assertRaises(StorageError):
            writer.write_detection(event(), event_id=key)
        self.assertEqual(receipt.path.read_bytes(), b"unrelated data")
        receipt.path.write_bytes(original + b"unexpected\n")
        with self.assertRaises(StorageError):
            writer.write_detection(event(15), event_id=str(uuid.uuid4()))

    def test_concurrent_writers_and_duplicates(self):
        writers = [self.writer() for _ in range(4)]
        keys = [str(uuid.uuid4()) for _ in range(10)]
        def write(index):
            return writers[index % 4].write_detection(event(index % 10), event_id=keys[index % 10])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(write, range(20)))
        self.assertEqual(sum(x.status == "stored" for x in receipts), 10)
        self.assertEqual(len(writers[0].iter_stored()), 10)

    def test_processes_share_one_idempotency_ledger(self):
        package_root = str(Path(logger.__file__).resolve().parents[1])
        program = ("import sys,json; from shared.config import WriterConfig; from shared.storage import DetectionWriter; "
                   "w=DetectionWriter(WriterConfig(sys.argv[1])); "
                   "print(w.write_detection(json.loads(sys.argv[3]),event_id=sys.argv[2]).status)")
        key = str(uuid.uuid4())
        args = [sys.executable, "-c", program, str(self.root / "records"), key, json.dumps(event())]
        env = {**os.environ, "PYTHONPATH": package_root}
        children = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for _ in range(2)]
        outputs = []
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stderr)
            outputs.append(stdout.strip())
        self.assertEqual(sorted(outputs), ["duplicate", "stored"])

    def test_invalid_event_and_id_never_create_detection_line(self):
        writer = self.writer()
        for key in ("../bad", "not-an-id"):
            with self.assertRaises(ValidationError):
                writer.write_detection(event(), event_id=key)
        with self.assertRaises(ValidationError):
            writer.write_detection({"raw_score": 0}, event_id=str(uuid.uuid4()))
        self.assertEqual(list(writer.root.rglob("*.jsonl")), [])


class DeliveryTests(SharedCase):
    def test_real_local_http_zero_and_cheat(self):
        with LocalReceiver(self.root / "server") as server:
            client = self.client(self.config(server.url))
            for score in (0, 15):
                receipt = client.send_detection(event(score))
                self.assertEqual(receipt.status, "queued")
            self.assertTrue(client.flush(3))
            self.assertEqual([x.result["raw_score"] for x in server.writer.iter_stored()], [0, 15])
            self.assertEqual(server.requests[0][2]["X-Gzz-Protocol-Version"], "1")
            self.assertEqual(client.status().acknowledged_this_run, 2)

    def test_lost_ack_retries_same_id_without_duplicate_storage(self):
        with LocalReceiver(self.root / "server", ["store_then_503"]) as server:
            client = self.client(self.config(server.url))
            client.send_detection(event(15))
            self.assertTrue(client.flush(3))
            self.assertEqual(len(server.requests), 2)
            self.assertEqual(server.requests[0][:2], server.requests[1][:2])
            self.assertEqual(len(server.writer.iter_stored()), 1)
            self.assertEqual(client.status().last_code, "duplicate")

    def test_wrong_ack_is_not_success(self):
        with LocalReceiver(self.root / "server", ["wrong_ack"] * 3) as server:
            client = self.client(self.config(server.url))
            client.send_detection(event())
            self.assertFalse(client.flush(3))
            self.assertEqual(client.status().failed, 1)
            self.assertEqual(client.status().acknowledged_this_run, 0)
            self.assertEqual(len(server.requests), 3)

    def test_auth_and_schema_errors_do_not_retry(self):
        for code in ("401", "422"):
            with self.subTest(code=code), LocalReceiver(self.root / code, [code]) as server:
                client = self.client(self.config(server.url, outbox_path=self.root / (code + ".sqlite3")))
                client.send_detection(event())
                self.assertFalse(client.flush(3))
                self.assertEqual(len(server.requests), 1)
                self.assertEqual(client.failures()[0]["last_error"], "http_" + code)

    def test_redirect_is_rejected_without_following(self):
        with LocalReceiver(self.root / "server", ["redirect"]) as server:
            client = self.client(self.config(server.url))
            client.send_detection(event())
            self.assertFalse(client.flush(3))
            self.assertEqual(len(server.requests), 1)
            self.assertEqual(client.failures()[0]["last_error"], "http_307")

    def test_transient_error_recovery_and_retry_after(self):
        with LocalReceiver(self.root / "server", ["429", "503"]) as server:
            client = self.client(self.config(server.url))
            client.send_detection(event())
            self.assertTrue(client.flush(3))
            self.assertEqual(len(server.requests), 3)
        self.assertEqual(_retry_after("2"), 2)
        self.assertIsNone(_retry_after("NaN"))
        self.assertIsNone(_retry_after("invalid"))

    def test_timeout_is_not_acknowledged_and_record_is_retained(self):
        with LocalReceiver(self.root / "server", ["slow"]) as server:
            client = self.client(self.config(server.url, timeout_seconds=0.02, max_attempts=1))
            client.send_detection(event())
            self.assertFalse(client.flush(3))
            self.assertEqual(client.status().failed, 1)
            # Wait for the deliberately slow test request to finish before temp cleanup.
            time.sleep(0.15)

    def test_tls_verification_error_is_not_bypassed(self):
        transport = HttpTransport(self.config("https://example.invalid"))
        with patch.object(transport._opener, "open", side_effect=urllib.error.URLError(ssl.SSLCertVerificationError("test"))):
            result = transport.send(encode_event(event()), str(uuid.uuid4()))
        self.assertEqual(result.disposition, "rejected")
        self.assertEqual(result.code, "tls_verification_failed")

    def test_restart_preserves_failed_payload_until_explicit_retry(self):
        class Offline:
            def send(self, payload, event_id):
                return DeliveryOutcome("retry", "network_error")
        config = self.config(max_attempts=1)
        client = self.client(config, transport=Offline())
        receipt = client.send_detection(event(15, 9876))
        self.assertFalse(client.flush(2))
        with self.assertRaises(EventAlreadyFailedError):
            client.send_detection(event(15, 9876), event_id=receipt.event_id)
        self.assertTrue(client.close())
        received = []
        class Online:
            def send(self, payload, event_id):
                received.append((event_id, json.loads(payload)))
                return DeliveryOutcome("accepted", "stored")
        recovered = self.client(config, transport=Online())
        self.assertEqual(recovered.status().failed, 1)
        self.assertEqual(recovered.retry_failed(receipt.event_id), 1)
        self.assertTrue(recovered.flush(2))
        self.assertEqual(received, [(receipt.event_id, event(15, 9876))])

    def test_pending_record_survives_close_and_resumes_on_restart(self):
        called = threading.Event()
        class Offline:
            def send(self, payload, event_id):
                called.set()
                return DeliveryOutcome("retry", "network_error")
        config = self.config(retry_base_seconds=0.2, retry_max_seconds=0.2)
        client = self.client(config, transport=Offline())
        receipt = client.send_detection(event(15, 4567))
        self.assertTrue(called.wait(2))
        self.assertTrue(client.close())
        self.assertEqual(client.status().pending, 1)
        received = []
        class Online:
            def send(self, payload, event_id):
                received.append((event_id, json.loads(payload)))
                return DeliveryOutcome("accepted", "stored")
        restarted = self.client(config, transport=Online())
        self.assertTrue(restarted.flush(2))
        self.assertEqual(received, [(receipt.event_id, event(15, 4567))])

    def test_queue_byte_limit_and_token_not_stored(self):
        client = self.client(self.config(max_queue_bytes=1))
        with self.assertRaises(QueueFullError):
            client.send_detection(event())
        self.assertEqual(client.status().pending, 0)
        client.close()
        self.assertNotIn(b"test-token", client.config.outbox_path.read_bytes())

    def test_sender_storage_failure_not_reported_as_queued(self):
        client = self.client()
        with patch.object(client._outbox, "enqueue", side_effect=StorageError("test failure")):
            with self.assertRaises(StorageError):
                client.send_detection(event())
        self.assertEqual(client.status().pending, 0)

    def test_failed_auth_can_be_retried_after_key_rotation(self):
        with LocalReceiver(self.root / "server") as server:
            wrong = self.client(self.config(server.url, api_token="wrong-token"))
            receipt = wrong.send_detection(event())
            self.assertFalse(wrong.flush(2))
            self.assertTrue(wrong.close())
            corrected = self.client(self.config(server.url))
            corrected.retry_failed(receipt.event_id)
            self.assertTrue(corrected.flush(2))
            self.assertEqual(len(server.writer.iter_stored()), 1)

    def test_enqueue_does_not_wait_for_http_and_multiple_threads_preserve_all_samples(self):
        entered, release = threading.Event(), threading.Event()
        received = []
        class Capture:
            def send(self, payload, event_id):
                entered.set()
                release.wait(3)
                received.append(json.loads(payload))
                return DeliveryOutcome("accepted", "stored")
        client = self.client(transport=Capture())
        try:
            client.send_detection(event(0, 0))
            self.assertTrue(entered.wait(2))
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                receipts = list(pool.map(lambda n: client.send_detection(event(15, n)), range(1, 21)))
            self.assertEqual(len(receipts), 20)
            self.assertEqual(client.status().pending, 21)
        finally:
            release.set()
        self.assertTrue(client.flush(3))
        self.assertEqual(sorted(x["timestamp_ms"] for x in received), list(range(21)))

    def test_queue_capacity_has_no_silent_eviction_or_network_block(self):
        entered, release = threading.Event(), threading.Event()
        class Slow:
            def send(self, payload, event_id):
                entered.set()
                release.wait(3)
                return DeliveryOutcome("accepted", "stored")
        self.addCleanup(release.set)
        client = self.client(self.config(max_queue_events=1), transport=Slow())
        source = event()
        client.send_detection(source)
        self.assertTrue(entered.wait(2))
        with self.assertRaises(QueueFullError):
            client.send_detection(event(15))
        self.assertEqual(client.status().pending, 1)
        self.assertFalse(client.close(timeout=0))
        release.set()
        self.assertTrue(client.close(2))

    def test_payload_is_detached_from_callers_mutation(self):
        received = []
        class Capture:
            def send(self, payload, event_id):
                received.append(json.loads(payload))
                return DeliveryOutcome("accepted", "stored")
        client = self.client(transport=Capture())
        original = event()
        client.send_detection(original)
        original["evidence"]["behavior_score"] = 999
        self.assertTrue(client.flush(2))
        self.assertEqual(received, [event()])

    def test_second_worker_cannot_use_same_queue(self):
        client = self.client()
        with self.assertRaises(ResourceBusyError):
            DetectionClient(self.config())
        self.assertTrue(client.close())
        self.assertTrue(self.client().close())

    def test_destination_change_requires_explicit_new_queue(self):
        self.client().close()
        with self.assertRaises(ConfigurationError):
            DetectionClient(self.config("http://localhost:10"))

    def test_rejects_after_close_and_invalid_event_before_queue(self):
        client = self.client()
        with self.assertRaises(ValidationError):
            client.send_detection({"bad": 1})
        self.assertEqual(client.status().pending, 0)
        client.close()
        with self.assertRaises(ClientClosedError):
            client.send_detection(event())

    def test_facade_explicit_lifecycle_and_server_independence(self):
        self.addCleanup(logger.shutdown_client)
        logger.shutdown_client()
        with self.assertRaises(ConfigurationError):
            logger.send_detection(event())
        writer = logger.configure_writer(WriterConfig(self.root / "server"))
        receipt = logger.write_detection(event(), event_id=str(uuid.uuid4()))
        self.assertEqual(receipt.status, "stored")
        self.assertEqual(len(writer.iter_stored()), 1)
        with LocalReceiver(self.root / "receiver") as server:
            logger.configure_client(self.config(server.url))
            with self.assertRaises(ResourceBusyError):
                logger.configure_client(self.config(server.url))
            logger.send_detection(event())
            self.assertTrue(logger.flush_client(3))
            self.assertTrue(logger.shutdown_client())


if __name__ == "__main__":
    unittest.main()
