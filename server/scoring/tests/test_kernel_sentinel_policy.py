"""Provisional kernel policy regression; synthetic events, not Windows replay."""
import copy
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
import server.scoring.main as scoring
from server.scoring.calibration import resolve_calibration
from server.scoring.policies.registry import evaluate_registered_policy


def event(score=0, reasons=None, **evidence):
    values=dict(type='sensor_cycle',sample_kind='sensor_cycle',measurement_valid=True,
                measurement_errors=[],status='NORMAL' if score == 0 else 'DETECTED',
                access_approval_counts=dict(approved_observation=1,unresolved=0),
                timestamp_basis='launcher_session_start')
    values.update(evidence)
    return dict(session_id='s',player_id='p',module='kernel_sentinel',timestamp_ms=1000,
                evidence=values,reasons=reasons or [],raw_score=score)


class KernelPolicyTests(unittest.TestCase):
    def classify(self, item):
        return resolve_calibration(item['module'],raw_score=item['raw_score'],
                                   evidence=item['evidence'],reasons=item['reasons'])

    def test_normal_strong_weak_and_unknown(self):
        cases=[(event(),'threshold'),
               (event(3,['kernel_entry_bytes_changed_since_driver_load']),'threshold'),
               (event(4,['configured_file_sha256_match']),'threshold'),
               (event(2,['name_pattern_match:example.exe']),'advisory'),
               (event(2,['object_callback_probe_missing_persistent']),'advisory'),
               (event(1,['sensitive_handle_rights_granted'],status='SUSPICIOUS'),'pending'),
               (event(3,['unknown_reason']),'pending'),
               (event(3,[]),'pending'),
               (event(1,['kernel_entry_bytes_changed_since_driver_load']),'pending'),
               (event(3,['kernel_entry_bytes_changed_since_driver_load'],measurement_valid=None),'pending'),
               (event(0,[],measurement_errors=['read_failed']),'pending')]
        for item, mode in cases:
            with self.subTest(item=item):self.assertEqual(self.classify(item).mode,mode)
        self.assertEqual(self.classify(event()).version,'kernel-source-provisional-v1')
        self.assertEqual(self.classify(event()).threshold,3)

    def test_structured_individual_evidence_required(self):
        strong=event(3,['kernel_entry_bytes_changed_since_driver_load'],type='kernel_diagnostics',
                     changed=1,valid=15,failed=0,module_status=0)
        self.assertEqual(self.classify(strong).mode,'threshold')
        strong['evidence']['valid']=0
        self.assertEqual(self.classify(strong).mode,'pending')
        item=event(4,['configured_file_sha256_match'],type='file_evidence',image_kind='kernel_image',sha256='a'*64)
        self.assertEqual(self.classify(item).mode,'threshold')
        item['evidence']['sha256']=None
        self.assertEqual(self.classify(item).mode,'pending')

    def test_approved_zero_and_error_not_normalized(self):
        item=event(0,['approved_defense_module_access'],type='handle_post',original_access=0x1010,
                   before_access=0x1010,granted_access=0x1010,access_approval=dict(
                   classification='approved_observation',approval_valid=True,source_sha256_verified=True,
                   allowed_process_access=0x101010,actor_module='hide_anywhere'))
        self.assertEqual(self.classify(item).mode,'threshold')
        item['evidence']['original_access']=0x1fffff
        self.assertEqual(self.classify(item).mode,'pending')
        failed=event(measurement_valid=False,status='ERROR')
        self.assertEqual(evaluate_registered_policy(failed).signal.state,'MEASUREMENT_UNAVAILABLE')
        self.assertEqual(self.classify(failed).mode,'threshold')  # availability gate blocks activation independently

    def test_raw_and_overlap_unchanged(self):
        item=event(3,['kernel_entry_bytes_changed_since_driver_load']);before=copy.deepcopy(item)
        result=evaluate_registered_policy(item)
        self.assertEqual(result.signal.raw_score,3)
        self.assertEqual(result.annotations.overlap_tags,())
        self.assertEqual(item,before)
        self.assertEqual(evaluate_registered_policy(event(5,['unknown'])).signal.state,'OUT_OF_AUDITED_RANGE')

    def test_receiver_dashboard_api_provisional_verdict(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from server.receiver import create_router
        from server.receiver.router import bearer_token_verifier
        from server.dashboard_backend import DashboardService,create_dashboard_router
        from shared.storage import DetectionWriter
        from shared.config import WriterConfig
        with tempfile.TemporaryDirectory() as tmp,patch.object(scoring,'_store',None):
            root=Path(tmp);scoring.configure_scoring(root/'score.sqlite3')
            writer=DetectionWriter(WriterConfig(root/'events'))
            service=DashboardService(writer=writer,scoring=scoring,index_path=root/'dash.sqlite3',
                                     cursor_secret='test',verdict_provider=scoring.get_player_final_verdict)
            app=FastAPI()
            app.include_router(create_router(writer.write_detection,scoring.process,
                               verify_token=bearer_token_verifier('test')))
            app.include_router(create_dashboard_router(service,verify_token=bearer_token_verifier('test')))
            with TestClient(app) as client:
                items=[(event(),'NO_ACTIVE_EVIDENCE'),
                       (event(3,['kernel_entry_bytes_changed_since_driver_load']),'SUSPICIOUS'),
                       (event(measurement_valid=False,status='ERROR'),'INCONCLUSIVE')]
                for seq,(item,expected) in enumerate(items,1):
                    item['timestamp_ms']=seq*1000
                    response=client.post('/api/detection',json=item,headers={'Authorization':'Bearer test',
                        'Idempotency-Key':str(uuid.uuid4()),'X-GZZ-Protocol-Version':'1'})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.json()['status'],'stored')
                    snapshot=client.get('/api/dashboard/sessions/s/players/p/snapshot',
                                        headers={'Authorization':'Bearer test'})
                    self.assertEqual(snapshot.status_code,200)
                    verdict=snapshot.json()['final_verdict']
                    self.assertEqual(verdict['status'],expected)
                    self.assertEqual(verdict['unresolved_modules'],[])
                self.assertEqual([row.result['raw_score'] for row in writer.iter_stored()],[0,3,0])

    def test_full_scoring_verdict_sequence_no_accumulation(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(scoring,'_store',None):
            scoring.configure_scoring(Path(tmp)/'scoring.sqlite3')
            samples=[
                (event(),'INACTIVE','NO_ACTIVE_EVIDENCE'),
                (event(1,['sensitive_handle_rights_granted'],status='SUSPICIOUS'),'UNRESOLVED','INCONCLUSIVE'),
                (event(2,['name_pattern_match:example.exe']),'ADVISORY','NO_ACTIVE_EVIDENCE'),
                (event(3,['kernel_entry_bytes_changed_since_driver_load']),'ACTIVE','SUSPICIOUS'),
                (event(3,['kernel_entry_bytes_changed_since_driver_load']),'ACTIVE','SUSPICIOUS'),
                (event(4,['configured_file_sha256_match']),'ACTIVE','SUSPICIOUS'),
                (event(0,[],measurement_valid=False,status='ERROR'),'UNAVAILABLE','INCONCLUSIVE'),
                (event(),'INACTIVE','NO_ACTIVE_EVIDENCE')]
            for seq,(item,state,verdict) in enumerate(samples,1):
                with self.subTest(state=state,seq=seq):
                    item['timestamp_ms']=seq*1000
                    scoring.process(item,event_id=str(uuid.uuid4()),sequence=seq)
                    aggregate=scoring.get_player_aggregate_evidence('s','p')
                    self.assertEqual(aggregate.signals[0].status,state)
                    result=scoring.get_player_final_verdict('s','p')
                    self.assertEqual(result.status,verdict)
                    self.assertEqual(result.evidence_unit_count,1 if state=='ACTIVE' else 0)
                    self.assertEqual(aggregate.signals[0].raw_score,item['raw_score'])


if __name__=='__main__':unittest.main()
