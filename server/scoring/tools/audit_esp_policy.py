"""PR ESP의 실제 순수 탐지/변환 경로와 A 정책 호환성을 검사한다.

게임·Windows 센서·HTTP 서버에는 접근하지 않는다. 합성 SensorEvent와
메모리 SQLite만 사용하며 탐지기/원본 로그를 변경하지 않는다.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import sys
import unittest


class EspProducerCompatibilityTests(unittest.TestCase):
    def raw(self, event_type="process_access", payload=None, event_id="synthetic-audit-1"):
        return self.SensorEvent(
            session_id="esp_policy_fixture", sensor_id="synthetic_sensor",
            event_type=event_type, subject_id="synthetic-game-process:500",
            timestamp_ms=101_000, event_id=event_id,
            payload=payload if payload is not None else {"source_pid": 900, "target_pid": 500, "access_mask": 0x10},
        )

    def convert(self, raw):
        detector = self.Detector()
        adapter = self.Adapter(session_started_at=100.0, player_id="fixture_player")
        return adapter.convert(raw, detector.detect(raw))

    def analyse(self, converted):
        event = converted.to_dict()
        original = deepcopy(event)
        result = self.registry.evaluate(event)
        self.assertEqual(event, original)
        self.assertEqual(result.signal.raw_score, converted.raw_score)
        self.assertEqual(result.annotations.overlap_tags, ())
        return result

    def test_actual_process_adapter_emits_1_2_3_and_elapsed_time(self):
        for access, score, category in ((0x40, 1, "handle_duplicate"), (0x10, 2, "memory_read"), (0x30, 3, "process_tamper")):
            converted = self.convert(self.raw(payload={"source_pid": 900, "target_pid": 500, "access_mask": access}))
            self.assertEqual(converted.raw_score, score)
            self.assertEqual(converted.timestamp_ms, 1000)
            self.assertEqual(converted.evidence["categories"], [category])
            self.assertEqual(self.analyse(converted).annotations.entity_key, "external_process:900")

    def test_actual_window_adapter_is_corroborating_observation(self):
        converted = self.convert(self.raw("window_overlap", {
            "window_pid": 800, "game_pid": 500, "hwnd": 4660,
            "overlap_ratio": 0.8, "style_labels": ["LAYERED", "TRANSPARENT"],
        }))
        self.assertEqual(converted.raw_score, 1)
        self.assertEqual(self.analyse(converted).annotations.entity_key, "overlay_window:800:4660")

    def test_actual_module_adapters_preserve_mapping_and_trust_distinctions(self):
        for event_type, payload, score in (
            ("module_added", {"target_pid": 500, "module_path": "C:/Game/extra.dll"}, 1),
            ("module_changed", {"target_pid": 500, "after": {"path": "C:/Game/extra.dll"}}, 1),
            ("module_trust", {"target_pid": 500, "module_path": "C:/Game/extra.dll", "signature_status": "unsigned"}, 1),
            ("module_trust", {"target_pid": 500, "module_path": "C:/Game/extra.dll", "known_bad_hash": True}, 3),
        ):
            converted = self.convert(self.raw(event_type, payload))
            self.assertEqual(converted.raw_score, score)
            result = self.analyse(converted)
            self.assertTrue(result.annotations.entity_key.startswith("game_module:500:"))

    def test_actual_normal_or_unavailable_observation_does_not_emit_zero(self):
        cases = (
            self.raw(payload={"source_pid": 900, "target_pid": 500, "access_mask": 0x10, "allowlisted": True}),
            self.raw(payload={"source_pid": 500, "target_pid": 500, "access_mask": 0x10}),
            self.raw("module_trust", {"module_path": "C:/Game/extra.dll", "signature_status": "unavailable"}),
            self.raw("module_added", {"module_path": "C:/Game/extra.dll", "baseline_created": True}),
        )
        for raw in cases:
            self.assertIsNone(self.convert(raw))

    def test_initial_unsigned_not_emitted_but_known_bad_baseline_emitted(self):
        fields = {"target_pid": 500, "module_path": "C:/Game/extra.dll", "baseline_created": True, "signature_status": "unsigned"}
        self.assertIsNone(self.convert(self.raw("module_trust", fields)))
        fields["hash_blacklisted"] = True
        converted = self.convert(self.raw("module_trust", fields))
        self.assertEqual(converted.raw_score, 3)
        self.assertTrue(any("초기 기준선" in note for note in self.analyse(converted).annotations.notes))

    def test_actual_adapter_can_sum_extended_evidence_without_policy_clipping(self):
        raw = self.raw()
        detected = self.Detector().detect(raw)
        extra = self.EvidenceEvent(
            category="process_tamper", timestamp=101.0, source="synthetic_extension",
            reason="synthetic extra observation", details={"sensor_event_id": raw.event_id},
            event_id="synthetic-extra", session_id=raw.session_id,
        )
        converted = self.Adapter(session_started_at=100.0, player_id="fixture_player").convert(raw, (*detected, extra))
        self.assertEqual(converted.raw_score, 5)
        result = self.analyse(converted)
        self.assertIsNone(result.annotations.entity_key)
        self.assertEqual(result.signal.raw_score, 5)

    def test_actual_local_pipeline_dedups_same_raw_id_not_same_process_forever(self):
        class MemoryTelemetry:
            def __init__(self):
                self.events = {}

            def append_raw_event(self, _event):
                pass

            def append_team_event(self, event, *, idempotency_key=None):
                self.events[idempotency_key] = event

        store = self.Store(":memory:")
        telemetry = MemoryTelemetry()
        try:
            pipeline = self.Pipeline(
                detectors=(self.Detector(),), store=store, scoring=self.SuspicionEngine(),
                team_adapter=self.Adapter(session_started_at=100.0, player_id="fixture_player"),
                telemetry=telemetry,
            )
            raw = self.raw()
            first = pipeline.process_batch(self.SensorBatch("synthetic_sensor", "online", (raw,)))
            repeated = pipeline.process_batch(self.SensorBatch("synthetic_sensor", "online", (raw,)))
            changed_id = pipeline.process_batch(self.SensorBatch("synthetic_sensor", "online", (self.raw(event_id="synthetic-audit-2"),)))
            self.assertEqual(first.accepted_evidence_count, 1)
            self.assertEqual(repeated.accepted_evidence_count, 0)
            self.assertEqual(repeated.team_events, ())
            self.assertEqual(changed_id.accepted_evidence_count, 1)
            self.assertEqual(len(telemetry.events), 2)
            for converted in telemetry.events.values():
                self.analyse(converted)
        finally:
            store.close()

    def test_transport_id_is_stable_canonical_uuid_not_sensor_id(self):
        from shared.schema import validate_event_id
        first = self.Sink._event_id("synthetic-outbox-a")
        second = self.Sink._event_id("synthetic-outbox-a")
        different = self.Sink._event_id("synthetic-outbox-b")
        self.assertEqual(validate_event_id(first), first)
        self.assertEqual(first, second)
        self.assertNotEqual(first, different)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path, help="ESP PR을 검토한 저장소 체크아웃")
    args = parser.parse_args(argv)
    reference = args.repo_root.resolve()
    esp_root = reference / "client/detectors/esp"
    if not (esp_root / "anti_esp/team_format.py").is_file():
        parser.error("reference checkout does not contain the ESP team adapter")
    own_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(own_root))
    sys.path.insert(0, str(esp_root))
    from server.scoring.policies.contract import PolicyRegistry
    from server.scoring.policies.esp import evaluate
    from anti_esp.core.events import SensorBatch, SensorEvent
    from anti_esp.detectors.esp_detector import EspEventDetector
    from anti_esp.models import EvidenceEvent
    from anti_esp.pipeline import EspDetectionPipeline
    from anti_esp.scoring import SuspicionEngine
    from anti_esp.shared_transport import SharedEventSink
    from anti_esp.store import SQLiteEvidenceStore
    from anti_esp.team_format import TeamEventAdapter

    cls = EspProducerCompatibilityTests
    cls.SensorEvent, cls.SensorBatch, cls.Detector = SensorEvent, SensorBatch, EspEventDetector
    cls.Adapter, cls.EvidenceEvent, cls.Store = TeamEventAdapter, EvidenceEvent, SQLiteEvidenceStore
    cls.Pipeline, cls.SuspicionEngine, cls.Sink = EspDetectionPipeline, SuspicionEngine, SharedEventSink
    cls.registry = PolicyRegistry()
    cls.registry.register("esp", evaluate)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
