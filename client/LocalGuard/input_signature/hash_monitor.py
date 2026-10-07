"""YARA 검사와 별개 스레드에서 실행 파일 해시를 주기적으로 확인한다.

YARA 전체 메모리 검사가 오래 걸려도 파일 해시 검사는 자신의 주기를 유지한다.
한 주기 결과는 상세 raw 로그에 기록하고, 판정 가능한 결과만 공통 Event로
내보낸다. 생존 상태는 별도의 하트비트 구성 요소로 보고한다.
"""
import os
from pathlib import Path
import threading
import time

from executable_hashes import scan_running_executable_hashes
from replay_events import json_line
from windows_process import process_session_id


class HashMonitor:
    """해시 검사 스레드의 시작·실패 전파·정지를 관리한다."""

    def __init__(self, *, catalogue, game_process, session, heartbeat,
                 interval_seconds=5.0):
        """카탈로그, 게임 식별자, 기록 세션을 공유하되 검사 스레드는 따로 둔다."""
        if not 5 <= float(interval_seconds) <= 60:
            raise ValueError('hash interval must be 5..60 seconds')
        self.catalogue = catalogue
        self.game_process = game_process
        self.session = session
        self.heartbeat = heartbeat
        self.interval_seconds = float(interval_seconds)
        self.stop_event = threading.Event()
        self.thread = None
        self.failure = None
        self.raw = None
        # 검사 지연 여유를 포함한 신선도 한도. 보고가 끊겼는데도 계속
        # 'running'으로 보이지 않도록 HeartbeatClient가 이 값을 사용한다.
        self.stale_after_ms = max(15000, int((self.interval_seconds * 3 + 20) * 1000))

    def _run_once(self):
        """한 시점의 프로세스 목록을 조사하고 raw/Event/하트비트를 갱신한다."""
        start = self.session.elapsed()
        self.game_process.check()
        result = scan_running_executable_hashes(
            self.catalogue, game_pid=self.game_process.pid,
            game_session_id=process_session_id(self.game_process.pid),
            stop_event=self.stop_event)
        self.game_process.check()
        end = self.session.elapsed()
        matches = result['matches']
        # raw 로그에는 검증용 상세 경로가 남지만 서버 공통 Event에는 경로를 빼고
        # 규칙 ID와 해시 등 필요한 최소 정보만 담는다.
        public_matches = [{key: item[key] for key in
                           ('pid', 'image_name', 'sha256', 'catalogue_ids')}
                          for item in matches]
        json_line(self.raw, {
            'type': 'hash_scan_result', 'timestamp_ms': end,
            'scan_start_ms': start, 'scan_duration_ms': end - start,
            'scope': result['scope'], 'catalogue_sha256': result['catalogue_sha256'],
            'catalogue_entry_count': result['catalogue_entry_count'],
            'process_count': result['process_count'],
            'same_session_count': result['same_session_count'],
            'same_account_session_count': result['same_account_session_count'],
            'other_account_count': result['other_account_count'],
            'owner_unavailable_count': result['owner_unavailable_count'],
            'size_candidate_count': result['size_candidate_count'],
            'hashed_process_count': result['hashed_process_count'],
            'complete': result['complete'], 'matches': matches,
            'skipped': result['skipped'],
            'score_evaluated': bool(matches) or result['complete'],
        })
        if not result['complete']:
            self.session.error('executable_hash_scan_incomplete')
        # 부분 검사에서 일치가 없으면 '정상 0점'을 만들 근거가 없다.
        # 반대로 일치가 실제로 확인됐다면 다른 PID가 누락됐어도 그 증거는 남긴다.
        if matches or result['complete']:
            self.session.emit(
                'localguard_executable_hash', self.session.manifest['player_id'],
                {'measurement_valid': True, 'scope': result['scope'],
                 'scan_start_ms': start, 'scan_duration_ms': end - start,
                 'coverage_complete': result['complete'],
                 'owner_unavailable_count': result['owner_unavailable_count'],
                 'matched_executables': public_matches,
                 'catalogue_sha256': result['catalogue_sha256'],
                 'active_cheat_proven': False, 'cheat_confirmed': False,
                 'zero_means': 'no_known_executable_hash_match_in_confirmed_game_account_processes_not_proven_clean'},
                ['Known executable SHA-256 matched: ' + ','.join(item['catalogue_ids'])
                 for item in public_matches],
                1 if matches else 0, timestamp_ms=end)
        else:
            # 부분 검사에서 일치가 없었다는 사실은 정상 0점이 아니다. 서버도
            # 마지막 성공 표본을 정상으로 갱신하지 않도록 측정 불가를 명시한다.
            self.session.emit(
                'localguard_executable_hash', self.session.manifest['player_id'],
                {'status': 'ERROR', 'measurement_valid': False,
                 'scope': result['scope'], 'scan_start_ms': start,
                 'scan_duration_ms': end - start,
                 'coverage_complete': False,
                 'owner_unavailable_count': result['owner_unavailable_count'],
                 'skipped_count': len(result['skipped']),
                 'matched_executables': [],
                 'catalogue_sha256': result['catalogue_sha256']},
                ['Executable hash scan incomplete'], 0, timestamp_ms=end)
        self.heartbeat.update_component(
            'localguard_file_hash', 'running' if result['complete'] else 'degraded',
            pid=os.getpid(), stale_after_ms=self.stale_after_ms,
            details={'scanner': 'sha256_running_executable_images',
                     'catalogue_entry_count': result['catalogue_entry_count'],
                     'last_scan_completed_ms': end,
                     'coverage_complete': result['complete'],
                     'owner_unavailable_count': result['owner_unavailable_count'],
                     'skipped_count': len(result['skipped']),
                     'matched_process_count': len(matches)})

    def _run(self):
        """종료 신호까지 반복하며 회복 가능한 한 주기 오류는 다음 주기에 재시도한다."""
        try:
            with (self.session.raw / 'executable_hashes.jsonl').open('x', encoding='utf-8') as raw:
                self.raw = raw
                while not self.stop_event.is_set():
                    began = time.monotonic()
                    start_ms = self.session.elapsed()
                    try:
                        self._run_once()
                    except InterruptedError:
                        break
                    except Exception as exc:
                        end = self.session.elapsed()
                        json_line(raw, {'type': 'hash_scan_error',
                                        'timestamp_ms': end,
                                        'error_type': type(exc).__name__,
                                        'score_evaluated': False})
                        self.session.error('executable_hash_scan_failed')
                        self.session.emit(
                            'localguard_executable_hash', self.session.manifest['player_id'],
                            {'status': 'ERROR', 'measurement_valid': False,
                             'scope': 'running_game_account_same_session_executable_disk_sha256',
                             'scan_start_ms': start_ms,
                             'scan_duration_ms': end - start_ms,
                             'coverage_complete': False,
                             'error_type': type(exc).__name__},
                            ['Executable hash scan unavailable: ' + type(exc).__name__],
                            0, timestamp_ms=end)
                        self.heartbeat.update_component(
                            'localguard_file_hash', 'degraded', pid=os.getpid(),
                            stale_after_ms=self.stale_after_ms,
                            details={'scanner': 'sha256_running_executable_images',
                                     'error_type': type(exc).__name__})
                    # 검사 소요 시간을 제외한 나머지만 기다려 시작 간격을 맞춘다.
                    remaining = self.interval_seconds - (time.monotonic() - began)
                    self.stop_event.wait(max(0, remaining))
        except Exception as exc:
            self.failure = type(exc).__name__

    def start(self):
        """백그라운드 검사기를 한 번만 시작한다."""
        if self.thread is not None:
            raise RuntimeError('hash monitor already started')
        self.thread = threading.Thread(target=self._run,
                                       name='MecchaExecutableHashMonitor', daemon=True)
        self.thread.start()

    def check_background(self):
        """백그라운드 스레드의 치명적 오류를 메인 루프에 알린다."""
        if self.failure:
            raise RuntimeError('hash_monitor_background_failure:' + self.failure)
        if self.thread and not self.thread.is_alive() and not self.stop_event.is_set():
            raise RuntimeError('hash_monitor_stopped_unexpectedly')

    def stop(self):
        """종료를 요청하고 최대 30초 기다린 뒤 미종료를 오류로 처리한다."""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise RuntimeError('hash_monitor_did_not_stop')
        self.check_background()
