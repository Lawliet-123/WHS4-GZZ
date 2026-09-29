"""런처 세션 시각과 검사기 자체 실행 시간의 분리를 검증한다."""
import argparse
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from heartbeat import HeartbeatClient
from replay_events import ReplaySession, check_session_args, session_args


class LauncherTimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ticks = 500_000

    def clock(self):
        return self.ticks

    def test_launcher_t0_aligns_event_marker_and_heartbeat(self):
        wall_now = [1_000_012.5]
        session = ReplaySession(
            self.temp.name, 'shared_time', label='cheat', cheat_name='test_cheat',
            modules=['localguard_yara'], clock=self.clock,
            wall_clock=lambda: wall_now[0], t0_epoch=1_000_000.0)
        self.assertEqual(session.elapsed(), 12_500)
        self.assertEqual(session.run_elapsed(), 0)

        wall_now[0] -= 3_600  # 시작 후 시스템 시계를 바꿔도 세션 시간은 역행하지 않는다.
        self.ticks += 2_000
        event = session.emit('localguard_yara', 'local_player', {}, [], 0)
        self.assertEqual(event['timestamp_ms'], 14_500)
        self.assertEqual(session.mark('on'), 14_500)

        heartbeat = HeartbeatClient(
            session_id='shared_time', player_id='local_player',
            log_path=session.raw / 'heartbeat.jsonl',
            clock=session.clock, origin_ms=session.origin,
            utc_now=lambda: '2026-09-30T00:00:00+00:00')
        payload, delivered = heartbeat.emit_once()
        self.assertIsNone(delivered)
        self.assertEqual(payload['timestamp_ms'], event['timestamp_ms'])
        heartbeat.stop()

        self.ticks += 1_000
        self.assertEqual(session.mark('off'), 15_500)
        session.finish()
        manifest = json.loads((session.path / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['session_t0_epoch'], 1_000_000.0)
        self.assertEqual(manifest['module_start_offset_ms'], 12_500)
        self.assertEqual(manifest['duration_ms'], 15_500)
        self.assertEqual(session.run_elapsed(), 3_000)

    def test_standalone_time_still_starts_at_zero(self):
        session = ReplaySession(self.temp.name, 'standalone', clock=self.clock)
        self.assertEqual(session.elapsed(), session.run_elapsed())
        self.ticks += 500
        self.assertEqual(session.elapsed(), 500)
        self.assertEqual(session.run_elapsed(), 500)
        session.finish()

    def test_seconds_remains_module_runtime_when_t0_is_older(self):
        session = ReplaySession(
            self.temp.name, 'runtime_limit', clock=self.clock,
            wall_clock=lambda: 1_000_020.0, t0_epoch=1_000_000.0)
        self.assertGreaterEqual(session.elapsed(), 20_000)
        self.assertLess(session.run_elapsed(), 2_000)
        self.ticks += 2_000
        self.assertEqual(session.run_elapsed(), 2_000)
        session.finish()

    def test_bad_t0_is_rejected_before_creating_session_folder(self):
        for name, t0 in [('nan', float('nan')), ('future', 1_000_100.0),
                         ('old', 800_000.0), ('bool', True)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ReplaySession(self.temp.name, name, clock=self.clock,
                              wall_clock=lambda: 1_000_000.0, t0_epoch=t0)
            self.assertFalse((Path(self.temp.name) / name).exists())

    def test_cli_accepts_launcher_epoch_and_rejects_nonfinite_value(self):
        parser = argparse.ArgumentParser()
        session_args(parser)
        args = parser.parse_args(['--t0', '1000000.125'])
        check_session_args(parser, args)
        self.assertEqual(args.t0, 1_000_000.125)
        args.log_root = Path(self.temp.name)
        args.session_id = 'from_cli'
        session = ReplaySession.from_args(
            args, clock=self.clock, wall_clock=lambda: 1_000_003.125)
        self.assertEqual(session.elapsed(), 3_000)
        session.finish()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            check_session_args(parser, parser.parse_args(['--t0', 'nan']))


if __name__ == '__main__':
    unittest.main()
