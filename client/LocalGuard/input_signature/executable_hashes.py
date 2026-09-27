"""Read-only SHA-256 checks of on-disk images backing running local EXEs.

Exact hashes are strong evidence for one known build, not a generic cheat
detector. A failed inspection is never converted to a clean result.
"""
import hashlib
import json
import os
from pathlib import Path
import re

from windows_process import ProcessIdentity, list_processes, process_session_snapshot


SCHEMA_VERSION = 'meccha-known-executable-hashes-1'
_ID = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
MAX_CATALOGUE_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 256 * 1024 * 1024


def load_blacklist(path):
    """Load a trusted local catalogue; no network lookup or dynamic code."""
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
    """Hash an EXE file handle and reject obvious replacement during reading."""
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
    path_after = os.stat(path)
    identity_fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
    if (any(getattr(before, key) != getattr(after, key) for key in identity_fields) or
            any(getattr(after, key) != getattr(path_after, key) for key in identity_fields)):
        raise RuntimeError('image_changed_during_hash')
    return digest.hexdigest()


def scan_running_executable_hashes(catalogue, *, game_session_id, stop_event=None,
                                   process_rows=None, session_lookup=None,
                                   identity_factory=ProcessIdentity):
    """Inspect running same-session EXEs, filtering by known sizes before hashing.

    `complete` refers to this one snapshot only. Even a complete snapshot can
    miss a process that started and exited between polling intervals.
    """
    rows = list_processes() if process_rows is None else process_rows
    if session_lookup is None:
        sessions = process_session_snapshot()
        def session_lookup(pid):
            if pid not in sessions:
                raise LookupError('pid_absent_from_process_session_snapshot')
            return sessions[pid]
    same_session = 0
    size_candidates = 0
    hashed = 0
    skipped = []
    matches = []
    cache = {}
    for row in rows:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError('hash_scan_stopped')
        pid = row.get('pid')
        if type(pid) is not int or pid <= 0:
            continue
        try:
            if session_lookup(pid) != game_session_id:
                continue
        except (OSError, LookupError) as exc:
            skipped.append({'pid': pid, 'reason': 'session_unavailable',
                            'error_type': type(exc).__name__})
            continue
        same_session += 1
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
            skipped.append({'pid': pid, 'reason': 'image_inspection_failed',
                            'error_type': type(exc).__name__})
    matches.sort(key=lambda item: item['pid'])
    return {'matches': matches, 'complete': not skipped,
            'process_count': len(rows), 'same_session_count': same_session,
            'size_candidate_count': size_candidates, 'hashed_process_count': hashed,
            'skipped': skipped,
            'scope': 'running_same_session_executable_disk_sha256',
            'catalogue_sha256': catalogue['catalogue_sha256'],
            'catalogue_entry_count': catalogue['entry_count']}
