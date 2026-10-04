import json
import tempfile
import unittest
from pathlib import Path

from anti_esp.core.context import ProcessTarget, SensorContext
from anti_esp.detectors.esp_detector import EspEventDetector
from anti_esp.sensors.anticheat_registry import AntiCheatPidRegistry
from anti_esp.sensors.handle_sensor import (
    CurrentProcessHandleSensor,
    HandleInventorySnapshot,
    ProcessHandleRecord,
)
from anti_esp.sensors.process_access import SysmonProcessAccessSensor
from anti_esp.sysmon import SysmonPollResult, SysmonProcessAccess, SysmonStatus
from anti_esp.team_format import TeamEventAdapter


def _filetime(unix_seconds: float) -> int:
    return int(round((unix_seconds + 11_644_473_600.0) * 10_000_000.0))


def _write_hide_registry(path: Path, *, pid: int, created_at: float) -> None:
    path.write_text(
        json.dumps(
            {
                "entries": {
                    "hide_anywhere": {
                        "pid": pid,
                        "create_time": _filetime(created_at),
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _sysmon_record(source_pid: int) -> SysmonProcessAccess:
    return SysmonProcessAccess(
        record_id=42,
        event_id=10,
        timestamp=102.0,
        computer="TEST-PC",
        source_process_id=source_pid,
        source_thread_id=301,
        source_image=r"C:\Team\hide_anywhere.exe",
        source_user=r"TEST-PC\student",
        target_process_id=77,
        target_image=r"C:\Game\game.exe",
        target_user=r"TEST-PC\student",
        granted_access=0x10,
        granted_access_raw="0x10",
        call_trace="frame-a",
        rule_name="",
        data={},
    )


class _SysmonPoller:
    def __init__(self, source_pid: int) -> None:
        self.source_pid = source_pid

    def poll(self) -> SysmonPollResult:
        return SysmonPollResult(
            SysmonStatus(True, True, True, "ready", "ready"),
            (_sysmon_record(self.source_pid),),
            scanned_count=1,
        )


class _HandleProvider:
    def __init__(self, snapshots: list[HandleInventorySnapshot]) -> None:
        self.snapshots = list(snapshots)
        self.exclusions: list[frozenset[int]] = []

    def __call__(
        self,
        _target_pids: frozenset[int],
        excluded_owner_pids: frozenset[int],
    ) -> HandleInventorySnapshot:
        self.exclusions.append(excluded_owner_pids)
        return self.snapshots.pop(0)


def _handle_snapshot(*records: ProcessHandleRecord, observed_at: float) -> HandleInventorySnapshot:
    return HandleInventorySnapshot(
        records=tuple(records),
        observed_at=observed_at,
        total_handle_count=len(records),
        scanned_handle_count=len(records),
        candidate_handle_count=len(records),
        target_mapping_count=1,
    )


class LauncherRegistryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = SensorContext(
            "esp_registry_test",
            (ProcessTarget(77, r"C:\Game\game.exe", created_at=100.0),),
            session_started_at=100.0,
            observed_at=102.0,
        )

    def test_live_hide_anywhere_pid_is_excluded_from_both_access_sensors(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "anticheat_pids.json"
            _write_hide_registry(registry_path, pid=300, created_at=200.0)
            registry = AntiCheatPidRegistry(
                registry_path,
                creation_time_provider=lambda pid: 200.0 if pid == 300 else None,
            )

            sysmon = SysmonProcessAccessSensor(
                poller=_SysmonPoller(300),
                self_pid=999,
                clock=lambda: 102.0,
                excluded_pid_provider=registry.live_pids,
            )
            handle_provider = _HandleProvider(
                [
                    _handle_snapshot(
                        ProcessHandleRecord(300, 0x40, 77, 0x10, r"C:\Team\hide_anywhere.exe"),
                        observed_at=102.0,
                    )
                ]
            )
            handles = CurrentProcessHandleSensor(
                enumeration_provider=handle_provider,
                self_pid=999,
                excluded_pid_provider=registry.live_pids,
            )

            self.assertEqual(sysmon.poll(self.context).events, ())
            handle_batch = handles.poll(self.context)
            self.assertEqual(handle_batch.events, ())
            self.assertEqual(handle_batch.details["filtered_record_count"], 0)
            self.assertIn(300, handle_provider.exclusions[0])

    def test_reused_hide_pid_is_not_excluded_and_vm_read_still_reaches_team_event(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "anticheat_pids.json"
            _write_hide_registry(registry_path, pid=300, created_at=200.0)
            registry = AntiCheatPidRegistry(
                registry_path,
                creation_time_provider=lambda pid: 201.0 if pid == 300 else None,
            )
            self.assertEqual(registry.live_pids(), frozenset())

            sysmon = SysmonProcessAccessSensor(
                poller=_SysmonPoller(300),
                self_pid=999,
                clock=lambda: 102.0,
                excluded_pid_provider=registry.live_pids,
            )
            self.assertEqual(len(sysmon.poll(self.context).events), 1)

            record = ProcessHandleRecord(
                300,
                0x40,
                77,
                0x10,
                r"C:\Tools\unregistered_reader.exe",
            )
            handle_provider = _HandleProvider(
                [
                    _handle_snapshot(observed_at=101.0),
                    _handle_snapshot(record, observed_at=102.0),
                ]
            )
            handles = CurrentProcessHandleSensor(
                enumeration_provider=handle_provider,
                self_pid=999,
                excluded_pid_provider=registry.live_pids,
            )
            self.assertEqual(handles.poll(self.context).events, ())
            handle_events = handles.poll(self.context).events
            self.assertEqual(len(handle_events), 1)
            self.assertEqual(handle_events[0].payload["access_labels"], ["VM_READ"])

            evidence = EspEventDetector().detect(handle_events[0])
            team_event = TeamEventAdapter(
                session_started_at=100.0,
                player_id="player_042",
            ).convert(handle_events[0], evidence)
            self.assertIsNotNone(team_event)
            assert team_event is not None
            self.assertEqual(team_event.module, "esp")
            self.assertEqual(team_event.raw_score, 2)
            self.assertEqual(
                set(team_event.to_dict()),
                {
                    "session_id",
                    "player_id",
                    "module",
                    "timestamp_ms",
                    "evidence",
                    "reasons",
                    "raw_score",
                },
            )


if __name__ == "__main__":
    unittest.main()
