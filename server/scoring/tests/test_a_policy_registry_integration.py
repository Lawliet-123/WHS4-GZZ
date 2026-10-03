"""A 담당 detector 정책이 서버 기본 Registry에 실제 연결되는지 검증한다."""

from __future__ import annotations

import unittest

from server.scoring import evaluate_event_policy
from server.scoring.policies import localguard
from server.scoring.policies.registry import build_default_registry


def event(module: str, raw_score: int = 0, *, evidence=None, reasons=None):
    return {
        "session_id": "registry_a",
        "player_id": "player_a",
        "module": module,
        "timestamp_ms": 1000,
        "evidence": evidence or {},
        "reasons": reasons or [],
        "raw_score": raw_score,
    }


class APolicyRegistryIntegrationTests(unittest.TestCase):

    def test_all_localguard_modules_are_registered(self):
        registry = build_default_registry()

        for module in localguard.SUPPORTED_MODULES:
            with self.subTest(module=module):
                result = registry.evaluate(event(module))
                # 미등록 handler라면 annotations는 비어 있으므로,
                # A의 읽기 전용 주석이 실제 적용됐는지 확인한다.
                self.assertTrue(result.annotations.notes)

    def test_whistle_modules_are_registered(self):
        registry = build_default_registry()

        for module in ("whistle", "whistle_rpc"):
            with self.subTest(module=module):
                result = registry.evaluate(event(module))
                self.assertTrue(result.annotations.notes)

    def test_hide_anywhere_is_registered(self):
        result = build_default_registry().evaluate(event(
            "hide_anywhere",
            evidence={
                "hide_value_pattern": 0,
                "injected_module": 0,
                "viewport_hook": 0,
            },
        ))
        self.assertTrue(result.annotations.notes)

    def test_esp_is_registered(self):
        result = build_default_registry().evaluate(event(
            "esp",
            2,
            evidence={
                "sensor_event_id": "registry-test",
                "event_type": "process_access",
                "categories": ["memory_read"],
                "source_pid": 900,
                "target_pid": 500,
            },
        ))

        self.assertEqual(result.signal.state, "POLICY_NOT_CALIBRATED")
        self.assertEqual(result.annotations.entity_key, "external_process:900")
        self.assertTrue(result.annotations.notes)

    def test_public_scoring_entrypoint_uses_a_registry(self):
        result = evaluate_event_policy(event(
            "external_access",
            6,
            evidence={
                "submodule": "external_process",
                "source_pid": 1234,
            },
        ))

        self.assertEqual(result.signal.module, "external_access")
        self.assertTrue(result.annotations.notes)


if __name__ == "__main__":
    unittest.main()
