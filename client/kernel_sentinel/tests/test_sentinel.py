import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from agent import protocol as p
from agent.analysis import CrossView, findings, match_path, validate_config
from agent.main import Log

class ProtocolTests(unittest.TestCase):
    def test_event_roundtrip_unicode_and_64_bit(self):
        values = [1, 116444736010000000, 3, 0, 0xffff800012340000, 8192,
                  6, 1, 2, 3, 4, 5, 0x28, 0x28, 0, 4, 0, 0]
        path = '\\SystemRoot\\테스트.sys'
        raw = p.BATCH.pack(1,1,7) + p.EVENT.pack(*values, path.encode('utf-16-le'))
        events, dropped = p.parse_events(raw)
        self.assertEqual(dropped,7)
        self.assertEqual(events[0]['path'],path)
        self.assertEqual(events[0]['image_base'],0xffff800012340000)
        self.assertEqual(events[0]['unix_ms'],1000)
    def test_reject_malformed_batch(self):
        for data in (b'', p.BATCH.pack(1,1,0), p.BATCH.pack(2,0,0), p.BATCH.pack(1,0,0)+b'x'):
            with self.assertRaises(ValueError): p.parse_events(data)
    def test_status_version_and_size(self):
        raw = p.STATUS.pack(1,100,1,1,2,1234,7,2,31)
        self.assertEqual(p.parse_status(raw)['create_time'],1234)
        with self.assertRaises(ValueError): p.parse_status(raw[:-1])
        with self.assertRaises(ValueError): p.parse_status(p.STATUS.pack(2,0,0,0,0,0,0,0,0))
    def test_diagnostics_and_jump(self):
        raw = bytearray(p.DIAG_SIZE)
        struct.pack_into('<8I',raw,0,1,1,0,15,1,0,0,0)
        current = b'\xe9'+struct.pack('<i',-10)+bytes(27)
        struct.pack_into('<QII32s32s',raw,32,0xffff800000001000,0,0,bytes(32),current)
        struct.pack_into('<QI256sI',raw,400,0xffff800000000000,0x2000,b'test.sys',0)
        d = p.parse_diagnostics(raw)
        self.assertEqual(d['probes'][0]['entry_e9_target'],0xffff800000000ffb)
        self.assertEqual(d['modules'][0]['path'],'test.sys')
        struct.pack_into('<I',raw,4,1025)
        with self.assertRaises(ValueError): p.parse_diagnostics(raw)
    def test_code_scan_abi(self):
        self.assertEqual(p.CODE.size,352)
        data = p.CODE.pack(1,0,3,100,0xffff800000000000,4096,1000,bytes(32),b'a'*32,b'x.sys')
        d = p.parse_code(data)
        self.assertEqual(d['path'],'x.sys')
        self.assertEqual(findings(d,{})[0][1],3)

class RuleTests(unittest.TestCase):
    def handle(self, **kw):
        d = dict(type='handle_post',flags=0,status=0,before_access=0x28,granted_access=0x28)
        d.update(kw)
        return d
    def test_failed_handle_is_not_granted(self):
        self.assertEqual(findings(self.handle(status=0xc0000022),{})[0][1],0)
    def test_granted_vs_restricted_and_self(self):
        self.assertEqual(findings(self.handle(),{})[0][0],'sensitive_handle_rights_granted')
        self.assertEqual(findings(self.handle(flags=4,granted_access=0),{})[0][0],'sensitive_handle_rights_restricted')
        self.assertEqual(findings(self.handle(flags=2),{}),[])
        self.assertEqual(findings(self.handle(flags=1),{}),[])
        self.assertEqual(findings(self.handle(granted_access=0x1000),{}),[])
    def test_no_pre_post_double_score(self):
        self.assertEqual(findings(self.handle(type='handle_pre'),{}),[])
    def test_signature_unknown_not_cheat(self):
        self.assertEqual(findings(dict(type='file_evidence',image_kind='kernel_image',sha256=None,signature='unknown'),{}),[])
    def test_hash_match_and_config_validation(self):
        d = dict(type='file_evidence',image_kind='kernel_image',sha256='a'*64)
        self.assertEqual(findings(d,{'driver_sha256':['A'*64]})[0][1],4)
        for conf in ({'x':[]},{'driver_sha256':['bad']},{'process_patterns':'x'}):
            with self.assertRaises(ValueError): validate_config(conf)
    def test_windows_basename(self):
        self.assertEqual(match_path('\\??\\C:\\Windows\\Notepad.EXE',['notepad.exe']),'notepad.exe')
        self.assertIsNone(match_path('C:\\notepad.exe.old',['notepad.exe']))
    def test_initial_code_baseline_not_detection(self):
        self.assertEqual(findings(dict(type='kernel_code_scan',status=0,flags=4),{}),[])
        self.assertEqual(findings(dict(type='kernel_code_scan',status=0xc0000001,flags=2),{})[0][1],0)

class CrossViewTests(unittest.TestCase):
    def diag(self, bases, status=0):
        return {'module_status':status,'modules':[{'base':b} for b in bases]}
    def test_persistence_and_reset(self):
        c=CrossView(); d=self.diag([1,2]); u=[{'base':1}]
        self.assertEqual(c.compare(d,u,d),[])
        self.assertEqual(c.compare(d,u,d),[])
        self.assertEqual(c.compare(d,u,d)[0]['base'],2)
        self.assertEqual(c.compare(d,None,d),[])
        self.assertEqual(c.compare(d,u,d),[])
    def test_loading_unloading_not_reported(self):
        c=CrossView()
        for _ in range(5):
            self.assertEqual(c.compare(self.diag([1,2]),[{'base':1},{'base':3}],self.diag([1,3])),[])
    def test_unavailable_addresses_not_reported(self):
        c=CrossView()
        for _ in range(5):
            self.assertEqual(c.compare(self.diag([1]),[{'base':0}],self.diag([1])),[])

class IntegrationTests(unittest.TestCase):
    def test_logging_keeps_raw_rate_limits_common(self):
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td)/'run'
            log=Log(directory,'test','local',{'process_patterns':['notepad.exe']})
            event={'type':'process_create','path':'C:\\Windows\\notepad.exe','target_pid':10}
            log.write(dict(event)); log.write(dict(event)); log.close()
            raw=(directory/'raw_events.jsonl').read_text().splitlines()
            common=(directory/'common_events.jsonl').read_text().splitlines()
            self.assertEqual(len(raw),2); self.assertEqual(len(common),1)
            self.assertEqual(json.loads(common[0])['module'],'kernel_sentinel')
            with self.assertRaises(FileExistsError): Log(directory,'test','local',{})
    def test_c_policy_and_abi(self):
        compiler=shutil.which('gcc')
        if not compiler: self.skipTest('GCC unavailable: run this check on a machine with GCC')
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            exe=Path(td)/('policy_test.exe' if os.name == 'nt' else 'policy_test')
            subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-I',str(root/'driver'),
                            str(root/'tests/policy_test.c'),'-o',str(exe)],check=True,capture_output=True)
            subprocess.run([str(exe)],check=True,capture_output=True)

if __name__ == '__main__': unittest.main()
