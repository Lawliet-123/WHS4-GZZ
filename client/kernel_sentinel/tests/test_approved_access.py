"""Synthetic identities only; no assertion of actual Windows/driver coverage."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from agent.approved_access import ApprovedAccess, WindowsInspector, digest
from agent.main import Log
from test_integration import diagnostics


def handle(**changes):
    event = dict(type='handle_post', flags=256, status=0, actor_pid=20,
                 actor_create_time=200, target_pid=10, source_pid=20, recipient_pid=20, original_access=0x1010,
                 before_access=0x1010, granted_access=0x1010)
    event.update(changes)
    return event


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('python.exe','sensor.py','client/Launcher/main.py'):
            p = self.root/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('reviewed')
        image = str(self.root/'python.exe')
        self.argv = [image,'sensor.py','--session-id','normal','--player-id','p','--pid','10']
        self.processes = {
            10:dict(create_time=100,image='game.exe'),
            20:dict(create_time=200,image=image,argv=self.argv,cwd=str(self.root)),
            30:dict(create_time=150,image=image,cwd=str(self.root),
                    argv=[image,'client/Launcher/main.py','--session','normal','--player','p'])}
        self.registry = self.root/'registry.json'
        self.claim = dict(session_id='normal',stopping=False,launcher_pid=30,launcher_create_time=150,
            entries=dict(hide_anywhere=dict(pid=20,create_time=200,cwd=str(self.root),argv=self.argv)))
        self.save_registry()
        self.policy = self.root/'policy.json'
        self.spec = dict(entry=['sensor.py'],trees=['sensor.py'],allowed_process_access=0x101010,
                         target_option='--pid',task='read-only')
        self.approval = dict(source_revision='reviewed',modules=dict(hide_anywhere=self.spec),
                             launcher_trees=['client/Launcher/main.py'],
                             sha256={p:digest(self.root/p) for p in ('sensor.py','client/Launcher/main.py')})
        self.save_policy()
        self.inspect = Mock(side_effect=lambda pid,command:copy.deepcopy(self.processes[pid]))
        self.classifier = ApprovedAccess('normal','p',10,100,self.inspect,
            dict(image=image,sha256=digest(image)),root=self.root,registry=self.registry,policy=self.policy)

    def save_registry(self): self.registry.write_text(json.dumps(self.claim))
    def save_policy(self): self.policy.write_text(json.dumps(self.approval))
    def reason(self, event=None): return self.classifier.classify(event or handle())['reason']

    def test_verified_scope_approved(self):
        result = self.classifier.classify(handle())
        self.assertEqual(result['classification'],'approved_observation')
        self.assertTrue(result['approval_valid']); self.assertTrue(result['source_sha256_verified'])
        self.assertEqual(result['actor_module'],'hide_anywhere')

    def test_identity_and_registry_failures(self):
        cases = [
            ('registry_session_stale_or_stopping',lambda:self.claim.update(session_id='old')),
            ('registry_session_stale_or_stopping',lambda:self.claim.update(stopping=True)),
            ('actor_not_uniquely_registered',lambda:self.claim['entries'].clear()),
            ('registry_actor_generation_mismatch',lambda:self.claim['entries']['hide_anywhere'].update(create_time=201)),
            ('actor_pid_reused',lambda:self.processes[20].update(create_time=201)),
            ('target_pid_reused',lambda:self.processes[10].update(create_time=101)),
            ('launcher_pid_reused_or_stale_registry',lambda:self.processes[30].update(create_time=151)),
            ('registered_work_directory_mismatch',lambda:self.processes[20].update(cwd=str(self.root/'elsewhere'))),
            ('executable_not_attested_collector_runtime',lambda:self.processes[20].update(image='other-python.exe')),
            ('live_command_differs_from_registry',lambda:self.processes[20].update(argv=['python','other.py'])),
        ]
        original_claim, original_processes = copy.deepcopy(self.claim),copy.deepcopy(self.processes)
        for reason, mutate in cases:
            with self.subTest(reason=reason):
                self.claim,self.processes=copy.deepcopy(original_claim),copy.deepcopy(original_processes)
                mutate();self.save_registry();self.assertEqual(self.reason(),reason)

    def test_approval_inventory_and_source_changes(self):
        (self.root/'sensor.py').write_text('changed')
        self.assertEqual(self.reason(),'reviewed_source_hash_or_inventory_mismatch')

    def test_script_and_live_session_requirements(self):
        for args, reason in ((['other.py'],'responsible_script_mismatch'),
                             (['sensor.py','--session-id','old','--player-id','p','--pid','10'],'live_session_or_player_mismatch'),
                             (['sensor.py','--session-id','normal','--player-id','p','--pid','11'],'live_target_argument_mismatch')):
            with self.subTest(reason=reason):
                argv=[str(self.root/'python.exe')]+args
                self.claim['entries']['hide_anywhere']['argv']=argv
                self.processes[20]['argv']=argv;self.save_registry()
                self.assertEqual(self.reason(),reason)

    def test_masks_missing_identity_duplicate_and_failed_reads(self):
        for changes, reason in ((dict(granted_access=0x1fffff),'access_outside_reviewed_scope'),
                                (dict(original_access=0x1fffff),'access_outside_reviewed_scope'),
                                (dict(before_access=0x1fffff),'access_outside_reviewed_scope'),
                                (dict(granted_access=None),'access_measurement_missing_or_invalid'),
                                (dict(flags=0),'driver_caller_identity_missing'),
                                (dict(recipient_pid=77),'handle_source_or_recipient_not_caller'),
                                (dict(flags=256|128),'thread_duplicate_restricted_or_incomplete_operation_not_approved'),
                                (dict(flags=256|8),'thread_duplicate_restricted_or_incomplete_operation_not_approved'),
                                (dict(status=0xc0000022),'handle_operation_failed')):
            with self.subTest(reason=reason):self.assertEqual(self.reason(handle(**changes)),reason)
        self.inspect.side_effect=OSError('denied')
        result=self.classifier.classify(handle())
        self.assertFalse(result['approval_valid']);self.assertEqual(result['reason'],'approval_validation_unavailable')

    def test_unverified_rights_stay_unresolved(self):
        self.spec['allowed_process_access']=None;self.save_policy()
        self.assertEqual(self.reason(),'required_access_scope_not_verified')

    def test_reuse_during_validation_and_no_stale_success(self):
        original = self.inspect.side_effect;calls=[0]
        def inspect(pid,command):
            calls[0]+=1
            result=original(pid,command)
            if calls[0]==4:result['create_time']=999
            return result
        self.inspect.side_effect=inspect
        self.assertEqual(self.reason(),'process_generation_changed_during_validation')
        self.inspect.side_effect=original
        self.assertTrue(self.classifier.classify(handle())['approval_valid'])
        self.registry.write_text('broken')
        self.assertEqual(self.reason(),'approval_validation_unavailable')

    def test_cycle_raw_mixed_error_positive_and_reset(self):
        log=Log(self.root/'run','normal','p',{},cycle_events=True,access_classifier=self.classifier)
        event=handle();log.write(event);log.write(diagnostics());log.write(dict(type='sensor_cycle'))
        log.write(handle());log.write(diagnostics(changed=1));log.write(dict(type='sensor_cycle'))
        log.write(diagnostics(failed=1));log.write(dict(type='sensor_cycle'))
        log.write(diagnostics());log.write(dict(type='sensor_cycle'));log.close()
        rows=[json.loads(s) for s in (self.root/'run/common_events.jsonl').read_text().splitlines()]
        self.assertEqual(rows[0]['raw_score'],0);self.assertEqual(rows[0]['evidence']['status'],'NORMAL')
        self.assertEqual(set(rows[0]),{'session_id','player_id','module','timestamp_ms','evidence','reasons','raw_score'})
        cycles=[r for r in rows if r['evidence'].get('sample_kind')=='sensor_cycle']
        self.assertEqual([r['raw_score'] for r in cycles],[0,3,0,0])
        self.assertEqual([r['evidence']['status'] for r in cycles],['NORMAL','DETECTED','ERROR','NORMAL'])
        self.assertEqual(cycles[-1]['reasons'],[])
        raw=[json.loads(s) for s in (self.root/'run/raw_events.jsonl').read_text().splitlines()]
        self.assertEqual(raw[0],event);self.assertNotIn('access_approval',raw[0])

    def test_excess_access_and_collection_error_not_normalized(self):
        log=Log(self.root/'run','normal','p',{},cycle_events=True,access_classifier=self.classifier)
        log.write(handle(granted_access=0x1fffff));log.write(diagnostics());log.write(dict(type='sensor_cycle'))
        self.inspect.side_effect=OSError('denied')
        log.write(handle());log.write(diagnostics());log.write(dict(type='sensor_cycle'));log.close()
        rows=[json.loads(s) for s in (self.root/'run/common_events.jsonl').read_text().splitlines()]
        cycles=[r for r in rows if r['evidence'].get('sample_kind')=='sensor_cycle']
        self.assertEqual([r['raw_score'] for r in cycles],[1,1])
        self.assertTrue(all(r['evidence']['status']!='NORMAL' for r in rows))
        self.assertFalse(cycles[-1]['evidence']['measurement_valid'])
        self.assertIn('access_approval_validation_unavailable',cycles[-1]['reasons'])

    def test_approved_only_cycle_and_query_probe(self):
        log=Log(self.root/'run','normal','p',{},cycle_events=True,access_classifier=self.classifier)
        log.write(handle());log.write(dict(type='sensor_cycle'))
        # Non-sensitive self query is not an approval check or a scoring input.
        log.write(handle(actor_pid=99,original_access=0x1000,before_access=0x1000,granted_access=0x1000))
        log.write(diagnostics());log.write(dict(type='sensor_cycle'));log.close()
        rows=[json.loads(s) for s in (self.root/'run/common_events.jsonl').read_text().splitlines()]
        cycles=[r for r in rows if r['evidence'].get('sample_kind')=='sensor_cycle']
        self.assertTrue(all(r['raw_score']==0 and r['evidence']['status']=='NORMAL' for r in cycles))
        self.assertEqual(cycles[-1]['evidence']['access_approval_counts']['unresolved'],0)

    def test_loopback_shared_ack_storage_scoring_and_dashboard(self):
        import os,socket,threading,time,urllib.request
        from unittest.mock import patch
        import uvicorn
        from fastapi import FastAPI
        from server.receiver import create_router
        from server.receiver.router import bearer_token_verifier
        from server.dashboard_backend import DashboardService,create_dashboard_router
        from server.scoring.storage import ScoringStore
        from shared.config import WriterConfig
        from shared.storage import DetectionWriter
        from shared.logger import get_client_status,flush_client
        writer=DetectionWriter(WriterConfig(self.root/'server'))
        store=ScoringStore(self.root/'scoring.sqlite3')
        service=DashboardService(writer=writer,scoring=store,index_path=self.root/'dashboard.sqlite3',cursor_secret='test')
        app=FastAPI()
        app.include_router(create_router(writer.write_detection,store.process_event,verify_token=bearer_token_verifier('synthetic')))
        app.include_router(create_dashboard_router(service,verify_token=bearer_token_verifier('synthetic')))
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='off'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        deadline=time.monotonic()+5
        while not server.started:
            if time.monotonic()>deadline:self.fail('server startup timed out')
            time.sleep(.01)
        env=dict(GZZ_TELEMETRY_URL=f'http://127.0.0.1:{port}',GZZ_TELEMETRY_TOKEN='synthetic',
                 GZZ_TELEMETRY_OUTBOX=str(self.root/'outbox.sqlite3'),GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK='true')
        try:
            with patch.dict(os.environ,env):
                log=Log(self.root/'run','normal','p',{},t0=str(time.time()-5),telemetry='managed',
                        cycle_events=True,access_classifier=self.classifier)
                try:
                    log.write(handle());log.write(dict(type='sensor_cycle'))
                    # Independently valid positive signal must survive approval filtering.
                    log.write(diagnostics(changed=1));log.write(dict(type='sensor_cycle'))
                    self.inspect.side_effect=OSError('denied')
                    log.write(handle());log.write(diagnostics());log.write(dict(type='sensor_cycle'))
                    log.write(diagnostics(failed=1));log.write(dict(type='sensor_cycle'))
                    self.assertTrue(flush_client(timeout=5))
                    status=get_client_status()
                    self.assertEqual((status.pending,status.failed),(0,0))
                    self.assertEqual(status.acknowledged_this_run,log.count)
                    request=urllib.request.Request(f'http://127.0.0.1:{port}/api/dashboard/events?session_id=normal&player_id=p',
                                                   headers={'Authorization':'Bearer synthetic'})
                    with urllib.request.urlopen(request,timeout=5) as response:
                        items=json.load(response)['items']
                    statuses={item['evidence']['status'] for item in items}
                    self.assertTrue({'NORMAL','DETECTED','SUSPICIOUS','ERROR'} <= statuses)
                    self.assertTrue(all(item['module']=='kernel_sentinel' for item in items))
                    self.assertTrue(all(item['evidence']['timestamp_basis']=='launcher_session_start' for item in items))
                finally:log.close()
                local=[json.loads(s) for s in (self.root/'run/common_events.jsonl').read_text().splitlines()]
                self.assertEqual([item.result for item in writer.iter_stored()],local)
                self.assertIn('"complete": true',(self.root/'run/raw_events.jsonl').read_text())
        finally:
            server.should_exit=True;thread.join(timeout=5);sock.close()

    def test_other_detection_and_error_survive_in_same_cycle(self):
        log=Log(self.root/'run','normal','p',{},cycle_events=True,access_classifier=self.classifier)
        log.write(handle());log.write(diagnostics(changed=1,failed=1));log.write(dict(type='sensor_cycle'))
        log.write(diagnostics());log.write(dict(type='sensor_cycle'));log.close()
        rows=[json.loads(s) for s in (self.root/'run/common_events.jsonl').read_text().splitlines()]
        cycles=[r for r in rows if r['evidence'].get('sample_kind')=='sensor_cycle']
        self.assertEqual(cycles[0]['raw_score'],3)
        self.assertFalse(cycles[0]['evidence']['measurement_valid'])
        self.assertIn('kernel_entry_bytes_changed_since_driver_load',cycles[0]['reasons'])
        self.assertIn('kernel_diagnostics_incomplete',cycles[0]['reasons'])
        self.assertEqual(cycles[1]['reasons'],[])
        self.assertEqual(cycles[1]['evidence']['status'],'NORMAL')

    def test_missing_scope_unknown_module_and_inventory_addition(self):
        self.claim['entries']['other']=self.claim['entries'].pop('hide_anywhere');self.save_registry()
        self.assertEqual(self.reason(),'module_has_no_reviewed_policy')
        self.claim['entries']['hide_anywhere']=self.claim['entries'].pop('other');self.save_registry()
        folder=self.root/'reviewed';folder.mkdir();(folder/'sensor.py').write_text('reviewed')
        self.spec['trees']=['reviewed'];self.approval['sha256']={'reviewed/sensor.py':digest(folder/'sensor.py'),
            'client/Launcher/main.py':digest(self.root/'client/Launcher/main.py')};self.save_policy()
        self.assertTrue(self.classifier.classify(handle())['approval_valid'])
        (folder/'extra.py').write_text('unreviewed')
        self.assertEqual(self.reason(),'reviewed_source_hash_or_inventory_mismatch')

    def test_native_inspector_failure_closes_handles_and_keeps_no_cache(self):
        import sys,types
        from unittest.mock import patch
        api=Mock();api.process.return_value=(42,200,'python.exe');api.wait.return_value=258
        process=Mock();process.cmdline.side_effect=OSError('denied')
        with patch.dict(sys.modules,{'psutil':types.SimpleNamespace(Process=lambda pid:process)}):
            with self.assertRaises(OSError):WindowsInspector(api)(20,command=True)
        api.close.assert_called_once_with(42)


if __name__=='__main__':unittest.main()
