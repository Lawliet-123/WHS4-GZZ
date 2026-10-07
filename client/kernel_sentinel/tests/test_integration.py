"""No driver is loaded: contract and real loopback Shared/Receiver checks."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from agent.main import Log
from agent.session_clock import SessionClock
from agent.driver_setup import ensure_driver
from agent.telemetry import Telemetry


def diagnostics(**overrides):
    event = dict(type='kernel_diagnostics', changed=0, dispatch=0, failed=0,
                 valid=15, module_status=0, modules=[], dispatch_pointers=[])
    event.update(overrides)
    return event


class IntegrationTests(unittest.TestCase):
    def test_common_clock_and_driver_acquisition_time(self):
        with patch('agent.session_clock.time.time_ns', return_value=10_000_000_000), \
             patch('agent.session_clock.time.monotonic_ns', return_value=100):
            clock = SessionClock('5.0')
            self.assertEqual(clock.elapsed_ms(), 5000)
            self.assertEqual(clock.event_ms({'unix_ms': 7000}), 2000)
            self.assertEqual(clock.evidence()['timestamp_basis'], 'launcher_session_start')
            for bad in ('NaN', 'inf', '-1', '100', 'garbage'):
                with self.assertRaises(ValueError): SessionClock(bad)

    def test_normal_cycle_and_failed_cycle_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Log(Path(tmp)/'run', 's', 'p', {}, cycle_events=True)
            log.write(diagnostics())
            log.write(dict(type='sensor_cycle'))
            log.write(diagnostics(failed=1))
            log.write(dict(type='sensor_cycle'))
            log.close()
            rows = [json.loads(s) for s in (Path(tmp)/'run/common_events.jsonl').read_text().splitlines()]
            cycles = [r for r in rows if r['evidence'].get('sample_kind') == 'sensor_cycle']
            self.assertEqual([r['raw_score'] for r in cycles], [0,0])
            self.assertEqual([r['evidence']['status'] for r in cycles], ['NORMAL','ERROR'])
            self.assertFalse(cycles[1]['evidence']['measurement_valid'])

    def test_local_write_precedes_send_and_enqueue_failure_preserves_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'run'
            bridge = Mock()
            def send(result):
                self.assertEqual(json.loads((root/'common_events.jsonl').read_text()), result)
                raise OSError('offline')
            bridge.send.side_effect = send
            bridge.close.return_value = (True,True)
            with patch('agent.main.Telemetry', return_value=bridge):
                log = Log(root,'s','p',{'process_patterns':['sample.exe']},telemetry='managed')
                log.write(dict(type='process_create',path='sample.exe'))
                log.close()
            self.assertEqual(bridge.send.call_count,1)
            self.assertIn('server_enqueue_error',(root/'raw_events.jsonl').read_text())

    def test_shared_facade_uses_generated_uuid_and_lifecycle(self):
        api = Mock()
        with patch.dict(os.environ, {'GZZ_TELEMETRY_OUTBOX':'unique.sqlite3'}), \
             patch('agent.telemetry.importlib.import_module',return_value=api):
            bridge=Telemetry(); event={'raw_score':0}; bridge.send(event); bridge.close()
        api.configure_client.assert_called_once()
        api.send_detection.assert_called_once_with(event)
        api.flush_client.assert_called_once(); api.shutdown_client.assert_called_once()

    def test_missing_outbox_is_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError): Telemetry()

    def test_auto_installer_rejects_unapproved_input(self):
        with patch('agent.driver_setup.os.name','nt'), patch.dict(os.environ,{},clear=True), \
             patch('agent.driver_setup.subprocess.run') as run:
            with self.assertRaises(ValueError): ensure_driver('test.sys')
            with self.assertRaises(ValueError): ensure_driver('test.sys','bad')
            run.assert_not_called()

    def test_bundled_driver_has_fixed_hash_and_needs_no_env(self):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'artifacts/driver_manifest.json').read_text())
        self.assertEqual(manifest['filename'],'KernelSentinel.sys')
        windows_os=Mock(wraps=os);windows_os.name='nt'
        with patch('agent.driver_setup.os',windows_os), patch.dict(os.environ,{},clear=True), \
             patch('agent.driver_setup.subprocess.run',return_value=types.SimpleNamespace(returncode=0)) as run:
            ensure_driver()
        argv=run.call_args.args[0]
        self.assertEqual(argv[argv.index('-ExpectedSha256')+1],manifest['sha256'])
        self.assertEqual(Path(argv[argv.index('-SysPath')+1]),root/'artifacts/KernelSentinel.sys')

    def test_auto_installer_passes_arguments_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'driver name.sys';source.write_bytes(b'synthetic')
            windows_os = Mock(wraps=os); windows_os.name = 'nt'
            with patch('agent.driver_setup.os', windows_os), \
                 patch('agent.driver_setup.subprocess.run',return_value=types.SimpleNamespace(returncode=0)) as run:
                ensure_driver(str(source),'a'*64)
            argv=run.call_args.args[0]
            self.assertEqual(argv[argv.index('-SysPath')+1],str(source))
            self.assertNotIn('shell',run.call_args.kwargs)

    def test_loopback_outbox_receiver_and_scoring(self):
        import uvicorn
        from fastapi import FastAPI
        from server.receiver import create_router
        from server.receiver.router import bearer_token_verifier
        import server.scoring.main as scoring
        from server.scoring.storage import ScoringStore
        from shared.storage import DetectionWriter
        from shared.config import WriterConfig
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); writer=DetectionWriter(WriterConfig(root/'server'));store=ScoringStore(root/'scoring.sqlite3')
            app=FastAPI(); app.include_router(create_router(writer.write_detection,scoring.process,
                                                            verify_token=bearer_token_verifier('synthetic-token')))
            sock=socket.socket();sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='off'))
            thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
            deadline=time.monotonic()+5
            while not server.started:
                if time.monotonic()>deadline: self.fail('startup timed out')
                time.sleep(.01)
            env={'GZZ_TELEMETRY_URL':f'http://127.0.0.1:{port}', 'GZZ_TELEMETRY_TOKEN':'synthetic-token',
                 'GZZ_TELEMETRY_OUTBOX':str(root/'outbox.sqlite3'),'GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK':'true'}
            try:
                with patch.object(scoring,'_store',store),patch.dict(os.environ,env):
                    log=Log(root/'run','s','p',{'process_patterns':['sample.exe']},
                            t0=str(time.time()-5),telemetry='managed',cycle_events=True)
                    try:
                        log.write(diagnostics()); log.write(dict(type='sensor_cycle'))
                        log.write(dict(type='process_create',path='sample.exe'))
                        log.write(diagnostics());log.write(dict(type='sensor_cycle'))
                    finally:log.close()
                    local=[json.loads(s) for s in (root/'run/common_events.jsonl').read_text().splitlines()]
                    self.assertEqual([s.result for s in writer.iter_stored()],local)
                    self.assertEqual([r['raw_score'] for r in local],[0,2,2])
                    self.assertTrue(all(r['evidence']['timestamp_basis']=='launcher_session_start' for r in local))
                    self.assertEqual(store.get_module_state('s','p','kernel_sentinel').raw_score,2)
                    self.assertIn('"complete": true',(root/'run/raw_events.jsonl').read_text())
            finally:
                server.should_exit=True;thread.join(timeout=5);sock.close()


if __name__=='__main__':unittest.main()
