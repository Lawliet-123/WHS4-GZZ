# Hide Anywhere detector / Shared 0.2.0 (v11)

전체 shared/ 패키지를 팀 레포 루트에 유지한다. 수집기 파일도 레포 루트에 두거나
런처에서 shared/ 부모 경로를 PYTHONPATH에 공급한다. shared/는 ZIP에 포함하지 않는다.

```powershell
py -3.12 .\mecha_logger.py --pid 1234 --session-id hide_anywhere_006 --player-id player_042 --play-label CHEAT
```

기본은 Shared 전송 활성화다. 로컬 수집만 하려면 --local-only를 추가한다.
--server-config와 --server-logger-module은 제거했다. --code는 기존 동기 스캔으로
수집 공백을 만들 수 있어 실행 예에서는 생략했다.

## 런처 설정

GZZ_TELEMETRY_URL, GZZ_TELEMETRY_TOKEN, GZZ_TELEMETRY_OUTBOX를 공급한다.
OUTBOX는 이 프로세스 전용 경로를 명시해야 한다. 수집기는 환경을 덮어쓰지 않는다.
configure_client(ClientConfig.from_env())를 시작 시 한 번 호출한다. 종료 시
flush_client(timeout=5)와 shutdown_client(timeout=5)를 호출한다.
지속 재시도가 필요하면 GZZ_TELEMETRY_RETRY_MODE=persistent를 명시한다.
세션마다 OUTBOX를 바꾸지 않으면 재시작 시 기존 pending을 이어받을 수 있다.

## Event 계약

최상위 7필드를 유지한다. 매 평가의 0점도 events.jsonl에 기록하고 flush한 다음
동일한 dict를 send_detection(result)에 전달한다. event_id는 Shared가 생성한다.
queued는 로컬 outbox 저장이며 서버 수신 성공이 아니다. 큐/종료 오류는 raw에 남긴다.
추가 상태는 모두 evidence 안에 넣는다. 토큰과 예외 원문은 전송 로그에 남기지 않는다.

## 판정

동일한 Pawn/컴포넌트에서 유효한 값 6개가 3회 연속 일치해야 3점이다.
1~2회는 pending이며 확인 후에도 매 평가마다 3점 Event를 기록한다.
Pawn/컴포넌트 변경, 누락, 비유한 값, 읽기 실패 또는 정상 미일치는 확인을 초기화한다.
실패한 필드의 이전 캐시를 삭제하고 현재 샘플에서 읽기에 성공한 값만 평가한다.
DLL 로드와 vtable 외부 연결은 각각 1점이며 값 확인 전에는 최대 2점이다.

evidence.status: NORMAL / SUSPICIOUS / DETECTED / ERROR.
measurement_valid는 값 패턴 채널, module_observation_valid와
viewport_observation_valid는 각각 개입 관측 채널의 유효성이다.
measurement_errors, missing_fields, invalid_fields, consecutive_matches,
required_matches, timestamp_basis도 evidence에 기록한다.
실패한 플래그는 null이며 이전 관측값으로 채우지 않는다.
일부 실패가 있어도 다른 유효 채널의 증거는 유지한다. 0점 + ERROR를 정상 분포에
넣지 않는다. status=SUSPICIOUS/DETECTED에서도 채널 유효성은 함께 확인한다.

## 출력과 검증

logs/<session_id>/events.jsonl: 공통 Event
logs/<session_id>/raw/mecha_log.jsonl: 기존 raw 형식
logs/<session_id>/manifest.json: 테스트 메타데이터

```powershell
py -3.12 -m unittest -v test_detector test_server_bridge test_shared_contract
```

계약 테스트는 실제 Shared 설정/검증/UUID/outbox/종료를 사용하고 HTTP transport만
모의 처리한다. 실제 서버 수신과 Windows 게임 실행 검증은 별도 테스트가 필요하다.
SDK/EXE/DLL이 없으면 해당 정적 검증은 skipped 처리된다.
manifest의 ON/OFF 작성 정책은 이번 변경에서 바꾸지 않았다. 과거 로그의 3점 시점은
단일 샘플 기준이며 수정본은 3번째 연속 일치 샘플 기준이다.
