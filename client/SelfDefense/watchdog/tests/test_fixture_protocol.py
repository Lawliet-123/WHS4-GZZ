"""Regression tests for IPC identity; these do not start or stop any processes."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_protocol import control_path, matches_process


class FixtureProtocolTests(unittest.TestCase):
    def setUp(self):
        self.record = dict(status="RUNNING", pid=22, create_time=200,
                           parent_pid=11, parent_create_time=100)

    def test_direct_python(self):
        self.assertTrue(matches_process(self.record, 22, 200))

    def test_venv_redirector(self):
        self.assertTrue(matches_process(self.record, 11, 100))

    def test_reused_worker_pid(self):
        self.assertFalse(matches_process(self.record, 22, 201))

    def test_reused_parent_pid(self):
        self.assertFalse(matches_process(self.record, 11, 101))

    def test_missing_or_stopped(self):
        self.assertFalse(matches_process(None, 22, 200))
        self.record["status"] = "STOPPED"
        self.assertFalse(matches_process(self.record, 22, 200))

    def test_missing_creation_time(self):
        self.assertFalse(matches_process(self.record, 22, 0))
        self.record["create_time"] = 0
        self.assertFalse(matches_process(self.record, 11, 100))

    def test_private_control_path(self):
        self.assertEqual(control_path(Path("owned"), "a" * 32),
                         Path("owned/artifacts/control") / ("a" * 32 + ".json"))

    def test_reject_invalid_control_id(self):
        for value in (None, "../release", "a" * 31, "A" * 32, 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                control_path(Path("owned"), value)


if __name__ == "__main__":
    unittest.main()
