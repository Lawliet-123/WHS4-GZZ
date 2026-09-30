import json
import tempfile
import unittest
from pathlib import Path

from gzz_anticheat.paint_summary import summarize


class PaintSummaryTests(unittest.TestCase):
    def test_missing_collector_is_not_a_normal_verdict(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "manifest.json").write_text(json.dumps({"session_id": "normal", "label": "NORMAL"}))
            result = summarize(folder)
            self.assertEqual(result["behavioral_verdict"], "NOT_ENABLED_IN_SOURCE_SESSION")
            self.assertEqual(result["calls_by_function"], {})
            self.assertTrue(any("missing" in warning for warning in result["warnings"]))
