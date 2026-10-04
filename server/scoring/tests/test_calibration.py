from __future__ import annotations

import unittest

from server.scoring.calibration import (
    CALIBRATION_VERSION,
    get_calibration,
    require_calibration,
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
