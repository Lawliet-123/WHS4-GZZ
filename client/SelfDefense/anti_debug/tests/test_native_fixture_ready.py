"""Regression: a ready-file path can exist before its JSON is complete."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_integration import fixture_is_ready


class NativeFixtureReadyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gzz-ready-test-")
        self.addCleanup(self.temp.cleanup)
        self.ready = Path(self.temp.name) / "ready"

    def test_missing_file_is_not_ready(self):
        self.assertFalse(fixture_is_ready(self.ready, 123))

    def test_empty_file_is_not_ready(self):
        self.ready.write_text("", encoding="utf-8")
        self.assertFalse(fixture_is_ready(self.ready, 123))

    def test_partial_json_is_not_ready(self):
        self.ready.write_text('{"pid":', encoding="utf-8")
        self.assertFalse(fixture_is_ready(self.ready, 123))

    def test_complete_owned_identity_is_ready(self):
        self.ready.write_text(json.dumps({"pid": 123}), encoding="utf-8")
        self.assertTrue(fixture_is_ready(self.ready, 123))

    def test_wrong_pid_is_rejected(self):
        self.ready.write_text(json.dumps({"pid": 456}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            fixture_is_ready(self.ready, 123)

    def test_boolean_pid_is_rejected(self):
        self.ready.write_text(json.dumps({"pid": True}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            fixture_is_ready(self.ready, 1)

    def test_non_object_is_rejected(self):
        self.ready.write_text("123", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            fixture_is_ready(self.ready, 123)

    def test_permissions_error_is_not_hidden_as_pending(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("fixture")):
            with self.assertRaises(PermissionError):
                fixture_is_ready(self.ready, 123)


if __name__ == "__main__":
    unittest.main()
