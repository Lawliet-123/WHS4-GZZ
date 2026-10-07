"""Execution evidence, not a cheat verdict or a universal sensor health check."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from .analysis import CrossView, findings

FEATURES = {
    'target': '보호 대상 게임 등록', 'lifecycle': '프로세스·스레드 감시',
    'handles': '게임 핸들 접근 감시', 'enforcement': '일부 핸들 권한 제한',
    'images': 'DLL·드라이버 로드 감시', 'entry': '커널 함수 진입부 검사',
    'code': '커널 모듈 코드 무결성 검사', 'dispatch': '자체 디스패치 포인터 검사',
    'crossview': '드라이버 목록 교차 검사', 'threads': '커널 시스템 스레드 출처 검사',
    'callback': '핸들 콜백 동작 점검', 'files': '파일 식별·서명 정보 수집',
    'direct_memory': '커널 메모리 접근 직접 관측',
}
LABELS = {'observed':'관측됨', 'checked':'검사 완료', 'baseline_only':'기준 설정만',
          'partial':'일부 실패/불완전', 'failed':'실패/불완전', 'finding':'변화·차이 관측',
          'not_observed':'미관측', 'disabled':'비활성', 'unsupported':'지원 안 됨',
          'inconclusive':'판정 불가', 'not_implemented':'미구현'}

def utc(event):
    if event.get('timestamp_100ns'):
        seconds = (event['timestamp_100ns']-116444736000000000)/10000000
    else:
        value = event.get('unix_ms', event.get('collected_unix_ms'))
        if value is None: return None
        seconds = value/1000
    return datetime.fromtimestamp(seconds,timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')

def metric():
    return dict(records=0, successes=0, findings=0, errors=0, baselines=0,
                last_observed_utc=None, last_success_utc=None, details=Counter())

class FeatureStatus:
    def __init__(self):
        self.session = None
        self.ended = False
        self.rows = {key:metric() for key in FEATURES}
        self.kinds = Counter(); self.gaps = Counter(); self.dropped_max = 0
        self.pre = {}; self.evicted_pre = 0
        self.cross = CrossView(); self.legacy_cross = metric()
        self.before = None; self.user = None; self.user_pending = False
        self.thread_pending = False

    def mark(self, key, e, result=None, count=1):
        m = self.rows[key] if isinstance(key,str) else key
        m['records'] += count
        when=utc(e)
        if when:m['last_observed_utc']=max(m['last_observed_utc'] or when,when)
        if result:
            m[result] += 1
            if result=='successes' and when:
                m['last_success_utc']=max(m['last_success_utc'] or when,when)

    def cross_result(self,m,e):
        result={'matched':'successes','difference':'findings','inconclusive':'errors'}.get(e.get('outcome'),'errors')
        self.mark(m,e,result)
        m['details'][e.get('outcome','unknown')]+=1

    @staticmethod
    def handle_key(e):
        return (e.get('generation'),e.get('operation_id'),e.get('actor_pid'),
                e.get('target_pid'),e.get('thread_id'),e.get('flags',0)&128)

    def observe(self,e):
        k=e.get('type','unknown'); self.kinds[k]+=1
        if k=='session_start':
            if self.session is not None:raise ValueError('one session per status report; split merged logs')
            self.session=e
        elif k=='session_end':self.ended=True
        elif k=='coverage_gap':
            reason=e.get('reason','unknown');self.gaps[reason]+=1
            if reason=='kernel_thread_scan_inconclusive' and self.thread_pending:
                self.rows['threads']['errors']+=1;self.thread_pending=False
        elif k=='driver_status':
            self.dropped_max=max(self.dropped_max,e.get('dropped_total',0))
            s=self.session or {}
            if s.get('pid'):
                ok=(e.get('pid')==s['pid'] and e.get('create_time')==s.get('create_time') and
                    e.get('lease_active')==1 and e.get('mode')=={'observe':1,'enforce':2}.get(s.get('mode')))
                self.mark('target',e,'successes' if ok else 'errors')
        elif k in ('process_create','process_exit','thread_create','thread_exit'):
            self.mark('lifecycle',e); self.rows['lifecycle']['details'][k]+=1
        elif k in ('game_image','kernel_image','driver_snapshot'):
            self.mark('images',e); self.rows['images']['details'][k]+=1
        elif k=='handle_pre':
            self.mark('handles',e); self.rows['handles']['details']['pre']+=1
            if e.get('flags',0)&64:self.rows['handles']['details']['post_context_unavailable']+=1
            if len(self.pre)>=16384:
                self.pre.pop(next(iter(self.pre)));self.evicted_pre+=1
            key=self.handle_key(e)
            if key in self.pre:self.rows['handles']['details']['duplicate_pre_key']+=1
            self.pre[key]=e
            if e.get('flags',0)&32:
                before=e.get('before_access');after=e.get('after_access')
                exempt=bool(e.get('flags',0)&3)
                mask=0x13 if e.get('flags',0)&8 else 0x86b
                valid=before is not None and after is not None and after==(before if exempt else before&~mask)
                self.mark('enforcement',e,'successes' if valid else 'errors')
                d=self.rows['enforcement']['details']
                d['exempt' if exempt else 'eligible']+=1
                if valid and before!=after:d['stripped']+=1
        elif k=='handle_post':
            self.mark('handles',e);d=self.rows['handles']['details'];d['post']+=1
            pre=self.pre.pop(self.handle_key(e),None)
            if pre is None:d['post_without_pre']+=1
            else:
                self.mark('handles',e,'successes',count=0);d['paired']+=1
            # An access denied result is a completed handle operation, not a sensor failure.
            if e.get('status',0)&0x80000000:d['request_failed']+=1
            else:d['request_succeeded']+=1
        elif k=='kernel_diagnostics':
            good=e.get('valid')==15 and e.get('failed')==0
            self.mark('entry',e,'successes' if good and not e.get('changed') else None)
            if not good:self.rows['entry']['errors']+=1
            if e.get('changed'):self.rows['entry']['findings']+=1
            ptrs=e.get('dispatch_pointers',[]);mods=e.get('modules',[])
            valid=(e.get('module_status')==0 and bool(mods) and len(ptrs)==3 and
                   all(p.get('current') and p.get('baseline') for p in ptrs))
            changed=bool(e.get('dispatch')) or any(p.get('current')!=p.get('baseline') for p in ptrs)
            outside=valid and any(not any(m['base']<=p['current']<m['base']+m['size'] for m in mods) for p in ptrs)
            self.mark('dispatch',e,'successes' if valid and not changed and not outside else None)
            if not valid:self.rows['dispatch']['errors']+=1
            if changed or outside:self.rows['dispatch']['findings']+=1
            if self.user_pending:
                self.cross.compare(self.before or {'module_status':1,'modules':[]},self.user,e)
                self.cross_result(self.legacy_cross,dict(self.cross.last_status,collected_unix_ms=e.get('collected_unix_ms')))
                self.before=None;self.user=None;self.user_pending=False
            else:self.before=e
        elif k=='psapi_snapshot':
            self.user=e.get('modules');self.user_pending=True
        elif k=='driver_cross_view_status':self.cross_result(self.rows['crossview'],e)
        elif k=='driver_view_mismatch':self.rows['crossview']['details']['persistent_difference_records']+=1
        elif k=='kernel_code_scan':
            f=e.get('flags',0)
            if e.get('status')!=0 or not e.get('bytes_hashed') or not f&7:result='errors'
            elif f&2:result='findings'
            elif f&4:result='baselines'
            else:result='successes'
            self.mark('code',e,result)
            if result=='errors':self.rows['code']['details']['failed:'+e.get('path','unknown')]+=1
        elif k=='kernel_thread_snapshot':
            if self.thread_pending:
                self.rows['threads']['errors']+=1
                self.rows['threads']['details']['completion_missing']+=1
            bad=e.get('status')!=0 or bool(e.get('flags',0))
            self.mark('threads',e,'errors' if bad else None)
            self.thread_pending=not bad
        elif k=='kernel_thread_scan_summary':
            d=self.rows['threads']['details']
            for field in ('checked','query_failed','lookup_failed','outside'):d[field]+=e.get(field,0)
            bad=e.get('query_failed',0) or e.get('lookup_failed',0) or not self.thread_pending
            self.mark('threads',e,'errors' if bad else ('findings' if e.get('outside') else 'successes'),count=0)
            self.thread_pending=False
        elif k=='callback_health':
            result={'observed':'successes','missing':'findings','inconclusive':'errors'}.get(e.get('outcome'),'errors')
            self.mark('callback',e,result);self.rows['callback']['details'][e.get('outcome','unknown')]+=1
        elif k=='file_evidence':
            d=self.rows['files']['details'];signature=e.get('signature','not_checked')
            hash_ok=isinstance(e.get('sha256'),str) and len(e['sha256'])==64
            sig_unavailable=isinstance(signature,str) and signature.startswith('unavailable')
            self.mark('files',e,'errors' if e.get('error') or not hash_ok or sig_unavailable else 'successes')
            if e.get('error'):d['error:'+str(e['error'])]+=1
            d['hash_success' if hash_ok else 'hash_unavailable']+=1
            if signature=='not_checked':d['signature_not_checked']+=1
            elif isinstance(signature,dict) and 'Status' in signature:
                d['signature_checked']+=1;d['signature_status:'+str(signature['Status'])]+=1
            else:d['signature_unavailable']+=1
        # Policy matches are counts of evidence records, not a new score.
        config=(self.session or {}).get('config')
        if isinstance(config,dict) and k in ('process_create','game_image','kernel_image','driver_snapshot','file_evidence'):
            for reason,_ in findings(e,config):
                if reason.startswith('name_pattern_match:') or reason=='configured_file_sha256_match':
                    self.rows['files']['details']['policy_match_records']+=1

    def report(self):
        s=self.session or {}; rows=deepcopy(self.rows)
        if self.thread_pending:
            rows['threads']['errors']+=1;rows['threads']['details']['completion_missing']+=1
        legacy=not rows['crossview']['records']
        if legacy:
            old=rows['crossview']['details']
            rows['crossview']=deepcopy(self.legacy_cross)
            rows['crossview']['details'].update(old)
            if self.user_pending:rows['crossview']['errors']+=1
        rows['handles']['details']['pre_without_post']=len(self.pre)
        rows['handles']['details']['pre_tracking_evictions']=self.evicted_pre
        if any(rows['handles']['details'][x] for x in ('pre_without_post','post_without_pre','duplicate_pre_key','pre_tracking_evictions')):
            rows['handles']['errors']+=1  # one aggregate completeness issue, not a count of failed calls
        config=s.get('config'); policies=sum(len(v) for v in config.values() if isinstance(v,list)) if isinstance(config,dict) else None
        rows['files']['details']['policy_entries']=policies
        fd=rows['files']['details']
        rows['files']['subchecks']={
            'signature':{'enabled':s.get('verify_signatures'),
                'state':'inconclusive' if (s.get('verify_signatures') is False and fd['signature_checked']) else
                        ('partial' if fd['signature_unavailable'] else
                         ('checked' if fd['signature_checked'] else
                          ('disabled' if s.get('verify_signatures') is False else 'not_checked'))),
                'checked':fd['signature_checked'],'not_checked':fd['signature_not_checked'],
                'unavailable':fd['signature_unavailable']},
            'configured_identity_rules':{'enabled':bool(policies) if policies is not None else None,
                'state':'unknown' if policies is None else ('disabled' if not policies else 'configured'),
                'entries':policies,'match_records':fd['policy_match_records']}}
        ko=s.get('scope')=='kernel_only'
        enabled={key:None for key in rows}
        if self.session:
            enabled.update({key:True for key in rows})
            for key in ('target','lifecycle','handles'):enabled[key]=not ko
            enabled['enforcement']=not ko and s.get('mode')=='enforce'
            enabled['threads']=s.get('thread_interval',None)!=0
            enabled['callback']=s.get('callback_health_enabled') if not ko else False
            enabled['code']=(s['code_per_cycle']>0) if 'code_per_cycle' in s else None
        reasons={
            'target':'등록 PID·생성 시각·모드·lease의 상태 조회 결과',
            'lifecycle':f"프로세스 {sum(rows['lifecycle']['details'][k] for k in ('process_create','process_exit'))}; 게임 스레드 {sum(rows['lifecycle']['details'][k] for k in ('thread_create','thread_exit'))}; 사건 수집만 확인",
            'handles':f"pre {rows['handles']['details']['pre']}; post {rows['handles']['details']['post']}; 짝 {rows['handles']['details']['paired']}; 읽기 수행 증거 아님",
            'enforcement':f"권한 제거 관측 {rows['enforcement']['details']['stripped']}; observe에서는 비활성",
            'images':f"게임 이미지 {rows['images']['details']['game_image']}; 커널 로드 {rows['images']['details']['kernel_image']}; 목록 신규 {rows['images']['details']['driver_snapshot']}",
            'entry':'4개 함수의 기준 대비 검사 묶음 수; 모듈 목록 실패와 분리',
            'code':f"동일 {rows['code']['successes']}; 기준 설정 {rows['code']['baselines']}; 변경 {rows['code']['findings']}; 실패 {rows['code']['errors']}",
            'dispatch':'자체 포인터 3개 변경·모듈 소속 검사; 다른 드라이버 전체 아님',
            'crossview':('기존 목록 3개를 오프라인 재비교; 당시 완료 이벤트의 증명은 아님' if legacy else '수집기가 기록한 비교 결과')+'; 차이=은닉 확정 아님',
            'threads':f"누적 검사 {rows['threads']['details']['checked']}; 밖 {rows['threads']['details']['outside']}; 고유 스레드 수 아님",
            'callback':'observed=시험 pre/post 확인; missing=미관측; 전체 콜백 표 검증 아님',
            'files':f"해시 {rows['files']['details']['hash_success']}; 서명 검사 {rows['files']['details']['signature_checked']}; 미검사 {rows['files']['details']['signature_not_checked']}; 정책 {policies if policies is not None else '불명'}개",
            'direct_memory':'하이퍼바이저 보류; 직접 읽기·쓰기 센서 미구현',
        }
        for key,m in rows.items():
            if key=='direct_memory':state='not_implemented';enabled[key]=False
            elif enabled[key] is False and m['records']:
                state='inconclusive';reasons[key]+='; 비활성 설정과 관측 기록 충돌'
            elif enabled[key] is False:state='disabled'
            elif key=='threads' and s.get('capabilities') is not None and not s['capabilities']&32 and not m['records']:state='unsupported'
            elif m['errors']:state='partial' if m['successes'] or m['findings'] or m['baselines'] else 'failed'
            elif m['findings']:state='finding'
            elif key in ('lifecycle','images','handles','files','enforcement') and m['records']:state='observed'
            elif m['successes']:state='checked'
            elif m['baselines']:state='baseline_only'
            elif m['records']:state='inconclusive'
            else:state='not_observed'
            if key=='images' and m['records'] and not (m['details']['kernel_image']+m['details']['game_image']):
                reasons[key]+='; 목록만 관측, 로드 콜백 실행 미확인'
            m.update(feature=key,name=FEATURES[key],state=state,label=LABELS[state],enabled=enabled[key],reason=reasons[key])
            m['details']=dict(m['details'])
        warnings=[]
        if not self.session:warnings.append('session_start 없음: 활성 설정·대상 신원 불명')
        if not self.ended:warnings.append('session_end 없음: 실행 중이거나 불완전하게 종료된 로그')
        if self.gaps:warnings.append('수집 공백 있음: 기능별 관측 결과가 전체 구간의 완전성을 보증하지 않음')
        if self.dropped_max:warnings.append('드라이버 누적 손실 있음: 세션 이전 손실 포함 가능')
        return dict(schema_version=1,agent_version=s.get('agent_version'),session_complete=self.ended,
            gap_records=dict(self.gaps),driver_dropped_total_max=self.dropped_max,
            discarded_pre_session_events=s.get('discarded_pre_session_events'),warnings=warnings,
            features=list(rows.values()),meaning='실행·관측 근거 요약. 치트 판정 또는 모든 센서의 정상 동작 보증이 아님.')

def render_table(report):
    lines=['기능별 실행 상태표','',report['meaning'],'',
           '| 기능 | 상태 | 기록 | 성공 검사/짝 | 변화·차이 | 실패/불완전 | 근거·범위 |',
           '|---|---|---:|---:|---:|---:|---|']
    for r in report['features']:
        lines.append(f"| {r['name']} | {r['label']} | {r['records']} | {r['successes']} | {r['findings']} | {r['errors']} | {r['reason']} |")
    lines+=['','사건형 기능의 성공 검사 0은 실행 실패가 아니다. 기록/검사 건수는 고유 대상 수가 아니다.',
            '성공은 기록된 검사 범위에 한정한다. 변화·차이와 실패/불완전은 서로 독립적으로 집계한다.',
            '마지막 관측·성공 시각(UTC)과 세부 건수는 JSON의 features에 포함된다.',
            f"수집 공백 {sum(report['gap_records'].values())}건; 드라이버 누적 손실 최대 {report['driver_dropped_total_max']}; 세션 종료 기록 {'있음' if report['session_complete'] else '없음'}."]
    lines += ['주의: '+w for w in report['warnings']]
    return '\n'.join(lines)+'\n'
