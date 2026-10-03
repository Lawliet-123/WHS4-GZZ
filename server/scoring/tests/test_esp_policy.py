"""ESP 중앙 정책은 관측 해석이며 ESP 사용 확정·최종 점수 계산이 아님."""

from copy import deepcopy
import unittest

from server.scoring.policies.contract import PolicyRegistry
from server.scoring.policies.esp import evaluate
from server.scoring.policy import inspect_event
from shared.errors import ValidationError


def sample(*, event_type="process_access", categories=None, score=2, evidence=None, reasons=None, module="esp"):
    fields = {
        "sensor_event_id": "synthetic-sensor-event", "event_type": event_type,
        "categories": list(categories) if categories is not None else ["memory_read"],
        "source_pid": 900, "target_pid": 500,
    }
    fields.update(deepcopy(evidence) if evidence is not None else {})
    return {
        "session_id": "synthetic_esp", "player_id": "test_player", "module": module,
        "timestamp_ms": 1000, "evidence": fields,
        "reasons": list(reasons or []), "raw_score": score,
    }


class EspPolicyTests(unittest.TestCase):
    def setUp(self):
        self.registry = PolicyRegistry()
        self.registry.register("esp", evaluate)

    def analyse(self, event):
        original = deepcopy(event)
        result = self.registry.evaluate(event)
        self.assertEqual(event, original)
        self.assertEqual(result.signal, inspect_event(original))
        self.assertEqual(result.annotations.overlap_tags, ())
        if result.annotations.entity_key is not None:
            self.assertLessEqual(len(result.annotations.entity_key), 128)
        return result

    def assert_note(self, result, fragment):
        self.assertTrue(any(fragment in note for note in result.annotations.notes), fragment)

    def test_process_read_score_not_local_risk_or_game_process_entity(self):
        result = self.analyse(sample())
        self.assertEqual(result.signal.raw_score, 2)
        self.assertEqual(result.annotations.entity_key, "external_process:900")
        self.assert_note(result, "0~100 의심도")
        self.assert_note(result, "팀 탐지기 자기 탐지")

    def test_all_process_categories_explained_without_new_score(self):
        for category, score, fragment in (("process_tamper", 3, "실제 메모리 변조"), ("memory_read", 2, "읽기 권한"), ("handle_duplicate", 1, "실제 핸들 전송")):
            result = self.analyse(sample(categories=[category], score=score))
            self.assertEqual(result.signal.raw_score, score)
            self.assert_note(result, fragment)

    def test_numeric_pid_aliases_follow_sensor_contract(self):
        for fields in ({"source_pid": "0x384"}, {"source_pid": None, "source_process_id": "900"}):
            self.assertEqual(self.analyse(sample(evidence=fields)).annotations.entity_key, "external_process:900")

    def test_bad_pid_or_same_source_target_not_external_entity(self):
        for pid in (None, True, 0, -1, 1.5, "reader PID 900", 2**32, 500):
            result = self.analyse(sample(evidence={"source_pid": pid, "source_image": "C:/Tools/reader.exe"}))
            self.assertIsNone(result.annotations.entity_key)
            self.assert_note(result, "유효한 외부 source_pid가 없다")

    def test_reviewed_profile_remains_not_calibrated_by_handler(self):
        result = self.analyse(sample())
        self.assertEqual(result.signal.state, "POLICY_NOT_CALIBRATED")
        self.assertIsNone(result.signal.raw_fraction_pct)
        self.assert_note(result, "최종 위험도 보정")

    def test_reviewed_profile_preserves_explicit_measurement_failure(self):
        for fields in ({"status": "ERROR"}, {"status": "OFFLINE"}, {"measurement_valid": False}):
            result = self.analyse(sample(evidence=fields))
            self.assertEqual(result.signal.state, "MEASUREMENT_UNAVAILABLE")
            self.assertIsNone(result.annotations.entity_key)
            self.assert_note(result, "정상 근거로 해석하지 않고")
            self.assertEqual(result.signal.raw_score, 2)

    def test_partial_observation_remains_partial(self):
        result = self.analyse(sample(evidence={"status": "WARNING", "coverage_complete": False}))
        self.assert_note(result, "부분 검사")

    def test_zero_detection_not_healthy_snapshot(self):
        result = self.analyse(sample(score=0))
        self.assertIsNone(result.annotations.entity_key)
        self.assert_note(result, "양수 근거 전송 계약과 다르다")
        self.assert_note(result, "무전송을 정상 0점")

    def test_sensor_id_not_used_as_shared_transport_id_or_entity(self):
        result = self.analyse(sample(evidence={"sensor_event_id": "not-a-uuid-or-shared-id"}))
        self.assertEqual(result.annotations.entity_key, "external_process:900")
        self.assert_note(result, "다른 식별자")
        result = self.analyse(sample(evidence={"sensor_event_id": None}))
        self.assert_note(result, "개별 관측 ID를 임의 복원하지 않는다")

    def test_unknown_event_type_not_inferred_from_reason(self):
        for event_type in (None, "new_event", ["process_access"]):
            result = self.analyse(sample(event_type=event_type, reasons=["external process requested permission to read game memory"]))
            self.assertIsNone(result.annotations.entity_key)
            self.assert_note(result, "센서 종류를 추정하지 않는다")

    def test_bad_or_unknown_categories_not_dynamic_tags(self):
        for categories in (None, "memory_read", [], ["new_category"], [True], ["memory_read", "new_category"]):
            result = self.analyse(sample(evidence={"categories": categories}))
            self.assertIsNone(result.annotations.entity_key)
            self.assert_note(result, "자유 문자열 태그를 만들지 않는다")

    def test_category_type_mismatch_and_mixed_categories_not_single_entity(self):
        for event_type, categories in (("window_overlap", ["memory_read"]), ("process_access", ["memory_read", "process_tamper"])):
            result = self.analyse(sample(event_type=event_type, categories=categories))
            self.assertIsNone(result.annotations.entity_key)
            self.assert_note(result, "현재 단일 관측 탐지기의")

    def test_duplicate_categories_not_counted_twice(self):
        result = self.analyse(sample(categories=["memory_read", "memory_read"]))
        self.assertEqual(result.signal.raw_score, 2)
        self.assert_note(result, "목록 길이를 새로운 근거 수로 곱하지 않는다")

    def test_score_above_current_single_observation_limit_not_clipped(self):
        result = self.analyse(sample(score=100))
        self.assertEqual(result.signal.raw_score, 100)
        self.assert_note(result, "상한 3점을 벗어났다")
        self.assert_note(result, "현재 알려진 관측의 점수 생성식과 다르다")

    def test_window_scope_not_dx_hook_or_confirmed_esp(self):
        result = self.analyse(sample(event_type="window_overlap", categories=["overlay"], score=1, evidence={"window_pid": 800, "game_pid": 500, "hwnd": "0x1234"}))
        self.assertEqual(result.annotations.entity_key, "overlay_window:800:4660")
        self.assert_note(result, "정상 오버레이도 가능")
        self.assert_note(result, "DX 함수 후킹")

    def test_window_aliases_supported(self):
        result = self.analyse(sample(event_type="window_overlap", categories=["overlay"], score=1, evidence={"candidate_pid": "800", "hwnd": 4660}))
        self.assertEqual(result.annotations.entity_key, "overlay_window:800:4660")

    def test_bad_window_hwnd_or_pid_not_title_identity(self):
        for pid, hwnd in ((None, 4660), (4, 4660), (500, 4660), (800, None), (800, True), (800, 0), (800, -1), (800, 2**64), (800, "hwnd=4660")):
            result = self.analyse(sample(event_type="window_overlap", categories=["overlay"], score=1, evidence={"window_pid": pid, "game_pid": 500, "hwnd": hwnd, "window_title": "overlay"}))
            self.assertIsNone(result.annotations.entity_key)

    def test_module_path_canonicalization_and_scope_not_file_hash(self):
        paths = ["C:/Game/extra.dll", "c:\\GAME\\unused\\..\\EXTRA.DLL", "\\\\?\\C:\\Game\\extra.dll"]
        results = [self.analyse(sample(event_type="module_added", categories=["behavioral_signal"], score=1, evidence={"module_path": path})) for path in paths]
        self.assertEqual(len({r.annotations.entity_key for r in results}), 1)
        self.assertTrue(results[0].annotations.entity_key.startswith("game_module:500:"))
        self.assert_note(results[0], "파일 바이트 해시/개별 로드 사건 ID가 아니다")

    def test_module_changed_uses_after_container_not_before_or_hash(self):
        fields = {"module": {"path": "C:/Wrong/extra.dll"}, "after": {"path": "C:/Game/extra.dll"}, "sha256": "a" * 64}
        changed = self.analyse(sample(event_type="module_changed", categories=["behavioral_signal"], score=1, evidence=fields))
        added = self.analyse(sample(event_type="module_added", categories=["behavioral_signal"], score=1, evidence={"module_path": "C:/Game/extra.dll"}))
        self.assertEqual(changed.annotations.entity_key, added.annotations.entity_key)
        self.assert_note(changed, "파일 바이트 패치")

    def test_same_module_scope_across_observation_types_not_same_event(self):
        keys = []
        for event_type in ("module_added", "module_changed", "module_trust"):
            result = self.analyse(sample(event_type=event_type, categories=["behavioral_signal"], score=1, evidence={"module_path": "C:/Game/extra.dll"}))
            keys.append(result.annotations.entity_key)
        self.assertEqual(len(set(keys)), 1)

    def test_missing_pid_or_bad_module_path_not_named_dll_entity(self):
        for path in (None, "extra.dll", "C:extra.dll", "\\extra.dll", "C:/bad\x00.dll", True):
            result = self.analyse(sample(event_type="module_added", categories=["behavioral_signal"], score=1, evidence={"module_path": path, "module_name": "extra.dll", "sha256": "a" * 64}))
            self.assertIsNone(result.annotations.entity_key)
        result = self.analyse(sample(event_type="module_added", categories=["behavioral_signal"], score=1, evidence={"target_pid": None, "module_path": "C:/Game/extra.dll"}))
        self.assertIsNone(result.annotations.entity_key)

    def test_known_bad_hash_and_unsigned_are_different_evidence_not_game_behavior(self):
        for score, reason in ((3, "module hash matched the configured known-bad list"), (1, "module has no verifiable digital signature")):
            result = self.analyse(sample(event_type="module_trust", categories=["behavioral_signal"], score=score, evidence={"module_path": "C:/Game/extra.dll"}, reasons=[reason]))
            self.assertEqual(result.signal.raw_score, score)
            self.assert_note(result, "현재 ESP 행동의 증명은 아니다")

    def test_initial_module_trust_not_new_runtime_injection(self):
        result = self.analyse(sample(event_type="module_trust", categories=["behavioral_signal"], score=3, evidence={"module_path": "C:/Game/extra.dll", "baseline_created": True}, reasons=["module hash matched the configured known-bad list"]))
        self.assert_note(result, "검사 시작 후 새로 주입됐다고 해석하지 않는다")

    def test_free_reason_not_new_blacklist_identity_or_tag(self):
        result = self.analyse(sample(event_type="module_trust", categories=["behavioral_signal"], score=3, evidence={"module_path": "C:/Game/extra.dll"}, reasons=["future arbitrary known-bad reason"]))
        self.assert_note(result, "생산자 버전을 확인")

    def test_repeated_analysis_not_history_or_dedup_side_effect(self):
        event = sample()
        self.assertEqual(self.analyse(event), self.analyse(event))
        self.assert_note(self.analyse(event), "이력을 최신 모듈 1건으로 복원할 수 없다")

    def test_wrong_module_baseline_or_extra_fields_rejected(self):
        event = sample(module="injection")
        with self.assertRaises(ValueError):
            evaluate(event, inspect_event(event))
        with self.assertRaises(ValueError):
            evaluate(sample(), inspect_event(event))
        event = sample()
        event["sensor_event_id"] = "wrong-root-location"
        with self.assertRaises(ValidationError):
            self.registry.evaluate(event)


if __name__ == "__main__":
    unittest.main()
