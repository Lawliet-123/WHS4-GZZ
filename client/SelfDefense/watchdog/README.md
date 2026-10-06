# SelfDefense Watchdog 0.3.0

런처에 등록된 프로세스의 생존·종료 상태를 관찰한다. 감시 대상과 재시작 대상을 분리했다. 재시작은 허용된 대상에 한해 런처의 registry.restart_if_dead()에 요청한다. 별도 프로세스 실행·종료·재시작 정책은 구현하지 않는다.

2026-10-06 재확인한 공개 main은 31dc8332a770f51f2f50856637dcf8a1a4cee102이다. 런처와 shared Python 소스는 앞서 검증한 0253fca 사본과 같다. 런처와 shared 원본은 수정하지 않는다.

현재 배포 묶음은 0.3.0-testfix1이다. 운영 코드와 collector_version은 0.3.0 그대로이며, 테스트·문서만 보완했다. 변경 원인과 재현 명령은 [테스트 보완 안내](docs/TESTFIX.md)에 있다.

## 변경 내용

- restartable_names() 대신 registry.load()['entries'] 전체 관찰.
- 재시작 금지 모듈과 ONESHOT도 생존·종료 기록.
- modules.py의 실행 방식·재시작 설정을 등록부 허용 여부와 함께 확인.
- PID와 생성 시각을 같은 Windows 핸들에서 확인. 접근 거부를 정상 생존으로 표시하지 않음.
- ONESHOT 종료 코드로 완료·검사 실패·크래시 구분. 코드를 확인하지 못하면 unknown.
- 최초 관찰과 상태·프로세스 식별자 변화도 Event로 전송. 매 점검은 raw 기록.
- 기존 7필드, shared 사용 방식, 진입점, 기본 1초 주기, 치트 점수 0 유지.

## LocalGuard 감시 범위

| 담당 | 런처 등록명 | 방식 | 동작 |
| --- | --- | --- | --- |
| 1번 | external_access | continuous | 관찰, 허용된 재시작 요청 |
| 1번 | module_integrity | continuous | 관찰, 허용된 재시작 요청 |
| 2번 | memory_integrity | oneshot | 관찰, 완료/실패 구분. 주기 실행은 런처 담당 |
| 3번 | input_signature | continuous, restart=False | 관찰, 종료 보고. 재시작하지 않음 |

네 개 이름만 하드코딩한 것이 아니다. 다른 등록 항목도 관찰한다. 자기 자신, autopaint, --exclude-module 대상도 관찰하되 워치독에서 재시작하지 않는다. 워치독 자신의 복구는 런처 담당이다.

modules.py에 없는 등록명은 생존만 관찰하고 재시작하지 않는다. 아직 등록되지 않은 모듈은 관찰할 수 없다. 게임 대기, --only 제외, 미구현을 임의로 사망 처리하지 않는다. 한 번 관찰한 등록 항목이 사라지면 unregistered로 보고한다.

## 설치와 실행

ZIP 내용을 client/SelfDefense/watchdog/에 넣는다. 그 안에 main.py가 바로 있어야 한다. anti_debug·integrity·shared·Launcher는 이 ZIP에 없으며 교체하지 않는다. 기존 실행을 정상 종료한 뒤 교체하고 새 테스트 세션을 사용한다. 기존 ZIP·로그는 삭제하지 않는다.

의존성: Windows, 표준 라이브러리, 레포 루트의 shared 0.2.0, 신뢰하는 client/Launcher/registry.py와 modules.py. Python 3.14에서 실행 검증했다. Python 3.12 문법 검사는 했으나 3.12 런타임 검증은 별도다.

최신 런처의 self_defense 등록 경로·인자는 이미 맞다. 새 항목을 추가하지 않는다. 런처가 세션을 만든 뒤 아래 인자로 실행한다.

    py client/SelfDefense/watchdog/main.py --session-id SESSION_ID --player-id PLAYER_ID --t0 UNIX_SECONDS --telemetry managed

대문자 값은 자리표시자다. 그대로 입력하지 않는다. 운영에서는 런처가 실제 값을 전달한다. 런처 없이 실행하면 등록부 오류를 기록하며 임의 프로세스를 띄우지 않는다.

합성 데모는 레포 루트에서 실행한다. 실제 프로세스를 죽이거나 재시작하지 않고 서버에도 보내지 않는다.

    py client/SelfDefense/watchdog/main.py --session-id watchdog_demo_001 --player-id player_042 --demo --telemetry off --interval 0.25 --duration 3

| 인자 | 내용 |
| --- | --- |
| --session-id, --player-id | 필수 공통 식별자 |
| --t0 | 세션 시작 Unix 초. 기록은 그 후 경과 ms |
| --session-start-unix-ms | --t0 대신 Unix ms. 동시 지정 불가 |
| --launcher-dir | registry.py와 modules.py 폴더. 기본 client/Launcher |
| --shared-root | shared의 부모 폴더. 기본 레포 루트 |
| --output-dir | 기본 watchdog/logs |
| --interval | 기본 1초. 전체 점검이 끝난 뒤 대기하는 간격 |
| --self-name | 기본 self_defense. 자기 자신 재시작 제외용 |
| --exclude-module | 재시작 제외 추가. 감시에서는 제외하지 않음 |
| --telemetry | managed / external / off |
| --duration, --demo | 테스트 전용. 운영에서는 생략 |

py -m client.SelfDefense.watchdog.main도 가능하다. 임의 위치 배치는 --launcher-dir과 --shared-root를 명시한다. AC_LAUNCHER_LOG_DIR을 사용한다면 런처와 같은 절대 경로를 전달한다. 설정 코드는 실행 중 다시 읽지 않으므로 레포 업데이트 후 워치독도 재시작한다.

## 판단 규칙

| 상태 | 의미 |
| --- | --- |
| alive | 해당 PID+생성 시각의 프로세스가 관찰 시점에 살아 있음 |
| completed | ONESHOT 코드 0 또는 1. 1은 의심 발견 후 검사 완료이며 정상 플레이 판정이 아님 |
| scan_failed | ONESHOT 코드 2. 검사 성립 실패 |
| crashed | ONESHOT의 다른 종료 코드 |
| exited | 상주 프로세스가 더 이상 살아 있지 않음. 재시작 금지 대상에서도 보고 |
| unknown | 종료 코드를 놓쳤거나 실행 방식이 불명확하여 완료 여부 판단 불가 |
| unregistered | 관찰했던 항목이 등록부에서 사라짐 |
| error | 권한·등록부·API 등 관찰 오류 |
| restarted / backoff / gave_up | 런처 registry의 재시작 실행 / 대기 / 한도 초과 |
| stopping / orphaned / skip | 종료 중 / 런처 부재 / 재시작 요청 제외 |

종료 코드 규칙은 현재 런처 process_manager의 ONESHOT 계약과 일치한다. 새로운 모듈이 다른 종료 코드를 사용하면 계약부터 맞춘다. 종료 코드 1을 워치독 치트 점수로 변환하지 않는다.

재시작은 continuous, modules.py의 restart=True, 등록부의 restartable=True가 모두 맞을 때만 요청한다. 세션·런처 PID/생성 시각·종료 중 여부를 먼저 확인한다. 접근 거부나 잘못된 항목이면 요청하지 않는다. 잠금, PID 등록, 5분 5회 한도, 0/2/4/8/16초 간격은 기존 registry에 맡긴다.

## Windows 관찰

OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE) → GetProcessTimes로 생성 시각 확인 → WaitForSingleObject(..., 0)으로 생존 확인 → 종료됐다면 GetExitCodeProcess로 종료 코드 확인.

모듈별 핸들을 보관해 런처가 Popen 객체를 놓은 뒤에도 종료 코드를 읽는다. PID/생성 시각 변경, 등록 제거, 워치독 종료 때 닫는다. 현재 항목 최대 1,024개와 런처 한 개를 관찰하며 과거 등록명 기억은 4,096개로 제한한다. 게임 메모리 읽기·쓰기나 디버거 연결 권한은 요청하지 않는다.

참고: [Windows 종료 코드](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getexitcodeprocess), [대기 상태 확인](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject).

## 출력과 전송

    logs/<session_id>/session-clock.json
    logs/<session_id>/runs/<run_id>/manifest.json
    logs/<session_id>/runs/<run_id>/events.jsonl
    logs/<session_id>/runs/<run_id>/raw/watchdog.jsonl

동일 세션 재실행은 시계를 유지하고 새 run_id를 쓴다. player·시계 충돌은 시작 오류다. 강제 종료된 실행의 manifest가 RUNNING으로 남을 수 있으므로 이것만으로 생존을 판단하지 않는다.

raw는 매 점검 기록한다. Event는 최초 모듈 관찰, 상태·식별자·오류 변화, 재시작 실행, 오류 복구 때 기록하고 shared로 보낸다. 변화 없는 결과를 반복 전송하는 하트비트가 아니다.

Event 최상위는 session_id, player_id, module, timestamp_ms, evidence, reasons, raw_score 7개다. module=selfdefense, evidence.kind=module_health, raw_score=0인 운영 상태로 취급한다. 치트 점수에 합산하지 않는다.

evidence에는 status, registry_status, target_module, scope, pid, error_code, mode, restart_allowed, exit_code, create_time, health_scope, functional_health_checked, collector_version, run_id, synthetic, timestamp_basis가 들어간다. create_time은 정밀도 보존용 문자열이다. 새 PID에 이전 프로세스의 생성 시각·종료 코드를 붙이지 않으며 다음 관찰에서 새 정보를 기록한다. functional_health_checked는 false다.

managed는 시작 때 configure_client(ClientConfig.from_env()), 결과마다 send_detection(), 종료 때 flush_client(3초)·shutdown_client(5초)를 호출한다. 별도 HTTP 코드는 없다. 설정 실패는 알리고 로컬 관찰을 유지한다. external은 같은 프로세스의 기존 sender를 사용하고 종료하지 않는다. 별도 프로세스 런처에서는 managed/off를 사용한다.

설정: GZZ_TELEMETRY_URL, GZZ_TELEMETRY_TOKEN, 모듈 전용 GZZ_TELEMETRY_OUTBOX. 최신 registry.spawn의 outbox 분리를 그대로 사용한다. 지속 재시도는 GZZ_TELEMETRY_RETRY_MODE=persistent. .env 자동 로딩은 없다. queued는 대기열 등록이며 서버 수신 성공을 뜻하지 않는다.

## 한계와 후속 협의

- 프로세스 생존 확인이지 내부 검사 진행·스레드 정지·하트비트 검증이 아니다.
- 관찰 전에 끝나 OS에서 사라진 짧은 검사는 종료 코드를 알 수 없다. unknown으로 남긴다.
- 처음부터 미등록, 시작 실패, 의도적 제외, 다음 검사 예정/누락은 현재 등록부만으로 구분하지 못한다.
- 게임 종료 직후 stopping 기록 전에 상주 모듈이 먼저 끝나면 exited로 보일 수 있다. 치트 판정이 아니며 종료 의도를 단정하지 않는다.
- 세션 사전 확인과 재시작은 하나의 원자적 트랜잭션이 아니다. 신뢰하는 코드·등록부와 단일 세션 소유권을 전제로 한다.
- 런처가 먼저 되살리면 새 PID의 alive를 볼 수 있다. 모든 짧은 종료를 놓치지 않는 감사 로그는 아니다.
- registry 호출이 오래 잠기면 다음 점검도 지연된다. 1초는 탐지 지연 보장이 아니다.
- 로그 보존 기간·자동 정리·실게임 성능 실측은 별도 운영 과제다.

[런처 전달 사항](docs/LAUNCHER_HANDOFF.md), [검증 결과](docs/PACKAGE-VALIDATION.md), [실행 결과 JSON](docs/INTEGRATION-RESULT.json)을 참고한다. 전체 Launcher.main·실게임·중앙 HTTPS는 이번 검증 범위가 아니다.
