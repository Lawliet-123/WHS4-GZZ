import unittest
from pathlib import Path

from client.LocalGuard.external_access.common.models import TargetProcess
from client.LocalGuard.external_access.process_access.access_rights import (
    PROCESS_VM_READ,
    PROCESS_VM_WRITE,
)
from client.LocalGuard.external_access.process_access.handle_sensor import (
    ExternalHandleSensor,
    SystemHandleEntry,
)


class ExternalHandleSensorTests(unittest.TestCase):
    def test_collects_only_other_processes_that_point_to_the_game_object(self):
        game = TargetProcess(500, "game.exe", Path("C:/game.exe"), 1.0)
        entries = [
            # LocalGuard가 게임 object 주소를 알아내기 위해 연 query-only handle.
            SystemHandleEntry(99, 0x40, 0xABC, 0x1000),
            # 외부 python.exe가 게임에 read + write 권한을 열었다.
            SystemHandleEntry(700, 0x80, 0xABC, PROCESS_VM_READ),
            SystemHandleEntry(700, 0x88, 0xABC, PROCESS_VM_WRITE),
            # 게임 자신과 무관한 object는 무시한다.
            SystemHandleEntry(800, 0x90, 0xDEF, PROCESS_VM_WRITE),
            # 게임 프로세스 자신이 연 handle은 외부 접근이 아니므로 무시한다.
            SystemHandleEntry(500, 0x20, 0xABC, PROCESS_VM_WRITE),
        ]
        sensor = ExternalHandleSensor(
            entry_provider=lambda: entries,
            process_describer=lambda pid: TargetProcess(pid, "python.exe", Path("C:/Python/python.exe"), 2.0),
            current_pid_provider=lambda: 99,
            target_identity_resolver=lambda *_: type("Identity", (), {"object_address": 0xABC, "object_type_index": 0})(),
        )

        observations = sensor.scan(game)

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].source_pid, 700)
        self.assertEqual(observations[0].source_name, "python.exe")
        self.assertEqual(observations[0].granted_access, PROCESS_VM_READ | PROCESS_VM_WRITE)

    def test_uses_pid_confirmation_when_windows_hides_object_addresses(self):
        game = TargetProcess(500, "game.exe", Path("C:/game.exe"), 1.0)
        entries = [
            # Object 주소가 0이어도 우리 handle의 process type index는 얻을 수 있다.
            SystemHandleEntry(99, 0x40, 0, 0x1000, object_type_index=7),
            SystemHandleEntry(700, 0x80, 0, PROCESS_VM_WRITE, object_type_index=7),
            SystemHandleEntry(800, 0x88, 0, PROCESS_VM_WRITE, object_type_index=7),
        ]
        sensor = ExternalHandleSensor(
            entry_provider=lambda: entries,
            process_describer=lambda pid: TargetProcess(pid, f"pid_{pid}", None, 2.0),
            current_pid_provider=lambda: 99,
            target_identity_resolver=lambda *_: type("Identity", (), {"object_address": None, "object_type_index": 7})(),
            handle_target_resolver=lambda entry, target_pid: entry.handle_value == 0x80 and target_pid == 500,
        )

        observations = sensor.scan(game)

        self.assertEqual([item.source_pid for item in observations], [700])

    def test_ignores_kernel_system_process_handles(self):
        game = TargetProcess(500, "game.exe", Path("C:/game.exe"), 1.0)
        entries = [
            SystemHandleEntry(4, 0x10, 0xABC, PROCESS_VM_WRITE),
            SystemHandleEntry(700, 0x20, 0xABC, PROCESS_VM_WRITE),
        ]
        sensor = ExternalHandleSensor(
            entry_provider=lambda: entries,
            process_describer=lambda pid: TargetProcess(pid, f"pid_{pid}", None, 2.0),
            current_pid_provider=lambda: 99,
            target_identity_resolver=lambda *_: type("Identity", (), {"object_address": 0xABC, "object_type_index": 0})(),
        )

        observations = sensor.scan(game)

        self.assertEqual([item.source_pid for item in observations], [700])


if __name__ == "__main__":
    unittest.main()
