from __future__ import annotations

import unittest

from server.scoring.calibration import (
    CALIBRATION_VERSION,
    get_calibration,
    require_calibration,
    resolve_calibration,
)


class CalibrationTests(unittest.TestCase):
    def test_version_is_replay_v1(self):
        self.assertEqual(CALIBRATION_VERSION, "replay-v1")

    def test_replay_v1_thresholds(self):
        expected = {
            "aimbot": ("threshold", 4),
            "autopaint": ("threshold", 23),
            "godmode": ("event_threshold", 2),
            "hide_anywhere": ("threshold", 3),
            "injection": ("threshold", 40),
            "value_tamper": ("threshold", 100),
            "noclip": ("threshold", 3),
            "localguard_executable_hash": ("threshold", 1),
            "overlay_hook": ("threshold", 60),
        }

        for module, (mode, threshold) in expected.items():
            with self.subTest(module=module):
                calibration = require_calibration(module)
                self.assertEqual(calibration.version, "replay-v1")
                self.assertEqual(calibration.mode, mode)
                self.assertEqual(calibration.threshold, threshold)
                self.assertTrue(calibration.calibrated)

    def test_threshold_boundary_is_inclusive(self):
        calibration = require_calibration("aimbot")

        self.assertFalse(calibration.meets_threshold(3))
        self.assertTrue(calibration.meets_threshold(4))
        self.assertTrue(calibration.meets_threshold(8))

    def test_noclip_threshold_boundary_is_inclusive(self):
        calibration = require_calibration("noclip")

        self.assertFalse(calibration.meets_threshold(2))
        self.assertTrue(calibration.meets_threshold(3))
        self.assertTrue(calibration.meets_threshold(5))

    def test_executable_hash_threshold_boundary_is_inclusive(self):
        calibration = require_calibration("localguard_executable_hash")

        self.assertFalse(calibration.meets_threshold(0))
        self.assertTrue(calibration.meets_threshold(1))

    def test_godmode_event_threshold_is_not_snapshot_rule(self):
        calibration = require_calibration("godmode")

        self.assertEqual(calibration.mode, "event_threshold")
        self.assertFalse(calibration.meets_threshold(1))
        self.assertTrue(calibration.meets_threshold(2))
        self.assertTrue(calibration.meets_threshold(5))

    def test_filesystem_is_advisory(self):
        calibration = require_calibration("filesystem")

        self.assertEqual(calibration.mode, "advisory")
        self.assertIsNone(calibration.threshold)
        self.assertFalse(calibration.calibrated)
        self.assertIsNone(calibration.meets_threshold(0))
        self.assertIsNone(calibration.meets_threshold(100))

    def test_pending_modules_are_not_forced_clean_or_positive(self):
        for module in ("esp", "whistle", "whistle_rpc"):
            with self.subTest(module=module):
                calibration = require_calibration(module)
                self.assertEqual(calibration.mode, "pending")
                self.assertFalse(calibration.calibrated)
                self.assertIsNone(calibration.meets_threshold(0))
                self.assertIsNone(calibration.meets_threshold(100))

    def test_external_access_aggregate_stays_pending(self):
        calibration = resolve_calibration(
            "external_access",
            raw_score=8,
            evidence={
                "submodule": "aggregate",
                "status": "SUSPICIOUS",
            },
        )

        self.assertEqual(calibration.module, "external_access")
        self.assertIsNone(calibration.submodule)
        self.assertEqual(calibration.mode, "pending")
        self.assertIsNone(calibration.threshold)

    def test_external_process_has_separate_pending_calibration(self):
        calibration = resolve_calibration(
            "external_access",
            raw_score=8,
            evidence={
                "submodule": "external_process",
                "status": "SUSPICIOUS",
                "source_pid": 900,
            },
        )

        self.assertEqual(calibration.module, "external_access")
        self.assertEqual(calibration.submodule, "external_process")
        self.assertEqual(calibration.mode, "pending")
        self.assertIsNone(calibration.threshold)

    def test_module_integrity_has_separate_pending_calibration(self):
        calibration = resolve_calibration(
            "external_access",
            raw_score=2,
            evidence={
                "submodule": "module_integrity",
                "status": "SUSPICIOUS",
                "target_pid": 500,
                "module_path": "C:/Game/example.dll",
            },
        )

        self.assertEqual(calibration.module, "external_access")
        self.assertEqual(calibration.submodule, "module_integrity")
        self.assertEqual(calibration.mode, "pending")
        self.assertIsNone(calibration.threshold)

    def test_legacy_external_process_contract_uses_process_calibration(self):
        calibration = resolve_calibration(
            "external_access",
            raw_score=3,
            evidence={
                "source_pid": 900,
                "target_pid": 500,
            },
        )

        self.assertEqual(calibration.submodule, "external_process")
        self.assertEqual(calibration.mode, "pending")

    def test_external_submodule_pending_does_not_force_threshold_result(self):
        for evidence in (
            {
                "submodule": "external_process",
                "source_pid": 900,
            },
            {
                "submodule": "module_integrity",
                "target_pid": 500,
                "module_path": "C:/Game/example.dll",
            },
        ):
            with self.subTest(submodule=evidence["submodule"]):
                calibration = resolve_calibration(
                    "external_access",
                    raw_score=10,
                    evidence=evidence,
                )

                self.assertFalse(calibration.calibrated)
                self.assertIsNone(calibration.meets_threshold(10))

    def test_yara_legacy_without_ruleset_stays_pending(self):
        calibration = resolve_calibration(
            "localguard_yara",
            raw_score=3,
            evidence={
                "pid": 500,
                "scope": "selected_local_process_memory",
            },
        )

        self.assertEqual(calibration.mode, "pending")
        self.assertFalse(calibration.calibrated)
        self.assertIn("Legacy", calibration.note)

    def test_yara_custom_ruleset_is_advisory(self):
        calibration = resolve_calibration(
            "localguard_yara",
            raw_score=10,
            evidence={
                "ruleset": {
                    "id": "sha256:" + "a" * 64,
                    "source": "custom_cli",
                    "files": [{"file": "custom.yar", "sha256": "b" * 64}],
                    "rule_count": 1,
                    "test_rules_present": False,
                },
            },
        )

        self.assertEqual(calibration.mode, "advisory")
        self.assertIsNone(calibration.threshold)

    def test_yara_test_ruleset_is_advisory(self):
        calibration = resolve_calibration(
            "localguard_yara",
            raw_score=3,
            evidence={
                "ruleset": {
                    "id": "sha256:" + "a" * 64,
                    "source": "repository_default",
                    "files": [
                        {
                            "file": "repository_cheats.yar",
                            "sha256": "b" * 64,
                        },
                    ],
                    "rule_count": 12,
                    "test_rules_present": True,
                },
            },
        )

        self.assertEqual(calibration.mode, "advisory")
        self.assertIsNone(calibration.threshold)

    def test_yara_repository_default_identity_stays_pending_until_e2e(self):
        calibration = resolve_calibration(
            "localguard_yara",
            raw_score=3,
            evidence={
                "ruleset": {
                    "id": "sha256:" + "a" * 64,
                    "source": "repository_default",
                    "files": [
                        {
                            "file": "repository_cheats.yar",
                            "sha256": "b" * 64,
                        },
                    ],
                    "rule_count": 12,
                    "test_rules_present": False,
                },
            },
        )

        self.assertEqual(calibration.mode, "pending")
        self.assertFalse(calibration.calibrated)
        self.assertIn("E2E", calibration.note)

    def test_yara_malformed_repository_identity_stays_pending(self):
        calibration = resolve_calibration(
            "localguard_yara",
            raw_score=3,
            evidence={
                "ruleset": {
                    "id": "not-a-digest",
                    "source": "repository_default",
                    "files": [],
                    "rule_count": 0,
                    "test_rules_present": False,
                },
            },
        )

        self.assertEqual(calibration.mode, "pending")
        self.assertIn("malformed", calibration.note)

    def test_overlay_normal_zero_uses_base_threshold(self):
        calibration = resolve_calibration(
            "overlay_hook",
            raw_score=0,
            evidence={},
        )

        self.assertEqual(calibration.mode, "threshold")
        self.assertEqual(calibration.threshold, 60)
        self.assertFalse(calibration.meets_threshold(0))

    def test_overlay_controlled_harness_is_advisory(self):
        calibration = resolve_calibration(
            "overlay_hook",
            raw_score=100,
            evidence={
                "meta": {
                    "measurement_scope": "controlled_harness",
                    "untrusted_hookers": [
                        {"signature": "서명없음", "render": True},
                    ],
                },
            },
        )

        self.assertEqual(calibration.mode, "advisory")
        self.assertIsNone(calibration.threshold)

    def test_overlay_unverifiable_game_hook_is_advisory(self):
        calibration = resolve_calibration(
            "overlay_hook",
            raw_score=60,
            evidence={
                "meta": {
                    "measurement_scope": "game",
                    "untrusted_hookers": [
                        {"signature": "확인불가", "render": False},
                    ],
                },
            },
        )

        self.assertEqual(calibration.mode, "advisory")
        self.assertIsNone(calibration.threshold)

    def test_overlay_confirmed_untrusted_game_hook_uses_threshold(self):
        for signature in ("서명없음", "위조", "신뢰안됨"):
            with self.subTest(signature=signature):
                calibration = resolve_calibration(
                    "overlay_hook",
                    raw_score=60,
                    evidence={
                        "meta": {
                            "measurement_scope": "game",
                            "untrusted_hookers": [
                                {
                                    "signature": signature,
                                    "render": False,
                                },
                            ],
                        },
                    },
                )

                self.assertEqual(calibration.mode, "threshold")
                self.assertEqual(calibration.threshold, 60)
                self.assertTrue(calibration.meets_threshold(60))

    def test_overlay_legacy_positive_without_meta_stays_pending(self):
        calibration = resolve_calibration(
            "overlay_hook",
            raw_score=60,
            evidence={},
        )

        self.assertEqual(calibration.mode, "pending")
        self.assertIsNone(calibration.threshold)

    def test_unknown_module_is_distinct_from_pending(self):
        self.assertIsNone(get_calibration("future_detector"))

        with self.assertRaises(KeyError):
            require_calibration("future_detector")

    def test_invalid_raw_score_is_rejected(self):
        calibration = require_calibration("aimbot")

        for value in (-1, float("inf"), float("nan"), True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calibration.meets_threshold(value)


if __name__ == "__main__":
    unittest.main()
