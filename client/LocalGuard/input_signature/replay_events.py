"""LocalGuard 검사 결과를 세션별 JSONL과 ReplayAnalyzer 형식으로 기록한다.

normal/cheat 라벨은 실험자가 선언하는 정답 표식이지 탐지기가 내린 판정이
아니다. Event 최상위 7필드는 중앙 전송 형식과 같고, 상세 원시 로그는 별도
raw 파일에 둔다. 실패한 검사는 0점 Event로 채우지 않는다.
"""
from collections import Counter
import ctypes
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import threading
import time


if sys.platform == 'win32':
    # Windows 부팅 후 단조 증가하는 tick을 써서 벽시계 조정의 영향을 피한다.
    _tick = ctypes.WinDLL('kernel32', use_last_error=True).GetTickCount64
    _tick.argtypes = []
    _tick.restype = ctypes.c_ulonglong
    def clock_ms(): return int(_tick())
else:
    def clock_ms(): return time.monotonic_ns() // 1_000_000


def atomic_json(path, value):
    """manifest를 임시 파일에 완성한 뒤 교체해 부분 작성 파일을 피한다."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def json_line(stream, value):
    """JSON 한 건을 기록하고 즉시 flush한다; 쓰기 실패는 호출자에게 전파한다."""
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
    stream.flush()  # Write failures must stop collection, not silently lose evidence.


def session_args(parser):
    """검사기 CLI에 공통 세션·수동 실험 표식 옵션을 추가한다."""
    parser.add_argument('--session-id', help='Unique test name, e.g. normal_001; existing folders are never overwritten')
    parser.add_argument('--label', choices=['normal', 'cheat', 'unknown'], default='unknown')
    parser.add_argument('--cheat-name', default='')
    parser.add_argument('--player-id', default='local_player', help='Tester-assigned local player ID, NOT authenticated identity')
    parser.add_argument('--game-version', default='unverified')
    parser.add_argument('--hotkeys', action='store_true', help='F6=manual ON marker, F7=manual OFF marker; does NOT toggle a cheat')


def check_session_args(parser, args):
    """세션 이름과 실험 라벨의 조합을 실행 전에 검증한다."""
    if args.session_id and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', args.session_id):
        parser.error('--session-id: use 1-80 letters/digits/_/-')
    if not args.player_id.strip() or len(args.player_id) > 100:
        parser.error('--player-id must be 1-100 characters')
    if args.label == 'cheat' and not args.cheat_name.strip():
        parser.error('--label cheat requires --cheat-name')
    if args.label != 'cheat' and (args.cheat_name or args.hotkeys):
        parser.error('--cheat-name / --hotkeys require --label cheat')


class ReplaySession:
    """한 번의 검사 세션에서 Event·manifest·실험 ON/OFF 기록을 관리한다.

    동일 이름의 폴더를 재사용하지 않으므로 이전 증거가 덮어써지지 않는다.
    ``lock``은 해시/YARA/수동 표식 스레드가 함께 기록할 때 사용한다.
    """

    def __init__(self, root, session_id=None, *, label='unknown', player_id='local_player',
                 cheat_name='', game_version='unverified', data_origin='live_game',
                 modules=(), clock=clock_ms):
        """세션 디렉터리와 공통 Event 파일을 새로 만들고 manifest를 초기화한다."""
        session_id = session_id or 'localguard_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', session_id):
            raise ValueError('invalid session_id')
        if label not in ('normal', 'cheat', 'unknown') or (label == 'cheat' and not cheat_name):
            raise ValueError('invalid test label / missing cheat name')
        self.path = Path(root).resolve() / session_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.raw = self.path / 'raw'
        self.raw.mkdir()
        self.clock = clock
        self.origin = clock()
        self.lock = threading.RLock()
        self.file = (self.path / 'events.jsonl').open('x', encoding='utf-8')
        self.counts = Counter()
        self.errors = Counter()
        self.unscored = Counter()
        self.labelled_player_events = 0
        self.closed = False
        # 중앙 전송은 선택 기능이다. 로컬 기록을 먼저 완료한 뒤, 콘솔용 필드가
        # 더해지기 전의 7필드 Event 복사본만 전송 훅에 넘긴다.
        self.event_sink = None
        self.manifest = {
            'schema_version': 'localguard-replay-1', 'session_id': session_id,
            'player_id': player_id, 'label': label, 'label_scope': 'declared_player_only',
            'cheat_name': cheat_name or None, 'cheat_intervals': [],
            'ground_truth_source': 'tester_declaration_not_detector',
            'data_origin': data_origin, 'game_version': game_version,
            'started_utc': datetime.now(timezone.utc).isoformat(),
            'clock': 'GetTickCount64' if sys.platform == 'win32' else 'monotonic_ms',
            'clock_origin_ms': self.origin, 'timestamp_unit': 'milliseconds_since_session_start',
            'modules': list(modules), 'status': 'running', 'duration_ms': None,
            'event_counts': {}, 'collection_errors': {}, 'comparison_ready': False,
            'cheat_confirmed': False, 'automatic_blocking': False,
            'score_policy_version': 'localguard-prototype-1', 'thresholds_calibrated': False,
            'files': {'common_events': 'events.jsonl', 'raw': 'raw/'},
            'identity_note': 'Local ID is tester-supplied; remote IDs are session-local observed game IDs, not verified senders.'
        }
        self.write_manifest()

    @classmethod
    def from_args(cls, args, **kwargs):
        """CLI 인자를 명시적 생성자 인자로 옮기는 얇은 어댑터다."""
        return cls(args.log_root, args.session_id, label=args.label, player_id=args.player_id,
                   cheat_name=args.cheat_name, game_version=args.game_version, **kwargs)

    def elapsed(self):
        """세션 시작 기준의 경과 밀리초를 반환한다."""
        return max(0, self.clock() - self.origin)

    def write_manifest(self):
        """현재 집계값을 manifest에 반영하고 원자적으로 저장한다."""
        with self.lock:
            self.manifest['event_counts'] = dict(self.counts)
            self.manifest['collection_errors'] = dict(self.errors)
            self.manifest['unscored_observations'] = dict(self.unscored)
            self.manifest['labelled_player_event_count'] = self.labelled_player_events
            atomic_json(self.path / 'manifest.json', self.manifest)

    def update(self, **values):
        """검사 메타데이터를 갱신해 manifest에 바로 반영한다."""
        with self.lock:
            self.manifest.update(values)
            self.write_manifest()

    def error(self, reason):
        """검사 누락·실패 사유를 세션에 누적해 비교 가능 여부 판단에 사용한다."""
        with self.lock:
            self.errors[reason] += 1
            self.write_manifest()

    def emit(self, module, player_id, evidence, reasons, raw_score, *, timestamp_ms=None):
        """공통 7필드 Event를 로컬에 쓰고 선택적으로 중앙 전송에 전달한다.

        시간·점수·필드 형식을 검사한다. 중앙 송신 실패를 어떻게 처리할지는
        ``event_sink`` 구현이 결정하며, 검사 결과 자체는 바꾸지 않는다.
        """
        with self.lock:
            if self.closed: raise RuntimeError('session already closed')
            stamp = self.elapsed() if timestamp_ms is None else timestamp_ms
            if type(stamp) is not int or stamp < 0 or stamp > self.elapsed() + 1000:
                raise ValueError('timestamp must be relative to this session')
            if (type(raw_score) not in (int, float) or not math.isfinite(raw_score) or raw_score < 0):
                raise ValueError('invalid raw_score')
            if not module or not isinstance(player_id, str) or not player_id:
                raise ValueError('module and player ID required')
            if not isinstance(evidence, dict) or not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
                raise ValueError('invalid evidence/reasons')
            item = {'session_id': self.manifest['session_id'], 'player_id': player_id,
                    'module': module, 'timestamp_ms': stamp, 'evidence': evidence,
                    'reasons': reasons, 'raw_score': raw_score}
            # 증거는 메타데이터만 담는다. 원시 메모리나 임의의 문자열 바이트는
            # 로컬 공통 Event와 서버 전송 양쪽에 넣지 않는다.
            if self.file.tell() > 256 * 1024 * 1024:
                raise RuntimeError('common_event_log_size_limit')
            json_line(self.file, item)
            self.counts[module] += 1
            if player_id == self.manifest['player_id']: self.labelled_player_events += 1
            sink = self.event_sink
        if sink is not None and raw_score > 0:
            # 파일 잠금을 놓은 뒤 호출해야 전송 대기열의 디스크 작업이 다른
            # 검사 스레드의 로컬 증거 기록까지 오래 막지 않는다.
            sink(dict(item))
        return item

    def mark(self, action, *, source='manual_hotkey', timestamp_ms=None):
        """실험자가 선언한 핵 ON/OFF 시각을 순서대로 기록한다.

        이것은 핵 기능을 제어하는 동작이 아니라 검증용 정답 표식이다.
        """
        with self.lock:
            if self.closed: raise RuntimeError('session closed')
            if self.manifest['label'] != 'cheat': raise ValueError('markers require a cheat-labelled test')
            stamp = self.elapsed() if timestamp_ms is None else timestamp_ms
            if type(stamp) is not int or not 0 <= stamp <= self.elapsed():
                raise ValueError('invalid marker timestamp')
            intervals = self.manifest['cheat_intervals']
            active = intervals and intervals[-1]['off_ms'] is None
            if action == 'on' and not active:
                if intervals and stamp < intervals[-1]['off_ms']: raise ValueError('marker time went backwards')
                intervals.append({'cheat': self.manifest['cheat_name'], 'player_id': self.manifest['player_id'],
                                  'on_ms': stamp, 'off_ms': None, 'source': source})
            elif action == 'off' and active:
                if stamp <= intervals[-1]['on_ms']: raise ValueError('OFF must be after ON')
                intervals[-1]['off_ms'] = stamp
            else:
                raise ValueError('duplicate ON or OFF without ON')
            self.write_manifest()
            return stamp

    def finish(self, exit_code=0):
        """마지막 집계와 비교 가능 여부를 기록하고 세션을 닫는다."""
        with self.lock:
            if self.closed: return
            self.manifest.update(status='completed' if exit_code == 0 else 'failed',
                                 duration_ms=self.elapsed(), exit_code=exit_code,
                                 ended_utc=datetime.now(timezone.utc).isoformat())
            intervals = self.manifest['cheat_intervals']
            complete = (self.manifest['label'] == 'normal' or
                        (self.manifest['label'] == 'cheat' and bool(intervals) and
                         all(i['off_ms'] is not None for i in intervals)))
            self.manifest['ground_truth_complete'] = complete
            # ON 상태에서 검사기가 끝났다고 핵이 OFF가 된 것은 아니다.
            # 실험 구간과 수집 상태가 모두 완전해야 비교 준비로 표시한다.
            self.manifest['comparison_ready'] = bool(
                self.manifest['data_origin'] == 'live_game' and complete and
                not self.manifest.get('test_rules_present', False) and
                self.labelled_player_events > 0 and not self.errors and exit_code == 0)
            self.file.close()
            self.write_manifest()
            self.closed = True


class ManualMarkers:
    """F6/F7의 눌림 변화만 읽어 실험자의 ON/OFF 표식을 남긴다.

    키를 주입하거나 모든 키 입력을 기록하거나 핵 기능을 제어하지 않는다.
    """
    def __init__(self, session):
        """표식을 적을 세션과 종료 신호를 보관한다."""
        self.session = session
        self.stop_event = threading.Event()
        self.failure = None
        self.thread = None

    def start(self):
        """Windows에서 키의 상승 에지만 감시하는 짧은 폴링 스레드를 시작한다."""
        if sys.platform != 'win32': raise RuntimeError('hotkeys require Windows')
        get_key = ctypes.WinDLL('user32', use_last_error=True).GetAsyncKeyState
        get_key.argtypes = [ctypes.c_int]
        get_key.restype = ctypes.c_short
        def run():
            previous = {key: bool(get_key(key) & 0x8000) for key in (0x75, 0x76)}
            try:
                while not self.stop_event.wait(.02):
                    for key, action in ((0x75, 'on'), (0x76, 'off')):
                        down = bool(get_key(key) & 0x8000)
                        # 누르고 있는 동안 여러 ON/OFF가 기록되지 않도록
                        # 이전에는 떼어져 있었고 지금 눌린 순간만 처리한다.
                        if down and not previous[key]:
                            try:
                                stamp = self.session.mark(action)
                                print(f'[MARKER] {action.upper()} at {stamp} ms (manual declaration)', flush=True)
                            except ValueError as exc:
                                print('[MARKER ignored]', exc, flush=True)
                        previous[key] = down
            except Exception as exc:
                self.failure = str(exc)
                self.stop_event.set()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        print('F6=핵 ON 시각 기록 / F7=OFF 시각 기록. 핵을 켜거나 끄는 키가 아닙니다. 기록 출력을 확인하세요.')

    def check(self):
        """표식 스레드가 실패했으면 메인 루프에 알린다."""
        if self.failure: raise RuntimeError('marker_failed:' + self.failure)

    def stop(self):
        """감시를 멈추고 남은 스레드가 끝나기를 기다린다."""
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
