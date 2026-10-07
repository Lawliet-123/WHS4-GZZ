import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from server.dashboard_backend.evidence_projection import build_evidence_projection
from server.dashboard_backend.service import DashboardService
from server.scoring.player_snapshot import build_player_policy_snapshot
from server.scoring.storage import ScoringStore
from shared.config import WriterConfig
from shared.storage import DetectionWriter


class EvidenceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = ScoringStore(root / "scoring.sqlite3")
        self.service = DashboardService(
            writer=DetectionWriter(WriterConfig(root=root / "detections")),
            scoring=self.store, index_path=root / "dashboard.sqlite3", cursor_secret="test-only-cursor",
        )
        self.sequence = 0

    def add(self, module, raw, *, scope=None, evidence=None, timestamp=None):
        self.sequence += 1
        result = {
            "session_id": "test-session", "player_id": "test-player", "module": module,
            "timestamp_ms": self.sequence * 1000 if timestamp is None else timestamp,
            "raw_score": raw, "reasons": [f"original-reason-{self.sequence}"],
            "evidence": {"status": "NORMAL" if raw == 0 else "SUSPICIOUS", **(evidence or {})},
        }
        if scope is not None:
            result["evidence"]["submodule"] = scope
        event_id = str(uuid.uuid4())
        self.store.process_event(result, event_id=event_id, sequence=self.sequence)
        return event_id

    def projection(self, reader=None):
        snapshot = build_player_policy_snapshot(
            self.store.get_player_snapshot("test-session", "test-player"),
            session_id="test-session", player_id="test-player",
        )
        return build_evidence_projection(reader or self.store, snapshot)

    def test_scoped_dll_zero_positive_zero_retains_original_active_incident(self):
        self.add("external_access", 0, scope="external_process")
        self.add("external_access", 0, scope="module_integrity")
        incident_id = self.add("external_access", 2, scope="module_integrity")
        latest_id = self.add("external_access", 0, scope="module_integrity")
        result = self.projection()
        channels = {item["submodule"]: item for item in result["module_evidence"]}
        integrity = channels["module_integrity"]
        self.assertEqual(integrity["signal"]["status"], "ACTIVE")
        self.assertFalse(integrity["signal"]["threshold_met"])
        self.assertEqual(integrity["signal"]["calibration_threshold"], 2)
        self.assertEqual(integrity["latest_event"]["event_id"], latest_id)
        self.assertEqual(integrity["latest_event"]["raw_score"], 0)
        self.assertEqual(integrity["retained_incident_event"]["event_id"], incident_id)
        self.assertEqual(integrity["retained_incident_event"]["raw_score"], 2)
        self.assertEqual(integrity["retained_incident_event"]["reasons"], ["original-reason-3"])
        self.assertEqual(channels["external_process"]["signal"]["status"], "INACTIVE")
        self.assertEqual(result["aggregate_risk"]["active_modules"], ("external_access",))
        self.assertEqual(result["aggregate_risk"]["evidence_unit_count"], 1)

    def test_process_history_is_not_retained_after_recovery(self):
        self.add("external_access", 3, scope="external_process")
        self.add("external_access", 0, scope="external_process")
        self.add("external_access", 0, scope="module_integrity")
        result = self.projection()
        process = next(item for item in result["module_evidence"] if item["submodule"] == "external_process")
        self.assertEqual(process["signal"]["status"], "INACTIVE")
        self.assertIsNone(process["retained_incident_event"])
        self.assertEqual(result["aggregate_risk"]["active_module_count"], 0)

    def test_errored_dll_high_raw_is_not_a_retained_incident(self):
        self.add("external_access", 0, scope="external_process")
        self.add("external_access", 20, scope="module_integrity", evidence={"status": "ERROR", "measurement_valid": False})
        self.add("external_access", 0, scope="module_integrity")
        integrity = next(item for item in self.projection()["module_evidence"] if item["submodule"] == "module_integrity")
        self.assertEqual(integrity["signal"]["status"], "INACTIVE")
        self.assertIsNone(integrity["retained_incident_event"])

    def test_autopaint_retains_qualified_session_incident_after_zero(self):
        incident_id = self.add("autopaint", 23, evidence={"integrity_valid": 1, "behavior_valid": 0})
        self.add("autopaint", 0, evidence={"integrity_valid": 1, "behavior_valid": 0})
        signal = self.projection()["module_evidence"][0]
        self.assertEqual(signal["signal"]["status"], "ACTIVE")
        self.assertEqual(signal["latest_event"]["raw_score"], 0)
        self.assertEqual(signal["retained_incident_event"]["event_id"], incident_id)

    def test_highest_snapshot_history_does_not_promote_current_noclip(self):
        self.add("noclip", 3)
        self.add("noclip", 0)
        signal = self.projection()["module_evidence"][0]
        self.assertEqual(signal["signal"]["status"], "INACTIVE")
        self.assertEqual(signal["signal"]["calibration_threshold"], 3)
        self.assertIsNone(signal["retained_incident_event"])

    def test_missing_history_reader_is_not_empty_normal_history(self):
        self.add("external_access", 0, scope="external_process")
        self.add("external_access", 0, scope="module_integrity")
        result = self.projection(SimpleNamespace())
        self.assertTrue(all(item["signal"]["status"] == "UNRESOLVED" for item in result["module_evidence"]))
        self.assertTrue(all(item["latest_event"] is None for item in result["module_evidence"]))
        self.assertFalse(result["aggregate_risk"]["assessment_complete"])

    def test_missing_autopaint_history_does_not_certify_a_latest_zero_as_inactive(self):
        self.add("autopaint", 23, evidence={"integrity_valid": 1, "behavior_valid": 0})
        self.add("autopaint", 0, evidence={"integrity_valid": 1, "behavior_valid": 0})
        self.assertEqual(self.projection()["module_evidence"][0]["signal"]["status"], "ACTIVE")
        incomplete = self.projection(SimpleNamespace())
        self.assertEqual(incomplete["module_evidence"][0]["signal"]["status"], "DEFERRED")
        self.assertIsNone(incomplete["module_evidence"][0]["retained_incident_event"])
        self.assertFalse(incomplete["aggregate_risk"]["assessment_complete"])

    def test_snapshot_explanation_does_not_issue_verdict_without_provider(self):
        self.add("noclip", 3)
        result = self.service.snapshot("test-session", "test-player")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["final_verdict"])
        self.assertIsNone(result["score"])
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["policy"]["module_evidence"][0]["signal"]["status"], "ACTIVE")
        self.assertEqual(result["policy"]["aggregate_risk"]["active_module_count"], 1)

    def test_late_arrival_does_not_replace_current_state_in_explanation(self):
        current_id = self.add("external_access", 0, scope="external_process", timestamp=2000)
        self.add("external_access", 3, scope="external_process", timestamp=1000)
        self.add("external_access", 0, scope="module_integrity")
        process = next(item for item in self.projection()["module_evidence"] if item["submodule"] == "external_process")
        self.assertEqual(process["latest_event"]["event_id"], current_id)
        self.assertEqual(process["signal"]["status"], "INACTIVE")


if __name__ == "__main__":
    unittest.main()
