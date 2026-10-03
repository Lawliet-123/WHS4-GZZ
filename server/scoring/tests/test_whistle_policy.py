"""Whistle 정책 계약 테스트. 실게임 탐지율이나 중앙 HTTPS 성공 검증은 아님."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from server.scoring.policies.contract import PolicyAnnotations, PolicyRegistry
from server.scoring.policies.whistle import evaluate
from server.scoring.policy import inspect_event
from shared.errors import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[3]


def sample(module="whistle", *, score=60, reasons=None, evidence=None):
    """모듈 알고리즘이 아닌 입력 해석을 검사하는 합성 7필드 Event."""
    if reasons is None:
        reasons = ["exec_function_hooked"] if module == "whistle" else ["cooldown_violation"]
    return {
        "session_id": "synthetic_whistle", "player_id": "test_player",
        "module": module, "timestamp_ms": 1000,
        "evidence": deepcopy(evidence) if evidence is not None else {},
        "reasons": list(reasons), "raw_score": score,
    }


class WhistlePolicyTests(unittest.TestCase):
    def setUp(self):
        # B의 공통 등록 파일은 변경하지 않고 독립 Registry에서 연결을 검사한다.
        self.registry = PolicyRegistry()
        for module in ("whistle", "whistle_rpc"):
            self.registry.register(module, evaluate)

    def analyse(self, event):
        original = deepcopy(event)
        result = self.registry.evaluate(event)
        self.assertEqual(event, original)
        self.assertEqual(result.signal, inspect_event(original))
        self.assertIsInstance(result.annotations, PolicyAnnotations)
        self.assertEqual(result.annotations.overlap_tags, ())
        return result

    def assert_note(self, result, fragment):
        self.assertTrue(any(fragment in text for text in result.annotations.notes), fragment)

    def test_static_positive_preserves_score_and_scopes_game_pid(self):
        event = sample(evidence={"meta": {"target_pid": 7996}, "status": "SUSPICIOUS"})
        result = self.analyse(event)
        self.assertEqual(result.signal.raw_score, 60)
        self.assertEqual(result.annotations.entity_key, "game_pid:7996")
        self.assert_note(result, "PID 재사용")
        self.assert_note(result, "ExecFunction 교체")

    def test_zero_normal_does_not_clear_previous_risk_or_create_entity(self):
        result = self.analyse(sample(score=0, reasons=[], evidence={"status": "NORMAL", "meta": {"target_pid": 7996}}))
        self.assertIsNone(result.annotations.entity_key)
        self.assert_note(result, "명시적 NORMAL 0점")
        self.assert_note(result, "중앙에는 양수만")
        self.assert_note(result, "유지·만료 기준")

    def test_missing_status_zero_is_not_assumed_successful(self):
        result = self.analyse(sample(score=0, reasons=[]))
        self.assert_note(result, "NORMAL로 자동 보완하지 않는다")

    def test_failed_or_offline_measurement_never_creates_entity_or_tags(self):
        for module in ("whistle", "whistle_rpc"):
            for evidence in (
                {"status": "ERROR"}, {"status": "OFFLINE"}, {"measurement_valid": False},
            ):
                with self.subTest(module=module, evidence=evidence):
                    evidence = dict(evidence, meta={"target_pid": 7996})
                    result = self.analyse(sample(module, score=40, evidence=evidence))
                    self.assertEqual(result.signal.state, "MEASUREMENT_UNAVAILABLE")
                    self.assertEqual(result.signal.raw_score, 40)
                    self.assertIsNone(result.annotations.entity_key)
                    self.assert_note(result, "이전 위험이 해소된 관측으로 사용하지 않는다")

    def test_partial_measurement_remains_partial(self):
        result = self.analyse(sample(evidence={"status": "WARNING", "coverage_complete": False}))
        self.assert_note(result, "검사 범위가 부분적")

    def test_address_text_is_not_parsed_as_entity(self):
        result = self.analyse(sample(evidence={"address": "0x00007FFD12345678 (Play -> module.dll)"}))
        self.assertIsNone(result.annotations.entity_key)
        self.assert_note(result, "파싱으로 함수·주소별 사건 키를 만들지 않는다")

    def test_invalid_game_pid_is_not_an_entity(self):
        for pid in (True, False, 0, -1, 1.5, "7996", 2**32, None):
            with self.subTest(pid=pid):
                result = self.analyse(sample(evidence={"meta": {"target_pid": pid}}))
                self.assertIsNone(result.annotations.entity_key)

    def test_missing_or_non_object_meta_is_supported(self):
        for meta in (None, [], "legacy description", 123):
            with self.subTest(meta=meta):
                result = self.analyse(sample("whistle_rpc", score=40, evidence={"meta": meta}))
                self.assertIsNone(result.annotations.entity_key)
                self.assert_note(result, "RPC 관측 모드와 로그 구간 정보가 부족")

    def test_static_vtable_and_sound_are_distinct_observations(self):
        result = self.analyse(sample(score=95, reasons=["character_vtable_hooked", "provocation_sound_swapped"]))
        self.assert_note(result, "ProcessEvent vtable 교체")
        self.assert_note(result, "사운드 교체")
        self.assertEqual(result.signal.raw_score, 95)

    def test_rpc_window_is_not_an_event_delta(self):
        result = self.analyse(sample("whistle_rpc", score=40, evidence={"meta": {
            "mode": "window", "window_calls": 22, "window_violation_records": 14,
        }}))
        self.assert_note(result, "새 로그 구간 관측")
        self.assert_note(result, "raw_score는 사건별 증분 계약이 아니며")
        self.assertEqual(result.signal.emission, "window_history")
        self.assertEqual(result.signal.raw_score, 40)
        self.assertIsNone(result.annotations.entity_key)

    def test_rpc_zero_calls_is_not_a_test_of_normal_call_behavior(self):
        result = self.analyse(sample("whistle_rpc", score=0, reasons=[], evidence={
            "status": "NORMAL", "meta": {"mode": "window", "window_calls": 0},
        }))
        self.assert_note(result, "이번 구간의 도발 호출은 0건")
        self.assert_note(result, "후크 생존을 독립 검증하지 않는다")

    def test_rpc_observed_normal_calls_are_not_missing_calls(self):
        result = self.analyse(sample("whistle_rpc", score=0, reasons=[], evidence={
            "status": "NORMAL", "meta": {"mode": "window", "window_calls": 12},
        }))
        self.assert_note(result, "이번 구간의 호출 관측 수")
        self.assertFalse(any("도발 호출은 0건" in note for note in result.annotations.notes))

    def test_rpc_invalid_or_absent_call_count_is_not_zero(self):
        for calls in (True, -1, "0", 1.5, None):
            with self.subTest(calls=calls):
                result = self.analyse(sample("whistle_rpc", evidence={"meta": {"mode": "window", "window_calls": calls}}))
                self.assert_note(result, "호출 유무를 확정하지 않는다")

    def test_rpc_old_full_log_can_not_be_counted_as_new_events(self):
        result = self.analyse(sample("whistle_rpc", score=40, evidence={"meta": {"provocation_calls": 22, "violation_records": 14}}))
        self.assert_note(result, "후크 로그 전체")
        self.assert_note(result, "반복 결과를 새로운 사건으로 가산하지 않는다")

    def test_rpc_log_restart_does_not_become_an_entity(self):
        result = self.analyse(sample("whistle_rpc", evidence={"window_id": 12, "meta": {
            "mode": "window", "window_calls": 2, "log_restarts": 1,
            "target_pid": 7996, "log": "C:/logs/ac-whistle.jsonl", "cumulative_calls": 80,
        }}))
        self.assert_note(result, "로그 축소·재시작")
        self.assertIsNone(result.annotations.entity_key)

    def test_rpc_violation_count_is_not_multiplied_into_raw_score(self):
        event = sample("whistle_rpc", score=40, evidence={"meta": {
            "mode": "window", "window_calls": 200, "window_violation_records": 100,
        }})
        result = self.analyse(event)
        self.assertEqual(result.signal.raw_score, 40)
        self.assert_note(result, "호출 횟수를 다시 곱하지 않는다")
        self.assert_note(result, "공식 쿨다운으로 확정하지 않는다")

    def test_rpc_reason_variants_are_explained_without_score_changes(self):
        for reason, fragment in (
            ("role_violation", "호출자 역할"), ("dead_caller", "생존 상태"),
            ("foreign_target", "대상 지정"), ("no_input_event", "화면 회전 입력 탐지와 같은 신호는 아니다"),
        ):
            with self.subTest(reason=reason):
                result = self.analyse(sample("whistle_rpc", score=60, reasons=[reason]))
                self.assert_note(result, fragment)
                self.assertEqual(result.signal.raw_score, 60)

    def test_unknown_reason_is_not_a_new_correlation_tag(self):
        result = self.analyse(sample("whistle_rpc", score=30, reasons=["unknown_reason"]))
        self.assert_note(result, "미분류 reason")

    def test_positive_without_reason_preserves_input_and_warns(self):
        result = self.analyse(sample(reasons=[]))
        self.assert_note(result, "양수 점수에 reason 코드가 없다")

    def test_out_of_range_score_is_preserved(self):
        result = self.analyse(sample(score=101))
        self.assertEqual(result.signal.raw_score, 101)
        self.assertEqual(result.signal.state, "OUT_OF_AUDITED_RANGE")
        self.assert_note(result, "원점수를 자르거나 정상화하지 않고")

    def test_wrong_module_or_baseline_is_rejected(self):
        event = sample("injection")
        with self.assertRaises(ValueError):
            evaluate(event, inspect_event(event))
        with self.assertRaises(ValueError):
            evaluate(sample(), inspect_event(sample("whistle_rpc")))

    def test_seven_field_contract_still_required(self):
        for key in ("status", "score", "event_id"):
            event = sample()
            event[key] = "unexpected"
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.registry.evaluate(event)

    def test_repeated_evaluation_does_not_accumulate_or_mutate(self):
        event = sample("whistle_rpc", score=40)
        first = self.analyse(event)
        second = self.analyse(event)
        self.assertEqual(first, second)
        self.assertEqual(second.signal.raw_score, 40)

    def test_both_channels_do_not_automatically_become_correlated(self):
        for module in ("whistle", "whistle_rpc"):
            result = self.analyse(sample(module, score=40))
            self.assertEqual(result.annotations.overlap_tags, ())
            self.assert_note(result, "같은 사건 또는 독립 사건으로 단정")

    def test_captured_measurement_rows_adapted_only_in_test(self):
        """옛 score 형식의 캡처를 테스트에서만 7필드로 포장한다. 원본 파일은 보존한다."""
        folder = REPO_ROOT / "client/detectors/whistle-spoofing/measurements"
        cases = (("rpc_clean_001.jsonl", 4, 0), ("rpc_pi_002.jsonl", 1, 40))
        for filename, line_no, score in cases:
            with self.subTest(filename=filename):
                path = folder / filename
                original_bytes = path.read_bytes()
                row = json.loads(original_bytes.decode("utf-8-sig").splitlines()[line_no - 1])
                evidence = deepcopy(row["evidence"])
                evidence.update(status=row["status"], severity=row["severity"])
                event = {
                    "session_id": row["session_id"], "player_id": "test_fixture_player",
                    "module": row["module"], "timestamp_ms": row["timestamp_ms"],
                    "evidence": evidence, "reasons": row["reasons"], "raw_score": row["score"],
                }
                result = self.analyse(event)
                self.assertEqual(result.signal.raw_score, score)
                self.assert_note(result, "단발·과거 측정 형식")
                self.assertEqual(path.read_bytes(), original_bytes)
                # 캡처의 옛 본문은 현재 Shared 본문과 다르며 실제 전송 검증이 아니다.
                with self.assertRaises(ValidationError):
                    self.registry.evaluate(row)


if __name__ == "__main__":
    unittest.main()
