"""Synthetic integration only: does not assert Windows runtime detection."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from agent.main import watch, main

BASE = 0xfffff80000000000

class KernelOnlyTests(unittest.TestCase):
    def args(self, path, **kw):
        d = dict(kernel_only=True, pid=None, mode='observe', expected_image=None,
                 config=str(path/'policy.json'), out=str(path/'run'), session_id='synthetic',
                 player_id='test', thread_interval=15, interval=2, code_per_cycle=1,
                 verify_signatures=False, no_callback_health=False)
        d.update(kw)
        return argparse.Namespace(**d)

    def test_three_kernel_cycles_without_game_or_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'policy.json').write_text('{}')
            api, driver, worker = Mock(), Mock(), Mock()
            api.process.side_effect = AssertionError('must not open a game')
            api.wait.side_effect = AssertionError('must not wait on a game')
            api.enable_debug_privilege.return_value = True
            api.drivers.return_value = [{'base':BASE}]
            driver.status.return_value = dict(capabilities=63, pid=0, mode=0,
                create_time=0, lease_active=0, generation=1)
            driver.events.side_effect = [([],0), ([
                dict(type='process_create', flags=0, path='ignored.exe'),
                dict(type='kernel_image', flags=0, path='sample.sys')],0), ([],0), ([],0)]
            driver.diagnostics.return_value = dict(type='kernel_diagnostics', module_status=0,
                valid=15, changed=0, failed=0, dispatch=0, dispatch_pointers=[],
                modules=[dict(base=BASE,size=4096,path='normal.sys')])
            driver.thread_scan.return_value = dict(type='kernel_thread_snapshot', status=0,
                flags=0, lookup_failed=0, os_build=19045, duration_ms=2,
                threads=[dict(pid=4,tid=100,create_time=200,start=BASE+4096,
                              snapshot_start=BASE,status=0,flags=1)])
            driver.code_scan.return_value = dict(type='kernel_code_scan',status=0,flags=1)
            worker.results = queue.Queue(); worker.skipped = 0; worker.close.return_value = 0
            clock = [0,0]
            def sleep(_):
                clock[0] += 15; clock[1] += 1
                if clock[1] == 3: raise KeyboardInterrupt
            module = types.SimpleNamespace(Windows=Mock(return_value=api),Driver=Mock(return_value=driver))
            with patch.dict(sys.modules, {'agent.windows': module}), \
                 patch('agent.main.FileWorker', return_value=worker), \
                 patch('agent.main.time.monotonic', side_effect=lambda:clock[0]), \
                 patch('agent.main.time.sleep', side_effect=sleep), \
                 contextlib.redirect_stdout(io.StringIO()):
                watch(self.args(root))
            events = [json.loads(x) for x in (root/'run/raw_events.jsonl').read_text().splitlines()]
            common = [json.loads(x) for x in (root/'run/common_events.jsonl').read_text().splitlines()]
            self.assertEqual(events[0]['scope'], 'kernel_only')
            self.assertFalse(events[0]['callback_health_enabled'])
            self.assertEqual(events[-1]['excluded_user_events'], 1)
            self.assertEqual(driver.thread_scan.call_count,3)
            self.assertEqual(driver.code_scan.call_count,3)
            self.assertNotIn('process_create',[e['type'] for e in events])
            self.assertNotIn('callback_health',[e['type'] for e in events])
            self.assertEqual(common[0]['reasons'],['system_thread_start_outside_listed_modules_persistent'])
            api.process.assert_not_called(); api.wait.assert_not_called(); api.close.assert_not_called()
            driver.call.assert_not_called(); driver.probe_callback.assert_not_called()
            self.assertTrue(all(not c.args for c in driver.policy.call_args_list))
            driver.close.assert_called_once()

    def test_game_mode_still_requires_live_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'policy.json').write_text('{}')
            api = Mock(); api.process.return_value = (42,123,'game.exe'); api.wait.return_value = 0
            module = types.SimpleNamespace(Windows=Mock(return_value=api),Driver=Mock())
            with patch.dict(sys.modules, {'agent.windows':module}):
                with self.assertRaisesRegex(RuntimeError,'target is not running'):
                    watch(self.args(root,kernel_only=False,pid=99))
            api.process.assert_called_once_with(99)
            api.close.assert_called_once_with(42)
            module.Driver.assert_not_called()

    def test_kernel_only_rejects_enforce(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'policy.json').write_text('{}')
            api = Mock()
            module = types.SimpleNamespace(Windows=Mock(return_value=api),Driver=Mock())
            with patch.dict(sys.modules, {'agent.windows':module}):
                with self.assertRaisesRegex(ValueError,'supports observe'):
                    watch(self.args(root,mode='enforce'))
            api.process.assert_not_called(); module.Driver.assert_not_called()

    def test_cli_rejects_both_scopes(self):
        with patch.object(sys,'argv',['agent','watch','--kernel-only','--pid','42',
                                      '--out','unused','--session-id','test']), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught: main()
        self.assertEqual(caught.exception.code,2)

if __name__ == '__main__': unittest.main()
