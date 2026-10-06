from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("validate_environment.py")
SPEC = importlib.util.spec_from_file_location("validate_environment", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def valid_environment() -> dict[str, str]:
    return {
        "GZZ_TELEMETRY_TOKEN": "a" * 64,
        "MECCHA_HEARTBEAT_TOKEN": "b" * 64,
        "GZZ_DASHBOARD_TOKEN": "c" * 64,
        "GZZ_DASHBOARD_CURSOR_SECRET": "d" * 64,
        "GZZ_TELEMETRY_LOG_ROOT": "/var/lib/meccha-anticheat/detections",
        "GZZ_SCORING_DB": "/var/lib/meccha-anticheat/scoring/scoring.sqlite3",
        "MECCHA_HEARTBEAT_DB": "/var/lib/meccha-anticheat/heartbeat/heartbeat.sqlite3",
        "GZZ_DASHBOARD_INDEX": "/var/lib/meccha-anticheat/dashboard/dashboard.sqlite3",
    }


class ValidateEnvironmentTests(unittest.TestCase):
    def test_accepts_production_layout(self) -> None:
        self.assertEqual(MODULE.validate_environment(valid_environment()), [])

    def test_rejects_example_placeholder(self) -> None:
        environment = valid_environment()
        environment["GZZ_TELEMETRY_TOKEN"] = "REPLACE_WITH_SECRET_VALUE"
        errors = MODULE.validate_environment(environment)
        self.assertTrue(any("placeholder" in error for error in errors))

    def test_rejects_reused_secret(self) -> None:
        environment = valid_environment()
        environment["MECCHA_HEARTBEAT_TOKEN"] = environment["GZZ_TELEMETRY_TOKEN"]
        errors = MODULE.validate_environment(environment)
        self.assertIn("server secrets must use independent values", errors)

    def test_rejects_state_outside_managed_directory(self) -> None:
        environment = valid_environment()
        environment["GZZ_SCORING_DB"] = "/opt/meccha-anticheat/scoring.sqlite3"
        errors = MODULE.validate_environment(environment)
        self.assertTrue(any("GZZ_SCORING_DB must stay under" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
