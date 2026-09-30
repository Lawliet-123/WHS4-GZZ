"""로컬 7필드 Event 기록과 shared 대기열 연동의 경계를 검증한다.

외부 서버 대신 loopback 모의 수신기를 사용한다. 따라서 HTTP 요청 형식과
확인 응답까지는 검증하지만 실제 중앙 배포 주소의 수신 성공은 뜻하지 않는다.
"""
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import ssl
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay_events import ReplaySession
from yara_scanner import (configure_detection_forwarding, stop_detection_forwarding,
                          flush_client)
from shared.schema import EVENT_FIELDS, encode_event


class DetectionTransportTests(unittest.TestCase):
    """설정 없음·설정 오류·대기열 실패·전송 성공을 분리해서 확인한다."""
    def setUp(self):
        """테스트마다 덮어쓰기 위험이 없는 임시 세션을 만든다."""
        self.temp = tempfile.TemporaryDirectory()
        self.session = ReplaySession(self.temp.name, 'transport_test',
                                     modules=['localguard_yara'])

    def tearDown(self):
        """세션을 닫고 임시 파일을 제거한다."""
        self.session.finish()
        self.temp.cleanup()

    def emit(self, raw_score=0):
        """YARA 평가를 흉내 낸 동일한 7필드 Event를 기록한다."""
        reasons = ['YARA Rule Matched: test_rule'] if raw_score > 0 else []
        return self.session.emit(
            'localguard_yara',
            'local_player',
            {'matched_rules': ['test_rule'] if raw_score > 0 else []},
            reasons,
            raw_score,
        )

    def test_no_configuration_keeps_local_jsonl_only(self):
        """중앙 설정이 없는 독립 실행은 로컬 파일만 사용한다."""
        with patch.dict(os.environ, {}, clear=True), \
             patch('yara_scanner.configure_client') as configure:
            self.assertFalse(configure_detection_forwarding(self.session))
            event = self.emit()
            configure.assert_not_called()
        self.assertEqual(json.loads((self.session.path / 'events.jsonl').read_text(
            encoding='utf-8')), event)

    def test_each_event_is_queued_after_local_write_without_extra_fields(self):
        """로컬 기록 이후 중앙 대기열에 정확한 7필드가 들어가는지 확인한다."""
        sent = []

        def capture(event):
            local = json.loads((self.session.path / 'events.jsonl').read_text(
                encoding='utf-8').splitlines()[-1])
            self.assertEqual(local, event)
            encode_event(event)  # the real shared schema accepts it unchanged
            sent.append(event)

        env = {'GZZ_TELEMETRY_URL': 'https://telemetry.example',
               'GZZ_TELEMETRY_TOKEN': 'test-token'}
        with patch.dict(os.environ, env, clear=True), \
             patch('yara_scanner.configure_client') as configure, \
             patch('yara_scanner.send_detection', side_effect=capture), \
             patch('yara_scanner.flush_client', return_value=True) as flush, \
             patch('yara_scanner.shutdown_client', return_value=True) as shutdown:
            self.assertTrue(configure_detection_forwarding(self.session))
            self.assertEqual(configure.call_count, 1)
            self.assertEqual(configure.call_args.args[0].outbox_path,
                             (Path(__file__).resolve().parents[1] /
                              'telemetry-outbox' / 'client.sqlite3'))
            event = self.emit(3)
            event['_matched_strings_for_console'] = ['display only']
            self.emit(3)
            stop_detection_forwarding(True)
            flush.assert_called_once_with(timeout=3)
            shutdown.assert_called_once_with(timeout=5)

        self.assertEqual(len(sent), 2)
        self.assertEqual(set(sent[0]), EVENT_FIELDS)
        self.assertNotIn('_matched_strings_for_console', sent[0])
        lines = (self.session.path / 'events.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(set(json.loads(lines[0])), EVENT_FIELDS)

    def test_zero_score_event_stays_local_and_is_not_queued(self):
        """0점 정상 Event는 로컬 JSONL에만 남고 Shared로 보내지 않는다."""
        sent = []

        env = {
            'GZZ_TELEMETRY_URL': 'https://telemetry.example',
            'GZZ_TELEMETRY_TOKEN': 'test-token',
        }

        with patch.dict(os.environ, env, clear=True), \
             patch('yara_scanner.configure_client'), \
             patch('yara_scanner.send_detection',
                   side_effect=lambda event: sent.append(event)), \
             patch('yara_scanner.flush_client', return_value=True), \
             patch('yara_scanner.shutdown_client', return_value=True):
            self.assertTrue(configure_detection_forwarding(self.session))
            event = self.emit(0)
            stop_detection_forwarding(True)

        self.assertEqual(event['raw_score'], 0)
        self.assertEqual(sent, [])

        local = json.loads(
            (self.session.path / 'events.jsonl').read_text(
                encoding='utf-8'
            )
        )
        self.assertEqual(local, event)
    def test_queue_failure_does_not_erase_or_rescore_local_result(self):
        """송신 대기열 실패가 이미 기록된 점수와 로컬 증거를 바꾸지 않는다."""
        env = {'GZZ_TELEMETRY_URL': 'https://telemetry.example',
               'GZZ_TELEMETRY_TOKEN': 'test-token'}
        diagnostic = io.StringIO()
        with patch.dict(os.environ, env, clear=True), \
             patch('yara_scanner.configure_client'), \
             patch('yara_scanner.send_detection', side_effect=RuntimeError('secret')), \
             patch('yara_scanner.flush_client', return_value=False), \
             patch('yara_scanner.shutdown_client', return_value=True), \
             redirect_stderr(diagnostic):
            self.assertTrue(configure_detection_forwarding(self.session))
            event = self.emit(3)
            stop_detection_forwarding(True)
        self.assertEqual(event['raw_score'], 3)
        self.assertEqual(json.loads((self.session.path / 'events.jsonl').read_text(
            encoding='utf-8')), event)
        self.assertIn('중앙 전송 대기열 오류', diagnostic.getvalue())
        self.assertIn('중앙 전송 미완료', diagnostic.getvalue())
        self.assertNotIn('secret', diagnostic.getvalue())

    def test_partial_configuration_is_visible_but_local_scan_can_continue(self):
        """URL만 있는 잘못된 설정은 보이게 알리고 로컬 검사는 유지한다."""
        diagnostic = io.StringIO()
        with patch.dict(os.environ, {'GZZ_TELEMETRY_URL':
                                      'https://telemetry.example'}, clear=True), \
             redirect_stderr(diagnostic):
            self.assertFalse(configure_detection_forwarding(self.session))
            event = self.emit()
        self.assertEqual(json.loads((self.session.path / 'events.jsonl').read_text(
            encoding='utf-8')), event)
        self.assertIn('중앙 전송 설정 실패', diagnostic.getvalue())

    def test_real_shared_client_posts_to_loopback_receiver(self):
        """실제 shared 클라이언트가 HTTP 헤더·본문·ack를 처리하는지 확인한다."""
        received = []

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                received.append((self.path,
                                 {key.lower(): value for key, value in self.headers.items()},
                                 json.loads(body)))
                ack = json.dumps({'event_id': self.headers['Idempotency-Key'],
                                  'status': 'stored'}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(ack)))
                self.end_headers()
                self.wfile.write(ack)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {
            'GZZ_TELEMETRY_URL': f'http://127.0.0.1:{server.server_port}',
            'GZZ_TELEMETRY_TOKEN': 'test-token',
            'GZZ_TELEMETRY_OUTBOX': str(Path(self.temp.name) / 'queue.sqlite3'),
            'GZZ_TELEMETRY_ALLOW_INSECURE_LOOPBACK': 'true',
        }
        active = False
        try:
            # This test uses HTTP loopback only. The sandbox has no Windows
            # root certificates, but HttpTransport still constructs a TLS context.
            with patch.dict(os.environ, env), \
                 patch('shared.transport.ssl.create_default_context',
                       side_effect=lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)):
                active = configure_detection_forwarding(self.session)
                self.assertTrue(active)
                event = self.emit(3)
                self.assertTrue(flush_client(timeout=3))
                stop_detection_forwarding(active)
                active = False
        finally:
            if active:
                stop_detection_forwarding(active)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(len(received), 1)
        path, headers, body = received[0]
        self.assertEqual(path, '/api/detection')
        self.assertEqual(headers['authorization'], 'Bearer test-token')
        self.assertEqual(headers['x-gzz-protocol-version'], '1')
        self.assertEqual(body, event)
        self.assertEqual(set(body), EVENT_FIELDS)


if __name__ == '__main__':
    unittest.main()
