"""Mocked API readback tests. No server, game, detector or existing data reads."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from server.dashboard_backend import calibration_export as export


SESSION = "esp_calibration_test"
PLAYER = "calibration_player"
PRIVATE = "PRIVATE_ERROR_BODY_NEVER_RETAIN"


def payload(timestamp=1200):
    return {"session_id": SESSION, "player_id": PLAYER, "module": "esp", "timestamp_ms": timestamp,
            "raw_score": 3, "evidence": {"source_pid": 111, "source_image": "python.exe",
                "source_image_path_sha256": "a" * 64, "sensor_event_id": "sensor-sha256:" + "b" * 64,
                "event_type": "process_access", "categories": ["process_tamper"]},
            "reasons": ["Process VM write access observed"]}


def event(timestamp=1200, sequence=1):
    return {**payload(timestamp), "id": str(uuid4()), "sequence": sequence,
            "received_at_utc": "2026-10-07T01:02:03+00:00", "event_kind": "detection",
            "time_basis": "unknown", "observed_at_utc": None, "evidence_image": None, "log_excerpt": None}


class FakeHTTP:
    def __init__(self, rows=None):
        self.url = "http://127.0.0.1:8002"
        self.run_id = "abcdef123456"
        self.rows = [event()] if rows is None else rows
        self.calls = []
        self.detail_mutate = None
        self.page_mutate = None
        self.snapshot_mutate = None
        self.verdict_error = None
        self.verdict = {"session_id": SESSION, "player_id": PLAYER, "status": "INCONCLUSIVE",
            "assessment_complete": False, "evidence_unit_count": 0, "active_modules": [],
            "reason_codes": ["ASSESSMENT_INCOMPLETE"], "unresolved_modules": ["esp"]}

    def expected(self):
        return {row["id"]: {field: deepcopy(row[field]) for field in export.EVENT_FIELDS} for row in self.rows}

    def request(self, path):
        self.calls.append(path)
        parsed = urlsplit(path)
        if parsed.path == "/api/dashboard/events":
            query = parse_qs(parsed.query)
            assert query["session_id"] == [SESSION] and query["player_id"] == [PLAYER] and query["module"] == ["esp"]
            start = 1 if query.get("cursor") == ["page2"] else 0
            more = len(self.rows) > 1 and start == 0
            page = {"items": deepcopy(self.rows[start:start+1]), "has_more": more,
                    "next_cursor": "page2" if more else None,
                    "through_sequence": self.rows[-1]["sequence"] if self.rows else 0,
                    "index": {"catching_up": False}}
            if self.page_mutate:
                self.page_mutate(page, start)
            return page
        if parsed.path.startswith("/api/dashboard/events/"):
            detail = deepcopy(next(row for row in self.rows if row["id"] == parsed.path.rsplit("/", 1)[1]))
            if self.detail_mutate:
                self.detail_mutate(detail)
            return detail
        if parsed.path.endswith("/snapshot"):
            modules = []
            if self.rows:
                latest = self.rows[-1]
                modules = [{**{field: deepcopy(latest[field]) for field in export.EVENT_FIELDS},
                            "event_id": latest["id"], "sequence": latest["sequence"]}]
            snapshot = {"session_id": SESSION, "player_id": PLAYER, "modules": modules,
                "status": "INCONCLUSIVE" if self.rows else "UNKNOWN", "score": None, "confidence": None,
                "assessment_available": bool(self.rows), "final_verdict": deepcopy(self.verdict) if self.rows else None,
                "reason_codes": ["ASSESSMENT_INCOMPLETE"] if self.rows else [],
                "data_state": "available" if self.rows else "missing", "policy": {"modules": [], "module_evidence": []}}
            if self.snapshot_mutate:
                self.snapshot_mutate(snapshot)
            return snapshot
        if parsed.path.startswith("/api/dashboard/verdict/"):
            if self.verdict_error:
                raise self.verdict_error
            if not self.rows:
                raise urllib.error.HTTPError(self.url + path, 404, PRIVATE, {}, None)
            return deepcopy(self.verdict)
        raise AssertionError(PRIVATE)


class CalibrationExportTests(unittest.TestCase):
    def collect(self, api, **kwargs):
        return export.collect_evidence(api, SESSION, PLAYER, expected_events=api.expected(), **kwargs)

    def test_positive_keeps_original_shared_receipt_identity_and_pending_verdict(self):
        api = FakeHTTP()
        result = self.collect(api)
        self.assertEqual(result["result"], "SERVER_READBACK_VERIFIED")
        self.assertEqual(result["events"], api.rows)
        self.assertEqual(result["final_verdict"], api.verdict)
        self.assertEqual(result["snapshot"]["status"], "INCONCLUSIVE")
        self.assertIsNone(result["snapshot"]["score"])
        self.assertEqual(result["synthetic_events_created"], 0)
        self.assertEqual(result["capture_provenance"], "NOT_ASSESSED")
        self.assertEqual(result["shared_ack_provenance"], "NOT_ASSESSED")
        self.assertFalse(result["snapshot_watermark_atomic"])
        self.assertTrue(all(path.startswith("/api/dashboard/") for path in api.calls))

    def test_empty_normal_preserves_unknown_and_404_without_inventing_zero_event(self):
        api = FakeHTTP([])
        result = self.collect(api)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["event_count"], 0)
        self.assertIsNone(result["final_verdict"])
        self.assertEqual(result["snapshot"]["status"], "UNKNOWN")
        self.assertIsNone(result["snapshot"]["confidence"])

    def test_nullable_receipt_is_retained_without_observation_substitution(self):
        api = FakeHTTP()
        api.rows[0]["received_at_utc"] = None
        result = self.collect(api)
        self.assertIsNone(result["events"][0]["received_at_utc"])
        self.assertIsNone(result["events"][0]["observed_at_utc"])
        self.assertFalse(result["checks"]["received_at_metadata_complete"])

    def test_launcher_observation_matches_exact_declared_clock_and_keeps_milliseconds(self):
        api = FakeHTTP([event(timestamp=246771)])
        row = api.rows[0]
        row["evidence"].update(timestamp_basis="launcher_session_start", session_start_unix_ms=1791367718186)
        row.update(time_basis="session_relative", observed_at_utc="2026-10-07T10:12:44.957Z")
        result = self.collect(api)
        self.assertEqual(result["events"][0], row)
        self.assertTrue(result["checks"]["declared_clock_metadata_equal"])
        self.assertEqual(result["events"][0]["timestamp_ms"], 246771)
        self.assertNotEqual(result["events"][0]["received_at_utc"], row["observed_at_utc"])

    def test_observed_clock_never_inferred_for_unknown_epoch_or_missing_base(self):
        for evidence, basis in (
            ({}, "unknown"),
            ({"time_basis": "session_relative"}, "session_relative"),
            ({"time_basis": "unix_epoch_ms"}, "unix_epoch_ms"),
            ({"time_basis": "unix_epoch_ms", "timestamp_basis": "launcher_session_start"}, "unknown"),
            ({"time_basis": "invalid", "session_start_unix_ms": 1791367718186}, "unknown"),
        ):
            for supplied in (None, "2026-10-07T10:12:44.957+00:00"):
                api = FakeHTTP()
                api.rows[0]["evidence"].update(evidence)
                api.rows[0].update(time_basis=basis, observed_at_utc=supplied)
                with self.subTest(evidence=evidence, supplied=supplied):
                    if supplied is None:
                        self.assertIsNone(self.collect(api)["events"][0]["observed_at_utc"])
                    else:
                        with self.assertRaises(export.CalibrationExportError):
                            self.collect(api)

    def test_invalid_base_keeps_elapsed_but_cannot_supply_observation(self):
        for base in (None, True, "1791367718186", 1791367718186.0, 0, -1,
                     9007199254740992, 253402300800000):
            api = FakeHTTP()
            api.rows[0]["evidence"].update(time_basis="session_relative", session_start_unix_ms=base)
            api.rows[0].update(time_basis="session_relative", observed_at_utc=None)
            with self.subTest(base=base):
                self.assertEqual(self.collect(api)["events"][0]["time_basis"], "session_relative")

    def test_clock_basis_mismatch_observation_mismatch_and_invalid_utc_rejected(self):
        for mutate in (
            lambda row: row.update(time_basis="session_relative"),
            lambda row: row.update(observed_at_utc="2026-10-07T10:12:44.957"),
            lambda row: row.update(observed_at_utc="2026-10-07T19:12:44.957+09:00"),
            lambda row: row.update(observed_at_utc="2026-02-30T10:12:44.957Z"),
            lambda row: row.update(received_at_utc="20261007T010203Z"),
            lambda row: row.update(observed_at_utc=True),
        ):
            api = FakeHTTP()
            mutate(api.rows[0])
            with self.subTest(mutate=mutate), self.assertRaises(export.CalibrationExportError):
                self.collect(api)
        for observed in (None, "2026-10-07T10:12:44.958Z"):
            api = FakeHTTP([event(timestamp=246771)])
            api.rows[0]["evidence"].update(timestamp_basis="launcher_session_start", session_start_unix_ms=1791367718186)
            api.rows[0].update(time_basis="session_relative", observed_at_utc=observed)
            with self.subTest(observed=observed), self.assertRaises(export.CalibrationExportError):
                self.collect(api)

    def test_all_pages_share_frozen_watermark_and_detail_checked_for_each(self):
        api = FakeHTTP([event(sequence=1), event(timestamp=2400, sequence=3)])
        result = self.collect(api)
        self.assertEqual(result["through_sequence"], 3)
        self.assertEqual(result["event_count"], 2)
        self.assertEqual(result["request_count"], 6)
        self.assertIn("cursor=page2", api.calls[2])

    def test_missing_extra_or_changed_ledger_fails(self):
        api = FakeHTTP()
        for expected in ({}, {str(uuid4()): payload()}, {api.rows[0]["id"]: payload(999)}):
            with self.subTest(expected=expected), self.assertRaises(export.CalibrationExportError):
                export.collect_evidence(api, SESSION, PLAYER, expected_events=expected)

    def test_metadata_receipt_scope_uuid_and_shared_payload_fail_closed(self):
        mutations = (
            lambda row: row.update(id="not-a-uuid"),
            lambda row: row.update(sequence=True),
            lambda row: row.update(received_at_utc="2026-10-07T01:02:03"),
            lambda row: row.update(received_at_utc="2026-10-07T01:02:03+09:00"),
            lambda row: row.update(player_id="other_player"),
            lambda row: row.update(module="localguard_yara"),
            lambda row: row.update(raw_score=True),
            lambda row: row.update(evidence_image="file:///private.png"),
            lambda row: row.update(unexpected="field"),
        )
        for mutate in mutations:
            api = FakeHTTP()
            mutate(api.rows[0])
            with self.subTest(mutate=mutate), self.assertRaises(export.CalibrationExportError):
                self.collect(api)

    def test_detail_identity_payload_or_metadata_mismatch_rejected(self):
        for mutate in (lambda row: row.update(raw_score=0), lambda row: row.update(sequence=2),
                       lambda row: row.update(id=str(uuid4())),
                       lambda row: row.update(received_at_utc="2026-10-07T01:02:04+00:00")):
            api = FakeHTTP()
            api.detail_mutate = mutate
            with self.subTest(mutate=mutate), self.assertRaises(export.CalibrationExportError):
                self.collect(api)

    def test_watermark_cursor_partial_index_and_page_bounds_rejected(self):
        for mutate in (
            lambda page, start: page.update(through_sequence=4 if start else 3),
            lambda page, start: page.update(next_cursor="page2", has_more=True),
            lambda page, start: page.update(index={"catching_up": True}),
            lambda page, start: page.update(has_more=True, next_cursor=""),
        ):
            api = FakeHTTP([event(sequence=1), event(sequence=3)])
            api.page_mutate = mutate
            with self.subTest(mutate=mutate), self.assertRaises(export.CalibrationExportError):
                self.collect(api)
        with self.assertRaises(export.CalibrationExportError):
            self.collect(FakeHTTP([event(sequence=1), event(sequence=3)]), max_pages=1)

    def test_scoring_mismatch_and_missing_positive_verdict_fail(self):
        for mutate in (lambda value: value.update(status="SUSPICIOUS"),
                       lambda value: value.update(score=0), lambda value: value.update(modules=[]),
                       lambda value: value["modules"][0].update(sequence=99)):
            api = FakeHTTP()
            api.snapshot_mutate = mutate
            with self.subTest(mutate=mutate), self.assertRaises(export.CalibrationExportError):
                self.collect(api)
        api = FakeHTTP()
        api.verdict_error = urllib.error.HTTPError(api.url, 404, PRIVATE, {}, None)
        with self.assertRaises(export.CalibrationExportError):
            self.collect(api)

    def test_failures_are_type_only_and_do_not_fallback_or_echo_body(self):
        api = FakeHTTP([])
        for code in (401, 403, 500, 503):
            api.verdict_error = urllib.error.HTTPError(api.url, code, PRIVATE, {}, None)
            with self.subTest(code=code), self.assertRaises(export.CalibrationExportError) as failure:
                self.collect(api)
            self.assertNotIn(PRIVATE, str(failure.exception))
            self.assertIn("HTTPError", str(failure.exception))

    def test_private_keys_tokens_and_paths_in_evidence_reasons_policy_rejected(self):
        for private_value in ({"username": "private-name"}, {"computer_name": "private-host"},
                              {"window_title": "private-window"}, {"image_path": "C:\\Users\\private\\esp.exe"},
                              {"module_path": "/opt/private/esp.so"}, {"source_path": "private/folder/esp.exe"},
                              {"nested": {"GZZ_TELEMETRY_TOKEN": "private-token"}},
                              {"nested": {"api_token": "private-token"}}):
            api = FakeHTTP()
            api.rows[0]["evidence"].update(private_value)
            with self.subTest(private_value=private_value), self.assertRaises(export.CalibrationExportError):
                self.collect(api)
        api = FakeHTTP()
        api.rows[0]["reasons"] = ["at C:\\Users\\private\\file.dll"]
        with self.assertRaises(export.CalibrationExportError):
            self.collect(api)
        api = FakeHTTP()
        api.snapshot_mutate = lambda value: value["policy"].update(secret="private-token")
        with self.assertRaises(export.CalibrationExportError):
            self.collect(api)

    def test_live_http_unsafe_scope_and_budget_refused_before_requests(self):
        api = FakeHTTP()
        api.url = "https://production.invalid"
        with self.assertRaises(export.CalibrationExportError):
            self.collect(api)
        self.assertEqual(api.calls, [])
        api = FakeHTTP()
        for options in ({"max_pages": True}, {"max_details": 2001}, {"time_budget_seconds": 0},
                        {"time_budget_seconds": float("inf")}, {"time_budget_seconds": float("nan")}):
            with self.subTest(options=options), self.assertRaises(export.CalibrationExportError):
                self.collect(api, **options)
        with self.assertRaises(export.CalibrationExportError):
            export.collect_evidence(api, "../private", PLAYER, expected_events={})
        self.assertEqual(api.calls, [])
        with patch.object(export.time, "monotonic", side_effect=[0, 100]), self.assertRaises(export.CalibrationExportError):
            self.collect(api)
        self.assertEqual(api.calls, [])

    def test_create_http_uses_exact_setup_contract_without_inherited_env(self):
        setup = {"schema_version": export.SETUP_SCHEMA, "port": 8002, "run_id": "abcdef123456",
                 "endpoint": "http://127.0.0.1:8002", "result": "READY", "empty_start_verified": True,
                 "tokens_are_disposable_test_literals": True, "seeded_events": 0,
                 "test_tokens": {name: f"synthetic-browser-{kind}-abcdef123456" for name, kind in (
                     ("GZZ_TELEMETRY_TOKEN", "receiver"), ("MECCHA_HEARTBEAT_TOKEN", "heartbeat"),
                     ("GZZ_DASHBOARD_TOKEN", "dashboard"))}}
        with tempfile.TemporaryDirectory(prefix="calibration-export-setup-test-") as folder:
            path = Path(folder) / "setup.json"
            path.write_text(json.dumps(setup), encoding="utf-8")
            http = export.create_http(path)
            self.assertEqual(http.url, "http://127.0.0.1:8002")
            self.assertEqual(http.run_id, "abcdef123456")
            for mutate in (lambda value: value.update(schema_version="synthetic-browser-fixture"),
                           lambda value: value.update(endpoint="https://production.invalid"),
                           lambda value: value["test_tokens"].update(GZZ_DASHBOARD_TOKEN="live-secret")):
                document = deepcopy(setup)
                mutate(document)
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(mutate=mutate), self.assertRaises(export.CalibrationExportError):
                    export.create_http(path)
        with patch.object(export, "read_setup", side_effect=ValueError(PRIVATE)), self.assertRaises(export.CalibrationExportError) as failure:
            export.create_http("owned-test/setup.json")
        self.assertNotIn(PRIVATE, str(failure.exception))

    def test_export_is_exclusive_private_free_and_never_overwrites_existing(self):
        document = self.collect(FakeHTTP())
        with tempfile.TemporaryDirectory(prefix="calibration-export-test-") as directory:
            root = Path(directory)
            target = root / "new-evidence"
            path = export.export_evidence(document, target)
            self.assertEqual(path.name, "calibration-evidence.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), document)
            self.assertEqual([item.name for item in target.iterdir()], ["calibration-evidence.json"])
            with self.assertRaises(export.CalibrationExportError):
                export.export_evidence(document, target)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), document)
            private = deepcopy(document)
            private["password"] = "private-password"
            blocked = root / "must-not-exist"
            with self.assertRaises(export.CalibrationExportError):
                export.export_evidence(private, blocked)
            self.assertFalse(blocked.exists())


if __name__ == "__main__":
    unittest.main()
