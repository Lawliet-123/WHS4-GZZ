import hashlib
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from executable_hashes import (load_blacklist, scan_running_executable_hashes,
                               hash_stable_image)
from hash_monitor import HashMonitor
from replay_events import ReplaySession
from tests.validate_session import validate
from windows_process import process_session_id, process_session_snapshot
from yara_scanner import main as yara_main


class Identity:
    def __init__(self, pid, path, *, fail_check=False):
        self.pid = pid
        self.initial = {'image_path': str(path), 'creation_time_100ns': 123}
        self.fail_check = fail_check
    def check(self):
        if self.fail_check: raise RuntimeError('pid_reused')
    def __enter__(self): return self
    def __exit__(self, *args): return None


class HeartbeatRecorder:
    def __init__(self): self.calls = []
    def update_component(self, name, status, **kwargs):
        self.calls.append((name, status, kwargs))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / 'renamed_tool.exe'
        self.image.write_bytes(b'MECCHA_TEST_BINARY_' * 100)
        self.digest = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.catalogue_path = self.root / 'hashes.json'
        self.catalogue_path.write_text(json.dumps({
            'schema_version': 'meccha-known-executable-hashes-1',
            'entries': [{'id': 'test_exe', 'family': 'Test',
                         'source': 'controlled_fixture.exe',
                         'size_bytes': self.image.stat().st_size,
                         'sha256': self.digest}]}), encoding='utf-8')
        self.catalogue = load_blacklist(self.catalogue_path)

    def tearDown(self): self.temp.cleanup()

    def scan(self, *, factory=None):
        return scan_running_executable_hashes(
            self.catalogue, game_session_id=1,
            process_rows=[{'pid': 123, 'name': 'anything.exe', 'parent_pid': 1}],
            session_lookup=lambda pid: 1,
            identity_factory=factory or (lambda pid: Identity(pid, self.image)))

    def test_exact_hash_matches_renamed_running_image(self):
        result = self.scan()
        self.assertTrue(result['complete'])
        self.assertEqual(result['matches'][0]['catalogue_ids'], ['test_exe'])
        self.assertEqual(result['matches'][0]['sha256'], self.digest)

    def test_changed_content_same_size_does_not_match(self):
        self.image.write_bytes(b'X' * self.image.stat().st_size)
        result = self.scan()
        self.assertTrue(result['complete'])
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['hashed_process_count'], 1)

    def test_unreadable_or_reused_process_is_incomplete_not_clean(self):
        denied = self.scan(factory=lambda pid: (_ for _ in ()).throw(PermissionError()))
        self.assertFalse(denied['complete'])
        self.assertEqual(denied['matches'], [])
        reused = self.scan(factory=lambda pid: Identity(pid, self.image, fail_check=True))
        self.assertFalse(reused['complete'])
        self.assertEqual(reused['matches'], [])

    def test_nonmatching_size_avoids_file_hash(self):
        self.image.write_bytes(b'tiny')
        with patch('executable_hashes.hash_stable_image') as hash_file:
            result = self.scan()
        hash_file.assert_not_called()
        self.assertTrue(result['complete'])
        self.assertEqual(result['size_candidate_count'], 0)

    def test_invalid_catalogue_fails_closed(self):
        data = json.loads(self.catalogue_path.read_text(encoding='utf-8'))
        data['entries'][0]['sha256'] = 'not-a-hash'
        self.catalogue_path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError): load_blacklist(self.catalogue_path)

    def test_hash_monitor_emits_positive_and_redacts_path_from_common_event(self):
        session = ReplaySession(self.root, 'hash_positive',
                                data_origin='controlled_fixture',
                                modules=['localguard_executable_hash'])
        heartbeat = HeartbeatRecorder()
        monitor = HashMonitor(catalogue=self.catalogue,
                              game_process=SimpleNamespace(pid=1, check=lambda: None),
                              session=session, heartbeat=heartbeat)
        monitor.raw = (session.raw/'executable_hashes.jsonl').open('x', encoding='utf-8')
        try:
            with patch('hash_monitor.process_session_id', return_value=1), \
                 patch('hash_monitor.scan_running_executable_hashes', return_value=self.scan()):
                monitor._run_once()
            event = json.loads((session.path/'events.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(event['raw_score'], 1)
            self.assertEqual(event['module'], 'localguard_executable_hash')
            self.assertNotIn(str(self.image), json.dumps(event))
            self.assertEqual(heartbeat.calls[-1][1], 'running')
        finally:
            monitor.raw.close()
            session.finish()
        self.assertTrue(validate(session.path)['valid_format'])

    def test_hash_monitor_partial_no_hit_emits_no_zero(self):
        session = ReplaySession(self.root, 'hash_partial',
                                data_origin='controlled_fixture',
                                modules=['localguard_executable_hash'])
        heartbeat = HeartbeatRecorder()
        monitor = HashMonitor(catalogue=self.catalogue,
                              game_process=SimpleNamespace(pid=1, check=lambda: None),
                              session=session, heartbeat=heartbeat)
        monitor.raw = io.StringIO()
        partial = self.scan(factory=lambda pid: (_ for _ in ()).throw(PermissionError()))
        try:
            with patch('hash_monitor.process_session_id', return_value=1), \
                 patch('hash_monitor.scan_running_executable_hashes', return_value=partial):
                monitor._run_once()
            self.assertEqual(session.counts, {})
            self.assertEqual(heartbeat.calls[-1][1], 'degraded')
            self.assertFalse(json.loads(monitor.raw.getvalue())['score_evaluated'])
        finally:
            session.finish()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows native-process test')
    def test_live_non_python_exe_hash_match(self):
        compiler = shutil.which('gcc.exe')
        if not compiler:
            self.skipTest('C compiler unavailable')
        executable = self.root / 'renamed_native_fixture.exe'
        built = subprocess.run(
            [compiler, str(Path(__file__).parent/'fixtures'/'hold_read_handle.c'),
             '-o', str(executable)], capture_output=True, timeout=15)
        self.assertEqual(built.returncode, 0, built.stderr.decode(errors='replace'))
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        self.catalogue_path.write_text(json.dumps({
            'schema_version': 'meccha-known-executable-hashes-1',
            'entries': [{'id': 'live_native_fixture', 'family': 'Test',
                         'source': 'controlled_fixture.exe',
                         'size_bytes': executable.stat().st_size,
                         'sha256': digest}]}), encoding='utf-8')
        catalogue = load_blacklist(self.catalogue_path)
        ready = self.root / 'ready.txt'
        child = subprocess.Popen([str(executable), str(os.getpid()), str(ready)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                if child.poll() is not None:
                    self.fail('native hash fixture exited early')
                time.sleep(.1)
            self.assertTrue(ready.exists())
            from windows_process import process_session_id
            result = scan_running_executable_hashes(
                catalogue, game_session_id=process_session_id(os.getpid()),
                process_rows=[{'pid': child.pid, 'name': 'renamed_native_fixture.exe'}])
            self.assertEqual(result['matches'][0]['catalogue_ids'],
                             ['live_native_fixture'])
        finally:
            child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=5)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process-session test')
    def test_native_process_session_snapshot_includes_current_process(self):
        self.assertEqual(process_session_snapshot()[os.getpid()],
                         process_session_id(os.getpid()))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows scanner integration test')
    def test_yara_runner_starts_hash_worker_and_heartbeat(self):
        with redirect_stdout(io.StringIO()):
            code = yara_main([
                '--pid', str(os.getpid()), '--scan-mode', 'autopaint-bridge',
                '--seconds', '5.1', '--interval', '5',
                '--log-root', str(self.root), '--session-id', 'hash_runner_test',
            ])
        self.assertEqual(code, 0)
        folder = self.root / 'hash_runner_test' / 'raw'
        hash_rows = [json.loads(line) for line in
                     (folder/'executable_hashes.jsonl').read_text(encoding='utf-8').splitlines()]
        heartbeats = [json.loads(line) for line in
                      (folder/'heartbeat.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertGreaterEqual(len(hash_rows), 1)
        self.assertEqual(hash_rows[0]['type'], 'hash_scan_result')
        self.assertIn('localguard_file_hash', heartbeats[-1]['components'])
        manifest = json.loads((folder.parent/'manifest.json').read_text(encoding='utf-8'))
        self.assertIn('localguard_executable_hash', manifest['modules'])
        self.assertEqual(manifest['files']['executable_hashes'],
                         'raw/executable_hashes.jsonl')


if __name__ == '__main__': unittest.main()
