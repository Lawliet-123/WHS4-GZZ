# SelfDefense AntiDebug 0.1.0

1차 기능: 안티치트 프로세스에 Windows 네이티브 디버거가 연결됐는지 관측하고 기록한다. 디버거 해제·강제 종료·게임 차단·자동 제재는 하지 않는다. Python 표준 라이브러리와 팀 shared 0.2.0을 사용한다.

## 설치와 실행

ZIP 내용 전체를 `client/SelfDefense/anti_debug/`에 넣는다. main.py가 바로 그 안에 있어야 한다. shared는 통합 레포의 shared 폴더를 그대로 사용한다. 다른 SelfDefense 모듈을 import하거나 교체하지 않는다.

런처 연동 전 자체 확인은 통합 레포 최상위에서 다음 명령으로 한다.

```powershell
py client/SelfDefense/anti_debug/main.py --session-id antidebug_check_001 --player-id player_042 --self-only --once
```

이 명령은 검사기 자신만 확인한다. 결과의 scope=self_only이며 전체 안티치트가 정상이라는 뜻이 아니다. self-only는 중앙 전송을 허용하지 않는다.

실제 런처 등록부를 읽을 때는 런처가 만든 같은 세션 ID를 사용한다. 아래 SESSION과 T0는 런처의 session-id와 세션 시작 Unix 초로 바꾼다.

```powershell
py client/SelfDefense/anti_debug/main.py --session-id SESSION --player-id player_042 --t0 T0 --telemetry off
```

등록부 경로 우선순위:

1. `--registry-path`로 명시한 anticheat_pids.json
2. `AC_LAUNCHER_LOG_DIR/anticheat_pids.json`
3. `--launcher-dir/logs/anticheat_pids.json` — 기본 launcher-dir은 통합 레포의 client/Launcher

shared 탐색에 실패하면 `--shared-root`에 shared 폴더의 상위 경로를 지정한다. --help는 shared 설정 없이 볼 수 있다. 기본 전송은 off다. --interval 기본값은 검사 완료 후 2초이며, --once가 없으면 상주한다. Ctrl+C 또는 런처의 Console Break로 종료한다.

## 무엇을 검사하는가

- 검사기 자신, 등록부의 런처, entries에 등록된 각 프로세스가 대상이다. PC 전체를 검색하지 않는다.
- restartable=False인 AutoPaint나 실행 중인 ONESHOT도 검사한다. 재시작 목록과 검사 목록은 다르다.
- 프로세스 이름이 아니라 PID와 생성 시각(FILETIME)으로 대상을 확인한다. 생성 시각이 다르면 해당 대상의 디버거 상태를 조회하지 않는다.
- OpenProcess로 QUERY_INFORMATION | SYNCHRONIZE 권한만 요청한다. 메모리 읽기·쓰기, 원격 스레드 생성, 종료 권한은 요청하지 않는다. 관리자 권한이나 SeDebugPrivilege를 자동 활성화하지 않는다.
- 같은 프로세스 핸들로 생성 시각·실행 상태·CheckRemoteDebuggerPresent를 조회한다. API 성공 여부와 실제 debugger_present 반환값은 별개로 처리한다.
- 등록부는 제한된 크기의 JSON으로 읽기만 한다. registry.py를 import하지 않고 잠금·재시작·spawn 함수를 호출하지 않는다.
- 런처의 세션·신원이 확인되지 않으면 자식 등록 목록은 검사하지 않는다. 자기 검사만 성공해도 전체 NORMAL로 처리하지 않는다.

필요한 등록부 필드는 session_id, stopping(bool), launcher_pid, launcher_create_time, entries이다. 각 entry에는 pid와 create_time 정수가 필요하다. 2026-10-06 확인한 main 31dc8332a770f51f2f50856637dcf8a1a4cee102의 등록부로 재검증한다. 운영 모니터는 registry.py를 import하지 않으며, 시험 도구만 승인한 사본의 해시를 확인하고 import한다.

## 결과 해석

| 개별 대상 state | 의미 |
| --- | --- |
| CLEAR | 해당 관측에서 디버거 연결 없음 |
| DEBUGGER_PRESENT | 네이티브 디버거 연결 확인 |
| EXITED | 대상이 종료됐거나 더 이상 실행 중이지 않음. 연결 여부는 null |
| ERROR | 접근 권한, 생성 시각 불일치, API 실패 등으로 판정 못 함. 연결 여부는 null |

전체 status는 연결 확인이 하나라도 있으면 DETECTED, 확인된 연결 없이 검사 오류가 있으면 ERROR, 나머지는 NORMAL이다. 오류와 연결이 동시에 있으면 DETECTED와 scan_complete=false를 함께 기록한다.

종료된 ONESHOT은 치트로 표시하지 않는다. exited_targets에 따로 집계하며 NORMAL은 현재 검사한 실행 중 대상의 결과다. 종료 전 디버거 사용 여부를 증명하지 않는다. stopping=true면 자기 자신만 확인하고 registry_state=STOPPING, scan_complete=false로 남긴다. 정상 종료 중의 생략을 검사 오류로 만들지 않는다.

등록 이름이 여러 개이거나 검사기 자신이 entries에도 있으면 같은 프로세스가 둘 이상의 행에 나올 수 있다. 집계는 대상 행 수이며 고유 프로세스 수가 아니다. 각 행의 role과 PID·생성 시각을 함께 보면 된다.

--once 종료 코드는 0=오류·연결 확인 없음, 1=연결 확인, 2=검사 오류, 3=수집기 실패다. 연결과 오류가 함께 있으면 2이며 이벤트에는 연결 증거를 유지한다. 상주 모드는 연결을 확인했다고 종료하지 않는다. --duration은 테스트용 회차 사이 종료 조건이다.

## 로그와 전송

```text
logs/<session-id>/
├─ session-clock.json
└─ runs/<run-id>/
   ├─ manifest.json
   ├─ events.jsonl
   └─ raw/anti_debug.jsonl
```

정상 결과를 포함해 매 회차 기록한다. 공통 7필드와 raw_score=0을 유지한다. module=selfdefense, evidence.kind=debugger_presence, evidence.component=anti_debug로 구분한다. 디버거 연결은 플레이어 치트 점수가 아니라 보호 프로그램 운영 상태다.

timestamp_ms와 scan_start_ms/scan_end_ms는 세션 시작 후 경과 ms다. 런처의 --t0를 첫 실행부터 전달한다. 같은 세션의 재실행에서는 시간 원점을 재사용하고 run_id를 새로 만든다. sample_id는 각 run의 0부터 시작한다. 다른 player·등록부 경로·시간 원점으로 같은 세션을 재사용하지 않는다.

targets에는 모듈 이름, PID, 기대/관측 생성 시각, 상태, API 오류 코드가 들어간다. 생성 시각은 FILETIME 정수의 정확도를 브라우저에서 잃지 않도록 문자열로 출력한다. 등록부의 argv·경로·접근 키는 Event에 넣지 않는다.

실제 전송은 --telemetry managed로 활성화하며 GZZ_TELEMETRY_URL, GZZ_TELEMETRY_TOKEN, 이 프로세스 전용 GZZ_TELEMETRY_OUTBOX가 필요하다. outbox는 watchdog·integrity와 다른 절대 경로를 쓴다. persistent 재시도는 GZZ_TELEMETRY_RETRY_MODE=persistent로 선택한다. .env는 자동 로딩하지 않는다.

configure_client(ClientConfig.from_env()) 한 번 → 로컬 기록 → send_detection(event) → 종료 시 flush_client/shutdown_client 순서다. 직접 HTTP 코드는 없고 shared가 처리한다. 같은 Python 프로세스의 통합 프로그램이 sender를 소유할 때만 external을 쓴다.

전송 설정 실패 시 로컬 관측은 계속하며 stderr로 알린다. queued는 서버 도착 성공이 아니다. pending/failed와 worker 상태를 확인한다. enqueue 실패한 로컬 로그를 자동 재전송하지 않는다. 자기 확인 모드와 --synthetic는 전송을 금지한다.

## 테스트와 한계

단위 테스트는 통합 레포 최상위에서 실행한다.

```powershell
py -m unittest discover -s client/SelfDefense/anti_debug/tests -p test_anti_debug.py -v
```

tests/native_integration.py는 별도 명령으로 실행하는 시험 도구다. 명시적으로 지정한 커밋·해시와 일치하는 Launcher registry 사본과 새로운 시험용 Python 자식 프로세스만 사용한다. 실제 네이티브 디버깅 상태를 만들고 해제·정리하는 코드는 이 도구에만 있으며 운영 main.py는 사용하지 않는다. [검증 기록](docs/PACKAGE-VALIDATION.md)을 참고한다.

0.1.0-testfix1은 운영 코드·collector_version 0.1.0을 유지한 테스트·문서 보완본이다. 과거 acc9d2a 해시에 고정된 시험 조건을 명시적 승인 해시 인자로 변경했다. [변경 이유·재검사 명령](docs/TESTFIX.md)을 참고한다.

- Python의 pdb/debugpy 등 언어 수준 디버깅, 커널 디버거, 후킹 전체는 이번 보장 범위가 아니다. 네이티브 디버거가 없다는 것이 조작되지 않았다는 뜻은 아니다.
- 관측 사이의 짧은 연결, 등록 전에 끝난 ONESHOT, 등록부에 없는 자식 프로세스는 놓칠 수 있다. 등록부가 전체 모듈을 빠짐없이 담았는지는 현재 알 수 없다.
- 등록부와 PID 확인은 프로세스 선택 근거이지 인증 수단이 아니다. 관리자나 같은 권한의 공격자가 등록부·검사기·Python 실행 환경을 바꾸는 공격을 완전히 방어하지 못한다.
- 권한 차이 때문에 검사 실패할 수 있다. 권한 부족을 악성으로 확정하거나 우회하지 않는다.
- 최대 등록 대상 128개, 등록부 1 MiB다. 초과하면 오류로 보고한다. 검사 시간·디스크 로그·전송량은 실제 통합 환경에서 측정해야 한다.
- 로그 회전/보관 기간과 두 프로세스의 동시 실행 방지는 이 모듈에서 구현하지 않았다. 런처가 단일 실행을 관리한다.

[세 모듈 런처 전달 사항](docs/LAUNCHER_HANDOFF.md), [검토·승인 요청](docs/REVIEW_REQUESTS.md)을 함께 전달한다.

## API 근거

- [CheckRemoteDebuggerPresent](https://learn.microsoft.com/en-us/windows/win32/api/debugapi/nf-debugapi-checkremotedebuggerpresent)
- [GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)
- [Process access rights](https://learn.microsoft.com/en-us/windows/win32/procthread/process-security-and-access-rights)
