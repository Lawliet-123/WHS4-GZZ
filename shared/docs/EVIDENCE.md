# 추가 관측 정보와 운영 상태

최상위 7필드는 유지한다. 아래 값은 evidence 안에 넣는 선택 필드다. shared는 JSON 적합성만 검사하며 아래 관례를 강제하거나 기본값을 삽입하지 않는다. 기존 이벤트는 변경 없이 통과한다. 서버·분석기 담당자가 이 의미에 맞춰 소비하는 작업은 별도로 필요하다.

## 탐지기 관측 정보

| 키 | 권장 형식 | 의미 |
| --- | --- | --- |
| status | 문자열 | NORMAL / SUSPICIOUS / DETECTED / ERROR / OFFLINE |
| severity | 모듈이 정의한 값 | 표시용 등급. 공통 값·형식은 생산자/화면 담당자가 확정. 점수와 자동 변환하지 않음 |
| window_id | 0 이상 정수 권장 | 세션 내 관측 회차. 여러 모듈의 같은 회차를 묶으려면 런처가 공급 |
| sample_id | 0 이상 정수 권장 | 해당 회차의 탐지기/표본 순번. 기존 타입 변경은 담당자와 확인 |
| scan_start_ms | 0 이상 정수 | 세션 시작 후 스캔 시작 시각 |
| scan_end_ms | 0 이상 정수 | 세션 시작 후 스캔 종료 시각 |

- NORMAL: 실제 검사한 범위에서 정상. SUSPICIOUS/DETECTED: 모듈 자신의 판단 기준.
- ERROR: 검사 실패. OFFLINE: 게임이 실행되지 않는 등 검사 대상이 없어 검사하지 못함. 점수 0을 정상 검사 성공으로 해석하지 않는다.
- status가 없으면 모듈별 유효성 정보로 해석한다. 무조건 NORMAL로 채우지 않는다.
- 스캔형 모듈은 `scan_start_ms <= scan_end_ms`, `timestamp_ms = scan_end_ms`로 기록한다. 모두 같은 세션 시작 기준이어야 한다. 연속 관측형에 스캔 구간을 억지로 넣지 않는다.
- ERROR/OFFLINE은 정상 점수 분포·탐지율 계산에서 분리하고 검사 실패/불가 건수를 따로 센다. 이는 ReplayAnalyzer/scoring의 작업이며 shared는 전달만 한다.
- AutoPaint처럼 integrity와 behavior 유효성이 나뉘면 채널별 유효성을 유지한다. 일부 관측 실패만으로 전체 결과를 ERROR 또는 정상으로 바꾸지 않는다.
- window_id/sample_id는 전송 event_id가 아니다. 반복 관측 합산·시간창 집계는 scoring 책임이다.
- cheat_phase는 ON/OFF 정답 구간으로 만든 replay 전용 주석이다. 실시간 판단 입력으로 넣지 않는다.

## SelfDefense 운영 보고

`module="selfdefense"`, `raw_score=0`, `evidence.kind="module_health"`로 구분한다. 탐지 점수 표본이 아니므로 정상 플레이 분포에 섞지 않는다. 이는 제안한 통합 계약이며 receiver의 허용 모듈 목록과 Dashboard 표시 연결은 담당자 확인이 필요하다.

- evidence.registry_status: restarted / gave_up / orphaned / error 및 복구 후 상태.
- evidence.status: 상태 조회·복구 성공은 NORMAL, gave_up/orphaned/error는 ERROR. NORMAL은 플레이어 무결성 보장이 아니다.
- evidence.target_module, scope, pid: 감시 대상. pid는 registry가 반환한 값이지 별도로 검증된 프로세스 신원 증거가 아니다.
- evidence.error_code: 정해진 연동 오류 코드. 임의 예외 메시지나 registry의 세 번째 반환값은 전송하지 않는다.
- evidence.run_id: 워치독 실행 구간. 전송 식별자와 별개다.
- evidence.timestamp_basis: launcher_session_start / local_session_start. 통합 실행은 런처와 같은 시점을 전달한다.
- evidence.synthetic: 합성 데모 여부. 데모는 중앙 전송을 허용하지 않는다.

워치독은 치트 점수를 계산하지 않는다. 따라서 매 점검 결과는 raw에 기록하고, events.jsonl에는 상태 변화·재시작 동작을 기록한다. 탐지기가 raw_score를 계산할 때마다 Event를 남기는 기존 규칙은 그대로다.
