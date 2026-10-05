import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.dashboard_backend import DashboardService, create_dashboard_router
from server.dashboard_backend.demo import recover
from server.dashboard_backend.central_client import CentralQueryError
from server.receiver import create_heartbeat_router, create_router
from server.receiver.heartbeat_store import HeartbeatStore
from server.receiver.router import bearer_token_verifier
from server.receiver.tests.test_heartbeat import valid_heartbeat
from server.scoring.storage import ScoringStore
from shared.config import WriterConfig
from shared.storage import DetectionWriter


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.writer = DetectionWriter(WriterConfig(root=self.root / "detections"))
        self.scoring = ScoringStore(self.root / "scoring.sqlite3")
        self.heartbeats = HeartbeatStore(self.root / "heartbeat.sqlite3", clock=lambda: self.now.isoformat())
        self.service = self.make_service()
        self.fail_scoring = False

        def score(*args, **kwargs):
            if self.fail_scoring:
                raise RuntimeError("synthetic scoring failure")
            return self.scoring.process_event(*args, **kwargs)

        app = FastAPI()
        app.include_router(create_router(self.writer.write_detection, score, verify_token=bearer_token_verifier("detection-test")))
        app.include_router(create_heartbeat_router(self.heartbeats, verify_token=bearer_token_verifier("heartbeat-test")))
        app.include_router(create_dashboard_router(self.service, verify_token=bearer_token_verifier("dashboard-test")))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def make_service(self, **changes):
        options = dict(writer=self.writer, scoring=self.scoring, index_path=self.root / "dashboard.sqlite3", cursor_secret="cursor-test", heartbeat_store=self.heartbeats, clock=lambda: self.now)
        options.update(changes)
        return DashboardService(**options)

    def get(self, suffix, **params):
        return self.client.get("/api/dashboard/" + suffix, params=params, headers={"Authorization": "Bearer dashboard-test"})

    def post(self, *, event_id=None, **changes):
        payload = dict(session_id="s1", player_id="p1", module="noclip", timestamp_ms=2000, evidence={"status": "SUSPICIOUS"}, reasons=["sample"], raw_score=3)
        payload.update(changes)
        return self.client.post("/api/detection", json=payload, headers={"Authorization": "Bearer detection-test", "Idempotency-Key": event_id or str(uuid.uuid4()), "X-GZZ-Protocol-Version": "1"})

    def heartbeat(self, **changes):
        payload = valid_heartbeat(session_id="s1", player_id="p1", **changes)
        return self.client.post("/api/heartbeat", json=payload, headers={"Authorization": "Bearer heartbeat-test"})

    def snapshot(self):
        return self.get("sessions/s1/players/p1/snapshot").json()

    def test_receiver_to_dashboard_preserves_event_and_unknown_assessment(self):
        key = str(uuid.uuid4())
        self.assertEqual(self.post(event_id=key, evidence={"nested": [1, 2], "submodule": "test"}).status_code, 200)
        event = self.get("events").json()["items"][0]
        self.assertEqual(event["id"], key)
        self.assertEqual(event["evidence"]["nested"], [1, 2])
        self.assertIsNone(event["evidence_image"])
        overview = self.get("overview").json()
        self.assertEqual(overview["assessments"][0]["status"], "UNKNOWN")
        self.assertIsNone(overview["assessments"][0]["score"])
        self.assertEqual(self.get("events/" + key).json()["id"], key)
        self.assertEqual(self.snapshot()["modules"][0]["raw_score"], 3)

    def test_authentication_and_query_limits(self):
        self.assertEqual(self.client.get("/api/dashboard/overview").status_code, 401)
        for params in ({"limit": 201}, {"limit": 0}, {"cursor": "tampered"}, {"after_sequence": -1}):
            self.assertEqual(self.get("events", **params).status_code, 422)
        self.assertEqual(self.get("events/missing").status_code, 404)

    def test_duplicate_conflict_and_repeated_snapshot_scores(self):
        key = str(uuid.uuid4())
        self.assertEqual(self.post(event_id=key).json()["status"], "stored")
        self.assertEqual(self.post(event_id=key).json()["status"], "duplicate")
        self.assertEqual(self.post(event_id=key, raw_score=4).status_code, 409)
        self.post()
        self.assertEqual(len(self.get("events").json()["items"]), 2)
        self.assertEqual(self.snapshot()["modules"][0]["raw_score"], 3)

    def test_late_event_keeps_history_without_overwriting_latest(self):
        self.post(timestamp_ms=5000, raw_score=5)
        self.post(timestamp_ms=1000, raw_score=1)
        self.assertEqual(self.snapshot()["modules"][0]["raw_score"], 5)
        self.assertEqual([item["timestamp_ms"] for item in self.get("events").json()["items"]], [5000, 1000])

    def test_godmode_history_pagination_not_final_risk(self):
        for timestamp, score in enumerate((2, 3, 5, 3), start=1):
            self.post(module="godmode", timestamp_ms=timestamp * 1000, raw_score=score)
        first = self.get("sessions/s1/players/p1/history", limit=2).json()
        second = self.get("sessions/s1/players/p1/history", limit=2, after_sequence=first["next_after_sequence"]).json()
        self.assertEqual([item["raw_score"] for item in first["items"] + second["items"]], [2, 3, 5, 3])
        self.assertFalse(second["has_more"])
        self.assertIsNone(self.snapshot()["score"])
        self.assertEqual(self.get("sessions/s1/players/p1/history", module="aimbot").status_code, 422)

    def test_cursor_filter_binding_and_fixed_watermark(self):
        for i in range(3):
            self.post(timestamp_ms=i)
        first = self.get("events", limit=1, module="noclip").json()
        self.post(timestamp_ms=4000)
        cursor = first["next_cursor"]
        self.assertEqual(self.get("events", cursor=cursor, module="aimbot").status_code, 422)
        second = self.get("events", cursor=cursor, module="noclip", limit=100).json()
        self.assertEqual(len(second["items"]), 2)
        self.assertFalse(second["has_more"])
        new = self.get("events", module="noclip", after_sequence=second["through_sequence"]).json()
        self.assertEqual(len(new["items"]), 1)

    def test_combined_filters_and_literal_search(self):
        self.post(module="localguard", evidence={"submodule": "module_integrity", "text": "100%_match"})
        self.post(module="localguard", evidence={"submodule": "other"})
        self.post(session_id="s2", module="localguard", evidence={"submodule": "module_integrity"})
        result = self.get("events", session_id="s1", player_id="p1", module="localguard", submodule="module_integrity", q="%_").json()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(self.get("events", q="' OR 1=1 --").json()["items"], [])

    def test_index_budget_and_recreation(self):
        for i in range(5):
            self.post(timestamp_ms=i)
        bounded = self.make_service(sync_budget=2)
        result = bounded.overview()
        self.assertTrue(result["index"]["catching_up"])
        self.assertEqual(result["counts"]["events"], 2)
        bounded.overview()
        self.assertEqual(bounded.overview()["counts"]["events"], 5)
        recreated = self.make_service(index_path=self.root / "new-index.sqlite3")
        self.assertEqual(recreated.overview()["counts"]["events"], 5)

    def test_two_index_instances_refresh_concurrently(self):
        for i in range(4):
            self.post(timestamp_ms=i)
        other = self.make_service()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda service: service.overview(), (self.service, other)))
        self.assertEqual([result["counts"]["events"] for result in results], [4, 4])

    def test_storage_success_scoring_failure_retry_and_restart_recovery(self):
        self.fail_scoring = True
        key = str(uuid.uuid4())
        self.assertEqual(self.post(event_id=key).status_code, 503)
        self.assertEqual(len(self.get("events").json()["items"]), 1)
        self.assertEqual(self.snapshot()["modules"], [])
        self.fail_scoring = False
        self.assertEqual(self.post(event_id=key).json()["status"], "duplicate")
        self.assertEqual(len(self.snapshot()["modules"]), 1)
        self.fail_scoring = True
        self.post(module="godmode", raw_score=2)
        restart = ScoringStore(self.root / "scoring.sqlite3")
        recover(self.writer, restart)
        self.assertEqual(len(restart.get_event_delta_history("s1", "p1")), 1)
        recover(self.writer, restart)
        self.assertEqual(len(restart.get_event_delta_history("s1", "p1")), 1)

    def test_heartbeat_duplicate_does_not_refresh_liveness(self):
        self.assertEqual(self.heartbeat().status_code, 200)
        self.now += timedelta(seconds=31)
        self.assertEqual(self.heartbeat().status_code, 200)
        status = self.get("sessions/s1/players/p1/status").json()
        self.assertEqual(status["sources"][0]["state"], "stale")
        self.assertEqual(status["sources"][0]["components"][0]["state"], "stale")
        self.assertEqual(status["state"], "unknown")

    def test_heartbeat_scoping_and_multiple_sources(self):
        self.heartbeat()
        self.heartbeat(client_id="second", sequence=1)
        other = valid_heartbeat(session_id="other", player_id="p1", client_id="third")
        self.heartbeats.accept(other)
        status = self.get("sessions/s1/players/p1/status").json()
        self.assertEqual(len(status["sources"]), 2)
        self.assertEqual(self.get("sessions/s1/players/other/status").json()["sources"], [])

    def test_component_age_and_explicit_stop(self):
        payload = valid_heartbeat(session_id="s1", player_id="p1")
        payload["components"]["localguard_input_signature"]["age_ms"] = 110000
        self.heartbeats.accept(payload)
        self.assertEqual(self.get("sessions/s1/players/p1/status").json()["sources"][0]["components"][0]["state"], "stale")
        payload["sequence"] += 1
        payload["status"] = "stopped"
        payload["components"]["localguard_input_signature"]["status"] = "stopped"
        self.heartbeats.accept(payload)
        self.now += timedelta(hours=1)
        self.assertEqual(self.get("sessions/s1/players/p1/status").json()["sources"][0]["state"], "stopped")

    def test_operational_event_and_error_zero_not_final_normal(self):
        self.post(module="selfdefense", evidence={"kind": "module_health"}, raw_score=0)
        self.post(module="aimbot", evidence={"status": "ERROR"}, raw_score=0)
        result = self.get("overview").json()
        self.assertEqual(result["counts"]["operational_events"], 1)
        self.assertEqual(result["assessments"][0]["status"], "UNKNOWN")
        self.assertEqual(self.get("events").json()["items"][0]["event_kind"], "operational")

    def test_selfdefense_statuses_preserve_operational_state_by_kind_and_target(self):
        self.post(
            module="selfdefense",
            timestamp_ms=1000,
            evidence={
                "kind": "file_integrity",
                "component": "integrity",
                "status": "ERROR",
                "scan_complete": False,
                "scope": "release_files",
            },
            reasons=["BASELINE_READ_FAILED"],
            raw_score=0,
        )
        self.post(
            module="selfdefense",
            timestamp_ms=2000,
            evidence={
                "kind": "debugger_presence",
                "component": "anti_debug",
                "status": "DETECTED",
                "scan_complete": True,
                "scope": "launcher_registered",
            },
            reasons=["DEBUGGER_PRESENT"],
            raw_score=0,
        )
        self.post(
            module="selfdefense",
            timestamp_ms=3000,
            evidence={
                "kind": "module_health",
                "component": "watchdog",
                "target_module": "aimbot",
                "status": "alive",
                "scope": "launcher_registry",
            },
            reasons=[],
            raw_score=0,
        )
        self.post(
            module="selfdefense",
            timestamp_ms=4000,
            evidence={
                "kind": "module_health",
                "component": "watchdog",
                "target_module": "godmode",
                "status": "exited",
                "scope": "launcher_registry",
            },
            reasons=["PROCESS_EXITED"],
            raw_score=0,
        )

        overview = self.get("overview", session_id="s1", player_id="p1").json()
        statuses = overview["selfdefense_statuses"]

        self.assertEqual(len(statuses), 4)

        by_key = {
            (item["kind"], item["target_module"]): item
            for item in statuses
        }

        integrity = by_key[("file_integrity", None)]
        self.assertEqual(integrity["status"], "ERROR")
        self.assertFalse(integrity["scan_complete"])
        self.assertEqual(integrity["raw_score"], 0)

        anti_debug = by_key[("debugger_presence", None)]
        self.assertEqual(anti_debug["status"], "DETECTED")
        self.assertTrue(anti_debug["scan_complete"])
        self.assertEqual(anti_debug["raw_score"], 0)

        self.assertEqual(
            by_key[("module_health", "aimbot")]["status"],
            "alive",
        )
        self.assertEqual(
            by_key[("module_health", "godmode")]["status"],
            "exited",
        )

    def test_session_list_pagination_and_missing_status(self):
        self.post(session_id="a")
        self.post(session_id="b")
        first = self.get("overview", limit=1).json()
        second = self.get("overview", after_session=first["session_page"]["next_after_session"], limit=1).json()
        self.assertEqual([first["sessions"][0]["id"], second["sessions"][0]["id"]], ["a", "b"])
        self.assertEqual(self.get("sessions/a/players/p1/status").json()["sources"], [])

    def test_storage_errors_are_reported_without_internal_details(self):
        original = self.writer.iter_stored
        def fail(**kwargs):
            raise OSError("private-path-and-secret")
        self.writer.iter_stored = fail
        result = self.get("overview")
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("private-path", result.text)
        self.writer.iter_stored = original

    def test_heartbeat_only_sessions_are_discoverable_without_fabricated_events(self):
        self.heartbeat()
        overview = self.get("overview").json()
        self.assertEqual(overview["sessions"][0]["id"], "s1")
        self.assertEqual(overview["players"][0]["id"], "p1")
        self.assertEqual(overview["counts"]["events"], 0)
        self.assertEqual(overview["counts"]["scope"], "indexed_events")
        self.assertIsNone(overview["sessions"][0]["max_observed_timestamp_ms"])

    def test_latest_heartbeat_identity_replaces_old_identity_for_queries(self):
        payload = valid_heartbeat(session_id="s1", player_id="old")
        self.heartbeats.accept(payload)
        payload = {**payload, "sequence": 3, "player_id": "new"}
        self.heartbeats.accept(payload)
        self.assertEqual(self.heartbeats.list_latest("s1", "old"), [])
        self.assertEqual(len(self.heartbeats.list_latest("s1", "new")), 1)
        self.assertEqual(self.heartbeats.session_inventory()[0]["player_ids"], ["new"])

    def test_final_verdict_three_statuses_are_preserved_without_numeric_scores(self):
        self.post()
        for status in ("SUSPICIOUS", "INCONCLUSIVE", "NO_ACTIVE_EVIDENCE"):
            def verdict(session, player):
                return {"version": "conservative-v1", "session_id": session, "player_id": player,
                        "status": status, "assessment_complete": status != "INCONCLUSIVE",
                        "evidence_unit_count": 1 if status == "SUSPICIOUS" else 0,
                        "active_module_count": 1 if status == "SUSPICIOUS" else 0,
                        "active_modules": ["godmode"] if status == "SUSPICIOUS" else [],
                        "unresolved_modules": [], "reason_codes": ["TEST_CODE"]}
            service = self.make_service(verdict_provider=verdict)
            result = service.snapshot("s1", "p1")
            self.assertEqual(result["status"], status)
            self.assertEqual(result["final_verdict"]["assessment_complete"], status != "INCONCLUSIVE")
            self.assertIsNone(result["score"])
            self.assertIsNone(result["confidence"])
            self.assertEqual(service.overview()["assessments"][0]["status"], status)

    def test_missing_snapshot_never_becomes_no_active_evidence(self):
        def unexpected(*args):
            raise AssertionError("must not assess absent data")
        result = self.make_service(verdict_provider=unexpected).snapshot("missing", "p1")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["data_state"], "missing")
        self.assertFalse(result["assessment_available"])

    def test_remote_404_is_missing_but_503_is_unavailable(self):
        self.post()
        def missing(*args):
            raise CentralQueryError(404)
        result = self.make_service(verdict_provider=missing).snapshot("s1", "p1")
        self.assertEqual(result["data_state"], "missing")
        def unavailable(*args):
            raise CentralQueryError(503)
        self.service.verdict_provider = unavailable
        response = self.get("sessions/s1/players/p1/snapshot")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("normal", response.text)

    def test_incorrect_verdict_identity_is_rejected(self):
        self.post()
        self.service.verdict_provider = lambda *args: {"status": "SUSPICIOUS", "session_id": "wrong", "player_id": "p1", "reason_codes": []}
        self.assertEqual(self.get("sessions/s1/players/p1/snapshot").status_code, 503)

    def launcher_heartbeat(self, *, client_id="launcher-1000", sequence=1, session_id="s1", player_id="p1", status="healthy", phase="running", module_status="running", module_age=100, module_required=True):
        payload = valid_heartbeat(session_id=session_id, player_id=player_id, client_id=client_id, sequence=sequence, status=status)
        payload["components"] = {
            "launcher": {"status": "stopped" if status == "stopped" else "running", "required": True, "pid": 1234,
                         "updated_at_ms": 4900, "stale_after_ms": 30000, "age_ms": 100,
                         "details": {"phase": phase, "modules": 1, "loop_age_ms": 10}},
            "godmode": {"status": module_status, "required": module_required, "pid": 1235,
                        "updated_at_ms": 4900, "stale_after_ms": 30000, "age_ms": module_age,
                        "details": {"launcher_status": "RUNNING", "mode": "continuous", "runs": 1, "restarts": 0}},
        }
        return self.client.post("/api/heartbeat", json=payload, headers={"Authorization": "Bearer heartbeat-test"})

    def test_overview_uses_launcher_receipt_and_populates_module_status(self):
        self.assertEqual(self.launcher_heartbeat().status_code, 200)
        overview = self.get("overview").json()
        self.assertTrue(overview["capabilities"]["launcher_heartbeat"])
        self.assertEqual(overview["connection"]["Launcher"]["state"], "online")
        self.assertEqual(overview["connection"]["Launcher"]["scope"], "returned_session_page")
        self.assertEqual(overview["connection"]["Receiver"]["scope"], "local_detection_storage")
        self.assertEqual(overview["connection"]["Scoring"]["state"], "online")
        module = next(row for row in overview["module_statuses"] if row["id"] == "godmode")
        self.assertEqual(module["state"], "running")
        self.assertEqual((module["session_id"], module["player_id"]), ("s1", "p1"))
        self.assertEqual(module["last_seen_at"], self.now.isoformat())
        self.assertEqual(self.get("sessions/s1/players/p1/status").json()["state"], "healthy")

    def test_scanner_and_prefix_without_launcher_component_do_not_prove_launcher_health(self):
        self.heartbeat()
        self.heartbeat(client_id="launcher-1234", sequence=1)
        overview = self.get("overview").json()
        self.assertEqual(overview["connection"]["Launcher"]["state"], "unknown")
        self.assertEqual(overview["module_statuses"], [])

    def test_overview_launcher_expires_and_duplicate_does_not_revive_it(self):
        self.launcher_heartbeat()
        self.now += timedelta(seconds=30)
        self.launcher_heartbeat()
        overview = self.get("overview").json()
        self.assertEqual(overview["connection"]["Launcher"]["state"], "stale")
        self.assertFalse(overview["launcher_statuses"][0]["connected"])
        self.assertEqual(next(row for row in overview["module_statuses"] if row["id"] == "godmode")["state"], "stale")

    def test_launcher_new_sequence_restores_connection(self):
        self.launcher_heartbeat()
        self.now += timedelta(seconds=31)
        self.assertEqual(self.get("overview").json()["connection"]["Launcher"]["state"], "stale")
        self.launcher_heartbeat(sequence=2)
        self.assertEqual(self.get("overview").json()["connection"]["Launcher"]["state"], "online")

    def test_explicit_launcher_stop_is_not_reinterpreted_as_network_outage(self):
        self.launcher_heartbeat(status="stopped", phase="stopped", module_status="stopped")
        self.now += timedelta(hours=1)
        overview = self.get("overview").json()
        self.assertEqual(overview["connection"]["Launcher"]["state"], "stopped")
        self.assertFalse(overview["launcher_statuses"][0]["connected"])

    def test_new_execution_wins_over_delayed_old_launcher_packet(self):
        self.launcher_heartbeat(client_id="launcher-1000", status="stopped", phase="stopped", module_status="stopped")
        self.launcher_heartbeat(client_id="launcher-2000", status="degraded", module_status="failed")
        self.now += timedelta(seconds=1)
        self.launcher_heartbeat(client_id="launcher-1000", sequence=2)
        overview = self.get("overview").json()
        source = overview["launcher_statuses"][0]["source"]
        self.assertEqual(source["client_id"], "launcher-2000")
        self.assertEqual(overview["connection"]["Launcher"]["state"], "degraded")

    def test_required_component_expiry_degrades_even_fresh_healthy_sender(self):
        self.launcher_heartbeat(module_age=30001)
        status = self.get("sessions/s1/players/p1/status").json()
        self.assertEqual(status["state"], "degraded")
        self.assertTrue(status["launcher"]["connected"])
        self.assertEqual(self.get("overview").json()["connection"]["Launcher"]["state"], "degraded")

    def test_optional_missing_module_does_not_fail_healthy_launcher(self):
        self.launcher_heartbeat(module_status="unknown", module_required=False)
        self.assertEqual(self.get("overview").json()["connection"]["Launcher"]["state"], "online")

    def test_launcher_stopping_phase_is_visible(self):
        self.launcher_heartbeat(phase="stopping")
        self.assertEqual(self.get("overview").json()["connection"]["Launcher"]["state"], "stopping")

    def test_overview_selected_pair_does_not_inherit_other_pc_connection(self):
        self.launcher_heartbeat()
        self.launcher_heartbeat(session_id="s2", player_id="p2", client_id="launcher-2000", status="degraded", module_status="failed")
        overview = self.get("overview", session_id="s2", player_id="p2").json()
        self.assertEqual(overview["connection"]["Launcher"]["state"], "degraded")
        self.assertEqual(overview["connection"]["Launcher"]["scope"], "selected_session_player")
        self.assertTrue(all(row["player_id"] == "p2" for row in overview["module_statuses"]))
        missing = self.get("overview", session_id="missing", player_id="p1").json()
        self.assertEqual(missing["connection"]["Launcher"]["state"], "unknown")
        self.assertEqual(self.get("overview", session_id="s1").status_code, 422)

    def test_overview_page_connection_does_not_include_omitted_session(self):
        self.launcher_heartbeat(session_id="a")
        self.launcher_heartbeat(session_id="b", status="degraded", module_status="failed")
        self.assertEqual(self.get("overview", limit=1).json()["connection"]["Launcher"]["state"], "online")
        second = self.get("overview", after_session="a", limit=1).json()
        self.assertEqual(second["connection"]["Launcher"]["state"], "degraded")

    def test_status_does_not_claim_complete_health_with_truncated_source_list(self):
        self.launcher_heartbeat()
        for index in range(101):
            self.heartbeats.accept(valid_heartbeat(session_id="s1", player_id="p1", client_id=f"a{index:03}"))
        status = self.get("sessions/s1/players/p1/status").json()
        self.assertTrue(status["has_more_sources"])
        self.assertEqual(status["state"], "unknown")

    def test_local_scoring_read_failure_is_reported_unavailable(self):
        def unavailable(*args):
            raise RuntimeError("synthetic read error")
        self.scoring.get_player_snapshot = unavailable
        overview = self.get("overview").json()
        self.assertEqual(overview["connection"]["Scoring"]["state"], "unavailable")

    def test_no_heartbeat_store_means_capability_false_and_unknown(self):
        result = self.make_service(heartbeat_store=None).overview()
        self.assertFalse(result["capabilities"]["launcher_heartbeat"])
        self.assertEqual(result["connection"]["Launcher"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
