"""A 담당 LocalGuard 정책 해석 테스트. 탐지 알고리즘/실게임 정확도 검증은 아님."""

from copy import deepcopy
import unittest

from server.scoring.policies.contract import PolicyRegistry
from server.scoring.policies.localguard import SUPPORTED_MODULES, evaluate
from server.scoring.policy import inspect_event
from shared.errors import ValidationError


def sample(module="external_access", *, score=2, evidence=None, reasons=None):
    return {
        "session_id": "synthetic_localguard", "player_id": "test_player",
        "module": module, "timestamp_ms": 1000,
        "evidence": deepcopy(evidence) if evidence is not None else {},
        "reasons": list(reasons or []), "raw_score": score,
    }


def dll_evidence(**overrides):
    evidence = {
        "submodule": "module_integrity", "status": "SUSPICIOUS",
        "target_pid": 500, "module_path": "C:/Game/extra.dll",
        "change_type": "added", "signature_status": "unsigned",
    }
    evidence.update(overrides)
    return evidence


class LocalGuardPolicyTests(unittest.TestCase):
    def setUp(self):
        self.registry = PolicyRegistry()
        for module in SUPPORTED_MODULES:
            self.registry.register(module, evaluate)

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

    def test_handle_positive_scopes_source_not_game(self):
        result = self.analyse(sample(evidence={
            "submodule": "external_process", "source_pid": 900, "target_pid": 500,
            "status": "SUSPICIOUS", "access_rights": ["PROCESS_VM_WRITE"],
        }))
        self.assertEqual(result.annotations.entity_key, "external_process:900")
        self.assert_note(result, "PID 재사용")

    def test_handle_zero_does_not_represent_one_old_process(self):
        result = self.analyse(sample(score=0, evidence={
            "submodule": "external_process", "status": "NORMAL", "source_pid": 900,
        }))
        self.assertIsNone(result.annotations.entity_key)
        self.assert_note(result, "과거 모든 source_pid")

    def test_all_unavailable_states_preserve_score_without_entities(self):
        for evidence in ({"status": "ERROR"}, {"status": "OFFLINE"}, {"measurement_valid": False}):
            for submodule in ("external_process", "module_integrity"):
                with self.subTest(evidence=evidence, submodule=submodule):
                    fields = dll_evidence(submodule=submodule, source_pid=900)
                    fields.update(evidence)
                    result = self.analyse(sample(score=2, evidence=fields))
                    self.assertEqual(result.signal.state, "MEASUREMENT_UNAVAILABLE")
                    self.assertEqual(result.signal.raw_score, 2)
                    self.assertIsNone(result.annotations.entity_key)

    def test_invalid_source_pid_is_not_an_entity(self):
        for pid in (True, 0, -1, 1.5, "900", 2**32, None):
            with self.subTest(pid=pid):
                result = self.analyse(sample(evidence={"submodule": "external_process", "source_pid": pid}))
                self.assertIsNone(result.annotations.entity_key)

    def test_read_only_positive_is_flagged_not_scored_or_dropped(self):
        result = self.analyse(sample(score=2, evidence={
            "submodule": "external_process", "source_pid": 900,
            "access_rights": ["PROCESS_VM_READ"],
        }))
        self.assert_note(result, "VM_READ 단독 양수")
        self.assertEqual(result.signal.raw_score, 2)

    def test_legacy_source_pid_is_not_written_back_as_submodule(self):
        event = sample(evidence={"source_pid": 900})
        result = self.analyse(event)
        self.assertEqual(result.annotations.entity_key, "external_process:900")
        self.assertNotIn("submodule", event["evidence"])
        self.assert_note(result, "과거 source_pid")

    def test_unknown_submodule_never_falls_back_to_source_pid(self):
        for fields in ({"target_pid": 500}, {"submodule": "new_channel", "source_pid": 900}):
            result = self.analyse(sample(evidence=fields))
            self.assertIsNone(result.annotations.entity_key)
            self.assert_note(result, "어느 채널인지 임의로 선택하지 않는다")

    def test_dll_scope_is_not_handle_scope(self):
        result = self.analyse(sample(evidence=dll_evidence(source_pid=900)))
        self.assertTrue(result.annotations.entity_key.startswith("game_module:500:"))
        self.assert_note(result, "핸들 접근 신호가 아니다")
        self.assert_note(result, "서로 덮어쓸 수 있다")

    def test_dll_scope_canonicalizes_case_slashes_and_dot_segments(self):
        paths = ["C:/Game/extra.dll", "c:\\GAME\\unused\\..\\EXTRA.DLL", "\\\\?\\C:\\Game\\extra.dll", "\\??\\C:\\Game\\extra.dll"]
        keys = [self.analyse(sample(evidence=dll_evidence(module_path=p))).annotations.entity_key for p in paths]
        self.assertEqual(len(set(keys)), 1)

    def test_unc_dll_paths_canonicalize(self):
        paths = ["\\\\server\\share\\extra.dll", "\\\\?\\UNC\\server\\share\\extra.dll"]
        keys = [self.analyse(sample(evidence=dll_evidence(module_path=p))).annotations.entity_key for p in paths]
        self.assertIsNotNone(keys[0])
        self.assertEqual(keys[0], keys[1])

    def test_long_unicode_dll_path_fits_contract(self):
        result = self.analyse(sample(evidence=dll_evidence(module_path="C:/" + "모듈/" * 200 + "extra.dll")))
        self.assertIsNotNone(result.annotations.entity_key)
        self.assert_note(result, "경로 해시는 파일 해시가 아니며")

    def test_dll_different_pid_or_path_has_different_scope(self):
        events = [dll_evidence(), dll_evidence(target_pid=501), dll_evidence(module_path="C:/Other/extra.dll")]
        keys = [self.analyse(sample(evidence=e)).annotations.entity_key for e in events]
        self.assertEqual(len(set(keys)), 3)

    def test_invalid_dll_path_or_pid_never_falls_back_to_name_address_hash(self):
        for path in (None, "", "extra.dll", "C:extra.dll", "\\extra.dll", "C:/bad\x00.dll", 42):
            with self.subTest(path=path):
                result = self.analyse(sample(evidence=dll_evidence(module_path=path, module_name="extra.dll", base_address="0x1000", sha256="a"*64)))
                self.assertIsNone(result.annotations.entity_key)
        for pid in (True, 0, "500", 2**32):
            result = self.analyse(sample(evidence=dll_evidence(target_pid=pid)))
            self.assertIsNone(result.annotations.entity_key)

    def test_dll_zero_does_not_imply_previous_module_removed(self):
        result = self.analyse(sample(score=0, evidence=dll_evidence(status="NORMAL")))
        self.assertIsNone(result.annotations.entity_key)
        self.assert_note(result, "이전 DLL의 제거")

    def test_dll_initial_audit_not_new_injection_and_changed_not_text_patch(self):
        cases = [("baseline_unreviewed", "새로 주입된 DLL로 해석하지 않는다"), ("changed", "파일 바이트 변조")]
        for change, fragment in cases:
            result = self.analyse(sample(evidence=dll_evidence(change_type=change)))
            self.assert_note(result, fragment)

    def test_dll_unknown_change_has_no_entity(self):
        result = self.analyse(sample(evidence=dll_evidence(change_type="removed")))
        self.assertIsNone(result.annotations.entity_key)
        self.assert_note(result, "변화 종류를 추정하지 않는다")

    def test_dll_trust_read_failure_does_not_discard_mapping_observation(self):
        result = self.analyse(sample(score=1, evidence=dll_evidence(signature_status="unknown", inspection_error="file disappeared")))
        self.assertIsNotNone(result.annotations.entity_key)
        self.assert_note(result, "조회 실패와 DLL 매핑 관측은 구분")

    def test_dll_subchannel_bound_warns_even_when_common_profile_allows(self):
        result = self.analyse(sample(score=4, evidence=dll_evidence()))
        self.assertNotEqual(result.signal.state, "OUT_OF_AUDITED_RANGE")
        self.assert_note(result, "조사 상한은 3점")
        self.assertEqual(result.signal.raw_score, 4)

    def test_partial_and_unknown_zero_remain_distinct(self):
        result = self.analyse(sample(score=0, evidence={"submodule": "external_process", "coverage_complete": False}))
        self.assert_note(result, "부분 검사")
        self.assert_note(result, "0점만으로 검사 성공")

    def test_wrong_module_baseline_and_extra_root_fields_rejected(self):
        event = sample("not_localguard")
        with self.assertRaises(ValueError):
            evaluate(event, inspect_event(event))
        with self.assertRaises(ValueError):
            evaluate(sample(), inspect_event(sample("whistle")))
        event = sample()
        event["submodule"] = "external_process"
        with self.assertRaises(ValidationError):
            self.registry.evaluate(event)

    def test_repeated_interpretation_is_pure_not_accumulation(self):
        event = sample(evidence=dll_evidence())
        self.assertEqual(self.analyse(event), self.analyse(event))


if __name__ == "__main__":
    unittest.main()
