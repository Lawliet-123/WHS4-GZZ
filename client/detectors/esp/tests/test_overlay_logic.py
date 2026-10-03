from __future__ import annotations

import unittest

from anti_esp.overlay import (
    OverlayMonitor,
    assess_overlay_window,
    intersect_rectangles,
)
from anti_esp.windows_api import (
    Rect,
    WS_EX_LAYERED,
    WS_EX_TOPMOST,
    WS_EX_TRANSPARENT,
    WindowInfo,
)


def window(
    hwnd: int,
    pid: int,
    rect: Rect,
    *,
    styles: int = 0,
    visible: bool = True,
    path: str | None = None,
) -> WindowInfo:
    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        title=f"window-{hwnd}",
        class_name="TestWindow",
        rect=rect,
        ex_style=styles,
        visible=visible,
        process_path=path,
    )


class OverlayLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = window(100, 10, Rect(0, 0, 1920, 1080))
        self.overlay_styles = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST

    def test_rectangle_intersection(self) -> None:
        intersection = intersect_rectangles(Rect(0, 0, 100, 100), Rect(50, 25, 150, 75))
        self.assertEqual(intersection, Rect(50, 25, 100, 75))
        self.assertIsNone(
            intersect_rectangles(Rect(0, 0, 10, 10), Rect(10, 0, 20, 10))
        )

    def test_full_external_layered_transparent_topmost_window_is_suspicious(self) -> None:
        candidate = window(
            200,
            20,
            Rect(0, 0, 1920, 1080),
            styles=self.overlay_styles,
        )
        result = assess_overlay_window(self.game, candidate)

        self.assertTrue(result.suspicious)
        self.assertEqual(result.game_overlap_ratio, 1.0)
        self.assertEqual(
            set(result.style_labels), {"LAYERED", "TRANSPARENT", "TOPMOST"}
        )
        self.assertGreater(result.strength, 0.9)

    def test_topmost_alone_is_not_enough(self) -> None:
        candidate = window(
            200, 20, Rect(0, 0, 1920, 1080), styles=WS_EX_TOPMOST
        )
        result = assess_overlay_window(self.game, candidate)
        self.assertFalse(result.suspicious)
        self.assertIn("style", result.reason)

    def test_overlap_threshold_is_measured_against_game(self) -> None:
        candidate = window(
            200,
            20,
            Rect(0, 0, 960, 1080),
            styles=self.overlay_styles,
        )
        self.assertFalse(
            assess_overlay_window(
                self.game, candidate, minimum_overlap_ratio=0.55
            ).suspicious
        )
        self.assertTrue(
            assess_overlay_window(
                self.game, candidate, minimum_overlap_ratio=0.50
            ).suspicious
        )

    def test_game_same_pid_system_pid_and_explicit_pid_are_excluded(self) -> None:
        same_process = window(
            200, 10, Rect(0, 0, 1920, 1080), styles=self.overlay_styles
        )
        system = window(201, 4, Rect(0, 0, 1920, 1080), styles=self.overlay_styles)
        excluded = window(202, 88, Rect(0, 0, 1920, 1080), styles=self.overlay_styles)

        self.assertFalse(assess_overlay_window(self.game, same_process).suspicious)
        self.assertFalse(assess_overlay_window(self.game, system).suspicious)
        self.assertFalse(
            assess_overlay_window(self.game, excluded, excluded_pids={88}).suspicious
        )

    def test_monitor_applies_per_window_cooldown(self) -> None:
        monotonic_now = [0.0]
        wall_now = [1_800_000_000.0]
        candidate = window(
            200,
            20,
            Rect(0, 0, 1920, 1080),
            styles=self.overlay_styles,
            path=r"C:\Tools\overlay.exe",
        )
        monitor = OverlayMonitor(
            cooldown_seconds=10.0,
            self_pid=999,
            monotonic_clock=lambda: monotonic_now[0],
            wall_clock=lambda: wall_now[0],
        )

        first = monitor.scan_windows(10, [self.game, candidate], session_id="round")
        monotonic_now[0] = 5.0
        suppressed = monitor.scan_windows(10, [self.game, candidate])
        monotonic_now[0] = 10.0
        repeated = monitor.scan_windows(10, [self.game, candidate])

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].category, "overlay")
        self.assertEqual(first[0].session_id, "round")
        self.assertEqual(first[0].details["window_pid"], 20)
        self.assertEqual(suppressed, [])
        self.assertEqual(len(repeated), 1)

    def test_monitor_excludes_its_own_pid(self) -> None:
        own_window = window(
            200, 999, Rect(0, 0, 1920, 1080), styles=self.overlay_styles
        )
        monitor = OverlayMonitor(self_pid=999)
        self.assertEqual(monitor.scan_windows(10, [self.game, own_window]), [])


if __name__ == "__main__":
    unittest.main()
