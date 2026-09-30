import copy
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from agent import protocol as P
from agent.sensors import ThreadOrigins, CallbackHealth
from agent.analysis import findings
from agent.main import Log
from tools.summarize_run import summarize

BASE = 0xfffff80000000000

class ThreadTests(unittest.TestCase):
    def setUp(self):
        self.sensor = ThreadOrigins()
        self.modules = {'module_status': 0, 'modules': [{'base': BASE, 'size': 4096, 'path': 'test.sys'}]}
        self.thread = dict(pid=4, tid=100, flags=1, status=0, create_time=123, start=BASE+4096, snapshot_start=BASE)
        self.snapshot = dict(status=0, flags=0, os_build=19045, lookup_failed=0, duration_ms=12,
                             threads=[self.thread])

    def scan(self):
        return self.sensor.compare(self.modules, self.snapshot, self.modules)

    def alerts(self, events):
        return [f for e in events for f in findings(e, {}) if f[1] > 0]

    def test_persistent_outside_boundary(self):
        self.assertEqual(self.alerts(self.scan()), [])
        self.assertEqual(self.alerts(self.scan()), [])
        self.assertEqual(self.alerts(self.scan()), [('system_thread_start_outside_listed_modules_persistent', 3)])

    def test_inside_includes_first_and_last_byte(self):
        for address in (BASE, BASE+4095):
            self.thread['start'] = address
            for _ in range(4):
                self.assertEqual(self.alerts(self.scan()), [])
            self.assertFalse(self.sensor.streak)

    def test_reused_tid_does_not_reuse_streak(self):
        self.scan(); self.scan()
        self.thread['create_time'] += 1
        self.assertEqual(self.alerts(self.scan()), [])

    def test_failed_query_does_not_become_detection(self):
        self.scan(); self.scan()
        self.thread['status'] = 0xc0000022
        self.assertEqual(self.alerts(self.scan()), [])
        self.thread['status'] = 0
        self.assertEqual(self.alerts(self.scan()), [])

    def test_failed_or_truncated_snapshot_resets(self):
        for field, bad in [('status', 0xc00000bb), ('flags', 1)]:
            self.scan(); self.scan()
            self.snapshot[field] = bad
            self.assertEqual(self.alerts(self.scan()), [])
            self.snapshot[field] = 0
            self.assertEqual(self.alerts(self.scan()), [])

    def test_module_churn_is_inconclusive(self):
        self.scan(); self.scan()
        after = copy.deepcopy(self.modules)
        after['modules'][0]['size'] += 4096
        result = self.sensor.compare(self.modules, self.snapshot, after)
        self.assertEqual(result[0]['type'], 'coverage_gap')
        self.assertEqual(self.alerts(self.scan()), [])

    def test_missing_modules_and_zero_addresses(self):
        for modules in ([], [{'base': 0, 'size': 1, 'path': 'x'}]):
            self.modules['modules'] = modules
            for _ in range(4):
                self.assertEqual(self.alerts(self.scan()), [])

    def test_duplicate_thread_not_multiple_observations(self):
        self.snapshot['threads'] = [self.thread] * 10
        result = self.scan()
        self.assertEqual(len([e for e in result if e['type'] == 'kernel_thread_origin']), 1)
        self.assertEqual(self.alerts(result), [])

    def test_thread_disappears_resets(self):
        self.scan(); self.scan()
        self.snapshot['threads'] = []
        self.scan()
        self.snapshot['threads'] = [self.thread]
        self.assertEqual(self.alerts(self.scan()), [])

    def test_abi_and_invalid_lengths(self):
        data = bytearray(P.THREAD_SIZE)
        P.THREAD_HEADER.pack_into(data, 0, 1, 0, 1, 42, 0, 19045, 12, 0)
        P.THREAD_RECORD.pack_into(data, 32, 4, 100, 1, 0, 123, BASE+4096, BASE)
        self.assertEqual(P.parse_threads(data)['threads'][0], self.thread)
        for changed in (data[:-1], data+b'0'):
            with self.assertRaises(ValueError): P.parse_threads(changed)
        for offset, value in ((0, 2), (8, 4097), (12, 0)):
            bad = bytearray(data)
            struct.pack_into('<I', bad, offset, value)
            with self.assertRaises(ValueError): P.parse_threads(bad)

    def test_two_threads_not_rate_limited_as_one(self):
        self.scan(); self.scan()
        event = self.scan()[0]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)/'logs'
            log = Log(root, 'synthetic_test', 'local', {})
            log.write(dict(event)); log.write({**event, 'tid': 200}); log.close()
            self.assertEqual(len((root/'common_events.jsonl').read_text().splitlines()), 2)

class HealthTests(unittest.TestCase):
    def setUp(self):
        self.sensor = CallbackHealth(10, 20)

    def event(self, kind, **kw):
        return dict(type=kind, actor_pid=10, target_pid=20, generation=7, timestamp_100ns=101,
                    original_access=0x1000, operation_id=1, status=0, flags=0, **kw)

    def begin(self): self.sensor.begin(0, 100, 7, True)

    def test_pair_success(self):
        self.begin()
        self.sensor.observe(self.event('handle_pre'))
        self.sensor.observe(self.event('handle_post'))
        self.assertIsNone(self.sensor.finish(4.9))
        self.assertEqual(self.sensor.finish(5)['outcome'], 'observed')

    def test_missing_requires_three_checks(self):
        for i in range(3):
            self.begin()
            event = self.sensor.finish(5)
            self.assertEqual(bool(findings(event, {})), i == 2)

    def test_only_pre_or_wrong_operation_does_not_pass(self):
        self.begin()
        self.sensor.observe(self.event('handle_pre'))
        event = self.event('handle_post'); event['operation_id'] = 2
        self.sensor.observe(event)
        self.assertEqual(self.sensor.finish(5)['outcome'], 'missing')

    def test_stale_wrong_actor_or_kernel_does_not_pass(self):
        for key, value in [('timestamp_100ns', 99), ('actor_pid', 11), ('generation', 8), ('flags', 1)]:
            self.begin()
            for kind in ('handle_pre', 'handle_post'):
                event = self.event(kind); event[key] = value
                self.sensor.observe(event)
            self.assertEqual(self.sensor.finish(5)['outcome'], 'missing')

    def test_overflow_delay_backlog_or_failed_open_inconclusive(self):
        for case in ('lost', 'delay', 'backlog', 'open_failed'):
            self.sensor.begin(0, 100, 7, case != 'open_failed')
            if case == 'lost': self.sensor.invalidate()
            event = self.sensor.finish(16 if case == 'delay' else 5, case != 'backlog')
            self.assertEqual(event['outcome'], 'inconclusive')
            self.assertEqual(findings(event, {})[0][1], 0)

    def test_success_resets_missing_streak(self):
        self.begin(); self.sensor.finish(5)
        self.begin()
        self.sensor.observe(self.event('handle_pre')); self.sensor.observe(self.event('handle_post'))
        self.sensor.finish(5)
        self.begin()
        self.assertEqual(self.sensor.finish(5)['consecutive_misses'], 1)

class NativeTests(unittest.TestCase):
    def test_native_layout_bounds_and_cache_regression(self):
        compiler = shutil.which('gcc')
        if not compiler: self.skipTest('GCC not installed')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            for name in ('thread_layout_test', 'cache_test'):
                exe = Path(td)/(name + ('.exe' if os.name == 'nt' else ''))
                subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(root/'driver'),
                                str(root/'tests'/f'{name}.c'), '-o', str(exe)], check=True, capture_output=True)
                subprocess.run([str(exe)], check=True, capture_output=True)

    def test_kernel_collector_with_api_doubles(self):
        compiler = shutil.which('gcc')
        if not compiler: self.skipTest('GCC not installed')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td)/('scan.exe' if os.name == 'nt' else 'scan')
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-multichar',
                '-I', str(root/'driver'), str(root/'tests/thread_scan_test.c'), '-o', str(exe)],
                check=True, capture_output=True)
            subprocess.run([str(exe)], check=True, capture_output=True)

class SummaryTests(unittest.TestCase):
    def test_empty_does_not_claim_coverage(self):
        summary = summarize([])
        self.assertEqual(summary['thread_snapshots'], 0)
        self.assertIsNone(summary['cycle_ms_p95'])

    def test_failed_scan_separate_from_observed_callback(self):
        result = summarize(map(json.dumps, [dict(type='kernel_thread_snapshot', status=0xc00000bb),
            dict(type='callback_health', outcome='observed', consecutive_misses=0),
            dict(type='sensor_cycle', duration_ms=123)]))
        self.assertEqual(result['thread_scan_status'], {'0xC00000BB': 1})
        self.assertEqual(result['callback_health'], {'observed': 1})
        self.assertEqual(result['raw_repeated_findings'], {})
        self.assertEqual(result['cycle_ms_p95'], 123)

if __name__ == '__main__': unittest.main()
