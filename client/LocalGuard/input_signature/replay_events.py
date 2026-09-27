"""Common ReplayAnalyzer events. Labels are supplied by the tester, never the detector."""
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
    _tick = ctypes.WinDLL('kernel32', use_last_error=True).GetTickCount64
    _tick.argtypes = []
    _tick.restype = ctypes.c_ulonglong
    def clock_ms(): return int(_tick())
else:
    def clock_ms(): return time.monotonic_ns() // 1_000_000


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def json_line(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
    stream.flush()  # Write failures must stop collection, not silently lose evidence.


def session_args(parser):
    parser.add_argument('--session-id', help='Unique test name, e.g. normal_001; existing folders are never overwritten')
    parser.add_argument('--label', choices=['normal', 'cheat', 'unknown'], default='unknown')
    parser.add_argument('--cheat-name', default='')
    parser.add_argument('--player-id', default='local_player', help='Tester-assigned local player ID, NOT authenticated identity')
    parser.add_argument('--game-version', default='unverified')
    parser.add_argument('--hotkeys', action='store_true', help='F6=manual ON marker, F7=manual OFF marker; does NOT toggle a cheat')


def check_session_args(parser, args):
    if args.session_id and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', args.session_id):
        parser.error('--session-id: use 1-80 letters/digits/_/-')
    if not args.player_id.strip() or len(args.player_id) > 100:
        parser.error('--player-id must be 1-100 characters')
    if args.label == 'cheat' and not args.cheat_name.strip():
        parser.error('--label cheat requires --cheat-name')
    if args.label != 'cheat' and (args.cheat_name or args.hotkeys):
        parser.error('--cheat-name / --hotkeys require --label cheat')


class ReplaySession:
    def __init__(self, root, session_id=None, *, label='unknown', player_id='local_player',
                 cheat_name='', game_version='unverified', data_origin='live_game',
                 modules=(), clock=clock_ms):
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
        return cls(args.log_root, args.session_id, label=args.label, player_id=args.player_id,
                   cheat_name=args.cheat_name, game_version=args.game_version, **kwargs)

    def elapsed(self): return max(0, self.clock() - self.origin)

    def write_manifest(self):
        with self.lock:
            self.manifest['event_counts'] = dict(self.counts)
            self.manifest['collection_errors'] = dict(self.errors)
            self.manifest['unscored_observations'] = dict(self.unscored)
            self.manifest['labelled_player_event_count'] = self.labelled_player_events
            atomic_json(self.path / 'manifest.json', self.manifest)

    def update(self, **values):
        with self.lock:
            self.manifest.update(values)
            self.write_manifest()

    def error(self, reason):
        with self.lock:
            self.errors[reason] += 1
            self.write_manifest()

    def emit(self, module, player_id, evidence, reasons, raw_score, *, timestamp_ms=None):
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
            # Evidence must be metadata, never a process-memory dump or matched secret bytes.
            if self.file.tell() > 256 * 1024 * 1024:
                raise RuntimeError('common_event_log_size_limit')
            json_line(self.file, item)
            self.counts[module] += 1
            if player_id == self.manifest['player_id']: self.labelled_player_events += 1
            return item

    def mark(self, action, *, source='manual_hotkey', timestamp_ms=None):
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
            # An unfinished ON stays null; stopping the scanner is NOT proof of cheat OFF.
            self.manifest['comparison_ready'] = bool(
                self.manifest['data_origin'] == 'live_game' and complete and
                not self.manifest.get('test_rules_present', False) and
                self.labelled_player_events > 0 and not self.errors and exit_code == 0)
            self.file.close()
            self.write_manifest()
            self.closed = True


class ManualMarkers:
    """Read only F6/F7 high bits. No key injection, general keylogger or cheat control."""
    def __init__(self, session):
        self.session = session
        self.stop_event = threading.Event()
        self.failure = None
        self.thread = None

    def start(self):
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
        if self.failure: raise RuntimeError('marker_failed:' + self.failure)

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
