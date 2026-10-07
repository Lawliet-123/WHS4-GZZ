"""실행 중 EXE의 정확한 SHA-256 대조와 부분 검사 처리 테스트.

대부분 임시 파일/가짜 프로세스 지문으로 검증한다. 네이티브 검사는 현재
Windows에서 접근 가능한 자기 프로세스와 통제된 자식만 사용한다.
"""
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
from windows_process import (process_owner_snapshot, process_session_id,
                             process_session_snapshot)
from yara_scanner import main as yara_main


class Identity:
    """PID 재사용·이미지 경로를 제어하기 위한 가짜 ProcessIdentity."""
    def __init__(self, pid, path, *, fail_check=False):
        self.pid = pid
        self.initial = {'image_path': str(path), 'creation_time_100ns': 123}
        self.fail_check = fail_check
    def check(self):
        if self.fail_check: raise RuntimeError('pid_reused')
    def __enter__(self): return self
    def __exit__(self, *args): return None


class HeartbeatRecorder:
    """해시 검사기의 상태 보고를 네트워크 없이 수집한다."""
    def __init__(self): self.calls = []
    def update_component(self, name, status, **kwargs):
        self.calls.append((name, status, kwargs))


class Tests(unittest.TestCase):
    """일치·불일치·접근 실패가 서로 다른 결과를 내는지 확인한다."""
    def setUp(self):
        """매 테스트마다 독립적인 가짜 EXE와 정확한 카탈로그를 만든다."""
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
        """게임과 같은 세션의 단일 PID를 모의해 해시 검사를 수행한다."""
        return scan_running_executable_hashes(
            self.catalogue, game_pid=999, game_session_id=1,
            process_rows=[{'pid': 123, 'name': 'anything.exe', 'parent_pid': 1}],
            session_lookup=lambda pid: 1,
            owner_snapshot={999: b'game_account', 123: b'game_account'},
            identity_factory=factory or (lambda pid: Identity(pid, self.image)))

    def test_exact_hash_matches_renamed_running_image(self):
        """파일명 변경과 무관하게 동일 바이트의 알려진 빌드가 일치한다."""
        result = self.scan()
        self.assertTrue(result['complete'])
        self.assertEqual(result['matches'][0]['catalogue_ids'], ['test_exe'])
        self.assertEqual(result['matches'][0]['sha256'], self.digest)

    def test_changed_content_same_size_does_not_match(self):
        """크기가 같아도 내용이 달라지면 SHA-256 불일치로 처리한다."""
        self.image.write_bytes(b'X' * self.image.stat().st_size)
        result = self.scan()
        self.assertTrue(result['complete'])
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['hashed_process_count'], 1)

    def test_unreadable_or_reused_process_is_incomplete_not_clean(self):
        """읽기 실패·PID 재사용은 complete=False이며 정상 판정이 아니다."""
        denied = self.scan(factory=lambda pid: (_ for _ in ()).throw(PermissionError()))
        self.assertFalse(denied['complete'])
        self.assertEqual(denied['matches'], [])
        reused = self.scan(factory=lambda pid: Identity(pid, self.image, fail_check=True))
        self.assertFalse(reused['complete'])
        self.assertEqual(reused['matches'], [])

    def test_account_scope_does_not_trust_process_names(self):
        """다른 계정/소유자 불명은 별도 계수로 남기고 이름 위장은 통하지 않는다."""
        result = scan_running_executable_hashes(
            self.catalogue, game_pid=999, game_session_id=1,
            process_rows=[{'pid': 123, 'name': 'csrss.exe'},
                          {'pid': 124, 'name': 'renamed_tool.exe'},
                          {'pid': 125, 'name': 'renamed_tool.exe'}],
            session_lookup=lambda pid: 1,
            owner_snapshot={999: b'game_account', 123: b'game_account',
                            124: b'other_account', 125: None},
            identity_factory=lambda pid: Identity(pid, self.image))
        self.assertTrue(result['complete'])
        self.assertEqual([match['pid'] for match in result['matches']], [123])
        self.assertEqual(result['same_session_count'], 3)
        self.assertEqual(result['same_account_session_count'], 1)
        self.assertEqual(result['other_account_count'], 1)
        self.assertEqual(result['owner_unavailable_count'], 1)
        self.assertEqual(result['scope'],
                         'running_game_account_same_session_executable_disk_sha256')

    def test_unknown_game_owner_is_not_assumed_clean(self):
        """게임 소유자 SID 자체를 모르면 검사 실패로 처리한다."""
        with self.assertRaisesRegex(LookupError, 'game_process_owner_unavailable'):
            scan_running_executable_hashes(
                self.catalogue, game_pid=999, game_session_id=1,
                process_rows=[{'pid': 123, 'name': 'anything.exe'}],
                session_lookup=lambda pid: 1, owner_snapshot={123: b'user'},
                identity_factory=lambda pid: Identity(pid, self.image))

    def test_nonmatching_size_avoids_file_hash(self):
        """카탈로그에 없는 크기의 이미지는 불필요한 해시 계산을 건너뛴다."""
        self.image.write_bytes(b'tiny')
        with patch('executable_hashes.hash_stable_image') as hash_file:
            result = self.scan()
        hash_file.assert_not_called()
        self.assertTrue(result['complete'])
        self.assertEqual(result['size_candidate_count'], 0)

    def test_invalid_catalogue_fails_closed(self):
        """오염된 카탈로그를 빈 규칙 목록처럼 취급하지 않고 거부한다."""
        data = json.loads(self.catalogue_path.read_text(encoding='utf-8'))
        data['entries'][0]['sha256'] = 'not-a-hash'
        self.catalogue_path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError): load_blacklist(self.catalogue_path)

    def test_hash_monitor_emits_positive_and_redacts_path_from_common_event(self):
        """양성은 Event로 내보내되 개인 PC 경로는 공통 Event에서 제외한다."""
        session = ReplaySession(self.root, 'hash_positive',
                                data_origin='controlled_fixture',
                                modules=['localguard_executable_hash'])
        forwarded = []
        session.event_sink = forwarded.append
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
            self.assertEqual(forwarded, [event])
            self.assertEqual(heartbeat.calls[-1][1], 'running')
        finally:
            monitor.raw.close()
            session.finish()
        self.assertTrue(validate(session.path)['valid_format'])

    def test_hash_monitor_complete_no_hit_forwards_zero(self):
        """완전한 불일치 검사는 0점 Event를 로컬과 송신 경계에 남긴다."""
        self.image.write_bytes(b'X' * self.image.stat().st_size)
        complete = self.scan()
        self.assertTrue(complete['complete'])
        self.assertEqual(complete['matches'], [])
        session = ReplaySession(self.root, 'hash_zero',
                                data_origin='controlled_fixture',
                                modules=['localguard_executable_hash'])
        forwarded = []
        session.event_sink = forwarded.append
        heartbeat = HeartbeatRecorder()
        monitor = HashMonitor(catalogue=self.catalogue,
                              game_process=SimpleNamespace(pid=1, check=lambda: None),
                              session=session, heartbeat=heartbeat)
        monitor.raw = io.StringIO()
        try:
            with patch('hash_monitor.process_session_id', return_value=1), \
                 patch('hash_monitor.scan_running_executable_hashes', return_value=complete):
                monitor._run_once()
            self.assertEqual(len(forwarded), 1)
            event = forwarded[0]
            self.assertEqual(event['raw_score'], 0)
            self.assertTrue(event['evidence']['coverage_complete'])
            self.assertEqual(event['evidence']['matched_executables'], [])
            self.assertEqual(json.loads((session.path/'events.jsonl').read_text(encoding='utf-8')),
                             event)
            self.assertEqual(heartbeat.calls[-1][1], 'running')
        finally:
            session.finish()

    def test_hash_monitor_partial_no_hit_emits_no_zero(self):
        """부분 검사에 일치가 없으면 측정 불가 ERROR Event를 보낸다."""
        session = ReplaySession(self.root, 'hash_partial',
                                data_origin='controlled_fixture',
                                modules=['localguard_executable_hash'])
        forwarded = []
        session.event_sink = forwarded.append
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
            self.assertEqual(session.counts['localguard_executable_hash'], 1)
            self.assertEqual(len(forwarded), 1)
            self.assertEqual(forwarded[0]['raw_score'], 0)
            self.assertEqual(forwarded[0]['evidence']['status'], 'ERROR')
            self.assertIs(forwarded[0]['evidence']['measurement_valid'], False)
            self.assertFalse(forwarded[0]['evidence']['coverage_complete'])
            self.assertEqual(heartbeat.calls[-1][1], 'degraded')
            self.assertFalse(json.loads(monitor.raw.getvalue())['score_evaluated'])
        finally:
            session.finish()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows native-process test')
    def test_live_non_python_exe_hash_match(self):
        """실제 통제된 네이티브 EXE도 Python 외부 후보로 해시 대조한다."""
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
                catalogue, game_pid=os.getpid(),
                game_session_id=process_session_id(os.getpid()),
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
        """Windows 세션 스냅샷이 현재 PID를 포함하는지 확인한다."""
        self.assertEqual(process_session_snapshot()[os.getpid()],
                         process_session_id(os.getpid()))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process-owner test')
    def test_native_process_owner_snapshot_includes_current_process(self):
        """운영체제 소유자 목록에서 검사기 자신의 SID를 확인할 수 있다."""
        self.assertIsNotNone(process_owner_snapshot().get(os.getpid()))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows scanner integration test')
    def test_yara_runner_starts_hash_worker_and_heartbeat(self):
        """YARA 실행기가 해시 워커와 구성 요소 하트비트를 함께 시작한다."""
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
