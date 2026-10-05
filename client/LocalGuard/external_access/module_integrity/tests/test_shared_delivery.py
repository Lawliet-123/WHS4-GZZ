"""Durable module-integrity handoff failures using only synthetic temp data."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from client.LocalGuard.external_access.common.models import TargetProcess
from client.LocalGuard.external_access.module_integrity import runner as runner_module
from client.LocalGuard.external_access.module_integrity import shared_delivery
from client.LocalGuard.external_access.module_integrity.models import LoadedModule, ModuleSnapshot
from client.LocalGuard.external_access.module_integrity.runner import ModuleIntegrityRunner
from shared.client import DetectionClient
from shared.config import ClientConfig
from shared.errors import QueueFullError
from shared.schema import EVENT_FIELDS
from shared.transport import DeliveryOutcome


ROOT = Path(__file__).resolve().parents[5]


def event(timestamp=1000, *, score=1, status="SUSPICIOUS"):
    return dict(
        session_id="synthetic_handoff", player_id="synthetic_player",
        module="external_access", timestamp_ms=timestamp,
        evidence={"submodule": "module_integrity", "status": status},
        reasons=["Synthetic DLL change"] if score else [], raw_score=score,
    )


class _Locator:
    def find(self):
        return TargetProcess(500, "synthetic-game.exe", None, 1.0)


class _Sensor:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.calls = 0

    def capture(self, pid):
        self.calls += 1
        return next(self.snapshots)


class SharedDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="module-handoff-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "events.jsonl"

    def lines(self):
        return [json.loads(line) for line in self.output.read_text("utf-8").splitlines()]

    def test_real_shared_queue_full_keeps_runner_batch_and_original_local_line(self):
        entered, release = threading.Event(), threading.Event()

        class BlockedTransport:
            def send(self, payload, event_id):
                entered.set()
                release.wait(5)
                return DeliveryOutcome("accepted", "stored")

        client = DetectionClient(ClientConfig(
            server_url="http://127.0.0.1:9", api_token="synthetic-token",
            outbox_path=self.root / "shared.sqlite3", max_queue_events=1,
            allow_insecure_loopback=True,
        ), transport=BlockedTransport())
        self.addCleanup(client.close)
        self.addCleanup(release.set)
        client.send_detection(event(0, score=0, status="NORMAL"))
        self.assertTrue(entered.wait(2))

        game_module = LoadedModule("synthetic-game.exe", None, 0x1000, 4096)
        extra = LoadedModule("synthetic-extra.dll", None, 0x2000, 4096)
        sensor = _Sensor((
            ModuleSnapshot(500, 1, (game_module,)),
            ModuleSnapshot(500, 2, (game_module, extra)),
        ))
        runner = ModuleIntegrityRunner(
            game_executable_name="synthetic-game.exe", session_id="synthetic_handoff",
            player_id="synthetic_player", output_path=self.output,
            locator=_Locator(), sensor=sensor, audit_initial_snapshot=False,
            writer=runner_module._write_local_and_send,
        )
        attempted = []

        def send(payload, *, event_id):
            attempted.append((payload, event_id))
            return client.send_detection(payload, event_id=event_id)

        with patch.object(runner_module, "send_detection", side_effect=send):
            self.assertTrue(runner.scan_once().baseline_created)
            failed = runner.scan_once()
            self.assertIn("QueueFullError", failed.error)
            self.assertEqual(failed.emitted_detections, 0)
            self.assertEqual(len(self.lines()), 1)
            original = self.lines()[0]
            release.set()
            self.assertTrue(client.flush(3))
            recovered = runner.scan_once()
            self.assertIsNone(recovered.error)
            self.assertEqual(recovered.emitted_detections, 1)
            self.assertTrue(client.flush(3))

        self.assertEqual(sensor.calls, 2, "retry must use the retained snapshot/result")
        self.assertEqual(attempted[0], attempted[1])
        self.assertEqual(self.lines(), [original])
        self.assertEqual(original["module"], "external_access")
        self.assertEqual(original["evidence"]["submodule"], "module_integrity")
        self.assertEqual(client.status().acknowledged_this_run, 2)

    def test_pending_positive_survives_process_exit_then_precedes_new_normal_zero(self):
        script = """
import sys
from pathlib import Path
from client.LocalGuard.external_access.module_integrity.shared_delivery import write_local_and_queue
from shared.errors import QueueFullError
payload = {
    "session_id": "synthetic_handoff", "player_id": "synthetic_player",
    "module": "external_access", "timestamp_ms": 1000,
    "evidence": {"submodule": "module_integrity", "status": "SUSPICIOUS"},
    "reasons": ["Synthetic DLL change"], "raw_score": 1,
}
def full(*args, **kwargs):
    raise QueueFullError("synthetic private configuration value")
try:
    write_local_and_queue(Path(sys.argv[1]), payload, send=full)
except OSError as error:
    print(str(error))
else:
    raise AssertionError("queue failure was acknowledged")
"""
        child = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.output)],
            cwd=ROOT, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn("QueueFullError", child.stdout)
        self.assertNotIn("private configuration", child.stdout)
        self.assertEqual(self.lines(), [event()])
        delivered = []

        def ready(payload, *, event_id):
            delivered.append((payload, event_id))
            return SimpleNamespace(status="queued", event_id=event_id)

        normal = event(2000, score=0, status="NORMAL")
        shared_delivery.write_local_and_queue(self.output, normal, send=ready)
        shared_delivery.write_local_and_queue(self.output, event(), send=ready)
        self.assertEqual([payload for payload, _ in delivered], [event(), normal])
        self.assertNotEqual(delivered[0][1], delivered[1][1])
        self.assertEqual(self.lines(), [event(), normal])
        for payload in self.lines():
            self.assertEqual(set(payload), set(EVENT_FIELDS))

    def test_partial_batch_retries_only_unqueued_dll_without_duplicate_lines(self):
        game = LoadedModule("synthetic-game.exe", None, 0x1000, 4096)
        first = LoadedModule("a.dll", None, 0x2000, 4096)
        second = LoadedModule("b.dll", None, 0x3000, 4096)
        sensor = _Sensor((ModuleSnapshot(500, 1, (game,)),
                          ModuleSnapshot(500, 2, (game, first, second))))
        runner = ModuleIntegrityRunner(
            game_executable_name="synthetic-game.exe", session_id="synthetic_handoff",
            player_id="synthetic_player", output_path=self.output,
            locator=_Locator(), sensor=sensor, audit_initial_snapshot=False,
            writer=runner_module._write_local_and_send,
        )
        attempted = []

        def send(payload, *, event_id):
            attempted.append((payload["evidence"]["module_name"], event_id))
            if len(attempted) == 2:
                raise QueueFullError("synthetic full queue")
            return SimpleNamespace(status="queued", event_id=event_id)

        with patch.object(runner_module, "send_detection", side_effect=send):
            runner.scan_once()
            failed = runner.scan_once()
            self.assertEqual(failed.emitted_detections, 1)
            self.assertIn("QueueFullError", failed.error)
            recovered = runner.scan_once()
            self.assertEqual(recovered.emitted_detections, 1)
            self.assertIsNone(recovered.error)
        self.assertEqual([name for name, _ in attempted], ["a.dll", "b.dll", "b.dll"])
        self.assertEqual(attempted[1], attempted[2])
        self.assertEqual([line["evidence"]["module_name"] for line in self.lines()], ["a.dll", "b.dll"])
        self.assertEqual(sensor.calls, 2)

    def test_interrupted_local_append_recovers_matching_tail_before_enqueue(self):
        sent = []

        def send(payload, *, event_id):
            sent.append(payload)
            return SimpleNamespace(status="queued", event_id=event_id)

        def interrupted(path, ledger, row):
            path.write_bytes(bytes(row["payload"])[:12])
            raise OSError("synthetic interrupted append")

        with patch.object(shared_delivery, "_complete_local_row", side_effect=interrupted):
            with self.assertRaises(OSError):
                shared_delivery.write_local_and_queue(self.output, event(), send=send)
        self.assertEqual(sent, [])
        shared_delivery.write_local_and_queue(self.output, event(), send=send)
        self.assertEqual(self.lines(), [event()])
        self.assertEqual(sent, [event()])

    def test_existing_complete_log_is_preserved_without_replaying_older_lines(self):
        older = event(99, score=0, status="NORMAL")
        self.output.write_text(json.dumps(older) + "\n", encoding="utf-8")
        sent = []

        def send(payload, *, event_id):
            sent.append(payload)
            return SimpleNamespace(status="queued", event_id=event_id)

        shared_delivery.write_local_and_queue(self.output, event(), send=send)
        self.assertEqual(self.lines(), [older, event()])
        self.assertEqual(sent, [event()])

    def test_existing_incomplete_log_fails_closed_without_appending_or_sending(self):
        original = b'{"synthetic":"unfinished"'
        self.output.write_bytes(original)
        sent = []
        with self.assertRaisesRegex(OSError, "StorageError"):
            shared_delivery.write_local_and_queue(
                self.output, event(), send=lambda *args, **kwargs: sent.append(args)
            )
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(sent, [])

    def test_outside_edit_of_queued_record_fails_closed_on_duplicate_write(self):
        sent = []

        def send(payload, *, event_id):
            sent.append(payload)
            return SimpleNamespace(status="queued", event_id=event_id)

        shared_delivery.write_local_and_queue(self.output, event(), send=send)
        changed = b"[" + self.output.read_bytes()[1:]
        self.output.write_bytes(changed)
        for incoming in (event(), event(2000)):
            with self.assertRaisesRegex(OSError, "StorageError"):
                shared_delivery.write_local_and_queue(self.output, incoming, send=send)
        self.assertEqual(self.output.read_bytes(), changed)
        self.assertEqual(sent, [event()])

    def test_nonmatching_interrupted_tail_is_never_overwritten_or_enqueued(self):
        original = b"synthetic incompatible tail"

        def interrupted(path, ledger, row):
            path.write_bytes(original)
            raise OSError("synthetic interrupted append")

        with patch.object(shared_delivery, "_complete_local_row", side_effect=interrupted):
            with self.assertRaises(OSError):
                shared_delivery.write_local_and_queue(self.output, event(), send=None)
        with self.assertRaisesRegex(OSError, "StorageError"):
            shared_delivery.write_local_and_queue(self.output, event(), send=None)
        self.assertEqual(self.output.read_bytes(), original)

    def test_invalid_enqueue_receipt_retains_record_until_valid_acceptance(self):
        attempts = []
        for status in (None, "sent", "stored", "duplicate"):
            def unknown(payload, *, event_id):
                attempts.append(event_id)
                return SimpleNamespace(status=status, event_id=event_id)

            with self.subTest(status=status), self.assertRaisesRegex(OSError, "StorageError"):
                shared_delivery.write_local_and_queue(self.output, event(), send=unknown)
        with self.assertRaisesRegex(OSError, "StorageError"):
            shared_delivery.write_local_and_queue(
                self.output, event(),
                send=lambda *args, **kwargs: SimpleNamespace(status="queued", event_id="wrong-id"),
            )

        def accepted(payload, *, event_id):
            attempts.append(event_id)
            return SimpleNamespace(status="queued", event_id=event_id)

        shared_delivery.write_local_and_queue(self.output, event(), send=accepted)
        shared_delivery.write_local_and_queue(self.output, event(), send=accepted)
        self.assertEqual(len(attempts), 5)
        self.assertEqual(len(set(attempts)), 1)
        self.assertEqual(self.lines(), [event()])


if __name__ == "__main__":
    unittest.main()
