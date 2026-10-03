import unittest
from session_clock import SessionClock


class ClockTests(unittest.TestCase):
    def test_delayed_processes_share_origin(self):
        t0 = '1800000000.125'
        epoch = 1800000000125000000
        mono = [100000000000]
        first = SessionClock(t0, wall_ns=lambda: epoch + 5_000_000_000,
                             mono_ns=lambda: mono[0])
        mono[0] += 8_000_000_000
        second = SessionClock(t0, wall_ns=lambda: epoch + 13_000_000_000,
                              mono_ns=lambda: mono[0])
        self.assertEqual(second.elapsed_ms(), 13000)
        mono[0] += 12_000_000_000
        self.assertEqual(first.elapsed_ms(), 25000)
        self.assertEqual(second.elapsed_ms(), 25000)
        self.assertEqual(first.evidence(), second.evidence())
        self.assertEqual(first.basis, 'launcher_session_start')

    def test_wall_clock_adjustments_do_not_move_elapsed(self):
        wall, mono = [10_000_000_000], [50_000_000_000]
        clock = SessionClock('9', wall_ns=lambda: wall[0], mono_ns=lambda: mono[0])
        wall[0] = 0
        mono[0] += 1_250_000_000
        self.assertEqual(clock.elapsed_ms(), 2250)

    def test_local_is_explicit(self):
        mono = [100]
        clock = SessionClock(wall_ns=lambda: 10_000_000_000, mono_ns=lambda: mono[0])
        self.assertEqual(clock.elapsed_ms(), 0)
        self.assertEqual(clock.basis, 'local_session_start')

    def test_invalid_t0_never_falls_back(self):
        for t0 in ('NaN', 'Infinity', '-1', '11', 'bad', '10000000000000'):
            with self.subTest(t0=t0), self.assertRaises(ValueError):
                SessionClock(t0, wall_ns=lambda: 10_000_000_000, mono_ns=lambda: 1)
