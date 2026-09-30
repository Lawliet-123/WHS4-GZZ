import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.receiver import create_heartbeat_router, create_router
from server.receiver.heartbeat_store import HeartbeatConflict, HeartbeatStore
from server.receiver.router import bearer_token_verifier


def valid_heartbeat(**changes):
    payload = {
        "schema_version": "meccha-heartbeat-3",
        "message_type": "heartbeat",
        "session_id": "yara_test_001",
        "player_id": "player_042",
        "client_id": "8e6c1d9a0f3246ac9b754b738ce6ad92",
        "sequence": 2,
        "timestamp_ms": 5000,
        "sent_at_utc": "2026-09-27T00:00:00+00:00",
        "status": "healthy",
        "components": {
            "localguard_input_signature": {
                "status": "running", "required": True, "pid": 31000,
                "updated_at_ms": 4900, "stale_after_ms": 110000,
                "age_ms": 100, "details": {"scanner": "yara"},
            }
        },
        "transport": {
            "configured": True, "consecutive_failures": 0,
            "last_success_sequence": 1, "last_error_type": None,
        },
    }
    payload.update(changes)
    return payload


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="meccha-heartbeat-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "heartbeat.sqlite3"
        self.now = "2026-09-30T12:00:00+00:00"
        self.store = HeartbeatStore(self.path, clock=lambda: self.now)
        self.client = self.make_client(self.store)
        self.addCleanup(self.client.close)

    @staticmethod
    def make_client(store):
        app = FastAPI()
        app.include_router(create_heartbeat_router(
            store, verify_token=bearer_token_verifier("heartbeat-token"),
        ))
        return TestClient(app)

    def post(self, payload=None):
        return self.client.post(
            "/api/heartbeat", json=payload if payload is not None else valid_heartbeat(),
            headers={"Authorization": "Bearer heartbeat-token"},
        )

    def latest(self):
        return self.store.latest("yara_test_001", valid_heartbeat()["client_id"])

    def count(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]

    def test_supplied_v3_example_is_stored_and_exact_ack_returned(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "accepted": True, "session_id": "yara_test_001",
            "client_id": valid_heartbeat()["client_id"], "sequence": 2,
        })
        self.assertEqual(self.latest()["payload"], valid_heartbeat())
        self.assertEqual(self.latest()["received_at_utc"], self.now)

    def test_sequence_gaps_are_accepted(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post(valid_heartbeat(sequence=5)).status_code, 200)
        self.assertEqual(self.latest()["sequence"], 5)
        self.assertEqual(self.count(), 2)

    def test_duplicate_returns_same_ack_without_refreshing_liveness(self):
        first = self.post()
        original_time = self.latest()["received_at_utc"]
        self.now = "2026-09-30T12:10:00+00:00"
        # JSON ordering and whitespace do not alter identity.
        payload = dict(reversed(list(valid_heartbeat().items())))
        duplicate = self.post(payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json(), first.json())
        self.assertEqual(self.latest()["received_at_utc"], original_time)
        self.assertEqual(self.count(), 1)

    def test_same_sequence_different_content_is_409(self):
        self.post()
        self.assertEqual(self.post(valid_heartbeat(status="degraded")).status_code, 409)
        self.assertEqual(self.latest()["payload"]["status"], "healthy")
        self.assertEqual(self.count(), 1)

    def test_lower_sequence_is_409_even_if_its_historical_body_matches(self):
        self.post()
        self.post(valid_heartbeat(sequence=7))
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.post(valid_heartbeat(sequence=1)).status_code, 409)
        self.assertEqual(self.latest()["sequence"], 7)
        self.assertEqual(self.count(), 2)

    def test_new_sequence_updates_server_time(self):
        self.post()
        self.now = "2026-09-30T12:01:00+00:00"
        self.post(valid_heartbeat(sequence=3, sent_at_utc="2000-01-01T00:00:00Z"))
        self.assertEqual(self.latest()["received_at_utc"], self.now)

    def test_new_client_id_can_restart_at_one(self):
        self.post(valid_heartbeat(sequence=99))
        response = self.post(valid_heartbeat(client_id="new_client", sequence=1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.latest("yara_test_001", "new_client")["sequence"], 1)
        self.assertEqual(self.latest()["sequence"], 99)

    def test_different_sessions_have_independent_sequences(self):
        self.post(valid_heartbeat(sequence=99))
        self.assertEqual(self.post(valid_heartbeat(session_id="another_session", sequence=1)).status_code, 200)

    def test_restart_preserves_sequence_and_duplicate_liveness(self):
        original = self.post()
        original_time = self.latest()["received_at_utc"]
        restarted = HeartbeatStore(self.path, clock=lambda: "2026-10-01T00:00:00Z")
        with self.make_client(restarted) as client:
            headers = {"Authorization": "Bearer heartbeat-token"}
            duplicate = client.post("/api/heartbeat", json=valid_heartbeat(), headers=headers)
            stale = client.post("/api/heartbeat", json=valid_heartbeat(sequence=1), headers=headers)
        self.assertEqual(duplicate.json(), original.json())
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(restarted.latest("yara_test_001", valid_heartbeat()["client_id"])["received_at_utc"], original_time)
        self.assertEqual(self.count(), 1)

    def test_concurrent_identical_requests_store_once(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(lambda _: self.store.accept(valid_heartbeat()), range(12)))
        self.assertEqual(sum(not receipt.duplicate for receipt in receipts), 1)
        self.assertEqual(self.count(), 1)

    def test_concurrent_conflicting_requests_have_one_winner(self):
        def send(payload):
            try:
                return self.store.accept(payload)
            except HeartbeatConflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, [valid_heartbeat(), valid_heartbeat(status="degraded")]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.count(), 1)

    def test_heartbeat_is_not_sent_to_detection_writer_or_scoring(self):
        calls = []
        def forbidden(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("heartbeat entered detection pipeline")
        app = FastAPI()
        verifier = bearer_token_verifier("heartbeat-token")
        app.include_router(create_router(forbidden, forbidden, verify_token=verifier))
        app.include_router(create_heartbeat_router(self.store, verify_token=verifier))
        with TestClient(app) as client:
            response = client.post("/api/heartbeat", json=valid_heartbeat(), headers={"Authorization": "Bearer heartbeat-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [])

    def test_missing_or_wrong_token_is_401_without_storage(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            with self.subTest(headers=headers):
                response = self.client.post("/api/heartbeat", json=valid_heartbeat(), headers=headers)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.count(), 0)

    def test_incorrect_content_type_is_415(self):
        response = self.client.post("/api/heartbeat", content="{}", headers={"Authorization": "Bearer heartbeat-token"})
        self.assertEqual(response.status_code, 415)

    def test_non_ascii_credentials_fail_verification_without_server_error(self):
        self.assertFalse(bearer_token_verifier("heartbeat-token")("Bearer 잘못된토큰"))

    def test_oversized_body_is_413(self):
        payload = valid_heartbeat()
        payload["components"]["localguard_input_signature"]["details"] = {"blob": "x" * (256 * 1024)}
        self.assertEqual(self.post(payload).status_code, 413)
        self.assertEqual(self.count(), 0)

    def test_streamed_oversized_body_is_413(self):
        response = self.client.post(
            "/api/heartbeat", content=iter([b" " * 150000, b" " * 150000]),
            headers={"Authorization": "Bearer heartbeat-token", "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_malformed_duplicate_key_nonfinite_and_invalid_utf8_are_422(self):
        body = json.dumps(valid_heartbeat()).encode()
        cases = [b"{", b"\xff", body[:-1] + b', "sequence": 2}', body.replace(b'"sequence": 2', b'"sequence": NaN'),
                 body.replace(b'"scanner": "yara"', b'"scanner": 1e400')]
        for case in cases:
            with self.subTest(body=case[:20]):
                response = self.client.post("/api/heartbeat", content=case, headers={"Authorization": "Bearer heartbeat-token", "Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_v2_hwid_unknown_fields_missing_fields_and_invalid_status_are_422(self):
        cases = [valid_heartbeat(schema_version="meccha-heartbeat-2"), valid_heartbeat(hwid="a" * 64),
                 valid_heartbeat(extra="not permitted"), valid_heartbeat(status="invented")]
        missing = valid_heartbeat()
        del missing["components"]
        cases.append(missing)
        with_hwid_flag = valid_heartbeat()
        with_hwid_flag["transport"]["hwid_included_on_wire"] = False
        cases.append(with_hwid_flag)
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_integer_fields_reject_bool_float_negative_and_overflow(self):
        for changes in ({"sequence": True}, {"sequence": 2.0}, {"sequence": 0},
                        {"sequence": 2**63}, {"timestamp_ms": -1}, {"timestamp_ms": True}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post(valid_heartbeat(**changes)).status_code, 422)

    def test_invalid_identifiers_and_component_names_are_422(self):
        for changes in ({"session_id": "has/slash"}, {"client_id": "client\n"},
                        {"session_id": "x" * 81}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post(valid_heartbeat(**changes)).status_code, 422)
        payload = valid_heartbeat()
        payload["components"]["bad\n"] = payload["components"].pop("localguard_input_signature")
        self.assertEqual(self.post(payload).status_code, 422)

    def test_invalid_component_and_transport_values_are_422(self):
        for area, field, value in (("component", "required", 1), ("component", "pid", False),
                                   ("component", "details", []), ("component", "status", "bad"),
                                   ("transport", "configured", 1), ("transport", "consecutive_failures", -1)):
            payload = copy.deepcopy(valid_heartbeat())
            target = payload["components"]["localguard_input_signature"] if area == "component" else payload["transport"]
            target[field] = value
            with self.subTest(area=area, field=field):
                self.assertEqual(self.post(payload).status_code, 422)

    def test_sent_at_utc_requires_valid_timezone_aware_date_time(self):
        for timestamp in ("not a date", "2026-09-30T00:00:00", "2026-02-30T00:00:00Z"):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(self.post(valid_heartbeat(sent_at_utc=timestamp)).status_code, 422)

    def test_deep_json_and_surrogate_text_are_422(self):
        payload = valid_heartbeat()
        nested = {}
        payload["components"]["localguard_input_signature"]["details"] = nested
        for _ in range(15):
            child = {}
            nested["child"] = child
            nested = child
        self.assertEqual(self.post(payload).status_code, 422)
        body = json.dumps(valid_heartbeat(player_id="\ud800")).encode()
        response = self.client.post("/api/heartbeat", content=body, headers={"Authorization": "Bearer heartbeat-token", "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)

    def test_storage_failure_returns_503_without_acknowledging(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TABLE heartbeats")
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("accepted", response.json())

    def test_failed_insert_rolls_back_and_same_sequence_can_be_retried(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON heartbeats BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.count(), 0)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TRIGGER fail_insert")
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.count(), 1)


if __name__ == "__main__":
    unittest.main()
