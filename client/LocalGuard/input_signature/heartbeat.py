"""Independent anti-cheat component heartbeat with local JSONL fallback."""
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
    """Read component 1's opaque SHA-256 HWID; never derive hardware data here."""
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
    return time.monotonic_ns() // 1_000_000


def utc_iso():
    return datetime.now(timezone.utc).isoformat()


def _contains_secret_key(value):
    if isinstance(value, dict):
        return any(_SECRET_KEY.search(str(key)) or _contains_secret_key(item)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_key(item) for item in value)
    return False


def _validated_details(details):
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
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class HeartbeatClient:
    """Records component liveness locally and optionally POSTs it to a receiver.

    Heartbeats are operational status messages. They deliberately do not carry
    detection raw_score values or make cheat verdicts.
    """

    def __init__(self, *, session_id, player_id, log_path, endpoint=None,
                 token=None, interval_seconds=5.0, timeout_seconds=3.0,
                 stale_after_ms=None, client_id=None, clock=monotonic_ms,
                 origin_ms=None, utc_now=utc_iso, opener=None,
                 required_components=(), hwid=None):
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
        return max(0, int(self.clock() - self.origin_ms))

    def has_component(self, name):
        with self.lock:
            return name in self.components

    def update_component(self, name, status, *, required=True, pid=None, details=None,
                         stale_after_ms=None):
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
        """Poll a real health check before each heartbeat; exceptions mean failure."""
        if not callable(probe):
            raise ValueError('component probe must be callable')
        if not isinstance(name, str) or not _COMPONENT_NAME.fullmatch(name):
            raise ValueError('invalid component name')
        with self.probe_lock:
            if self.closed:
                raise RuntimeError('heartbeat already closed')
            self.probes[name] = probe

    def unregister_probe(self, name):
        # Waits for an in-flight probe, so its underlying process handle can close safely.
        with self.probe_lock:
            self.probes.pop(name, None)

    def _run_probes(self):
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
        with self.lock:
            now = self.elapsed()
            components = {}
            derived = []
            for name, source in sorted(self.components.items()):
                item = dict(source)
                item['details'] = dict(source['details'])
                item['age_ms'] = max(0, now - source['updated_at_ms'])
                if (source['required'] and source['status'] in ('starting', 'running', 'degraded', 'unknown')
                        and item['age_ms'] > source['stale_after_ms']):
                    item['status'] = 'stale'
                components[name] = item
                if source['required']:
                    derived.append(item['status'])

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
        if self.file.tell() + len(encoded) + 1 > MAX_LOG_BYTES:
            raise RuntimeError('heartbeat_log_size_limit')
        self.file.write(encoded.decode('utf-8') + '\n')
        self.file.flush()

    def _post(self, payload, encoded):
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
                    # The local log is redacted. Only the HTTPS/loopback wire
                    # payload receives component 1's opaque HWID.
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
        if self.failure:
            raise RuntimeError('heartbeat_background_failure:' + self.failure)

    def start(self):
        with self.lock:
            if self.closed or self.thread is not None:
                raise RuntimeError('heartbeat cannot be started')
        self.emit_once()

        def run():
            try:
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
