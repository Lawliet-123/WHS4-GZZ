from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
import unittest
from unittest.mock import patch

import anti_esp.dashboard as dashboard_module
from anti_esp.dashboard import (
    classify_status,
    event_row,
    format_access,
    format_metric,
    format_points,
    format_sensor_status,
    normalize_percent,
    normalize_status,
    sensor_semantic_state,
    snapshot_values,
)


class ReviewLevel(Enum):
    HIGH = "high"


@dataclass
class Snapshot:
    suspicion: float
    observation_confidence: float
    status: object


@dataclass
class EvidenceEvent:
    timestamp: datetime
    category: str
    source: str
    reason: str
    details: dict = field(default_factory=dict)


class DashboardFormattingTests(unittest.TestCase):
    def test_missing_pyqt_has_a_clear_runtime_error(self):
        with patch.object(
            dashboard_module.importlib,
            "import_module",
            side_effect=ModuleNotFoundError("PyQt5 is unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "requires PyQt5"):
                dashboard_module._load_qt()

    def test_percent_normalization_and_metric_format(self):
        self.assertEqual(normalize_percent(-4), 0.0)
        self.assertEqual(normalize_percent("72.25"), 72.25)
        self.assertEqual(normalize_percent(104), 100.0)
        self.assertIsNone(normalize_percent(math.nan))
        self.assertIsNone(normalize_percent(True))
        self.assertEqual(format_metric(72), "72 / 100")
        self.assertEqual(format_metric(72.25), "72.2 / 100")
        self.assertEqual(format_metric(None), "— / 100")

    def test_status_is_separate_from_observation_confidence(self):
        self.assertEqual(classify_status(90, 20), "INSUFFICIENT")
        self.assertEqual(classify_status(12, 90), "LOW")
        self.assertEqual(classify_status(40, 90), "REVIEW")
        self.assertEqual(classify_status(65, 90), "HIGH")
        self.assertEqual(classify_status(85, 90), "CRITICAL")

    def test_explicit_status_is_authoritative_and_normalized(self):
        self.assertEqual(normalize_status(ReviewLevel.HIGH, 10, 10), "HIGH")
        self.assertEqual(normalize_status("warning", 10, 90), "REVIEW")
        self.assertEqual(normalize_status("no_data", 90, 90), "INSUFFICIENT")
        self.assertEqual(normalize_status("unexpected", 62, 80), "HIGH")

    def test_snapshot_supports_scoring_core_dataclass_shape(self):
        self.assertEqual(
            snapshot_values(Snapshot(73.5, 82.0, ReviewLevel.HIGH)),
            (73.5, 82.0, "HIGH"),
        )
        self.assertEqual(
            snapshot_values(
                {
                    "suspicion_score": 44,
                    "observation_confidence": 77,
                    "status": "review",
                }
            ),
            (44.0, 77.0, "REVIEW"),
        )

    def test_access_and_points_are_explicitly_formatted(self):
        self.assertEqual(format_access(0x1F0FFF), "0x001F0FFF")
        self.assertEqual(format_access("PROCESS_VM_READ"), "PROCESS_VM_READ")
        self.assertEqual(format_points(12), "+12")
        self.assertEqual(format_points(-2.25), "-2.2")
        self.assertEqual(format_points(None), "—")

    def test_event_row_reads_access_and_points_from_details(self):
        event = EvidenceEvent(
            timestamp=datetime(2026, 9, 18, 10, 11, 12, 345000),
            category="process_access",
            source="python.exe",
            reason="Untrusted process opened the game",
            details={"granted_access": 0x1F0FFF, "score_contribution": 28},
        )
        self.assertEqual(
            event_row(event),
            (
                "2026-09-18 10:11:12.345",
                "process_access",
                "python.exe",
                "0x001F0FFF",
                "+28",
                "Untrusted process opened the game",
            ),
        )

    def test_event_row_does_not_invent_missing_access_or_points(self):
        row = event_row(
            {
                "timestamp": "10:15:02",
                "category": "overlay",
                "source": "window-sensor",
                "reason": "Matching transparent window",
            }
        )
        self.assertEqual(row[3], "—")
        self.assertEqual(row[4], "—")

    def test_sensor_status_supports_boolean_mapping_and_sysmon_shape(self):
        self.assertEqual(format_sensor_status(True), "ONLINE")
        self.assertEqual(sensor_semantic_state(False), "offline")
        self.assertEqual(
            format_sensor_status({"available": False, "code": "not_installed"}),
            "UNAVAILABLE",
        )
        self.assertEqual(
            format_sensor_status({"available": True, "enabled": True}), "ONLINE"
        )
        self.assertEqual(format_sensor_status({"state": "starting"}), "WAITING")
        self.assertEqual(format_sensor_status(None), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
