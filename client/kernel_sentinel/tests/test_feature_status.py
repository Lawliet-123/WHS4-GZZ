import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from agent.feature_status import FeatureStatus, render_table
from agent.analysis import CrossView
from agent.main import Log

def session(**kw):
    e=dict(type='session_start',pid=10,create_time=20,mode='observe',scope='kernel_and_game',
           capabilities=63,thread_interval=15,callback_health_enabled=True,config={},
           collected_unix_ms=1000)
    e.update(kw);return e
def report(*events):
    f=FeatureStatus()
    for e in events:f.observe(e)
    r=f.report();return {e['feature']:e for e in r['features']},r
def diagnostic(**kw):
    e=dict(type='kernel_diagnostics',module_status=0,valid=15,failed=0,changed=0,dispatch=0,
           modules=[dict(base=100,size=100,path='normal.sys')],
           dispatch_pointers=[dict(baseline=110,current=110)]*3,collected_unix_ms=2000)
    e.update(kw);return e

class FeatureStatusTests(unittest.TestCase):
    def test_absence_is_not_pass(self):
        rows,r=report(session())
        self.assertEqual(rows['entry']['state'],'not_observed')
        self.assertEqual(rows['direct_memory']['state'],'not_implemented')
        self.assertEqual(rows['enforcement']['state'],'disabled')
        self.assertFalse(r['session_complete'])

    def test_disabled_and_legacy_unknown_are_distinct(self):
        rows,_=report(session(code_per_cycle=0,thread_interval=0,callback_health_enabled=False))
        for key in ('code','threads','callback'):self.assertEqual(rows[key]['state'],'disabled')
        old,_=report(session())
        self.assertIsNone(old['code']['enabled'])
        self.assertEqual(old['code']['state'],'not_observed')

    def test_baseline_not_comparison_pass_and_failure_not_hidden(self):
        code=dict(type='kernel_code_scan',status=0,flags=4,bytes_hashed=128,path='a.sys')
        rows,_=report(session(),code)
        self.assertEqual(rows['code']['state'],'baseline_only')
        rows,_=report(session(),code,dict(code,flags=1),dict(code,status=0xc0000225,flags=0))
        self.assertEqual(rows['code']['state'],'partial')
        self.assertEqual(rows['code']['successes'],1)
        self.assertEqual(rows['code']['errors'],1)

    def test_diagnostics_independent_success_and_findings(self):
        rows,_=report(session(),diagnostic(module_status=1,modules=[]))
        self.assertEqual(rows['entry']['state'],'checked')
        self.assertEqual(rows['dispatch']['state'],'failed')
        rows,_=report(session(),diagnostic(changed=1,failed=2))
        self.assertEqual(rows['entry']['findings'],1)
        self.assertEqual(rows['entry']['errors'],1)

    def test_legacy_crossview_reconstruction_and_explicit_preference(self):
        d=diagnostic();p=dict(type='psapi_snapshot',modules=d['modules'])
        rows,_=report(session(),d,p,d)
        self.assertEqual(rows['crossview']['successes'],1)
        self.assertIn('재비교',rows['crossview']['reason'])
        rows,_=report(session(),d,p,d,dict(type='driver_cross_view_status',outcome='matched'))
        self.assertEqual(rows['crossview']['successes'],1)
        self.assertNotIn('재비교',rows['crossview']['reason'])

    def test_crossview_module_churn_and_missing_source_inconclusive(self):
        cross=CrossView();a=diagnostic();b=diagnostic(modules=[dict(base=200,size=100,path='b.sys')])
        cross.compare(a,a['modules'],b)
        self.assertEqual(cross.last_status['outcome'],'inconclusive')
        cross.compare(a,None,a)
        self.assertEqual(cross.last_status['reason'],'source_unavailable')

    def test_image_inventory_is_not_callback_verification(self):
        rows,_=report(session(),dict(type='driver_snapshot',path='normal.sys'))
        self.assertIn('로드 콜백 실행 미확인',rows['images']['reason'])

    def test_denied_handle_pair_is_not_sensor_failure(self):
        p=dict(type='handle_pre',operation_id=1,generation=1,actor_pid=30,target_pid=10,flags=0)
        rows,_=report(session(),p,dict(p,type='handle_post',status=0xc0000022))
        self.assertEqual(rows['handles']['errors'],0)
        self.assertEqual(rows['handles']['successes'],1)
        rows,_=report(session(),p)
        self.assertGreater(rows['handles']['errors'],0)

    def test_enforce_mask_validation_and_observe_inactive(self):
        p=dict(type='handle_pre',operation_id=1,flags=32|4,before_access=0x1fffff,after_access=0x1fffff&~0x86b)
        rows,_=report(session(mode='enforce'),p)
        self.assertEqual(rows['enforcement']['successes'],1)
        self.assertEqual(rows['enforcement']['details']['stripped'],1)
        rows,_=report(session(mode='enforce'),dict(p,after_access=0x1fffff))
        self.assertEqual(rows['enforcement']['errors'],1)

    def test_signature_and_empty_policy_not_claimed_checked(self):
        e=dict(type='file_evidence',image_kind='kernel_image',sha256='a'*64,signature='not_checked')
        rows,_=report(session(verify_signatures=False),e)
        sub=rows['files']['subchecks']
        self.assertEqual(sub['signature']['state'],'disabled')
        self.assertEqual(sub['configured_identity_rules']['state'],'disabled')
        rows,_=report(session(),e)
        self.assertEqual(rows['files']['subchecks']['signature']['state'],'not_checked')

    def test_thread_missing_summary_or_inconclusive_gap_not_success(self):
        snap=dict(type='kernel_thread_snapshot',status=0,flags=0)
        good=dict(type='kernel_thread_scan_summary',checked=10,query_failed=0,lookup_failed=0,outside=0)
        rows,_=report(session(),snap,good,snap)
        self.assertEqual(rows['threads']['state'],'partial')
        rows,_=report(session(),snap,dict(type='coverage_gap',reason='kernel_thread_scan_inconclusive'))
        self.assertEqual(rows['threads']['state'],'failed')

    def test_gaps_session_end_and_success_time_preserved(self):
        rows,r=report(session(),diagnostic(),dict(type='coverage_gap',reason='kernel_ring_overflow'),dict(type='session_end'))
        self.assertTrue(r['session_complete'])
        self.assertTrue(r['warnings'])
        self.assertEqual(rows['entry']['last_success_utc'],'1970-01-01T00:00:02.000Z')
        self.assertIn('수집 공백 1건',render_table(r))

    def test_repeated_report_is_idempotent_and_merged_sessions_rejected(self):
        f=FeatureStatus();f.observe(session())
        self.assertEqual(f.report(),f.report())
        with self.assertRaisesRegex(ValueError,'one session'):f.observe(session())

    def test_log_close_writes_table_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'run';log=Log(path,'test','local',{})
            log.write(session());log.write(dict(type='session_end'));log.close()
            self.assertTrue((path/'feature_status.md').is_file())
            data=json.loads((path/'feature_status.json').read_text())
            self.assertEqual(len(data['features']),13)
            self.assertTrue(data['session_complete'])

    def test_cli_json_compatibility_and_table_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw=Path(tmp)/'raw.jsonl';raw.write_text(json.dumps(session())+'\n')
            tool=Path(__file__).resolve().parents[1]/'tools/summarize_run.py'
            base=[sys.executable,str(tool),'--raw',str(raw)]
            r=subprocess.run(base,check=True,capture_output=True,text=True,encoding='utf-8')
            self.assertIn('feature_status',json.loads(r.stdout))
            r=subprocess.run(base+['--format','table'],check=True,capture_output=True,text=True,encoding='utf-8')
            self.assertIn('커널 메모리 접근 직접 관측',r.stdout)
            self.assertIn('미구현',r.stdout)

if __name__=='__main__':unittest.main()
