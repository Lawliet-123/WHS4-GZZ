import unittest

from anti_esp.core.context import ProcessTarget, SensorContext
from anti_esp.detectors.esp_detector import EspEventDetector
from anti_esp.sensors.window_overlap import WindowOverlapSensor
from anti_esp.windows_api import (
    Rect,
    WS_EX_LAYERED,
    WS_EX_TOPMOST,
    WS_EX_TRANSPARENT,
    WindowInfo,
)


def window(hwnd, pid, rect, style=0, visible=True):
    return WindowInfo(hwnd, pid, "title", "class", rect, style, visible, None)


class WindowOverlapSensorTests(unittest.TestCase):
    def setUp(self):
        self.context = SensorContext(
            "esp_001",
            (ProcessTarget(77, r"C:\Game\game.exe", 100.0),),
            session_started_at=100.0,
            observed_at=101.0,
        )

    def test_sensor_emits_geometry_and_detector_interprets_it(self):
        style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
        windows = (
            window(1, 77, Rect(0, 0, 1000, 1000)),
            window(2, 88, Rect(0, 0, 1000, 1000), style),
        )
        sensor = WindowOverlapSensor(
            window_provider=lambda: windows,
            self_pid=999,
            clock=lambda: 101.0,
            monotonic_clock=lambda: 5.0,
        )
        batch = sensor.poll(self.context)
        self.assertEqual(batch.status, "online")
        self.assertEqual(len(batch.events), 1)
        raw = batch.events[0]
        self.assertEqual(raw.payload["game_overlap_ratio"], 1.0)
        self.assertNotIn("suspicious", raw.payload)
        self.assertEqual(len(EspEventDetector().detect(raw)), 1)

    def test_ordinary_overlap_is_recorded_but_not_classified(self):
        windows = (
            window(1, 77, Rect(0, 0, 1000, 1000)),
            window(2, 88, Rect(0, 0, 100, 100), WS_EX_TOPMOST),
        )
        sensor = WindowOverlapSensor(
            window_provider=lambda: windows, self_pid=999, cooldown_seconds=0
        )
        raw = sensor.poll(self.context).events[0]
        self.assertEqual(EspEventDetector().detect(raw), ())

    def test_no_visible_game_window_is_waiting_not_online(self):
        sensor = WindowOverlapSensor(window_provider=lambda: (), self_pid=999)
        batch = sensor.poll(self.context)
        self.assertEqual(batch.status, "waiting")
        self.assertFalse(batch.details["game_window_found"])


if __name__ == "__main__":
    unittest.main()
