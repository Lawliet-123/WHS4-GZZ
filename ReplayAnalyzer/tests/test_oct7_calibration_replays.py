"""Check the Oct 7 public NORMAL bundle without rerunning sensors or HTTP.

Only the three explicitly named public artifacts are read. These assertions
verify recorded provenance and cross-file agreement; they do not independently
prove the tester's NORMAL label, human gameplay, private-source hashes, live
Server state, or a successful positive capture that has not been published.
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


if __name__ == "__main__":
    unittest.main()
