import sys
import unittest
from pathlib import Path


LAUNCHER_DIR = Path(__file__).resolve().parents[1]
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import modules  # noqa: E402


class EspRegistrationTests(unittest.TestCase):
    def test_esp_detector_receives_common_session_clock_and_transport_mode(self):
        registered = modules.by_name()["esp"]
        argv = registered.resolved(
            {
                "session": "esp_001",
                "player": "player_042",
                "t0": "1000.250",
                "telemetry": "managed",
            }
        )

        self.assertEqual(registered.mode, modules.CONTINUOUS)
        self.assertFalse(registered.restart)
        self.assertIn("client/detectors/esp/run.py", argv)
        self.assertEqual(argv[argv.index("--session-id") + 1], "esp_001")
        self.assertEqual(argv[argv.index("--player-id") + 1], "player_042")
        self.assertEqual(argv[argv.index("--t0") + 1], "1000.250")
        self.assertEqual(argv[argv.index("--central-telemetry") + 1], "managed")


if __name__ == "__main__":
    unittest.main()
