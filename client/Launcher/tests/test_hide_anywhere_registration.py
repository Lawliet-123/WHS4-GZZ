import sys
import unittest
from pathlib import Path


LAUNCHER_DIR = Path(__file__).resolve().parents[1]
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import modules  # noqa: E402


class HideAnywhereRegistrationTests(unittest.TestCase):
    def test_receives_current_path_common_clock_and_transport_mode(self):
        registered = modules.by_name()["hide_anywhere"]
        argv = registered.resolved(
            {
                "session": "normal_001",
                "player": "player_042",
                "t0": "1000.250",
                "game_pid": 9876,
                "telemetry": "managed",
            }
        )

        self.assertIn("client/detectors/hide_anywhere/mecha_logger.py", argv)
        self.assertEqual(argv[argv.index("--pid") + 1], "9876")
        self.assertEqual(argv[argv.index("--session-id") + 1], "normal_001")
        self.assertEqual(argv[argv.index("--player-id") + 1], "player_042")
        self.assertEqual(argv[argv.index("--t0") + 1], "1000.250")
        self.assertEqual(
            argv[argv.index("--out") + 1],
            "client/detectors/hide_anywhere/logs",
        )
        self.assertNotIn("--local-only", argv)
        self.assertFalse(registered.restart)

    def test_offline_transport_uses_local_only(self):
        registered = modules.by_name()["hide_anywhere"]
        argv = registered.resolved(
            {
                "session": "normal_001",
                "player": "player_042",
                "t0": "1000.250",
                "game_pid": 9876,
                "telemetry": "off",
            }
        )

        self.assertIn("--local-only", argv)


if __name__ == "__main__":
    unittest.main()
