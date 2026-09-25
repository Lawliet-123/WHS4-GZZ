import json
import tempfile
import unittest
from pathlib import Path

from client.LocalGuard.external_access.process_access.allowlist import ProcessAllowlist


class ProcessAllowlistTests(unittest.TestCase):
    def test_loads_and_matches_exact_name_and_sha256(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "allowlist.json"
            path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "executable_name": "normal.exe",
                                "sha256": "a" * 64,
                                "note": "reviewed",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            allowlist = ProcessAllowlist.from_json(path)

        self.assertIsNotNone(allowlist.find("NORMAL.EXE", "a" * 64))
        self.assertIsNone(allowlist.find("normal.exe", "b" * 64))

    def test_strict_system_entry_requires_path_signature_and_publisher(self):
        entry = {
            "entries": [
                {
                    "executable_name": "svchost.exe",
                    "sha256": "a" * 64,
                    "executable_path": "%SystemRoot%/System32/svchost.exe",
                    "signature_status": "trusted",
                    "publisher_contains": "O=Microsoft Corporation",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "allowlist.json"
            path.write_text(json.dumps(entry), encoding="utf-8")
            allowlist = ProcessAllowlist.from_json(path)

        trusted = allowlist.find(
            "svchost.exe",
            "a" * 64,
            executable_path=Path("C:/Windows/System32/svchost.exe"),
            signature_status="trusted",
            publisher="CN=Microsoft Windows, O=Microsoft Corporation, C=US",
        )
        wrong_path = allowlist.find(
            "svchost.exe",
            "a" * 64,
            executable_path=Path("C:/Temp/svchost.exe"),
            signature_status="trusted",
            publisher="CN=Microsoft Windows, O=Microsoft Corporation, C=US",
        )
        invalid_signature = allowlist.find(
            "svchost.exe",
            "a" * 64,
            executable_path=Path("C:/Windows/System32/svchost.exe"),
            signature_status="invalid",
            publisher="CN=Microsoft Windows, O=Microsoft Corporation, C=US",
        )

        self.assertIsNotNone(trusted)
        self.assertIsNone(wrong_path)
        self.assertIsNone(invalid_signature)


if __name__ == "__main__":
    unittest.main()
