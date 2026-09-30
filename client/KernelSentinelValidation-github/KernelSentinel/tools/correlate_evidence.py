"""Offline evidence correlation. Edges never establish causation or a cheat verdict."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import ntpath
from pathlib import Path

EPOCH = 116444736000000000

def stamp(e):
    if e.get('timestamp_100ns'):
        return int(e['timestamp_100ns'])
    if e.get('utc_filetime'):
        return int(e['utc_filetime'])
    return int(e.get('unix_ms', e.get('collected_unix_ms', 0))) * 10000 + EPOCH

def utc(t):
    seconds, fraction = divmod(t - EPOCH, 10_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{fraction:07d}Z'

def iso_ticks(s):
    d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('external timestamp requires UTC offset')
    delta = d.astimezone(timezone.utc) - datetime(1601, 1, 1, tzinfo=timezone.utc)
    return (delta.days*86400 + delta.seconds)*10_000_000 + delta.microseconds*10

def basename(path):
    return ntpath.basename(path.replace('/', '\\')).casefold()

def read_jsonl(path):
    result = []
    with Path(path).open(encoding='utf-8-sig') as f:
        for line, text in enumerate(f, 1):
            if not text.strip():
                continue
            try:
                e = json.loads(text)
                if not isinstance(e, dict): raise ValueError('expected object')
                result.append((line, e))
            except (ValueError, TypeError) as ex:
                raise ValueError(f'{path}: line {line}: {ex}') from ex
    return result

def coordinates(path):
    result = []
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        required = {'access_utc','pid','creation_utc','stage','status','x','y','z'}
        if not required <= set(reader.fieldnames or []): raise ValueError('coordinate CSV columns missing')
        for line, e in enumerate(reader, 2):
            if e.get('access_utc') == 'access_utc': continue  # source app appends headers on each run
            try:
                target = int(e['pid'])
                created = iso_ticks(e['creation_utc'])
                access = iso_ticks(e['access_utc']) if e['access_utc'] != '-' else None
                ok = e['stage'] == 'complete' and int(e['status'],0) == 0
                xyz = tuple(float(e[k]) for k in ('x','y','z')) if ok else None
                ok = ok and all(math.isfinite(x) for x in xyz)
                result.append(dict(line=line, target_pid=target, target_create_time=created,
                                   time=access, success=ok, xyz=xyz if ok else None,
                                   stage=e['stage'], status=e['status']))
            except (ValueError, TypeError) as ex:
                raise ValueError(f'coordinate CSV line {line}: {ex}') from ex
    return result

def match_call(pre, calls):
    """API interval containment is a candidate, never a native call-stack proof."""
    if not pre.get('actor_tid') or not pre.get('actor_create_time'):
        return {'status':'unidentified', 'reason':'requester_thread_or_creation_time_missing', 'candidates':[]}
    groups = {}
    for line, e in calls:
        if e.get('type') != 'client_call': continue
        key = (e.get('pid'),e.get('process_create_time'),e.get('tid'),e.get('call_id'))
        groups.setdefault(key, {}).setdefault(e.get('phase'), []).append((line,e))
    matches = []
    for key, phases in groups.items():
        if key[:3] != (pre['actor_pid'],pre['actor_create_time'],pre['actor_tid']): continue
        if len(phases.get('begin',[])) != 1 or len(phases.get('end',[])) != 1: continue
        bl, b = phases['begin'][0]; el, e = phases['end'][0]
        if b.get('api') != e.get('api') or b.get('target_pid') != pre['target_pid'] or e.get('target_pid') != pre['target_pid']: continue
        if not b.get('qpc_frequency') or b['qpc_frequency'] != e.get('qpc_frequency'): continue
        if stamp(b) <= stamp(pre) <= stamp(e) and b.get('qpc',0) <= e.get('qpc',0):
            matches.append({'api':b['api'], 'call_id':key[3], 'begin_line':bl,'end_line':el})
    return {'status':'api_interval_candidate' if matches else 'unidentified',
            'reason':'interval_does_not_identify_native_stack', 'candidates':matches}

def correlate(raw, driver_name, client_name, coord=None, calls=None, window_seconds=900):
    if not 0 <= window_seconds <= 86400: raise ValueError('correlation window must be 0..86400 seconds')
    starts = [(n,e) for n,e in raw if e.get('type')=='session_start']
    if len(starts) != 1: raise ValueError('exactly one session_start required; do not merge sessions')
    session = starts[0][1]; game = session.get('pid'); created = session.get('create_time')
    if not game or not created: raise ValueError('game PID and creation time required; kernel-only sessions cannot establish game identity')
    end = max((stamp(e) for _,e in raw),default=stamp(session))
    ordered = sorted(raw,key=lambda pair:(stamp(pair[1]),pair[0]))
    timeline, edges, warnings = [], [], []
    def node(n,e,kind=None):
        ref=f'raw:{n}'
        timeline.append(dict(id=ref, source='sentinel_raw',line=n,utc=utc(stamp(e)),
                             timestamp_basis='kernel_event' if e.get('timestamp_100ns') else 'collector_wall_clock',
                             time_100ns=stamp(e), fact=kind or e['type'], evidence=e))
        return ref
    def edge(a,b,kind,basis,limitation):
        edges.append(dict(source=a,target=b,relation=kind,basis=basis,
                          limitation=limitation,cheat_verdict=False,score_delta=0))
    loads=[]; proc=[]
    # Collect process lifetimes for ALL PIDs, not just the selected name, to avoid PID reuse joins.
    active={}
    for n,e in ordered:
        k=e.get('type'); pid=e.get('target_pid')
        if k=='process_create':
            if pid in active: active[pid]['end']=stamp(e)
            p=dict(line=n,event=e,start=stamp(e),end=end+1,created=e.get('process_create_time'),
                   selected=basename(e.get('path',''))==client_name.casefold())
            proc.append(p); active[pid]=p
            if p['selected']: node(n,e)
        elif k=='process_exit':
            p=active.get(pid)
            if p and e.get('process_create_time') == p['created']:
                p['end']=stamp(e)
                if p['selected']: node(n,e)
                del active[pid]
        elif k=='kernel_image' and basename(e.get('path',''))==driver_name.casefold():
            ref=node(n,e);loads.append((n,e,ref))
    # Prefer source sequence + generation. Basename alone never links a hash to a load.
    files=[]; snapshots=[]; linked_files=set()
    for n,e in ordered:
        k=e.get('type')
        if k=='file_evidence' and e.get('image_kind')=='kernel_image' and basename(e.get('path',''))==driver_name.casefold():
            ref=node(n,e);files.append((n,e))
            matches=[(ln,le,lr) for ln,le,lr in loads if e.get('source_sequence') is not None and e.get('generation') is not None and
                     stamp(e) >= stamp(le) and
                     e['source_sequence']==le.get('sequence') and e.get('generation')==le.get('generation')]
            if len(matches)==1:
                linked_files.add(n)
                edge(matches[0][2],ref,'file_analysis_for_load','source_sequence_and_generation',
                     'disk_file_at_analysis_time_not_loaded_memory_identity')
        elif k=='driver_snapshot' and basename(e.get('path',''))==driver_name.casefold():
            ref=node(n,e);snapshots.append((n,e))
            prior=[x for x in loads if stamp(x[1]) <= stamp(e)]
            if prior:
                _,le,lr=prior[-1]
                if (e.get('base'),e.get('size'))==(le.get('image_base'),le.get('image_size')):
                    edge(lr,ref,'module_inventory_match','same_base_size_after_load',
                         'address_reuse_possible_without_unload_identity')
    for p in proc:
        if not p['selected']:continue
        for n,e,ref in loads:
            delta=(p['start']-stamp(e))/10_000_000
            if 0 <= delta <= window_seconds:
                edge(ref,f"raw:{p['line']}",'temporal_candidate',f'load_before_process_by_{delta:.7f}_seconds',
                     'time_proximity_does_not_prove_process_uses_driver; installers_and_tools_can_coincide')
    def owner(e):
        candidates=[p for p in proc if p['event']['target_pid']==e.get('actor_pid') and
                    p['start']<=stamp(e)<p['end'] and
                    (not e.get('actor_create_time') or e['actor_create_time']==p['created'])]
        return candidates[0] if len(candidates)==1 else None
    pres={}; handles=[]; paired_pre=set(); handle_rows=0
    for n,e in ordered:
        if e.get('type') not in ('handle_pre','handle_post') or e.get('target_pid')!=game:continue
        p=owner(e)
        if not p or not p['selected']:continue
        handle_rows+=1
        ref=node(n,e)
        edge(f"raw:{p['line']}",ref,'process_identity_match',
             'pid_and_creation_time' if e.get('actor_create_time') else 'pid_within_observed_lifetime',
             'callback_execution_context_not_native_call_stack; legacy_identity_limited_by_lifecycle_coverage')
        key=(e.get('generation'),e.get('operation_id'),e.get('actor_pid'),e.get('target_pid'),e.get('thread_id'),e.get('flags',0)&128)
        if e['type']=='handle_pre':pres.setdefault(key,[]).append((n,e));continue
        prelist=pres.get(key,[])
        pre=prelist[0] if len(prelist)==1 and stamp(prelist[0][1])<=stamp(e) else None
        if pre:edge(f'raw:{pre[0]}',ref,'handle_operation_pair','generation_operation_actor_target',
                    'handle_grant_does_not_prove_memory_read')
        if pre:paired_pre.add(pre[0])
        attr=match_call(pre[1],calls or []) if pre else {'status':'unidentified','reason':'unique_pre_missing','candidates':[]}
        handles.append(dict(raw_line=n,utc=utc(stamp(e)),actor_pid=e['actor_pid'],
            actor_create_time=p['created'],actor_tid=e.get('actor_tid'),target_pid=game,
            object_kind='thread' if e.get('flags',0)&8 else 'process',
            kernel_handle=bool(e.get('flags',0)&1),duplicate=bool(e.get('flags',0)&128),
            operation_id=e.get('operation_id'),original_access=f"0x{e['original_access']:X}",
            granted_access=f"0x{e['granted_access']:X}",status=f"0x{e['status']:08X}",
            success=(e['status'] & 0x80000000)==0,call_attribution=attr,
            memory_read_proven=False))
    # External coordinate CSV is ground-truth evidence, not a new Sentinel sensor.
    ok=[]; rejected=[]; failed=0
    for e in coord or []:
        # CSV stores creation time only to milliseconds. Explicitly retain that precision limit.
        identity=(e['target_pid']==game and created and e['target_create_time']//10000==created//10000)
        in_session=e['time'] is not None and stamp(session)<=e['time']<=end
        if not identity or not in_session:
            rejected.append(dict(line=e['line'],reason='target_identity_or_session_window_mismatch'));continue
        if not e['success']:failed+=1;continue
        ok.append(e);ref=f"coord:{e['line']}"
        timeline.append(dict(id=ref,source='external_coordinate_csv',line=e['line'],utc=utc(e['time']),
            time_100ns=e['time'],fact='external_coordinate_read_report',evidence=e))
        for p in proc:
            if p['selected'] and p['start']<=e['time']<p['end']:
                edge(f"raw:{p['line']}",ref,'external_report_overlap',
                     'same_target_millisecond_creation_time_and_active_client_window',
                     'CSV_has_no_client_PID; temporal_overlap_does_not_prove_caller; external_self_report_not_Sentinel_detection')
    code=[e for _,e in raw if e.get('type')=='kernel_code_scan']
    selected_code=[e for e in code if basename(e.get('path',''))==driver_name.casefold()]
    coverage=[dict(raw_line=n,**e) for n,e in raw if e.get('type')=='coverage_gap']
    if coverage:warnings.append('coverage_gaps_present')
    if rejected:warnings.append('external_rows_rejected')
    if any(not e.get('sha256') for _,e in files):warnings.append('selected_file_hash_unavailable')
    timeline.sort(key=lambda e:(e['time_100ns'],e['id']))
    return dict(schema_version=1, session={'pid':game,'create_time':created,'mode':session.get('mode'),
        'start_utc':utc(stamp(session)),'end_utc':utc(end)},
        facts={
            'driver_load':{'observed':bool(loads),'events':len(loads),
                'disk_hashes':sorted(set(e['sha256'] for n,e in files if n in linked_files and e.get('sha256'))),
                'hash_scope':'source_sequence_generation_linked_disk_files_at_analysis_time',
                'unlinked_file_evidence_raw_lines':[n for n,_ in files if n not in linked_files]},
            'client_handle_access':{'observed':bool(handle_rows),'raw_events':handle_rows,
                'unpaired_pre_raw_lines':[n for values in pres.values() for n,_ in values if n not in paired_pre],
                'operations':handles},
            'memory_read':{'sentinel_direct_sensor':'not_implemented','sentinel_direct_observation':'unavailable',
                'external_success_rows':len(ok),'external_failed_rows':failed,
                'external_unique_coordinates':len(set(e['xyz'] for e in ok)),
                'external_start_utc':utc(min(e['time'] for e in ok)) if ok else None,
                'external_end_utc':utc(max(e['time'] for e in ok)) if ok else None,
                'external_rejected_rows':rejected,'source':'external_coordinate_csv',
                'interpretation':'reported_reads_are_not_independent_detector_observations'}},
        coverage={'gap_events':coverage,'kernel_ring_dropped_total_max':max((e.get('dropped_total',0) for _,e in raw if e.get('type')=='driver_status'),default=0),
                  'code_checks':dict(Counter(f"0x{e['status']:08X}/flags={e['flags']}" for e in code)),
                  'selected_driver_code_checks':dict(Counter(f"0x{e['status']:08X}/flags={e['flags']}" for e in selected_code))},
        timeline=timeline,links=edges,warnings=warnings,
        verdict={'cheat_confirmed_by_correlation':False,'score_added_by_correlation':0,
                 'direct_read_detection_gap':True,'handle_call_origin':'unidentified_unless_native_stack_evidence_supplied'},
        limitations=['driver_load_does_not_prove_memory_read','handle_grant_does_not_prove_use',
                     'API_intervals_do_not_capture_calls_made_outside_instrumented_APIs',
                     'file_hash_identifies_disk_bytes_at_analysis_time',
                     'no_alert_does_not_prove_no_reads','zero_recorded_loss_does_not_cover_unimplemented_sensors',
                     'UTC_clock_changes_can_break_temporal_links','preexisting_processes_without_creation_event_are_not_attributed'])

def markdown(report):
    facts=report['facts'];reads=facts['memory_read']
    lines=['# KernelSentinel 증거 분리 분석','',
        '**드라이버 로드와 핸들 접근은 실제 메모리 읽기 탐지로 판정하지 않는다.**','',
        '| 사실 | 결과 |','|---|---|',
        f"| 선택한 드라이버 로드 | {facts['driver_load']['events']}건 |",
        f"| 선택한 클라이언트의 게임 핸들 결과 | {len(facts['client_handle_access']['operations'])}건 |",
        f"| 외부 CSV의 읽기 성공 보고 | {reads['external_success_rows']}건, 서로 다른 좌표 {reads['external_unique_coordinates']}개 |",
        '| KernelSentinel의 직접 읽기 관측 | 센서 미구현·관측 불가 |',
        '| 시간순 연결에 의한 치트 확정/추가 점수 | 없음 / 0 |','',
        '## 핸들 호출 원인','',
        '| UTC | PID | 원래 요청 | 실제 부여 | 호출 근거 |',
        '|---|---:|---|---|---|']
    for h in facts['client_handle_access']['operations']:
        lines.append(f"| {h['utc']} | {h['actor_pid']} | {h['original_access']} | {h['granted_access']} | {h['call_attribution']['status']} |")
    lines+=['','`api_interval_candidate`는 API 실행 구간 후보이며 native 호출 스택 증거가 아니다.',
            '현재 미확인 호출의 원인을 Toolhelp 내부 호출 등으로 확정하지 않는다.','',
            '## 시간순 증거','', '| UTC | 출처/원시 행 | 사실 | 핵심 값 |','|---|---|---|---|']
    for row in report['timeline']:
        e=row['evidence'];kind=row['fact']
        if kind=='external_coordinate_read_report': continue
        detail=e.get('path') or (f"PID {e.get('actor_pid')} → {e.get('target_pid')}, access={hex(e.get('granted_access',e.get('original_access',0)))}" if kind.startswith('handle') else str(e.get('target_pid','')))
        if kind=='handle_pre':detail=f"PID {e.get('actor_pid')} → {e.get('target_pid')}, requested={hex(e.get('original_access',0))}"
        if kind=='process_create':detail=f"PID {e.get('target_pid')}; {detail}"
        if kind=='file_evidence':detail+=f"; SHA-256={e.get('sha256')}"
        lines.append(f"| {row['utc']} | {row['id']} | {kind} | {detail.replace('|','/')} |")
    lines+=['',f"외부 좌표 보고 구간: {reads['external_start_utc']} ~ {reads['external_end_utc']}",
            '', '전체 행별 연결·연결 이유·오탐 가능성은 `evidence.json`의 `links`를 확인한다.',
            'PID는 관측된 생성/종료 구간으로 구분하며, 새 로그는 생성 시각과 요청 TID를 추가로 사용한다.',
            '로드와 프로세스 실행의 시간 근접은 정상 설치 프로그램·관리 도구에서도 발생할 수 있다.',
            '', '## 관측 공백','',
            '일반 스레드의 IOCTL 처리 중 정상 로드된 드라이버가 게임 메모리를 읽는 동작은 직접 관측하지 않는다.',
            '코드 변화가 없고 콜백이 정상이라는 사실도 그 읽기가 없었다는 증거가 아니다.',
            'CSV는 시험 도구의 자체 보고이다. 실제 커널 읽기의 정답은 소스/실험 바이너리/독립 디버거 관찰과 함께 평가한다.',
            '', '## 수집 품질','', '```json',json.dumps(report['coverage'],ensure_ascii=False,indent=2),'```','']
    return '\n'.join(lines)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw',required=True);p.add_argument('--driver-name',required=True)
    p.add_argument('--client-name',required=True);p.add_argument('--coord');p.add_argument('--calls')
    p.add_argument('--out',required=True);p.add_argument('--window-seconds',type=float,default=900)
    a=p.parse_args()
    report=correlate(read_jsonl(a.raw),a.driver_name,a.client_name,
                     coordinates(a.coord) if a.coord else None,
                     read_jsonl(a.calls) if a.calls else None,a.window_seconds)
    report['inputs']={key:{'name':Path(value).name,'sha256':hashlib.sha256(Path(value).read_bytes()).hexdigest()}
                      for key,value in [('raw',a.raw),('coord',a.coord),('calls',a.calls)] if value}
    out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    (out/'evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out/'timeline.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in report['timeline']),encoding='utf-8')
    (out/'report.md').write_text(markdown(report),encoding='utf-8')
    print(json.dumps({'output':str(out),'facts':report['facts'],'verdict':report['verdict']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
