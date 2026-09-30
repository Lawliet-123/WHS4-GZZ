"""LocalGuard 구성 요소의 생존·신선도를 기록하는 하트비트 클라이언트.

하트비트는 탐지 점수 Event와 별개다. 항상 로컬 JSONL을 남기며, 수신 URL이
설정된 경우에만 임시 계약에 따라 POST한다. HWID는 다른 담당자가 계산한
최종 식별값을 받아 *네트워크 본문에만* 포함한다.
"""
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


SCHEMA_VERSION = 'meccha-heartbeat-2'
MESSAGE_TYPE = 'heartbeat'
# 필수 구성 요소가 아직 시작 중이거나 오래 갱신되지 않았다면 전체 상태를
# healthy로 보고하지 않는다. 목록은 호출자가 추가할 수도 있다.
LOCALGUARD_REQUIRED_COMPONENTS = (
    'localguard_input_signature',
)
COMPONENT_STATES = {'starting', 'running', 'degraded', 'failed', 'stopped', 'unknown'}
FINAL_STATES = {'stopped', 'failed'}
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_DETAILS_BYTES = 8 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
_SESSION_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
_COMPONENT_NAME = re.compile(r'[a-z][a-z0-9_.-]{0,63}')
_SECRET_KEY = re.compile(r'(?:token|secret|password|authorization|cookie|credential)', re.I)
_HWID = re.compile(r'[0-9a-f]{64}\Z')


def load_hwid_file(path):
    """외부 담당 모듈이 만든 ``{"hwid": "..."}`` 파일만 읽는다.

    원시 하드웨어 정보는 수집하지 않는다. 현재 64자리 소문자 SHA-256 형태는
    최종 팀 계약이 아니라 임시 입력 계약이므로 형식이 달라지면 조정해야 한다.
    """
    path = Path(path).resolve()
    if path.stat().st_size > 4096:
        raise ValueError('hwid_file_too_large')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('invalid_hwid_file') from exc
    if (not isinstance(value, dict) or set(value) != {'hwid'} or
            not isinstance(value['hwid'], str) or
            not _HWID.fullmatch(value['hwid'])):
        raise ValueError('hwid_file_requires_lowercase_sha256_hex')
    return value['hwid']


def monotonic_ms():
    """벽시계 변경과 무관한 경과 시간 계산용 밀리초를 반환한다."""
    return time.monotonic_ns() // 1_000_000


def utc_iso():
    """사람이 읽을 수 있는 전송 시각을 UTC 문자열로 만든다."""
    return datetime.now(timezone.utc).isoformat()


def _contains_secret_key(value):
    """중첩된 상태 설명에 토큰·암호처럼 보이는 키가 섞였는지 검사한다."""
    if isinstance(value, dict):
        return any(_SECRET_KEY.search(str(key)) or _contains_secret_key(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_key(item) for item in value)
    return False


def _validated_details(details):
    """구성 요소의 부가 상태를 크기가 제한된 JSON 값으로 정규화한다.

    토큰처럼 보이는 키와 NaN/Infinity를 거부한다. JSON 왕복 복사로 호출자가
    나중에 원본 dict를 수정해도 이미 기록한 상태가 바뀌지 않게 한다.
    """
    if details is None:
        return {}
    if not isinstance(details, dict):
        raise ValueError('component details must be an object')
    if _contains_secret_key(details):
        raise ValueError('component details must not contain secret-bearing keys')
    try:
        encoded = json.dumps(details, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError('component details must be finite JSON data') from exc
    if len(encoded) > MAX_DETAILS_BYTES:
        raise ValueError('component details too large')
    return json.loads(encoded.decode('utf-8'))


def _validated_endpoint(endpoint):
    """원격 주소는 HTTPS만 허용하고 HTTP는 loopback 테스트로 한정한다."""
    if endpoint is None or endpoint == '':
        return None
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise ValueError('invalid heartbeat endpoint')
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError('heartbeat endpoint must be an http(s) URL without credentials or fragment')
    if parsed.scheme == 'http':
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.casefold() == 'localhost'
        if not loopback:
            raise ValueError('remote heartbeat endpoint requires HTTPS')
    return endpoint


class _NoRedirect(HTTPRedirectHandler):
    """POST가 다른 호스트로 리다이렉트되어 토큰을 보내는 일을 막는다."""
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        """리다이렉트를 따르지 않고 호출자에게 실패로 돌려준다."""
        return None


class HeartbeatClient:
    """구성 요소별 상태를 합쳐 로컬 기록과 선택적 서버 전송을 수행한다.

    ``update_component``가 상태를 보고하고 ``register_probe``가 실제 생존을
    재확인한다. 오래된 필수 보고는 stale로 낮춰 보며, 핵 사용 여부나 점수를
    이 클래스에서 판정하지 않는다.
    """

    def __init__(self, *, session_id, player_id, log_path, endpoint=None,
                 token=None, interval_seconds=5.0, timeout_seconds=3.0,
                 stale_after_ms=None, client_id=None, clock=monotonic_ms,
                 origin_ms=None, utc_now=utc_iso, opener=None,
                 required_components=(), hwid=None):
        """식별자·주기·전송 조건을 검증하고 새 로컬 하트비트 파일을 연다.

        ``clock``/``utc_now``/``opener`` 주입은 테스트용이며, 기본값은 실제
        시간과 HTTP 클라이언트다. 이미 있는 로그 파일은 덮어쓰지 않는다.
        """
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ValueError('invalid heartbeat session_id')
        if not isinstance(player_id, str) or not player_id.strip() or len(player_id) > 100:
            raise ValueError('invalid heartbeat player_id')
        if not 1.0 <= float(interval_seconds) <= 300.0:
            raise ValueError('heartbeat interval must be 1..300 seconds')
        if not 0.5 <= float(timeout_seconds) <= 30.0:
            raise ValueError('heartbeat timeout must be 0.5..30 seconds')
        if token is not None and (not isinstance(token, str) or len(token) > 4096 or
                                  '\r' in token or '\n' in token):
            raise ValueError('invalid heartbeat token')
        if hwid is not None and (not isinstance(hwid, str) or
                                 not _HWID.fullmatch(hwid)):
            raise ValueError('HWID must be a precomputed lowercase SHA-256 hex string')
        client_id = client_id or uuid.uuid4().hex
        if not isinstance(client_id, str) or not _SESSION_ID.fullmatch(client_id):
            raise ValueError('invalid heartbeat client_id')

        self.session_id = session_id
        self.player_id = player_id
        self.client_id = client_id
        self.endpoint = _validated_endpoint(endpoint)
        self.token = token
        self.hwid = hwid
        self.interval_seconds = float(interval_seconds)
        self.timeout_seconds = float(timeout_seconds)
        if self.endpoint and self.timeout_seconds >= self.interval_seconds:
            raise ValueError('heartbeat network timeout must be shorter than interval')
        self.stale_after_ms = (int(stale_after_ms) if stale_after_ms is not None
                               else max(3000, int(self.interval_seconds * 3000)))
        if self.stale_after_ms < 1000:
            raise ValueError('heartbeat stale window must be at least 1000 ms')
        self.clock = clock
        self.origin_ms = clock() if origin_ms is None else origin_ms
        self.utc_now = utc_now
        self.opener = opener or build_opener(_NoRedirect()).open
        # 상태 스냅샷, 순번, 전송이 서로 다른 스레드에서 충돌하지 않도록
        # 상태 잠금과 송신 직렬화 잠금을 분리한다.
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.probe_lock = threading.RLock()
        self.probes = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.failure = None
        self.closed = False
        self.sequence = 0
        self.components = {}
        if isinstance(required_components, str):
            raise ValueError('required components must be a collection of names')
        self.required_components = frozenset(required_components)
        if any(not isinstance(name, str) or not _COMPONENT_NAME.fullmatch(name)
               for name in self.required_components):
            raise ValueError('invalid required component name')
        # 아직 첫 보고가 없는 필수 구성 요소를 'running'으로 추정하지 않는다.
        for name in self.required_components:
            self.components[name] = {
                'status': 'starting', 'required': True, 'pid': None,
                'updated_at_ms': self.elapsed(), 'stale_after_ms': self.stale_after_ms,
                'details': {'awaiting_report': True},
            }
        self.transport = {
            'configured': bool(self.endpoint),
            'hwid_included_on_wire': bool(hwid and self.endpoint),
            'consecutive_failures': 0,
            'last_success_sequence': None,
            'last_error_type': None,
        }
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open('x', encoding='utf-8')

    def elapsed(self):
        """세션 시작 기준 경과 밀리초. 시스템 시계 변경으로 역행하지 않는다."""
        return max(0, int(self.clock() - self.origin_ms))

    def has_component(self, name):
        """해당 구성 요소가 한 번이라도 등록·보고됐는지 확인한다."""
        with self.lock:
            return name in self.components

    def update_component(self, name, status, *, required=True, pid=None, details=None,
                         stale_after_ms=None):
        """검사기의 상태와 마지막 갱신 시각을 원자적으로 교체한다.

        ``required=False``인 보조 항목의 실패는 전체 건강 상태를 낮추지
        않는다. 필수 항목은 호출자가 임의로 선택 항목으로 바꿀 수 없다.
        """
        if not isinstance(name, str) or not _COMPONENT_NAME.fullmatch(name):
            raise ValueError('invalid component name')
        if status not in COMPONENT_STATES:
            raise ValueError('invalid component status')
        if type(required) is not bool:
            raise ValueError('component required must be boolean')
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ValueError('component PID must be positive')
        if name in self.required_components and not required:
            raise ValueError('cannot make an expected component optional')
        if stale_after_ms is not None and (type(stale_after_ms) is not int or stale_after_ms < 1000):
            raise ValueError('component stale window must be at least 1000 ms')
        validated_details = _validated_details(details)
        with self.lock:
            if self.closed:
                raise RuntimeError('heartbeat already closed')
            previous = self.components.get(name)
            self.components[name] = {
                'status': status, 'required': required, 'pid': pid,
                'updated_at_ms': self.elapsed(),
                'stale_after_ms': stale_after_ms if stale_after_ms is not None else
                                  (previous['stale_after_ms'] if previous else self.stale_after_ms),
                'details': validated_details,
            }

    def register_probe(self, name, probe):
        """매 하트비트 직전에 실제 생존·정상 상태를 재확인할 함수를 등록한다.

        probe는 ``{'status': ..., 'pid': ..., 'details': ...}``를 반환한다.
        예외는 해당 구성 요소의 failed로 기록해 조용히 healthy로 남지 않는다.
        """
        if not callable(probe):
            raise ValueError('component probe must be callable')
        if not isinstance(name, str) or not _COMPONENT_NAME.fullmatch(name):
            raise ValueError('invalid component name')
        with self.probe_lock:
            if self.closed:
                raise RuntimeError('heartbeat already closed')
            self.probes[name] = probe

    def unregister_probe(self, name):
        """진행 중인 probe가 끝난 뒤 등록을 해제해 프로세스 핸들을 안전하게 닫는다."""
        with self.probe_lock:
            self.probes.pop(name, None)

    def _run_probes(self):
        """등록된 상태 점검 함수를 돌리고 결과 또는 실패 상태를 반영한다."""
        with self.probe_lock:
            for name, probe in self.probes.items():
                try:
                    result = probe()
                    if not isinstance(result, dict) or result.get('status') not in COMPONENT_STATES:
                        raise ValueError('component probe must return a status object')
                    self.update_component(name, result['status'], pid=result.get('pid'),
                                          details=result.get('details'),
                                          stale_after_ms=result.get('stale_after_ms'))
                except Exception as exc:
                    self.update_component(name, 'failed',
                                          details={'probe_error_type': type(exc).__name__})

    def _snapshot(self, overall_status=None):
        """구성 요소의 최신 상태로 하트비트 한 건과 증가하는 순번을 만든다."""
        with self.lock:
            now = self.elapsed()
            components = {}
            derived = []
            for name, source in sorted(self.components.items()):
                item = dict(source)
                item['details'] = dict(source['details'])
                item['age_ms'] = max(0, now - source['updated_at_ms'])
                # 필수 항목의 과거 'running' 보고를 무한히 신뢰하지 않는다.
                if (source['required'] and source['status'] in ('starting', 'running', 'degraded', 'unknown')
                        and item['age_ms'] > source['stale_after_ms']):
                    item['status'] = 'stale'
                components[name] = item
                if source['required']:
                    derived.append(item['status'])

            # 필수 항목 중 하나라도 실패·지연이면 degraded; 모두 실제로
            # 갱신되어 running일 때만 healthy가 된다.
            if overall_status is None:
                if any(state in ('degraded', 'failed', 'stopped', 'stale') for state in derived):
                    overall_status = 'degraded'
                elif not derived or any(state in ('starting', 'unknown') for state in derived):
                    overall_status = 'starting'
                else:
                    overall_status = 'healthy'
            elif overall_status not in FINAL_STATES:
                raise ValueError('invalid final heartbeat status')

            self.sequence += 1
            payload = {
                'schema_version': SCHEMA_VERSION,
                'message_type': MESSAGE_TYPE,
                'session_id': self.session_id,
                'player_id': self.player_id,
                'client_id': self.client_id,
                'sequence': self.sequence,
                'timestamp_ms': now,
                'sent_at_utc': self.utc_now(),
                'status': overall_status,
                'components': components,
                'transport': dict(self.transport),
            }
            return payload

    def _write_local(self, payload, encoded):
        """HWID를 제외한 스냅샷을 즉시 flush해 전송 실패 후에도 진단 가능하게 한다."""
        if self.file.tell() + len(encoded) + 1 > MAX_LOG_BYTES:
            raise RuntimeError('heartbeat_log_size_limit')
        self.file.write(encoded.decode('utf-8') + '\n')
        self.file.flush()

    def _post(self, payload, encoded):
        """임시 하트비트 계약으로 POST하고 동일 순번의 확인 응답을 요구한다.

        단순 2xx나 HTML 응답은 수신 확인이 아니다. 응답 크기를 제한하고
        어떤 성공·실패 경로에서도 응답 핸들을 닫는다.
        """
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'MecchaAntiCheat-Heartbeat/1',
        }
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = Request(self.endpoint, data=encoded, headers=headers, method='POST')
        response = self.opener(request, timeout=self.timeout_seconds)
        try:
            status = getattr(response, 'status', None)
            if status is None and hasattr(response, 'getcode'):
                status = response.getcode()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RuntimeError('heartbeat_response_too_large')
            if type(status) is not int or not 200 <= status < 300:
                raise RuntimeError('heartbeat_http_status_' + str(status))
            try:
                ack = json.loads(body)
            except (ValueError, UnicodeDecodeError) as exc:
                raise RuntimeError('heartbeat_invalid_ack') from exc
            if (not isinstance(ack, dict) or ack.get('accepted') is not True or
                    ack.get('session_id') != payload['session_id'] or
                    ack.get('client_id') != payload['client_id'] or
                    type(ack.get('sequence')) is not int or
                    ack['sequence'] != payload['sequence']):
                raise RuntimeError('heartbeat_invalid_ack')
        finally:
            close = getattr(response, 'close', None)
            if close:
                close()

    def emit_once(self, *, final_status=None):
        """한 건을 로컬에 남긴 뒤 서버 전송을 시도하고 성공 여부를 반환한다.

        전송 오류는 다음 하트비트의 ``transport`` 진단 상태에 반영한다.
        로컬 파일 오류는 숨기지 않아 검사기의 실패로 전파한다.
        """
        with self.send_lock:
            with self.lock:
                if self.closed:
                    raise RuntimeError('heartbeat already closed')
            if final_status is None:
                self._run_probes()
            payload = self._snapshot(final_status)
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8')
            if len(encoded) > MAX_PAYLOAD_BYTES:
                raise RuntimeError('heartbeat_payload_too_large')
            with self.lock:
                self._write_local(payload, encoded)

            delivered = None
            if self.endpoint:
                try:
                    # 로컬 파일에는 HWID를 남기지 않고 전송 본문 복사본에만 추가한다.
                    wire_payload = dict(payload)
                    if self.hwid is not None:
                        wire_payload['hwid'] = self.hwid
                    wire_encoded = json.dumps(
                        wire_payload, ensure_ascii=False, allow_nan=False,
                        separators=(',', ':')).encode('utf-8')
                    if len(wire_encoded) > MAX_PAYLOAD_BYTES:
                        raise RuntimeError('heartbeat_wire_payload_too_large')
                    self._post(wire_payload, wire_encoded)
                    delivered = True
                    with self.lock:
                        self.transport.update(consecutive_failures=0,
                                              last_success_sequence=payload['sequence'],
                                              last_error_type=None)
                except Exception as exc:
                    delivered = False
                    with self.lock:
                        self.transport['consecutive_failures'] += 1
                        self.transport['last_error_type'] = type(exc).__name__
            return payload, delivered

    def check_background(self):
        """백그라운드 스레드의 치명적 오류를 메인 검사 루프에 전파한다."""
        if self.failure:
            raise RuntimeError('heartbeat_background_failure:' + self.failure)

    def start(self):
        """첫 하트비트를 즉시 기록한 다음 일정 간격의 백그라운드 송신을 시작한다."""
        with self.lock:
            if self.closed or self.thread is not None:
                raise RuntimeError('heartbeat cannot be started')
        self.emit_once()

        def run():
            try:
                # 완료 후 무조건 interval만큼 자면 송신 시간이 누적된다.
                # 예정 시각을 유지하되, 이미 밀렸으면 다음 주기를 새로 잡는다.
                deadline = time.monotonic() + self.interval_seconds
                while not self.stop_event.wait(max(0, deadline - time.monotonic())):
                    self.emit_once()
                    deadline += self.interval_seconds
                    if deadline <= time.monotonic():
                        deadline = time.monotonic() + self.interval_seconds
            except Exception as exc:
                self.failure = type(exc).__name__
                self.stop_event.set()

        self.thread = threading.Thread(target=run, name='MecchaHeartbeat', daemon=True)
        self.thread.start()

    def stop(self, *, final_status='stopped'):
        """스레드 종료를 기다리고 최종 상태 한 건을 기록한 뒤 파일을 닫는다."""
        with self.lock:
            if self.closed:
                return
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=min(35.0, self.timeout_seconds + 5.0))
            if self.thread.is_alive():
                raise RuntimeError('heartbeat_background_thread_did_not_stop')
        try:
            self.emit_once(final_status='failed' if self.failure else final_status)
        finally:
            with self.lock:
                self.file.close()
                self.closed = True
        self.check_background()
