from __future__ import annotations

import unittest

from anti_esp.sensors.module_sensor import (
    ModuleChangeDetector,
    ModuleInfo,
    ModuleSnapshot,
    ToolhelpModuleSensor,
    canonical_module_path,
    diff_module_snapshots,
)


def module(
    path: str,
    *,
    base: int = 0x10000000,
    size: int = 0x1000,
) -> ModuleInfo:
    return ModuleInfo(
        name=path.replace("/", "\\").rsplit("\\", 1)[-1],
        path=path,
        base_address=base,
        image_size=size,
    )


def snapshot(pid: int, timestamp: float, *modules: ModuleInfo) -> ModuleSnapshot:
    return ModuleSnapshot(pid=pid, captured_at=timestamp, modules=tuple(modules))


class ModuleSensorTests(unittest.TestCase):
    def test_windows_paths_are_compared_case_insensitively(self) -> None:
        self.assertEqual(
            canonical_module_path(r"C:\Game\Binaries\..\Binaries\GAME.DLL"),
            canonical_module_path(r"c:/game/binaries/game.dll"),
        )
        self.assertEqual(
            canonical_module_path(r"\\?\C:\Game\game.dll"),
            canonical_module_path(r"c:\game\game.dll"),
        )

    def test_diff_classifies_added_removed_and_changed(self) -> None:
        game = module(r"C:\Game\game.exe", base=0x140000000, size=0x9000)
        removed = module(r"C:\Game\old.dll", base=0x20000000)
        moved_before = module(r"C:\Game\same.dll", base=0x30000000, size=0x1000)
        moved_after = module(r"c:\game\SAME.dll", base=0x31000000, size=0x2000)
        added = module(r"C:\Temp\new.dll", base=0x40000000)

        result = diff_module_snapshots(
            snapshot(123, 10.0, game, removed, moved_before),
            snapshot(123, 11.0, game, moved_after, added),
        )

        self.assertEqual(result.added, (added,))
        self.assertEqual(result.removed, (removed,))
        self.assertEqual(len(result.changed), 1)
        self.assertEqual(result.changed[0].before, moved_before)
        self.assertEqual(result.changed[0].after, moved_after)
        self.assertTrue(result.has_changes)
        self.assertFalse(result.baseline_created)

    def test_unchanged_snapshot_has_no_changes(self) -> None:
        first = snapshot(42, 1.0, module(r"C:\Game\game.exe"))
        second = snapshot(42, 2.0, module(r"c:\game\GAME.EXE"))
        result = diff_module_snapshots(first, second)
        self.assertFalse(result.has_changes)

    def test_different_pids_cannot_be_compared(self) -> None:
        with self.assertRaises(ValueError):
            diff_module_snapshots(snapshot(1, 1.0), snapshot(2, 2.0))

    def test_snapshot_rejects_duplicate_paths(self) -> None:
        with self.assertRaises(ValueError):
            snapshot(
                1,
                1.0,
                module(r"C:\Game\same.dll"),
                module(r"c:\game\SAME.DLL", base=0x20000000),
            )

    def test_sensor_uses_injected_provider_and_clock(self) -> None:
        calls: list[int] = []
        expected = module(r"C:\Game\game.exe")

        def provider(pid: int):
            calls.append(pid)
            return [expected]

        sensor = ToolhelpModuleSensor(
            module_provider=provider,
            clock=lambda: 1234.5,
        )
        result = sensor.capture(77)

        self.assertEqual(calls, [77])
        self.assertEqual(result.pid, 77)
        self.assertEqual(result.captured_at, 1234.5)
        self.assertEqual(result.modules, (expected,))

    def test_first_observation_creates_baseline_without_alerting_every_module(self) -> None:
        detector = ModuleChangeDetector()
        baseline = detector.observe(
            snapshot(77, 1.0, module(r"C:\Game\game.exe"))
        )

        self.assertTrue(baseline.baseline_created)
        self.assertFalse(baseline.has_changes)
        self.assertEqual(baseline.added, ())

    def test_later_observation_reports_only_new_path(self) -> None:
        detector = ModuleChangeDetector()
        game = module(r"C:\Game\game.exe")
        new_dll = module(r"C:\Temp\plugin.dll", base=0x50000000)
        detector.observe(snapshot(77, 1.0, game))

        result = detector.observe(snapshot(77, 2.0, game, new_dll))

        self.assertEqual(result.added, (new_dll,))
        self.assertFalse(result.baseline_created)

    def test_detector_keeps_independent_baselines_per_pid(self) -> None:
        detector = ModuleChangeDetector()
        self.assertTrue(detector.observe(snapshot(10, 1.0)).baseline_created)
        self.assertTrue(detector.observe(snapshot(20, 1.0)).baseline_created)
        self.assertFalse(detector.observe(snapshot(10, 2.0)).baseline_created)

    def test_reset_causes_next_observation_to_be_a_new_baseline(self) -> None:
        detector = ModuleChangeDetector()
        detector.observe(snapshot(10, 1.0))
        detector.reset(10)
        self.assertTrue(detector.observe(snapshot(10, 2.0)).baseline_created)

    def test_invalid_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            module(r"C:\Game\x.dll", base=-1)
        with self.assertRaises(ValueError):
            ToolhelpModuleSensor(module_provider=lambda _pid: ()).capture(0)


if __name__ == "__main__":
    unittest.main()
