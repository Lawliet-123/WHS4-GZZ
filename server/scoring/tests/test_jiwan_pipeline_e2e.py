"""Synthetic Jiwan sensors -> production Shared HTTP -> scoring -> Dashboard API.

Run from the repository root with:
    python -m unittest server.scoring.tests.test_jiwan_pipeline_e2e -v

Only sensor acquisition is substituted. These tests use the production ESP
detector/adapter/local outbox, module-integrity runner, Shared SQLite sender,
HTTP transport, Receiver, durable writer, ScoringStore, FinalVerdict, and
Dashboard API. One retry test injects a single failure after durable scoring.
They bind an ephemeral loopback socket and keep every artifact in a temporary
directory; they neither inspect environment configuration nor run a real game.
The React UI is outside this test's coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import FastAPI
import uvicorn

# ESP's production modules retain their standalone ``anti_esp`` imports.
ESP_ROOT = Path(__file__).resolve().parents[3] / "client" / "detectors" / "esp"
if str(ESP_ROOT) not in sys.path:
    sys.path.insert(0, str(ESP_ROOT))

from anti_esp.core.events import SensorBatch, SensorEvent
from anti_esp.core.session import SessionTelemetryWriter
from anti_esp.detectors.esp_detector import EspEventDetector
from anti_esp.pipeline import EspDetectionPipeline
from anti_esp.scoring import SuspicionEngine
from anti_esp.shared_transport import SharedEventSink
from anti_esp.store import SQLiteEvidenceStore
from anti_esp.team_format import TeamEventAdapter
from client.LocalGuard.external_access.common.models import TargetProcess
from client.LocalGuard.external_access.module_integrity.models import (
    LoadedModule,
    ModuleSnapshot,
)
from client.LocalGuard.external_access.module_integrity.module_sensor import (
    ModuleSensorUnavailable,
)
from client.LocalGuard.external_access.module_integrity.runner import (
    ModuleIntegrityRunner,
    _write_local_and_send,
)
from server.dashboard_backend import DashboardService, create_dashboard_router
from server.receiver import create_router
from server.receiver.router import bearer_token_verifier
from server.scoring.external_access_summary import summarize_external_access_history
from server.scoring import main as scoring_queries
from server.scoring.storage import ScoringStore
from shared import logger
from shared.config import ClientConfig, WriterConfig
from shared.schema import EVENT_FIELDS
from shared.storage import DetectionWriter


SESSION = "jiwan_synthetic_e2e"
PLAYER = "synthetic_player"
DETECTION_TOKEN = "synthetic-detection-token"
DASHBOARD_TOKEN = "synthetic-dashboard-token"


class _SyntheticModuleSensor:
    """Supply explicit snapshots or acquisition errors without OS acquisition."""

    def __init__(self, captures):
        self.captures = iter(captures)

    def capture(self, pid):
        value = next(self.captures)
        if isinstance(value, Exception):
            raise value
        if value.pid != pid:
            raise AssertionError("fixture PID must match the selected target")
        return value


class _SyntheticLocator:
    def find(self):
        return TargetProcess(500, "synthetic-game.exe", None, 100.0)


class JiwanPipelineE2ETests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gzz-jiwan-e2e-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.writer = DetectionWriter(WriterConfig(self.root / "received"))
        self.scoring = ScoringStore(self.root / "scoring.sqlite3")
        # Substitute only storage location. The public scoring/final-verdict
        # adapters stay real, and restoration runs after the HTTP worker stops.
        store_patch = patch.object(scoring_queries, "_store", self.scoring)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        self.receipts = []
        self.scoring_receipts = []
        self.fail_after_scoring_once = False

        def write_detection(payload, *, event_id):
            receipt = self.writer.write_detection(payload, event_id=event_id)
            self.receipts.append((receipt.event_id, receipt.status))
            return receipt

        def submit_to_scoring(payload, *, event_id, sequence):
            receipt = scoring_queries.process(
                payload, event_id=event_id, sequence=sequence
            )
            self.scoring_receipts.append(receipt)
            if self.fail_after_scoring_once:
                self.fail_after_scoring_once = False
                # Scoped fault injection: Receiver returns 503 after both stores
                # commit, so the real sender must repeat the original request.
                raise RuntimeError("synthetic failure after durable scoring")
            return receipt

        app = FastAPI()
        app.include_router(create_router(
            write_detection, submit_to_scoring,
            verify_token=bearer_token_verifier(DETECTION_TOKEN),
        ))
        dashboard = DashboardService(
            writer=self.writer,
            scoring=scoring_queries,
            index_path=self.root / "dashboard.sqlite3",
            cursor_secret="synthetic-cursor-secret",
            verdict_provider=scoring_queries.get_player_final_verdict,
        )
        app.include_router(create_dashboard_router(
            dashboard, verify_token=bearer_token_verifier(DASHBOARD_TOKEN)
        ))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(
            app, log_level="error", access_log=False, lifespan="off"
        ))
        self.worker = threading.Thread(
            target=self.server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        self.addCleanup(self.stop_server)
        self.worker.start()
        deadline = time.monotonic() + 5
        while not self.server.started and self.worker.is_alive():
            if time.monotonic() >= deadline:
                self.fail("loopback Receiver did not start within five seconds")
            time.sleep(0.01)
        self.assertTrue(self.server.started, "loopback Receiver failed to start")
        self.opener = build_opener(ProxyHandler({}))
        self.addCleanup(self.shutdown_sender)
        self.configure_sender("sender.sqlite3")

    def stop_server(self):
        self.server.should_exit = True
        self.worker.join(5)
        self.assertFalse(self.worker.is_alive(), "loopback server did not stop")

    def shutdown_sender(self):
        self.assertTrue(logger.shutdown_client(5), "Shared sender did not stop")

    def configure_sender(self, name):
        self.sender = logger.configure_client(ClientConfig(
            server_url=self.url,
            api_token=DETECTION_TOKEN,
            outbox_path=self.root / name,
            allow_insecure_loopback=True,
            timeout_seconds=1,
            max_attempts=3,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
            retry_jitter_ratio=0,
        ))

    def flush(self):
        self.assertTrue(logger.flush_client(5), self.sender.status())
        self.assertEqual(self.sender.status().pending, 0)
        self.assertEqual(self.sender.status().failed, 0)
        self.assertTrue(self.sender.config.outbox_path.is_file())

    def dashboard_get(self, suffix, **params):
        query = "?" + urlencode(params) if params else ""
        request = Request(
            self.url + "/api/dashboard/" + suffix + query,
            headers={"Authorization": "Bearer " + DASHBOARD_TOKEN},
        )
        with self.opener.open(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def dashboard_events(self, **params):
        return self.dashboard_get(
            "events", session_id=SESSION, player_id=PLAYER, **params
        )["items"]

    def dashboard_snapshot(self):
        return self.dashboard_get(f"sessions/{SESSION}/players/{PLAYER}/snapshot")

    def esp_pipeline(self):
        store = SQLiteEvidenceStore(self.root / "esp.sqlite3")
        self.addCleanup(store.close)
        telemetry = SessionTelemetryWriter(
            self.root / "esp-telemetry", session_id=SESSION,
            game_executable="synthetic-game.exe",
        )
        self.addCleanup(telemetry.close)
        sink = SharedEventSink()
        queued = []

        def queue(payload, outbox_id):
            queued.append((payload, outbox_id))
            return sink.queue(payload, outbox_id)

        pipeline = EspDetectionPipeline(
            detectors=(EspEventDetector(),), store=store,
            scoring=SuspicionEngine(),
            team_adapter=TeamEventAdapter(session_started_at=100, player_id=PLAYER),
            telemetry=telemetry, team_event_sink=queue,
        )
        return pipeline, store, queued

    @staticmethod
    def esp_event(*, trusted=False):
        return SensorEvent(
            session_id=SESSION, sensor_id="synthetic_process_access",
            event_type="process_access", subject_id="synthetic-game:500",
            event_id="synthetic-vm-read", timestamp_ms=107_000,
            payload={
                "source_pid": 900, "target_pid": 500,
                "granted_access": 0x10, "access_labels": ["VM_READ"],
                "trusted": trusted,
                "source_image": r"C:\Users\synthetic-private-user\reader.exe",
                "target_image": r"C:\Users\synthetic-private-user\synthetic-game.exe",
                "computer": "SYNTHETIC-PRIVATE-HOST",
            },
        )

    def test_esp_detector_reaches_shared_receiver_scoring_and_dashboard(self):
        pipeline, store, _ = self.esp_pipeline()
        healthy = pipeline.process_batch(SensorBatch(
            "synthetic_process_access", "online", (self.esp_event(trusted=True),)
        ))
        self.assertEqual(healthy.team_events, ())
        self.assertEqual(self.dashboard_events(module="esp"), [])

        result = pipeline.process_batch(SensorBatch(
            "synthetic_process_access", "online", (self.esp_event(),)
        ))
        self.assertEqual(result.accepted_evidence_count, 1)
        self.assertEqual(len(result.team_events), 1)
        payload = result.team_events[0].to_dict()
        self.assertEqual(set(payload), set(EVENT_FIELDS))
        self.assertEqual(payload["timestamp_ms"], 7000)
        self.assertEqual(payload["raw_score"], 2)
        self.flush()
        self.assertEqual(store.pending_team_event_count(session_id=SESSION), 0)
        stored = self.writer.iter_stored()
        self.assertEqual([item.result for item in stored], [payload])
        events = self.dashboard_events(module="esp")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["id"], stored[0].event_id)
        self.assertEqual(events[0]["evidence"], payload["evidence"])
        self.assertEqual(events[0]["raw_score"], 2)
        self.assertNotIn("synthetic-private-user", json.dumps(events))
        self.assertNotIn("SYNTHETIC-PRIVATE-HOST", json.dumps(events))
        snapshot = self.dashboard_snapshot()
        self.assertEqual(snapshot["modules"][0]["raw_score"], 2)
        self.assertEqual(snapshot["status"], "INCONCLUSIVE")
        self.assertTrue(snapshot["assessment_available"])
        self.assertEqual(snapshot["data_state"], "available")
        self.assertIsNone(snapshot["score"])
        self.assertIsNone(snapshot["confidence"])
        self.assertEqual(snapshot["final_verdict"]["status"], "INCONCLUSIVE")
        self.assertIn("esp", snapshot["final_verdict"]["unresolved_modules"])
        self.assertIn("ASSESSMENT_INCOMPLETE", snapshot["reason_codes"])
        overview = self.dashboard_get("overview", session_id=SESSION, player_id=PLAYER)
        self.assertTrue(overview["capabilities"]["final_assessment"])
        self.assertEqual(overview["assessments"][0]["status"], "INCONCLUSIVE")
        self.assertIsNone(overview["assessments"][0]["score"])

        # ESP is event based: an empty or failed observation creates no NORMAL
        # zero and must not overwrite the previously received positive event.
        for status in ("online", "unavailable"):
            empty = pipeline.process_batch(SensorBatch(
                "synthetic_process_access", status,
                message="synthetic unavailable sensor" if status != "online" else "",
            ))
            self.assertEqual(empty.team_events, ())
        self.flush()
        self.assertEqual(len(self.receipts), 1)
        self.assertEqual(self.dashboard_snapshot()["modules"][0]["raw_score"], 2)

    def test_esp_retry_and_fresh_sender_keep_stable_id_and_one_server_event(self):
        pipeline, _, queued = self.esp_pipeline()
        self.fail_after_scoring_once = True
        pipeline.process_batch(SensorBatch(
            "synthetic_process_access", "online", (self.esp_event(),)
        ))
        self.flush()
        payload, outbox_id = queued[0]
        event_id = SharedEventSink._event_id(outbox_id)
        self.assertEqual(self.receipts, [(event_id, "stored"), (event_id, "duplicate")])
        self.assertEqual([receipt.status for receipt in self.scoring_receipts],
                         ["processed", "duplicate"])
        self.assertEqual(self.dashboard_snapshot()["modules"][0]["event_id"], event_id)
        self.assertEqual(len(self.dashboard_events(module="esp")), 1)

        self.shutdown_sender()
        self.configure_sender("fresh-sender.sqlite3")
        self.assertTrue(SharedEventSink().queue(payload, outbox_id))
        self.flush()
        self.assertEqual(self.receipts[-1], (event_id, "duplicate"))
        self.assertEqual(len(self.writer.iter_stored()), 1)
        self.assertEqual([receipt.status for receipt in self.scoring_receipts],
                         ["processed", "duplicate", "duplicate"])
        self.assertEqual(self.dashboard_snapshot()["modules"][0]["raw_score"], 2)
        self.assertEqual(len(self.dashboard_events(module="esp")), 1)

    def test_module_integrity_normal_positive_failure_and_recovery_reach_dashboard(self):
        game_module = LoadedModule("synthetic-game.exe", None, 0x1000, 4096)
        added_module = LoadedModule("synthetic-extra.dll", None, 0x2000, 4096)
        captures = (
            ModuleSnapshot(500, 101, (game_module,)),
            ModuleSnapshot(500, 102, (game_module, added_module)),
            ModuleSensorUnavailable(500, "synthetic acquisition denied", 5),
            ModuleSnapshot(500, 104, (game_module, added_module)),
        )
        wall_time = [101.0]
        local_path = self.root / "module-integrity.jsonl"
        runner = ModuleIntegrityRunner(
            game_executable_name="synthetic-game.exe", session_id=SESSION,
            player_id=PLAYER, output_path=local_path,
            locator=_SyntheticLocator(), sensor=_SyntheticModuleSensor(captures),
            audit_initial_snapshot=False, writer=_write_local_and_send,
            wall_clock=lambda: wall_time[0], session_t0=100,
            emit_status_events=True,
        )
        for index in range(3):
            wall_time[0] = 101.0 + index
            report = runner.scan_once()
            self.flush()
            if index == 0:
                self.assertTrue(report.baseline_created)
            elif index == 1:
                self.assertEqual(report.emitted_detections, 1)
            else:
                self.assertIsNotNone(report.error)

        events = self.dashboard_events(module="external_access", submodule="module_integrity")
        self.assertEqual([event["evidence"]["status"] for event in events],
                         ["NORMAL", "SUSPICIOUS", "ERROR"])
        self.assertEqual([event["raw_score"] for event in events], [0, 1, 0])
        self.assertEqual([event["timestamp_ms"] for event in events], [1000, 2000, 3000])
        self.assertEqual(events[-1]["evidence"]["error_code"], "MODULE_SENSOR_UNAVAILABLE")
        scoped = self.scoring.get_scoped_module_state(
            SESSION, PLAYER, "external_access", "module_integrity"
        )
        self.assertEqual(scoped.evidence["status"], "ERROR")
        snapshot = self.dashboard_snapshot()
        self.assertTrue(snapshot["assessment_available"])
        self.assertEqual(snapshot["status"], "INCONCLUSIVE")
        self.assertIsNone(snapshot["score"])
        self.assertFalse(snapshot["final_verdict"]["assessment_complete"])
        self.assertEqual(snapshot["final_verdict"]["evidence_unit_count"], 0)
        self.assertIn("external_access", snapshot["final_verdict"]["unresolved_modules"])
        self.assertEqual(snapshot["modules"][0]["evidence"]["status"], "WARNING")
        self.assertFalse(snapshot["modules"][0]["evidence"]["coverage_complete"])
        self.assertIn("module_integrity",
                      snapshot["modules"][0]["evidence"]["unavailable_submodules"])

        wall_time[0] = 104.0
        recovered = runner.scan_once()
        self.assertEqual(recovered.added_modules, 0)
        self.assertEqual(recovered.emitted_detections, 0)
        self.flush()
        history = self.scoring.get_external_access_history(SESSION, PLAYER, "module_integrity")
        summary = summarize_external_access_history(
            history, session_id=SESSION, player_id=PLAYER, submodule="module_integrity"
        )
        self.assertEqual(summary.positive_events, 1)
        self.assertEqual(summary.latest_status, "NORMAL")
        self.assertTrue(summary.has_positive_history)
        local = [json.loads(line) for line in local_path.read_text("utf-8").splitlines()]
        self.assertEqual([item.result for item in self.writer.iter_stored()], local)
        final_events = self.dashboard_events(module="external_access", submodule="module_integrity")
        self.assertEqual(len(final_events), 4)
        self.assertEqual([event["evidence"] for event in final_events],
                         [event["evidence"] for event in local])


if __name__ == "__main__":
    unittest.main()
