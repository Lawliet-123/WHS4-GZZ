"""Hide Anywhere의 이벤트 의미/전송 한계 검증. 실게임 탐지율 검증은 아님."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from server.scoring.policies.contract import PolicyRegistry
from server.scoring.policies.hide_anywhere import evaluate
from server.scoring.policy import inspect_event
from shared.errors import ValidationError
from shared.schema import validate_event_id


REPO_ROOT = Path(__file__).resolve().parents[3]


def sample(*, score=3, evidence=None, reasons=None, module="hide_anywhere"):
    return {
        "session_id": "synthetic_hide", "player_id": "test_player", "module": module,
        "timestamp_ms": 1000, "evidence": deepcopy(evidence) if evidence is not None else {
            "hide_value_pattern": 1, "injected_module": 0, "viewport_hook": 0,
        }, "reasons": list(reasons) if reasons is not None else ["Hide Anywhere Value Pattern Matched"],
        "raw_score": score,
    }


class HideAnywherePolicyTests(unittest.TestCase):
    def setUp(self):
        self.registry = PolicyRegistry()
        self.registry.register("hide_anywhere", evaluate)

    def analyse(self, event):
        original = deepcopy(event)
        result = self.registry.evaluate(event)
        self.assertEqual(event, original)
        self.assertEqual(result.signal, inspect_event(original))
        self.assertIsNone(result.annotations.entity_key)
        self.assertEqual(result.annotations.overlap_tags, ())
        return result

    def assert_note(self, result, fragment):
        self.assertTrue(any(fragment in note for note in result.annotations.notes), fragment)

    def test_pattern_preserves_score_but_is_not_behavior_proof(self):
        result = self.analyse(sample())
        self.assertEqual(result.signal.raw_score, 3)
        self.assert_note(result, "실제 숨기 성공")
        self.assert_note(result, "3회 연속 확인 증명이 아니다")

    def test_snapshot_repeated_result_is_not_new_event_accumulation(self):
        event = sample()
        self.assertEqual(self.analyse(event), self.analyse(event))
        self.assertEqual(self.analyse(event).signal.emission, "snapshot")

    def test_explicit_failed_or_offline_measurement_not_revived_by_pattern(self):
        for fields in ({"status": "ERROR"}, {"status": "OFFLINE"}, {"measurement_valid": False}):
            event = sample()
            event["evidence"].update(fields)
            result = self.analyse(event)
            self.assertEqual(result.signal.state, "MEASUREMENT_UNAVAILABLE")
            self.assertEqual(result.signal.raw_score, 3)
            self.assert_note(result, "유효 관측을 복구하지 않는다")

    def test_validity_unspecified_never_assumed_fresh(self):
        result = self.analyse(sample())
        self.assert_note(result, "측정 유효성이 명시되지 않았다")
        self.assert_note(result, "이전 성공 값을 유지할 수 있다")

    def test_explicit_validity_is_report_not_independent_recheck(self):
        event = sample()
        event["evidence"]["measurement_valid"] = True
        result = self.analyse(event)
        self.assert_note(result, "생산자의 명시적 보고")
        self.assertFalse(any("측정 유효성이 명시되지 않았다" in note for note in result.annotations.notes))

    def test_zero_flags_do_not_prove_successful_read(self):
        result = self.analyse(sample(score=0, reasons=[], evidence={
            "hide_value_pattern": 0, "injected_module": 0, "viewport_hook": 0,
        }))
        self.assert_note(result, "성공한 최신 메모리 검사")

    def test_auxiliary_module_and_viewport_are_not_specific_cheat_behavior(self):
        result = self.analyse(sample(score=2, reasons=["Injected Module Loaded", "Viewport VTable Outside Main Image"], evidence={
            "hide_value_pattern": 0, "injected_module": 1, "viewport_hook": 1,
        }))
        self.assert_note(result, "파일 해시/서명")
        self.assert_note(result, "특정 ESP 사용과 구분")
        self.assertEqual(result.signal.raw_score, 2)

    def test_pattern_wins_without_readding_auxiliary_points(self):
        result = self.analyse(sample(score=3, reasons=list(("Hide Anywhere Value Pattern Matched", "Injected Module Loaded", "Viewport VTable Outside Main Image")), evidence={
            "hide_value_pattern": 1, "injected_module": 1, "viewport_hook": 1,
        }))
        self.assertEqual(result.signal.raw_score, 3)
        self.assertFalse(any("원점수가 현재 v9 생성식" in note for note in result.annotations.notes))

    def test_missing_or_non_integer_flags_not_filled_as_zero(self):
        for value in (None, True, "1", 1.0, -1, 2, []):
            event = sample()
            event["evidence"]["hide_value_pattern"] = value
            self.assert_note(self.analyse(event), "플래그가 누락되거나 형식이 다르다")
        self.assert_note(self.analyse(sample(evidence={})), "플래그가 누락되거나 형식이 다르다")

    def test_historical_pattern_score_not_rewritten_to_v9_score(self):
        result = self.analyse(sample(score=1))
        self.assertEqual(result.signal.raw_score, 1)
        self.assert_note(result, "구버전/계약 차이")

    def test_out_of_range_score_preserved(self):
        result = self.analyse(sample(score=4))
        self.assertEqual(result.signal.state, "OUT_OF_AUDITED_RANGE")
        self.assertEqual(result.signal.raw_score, 4)

    def test_inconsistent_reason_and_flags_not_repaired(self):
        result = self.analyse(sample(reasons=["Injected Module Loaded"]))
        self.assert_note(result, "플래그와 알려진 reason 목록")

    def test_unknown_or_absent_reason_not_dynamic_tag(self):
        self.assert_note(self.analyse(sample(reasons=["arbitrary future reason"])), "미분류 reason")
        self.assert_note(self.analyse(sample(reasons=[])), "양수 점수에 reason이 없다")

    def test_partial_measurement_not_full_clean(self):
        event = sample()
        event["evidence"].update(status="WARNING", coverage_complete=False)
        self.assert_note(self.analyse(event), "부분 검사")

    def test_extra_identity_hints_do_not_make_fake_event_key(self):
        event = sample()
        event["evidence"].update(pid=500, pawn="0x1234", sample_id=42, log_path="C:/raw/hide.jsonl")
        self.analyse(event)

    def test_wrong_module_baseline_and_extra_root_fields_rejected(self):
        event = sample(module="value_tamper")
        with self.assertRaises(ValueError):
            evaluate(event, inspect_event(event))
        with self.assertRaises(ValueError):
            evaluate(sample(), inspect_event(event))
        event = sample()
        event["status"] = "NORMAL"
        with self.assertRaises(ValidationError):
            self.registry.evaluate(event)

    def test_real_v9_producer_immediate_match_and_rule_confirmation_are_different(self):
        path = REPO_ROOT / "client/detectors/Hide_anywhere_detector/mecha_detector_v9.py"
        spec = importlib.util.spec_from_file_location("_hide_v9_fixture", path)
        producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(producer)
        rule = producer.Rule(required=3)
        values = dict(producer.EXPECTED)
        self.assertFalse(rule.evaluate("pawn", values))
        event = producer.make_common_event("fixture_hide", "fixture_player", "hide_anywhere", 1000, values)
        self.assertEqual(event["raw_score"], 3)
        self.assert_note(self.analyse(event), "3회 연속 확인 증명이 아니다")
        for values, score in (({}, 0), ({}, 1), ({}, 2)):
            event = producer.make_common_event("fixture_hide", "fixture_player", "hide_anywhere", 1000,
                                               values, injected_module=score > 0, viewport_hook=score > 1)
            self.assertEqual(self.analyse(event).signal.raw_score, score)

    def test_current_bridge_event_id_still_fails_shared_contract_not_policy_fixed(self):
        with self.assertRaises(ValidationError):
            validate_event_id("a" * 32 + ":1")

    def test_actual_v9_replay_preserved_not_relabelled_as_https_success(self):
        path = REPO_ROOT / "ReplayAnalyzer/replay-data/hide-anywhere/hide_anywhere_003/events.jsonl"
        original = path.read_bytes()
        rows = [json.loads(line) for line in original.decode("utf-8-sig").splitlines()]
        self.assertEqual(rows[0]["raw_score"], 0)
        self.assertEqual(rows[21]["raw_score"], 3)
        for row in rows:
            result = self.analyse(row)
            self.assertEqual(result.signal.raw_score, row["raw_score"])
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
