import copy
import ctypes as C
import ntpath
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'KernelSentinel'))
from validation.driver_load import default_probe, assess_load, run_driver_load
from validation.probe_service import ProbeService
from validation import live


def resolve(path):
    return path[4:] if path.startswith('\\??\\') else path


class DriverLoadTests(unittest.TestCase):
    def setUp(self):
        self.path = r'C:\test\KsValidationProbe.sys'
        self.before = dict(module_status=0, modules=[])
        self.after = dict(module_status=0, modules=[dict(path='\\??\\'+self.path, base=0x8000, size=0x1000)])
        self.event = dict(type='kernel_image', path='\\??\\'+self.path, image_base=0x8000, image_size=0x1000, timestamp_100ns=101)

    def assess(self, events=None, lost=0):
        return assess_load([self.event] if events is None else events, self.before, self.after,
                           self.path, 100, lost, resolve)[0]

    def test_actual_event_matches_module(self):
        self.assertEqual(self.assess(), 'PASS')

    def test_no_event_duplicate_old_event_wrong_path_base_size_fail(self):
        self.assertEqual(self.assess([]), 'FAIL')
        self.assertEqual(self.assess([self.event]*2), 'FAIL')
        for key, value in [('timestamp_100ns',99), ('path',r'C:\other\KsValidationProbe.sys'),
                           ('image_base',0x9000), ('image_size',1)]:
            with self.subTest(key=key):
                self.assertEqual(self.assess([{**self.event,key:value}]), 'FAIL')

    def test_lost_or_unavailable_snapshot_is_inconclusive(self):
        self.assertEqual(self.assess(lost=1), 'INCONCLUSIVE')
        self.after['module_status'] = 1
        self.assertEqual(self.assess(), 'INCONCLUSIVE')

    def test_preexisting_fixture_not_new_load(self):
        self.before = copy.deepcopy(self.after)
        self.assertEqual(self.assess(), 'ERROR')

    def test_existing_binary_preferred_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(default_probe(root), root/'driver-load-fixture/KsValidationProbe.sys')
            old = root/'bin/driver/KsValidationProbe.sys'; old.parent.mkdir(parents=True); old.write_bytes(b'existing signed fixture')
            self.assertEqual(default_probe(root), old)
            self.assertEqual(old.read_bytes(), b'existing signed fixture')

    def test_standard_modes_enable_load_and_skip_is_explicit(self):
        for mode in ('observe','enforce'):
            with patch.object(sys,'argv',['live.py','--mode',mode,'--out','unused']), patch.object(live,'run',return_value=2) as run:
                self.assertEqual(live.main(), 2)
                args = run.call_args.args[0]
                self.assertFalse(args.skip_driver_load)
                self.assertIsNone(args.probe_sys)
        with patch.object(sys,'argv',['live.py','--mode','observe','--out','unused','--skip-driver-load']), patch.object(live,'run',return_value=2) as run:
            live.main(); self.assertTrue(run.call_args.args[0].skip_driver_load)

    def setup_flow(self, folder):
        path = Path(folder)/'KsValidationProbe.sys'; path.write_bytes(b'unit-test only')
        api, driver, session, report, fixture = Mock(), Mock(), Mock(), Mock(), Mock()
        api.local_path.side_effect = resolve
        session.lost = 0
        session.events = [{**self.event,'path':'\\??\\'+str(path)}]
        after = dict(module_status=0, modules=[dict(path='\\??\\'+str(path),base=0x8000,size=0x1000)])
        driver.diagnostics.side_effect = [self.before, after]
        driver.thread_scan.return_value = dict(status=0,flags=0,threads=[dict(tid=42,status=0,flags=1,start=0x8010)])
        fixture.start.return_value = 42
        factory = Mock(return_value=fixture)
        return path, api, driver, session, report, fixture, factory

    def test_full_flow_pass_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            p,a,d,s,r,f,factory = self.setup_flow(folder)
            run_driver_load(p,a,d,s,r,lambda:100,factory)
            results = [c.args for c in r.add.call_args_list]
            self.assertEqual(results[0][:2], ('05.driver_load','PASS'))
            self.assertEqual(results[1][:2], ('10.fixture_system_thread','PASS'))
            f.start.assert_called_once(); f.close.assert_called_once()

    def test_load_failure_is_named_error_and_still_cleans_up(self):
        with tempfile.TemporaryDirectory() as folder:
            p,a,d,s,r,f,factory = self.setup_flow(folder)
            f.start.side_effect = RuntimeError('signature rejected 577')
            run_driver_load(p,a,d,s,r,lambda:100,factory)
            self.assertEqual(r.add.call_args.args[:2], ('05.driver_load','ERROR'))
            self.assertIn('577', r.add.call_args.args[2])
            f.close.assert_called_once()

    def test_missing_binary_is_named_error_without_service(self):
        r, factory = Mock(), Mock()
        with tempfile.TemporaryDirectory() as folder:
            run_driver_load(Path(folder)/'missing.sys',Mock(),Mock(),Mock(),r,lambda:100,factory)
        self.assertEqual(r.add.call_args.args[:2], ('05.driver_load','ERROR'))
        factory.assert_not_called()

    def test_preexisting_driver_service_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            p,a,d,s,r,f,factory = self.setup_flow(folder)
            d.diagnostics.side_effect = [self.after]
            run_driver_load(p,a,d,s,r,lambda:100,factory)
            self.assertEqual(r.add.call_args.args[:2], ('05.driver_load','ERROR'))
            factory.assert_not_called()

    def test_cleanup_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            p,a,d,s,r,f,factory = self.setup_flow(folder)
            f.close.side_effect = RuntimeError('stop failed')
            run_driver_load(p,a,d,s,r,lambda:100,factory)
            self.assertEqual(r.add.call_args.args[:2], ('05.probe_cleanup','ERROR'))

    def test_service_signature_rejection_hint(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'KsValidationProbe.sys'; path.write_bytes(b'unit-test only')
            service = ProbeService(path, Mock(), Mock())
            service.verify_registered_path = Mock()
            service.command = Mock(side_effect=[SimpleNamespace(returncode=n) for n in (1060,0,577)])
            with self.assertRaisesRegex(RuntimeError,'signature rejected'):
                service.start()
            self.assertTrue(service.created)


if __name__ == '__main__':
    unittest.main()
