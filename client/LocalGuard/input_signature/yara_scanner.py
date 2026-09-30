"""게임 메모리와 제한된 외부 후보를 읽기 전용 YARA 규칙으로 검사한다.

먼저 알려진 Auto Paint bridge DLL의 매핑 메모리를 확인하고, 기본 모드에서는
일치가 없을 때 게임 프로세스 전체 검사로 넘어간다. 선택 옵션으로 같은
Windows 세션의 Python 후보도 검사한다. 규칙 일치는 *코드 존재의 단서*이지
핵 기능 사용 확정이 아니며, 검사 실패를 정상 0점으로 바꾸지 않는다.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback

from heartbeat import (HeartbeatClient, LOCALGUARD_REQUIRED_COMPONENTS,
                       SCHEMA_VERSION as HEARTBEAT_SCHEMA_VERSION,
                       load_hwid_file)
from executable_hashes import load_blacklist
from hash_monitor import HashMonitor
from replay_events import ReplaySession, ManualMarkers, session_args, check_session_args, json_line
from windows_process import (ProcessIdentity, find_processes, list_processes,
                             list_process_modules, process_session_id,
                             read_process_module)

ROOT = Path(__file__).resolve().parent
# 스크립트 직접 실행 시 sys.path[0]은 이 폴더다. 저장소 루트의 shared 패키지를
# import하려면 루트 경로를 명시해야 하며, 런처 코드 자체는 수정하지 않는다.
REPO_ROOT = ROOT.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.config import ClientConfig
from shared.logger import configure_client, send_detection, flush_client, shutdown_client

GAME = 'PenguinHotel-Win64-Shipping.exe'
EXTERNAL_PYTHON_NAMES = frozenset(('python.exe', 'pythonw.exe'))

# 출력 가능한 문자열은 이미 알려진 규칙의 고정 리터럴만 허용한다. 메모리에서
# 우연히 일치한 바이트나 사용자가 추가한 규칙의 원시 내용을 그대로 출력하지 않는다.
DISPLAY_LITERALS = {
    'MECCHA_Repo_AutoPaint_Bridge': {
        '$dispatcher': ('mesh-first paint requires the async queued dispatcher', 'ascii'),
        '$completed': ('mesh-first paint completed', 'ascii'),
        '$stream': ('mesh-first single-stroke ServerPaintBatch stream prepared', 'ascii'),
    },
    'MECCHA_PRReady_AutoPaint_Bridge': {
        '$pipeline': ('mesh_first_pipeline', 'ascii'),
        '$uv_marker': ('mesh-first-uv-color-', 'wide'),
        '$compact_batch': ('ServerCompactPaintBatch', 'ascii'),
        '$local_brush': ('PaintAtUVWithBrush', 'ascii'),
    },
}


def configure_detection_forwarding(session):
    """세션 Event를 shared 전송 대기열에 연결한다.

    URL/토큰이 모두 없으면 오프라인 로컬 검사로 유지한다. 설정이 일부만
    있거나 잘못됐으면 오류를 알리되 탐지와 로컬 기록은 계속한다.
    ``configure_client``는 시작 시 한 번만 호출하고 Event마다 재생성하지 않는다.
    """
    if not any(name in os.environ for name in
               ('GZZ_TELEMETRY_URL', 'GZZ_TELEMETRY_TOKEN')):
        return False

    # outbox는 프로세스별 단일 송신자가 사용한다. 런처가 별도 경로를 지정하지
    # 않았을 때 다른 탐지기와 같은 DB를 열지 않도록 모듈 전용 기본값을 둔다.
    os.environ.setdefault('GZZ_TELEMETRY_OUTBOX',
                          str(ROOT / 'telemetry-outbox' / 'client.sqlite3'))
    try:
        configure_client(ClientConfig.from_env())
    except Exception as exc:
        print('[중앙 전송 설정 실패]', type(exc).__name__,
              '— 탐지 결과는 로컬 events.jsonl에 계속 기록합니다.',
              file=sys.stderr, flush=True)
        return False

    def forward(event):
        """로컬 저장이 끝난 7필드 Event를 대기열에 넣고 실패를 진단한다."""
        try:
            send_detection(event)
        except Exception as exc:
            # 이미 저장된 증거·점수는 전송 대기열 오류 때문에 덮어쓰지 않는다.
            print('[중앙 전송 대기열 오류]', type(exc).__name__,
                  '— 로컬 events.jsonl은 유지됩니다.',
                  file=sys.stderr, flush=True)

    session.event_sink = forward
    return True


def stop_detection_forwarding(active):
    """종료 시 미전송 항목을 잠시 기다리고 송신 스레드를 정리한다.

    ``flush_client``가 False여도 대기 데이터는 지우지 않는다. 실패는 stderr
    (런처 실행 시 모듈 로그)에 알리고 검사의 점수·로컬 파일은 그대로 둔다.
    """
    if not active:
        return
    try:
        if not flush_client(timeout=3):
            print('[중앙 전송 미완료] 미전송·실패 항목이 대기열에 남아 있습니다.',
                  file=sys.stderr, flush=True)
    except Exception as exc:
        print('[중앙 전송 정리 오류]', type(exc).__name__, file=sys.stderr, flush=True)
    finally:
        try:
            if not shutdown_client(timeout=5):
                print('[중앙 전송 종료 지연] 전송 작업이 아직 진행 중입니다.',
                      file=sys.stderr, flush=True)
        except Exception as exc:
            print('[중앙 전송 종료 오류]', type(exc).__name__, file=sys.stderr, flush=True)


def scanner_stale_window_ms(scan_interval, scan_timeout, heartbeat_interval):
    """긴 YARA 검사 한 번의 주기·제한 시간을 포함하는 신선도 한도를 계산한다.

    하트비트 주기보다 YARA가 오래 걸릴 수 있으므로 즉시 stale로 잘못
    표시하지 않되, 검사가 영원히 멈춰도 healthy로 남지 않게 한다.
    """
    return max(
        int(heartbeat_interval * 3000),
        int((scan_interval + scan_timeout + heartbeat_interval) * 1000),
    )


def select_external_python_candidates(rows, *, game_pid, game_session_id,
                                      scanner_pid, session_lookup):
    """게임과 같은 Windows 세션의 Python 실행 프로세스 후보만 고른다.

    스캐너 자신·런처 부모·게임 PID는 제외한다. 후보 선정은 검사 범위 결정일
    뿐, python.exe라는 이름이나 명령줄만으로 핵이라고 판정하지 않는다.
    같은 Python 실행기 안의 스크립트 이름이 달라져도 후보가 될 수 있다.
    """
    candidates = []
    skipped = []
    seen = set()
    scanner_parent_pid = next((row['parent_pid'] for row in rows
                               if row['pid'] == scanner_pid), None)
    for row in rows:
        pid = row['pid']
        if (type(pid) is not int or pid <= 0 or pid in seen or
                pid in (game_pid, scanner_pid, scanner_parent_pid) or
                row['name'].casefold() not in EXTERNAL_PYTHON_NAMES):
            continue
        seen.add(pid)
        try:
            candidate_session = session_lookup(pid)
        except OSError as exc:
            skipped.append({'pid': pid, 'error_type': type(exc).__name__})
            continue
        if candidate_session == game_session_id:
            candidates.append({'pid': pid, 'name': row['name'],
                               'parent_pid': row['parent_pid']})
    candidates.sort(key=lambda row: row['pid'])
    return candidates, skipped


def candidate_batch(candidates, cursor, limit):
    """후보가 많아도 주기당 상한만 검사하고 다음 주기에는 이어서 고른다."""
    if not candidates: return [], cursor
    count = min(len(candidates), limit)
    start = cursor % len(candidates)
    return ([candidates[(start + offset) % len(candidates)]
             for offset in range(count)], cursor + count)


def load_rules(paths):
    """신뢰하는 로컬 .yar 파일을 크기 제한 후 컴파일하고 규칙 정보를 남긴다.

    include를 금지해 기록된 파일 밖의 규칙이 끼어들지 않게 한다. 규칙별 점수
    범위를 검증하며, 경고를 성공으로 무시하지 않는다.
    """
    import yara
    records = []
    sources = {}
    for i, path in enumerate(paths):
        path = Path(path).resolve()
        data = path.read_bytes()
        if len(data) > 1024 * 1024: raise ValueError('rule_file_too_large')
        records.append({'file': path.name, 'sha256': hashlib.sha256(data).hexdigest()})
        sources['rules_' + str(i)] = data.decode('utf-8-sig')
    # 파일 목록과 해시가 실제 사용 규칙 전체를 설명하도록 include를 막는다.
    rules = yara.compile(sources=sources, includes=False, error_on_warning=True)
    exported = list(rules)
    if not exported: raise ValueError('empty_yara_rules')
    for rule in exported:
        score = rule.meta.get('score', 1)
        if type(score) is not int or not 1 <= score <= 10:
            raise ValueError('rule score must be an integer from 1 to 10')
    return rules, {'engine': 'yara-python', 'engine_version': yara.__version__,
                   'files': records, 'rule_count': len(exported),
                   'test_rules_present': any(r.meta.get('test_only',False) for r in exported)}


def _hits_from_matches(matches):
    """YARA 결과에서 규칙 ID·점수·허용된 문자열 설명만 추출한다.

    ``matched_data``는 게임이나 외부 프로세스의 메모리일 수 있다. 고정된
    허용 목록과 바이트가 정확히 같을 때만 사람이 읽는 문자열로 변환한다.
    """
    hits = []
    for match in matches:
        # 실제 매칭 바이트는 비교에만 사용하고 결과 로그에는 보관하지 않는다.
        matched_strings = []
        known = DISPLAY_LITERALS.get(match.rule, {})
        for pattern in match.strings:
            item = known.get(pattern.identifier)
            if item is None: continue
            value, encoding = item
            expected = value.encode('utf-16le' if encoding == 'wide' else 'ascii')
            if any(instance.matched_data == expected for instance in pattern.instances):
                matched_strings.append({'identifier': pattern.identifier,
                                        'text': value, 'encoding': encoding})
        matched_strings.sort(key=lambda item: item['identifier'])
        hits.append({'rule': match.rule, 'namespace': match.namespace,
                     'score': int(match.meta.get('score', 1)),
                     'test_only': bool(match.meta.get('test_only', False)),
                     'pattern_ids': sorted({s.identifier for s in match.strings}),
                     'matched_strings': matched_strings})
    hits.sort(key=lambda h: (h['namespace'], h['rule']))
    return hits


def format_matched_strings(hits):
    """허용된 고정 리터럴만 콘솔 출력용 문장으로 만든다."""
    return [f"[문자열] {hit['rule']}: " + ', '.join(
                f"{item['identifier']}=\"{item['text']}\" ({item['encoding']})"
                for item in hit['matched_strings'])
            for hit in hits if hit.get('matched_strings')]


def _emit_scan_result(process, session, raw_stream, start, hits, *, scope,
                      coverage, module=None, zero_means=None, scan_method=None,
                      module_inventory_count=None, selection=None):
    """성공한 검사 한 건을 상세 raw 로그와 7필드 공통 Event로 기록한다.

    여러 규칙이 같은 도구를 가리켜 점수가 중복 가산되지 않도록 최고 점수만
    사용한다. ``scope``와 ``zero_means``는 0점의 검사 범위를 명확히 한다.
    """
    end = session.elapsed()
    score = max((h['score'] for h in hits), default=0)  # 같은 코드의 다중 규칙 중복 가산 방지.
    record = {'type': 'scan_result', 'timestamp_ms': end, 'scan_start_ms': start,
              'scan_duration_ms': end - start, 'pid': process.pid, 'matches': hits,
              'score_evaluated': True, 'raw_score': score, 'scope': scope}
    if scan_method: record['scan_method'] = scan_method
    if selection: record['selection'] = selection
    if module_inventory_count is not None:
        record['module_inventory_count'] = module_inventory_count
    if module:
        record['module_name'] = module['name']
        record['module_size'] = module['size']
    json_line(raw_stream, record)
    evidence = {
        'measurement_valid': True, 'scope': scope, 'pid': process.pid,
        'scan_start_ms': start, 'scan_duration_ms': end - start,
        'matched_rules': [h['rule'] for h in hits],
        'rule_match_count': len(hits), 'test_rule_match': any(h['test_only'] for h in hits),
        'score_policy': 'maximum_matched_rule_score_else_zero',
        'identity_verified': False, 'active_cheat_proven': False, 'cheat_confirmed': False,
        'zero_means': zero_means or 'no_rule_match_in_readable_memory_not_proven_clean',
        'coverage': coverage,
    }
    if scan_method: evidence['scan_method'] = scan_method
    if selection: evidence['selection'] = selection
    if module_inventory_count is not None:
        evidence['module_inventory_count'] = module_inventory_count
    if module:
        evidence['module_name'] = module['name']
        evidence['module_size'] = module['size']
    event = session.emit('localguard_yara', session.manifest['player_id'], evidence,
                         ['YARA Rule Matched: ' + h['rule'] for h in hits], score,
                         timestamp_ms=end)
    # 서버로 보낼 7필드 Event가 먼저 완성·전달된다. 그 후 콘솔 전용 필드를
    # 반환 객체에만 붙여 중앙 스키마를 변경하지 않는다.
    event['_matched_strings_for_console'] = format_matched_strings(hits)
    return event


def scan_loaded_autopaint_bridge_once(rules, process, session, raw_stream, *,
                                      timeout=45, bridge_only=False):
    """이름이 알려진 Auto Paint bridge의 *매핑된 메모리*를 검사한다.

    기본 모드는 모듈 부재·불일치 시 전체 프로세스 검사로 넘어가도록 None을
    반환한다. bridge-only 모드의 0점은 알려진 이름의 DLL 범위에 한정된다.
    읽기 실패나 모듈 언로드는 검사 실패로 남고 0점이 되지 않는다.
    """
    start = session.elapsed()
    try:
        import yara
        process.check()
        inventory = list_process_modules(process.pid)
        # 모듈명만으로 치트 판정을 내리지 않고 해당 매핑 메모리를 YARA로 검사한다.
        modules = [module for module in inventory
                   if module['name'].casefold() == 'runtime-bridge.dll' or
                   module['name'].casefold().startswith('meccha-direct-bridge-v1-')]
        if not modules:
            if not bridge_only: return None
            process.check()
            return _emit_scan_result(
                process, session, raw_stream, start, [],
                scope='known_autopaint_bridge_module_inventory',
                coverage='No known named Auto Paint bridge in the loader module list; full memory not scanned',
                zero_means='known_named_bridge_not_loaded_at_observation_time_not_proven_clean',
                scan_method='module_inventory', module_inventory_count=len(inventory))
        if len(modules) > 8: raise RuntimeError('too_many_autopaint_bridge_modules')
        for module in modules:
            data = read_process_module(process.pid, module)
            # YARA 경고는 부분 검사 가능성을 뜻하므로 정상 결과로 채택하지 않는다.
            warnings = []
            def warning_callback(kind, message):
                warnings.append(str(kind))
                return yara.CALLBACK_ABORT
            matches = rules.match(data=data, timeout=min(timeout, 10), fast=True,
                                  warnings_callback=warning_callback)
            if warnings: raise RuntimeError('yara_incomplete_module_scan_warning:' + ','.join(warnings))
            if bridge_only:
                matches = [match for match in matches if match.rule in DISPLAY_LITERALS]
            process.check()
            # 스캔 도중 언로드·재로드된 다른 DLL을 같은 대상으로 착각하지 않도록
            # 주소·크기·경로를 다시 대조한다.
            still_loaded = any(m['base_address'] == module['base_address'] and
                               m['size'] == module['size'] and m['path'] == module['path']
                               for m in list_process_modules(process.pid))
            if not still_loaded: raise RuntimeError('module_unloaded_during_scan')
            hits = _hits_from_matches(matches)
            if hits:
                return _emit_scan_result(
                    process, session, raw_stream, start, hits,
                    scope='loaded_autopaint_bridge_module_memory',
                    coverage='Mapped loaded bridge image bytes only; not a full-process scan',
                    module=module, scan_method='yara_module_memory',
                    module_inventory_count=len(inventory))
        if bridge_only:
            return _emit_scan_result(
                process, session, raw_stream, start, [],
                scope='loaded_autopaint_bridge_module_memory',
                coverage='Known named bridge modules scanned; full memory not scanned',
                zero_means='no_autopaint_rule_match_in_named_bridge_modules_not_proven_clean',
                scan_method='yara_module_memory', module_inventory_count=len(inventory))
    except Exception as exc:
        json_line(raw_stream, {'type': 'scan_error' if bridge_only else 'module_scan_error',
                               'timestamp_ms': session.elapsed(), 'scan_start_ms': start,
                               'pid': process.pid, 'error_type': type(exc).__name__,
                               'error': str(exc), 'score_evaluated': False,
                               'fallback': 'none' if bridge_only else 'full_process_scan'})
        # 기본 모드에서는 후속 전체 프로세스 검사가 성공할 수 있으므로
        # 여기서 세션 전체 실패로 확정하지 않는다.
        if bridge_only: session.error('yara_scan_failed')
    return None


def scan_once(rules, process, session, raw_stream, *, timeout=45,
              scope='selected_local_process_memory', selection=None):
    """선택한 PID의 읽을 수 있는 메모리를 YARA로 검사한다.

    완전하게 끝난 관측은 일치가 없어도 범위가 명시된 0점 Event를 남긴다.
    접근 거부·타임아웃·YARA 경고·PID 변경은 오류만 남기고 0점은 내지 않는다.
    """
    start = session.elapsed()
    try:
        import yara
        process.check()
        warnings = []
        def warning_callback(kind, message):
            warnings.append(str(kind))
            return yara.CALLBACK_ABORT
        matches = rules.match(pid=process.pid, timeout=timeout, fast=True, warnings_callback=warning_callback)
        process.check()
        if warnings: raise RuntimeError('yara_incomplete_scan_warning:' + ','.join(warnings))
        hits = _hits_from_matches(matches)
    except Exception as exc:
        record = {'type': 'scan_error', 'timestamp_ms': session.elapsed(), 'scan_start_ms': start,
                  'pid': process.pid, 'error_type': type(exc).__name__, 'error': str(exc),
                  'score_evaluated': False}
        json_line(raw_stream, record)
        session.error('yara_scan_failed')
        # 검사 공백을 '정상'이라고 오인하지 않도록 공통 0점 Event를 만들지 않는다.
        return None
    return _emit_scan_result(
        process, session, raw_stream, start, hits,
        scope=scope,
        coverage='YARA readable memory; not an atomic snapshot or proof of full-process coverage',
        selection=selection)


def scan_external_python_candidates(rules, game_process, session, raw_stream, *,
                                    timeout, max_targets, cursor, scanned_pids=None):
    """같은 Windows 세션의 Python 후보를 제한된 수만큼 찾아 검사한다.

    발견한 전체/이번 주기/다음 주기 PID를 raw 로그에 기록한다. 후보 부재는
    외부 핵이 없다는 증명이 아니며 접근 실패는 정상 0점으로 치지 않는다.
    """
    try:
        game_process.check()
        game_session_id = process_session_id(game_process.pid)
        rows = list_processes()
        candidates, skipped = select_external_python_candidates(
            rows, game_pid=game_process.pid, game_session_id=game_session_id,
            scanner_pid=os.getpid(), session_lookup=process_session_id)
    except Exception as exc:
        json_line(raw_stream, {'type': 'candidate_discovery_error',
                               'timestamp_ms': session.elapsed(),
                               'error_type': type(exc).__name__,
                               'score_evaluated': False})
        session.error('external_candidate_discovery_failed')
        print('[외부후보] 탐색 실패: 정상 0점으로 기록하지 않았습니다.')
        return cursor, 1
    # 한 주기에 모든 Python 프로세스를 무제한 스캔하지 않도록 순환 선택한다.
    selected, cursor = candidate_batch(candidates, cursor, max_targets)
    candidate_pids = [item['pid'] for item in candidates]
    selected_pids = [item['pid'] for item in selected]
    deferred_pids = [pid for pid in candidate_pids if pid not in selected_pids]
    json_line(raw_stream, {'type': 'candidate_discovery',
                           'timestamp_ms': session.elapsed(),
                           'game_pid': game_process.pid,
                           'game_session_id': game_session_id,
                           'candidate_count': len(candidates),
                           'candidate_pids': candidate_pids,
                           'selected_pids': selected_pids,
                           'deferred_count': len(candidates) - len(selected),
                           'session_lookup_skipped': skipped,
                           'score_evaluated': False})
    print(f"[외부후보] 같은 세션 Python PID ({len(candidate_pids)}개): "
          + (', '.join(map(str, candidate_pids)) or '없음'))
    print('[외부후보] 이번 주기 검사 PID: '
          + (', '.join(map(str, selected_pids)) or '없음')
          + (f" | 다음 주기 대기: {', '.join(map(str, deferred_pids))}"
             if deferred_pids else ''))
    failures = len(skipped)
    if skipped: session.error('external_candidate_session_unavailable')
    for item in selected:
        pid = item['pid']
        try:
            # 스냅샷 이후 PID가 종료·재사용될 수 있어 실제 검사 직전에 재확인한다.
            if process_session_id(pid) != game_session_id:
                raise RuntimeError('candidate_session_changed')
            with ProcessIdentity(pid) as candidate:
                if (Path(candidate.initial['image_path']).name.casefold()
                        not in EXTERNAL_PYTHON_NAMES):
                    raise RuntimeError('candidate_image_changed')
                json_line(raw_stream, {'type': 'external_candidate_identity',
                                       'timestamp_ms': session.elapsed(),
                                       'pid': pid,
                                       'image_name': Path(candidate.initial['image_path']).name,
                                       'creation_time_100ns': candidate.initial['creation_time_100ns'],
                                       'score_evaluated': False})
                event = scan_once(rules, candidate, session, raw_stream,
                                  timeout=timeout,
                                  scope='same_session_external_python_memory')
            if event is None:
                failures += 1
                print(f'[외부후보 PID={pid}] 검사 실패: 정상 0점이 아닙니다.')
            else:
                if scanned_pids is not None: scanned_pids.add(pid)
                summary = ', '.join(event['evidence']['matched_rules']) or '일치 규칙 없음'
                print(f"[외부후보 PID={pid}] score={event['raw_score']} | {summary}")
        except Exception as exc:
            failures += 1
            json_line(raw_stream, {'type': 'external_candidate_error',
                                   'timestamp_ms': session.elapsed(), 'pid': pid,
                                   'error_type': type(exc).__name__,
                                   'score_evaluated': False})
            session.error('external_candidate_scan_failed')
            print(f'[외부후보 PID={pid}] 검사 불가: 정상 0점이 아닙니다.')
    return cursor, failures


def main(argv=None):
    """CLI 진입점: 세션 준비 → 상태·전송 시작 → 반복 검사 → 안전한 종료.

    YARA·파일 해시 결과는 각각 공통 Event를 만들고, 하트비트는 생존 상태만
    전한다. 서버 설정이 없으면 검사는 로컬 기록만으로도 실행 가능하다.
    """
    # 런처의 Ctrl+Break 종료 요청을 KeyboardInterrupt로 받아 아래 finally에서
    # 전송 대기열과 manifest를 정리한다. Windows 이외 환경에는 SIGBREAK가 없다.
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    parser = argparse.ArgumentParser(description='LocalGuard: read-only YARA process-memory scan / ReplayAnalyzer export')
    # 검사 범위: 명시 PID 또는 게임 자동 탐색, 알려진 DLL 우선 검사,
    # 선택적으로 같은 세션 Python 후보까지 확인한다.
    parser.add_argument('--pid', type=int, help='Explicit local process PID; default selects exactly one MECCHA process')
    parser.add_argument('--rules', type=Path, action='append', help='Trusted .yar file; repeat to add files')
    parser.add_argument('--scan-mode', choices=['auto', 'autopaint-bridge'], default='auto',
                        help='auto: bridge positive first, else full process; autopaint-bridge: named bridge modules only')
    parser.add_argument('--auto-external-python', action='store_true',
                         help='While the game runs, discover and scan same-session Python processes')
    parser.add_argument('--external-max-targets', type=int, default=4,
                        help='Maximum candidates per external source scanned per interval (1..16)')
    parser.add_argument('--external-timeout', type=int, default=15,
                         help='YARA timeout for each external process, seconds (1..120)')
    parser.add_argument('--hash-blacklist', type=Path,
                        default=ROOT / 'rules' / 'known_cheat_executables.json',
                        help='Trusted local JSON of exact known cheat EXE SHA-256 values')
    parser.add_argument('--hash-interval', type=float, default=5,
                        help='Seconds between running-EXE hash scans (5..60)')
    parser.add_argument('--no-executable-hash', action='store_true',
                        help='Disable the running-EXE SHA-256 monitor')
    # 검사 소요·종료 조건. seconds=0이면 사용자가 중단하거나 게임이 끝날 때까지
    # 반복하며, 한 주기 안의 실패와 부분 검사는 0점으로 채우지 않는다.
    parser.add_argument('--interval', type=float, default=60, help='Minimum seconds between scan starts')
    parser.add_argument('--timeout', type=int, default=45, help='YARA timeout for each scan, seconds')
    parser.add_argument('--seconds', type=float, default=0,
                        help='0 until Ctrl+C; otherwise scanner runtime, independent of --t0')
    parser.add_argument('--log-root', type=Path, default=ROOT / 'sessions')
    # 하트비트 설정은 탐지 Event용 GZZ_TELEMETRY_* 설정과 독립이다.
    # 실제 /api/heartbeat 수신 계약과 HWID 생산 형식은 아직 임시다.
    parser.add_argument('--heartbeat-url',
                        default=os.environ.get('MECCHA_TELEMETRY_HEARTBEAT_URL'),
                        help='Central receiver URL; can also use MECCHA_TELEMETRY_HEARTBEAT_URL')
    parser.add_argument('--hwid-file', type=Path,
                        default=os.environ.get('MECCHA_HWID_FILE'),
                        help='Component 1 JSON file with a precomputed SHA-256 HWID')
    parser.add_argument('--heartbeat-interval', type=float, default=5,
                        help='Heartbeat interval in seconds (5..10); local JSONL is always recorded')
    parser.add_argument('--heartbeat-timeout', type=float, default=3,
                        help='Network POST timeout in seconds; must be shorter than heartbeat interval')
    session_args(parser)
    args = parser.parse_args(argv)
    # 잘못된 설정은 세션이나 대상 핸들을 만들기 전에 거부한다.
    check_session_args(parser, args)
    if not .1 <= args.interval <= 3600: parser.error('--interval must be 0.1..3600')
    if not 1 <= args.timeout <= 120: parser.error('--timeout must be 1..120')
    if not 1 <= args.external_max_targets <= 16: parser.error('--external-max-targets must be 1..16')
    if not 1 <= args.external_timeout <= 120: parser.error('--external-timeout must be 1..120')
    if not 5 <= args.hash_interval <= 60: parser.error('--hash-interval must be 5..60')
    if args.auto_external_python and args.scan_mode != 'auto':
        parser.error('automatic external discovery requires --scan-mode auto')
    if not 0 <= args.seconds <= 86400: parser.error('--seconds must be 0..86400')
    if not 5 <= args.heartbeat_interval <= 10: parser.error('--heartbeat-interval must be 5..10')
    if not .5 <= args.heartbeat_timeout <= 30: parser.error('--heartbeat-timeout must be 0.5..30')
    if args.heartbeat_url and args.heartbeat_timeout >= args.heartbeat_interval:
        parser.error('--heartbeat-timeout must be shorter than --heartbeat-interval')
    if args.heartbeat_url and not args.hwid_file:
        parser.error('--heartbeat-url requires --hwid-file or MECCHA_HWID_FILE')
    if args.pid is not None and args.pid <= 0: parser.error('--pid must be positive')
    for out in (sys.stdout, sys.stderr):
        if hasattr(out, 'reconfigure'): out.reconfigure(encoding='utf-8', errors='replace')
    try:
        session = ReplaySession.from_args(
            args, modules=['localguard_yara'] +
            ([] if args.no_executable_hash else ['localguard_executable_hash']))
    except Exception as exc:
        print('세션 생성 실패 (기존 폴더는 덮어쓰지 않습니다):', exc)
        return 1
    raw = (session.raw / 'yara_scan.jsonl').open('x', encoding='utf-8')
    markers = ManualMarkers(session)
    heartbeat = None
    game_monitor = None
    hash_game_monitor = None
    hash_monitor = None
    exit_code = 0
    detection_forwarding_active = False
    # 전체 메모리 검사와 외부 후보 검사는 하트비트 주기보다 오래 걸릴 수 있다.
    # 실제 검사 일정에 맞춰 신선도 한도를 계산한다.
    scanner_stale_ms = scanner_stale_window_ms(
        args.interval,
        (min(args.timeout, 10) if args.scan_mode == 'autopaint-bridge' else args.timeout)
        + (args.external_max_targets * args.external_timeout
           if args.auto_external_python else 0),
        args.heartbeat_interval)
    if args.scan_mode == 'autopaint-bridge':
        print('LocalGuard Auto Paint bridge — 모듈 목록 확인 후 로드된 bridge 메모리만 YARA 검사합니다. 주입/차단/밴 없음.')
    else:
        print('LocalGuard YARA — 지정한 내 PC 프로세스 메모리만 읽습니다. 주입/차단/밴 없음.')
    print('세션:', session.path)
    print('규칙 일치는 코드/패턴 존재의 단서이며 핵 ON/OFF 판정이 아닙니다.')
    if args.seconds:
        print(f'종료 기준: 검사기 시작 후 {args.seconds:g}초 경과, 현재 검사 주기 완료 후 종료')
    else:
        print('종료 기준: Ctrl+C (게임 종료 또는 반복 검사 실패 시 오류 종료)')
    try:
        # Event 전송은 로컬 세션이 만들어진 뒤 한 번만 설정한다.
        detection_forwarding_active = configure_detection_forwarding(session)
        hwid = load_hwid_file(args.hwid_file) if args.hwid_file else None
        # 하트비트는 탐지 점수가 아니라 스캐너·해시 검사·게임의 생존을 보고한다.
        heartbeat = HeartbeatClient(
            session_id=session.manifest['session_id'],
            player_id=session.manifest['player_id'],
            log_path=session.raw / 'heartbeat.jsonl',
            endpoint=args.heartbeat_url,
            token=os.environ.get('MECCHA_HEARTBEAT_TOKEN'),
            hwid=hwid,
            interval_seconds=args.heartbeat_interval,
            timeout_seconds=args.heartbeat_timeout,
            clock=session.clock,
            origin_ms=session.origin,
            required_components=(LOCALGUARD_REQUIRED_COMPONENTS +
                                 (() if args.no_executable_hash else ('localguard_file_hash',))),
        )
        heartbeat.update_component(
            'localguard_input_signature', 'starting', pid=os.getpid(),
            details={'scanner': 'yara', 'detection_channel_separate': True})
        if not args.no_executable_hash:
            heartbeat.update_component(
                'localguard_file_hash', 'starting', pid=os.getpid(),
                details={'scanner': 'sha256_running_executable_images',
                         'awaiting_first_scan': True})
        heartbeat.start()
        session.update(
             heartbeat={'schema_version': HEARTBEAT_SCHEMA_VERSION,
                        'interval_seconds': args.heartbeat_interval,
                        'receiver_configured': bool(args.heartbeat_url),
                        'hwid_configured': bool(hwid),
                        'token_configured': bool(os.environ.get('MECCHA_HEARTBEAT_TOKEN'))},
            files=dict(session.manifest['files'], heartbeat='raw/heartbeat.jsonl',
                       **({} if args.no_executable_hash else
                          {'executable_hashes': 'raw/executable_hashes.jsonl'})))
        # 실제 사용한 규칙 파일의 해시와 엔진 버전을 manifest에 남겨 재현성을
        # 확보한다. 규칙을 읽지 못하면 정상 0점이 아닌 실행 실패다.
        rules, rule_manifest = load_rules(args.rules or [ROOT / 'rules' / 'repository_cheats.yar'])
        hash_catalogue = (None if args.no_executable_hash else
                          load_blacklist(args.hash_blacklist))
        if args.scan_mode == 'autopaint-bridge' and not any(
                rule.identifier in DISPLAY_LITERALS for rule in rules):
            raise ValueError('autopaint-bridge mode requires an Auto Paint bridge YARA rule')
        heartbeat.update_component(
            'localguard_input_signature', 'running', pid=os.getpid(),
            stale_after_ms=scanner_stale_ms,
            details={'scanner': 'yara', 'rule_count': rule_manifest['rule_count'],
                     'engine_version': rule_manifest['engine_version'],
                     'detection_channel_separate': True})
        session.update(yara=rule_manifest, test_rules_present=rule_manifest['test_rules_present'],
                        executable_hash_blacklist=(None if hash_catalogue is None else {
                            'entry_count': hash_catalogue['entry_count'],
                            'catalogue_sha256': hash_catalogue['catalogue_sha256'],
                            'scan_interval_seconds': args.hash_interval,
                            'scope': 'running_same_session_executable_disk_sha256',
                            'known_builds_only': True}),
                       scan_interval_seconds=args.interval, scan_timeout_seconds=args.timeout,
                       scan_mode=args.scan_mode,
                        external_python_auto_discovery={
                            'enabled': args.auto_external_python,
                            'scope': 'same_windows_session_python_executables_only',
                            'max_targets_per_cycle': args.external_max_targets,
                            'timeout_seconds_per_target': args.external_timeout,
                            'candidate_absence_not_proof_of_no_external_cheat': True},
                        scan_strategy=('named_autopaint_bridge_inventory_and_module_memory_only'
                                       if args.scan_mode == 'autopaint-bridge' else
                                       'loaded_autopaint_bridge_positive_first_else_full_process'
                                       + ('_plus_same_session_external_python'
                                          if args.auto_external_python else '')),
                       scan_scope_warning=('Bridge-only zero means no matching named module or no match in its mapped image; full process not scanned.'
                                           if args.scan_mode == 'autopaint-bridge' else None),
                       detection_target='known_code_presence_not_feature_activity',
                       ground_truth_warning='Manual ON/OFF describes feature use; DLL code can remain resident after OFF.')
        # 정확히 한 게임 프로세스를 선택해야 검사 결과가 어느 대상의 것인지
        # 모호해지지 않는다. 명시 PID를 줬더라도 실제 이미지와 생성 시각을 본다.
        pids = [args.pid] if args.pid else find_processes(GAME)
        if len(pids) != 1:
            heartbeat.update_component('game', 'failed',
                                       details={'matching_process_count': len(pids)})
            raise RuntimeError('게임을 실행하거나 --pid로 검사할 내 PC 프로세스 하나를 지정하세요.')
        with ProcessIdentity(pids[0]) as process:
            # YARA, 해시, 하트비트가 같은 게임 프로세스를 보고 있는지 확인한다.
            game_monitor = ProcessIdentity(pids[0])
            if game_monitor.initial != process.initial:
                raise RuntimeError('game_identity_changed_during_monitor_setup')
            is_game = Path(process.initial['image_path']).name.casefold() == GAME.casefold()
            if args.auto_external_python and not is_game:
                raise RuntimeError('자동 외부 탐색은 게임 PID를 대상으로 실행해야 합니다.')
            heartbeat.update_component(
                'game', 'running', pid=process.pid,
                details={'identity_verified': True, 'expected_image_name': GAME,
                         'selected_image_matches': is_game})
            def game_probe():
                game_monitor.check()
                return {'status': 'running', 'pid': game_monitor.pid,
                        'details': {'identity_verified': True,
                                    'expected_image_name': GAME,
                                    'selected_image_matches': is_game}}
            heartbeat.register_probe('game', game_probe)
            session.update(process=process.initial, data_origin='live_game' if is_game else 'local_process_test')
            print(f'[게임] 검사 대상 PID={process.pid}')
            if hash_catalogue is not None:
                # 파일 해시 검사는 별도 스레드이므로 느린 YARA 주기에도 독립적이다.
                hash_game_monitor = ProcessIdentity(process.pid)
                if hash_game_monitor.initial != process.initial:
                    raise RuntimeError('game_identity_changed_during_hash_setup')
                hash_monitor = HashMonitor(
                    catalogue=hash_catalogue, game_process=hash_game_monitor,
                    session=session, heartbeat=heartbeat,
                    interval_seconds=args.hash_interval)
                hash_monitor.start()
                print(f"[파일 해시] 알려진 EXE {hash_catalogue['entry_count']}개 기준, "
                      f'{args.hash_interval:g}초 간격으로 실행 중 이미지 검사')
            if args.hotkeys: markers.start()
            failed = 0
            external_cursor = 0
            while not args.seconds or session.run_elapsed() < args.seconds * 1000:
                # 백그라운드 검사기의 치명적 오류를 먼저 확인하고 이번 주기 시작.
                heartbeat.check_background()
                if hash_monitor: hash_monitor.check_background()
                markers.check()
                begin = time.monotonic()
                scanned_external_pids = set()
                event = scan_loaded_autopaint_bridge_once(
                    rules, process, session, raw, timeout=args.timeout,
                    bridge_only=args.scan_mode == 'autopaint-bridge')
                # 기본 모드에서 bridge가 없거나 일치하지 않으면 게임 전체의
                # 읽을 수 있는 메모리로 범위를 넓힌다.
                if event is None and args.scan_mode == 'auto':
                    event = scan_once(rules, process, session, raw, timeout=args.timeout)
                if event is None:
                    # 실패한 평가에는 공통 정상 0점을 만들지 않는다. 반복 실패는
                    # 관측 불가 상태로 종료해 운영자가 알아볼 수 있게 한다.
                    failed += 1
                    heartbeat.update_component(
                        'localguard_input_signature', 'degraded', pid=os.getpid(),
                        details={'scanner': 'yara', 'rule_count': rule_manifest['rule_count'],
                                 'consecutive_scan_failures': failed,
                                 'detection_channel_separate': True})
                    print('[검사 실패] raw/yara_scan.jsonl 확인. 정상 0점으로 기록하지 않았습니다.')
                    if failed >= 3: raise RuntimeError('three_consecutive_scan_failures')
                else:
                    failed = 0
                    heartbeat.update_component(
                        'localguard_input_signature', 'running', pid=os.getpid(),
                        details={'scanner': 'yara', 'rule_count': rule_manifest['rule_count'],
                                 'consecutive_scan_failures': 0,
                                 'last_scan_completed_ms': event['timestamp_ms'],
                                 'detection_channel_separate': True})
                    if event['evidence']['scope'] == 'known_autopaint_bridge_module_inventory':
                        summary = '알려진 Auto Paint bridge DLL 미로드 (전체 메모리 미검사)'
                    else:
                        summary = ', '.join(event['evidence']['matched_rules']) or '일치 규칙 없음'
                    print(f"[평가] {event['timestamp_ms']} ms | score={event['raw_score']} | {summary}")
                    # Details live in the local raw scan log, not in the
                    # common event sent to the central receiver.
                    for line in event.get('_matched_strings_for_console', ()):
                        print(line)
                if args.auto_external_python:
                    # 외부 도구를 자동으로 찾는 옵션은 Python 실행기 후보에만
                    # 적용된다. 후보 이름이나 존재 자체를 치트 판정으로 쓰지 않는다.
                    external_cursor, external_failures = scan_external_python_candidates(
                        rules, process, session, raw, timeout=args.external_timeout,
                        max_targets=args.external_max_targets, cursor=external_cursor,
                        scanned_pids=scanned_external_pids)
                    if external_failures:
                        heartbeat.update_component(
                            'localguard_input_signature', 'degraded', pid=os.getpid(),
                            details={'scanner': 'yara', 'external_scan_failures': external_failures,
                                     'detection_channel_separate': True})
                    elif event is not None:
                        heartbeat.update_component(
                            'localguard_input_signature', 'running', pid=os.getpid(),
                            details={'scanner': 'yara', 'external_scan_failures': 0,
                                      'last_scan_completed_ms': session.elapsed(),
                                      'detection_channel_separate': True})
                session.write_manifest()
                while time.monotonic() - begin < args.interval:
                    heartbeat.check_background()
                    if hash_monitor: hash_monitor.check_background()
                    markers.check()
                    if args.seconds and session.run_elapsed() >= args.seconds * 1000: break
                    time.sleep(.05)
    except KeyboardInterrupt:
        if heartbeat:
            heartbeat.update_component('localguard_input_signature', 'stopped', pid=os.getpid(),
                                        details={'reason': 'operator_requested_stop'})
        print('검사를 종료합니다. 마지막 ON의 OFF 시각을 자동 추정하지 않습니다.')
    except Exception as exc:
        exit_code = 1
        if heartbeat:
            try:
                heartbeat.update_component('localguard_input_signature', 'failed', pid=os.getpid(),
                                            details={'error_type': type(exc).__name__})
            except Exception:
                pass
        session.error('runner_failed')
        json_line(raw, {'type': 'fatal_error', 'timestamp_ms': session.elapsed(),
                        'error': str(exc), 'traceback': traceback.format_exc()})
        print('검사 불가:', exc)
        print('보안 프로그램을 끄지 마세요. 접근 불가는 검사 실패로 남습니다.')
    finally:
        # 종료 순서: 입력 표식·해시 워커 중지 → 최종 하트비트 → 중앙 Event
        # 대기열 정리 → 로컬 파일·manifest 닫기. 미전송 데이터는 삭제하지 않는다.
        markers.stop()
        if markers.failure:
            exit_code = 1; session.error('marker_failed')
        if hash_monitor:
            try:
                hash_monitor.stop()
            except Exception as exc:
                exit_code = 1
                session.error('hash_monitor_failed')
                json_line(raw, {'type': 'hash_monitor_error',
                                'timestamp_ms': session.elapsed(),
                                'error_type': type(exc).__name__,
                                'score_evaluated': False})
        if hash_game_monitor:
            hash_game_monitor.close()
        if heartbeat:
            try:
                final_component_state = 'stopped' if exit_code == 0 else 'failed'
                heartbeat.update_component(
                    'localguard_input_signature', final_component_state, pid=os.getpid(),
                    details={'exit_code': exit_code, 'detection_channel_separate': True})
                if not args.no_executable_hash:
                    heartbeat.update_component(
                        'localguard_file_hash', final_component_state, pid=os.getpid(),
                        details={'exit_code': exit_code,
                                 'scanner': 'sha256_running_executable_images'})
                heartbeat.stop(final_status='stopped' if exit_code == 0 else 'failed')
            except Exception as exc:
                exit_code = 1
                session.error('heartbeat_failed')
                json_line(raw, {'type': 'heartbeat_error', 'timestamp_ms': session.elapsed(),
                                'error_type': type(exc).__name__, 'score_evaluated': False})
        if game_monitor and (not heartbeat or not heartbeat.thread or
                             not heartbeat.thread.is_alive()):
            game_monitor.close()
        stop_detection_forwarding(detection_forwarding_active)
        raw.close()
        session.finish(exit_code)
    print('결과 폴더:', session.path)
    print('실제 정상/핵 비교 준비:', session.manifest['comparison_ready'])
    return exit_code


if __name__ == '__main__': raise SystemExit(main())
