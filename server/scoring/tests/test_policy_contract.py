"""A/B 공통 분석 계약 테스트. 위험도/판정의 정확도를 검사하는 테스트는 아니다."""

from __future__ import annotations

import unittest

from server.scoring.policies.contract import PolicyAnnotations, PolicyRegistry
from shared.errors import ValidationError


def sample(module="noclip", *, raw_score=3, evidence=None):
    """Shared의 정확한 7필드를 사용한 최소 테스트 이벤트."""
    return {
        "session_id": "s", "player_id": "p", "module": module,
        "timestamp_ms": 1000, "evidence": evidence if evidence is not None else {},
        "reasons": ["signal"], "raw_score": raw_score,
    }


class PolicyContractTests(unittest.TestCase):
    def test_without_handler_preserves_existing_b2a_inspection(self):
        """아직 구현하지 않은 모듈도 B2a 결과가 그대로 표시된다."""
        result = PolicyRegistry().evaluate(sample())
        self.assertEqual(result.signal.module, "noclip")
        self.assertEqual(result.signal.raw_fraction_pct, 60)
        self.assertEqual(result.annotations, PolicyAnnotations())

    def test_registered_handler_can_add_evidence_tags(self):
        """담당자 정책은 근거 태그만 추가하고 원시 점수를 바꾸지 않는다."""
        registry = PolicyRegistry()
        registry.register(
            "external_access",
            lambda event, base: PolicyAnnotations(
                entity_key=str(event["evidence"]["source_pid"]),
                overlap_tags=("process_access",),
                notes=("source PID는 재시작 때 달라질 수 있음",),
            ),
        )
        result = registry.evaluate(sample("external_access", raw_score=6,
                                          evidence={"source_pid": 1234}))
        self.assertEqual(result.signal.raw_score, 6)
        self.assertEqual(result.signal.emission, "per_entity_positive_only")
        self.assertEqual(result.annotations.entity_key, "1234")
        self.assertEqual(result.annotations.overlap_tags, ("process_access",))

    def test_unknown_and_pending_modules_do_not_appear_clean(self):
        """미등록/ESP 규격 대기를 점수 0 또는 정상 판정으로 만들지 않는다."""
        registry = PolicyRegistry()
        self.assertEqual(registry.evaluate(sample("new_mod")).signal.state,
                         "UNKNOWN_MODULE")
        self.assertEqual(registry.evaluate(sample("esp")).signal.state,
                         "AWAITING_DETECTOR")

    def test_failed_measurement_keeps_unavailable_state(self):
        """담당자 정책이 등록되어도 ERROR는 기본 분석 상태를 유지한다."""
        registry = PolicyRegistry()
        registry.register("noclip", lambda event, base: PolicyAnnotations(notes=("checked",)))
        result = registry.evaluate(sample(evidence={"status": "ERROR"}))
        self.assertEqual(result.signal.state, "MEASUREMENT_UNAVAILABLE")
        self.assertIsNone(result.signal.raw_fraction_pct)

    def test_godmode_remains_event_delta(self):
        """사건별 증분을 스냅샷 총점으로 오해하지 않는다."""
        registry = PolicyRegistry()
        result = registry.evaluate(sample("godmode"))
        self.assertEqual(result.signal.emission, "event_delta")
        self.assertIsNone(result.signal.raw_fraction_pct)

    def test_duplicate_registration_is_rejected(self):
        """두 담당자가 동일한 module을 실수로 중복 등록하면 즉시 실패한다."""
        registry = PolicyRegistry()
        handler = lambda event, base: PolicyAnnotations()
        registry.register("noclip", handler)
        with self.assertRaises(ValueError):
            registry.register("noclip", handler)

    def test_bad_handler_or_result_rejected(self):
        """함수 대신 값이 들어오거나 반환 자료형이 다르면 조기에 실패한다."""
        registry = PolicyRegistry()
        with self.assertRaises(TypeError):
            registry.register("aimbot", None)
        registry.register("aimbot", lambda event, base: {})
        with self.assertRaises(TypeError):
            registry.evaluate(sample("aimbot"))

    def test_invalid_annotations_are_rejected(self):
        """유효하지 않은 태그/주석 형식은 조용히 통과시키지 않는다."""
        for annotations in (
            PolicyAnnotations(entity_key=""),
            PolicyAnnotations(overlap_tags=("same", "same")),
            PolicyAnnotations(overlap_tags=["not-tuple"]),
            PolicyAnnotations(notes=("",)),
        ):
            with self.subTest(annotations=annotations):
                registry = PolicyRegistry()
                registry.register("noclip", lambda e, b: annotations)
                with self.assertRaises(ValueError):
                    registry.evaluate(sample())

    def test_original_input_is_not_changed_by_handler(self):
        """잘못 작성된 정책이 evidence를 수정해도 원래 이벤트는 보존한다."""
        event = sample(evidence={"nested": {"name": "original"}})
        registry = PolicyRegistry()
        def wrong_handler(copied, baseline):
            copied["evidence"]["nested"]["name"] = "modified"
            return PolicyAnnotations()
        registry.register("noclip", wrong_handler)
        registry.evaluate(event)
        self.assertEqual(event["evidence"]["nested"]["name"], "original")

    def test_shared_seven_field_validation_is_required(self):
        """필드가 빠진 입력은 담당자 정책이 있어도 검사 전에 거부한다."""
        event = sample()
        del event["raw_score"]
        with self.assertRaises(ValidationError):
            PolicyRegistry().evaluate(event)

    def test_registries_are_independent(self):
        """테스트/서버마다 정책 등록 객체를 분리하여 등록 충돌을 막는다."""
        first = PolicyRegistry()
        second = PolicyRegistry()
        first.register("noclip", lambda event, base: PolicyAnnotations(notes=("first",)))
        self.assertEqual(first.evaluate(sample()).annotations.notes, ("first",))
        self.assertEqual(second.evaluate(sample()).annotations.notes, ())


if __name__ == "__main__":
    unittest.main()
