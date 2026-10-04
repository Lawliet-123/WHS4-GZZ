"""실행 중인 프로세스의 디스크 EXE를 읽기 전용으로 해시 대조한다.

블랙리스트의 SHA-256과 크기가 모두 같은 *알려진 빌드*만 찾는다. 이름을 바꾼
EXE도 찾을 수 있지만, 재빌드·수정된 파일이나 DLL/스크립트까지 찾는 검사는
아니다. 프로세스에 접근하지 못한 경우는 정상 0점이 아닌 검사 공백으로 남긴다.
"""
import hashlib
import json
import os
from pathlib import Path
import re

from windows_process import (ProcessIdentity, list_processes, process_owner_snapshot,
                             process_session_snapshot)


SCHEMA_VERSION = 'meccha-known-executable-hashes-1'
# 카탈로그는 신뢰할 수 있는 로컬 파일이어야 한다. 크기·형식을 제한해 오입력이나
# 지나치게 큰 파일 때문에 검사기가 멈추는 일을 줄인다.
_ID = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
MAX_CATALOGUE_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 256 * 1024 * 1024


def load_blacklist(path):
    """로컬 JSON 카탈로그를 검증하고 검색용 색인을 만든다.

    반환값의 ``sizes``는 해시 계산 전의 빠른 후보 필터이고, ``by_digest``는
    (파일 크기, SHA-256)으로 규칙 ID를 찾는 표다. 네트워크 조회나 동적 코드
    실행은 하지 않는다. 형식이 잘못되면 조용히 빈 목록으로 처리하지 않는다.
    """
    path = Path(path).resolve()
    if path.stat().st_size > MAX_CATALOGUE_BYTES:
        raise ValueError('hash_catalogue_too_large')
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('invalid_hash_catalogue_json') from exc
    if (not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION or
            set(data) != {'schema_version', 'entries'} or
            not isinstance(data['entries'], list) or not data['entries'] or
            len(data['entries']) > 1024):
        raise ValueError('invalid_hash_catalogue')
    # ID 중복을 금지해야 한 빌드의 결과가 여러 규칙으로 잘못 집계되지 않는다.
    by_digest = {}
    ids = set()
    for item in data['entries']:
        if not isinstance(item, dict) or set(item) != {
                'id', 'family', 'source', 'size_bytes', 'sha256'}:
            raise ValueError('invalid_hash_entry')
        name = item['id']
        digest = item['sha256']
        size = item['size_bytes']
        if (not isinstance(name, str) or not _ID.fullmatch(name) or name in ids or
                not isinstance(digest, str) or not _HEX.fullmatch(digest) or
                type(size) is not int or not 0 < size <= MAX_IMAGE_BYTES or
                not isinstance(item['family'], str) or not 0 < len(item['family']) <= 80 or
                not isinstance(item['source'], str) or not 0 < len(item['source']) <= 256):
            raise ValueError('invalid_hash_entry')
        ids.add(name)
        by_digest.setdefault((size, digest), []).append(name)
    return {'entries': data['entries'],
            'sizes': frozenset(item['size_bytes'] for item in data['entries']),
            'by_digest': by_digest,
            'catalogue_sha256': hashlib.sha256(raw).hexdigest(),
            'entry_count': len(data['entries'])}


def hash_stable_image(path, expected_size, *, stop_event=None):
    """파일 핸들에서 SHA-256을 계산하며 읽는 도중 교체·변경을 확인한다.

    해시 전후의 핸들 정보와 현재 경로 정보를 비교한다. 서로 다르면 계산한
    해시를 판정에 쓰지 않는다. ``stop_event``는 종료 요청에 빠르게 응답한다.
    """
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_size != expected_size:
            raise RuntimeError('image_size_changed_before_hash')
        while True:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError('hash_scan_stopped')
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    # 열린 핸들은 옛 파일을 가리킬 수 있으므로 경로도 다시 조회한다.
    path_after = os.stat(path)
    identity_fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
    if (any(getattr(before, key) != getattr(after, key) for key in identity_fields) or
            any(getattr(after, key) != getattr(path_after, key) for key in identity_fields)):
        raise RuntimeError('image_changed_during_hash')
    return digest.hexdigest()


def scan_running_executable_hashes(catalogue, *, game_pid, game_session_id,
                                   stop_event=None, process_rows=None,
                                   session_lookup=None, owner_snapshot=None,
                                   identity_factory=ProcessIdentity):
    """게임 계정·Windows 세션에서 실행 중인 EXE만 한 번 조사한다.

    먼저 파일 크기로 후보를 좁히고 SHA-256을 계산한다. 프로세스 식별자를
    검사 전후에 확인해 PID 재사용을 피한다. WTS가 소유자 SID를 제공하지
    않은 프로세스는 게임 계정 소유라고 추측하지 않고 별도 계수로 기록한다.
    ``complete``는 *확인된 게임 계정·세션 대상*을 건너뛰지 않았다는 뜻이며,
    소유자 불명 프로세스나 검사 주기 사이의 짧은 실행까지 확인한 뜻은 아니다.
    """
    if type(game_pid) is not int or game_pid <= 0:
        raise ValueError('positive game PID required')
    rows = list_processes() if process_rows is None else process_rows
    owners = process_owner_snapshot() if owner_snapshot is None else owner_snapshot
    game_owner = owners.get(game_pid)
    if game_owner is None:
        raise LookupError('game_process_owner_unavailable')
    if session_lookup is None:
        sessions = process_session_snapshot()
        def session_lookup(pid):
            if pid not in sessions:
                raise LookupError('pid_absent_from_process_session_snapshot')
            return sessions[pid]
    same_session = 0
    same_account_session = 0
    other_account = 0
    owner_unavailable = 0
    size_candidates = 0
    hashed = 0
    skipped = []
    matches = []
    # 같은 이미지 파일을 여러 프로세스가 실행할 수 있어 한 주기 안에서만
    # 해시를 재사용한다. 다음 주기에는 파일 변경 가능성을 고려해 다시 계산한다.
    cache = {}
    for row in rows:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError('hash_scan_stopped')
        pid = row.get('pid')
        if type(pid) is not int or pid <= 0:
            continue
        owner = owners.get(pid)
        try:
            if session_lookup(pid) != game_session_id:
                continue
        except (OSError, LookupError) as exc:
            # 확인된 게임 계정의 PID만 검사 대상이다. 다른 계정이나 소유자
            # 불명 PID의 세션 조회 실패는 별도 범위 공백으로 남긴다.
            if owner == game_owner:
                skipped.append({'pid': pid, 'reason': 'session_unavailable',
                                'error_type': type(exc).__name__})
            elif owner is None:
                owner_unavailable += 1
            else:
                other_account += 1
            continue
        same_session += 1
        if owner is None:
            owner_unavailable += 1
            continue
        if owner != game_owner:
            other_account += 1
            continue
        same_account_session += 1
        try:
            with identity_factory(pid) as process:
                path = process.initial['image_path']
                stat = os.stat(path)
                if stat.st_size not in catalogue['sizes']:
                    process.check()
                    continue
                size_candidates += 1
                key = (path.casefold(), stat.st_dev, stat.st_ino,
                       stat.st_size, stat.st_mtime_ns)
                if key not in cache:
                    cache[key] = hash_stable_image(path, stat.st_size,
                                                   stop_event=stop_event)
                hashed += 1
                process.check()
                ids = catalogue['by_digest'].get((stat.st_size, cache[key]), ())
                if ids:
                    matches.append({'pid': pid, 'image_name': Path(path).name,
                                    'image_path': path,
                                    'creation_time_100ns': process.initial['creation_time_100ns'],
                                    'sha256': cache[key], 'catalogue_ids': list(ids)})
        except InterruptedError:
            raise
        except Exception as exc:
            # 접근 거부·종료·PID 재사용 등은 모두 skipped에 남겨 complete=False로 만든다.
            skipped.append({'pid': pid, 'reason': 'image_inspection_failed',
                            'error_type': type(exc).__name__})
    matches.sort(key=lambda item: item['pid'])
    return {'matches': matches, 'complete': not skipped,
            'process_count': len(rows), 'same_session_count': same_session,
            'same_account_session_count': same_account_session,
            'other_account_count': other_account,
            'owner_unavailable_count': owner_unavailable,
            'size_candidate_count': size_candidates, 'hashed_process_count': hashed,
            'skipped': skipped,
            'scope': 'running_game_account_same_session_executable_disk_sha256',
            'catalogue_sha256': catalogue['catalogue_sha256'],
            'catalogue_entry_count': catalogue['entry_count']}
