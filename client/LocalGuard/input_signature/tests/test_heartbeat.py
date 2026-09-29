"""하트비트의 상태 계산·로컬 기록·임시 HTTP 계약을 분리해 검증한다.

시계와 응답을 제어해 실제 서버 없이 신선도·순번·인증 헤더·HWID 비기록
동작을 재현한다. loopback 테스트도 배포 서버의 수신 성공을 의미하지 않는다.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import URLError

from heartbeat import (HeartbeatClient, LOCALGUARD_REQUIRED_COMPONENTS,
                       SCHEMA_VERSION, _NoRedirect, load_hwid_file)


class Clock:
    """시간 경과를 수동으로 밀어 stale 경계값을 재현하는 가짜 시계."""
    def __init__(self, value=1000): self.value = value
    def __call__(self): return self.value
    def advance(self, milliseconds): self.value += milliseconds


class Response:
    """HTTP 상태·본문·close 호출을 제어하는 가짜 응답."""
    status = 202
    def __init__(self, body=b''): self.body = body; self.closed = False
    def read(self, limit): return self.body[:limit]
    def close(self): self.closed = True


class Tests(unittest.TestCase):
    """필수 상태는 보수적으로 집계하고 비밀값은 로컬에 남기지 않는지 확인한다."""
    def setUp(self):
        """각 테스트에 고립된 하트비트 파일과 가짜 시계를 만든다."""
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.path = Path(self.temp.name) / 'heartbeat.jsonl'

    def tearDown(self): self.temp.cleanup()

    def client(self, **kwargs):
        """고정 세션·고정 UTC 시각으로 HeartbeatClient를 재현 가능하게 만든다."""
        return HeartbeatClient(
            session_id='round_12', player_id='player_042', log_path=self.path,
            interval_seconds=5, clock=self.clock, origin_ms=1000,
            utc_now=lambda: '2026-09-23T00:00:00+00:00', **kwargs)

    def test_local_log_has_separate_schema_and_monotonic_sequence(self):
        """탐지 Event가 아닌 별도 형식과 증가하는 순번·최종 상태를 검사한다."""
        heartbeat = self.client(client_id='client_1')
        heartbeat.update_component('localguard_input_signature', 'running', pid=123,
                                   details={'scanner': 'yara'})
        heartbeat.update_component('game', 'running', pid=456,
                                   details={'identity_verified': True})
        first, delivered = heartbeat.emit_once()
        self.clock.advance(5000)
        second, _ = heartbeat.emit_once()
        heartbeat.stop()
        rows = [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(first['schema_version'], SCHEMA_VERSION)
        self.assertEqual(first['status'], 'healthy')
        self.assertIsNone(delivered)
        self.assertEqual(second['sequence'], first['sequence'] + 1)
        self.assertEqual(rows[-1]['status'], 'stopped')
        self.assertNotIn('raw_score', first)
        self.assertNotIn('reasons', first)

    def test_stale_required_component_degrades_health(self):
        """필수 구성 요소의 오래된 running 보고는 stale/degraded로 낮춘다."""
        heartbeat = self.client(stale_after_ms=3000)
        heartbeat.update_component('game', 'running', pid=456)
        self.clock.advance(3001)
        payload, _ = heartbeat.emit_once()
        heartbeat.stop(final_status='failed')
        self.assertEqual(payload['status'], 'degraded')
        self.assertEqual(payload['components']['game']['status'], 'stale')

    def test_optional_stopped_component_does_not_degrade(self):
        """보조 구성 요소 종료는 필수 구성 요소의 healthy를 가리지 않는다."""
        heartbeat = self.client()
        heartbeat.update_component('scanner', 'running')
        heartbeat.update_component('dashboard', 'stopped', required=False)
        payload, _ = heartbeat.emit_once()
        heartbeat.stop()
        self.assertEqual(payload['status'], 'healthy')

    def test_required_failure_takes_precedence_over_starting(self):
        """필수 항목 실패가 다른 항목의 starting보다 우선한다."""
        heartbeat = self.client()
        heartbeat.update_component('scanner', 'starting')
        heartbeat.update_component('game', 'failed')
        payload, _ = heartbeat.emit_once()
        heartbeat.stop(final_status='failed')
        self.assertEqual(payload['status'], 'degraded')

    def test_missing_signature_or_hash_never_report_healthy(self):
        """아직 보고하지 않은 필수 탐지기는 healthy로 추정하지 않는다."""
        heartbeat = self.client(required_components=LOCALGUARD_REQUIRED_COMPONENTS +
                                ('localguard_file_hash',),
                                stale_after_ms=3000)
        heartbeat.update_component('localguard_input_signature', 'running', pid=123)
        first, _ = heartbeat.emit_once()
        self.assertEqual(first['status'], 'starting')
        self.assertEqual(first['components']['localguard_file_hash']['status'], 'starting')
        self.clock.advance(3001)
        second, _ = heartbeat.emit_once()
        self.assertEqual(second['status'], 'degraded')
        self.assertEqual(second['components']['localguard_file_hash']['status'], 'stale')
        heartbeat.update_component('localguard_file_hash', 'running',
                                   details={'last_scan_completed_ms': 3001})
        heartbeat.update_component('localguard_input_signature', 'running', pid=123)
        third, _ = heartbeat.emit_once()
        heartbeat.stop()
        self.assertEqual(third['status'], 'healthy')

    def test_process_probe_refreshes_game_and_reports_failure(self):
        """실제 생존 probe의 실패가 다음 하트비트에 반영된다."""
        alive = {'value': True}
        def probe():
            if not alive['value']:
                raise RuntimeError('process_exited')
            return {'status': 'running', 'pid': 456,
                    'details': {'identity_verified': True}}
        heartbeat = self.client(stale_after_ms=3000)
        heartbeat.register_probe('game', probe)
        self.clock.advance(10000)
        first, _ = heartbeat.emit_once()
        self.assertEqual(first['components']['game']['status'], 'running')
        alive['value'] = False
        second, _ = heartbeat.emit_once()
        heartbeat.unregister_probe('game')
        heartbeat.stop(final_status='failed')
        self.assertEqual(second['components']['game']['status'], 'failed')
        self.assertEqual(second['components']['game']['details']['probe_error_type'], 'RuntimeError')

    def test_http_post_uses_json_and_bearer_without_logging_token(self):
        """전송 요청은 JSON·Bearer를 사용하고 토큰은 로그에 남기지 않는다."""
        calls = []
        def opener(request, timeout):
            calls.append((request, timeout))
            sent = json.loads(request.data)
            return Response(json.dumps({'accepted': True, 'session_id': sent['session_id'],
                                        'client_id': sent['client_id'],
                                        'sequence': sent['sequence']}).encode())
        heartbeat = self.client(endpoint='http://127.0.0.1:8000/api/heartbeat',
                                token='test-token', opener=opener)
        heartbeat.update_component('scanner', 'running')
        payload, delivered = heartbeat.emit_once()
        heartbeat.stop()
        request, timeout = calls[0]
        self.assertTrue(delivered)
        self.assertEqual(timeout, 3.0)
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
        self.assertEqual(json.loads(request.data), payload)
        self.assertNotIn('test-token', self.path.read_text(encoding='utf-8'))

    def test_component_one_hwid_is_wire_only(self):
        """HWID는 네트워크 본문에만 들어가고 로컬 JSONL에는 없다."""
        hwid = 'a' * 64
        sent = []
        def opener(request, timeout):
            payload = json.loads(request.data)
            sent.append(payload)
            return Response(json.dumps({'accepted': True,
                                        'session_id': payload['session_id'],
                                        'client_id': payload['client_id'],
                                        'sequence': payload['sequence']}).encode())
        heartbeat = self.client(endpoint='http://127.0.0.1:8000/api/heartbeat',
                                hwid=hwid, opener=opener)
        local, delivered = heartbeat.emit_once()
        heartbeat.stop()
        self.assertTrue(delivered)
        self.assertEqual(sent[0]['hwid'], hwid)
        self.assertNotIn('hwid', local)
        self.assertTrue(local['transport']['hwid_included_on_wire'])
        self.assertNotIn(hwid, self.path.read_text(encoding='utf-8'))

    def test_hwid_file_requires_prehashed_value(self):
        """원시 시리얼 대신 임시 계약의 64자리 소문자 해시만 받는다."""
        path = Path(self.temp.name) / 'hwid.json'
        path.write_text(json.dumps({'hwid': 'b' * 64}), encoding='utf-8')
        self.assertEqual(load_hwid_file(path), 'b' * 64)
        path.write_text(json.dumps({'hwid': 'raw-machine-serial'}), encoding='utf-8')
        with self.assertRaises(ValueError):
            load_hwid_file(path)

    def test_real_loopback_http_receives_hwid_and_acknowledges_sequence(self):
        """로컬 HTTP 모의 서버가 HWID 본문과 같은 순번의 확인을 교환한다."""
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers['Content-Length'])
                payload = json.loads(self.rfile.read(length))
                received.append((self.path, payload))
                body = json.dumps({'accepted': True,
                                   'session_id': payload['session_id'],
                                   'client_id': payload['client_id'],
                                   'sequence': payload['sequence']}).encode()
                self.send_response(202)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        heartbeat = None
        try:
            url = f'http://127.0.0.1:{server.server_port}/api/heartbeat'
            heartbeat = self.client(endpoint=url, hwid='c' * 64)
            first, delivered = heartbeat.emit_once()
            self.assertTrue(delivered)
            heartbeat.stop()
            self.assertEqual(received[0][0], '/api/heartbeat')
            self.assertEqual(received[0][1]['hwid'], 'c' * 64)
            self.assertEqual(received[0][1]['sequence'], first['sequence'])
            self.assertNotIn('c' * 64, self.path.read_text(encoding='utf-8'))
        finally:
            if heartbeat and not heartbeat.closed:
                heartbeat.stop(final_status='failed')
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_success_without_matching_ack_is_transport_failure(self):
        """2xx만 받았다고 성공이 아니며 식별자·순번 확인이 필요하다."""
        heartbeat = self.client(endpoint='http://127.0.0.1:8000/api/heartbeat',
                                opener=lambda request, timeout: Response(b'<html>login</html>'))
        heartbeat.update_component('scanner', 'running')
        _, delivered = heartbeat.emit_once()
        next_payload, _ = heartbeat.emit_once()
        heartbeat.stop(final_status='failed')
        self.assertFalse(delivered)
        self.assertEqual(next_payload['transport']['last_error_type'], 'RuntimeError')
        self.assertEqual(next_payload['transport']['consecutive_failures'], 1)

    def test_default_redirect_handler_rejects_redirect(self):
        """리다이렉트가 접근 토큰을 다른 목적지로 넘기지 못하게 한다."""
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, 'found', {},
                                                          'https://untrusted.example/'))
        heartbeat = self.client(endpoint='http://127.0.0.1:8000/api/heartbeat')
        self.assertTrue(any(isinstance(handler, _NoRedirect)
                            for handler in heartbeat.opener.__self__.handlers))
        heartbeat.endpoint = None
        heartbeat.stop()

    def test_network_failure_is_reported_on_next_heartbeat_not_raised(self):
        """일시적 전송 실패는 다음 상태에 기록하고 검사기를 죽이지 않는다."""
        def opener(request, timeout): raise URLError('offline')
        heartbeat = self.client(endpoint='http://127.0.0.1:8000/api/heartbeat', opener=opener)
        heartbeat.update_component('scanner', 'running')
        _, delivered = heartbeat.emit_once()
        payload, _ = heartbeat.emit_once()
        heartbeat.stop(final_status='failed')
        self.assertFalse(delivered)
        self.assertEqual(payload['transport']['consecutive_failures'], 1)
        self.assertEqual(payload['transport']['last_error_type'], 'URLError')

    def test_secret_bearing_details_are_rejected(self):
        """component details의 비밀값처럼 보이는 키를 거부한다."""
        heartbeat = self.client()
        with self.assertRaises(ValueError):
            heartbeat.update_component('scanner', 'running', details={'api_token': 'nope'})
        heartbeat.stop(final_status='failed')

    def test_invalid_endpoint_credentials_are_rejected(self):
        """URL에 계정정보를 끼워 넣는 설정을 허용하지 않는다."""
        with self.assertRaises(ValueError):
            self.client(endpoint='https://user:password@example.test/api/heartbeat')

    def test_remote_http_is_rejected(self):
        """loopback이 아닌 원격 주소는 HTTPS만 허용한다."""
        with self.assertRaises(ValueError):
            self.client(endpoint='http://example.test/api/heartbeat')

    def test_network_timeout_must_fit_cadence(self):
        """전송 제한 시간이 하트비트 주기를 잠식하지 않도록 검사한다."""
        with self.assertRaises(ValueError):
            self.client(endpoint='http://127.0.0.1:8000/api/heartbeat',
                        timeout_seconds=5)

    def test_periodic_scan_can_have_its_own_freshness_window(self):
        """느린 정기 검사는 별도 stale 한도로 오탐 상태 저하를 피한다."""
        heartbeat = self.client(stale_after_ms=3000)
        heartbeat.update_component('periodic_scan', 'running',
                                   stale_after_ms=20000,
                                   details={'last_scan_completed_ms': 0})
        self.clock.advance(15000)
        first, _ = heartbeat.emit_once()
        self.clock.advance(5001)
        second, _ = heartbeat.emit_once()
        heartbeat.stop()
        self.assertEqual(first['status'], 'healthy')
        self.assertEqual(second['components']['periodic_scan']['status'], 'stale')

    def test_background_write_failure_is_exposed_to_runner(self):
        """로컬 기록 실패를 숨기지 않고 메인 검사기에 전달한다."""
        heartbeat = self.client()
        heartbeat.interval_seconds = 1
        heartbeat.start()
        def fail_write(payload, encoded):
            raise RuntimeError('disk_full')
        heartbeat._write_local = fail_write
        self.assertTrue(heartbeat.stop_event.wait(2))
        with self.assertRaisesRegex(RuntimeError, 'heartbeat_background_failure'):
            heartbeat.check_background()
        with self.assertRaises(RuntimeError):
            heartbeat.stop(final_status='failed')
        self.assertTrue(heartbeat.closed)

    def test_concurrent_emissions_are_serialized(self):
        """두 스레드가 동시에 보내도 순번·파일 작성은 직렬화된다."""
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def opener(request, timeout):
            sent = json.loads(request.data)
            calls.append(sent['sequence'])
            if sent['sequence'] == 1:
                entered.set()
                self.assertTrue(release.wait(2))
            return Response(json.dumps({'accepted': True, 'session_id': sent['session_id'],
                                        'client_id': sent['client_id'],
                                        'sequence': sent['sequence']}).encode())
        heartbeat = self.client(endpoint='http://127.0.0.1:8000/api/heartbeat', opener=opener)
        first = threading.Thread(target=heartbeat.emit_once)
        second = threading.Thread(target=heartbeat.emit_once)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        release.set()
        first.join(2)
        second.join(2)
        heartbeat.stop()
        self.assertEqual(calls, [1, 2, 3])


if __name__ == '__main__': unittest.main()
