import copy
import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'KernelSentinel'))
from agent.analysis import findings, CrossView
from agent.sensors import ThreadOrigins, CallbackHealth
from agent.files import FileWorker
from validation.checks import check_handle


class HandleVerdictTests(unittest.TestCase):
    def setUp(self):
        self.truth = dict(opened=True, error=0, query_status=0, granted=0x1010,
                          thread=False, duplicate=False, actor_pid=200, target_pid=300,
                          tid=0, requested=0x1038, begin=100, end=200)
        self.pre = dict(type='handle_pre', actor_pid=200, target_pid=300, generation=7,
                        flags=36, thread_id=0, timestamp_100ns=130, original_access=0x1038,
                        source_pid=200, recipient_pid=200, operation_id=42,
                        before_access=0x1038, after_access=0x1010)
        self.post = {**self.pre, 'type': 'handle_post', 'timestamp_100ns': 140,
                     'granted_access': 0x1010, 'status': 0}

    def check(self, events=None, mode='enforce', lost=0):
        return check_handle(self.truth, [self.pre, self.post] if events is None else events, mode, 7, lost)[0]

    def test_correct_strip_and_read_preservation(self):
        self.assertEqual(self.check(), 'PASS')

    def test_empty_and_unpaired_evidence_never_pass(self):
        self.assertEqual(self.check([]), 'FAIL')
        self.assertEqual(self.check([self.pre]), 'FAIL')
        self.post['operation_id'] += 1
        self.assertEqual(self.check(), 'FAIL')

    def test_reused_pid_old_generation_and_stale_events_rejected(self):
        for key, value in [('generation', 6), ('timestamp_100ns', 99), ('actor_pid', 999),
                           ('recipient_pid', 999), ('thread_id', 999), ('flags', 36 | 128)]:
            events = [dict(self.pre), dict(self.post)]
            for e in events: e[key] = value
            self.assertNotEqual(self.check(events), 'PASS', key)

    def test_wrong_actual_granted_rights_fail(self):
        self.truth['granted'] |= 0x20
        self.assertEqual(self.check(), 'FAIL')
        self.post['granted_access'] = self.truth['granted']
        self.assertEqual(self.check(), 'FAIL')

    def test_lost_or_no_post_context_is_inconclusive(self):
        self.assertEqual(self.check(lost=1), 'INCONCLUSIVE')
        self.pre['flags'] |= 64
        self.assertEqual(self.check(), 'INCONCLUSIVE')

    def test_denied_api_and_failed_query_do_not_prove_filtering(self):
        self.truth['opened'] = False
        self.assertEqual(self.check(), 'INCONCLUSIVE')
        self.truth['opened'] = True
        self.truth['query_status'] = 0xc0000022
        self.assertEqual(self.check(), 'INCONCLUSIVE')

    def test_inactive_lease_not_enforcement_success(self):
        self.pre['flags'] &= ~32
        self.assertEqual(self.check(), 'INCONCLUSIVE')

    def test_observe_does_not_accept_stripping(self):
        self.assertEqual(self.check(mode='observe'), 'FAIL')
        for e in (self.pre, self.post):
            e.update(flags=0, after_access=0x1038)
        self.truth['granted'] = self.post['granted_access'] = 0x1038
        self.assertEqual(self.check(mode='observe'), 'PASS')

    def test_duplicate_thread_requires_both_flags_and_tid(self):
        self.truth.update(thread=True, duplicate=True, tid=123, requested=0x81b, granted=0x808)
        for e in (self.pre, self.post):
            e.update(flags=36 | 8 | 128, thread_id=123, original_access=0x81b,
                     before_access=0x81b, after_access=0x808, granted_access=0x808)
        self.assertEqual(self.check(), 'PASS')
        self.pre['flags'] &= ~128
        self.assertEqual(self.check(), 'FAIL')


class SignatureRegressionTests(unittest.TestCase):
    def test_inherited_powershell7_modules_and_localized_response(self):
        message = {'Status': 2, 'StatusMessage': '서명되지 않은 테스트 파일'}
        response = base64.b64encode(json.dumps(message, ensure_ascii=False).encode('utf-8')).decode('ascii')
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / '서명 검사.dll'
            target.write_bytes(b'test-file')
            with patch.dict(os.environ, {'PSModulePath': 'C:/PowerShell/7/Modules'}), patch(
                    'agent.files.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=response)) as run:
                worker = FileWorker(SimpleNamespace(local_path=lambda p: p), signatures=True)
                try:
                    worker.submit(dict(type='game_image', path=str(target)))
                    result = worker.results.get(timeout=5)
                finally:
                    worker.close()
                self.assertEqual(result['signature'], message)
                self.assertEqual(result['sha256'], hashlib.sha256(b'test-file').hexdigest())
                args, kwargs = run.call_args
                self.assertEqual(kwargs['env']['PSModulePath'], str(Path(args[0][0]).parent / 'Modules'))
                self.assertEqual(os.environ['PSModulePath'], 'C:/PowerShell/7/Modules')
                self.assertEqual(json.loads(kwargs['input']), str(target))
                self.assertNotIn(str(target), args[0][-1])


class FeatureNegativeControls(unittest.TestCase):
    def test_dispatch_range_end_is_exclusive(self):
        event = dict(type='kernel_diagnostics', valid=15, changed=0, failed=0, dispatch=0,
                     module_status=0, modules=[dict(base=1000, size=100)],
                     dispatch_pointers=[dict(current=1000), dict(current=1099), dict(current=1100)])
        self.assertEqual(findings(event, {}), [('sentinel_dispatch_outside_listed_modules:2', 2)])
        event['module_status'] = 0xc0000001
        self.assertEqual(findings(event, {}), [('kernel_diagnostics_incomplete', 0)])

    def test_each_entry_change_bit_has_a_finding(self):
        for bit in range(4):
            event = dict(type='kernel_diagnostics', valid=15, changed=1 << bit, failed=0,
                         dispatch=0, module_status=0, modules=[], dispatch_pointers=[])
            self.assertIn(('kernel_entry_bytes_changed_since_driver_load', 3), findings(event, {}))

    def test_unsigned_and_signed_files_are_not_cheat_verdicts(self):
        for status in range(7):
            event = dict(type='file_evidence', image_kind='game_image', sha256='a' * 64,
                         signature={'Status': status})
            self.assertEqual(findings(event, {}), [])

    def test_crossview_transient_and_persistent_both_directions(self):
        for direction in ('aux_only', 'psapi_only'):
            sensor = CrossView()
            d = dict(module_status=0, modules=[dict(base=100), dict(base=200)])
            u = [dict(base=100)] if direction == 'aux_only' else d['modules'] + [dict(base=300)]
            self.assertEqual(sensor.compare(d, u, d), [])
            self.assertEqual(sensor.compare(d, u, d), [])
            self.assertEqual(sensor.compare(d, u, d)[0]['direction'], direction)
            self.assertEqual(sensor.compare(d, d['modules'], d), [])
            self.assertEqual(sensor.compare(d, u, d), [])

    def test_health_failed_post_cannot_pass(self):
        sensor = CallbackHealth(10, 20)
        sensor.begin(0, 100, 7, True)
        event = dict(actor_pid=10, target_pid=20, generation=7, timestamp_100ns=101,
                     original_access=0x1000, operation_id=1, flags=0, status=0xc0000022)
        for kind in ('handle_pre', 'handle_post'):
            sensor.observe(dict(event, type=kind))
        self.assertEqual(sensor.finish(5)['outcome'], 'missing')


if __name__ == '__main__':
    unittest.main()
