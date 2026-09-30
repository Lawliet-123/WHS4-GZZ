"""Attribution boundaries: avoid false causes, false reads, and PID-reuse joins."""
import json
import unittest
from agent import protocol as p
from tools.correlate_evidence import correlate, match_call, EPOCH
from tools.instrument_client import instrument, REPLACEMENTS
from tools.summarize_run import summarize

T = EPOCH + 100_000_000
def events(*rows):
    return list(enumerate(rows,1))
def row(kind, dt, **kw):
    return dict(type=kind,timestamp_100ns=T+dt,**kw)
def sample():
    return events(
        row('session_start',0,pid=10,create_time=T-20000),
        row('kernel_image',1,path='C:\\x\\test.sys',sequence=7,generation=2,image_base=4096,image_size=512),
        row('driver_snapshot',2,path='C:\\x\\test.sys',base=4096,size=512),
        row('file_evidence',3,path='C:\\x\\test.sys',image_kind='kernel_image',source_sequence=7,generation=2,sha256='a'*64),
        row('process_create',4,target_pid=20,process_create_time=T+3,path='C:\\client.exe'),
        row('handle_pre',5,actor_pid=20,target_pid=10,operation_id=1,generation=2,thread_id=0,flags=0,
            original_access=0x1fffff),
        row('handle_post',6,actor_pid=20,target_pid=10,operation_id=1,generation=2,thread_id=0,flags=0,
            original_access=0x1fffff,granted_access=0x1fffff,status=0),
        row('session_end',10))
def result(raw=None, **kw):
    return correlate(raw if raw is not None else sample(),'test.sys','client.exe',**kw)

class EvidenceTests(unittest.TestCase):
    def test_load_and_handle_never_prove_read_or_cheat(self):
        r=result()
        self.assertTrue(r['facts']['driver_load']['observed'])
        h=r['facts']['client_handle_access']['operations'][0]
        self.assertFalse(h['memory_read_proven'])
        self.assertEqual(h['call_attribution']['status'],'unidentified')
        self.assertTrue(r['verdict']['direct_read_detection_gap'])
        self.assertTrue(all(not e['cheat_verdict'] and e['score_delta']==0 for e in r['links']))

    def test_file_requires_load_sequence_generation_not_basename(self):
        r=result();self.assertIn('file_analysis_for_load',[e['relation'] for e in r['links']])
        for key,value in [('source_sequence',None),('generation',None),('generation',3)]:
            raw=sample();raw[3][1][key]=value
            self.assertNotIn('file_analysis_for_load',[e['relation'] for e in result(raw)['links']])
            self.assertEqual(result(raw)['facts']['driver_load']['disk_hashes'],[])

    def test_pid_reuse_excludes_old_client_identity(self):
        raw=sample()
        raw.insert(5,(9,row('process_create',5,target_pid=20,process_create_time=T+5,path='other.exe')))
        self.assertFalse(result(raw)['facts']['client_handle_access']['observed'])

    def test_new_creation_identity_mismatch_does_not_join(self):
        raw=sample()
        for n in (5,6):raw[n][1]['actor_create_time']=T+99
        self.assertFalse(result(raw)['facts']['client_handle_access']['observed'])

    def test_unpaired_request_does_not_become_success_or_disappear(self):
        raw=[x for x in sample() if x[1]['type']!='handle_post']
        f=result(raw)['facts']['client_handle_access']
        self.assertTrue(f['observed']);self.assertEqual(f['operations'],[])
        self.assertEqual(f['unpaired_pre_raw_lines'],[6])

    def test_post_mismatched_operation_cannot_attribute(self):
        raw=sample();raw[6][1]['operation_id']=9
        f=result(raw)['facts']['client_handle_access']
        self.assertEqual(f['operations'][0]['call_attribution']['reason'],'unique_pre_missing')
        self.assertEqual(f['unpaired_pre_raw_lines'],[6])

    def test_external_coordinates_not_detector_and_wrong_identity_rejected(self):
        c=dict(line=2,target_pid=10,target_create_time=T-20000,time=T+7,success=True,xyz=(1,2,3))
        bad=dict(c,line=3,target_create_time=T+20000)
        f=result(coord=[c,bad])['facts']['memory_read']
        self.assertEqual(f['external_success_rows'],1)
        self.assertEqual(len(f['external_rejected_rows']),1)
        self.assertEqual(f['sentinel_direct_observation'],'unavailable')

    def test_temporal_window_and_multiple_sessions(self):
        self.assertNotIn('temporal_candidate',[e['relation'] for e in result(window_seconds=0)['links']])
        raw=sample();raw.append((10,row('session_start',20,pid=10,create_time=T)))
        with self.assertRaisesRegex(ValueError,'exactly one'):result(raw)

    def test_api_interval_requires_thread_creation_target_and_complete_pair(self):
        pre=dict(actor_pid=20,actor_tid=30,actor_create_time=T,target_pid=10,timestamp_100ns=T+5)
        base=dict(type='client_call',pid=20,tid=30,process_create_time=T,target_pid=10,
                  call_id=1,api='CreateToolhelp32Snapshot',qpc_frequency=10000000)
        calls=events(dict(base,phase='begin',utc_filetime=T+4,qpc=4),
                     dict(base,phase='end',utc_filetime=T+6,qpc=6))
        self.assertEqual(match_call(pre,calls)['status'],'api_interval_candidate')
        for key,value in [('actor_tid',31),('actor_create_time',T+1),('target_pid',11)]:
            self.assertEqual(match_call(dict(pre,**{key:value}),calls)['status'],'unidentified')
        self.assertEqual(match_call(pre,calls[:1])['status'],'unidentified')
        calls[1][1]['qpc']=2
        self.assertEqual(match_call(pre,calls)['status'],'unidentified')

    def test_flagged_protocol_identity_and_legacy(self):
        for flag in (0,256):
            values=[1,T,2,1,30,T-20,8,20,10,0,0,0,0x1000,0x1000,0x1000,flag,0,0x1000]
            data=p.BATCH.pack(1,1,0)+p.EVENT.pack(*values,b'')
            e=p.parse_events(data)[0][0]
            if flag:
                self.assertEqual(e['actor_tid'],30);self.assertEqual(e['actor_create_time'],T-20)
                self.assertNotIn('image_base',e)
            else:self.assertNotIn('actor_tid',e)

    def test_summary_empty_findings_does_not_hide_handle_results(self):
        r=summarize(json.dumps(e) for _,e in sample())
        self.assertEqual(r['raw_repeated_findings'],{})
        self.assertTrue(r['separate_facts']['handle_operation_results_not_memory_reads'])
        self.assertFalse(r['separate_facts']['driver_load_or_handle_grant_proves_memory_read'])

    def test_instrumentation_rejects_unknown_or_already_patched_client(self):
        original='\n'.join(REPLACEMENTS)
        patched=instrument(original)
        self.assertIn('CcpAuditOpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid)',patched)
        for invalid in ('',patched,original+original):
            with self.assertRaises(ValueError):instrument(invalid)

if __name__=='__main__':unittest.main()
