# SelfDefense Watchdog 0.2.1

상주 모듈의 생존 상태를 런처 registry로 확인하고 필요한 재시작을 그 registry에 요청한다. 프로세스 생성/종료/잠금/PID 등록/재시작 한도는 여기서 구현하지 않는다. 별도 하트비트, 안티디버깅, 자체 파일 무결성, 커널 기능도 이번 범위가 아니다.

팀에서 정한 watchdog 하위 폴더 구조로 바로잡은 버전이다. 0.2.0을 그대로 하위 폴더로 옮기면 기본 shared/Launcher 탐색 경로가 달라지므로 이 버전을 사용한다. 탐지 점수·이벤트 규격·재시작 정책은 바꾸지 않았다.

2026-10-01 공개 Launcher main acc9d2afe7d21e576b098f654c04643be8455b61의 원본 registry/process_manager를 사용한다. 런처 소스는 수정하지 않았다. 전체 Launcher.main·실제 게임·중앙 HTTPS 서버의 종단 검증은 남아 있다. [런처 담당자 반영 요청](docs/LAUNCHER_HANDOFF.md), [배포 검증 기록](docs/PACKAGE-VALIDATION.md)을 참고한다.

## 설치

- 이 모듈의 ZIP은 최상위에 main.py가 있다. 내용을 팀 저장소의 `client/SelfDefense/watchdog/`에 넣는다. watchdog 폴더를 한 겹 더 만들지 않는다.
- anti_debug/와 integrity/는 이번 ZIP에 없으며 해당 폴더·파일은 변경하지 않는다. SelfDefense 전체를 덮어쓰는 배포본이 아니다.
- 0.2.0 ZIP과 로그는 보관할 수 있지만 새 실행 경로와 동시에 실행하지 않는다. 업그레이드는 기존 실행을 정상 종료한 뒤 새 세션 ID로 진행한다. 예전 루트 파일이 이미 레포에 올라갔다면 파일 소유자를 확인하고 별도로 정리하며 폴더 전체를 삭제하지 않는다.
- 별도 GZZ-Shared-0.2.0 묶음의 `shared/`를 팀 저장소 최상위에 둔다. 라이브러리이므로 shared에는 실행용 main.py가 필요 없다.
- 표준 라이브러리만 사용한다. Python 3.14에서 검증했으며 팀의 Python 3.12 런타임 검증은 남아 있다.
- 일반 배치에서 shared 경로를 찾는다. 임의 위치에 압축을 풀었다면 `--shared-root`로 shared의 부모 폴더를 명시한다. 구버전 shared가 import되지 않는지 확인한다.

## 지금 가능한 합성 데모

팀 레포 루트에서 PowerShell로 실행한다.

```powershell
py client/SelfDefense/watchdog/main.py --session-id watchdog_demo_001 --player-id player_042 --demo --telemetry off --interval 0.25 --duration 3
```

ZIP만 다른 폴더에 풀었다면 그 폴더에서 아래처럼 실행한다. `C:\Team\MecchaAntiCheat`는 shared 폴더가 들어 있는 실제 경로로 바꾼다.

```powershell
py main.py --shared-root "C:\Team\MecchaAntiCheat" --session-id watchdog_demo_001 --player-id player_042 --demo --telemetry off --interval 0.25 --duration 3
```

합성 응답으로 restarted/gave_up/복구/orphaned 처리를 보여준다. 실제 프로그램을 죽이거나 되살리는 데모가 아니다. `--demo`와 중앙 전송을 함께 지정하면 실행을 거절한다. manifest와 Event에도 synthetic=true가 기록된다. 정상/핵 게임 표본으로 제출하지 않는다.

## 실제 런처 없이 실행

```powershell
py client/SelfDefense/watchdog/main.py --session-id watchdog_local_001 --player-id player_042 --telemetry off --duration 5
```

registry가 없으면 `REGISTRY_UNAVAILABLE` 운영 Event를 한 번 기록하고 계속 확인한다. 임의로 프로세스를 찾거나 재시작하지 않는다. 경로 오류를 alive로 처리하지 않는다. registry를 나중에 올바른 위치에 제공하면 다음 점검에서 다시 불러올 수 있다. 이미 불러온 코드의 변경은 워치독 재실행으로 반영한다.

## 런처 연동 계약 — acc9d2a 기준

- 진입점: `client/SelfDefense/watchdog/main.py`. CONTINUOUS, 기본 1초 주기. `py -m client.SelfDefense.watchdog.main`도 지원한다.
- 레포 배치를 기준으로 `client/Launcher/registry.py`와 레포 루트의 shared를 찾는다. 임의 폴더에 푼 단독 배치에서는 --launcher-dir과 --shared-root를 지정한다.
- `registry.restartable_names()` → 상주 모듈 이름 iterable. ONESHOT 제외는 registry 책임이다.
- 각 대상에 `registry.restart_if_dead(name, by="watchdog")` 호출.
- 실제 반환은 `(status, pid, Popen 또는 None)`이다. 세 번째 값은 사용/전송하지 않는다.
- 허용 상태: alive / restarted / backoff / gave_up / stopping / orphaned / skip.
- alive/restarted에는 양의 정수 PID가 있어야 한다. 실제 registry 계약이 다르면 launcher_adapter.py와 관련 테스트를 먼저 맞춘다.
- SelfDefense 자기 자신은 호출 대상에서 제외한다. 기본 등록명은 self_defense이며 옛 이름 selfdefense도 제외한다. 다른 이름이면 `--self-name`을 지정한다. SelfDefense 재시작은 런처 책임이다.
- autopaint도 기본 제외한다. `--exclude-module`로 다른 대상도 추가할 수 있다. 이 제외는 워치독에만 적용되므로 런처 자체의 AutoPaint 자동 재시작도 꺼야 한다.
- stopping/orphaned에서 직접 되살리지 않는다. registry가 이 상태를 반환하기 전에 재시작하지 않는다는 계약이 필요하다.
- registry 함수는 빠르게 반환해야 한다. 함수가 멈추면 현재 단일 점검 루프도 멈춘다. 강제 timeout/registry 작업 중단은 구현하지 않았다.
- registry 파일은 신뢰하는 팀 코드여야 한다. PID 파일·경로·1초 점검만으로 변조 방지나 프로세스 신원을 보장하지 않는다.
- 재시작 전 registry의 세션·런처 PID/생성 시각을 확인한다. 등록부 없음/읽기 불가/세션 불일치/런처 식별자 누락은 오류로 보고하고 복구를 요청하지 않는다. 런처 사망은 목록이 비어 있어도 LAUNCHER_ORPHANED로 보고한다.
- 세션 사전 확인은 원자적 잠금이나 인증이 아니다. 동시에 다른 세션이 같은 등록부를 교체하지 않도록 런처가 관리해야 한다.
- 실제 restartable_names는 ONESHOT뿐 아니라 restart=False 대상도 제외한다. 따라서 AutoPaint/input_signature 등 재시작 비허용 대상까지 이 워치독이 감시한다고 설명하지 않는다.
- AC_LAUNCHER_LOG_DIR을 사용하면 런처와 SelfDefense에 같은 절대 경로를 전달한다. 기본은 Launcher/logs다.

## 실행 인자

| 인자 | 의미 |
| --- | --- |
| --session-id / --player-id | 필수. 공통 safe ASCII 식별자 |
| --session-start-unix-ms | 통합 세션 시작 UTC Unix ms. 로그에는 그 후 경과시간을 기록 |
| --t0 | 런처의 Unix 초를 ms로 변환. ms 미만 절삭. 위 ms 옵션과 동시 사용 불가 |
| --launcher-dir | 실제 registry.py가 있는 폴더 |
| --shared-root | shared 패키지의 부모 폴더 |
| --output-dir | 기본: 이 모듈 폴더의 logs. 런처에서 쓰기 가능한 절대 경로 권장 |
| --interval | 기본 1초. 양의 유한 숫자 |
| --duration | 테스트용 실행 시간. 정상 상주 실행에서는 생략 |
| --self-name | registry에 등록된 자기 모듈 이름. 기본 self_defense |
| --exclude-module | 추가로 재시작 요청하지 않을 모듈. 반복 지정 가능 |
| --telemetry | managed(기본) / external / off |
| --demo | 합성 모드. 반드시 --telemetry off와 함께 사용 |

managed는 configure/send/flush/shutdown을 담당한다. external은 같은 프로세스의 기존 sender를 사용하며 종료하지 않는다. 런처가 별도 프로세스로 실행하면 managed를 사용한다. 부모 프로세스의 shared sender가 자식과 공유되는 것은 아니다.

managed에는 `GZZ_TELEMETRY_URL`, `GZZ_TELEMETRY_TOKEN`, sender 전용 `GZZ_TELEMETRY_OUTBOX`를 설정한다. 일시 장애의 지속 재시도를 원하면 `GZZ_TELEMETRY_RETRY_MODE=persistent`를 명시한다. .env 자동 로딩은 없다. 설정/전송 실패는 화면에 알리고 워치독 점검·로컬 기록은 계속한다. 시작 설정 실패 후 자동 재초기화는 하지 않으므로 수정 후 재실행한다.

## 로그와 시간

`logs/<session_id>/session-clock.json`에 최초 세션 시점을 보관한다. 재실행 시 같은 값을 사용한다. player, 합성 여부, 시작 시각이 충돌하면 조용히 덮어쓰지 않고 실행 오류로 알린다. 파일을 삭제해 오류를 숨기지 말고 올바른 ID/시각을 전달한다.

각 실행은 `logs/<session_id>/runs/<run_id>/`에 manifest.json, events.jsonl, raw/watchdog.jsonl을 만든다. 이전 실행 기록은 보존된다. 실행 구간별 하위 폴더이므로 기존 게임용 ReplayAnalyzer가 자동으로 읽는지는 별도로 연결해야 한다.

통합 런처는 최초 실행부터 같은 --t0 또는 --session-start-unix-ms를 전달해야 한다. 생략하면 워치독이 시작한 시각을 로컬 세션 시작으로 삼고 경고한다. 이 값은 다른 모듈의 세션 시간과 같다고 간주하면 안 된다. 한 실행 안에서는 monotonic 경과시간을 사용하고 재실행 때 저장된 UTC 시점에 다시 맞춘다. PC 시계가 실행 사이에 크게 바뀌면 시간 정확도에 영향이 있다.

- raw: 매 점검 결과.
- events: 재시작 동작, 오류 상태의 최초 확인/변경, 복구. 점수를 계산하는 탐지기가 아니므로 매 점검 0점 Event를 전송하지 않는다.
- module=selfdefense, raw_score=0, evidence.kind=module_health. 게임 정상 표본과 분리한다.
- ERROR는 검사/감시 실패이고 NORMAL은 상태 조회/복구 성공이다. 플레이어가 정상이라는 판정이 아니다.
- 성공적으로 queued됐어도 서버 도착이 확인된 것은 아니다. 로컬 기록/전송 실패를 화면에 알린다. shared enqueue에 실패한 로컬 파일의 자동 재전송은 구현하지 않았다.

SIGINT/SIGTERM/Windows SIGBREAK를 처리한다. 원본 런처의 Ctrl+Break 정상 종료와 flush/shutdown·manifest 마감을 테스트용 프로세스에서 확인했다. 콘솔 없는 pythonw/창 모드 EXE는 별도 검증이 필요하다. 강제 종료는 finally 실행을 보장하지 않으며, 이미 저장된 shared outbox는 남는다. 종료코드 0은 실행 루프 정상 종료, 2는 시작/인자 오류, 3은 예상하지 못한 실행 오류다. 0이 감시 대상의 정상 상태를 의미하지는 않는다.

manifest에 run_status(RUNNING/STOPPED/ERROR), stop_reason, exit_code를 기록한다. 강제 종료하면 RUNNING이 남을 수 있으므로 이 값만으로 실제 생존을 판단하지 않는다.

## 테스트와 남은 확인

개발 프로젝트 루트: `py -m unittest discover -s tests -p test_selfdefense.py -v`

SelfDefense ZIP 루트: shared의 부모를 PYTHONPATH에 둔 뒤 같은 명령 실행. 실제 프로세스 종료·재시작은 하지 않는다.

팀 레포에 설치한 뒤에는 `py -m unittest discover -s client/SelfDefense/watchdog/tests -p test_selfdefense.py -v`로 실행한다.

실제 프로세스 연동 테스트는 명시적으로 다음 명령으로 실행한다. 새 임시 폴더와 테스트용 프로세스만 사용하며 Launcher.main·게임·실제 탐지기는 실행하지 않는다. 기존 report를 덮어쓰지 않으므로 매번 새 파일명을 사용한다. 약 30초 이상의 실제 재시작 backoff를 포함한다.

```powershell
py client/SelfDefense/watchdog/tests/launcher_integration.py --launcher-dir client/Launcher --shared-root . --report watchdog-integration-result.json
```

테스트가 실제로 사용한 런처 파일 SHA-256을 결과에 남긴다. 개발 기준 커밋과 다른 파일이면 같은 버전 검증으로 간주하지 않는다. 임시 폴더에 진단 로그를 보존하며 이 자료는 게임 정상/핵 표본이 아니다.

남은 실연동 확인:
- 실제 통합 Launcher.main 등록표에 필수 인자를 반영한 뒤 전체 게임 실행 흐름 확인.
- 콘솔 없는 패키징의 정상 종료 전달, 재시작 비허용 대상의 감시 범위 협의.
- receiver의 selfdefense 허용, module_health 별도 표시/집계, 실제 HTTPS 수신.
- Python 3.12 런타임.

새 작업자는 launcher_adapter.py(런처 경계), monitor.py(상태 처리), reporting.py(로컬·shared), main.py(설정·실행)를 나눠 수정한다. 공유 registry 대신 새 프로세스 제어 코드를 추가하지 않는다.
