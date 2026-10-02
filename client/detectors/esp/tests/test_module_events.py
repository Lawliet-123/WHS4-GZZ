import unittest

from anti_esp.core.context import ProcessTarget, SensorContext
from anti_esp.detectors.esp_detector import EspEventDetector
from anti_esp.sensors.module_events import LoadedModuleSensor
from anti_esp.sensors.module_sensor import ModuleInfo, ModuleSnapshot


class FakeSnapshots:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def capture(self, pid):
        value = self.snapshots.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class LoadedModuleSensorTests(unittest.TestCase):
    def setUp(self):
        self.target = ProcessTarget(77, r"C:\Game\game.exe", 100.0)
        self.context = SensorContext(
            "esp_001", (self.target,), session_started_at=100.0, observed_at=101.0
        )
        self.base = ModuleInfo("game.exe", r"C:\Game\game.exe", 0x1000, 50)
        self.dll = ModuleInfo("extra.dll", r"C:\Temp\extra.dll", 0x2000, 25)

    def test_first_snapshot_emits_factual_baseline_presence_without_scoring(self):
        sensor = LoadedModuleSensor(
            snapshot_sensor=FakeSnapshots(
                [ModuleSnapshot(77, 101.0, (self.base, self.dll))]
            )
        )
        batch = sensor.poll(self.context)
        self.assertEqual(batch.status, "online")
        self.assertEqual(len(batch.events), 2)
        self.assertTrue(
            all(event.event_type == "module_present" for event in batch.events)
        )
        self.assertTrue(
            all(event.payload["baseline_created"] for event in batch.events)
        )
        detector = EspEventDetector()
        self.assertEqual(detector.detect_many(batch.events), ())
        self.assertEqual(batch.details["baseline_created"], 1)

    def test_baseline_modules_are_identity_checked_and_trust_fact_is_emitted(self):
        inspected = []

        def identity(path):
            inspected.append(path)
            return {
                "sha256": "a" * 64,
                "signature_status": "trusted",
                "signature_native_code": 0,
            }

        sensor = LoadedModuleSensor(
            snapshot_sensor=FakeSnapshots(
                [ModuleSnapshot(77, 101.0, (self.base, self.dll))]
            ),
            identity_provider=identity,
        )
        batch = sensor.poll(self.context)

        self.assertCountEqual(
            inspected,
            [r"C:\Game\game.exe", r"C:\Temp\extra.dll"],
        )
        self.assertEqual(
            [event.event_type for event in batch.events],
            ["module_present", "module_trust", "module_present", "module_trust"],
        )
        self.assertEqual(EspEventDetector().detect_many(batch.events), ())

    def test_later_addition_is_factual_and_enriched(self):
        sensor = LoadedModuleSensor(
            snapshot_sensor=FakeSnapshots(
                [
                    ModuleSnapshot(77, 101.0, (self.base,)),
                    ModuleSnapshot(77, 102.0, (self.base, self.dll)),
                ]
            ),
            identity_provider=lambda path: {
                "sha256": "a" * 64,
                "signature_status": "unsigned",
            },
        )
        sensor.poll(self.context)
        batch = sensor.poll(self.context)
        self.assertEqual(len(batch.events), 2)
        event = batch.events[0]
        self.assertEqual(event.event_type, "module_added")
        self.assertEqual(event.payload["module_path"], r"C:\Temp\extra.dll")
        self.assertEqual(event.payload["signature_status"], "unsigned")
        self.assertEqual(batch.events[1].event_type, "module_trust")
        self.assertNotIn("suspicious", event.payload)

    def test_failure_is_not_reported_as_empty_healthy_snapshot(self):
        sensor = LoadedModuleSensor(
            snapshot_sensor=FakeSnapshots([OSError("access denied")])
        )
        batch = sensor.poll(self.context)
        self.assertEqual(batch.status, "error")
        self.assertIn("access denied", batch.details["errors"][0]["error"])

    def test_retired_pid_baseline_is_forgotten(self):
        sensor = LoadedModuleSensor(
            snapshot_sensor=FakeSnapshots(
                [
                    ModuleSnapshot(77, 101.0, (self.base,)),
                    ModuleSnapshot(77, 103.0, (self.base, self.dll)),
                ]
            )
        )
        sensor.poll(self.context)
        sensor.poll(SensorContext("esp_001", observed_at=102.0))
        batch = sensor.poll(self.context)
        self.assertEqual(
            [event.event_type for event in batch.events],
            ["module_present", "module_present"],
        )
        self.assertEqual(batch.details["baseline_created"], 1)

    def test_reused_pid_with_new_creation_time_gets_new_baseline(self):
        sensor = LoadedModuleSensor(
            snapshot_sensor=FakeSnapshots(
                [
                    ModuleSnapshot(77, 101.0, (self.base,)),
                    ModuleSnapshot(77, 201.0, (self.base, self.dll)),
                ]
            )
        )
        sensor.poll(self.context)
        restarted = SensorContext(
            "esp_001",
            (ProcessTarget(77, r"C:\Game\game.exe", 200.0),),
            session_started_at=100.0,
            observed_at=201.0,
        )
        batch = sensor.poll(restarted)
        self.assertEqual(
            [event.event_type for event in batch.events],
            ["module_present", "module_present"],
        )
        self.assertEqual(batch.details["baseline_created"], 1)


if __name__ == "__main__":
    unittest.main()
