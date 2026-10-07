"""Check the Oct 7 public NORMAL/positive bundles without sensors or HTTP.

Only the three explicitly named public artifacts are read. These assertions
verify recorded provenance and cross-file agreement; they do not independently
prove the tester's labels, human gameplay, private-source hashes, or live
Server state. Positive assertions preserve the recorded original ESP lifecycle.
"""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unittest
import uuid

from shared.schema import decode_event, EVENT_FIELDS


SESSION = "normal_calibration_20261007_run01"
PLAYER = "calibration_player"
FOLDER = Path(__file__).resolve().parents[1] / "replay-data" / "esp" / SESSION
FILES = ("manifest.json", "events.jsonl", "calibration-evidence.json")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|\+00:00)$")
PRIVATE_KEYS = {
    "username", "user", "sourceuser", "targetuser", "accountname", "computer",
    "computername", "hostname", "machinename", "windowtitle", "title",
    "hostidentity", "calltrace", "password", "token", "testtokens", "apitoken",
    "authorization", "cookie", "credential", "credentials", "secret", "privatekey",
    "rawpayload", "environment", "env",
}
PRIVATE_VALUE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|Bearer\s+|https?://|"
    r"(?:^|\s)(?:/[A-Za-z0-9_.-]+){2,}|"
    r"synthetic-browser-(?:receiver|heartbeat|dashboard)-|"
    r"-----BEGIN [^-]*PRIVATE KEY-----)", re.IGNORECASE,
)


def assert_public(test, value):
    """Reject identifying fields/paths without echoing their values on failure."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(char for char in key.casefold() if char.isalnum())
            if normalized in PRIVATE_KEYS or normalized.endswith(("password", "token", "apikey", "secret")):
                test.fail("Forbidden identifying or authentication field in public bundle")
            if normalized.endswith("path") and isinstance(item, str) and any(char in item for char in ("/", "\\")):
                test.fail("Full path in public bundle")
            if normalized.endswith("sha256") and isinstance(item, str):
                test.assertTrue(SHA256.fullmatch(item) is not None, "Malformed public SHA-256")
            if normalized == "sensoreventid":
                test.assertTrue(isinstance(item, str) and re.fullmatch(r"sensor-sha256:[0-9a-f]{64}", item) is not None,
                                "Unfingerprinted sensor event ID")
            assert_public(test, item)
    elif isinstance(value, list):
        for item in value:
            assert_public(test, item)
    elif isinstance(value, str) and PRIVATE_VALUE.search(value):
        test.fail("Private path, endpoint, credential, or operational secret in public bundle")


def utc(value):
    if not isinstance(value, str) or UTC.fullmatch(value) is None:
        raise AssertionError("Explicit UTC metadata required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise AssertionError("UTC timezone required")
    return parsed


class Oct7NormalCalibrationReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No source manifest/raw/session setup or SQLite is opened here.
        cls.public_bytes = {name: (FOLDER / name).read_bytes() for name in FILES}
        cls.manifest = json.loads(cls.public_bytes["manifest.json"])
        cls.proof = json.loads(cls.public_bytes["calibration-evidence.json"])
        cls.events = [decode_event(line) for line in cls.public_bytes["events.jsonl"].splitlines() if line.strip()]

    def test_public_artifacts_exclude_private_raw_paths_identity_and_credentials(self):
        for artifact in (self.manifest, self.events, self.proof):
            assert_public(self, artifact)

    def test_normal_label_and_observed_scores_are_preserved_not_cleaned_to_zero(self):
        self.assertEqual((self.manifest["session_id"], self.manifest["player_id"], self.manifest["label"]),
                         (SESSION, PLAYER, "NORMAL"))
        self.assertEqual(self.manifest["event_count"], 48)
        self.assertEqual(len(self.events), 48)
        self.assertEqual(Counter(event["raw_score"] for event in self.events), Counter({1: 38, 2: 10}))
        for key in ("cheat_type", "cheat_start_ms", "cheat_end_ms"):
            self.assertIsNone(self.manifest[key])
        self.assertEqual(self.manifest["source"]["session_status"], "completed")
        self.assertIsNone(self.manifest["source"]["producer"]["revision"])

    def test_common_events_keep_the_shared_seven_fields_and_original_sequence_of_values(self):
        for event in self.events:
            self.assertEqual(set(event), set(EVENT_FIELDS))
            self.assertEqual((event["session_id"], event["player_id"], event["module"]), (SESSION, PLAYER, "esp"))
            self.assertIs(type(event["timestamp_ms"]), int)
            self.assertGreaterEqual(event["timestamp_ms"], 0)
            self.assertIsNot(event["evidence"].get("synthetic"), True)

    def test_late_sensor_timestamp_is_not_reordered_as_server_sequence(self):
        # The recorded Sysmon observation arrived after newer overlay samples.
        # Timestamp is producer time; Shared sequence is storage order. Preserve
        # this real late arrival rather than sorting or rewriting its timestamp.
        self.assertEqual([event["timestamp_ms"] for event in self.events[2:5]], [6496, 6496, 5862])
        self.assertEqual([row["sequence"] for row in self.proof["events"][2:5]], [3, 4, 5])

    def test_session_bounds_and_owned_poc_absence_do_not_fabricate_human_behavior(self):
        observation = self.manifest["source"]["calibration_observation"]
        validation = self.manifest["source"]["realgame_validation"]
        start, end = utc(observation["session_start_utc"]), utc(observation["session_end_utc"])
        self.assertEqual(start.isoformat(timespec="milliseconds"), "2026-10-07T12:09:43.847+00:00")
        self.assertEqual(end.isoformat(timespec="milliseconds"), "2026-10-07T12:11:43.878+00:00")
        self.assertGreaterEqual((end - start).total_seconds(), 120)
        self.assertLessEqual(max(event["timestamp_ms"] for event in self.events), (end - start).total_seconds() * 1000)
        self.assertEqual(observation["timestamp_ms_basis"], "milliseconds since session_start_utc")
        self.assertEqual(observation["central_telemetry"], "disposable_loopback")
        self.assertEqual(validation["kind"], "no_esp_spawned")
        self.assertFalse(validation["remote_rendering_claimed"])
        self.assertEqual(observation["owned_esp_process_after_off"]["status"], "NOT_STARTED")
        self.assertEqual(observation["preexisting_poc_absence"], "NOT_ASSESSED")
        self.assertEqual(observation["human_behavior_status"], "NOT_OBSERVED")
        for key in ("human_behavior_start_ms", "human_behavior_end_ms", "actual_remote_geometry",
                    "program_spawn_bounds_ms", "program_exit_ms"):
            self.assertIsNone(observation[key])
        delta = observation["game_module_snapshot_delta"]
        self.assertEqual(delta["status"], "UNCHANGED")
        self.assertEqual(delta["added_identity_sha256"], [])
        self.assertEqual(delta["removed_identity_sha256"], [])
        self.assertEqual(delta["attribution"], "NOT_ASSESSED")

    def test_public_proof_hash_matches_manifest_without_opening_private_sources(self):
        source = self.manifest["source"]
        self.assertEqual(source["calibration_observation"]["server_evidence_sha256"],
                         hashlib.sha256(self.public_bytes["calibration-evidence.json"]).hexdigest())
        # These are references to private ORIGINAL capture bytes, not hashes of
        # this privacy-filtered/canonicalized public export. Never reconstruct
        # or open the private source merely to make a public-artifact test pass.
        for key in ("manifest_sha256", "events_sha256"):
            self.assertTrue(SHA256.fullmatch(source[key]) is not None, "Malformed original-source hash reference")

    def test_recorded_ack_and_readback_counts_agree_with_exact_event_set(self):
        delivery = self.manifest["source"]["calibration_observation"]["shared_delivery"]
        self.assertEqual(delivery, {"acknowledged_this_run": 48, "failed": 0, "pending": 0, "queued_unique_events": 48})
        proof = self.proof
        self.assertEqual(proof["schema_version"], "meccha.esp-server-calibration-readback.v1")
        self.assertEqual(proof["result"], "SERVER_READBACK_VERIFIED")
        self.assertEqual((proof["session_id"], proof["player_id"], proof["module"]), (SESSION, PLAYER, "esp"))
        self.assertEqual(proof["event_count"], 48)
        self.assertEqual(len(proof["events"]), 48)
        self.assertEqual(proof["through_sequence"], 48)
        self.assertEqual(proof["request_count"], 51)
        self.assertEqual(proof["synthetic_events_created"], 0)
        self.assertEqual(proof["capture_provenance"], "NOT_ASSESSED")
        self.assertEqual(proof["shared_ack_provenance"], "NOT_ASSESSED")
        self.assertFalse(proof["snapshot_watermark_atomic"])
        self.assertEqual(proof["receipt_time_basis"], "server_acceptance_utc")
        self.assertEqual(set(proof["checks"]), {"exact_expected_event_set", "list_detail_equal",
                         "event_pagination_complete", "snapshot_verdict_equal", "declared_clock_metadata_equal",
                         "received_at_metadata_complete"})
        self.assertTrue(all(value is True for value in proof["checks"].values()))

    def test_readback_payloads_ids_sequence_and_clocks_match_public_events(self):
        rows = self.proof["events"]
        payloads = [{field: row[field] for field in EVENT_FIELDS} for row in rows]
        self.assertTrue(payloads == self.events, "Server readback payloads differ from the public Replay Events")
        ids = [row["id"] for row in rows]
        self.assertEqual(len(set(ids)), 48)
        self.assertEqual([row["sequence"] for row in rows], list(range(1, 49)))
        for row in rows:
            self.assertEqual(str(uuid.UUID(row["id"])), row["id"])
            self.assertEqual(row["event_kind"], "detection")
            self.assertEqual(row["time_basis"], "unknown")
            self.assertIsNone(row.get("observed_at_utc"))
            utc(row["received_at_utc"])
            self.assertIsNone(row["evidence_image"])
            self.assertIsNone(row["log_excerpt"])

    def test_actual_scoring_stays_inconclusive_and_does_not_invent_threshold_or_score(self):
        snapshot, verdict = self.proof["snapshot"], self.proof["final_verdict"]
        for item in (snapshot, verdict):
            self.assertEqual((item["session_id"], item["player_id"], item["status"]), (SESSION, PLAYER, "INCONCLUSIVE"))
        self.assertIsNone(snapshot["score"])
        self.assertIsNone(snapshot["confidence"])
        self.assertTrue(snapshot["assessment_available"])
        self.assertTrue(snapshot["final_verdict"] == verdict, "Snapshot and Final Verdict differ")
        self.assertFalse(verdict["assessment_complete"])
        self.assertEqual(verdict["active_modules"], [])
        self.assertEqual(verdict["evidence_unit_count"], 0)
        self.assertEqual(verdict["unresolved_modules"], ["esp"])
        self.assertEqual(verdict["reason_codes"], ["ASSESSMENT_INCOMPLETE"])
        explanations = snapshot["policy"]["module_evidence"]
        self.assertEqual(len(explanations), 1)
        self.assertEqual(explanations[0]["signal"]["calibration_mode"], "pending")
        self.assertIsNone(explanations[0]["signal"]["calibration_threshold"])
        self.assertIsNone(explanations[0]["signal"]["threshold_met"])
        self.assertEqual(len(snapshot["modules"]), 1)
        state, latest = snapshot["modules"][0], self.proof["events"][-1]
        self.assertEqual((state["event_id"], state["sequence"]), (latest["id"], latest["sequence"]))
        self.assertTrue({field: state[field] for field in EVENT_FIELDS} == self.events[-1],
                        "Scoring latest state differs from the original server Event")


class Oct7PositiveCalibrationReplayTests(unittest.TestCase):
    SESSION = "esp_calibration_20261007_run04"
    ORIGINAL_ESP_SHA256 = "897dd9da21b39e738306c79b8225a8b9bd35de8a52d038c83ce4d829f39da973"

    @classmethod
    def setUpClass(cls):
        folder = FOLDER.parent / cls.SESSION
        cls.public_bytes = {name: (folder / name).read_bytes() for name in FILES}
        cls.manifest = json.loads(cls.public_bytes["manifest.json"])
        cls.proof = json.loads(cls.public_bytes["calibration-evidence.json"])
        cls.events = [decode_event(line) for line in cls.public_bytes["events.jsonl"].splitlines() if line.strip()]
        cls.observation = cls.manifest["source"]["calibration_observation"]
        cls.validation = cls.manifest["source"]["realgame_validation"]

    def test_positive_public_artifacts_omit_private_identity_paths_and_credentials(self):
        for artifact in (self.manifest, self.events, self.proof):
            assert_public(self, artifact)

    def test_positive_label_preserves_all_actual_background_and_positive_scores(self):
        self.assertEqual((self.manifest["session_id"], self.manifest["player_id"], self.manifest["label"]),
                         (self.SESSION, PLAYER, "CHEAT"))
        self.assertEqual(self.manifest["cheat_type"], "ESP")
        self.assertEqual(self.manifest["event_count"], 126)
        self.assertEqual(len(self.events), 126)
        self.assertEqual(Counter(event["raw_score"] for event in self.events), Counter({1: 74, 2: 50, 3: 2}))
        self.assertEqual(self.manifest["source"]["session_status"], "completed")
        for event in self.events:
            self.assertEqual(set(event), set(EVENT_FIELDS))
            self.assertEqual((event["session_id"], event["player_id"], event["module"]),
                             (self.SESSION, PLAYER, "esp"))
            self.assertIs(type(event["timestamp_ms"]), int)
            self.assertGreaterEqual(event["timestamp_ms"], 0)
            self.assertIsNot(event["evidence"].get("synthetic"), True)

    def test_recorded_spawn_handle_read_and_exit_are_separate_observed_moments(self):
        observation, validation = self.observation, self.validation
        self.assertEqual(observation["program_spawn_bounds_ms"], [6862, 6893])
        self.assertEqual(validation["handle_open_observation_bounds_ms"], [7749, 7751])
        self.assertEqual((self.manifest["cheat_start_ms"], validation["first_read_ready_ms"],
                          self.manifest["cheat_end_ms"]), (7751, 31807, 93639))
        self.assertLess(observation["program_spawn_bounds_ms"][1], self.manifest["cheat_start_ms"])
        self.assertLess(self.manifest["cheat_start_ms"], validation["first_read_ready_ms"])
        self.assertLess(validation["first_read_ready_ms"], self.manifest["cheat_end_ms"])
        self.assertEqual(observation["program_exit_ms"], self.manifest["cheat_end_ms"])
        self.assertEqual(validation["kind"], "observed_original_esp_lifecycle")
        self.assertTrue(validation["launch_precedes_on"])
        self.assertEqual(validation["source_sha256"], self.ORIGINAL_ESP_SHA256)
        self.assertEqual(validation["on_basis"],
                         "successful pinned game handle opening, observed immediately after API return")
        self.assertEqual(validation["off_basis"], "observed exact child process exit")
        self.assertEqual(validation["fresh_read_heartbeat_count"], 123)
        self.assertLessEqual(validation["maximum_heartbeat_gap_seconds"], 2.)

    def test_remote_painter_proof_keeps_observed_frames_not_continuous_visibility_claim(self):
        geometry = self.observation["actual_remote_geometry"]
        self.assertTrue(self.validation["remote_rendering_claimed"])
        self.assertEqual(self.validation["remote_player_count_at_first_ready"], 5)
        self.assertEqual((geometry["first_observed_ms"], geometry["last_observed_ms"]), (31807, 93236))
        self.assertEqual((geometry["first_frame_sequence"], geometry["last_frame_sequence"]), (7, 1849))
        self.assertEqual(geometry["unique_frame_count"], 85)
        self.assertGreaterEqual(geometry["unique_frame_count"], 2)
        self.assertEqual((geometry["remote_box_lines"], geometry["remote_skeleton_lines"]), (646, 2136))
        self.assertGreaterEqual(geometry["last_observed_epoch"] - geometry["first_observed_epoch"], 60.)
        self.assertLessEqual(self.manifest["cheat_start_ms"], geometry["first_observed_ms"])
        self.assertLess(geometry["last_observed_ms"], self.manifest["cheat_end_ms"])
        self.assertFalse(geometry["continuous_visibility_claimed"])
        self.assertFalse(geometry["screenshot_verified"])
        self.assertGreater(geometry["maximum_observation_gap_seconds"], 2.)
        self.assertEqual(geometry["basis"], "first/last completed original remote geometry painter calls")
        self.assertIsNone(geometry["user_behavior_start_ms"])
        self.assertIsNone(geometry["user_behavior_end_ms"])

    def test_session_bounds_owned_exit_and_module_delta_do_not_invent_gameplay_or_injection(self):
        observation = self.observation
        start, end = utc(observation["session_start_utc"]), utc(observation["session_end_utc"])
        self.assertEqual(start.isoformat(timespec="milliseconds"), "2026-10-07T12:58:26.695+00:00")
        self.assertEqual(end.isoformat(timespec="milliseconds"), "2026-10-07T13:01:58.525+00:00")
        self.assertGreaterEqual((end - start).total_seconds(), 210.)
        self.assertLessEqual(max(event["timestamp_ms"] for event in self.events), (end - start).total_seconds() * 1000)
        self.assertEqual(observation["timestamp_ms_basis"], "milliseconds since session_start_utc")
        self.assertEqual(observation["central_telemetry"], "disposable_loopback")
        self.assertEqual(observation["owned_esp_process_after_off"],
                         {"basis": "owned subprocess handle confirms termination", "descendants": "NOT_ASSESSED", "status": "ABSENT"})
        self.assertEqual(observation["preexisting_poc_absence"], "NOT_ASSESSED")
        self.assertEqual(observation["human_behavior_status"], "NOT_OBSERVED")
        self.assertIsNone(observation["human_behavior_start_ms"])
        self.assertIsNone(observation["human_behavior_end_ms"])
        delta = observation["game_module_snapshot_delta"]
        self.assertEqual(delta["status"], "UNCHANGED")
        self.assertEqual(delta["added_identity_sha256"], [])
        self.assertEqual(delta["removed_identity_sha256"], [])
        self.assertEqual(delta["attribution"], "NOT_ASSESSED")

    def test_original_python_observations_stay_correlated_without_hiding_background_events(self):
        # Public records correlate the same observed source PID. This is not an
        # independent PID-birth or ownership attestation; the source proof remains private.
        direct = [event for event in self.events if event["evidence"].get("source_pid") == 44272
                  or event["evidence"].get("window_pid") == 44272]
        self.assertEqual(len(direct), 12)
        self.assertEqual(Counter(event["raw_score"] for event in direct), Counter({1: 10, 3: 2}))
        access = [event for event in direct if event["evidence"]["event_type"] == "process_access"]
        self.assertEqual([event["timestamp_ms"] for event in access], [7751, 11447])
        for event in access:
            self.assertEqual(event["evidence"]["source_image"], "python.exe")
            self.assertEqual(event["evidence"]["granted_access"], 0x1f3fff)
        for event in direct:
            self.assertGreaterEqual(event["timestamp_ms"], self.manifest["cheat_start_ms"])
            self.assertLessEqual(event["timestamp_ms"], self.manifest["cheat_end_ms"])
        self.assertEqual(len(self.events) - len(direct), 114)

    def test_public_readback_proof_hash_and_original_capture_hash_references_are_preserved(self):
        source = self.manifest["source"]
        self.assertEqual(self.observation["server_evidence_sha256"],
                         hashlib.sha256(self.public_bytes["calibration-evidence.json"]).hexdigest())
        for key in ("manifest_sha256", "events_sha256"):
            self.assertTrue(SHA256.fullmatch(source[key]) is not None, "Malformed original-source hash reference")
        self.assertIsNone(source["producer"]["revision"])

    def test_recorded_positive_delivery_and_server_readback_cover_exact126_events(self):
        self.assertEqual(self.observation["shared_delivery"],
                         {"acknowledged_this_run": 126, "failed": 0, "pending": 0, "queued_unique_events": 126})
        proof = self.proof
        self.assertEqual(proof["schema_version"], "meccha.esp-server-calibration-readback.v1")
        self.assertEqual(proof["result"], "SERVER_READBACK_VERIFIED")
        self.assertEqual((proof["session_id"], proof["player_id"], proof["module"]), (self.SESSION, PLAYER, "esp"))
        self.assertEqual((proof["event_count"], len(proof["events"]), proof["through_sequence"], proof["request_count"]),
                         (126, 126, 126, 129))
        self.assertEqual(proof["synthetic_events_created"], 0)
        self.assertEqual(proof["capture_provenance"], "NOT_ASSESSED")
        self.assertEqual(proof["shared_ack_provenance"], "NOT_ASSESSED")
        self.assertFalse(proof["snapshot_watermark_atomic"])
        self.assertEqual(proof["receipt_time_basis"], "server_acceptance_utc")
        self.assertEqual(set(proof["checks"]), {"exact_expected_event_set", "list_detail_equal",
                         "event_pagination_complete", "snapshot_verdict_equal", "declared_clock_metadata_equal",
                         "received_at_metadata_complete"})
        self.assertTrue(all(value is True for value in proof["checks"].values()))

    def test_server_payloads_ids_sequence_and_explicit_utc_receipts_are_not_rewritten(self):
        rows = self.proof["events"]
        self.assertTrue([{field: row[field] for field in EVENT_FIELDS} for row in rows] == self.events,
                        "Positive readback differs from the exact public Replay Events")
        self.assertEqual(len({row["id"] for row in rows}), 126)
        self.assertEqual([row["sequence"] for row in rows], list(range(1, 127)))
        for row in rows:
            self.assertEqual(str(uuid.UUID(row["id"])), row["id"])
            self.assertEqual(row["event_kind"], "detection")
            self.assertEqual(row["time_basis"], "unknown")
            self.assertIsNone(row.get("observed_at_utc"))
            # json.loads preserves the exact stored UTC string; receipt is
            # separate metadata, not the unknown producer observation time.
            self.assertEqual(utc(row["received_at_utc"]).date().isoformat(), "2026-10-07")
            self.assertIsNone(row["evidence_image"])
            self.assertIsNone(row["log_excerpt"])

    def test_positive_scoring_retains_pending_inconclusive_and_latest_not_peak_event(self):
        snapshot, verdict = self.proof["snapshot"], self.proof["final_verdict"]
        for item in (snapshot, verdict):
            self.assertEqual((item["session_id"], item["player_id"], item["status"]),
                             (self.SESSION, PLAYER, "INCONCLUSIVE"))
        self.assertIsNone(snapshot["score"])
        self.assertIsNone(snapshot["confidence"])
        self.assertTrue(snapshot["assessment_available"])
        self.assertTrue(snapshot["final_verdict"] == verdict, "Snapshot and Final Verdict differ")
        self.assertFalse(verdict["assessment_complete"])
        self.assertEqual(verdict["active_modules"], [])
        self.assertEqual(verdict["evidence_unit_count"], 0)
        self.assertEqual(verdict["unresolved_modules"], ["esp"])
        self.assertEqual(verdict["reason_codes"], ["ASSESSMENT_INCOMPLETE"])
        signals = snapshot["policy"]["module_evidence"]
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["signal"]["calibration_mode"], "pending")
        self.assertIsNone(signals[0]["signal"]["calibration_threshold"])
        self.assertIsNone(signals[0]["signal"]["threshold_met"])
        self.assertEqual(len(snapshot["modules"]), 1)
        state, latest = snapshot["modules"][0], self.proof["events"][-1]
        self.assertEqual((state["event_id"], state["sequence"]), (latest["id"], 126))
        self.assertTrue({field: state[field] for field in EVENT_FIELDS} == self.events[-1],
                        "Scoring latest state differs from the original server Event")
        self.assertEqual(state["raw_score"], 1.)
        self.assertEqual(max(event["raw_score"] for event in self.events), 3.)


if __name__ == "__main__":
    unittest.main()
