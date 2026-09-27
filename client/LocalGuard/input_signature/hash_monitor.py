"""Periodic hash-blacklist worker independent of potentially slow YARA scans."""
import os
from pathlib import Path
import threading
import time

from executable_hashes import scan_running_executable_hashes
from replay_events import json_line
from windows_process import process_session_id


class HashMonitor:
    def __init__(self, *, catalogue, game_process, session, heartbeat,
                 interval_seconds=5.0):
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
        self.stale_after_ms = max(15000, int((self.interval_seconds * 3 + 20) * 1000))

    def _run_once(self):
        start = self.session.elapsed()
        self.game_process.check()
        result = scan_running_executable_hashes(
            self.catalogue, game_session_id=process_session_id(self.game_process.pid),
            stop_event=self.stop_event)
        self.game_process.check()
        end = self.session.elapsed()
        matches = result['matches']
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
            'size_candidate_count': result['size_candidate_count'],
            'hashed_process_count': result['hashed_process_count'],
            'complete': result['complete'], 'matches': matches,
            'skipped': result['skipped'],
            'score_evaluated': bool(matches) or result['complete'],
        })
        if not result['complete']:
            self.session.error('executable_hash_scan_incomplete')
        if matches or result['complete']:
            self.session.emit(
                'localguard_executable_hash', self.session.manifest['player_id'],
                {'measurement_valid': True, 'scope': result['scope'],
                 'scan_start_ms': start, 'scan_duration_ms': end - start,
                 'coverage_complete': result['complete'],
                 'matched_executables': public_matches,
                 'catalogue_sha256': result['catalogue_sha256'],
                 'active_cheat_proven': False, 'cheat_confirmed': False,
                 'zero_means': 'no_known_executable_hash_match_in_inspected_running_images_not_proven_clean'},
                ['Known executable SHA-256 matched: ' + ','.join(item['catalogue_ids'])
                 for item in public_matches],
                1 if matches else 0, timestamp_ms=end)
        self.heartbeat.update_component(
            'localguard_file_hash', 'running' if result['complete'] else 'degraded',
            pid=os.getpid(), stale_after_ms=self.stale_after_ms,
            details={'scanner': 'sha256_running_executable_images',
                     'catalogue_entry_count': result['catalogue_entry_count'],
                     'last_scan_completed_ms': end,
                     'coverage_complete': result['complete'],
                     'skipped_count': len(result['skipped']),
                     'matched_process_count': len(matches)})

    def _run(self):
        try:
            with (self.session.raw / 'executable_hashes.jsonl').open('x', encoding='utf-8') as raw:
                self.raw = raw
                while not self.stop_event.is_set():
                    began = time.monotonic()
                    try:
                        self._run_once()
                    except InterruptedError:
                        break
                    except Exception as exc:
                        json_line(raw, {'type': 'hash_scan_error',
                                        'timestamp_ms': self.session.elapsed(),
                                        'error_type': type(exc).__name__,
                                        'score_evaluated': False})
                        self.session.error('executable_hash_scan_failed')
                        self.heartbeat.update_component(
                            'localguard_file_hash', 'degraded', pid=os.getpid(),
                            stale_after_ms=self.stale_after_ms,
                            details={'scanner': 'sha256_running_executable_images',
                                     'error_type': type(exc).__name__})
                    remaining = self.interval_seconds - (time.monotonic() - began)
                    self.stop_event.wait(max(0, remaining))
        except Exception as exc:
            self.failure = type(exc).__name__

    def start(self):
        if self.thread is not None:
            raise RuntimeError('hash monitor already started')
        self.thread = threading.Thread(target=self._run,
                                       name='MecchaExecutableHashMonitor', daemon=True)
        self.thread.start()

    def check_background(self):
        if self.failure:
            raise RuntimeError('hash_monitor_background_failure:' + self.failure)
        if self.thread and not self.thread.is_alive() and not self.stop_event.is_set():
            raise RuntimeError('hash_monitor_stopped_unexpectedly')

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise RuntimeError('hash_monitor_did_not_stop')
        self.check_background()
