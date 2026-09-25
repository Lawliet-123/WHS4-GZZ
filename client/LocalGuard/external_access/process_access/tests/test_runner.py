import unittest
from pathlib import Path

from client.LocalGuard.external_access.common import ArtifactCache, ArtifactInspector
from client.LocalGuard.external_access.common.models import TargetProcess
from client.LocalGuard.external_access.process_access.access_rights import PROCESS_VM_WRITE
from client.LocalGuard.external_access.process_access.allowlist import (
    ProcessAllowlist,
    ProcessAllowlistEntry,
)
from client.LocalGuard.external_access.process_access.models import ExternalHandleObservation
from client.LocalGuard.external_access.process_access.runner import ProcessAccessRunner


class _FixedLocator:
    def __init__(self, game):
        self._game = game

    def find(self):
        return self._game


class _FixedSensor:
    def __init__(self, observations):
        self._observations = observations

    def scan(self, _game):
        return self._observations


class ProcessAccessRunnerTests(unittest.TestCase):
    def test_writes_one_result_for_a_risky_observation(self):
        saved = []
        game = TargetProcess(500, "game.exe", Path("C:/game.exe"), 1.0)
        observation = ExternalHandleObservation(700, "tool.exe", None, PROCESS_VM_WRITE)
        clock_values = iter([10.0, 10.0, 10.010, 10.020, 10.030])
        runner = ProcessAccessRunner(
            game_executable_name="game.exe",
            session_id="round_12",
            player_id="player_042",
            output_path=Path("ignored.jsonl"),
            locator=_FixedLocator(game),
            sensor=_FixedSensor([observation]),
            artifact_cache=ArtifactCache(ArtifactInspector(lambda _: ("trusted", "Test"))),
            writer=lambda _path, result: saved.append(result),
            clock=lambda: next(clock_values),
        )

        report = runner.scan_once()

        self.assertTrue(report.game_found)
        self.assertEqual(report.observed_processes, 1)
        self.assertEqual(report.allowed_processes, 0)
        self.assertEqual(report.emitted_detections, 1)
        self.assertEqual(saved[0]["raw_score"], 2)
        self.assertIn("scan_duration_ms", saved[0]["evidence"])

    def test_game_not_running_does_not_write_a_result(self):
        saved = []
        runner = ProcessAccessRunner(
            game_executable_name="game.exe",
            session_id="round_12",
            player_id="player_042",
            output_path=Path("ignored.jsonl"),
            locator=_FixedLocator(None),
            sensor=_FixedSensor([]),
            writer=lambda _path, result: saved.append(result),
        )

        report = runner.scan_once()

        self.assertFalse(report.game_found)
        self.assertEqual(saved, [])

    def test_exact_name_and_hash_allowlist_suppresses_a_reviewed_process(self):
        saved = []
        game = TargetProcess(500, "game.exe", Path("C:/game.exe"), 1.0)
        fixture = Path(__file__)
        import hashlib

        fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
        observation = ExternalHandleObservation(700, fixture.name, fixture, PROCESS_VM_WRITE)
        runner = ProcessAccessRunner(
            game_executable_name="game.exe",
            session_id="normal_001",
            player_id="player_042",
            output_path=Path("ignored.jsonl"),
            locator=_FixedLocator(game),
            sensor=_FixedSensor([observation]),
            allowlist=ProcessAllowlist([ProcessAllowlistEntry(fixture.name, fixture_hash)]),
            writer=lambda _path, result: saved.append(result),
        )

        report = runner.scan_once()

        self.assertEqual(report.allowed_processes, 1)
        self.assertEqual(report.emitted_detections, 0)
        self.assertEqual(saved, [])


if __name__ == "__main__":
    unittest.main()
