import json
import tempfile
import unittest
from pathlib import Path

from anti_esp.config import AllowlistSettings, load_settings
from anti_esp.policy import (
    FileFingerprintCache,
    PROCESS_VM_OPERATION,
    PROCESS_VM_READ,
    PROCESS_VM_WRITE,
    assess_process_access,
)


class ConfigPolicyTests(unittest.TestCase):
    def test_load_settings_resolves_database_relative_to_config(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"database_path": "state/events.db"}), encoding="utf-8")
            settings = load_settings(path)
            self.assertEqual(settings.database_path, (Path(temp) / "state/events.db").resolve())

    def test_team_telemetry_and_module_settings_are_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "telemetry": {
                            "enabled": True,
                            "root": "sessions",
                            "session_id": "esp_001",
                            "player_id": "player_042",
                            "scenario": "esp",
                            "cheat_on_ms": 30000,
                            "cheat_off_ms": 70000,
                        },
                        "module_monitor": {
                            "enabled": True,
                            "scan_interval_seconds": 7,
                        },
                        "handle_monitor": {
                            "enabled": True,
                            "scan_interval_seconds": 3,
                            "cooldown_seconds": 9,
                        },
                    }
                ),
                encoding="utf-8",
            )
            settings = load_settings(path)
            self.assertTrue(settings.telemetry.enabled)
            self.assertEqual(settings.telemetry.session_id, "esp_001")
            self.assertEqual(settings.telemetry.player_id, "player_042")
            self.assertEqual(settings.telemetry.scenario, "esp")
            self.assertEqual(settings.telemetry.cheat_on_ms, 30000)
            self.assertEqual(settings.telemetry.cheat_off_ms, 70000)
            self.assertEqual(settings.telemetry.root, (Path(temp) / "sessions").resolve())
            self.assertTrue(settings.module_monitor.enabled)
            self.assertEqual(settings.module_monitor.scan_interval_seconds, 7.0)
            self.assertTrue(settings.handle_monitor.enabled)
            self.assertEqual(settings.handle_monitor.scan_interval_seconds, 3.0)
            self.assertEqual(settings.handle_monitor.cooldown_seconds, 9.0)

    def test_untrusted_vm_read_is_high_signal(self):
        result = assess_process_access(
            source_path="C:/tools/reader.exe",
            access_mask=PROCESS_VM_READ,
            allowlist=AllowlistSettings(),
            fingerprints=FileFingerprintCache(),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.category, "memory_read")
        self.assertGreater(result.strength, 0.8)

    def test_tamper_rights_take_precedence_over_read(self):
        result = assess_process_access(
            source_path="C:/tools/reader.exe",
            access_mask=PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION,
            allowlist=AllowlistSettings(),
            fingerprints=FileFingerprintCache(),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.category, "process_tamper")

    def test_allowlisted_path_is_not_scored(self):
        source = str(Path("C:/trusted/tool.exe")).casefold()
        result = assess_process_access(
            source_path=source,
            access_mask=PROCESS_VM_READ,
            allowlist=AllowlistSettings(paths=(source,)),
            fingerprints=FileFingerprintCache(),
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.trusted)
        self.assertEqual(result.strength, 0.0)


if __name__ == "__main__":
    unittest.main()
