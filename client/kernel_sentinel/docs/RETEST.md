# v0.2.2 재시험 절차와 합격 기준

이번 패치의 성공은 증거 분리·연결·호출 원인 조사 가능성으로 평가한다.
정상 로드 드라이버의 커널 읽기를 탐지하는 센서가 생겼다고 평가하지 않는다.
과거 PID 6056은 새 실행의 PID가 아니므로 새 프로세스 신원을 사용한다.

## 1. 새 센서/수집기 준비

기존에 빌드 성공한 Windows 10 19045 x64 테스트 VM/WDK 구성을 유지한다.
이 소스의 Windows 빌드와 VM 실행은 아직 검증되지 않았다.

1. 새 폴더에 패키지를 풀고 기존 WDK 구성으로 KernelSentinel을 빌드한다.
2. 기존 수집기를 Ctrl+C로 종료하고 `sc.exe stop KernelSentinel` 후, 새 `.sys`를 기존 설치 스크립트로 등록/로드한다. 실제 빌드 출력 경로를 사용한다.
3. 새 `agent`, `tools`를 함께 사용한다. 실제 게임을 실행하고 PID를 구한다.
4. 시험 드라이버를 새로 로드하기 **전**에 아래 수집기를 시작한다. 이미 로드되어 있으면 목록은 보여도 새로운 load 이벤트가 없을 수 있으므로 새 VM 실행/정상 언로드·재로드로 구분한다.

```powershell
# 같은 이름의 게임 인스턴스가 하나인 시험에서 사용
$gameProcesses = @(Get-Process -Name 'PenguinHotel-Win64-Shipping')
if ($gameProcesses.Count -ne 1) { throw 'Select exactly one target game process.' }
$gamePid = $gameProcesses[0].Id
python -m agent.main watch --pid $gamePid --mode observe --session-id mecha_kernel_002 --player-id player_local --out .\runs\mecha_kernel_002
```

이 PowerShell은 수집 전용으로 둔다. 별도 관리자 PowerShell에서 시험 드라이버/클라이언트를 실행한다.
`--kernel-only`는 게임 핸들 수집 검증에 쓰지 않는다. 기존 정책 이름/해시 목록은 기록하고, 이 사례에서는 빈 정책으로 비교한다.

새 핸들 로그에서 `flags & 0x100`이 참이고 `actor_tid`와 `actor_create_time`이 있어야 새 드라이버가 사용된 것이다.
`session_start`의 `agent_version=0.2.2`만으로 드라이버 업데이트가 확인되는 것은 아니다.

## 2. 호출 구간 기록용 클라이언트 만들기

원래 v3 시험 프로젝트를 별도 폴더에 복사한다. 아래 경로는 그 **복사본**으로 바꾼다.
패치 도구는 알려진 소스 패턴이 각각 한 번씩 없으면 중단하므로 다른 버전에는 수동 확인 없이 적용하지 않는다.

```powershell
$probeCopy = Join-Path $env:USERPROFILE 'Desktop\ChameleonKernelProbe-audit'
python .\tools\instrument_client.py --source "$probeCopy\client\ChameleonCoord.cpp" --out .\instrumented_client
Copy-Item .\instrumented_client\ChameleonCoord.cpp "$probeCopy\client\ChameleonCoord.cpp"
Copy-Item .\instrumented_client\client_call_trace.h "$probeCopy\client\client_call_trace.h"
```

원래 프로젝트의 x64 클라이언트 빌드 절차로 복사본 클라이언트를 재빌드한다.
새 클라이언트를 시작한 현재 작업 폴더에 `client_calls_<PID>_<생성FILETIME>.jsonl`이 생긴다.
로깅 파일 생성 실패는 종료 코드 3, 중간 쓰기 실패는 표준 오류로 알린다. 그 경우 호출 구간 증거는 불완전하다.
인수는 기존 `<game-pid> [seconds] [log.csv]` 그대로다.

```powershell
# 이 창에서 게임 PID를 다시 확인해 지정한다. 아래 2376은 예시다.
$gamePid = 2376
$clientExe = "$probeCopy\build\client\ChameleonCoord.exe"
Get-FileHash -Algorithm SHA256 -LiteralPath $clientExe
# 실제 로드할 드라이버 파일도 같은 방식으로 해시를 보관
& $clientExe $gamePid 60 .\coord_read_002.csv
```

기록 API: OpenProcess, QueryFullProcessImageNameW, GetProcessTimes, CreateToolhelp32Snapshot, Module32FirstW.
각 begin/end에 PID, 생성 시각, TID, 대상 PID, UTC FILETIME, QPC/frequency, 요청 권한, 반환값, LastError가 있다.
성공 시 LastError는 이전 값일 수 있으므로 실패 여부는 API 반환 규칙으로 판단한다.
API 구간 로그는 메타데이터 호출만 계측하며 DeviceIoControl/커널 메모리 복사를 관측하지 않는다.
WriteFile 기록 비용으로 타이밍이 달라질 수 있으므로 계측 없는 실행도 별도 보관한다.

## 3. 분석 및 출력

```powershell
python .\tools\summarize_run.py --raw .\runs\mecha_kernel_002\raw_events.jsonl
python .\tools\correlate_evidence.py --raw .\runs\mecha_kernel_002\raw_events.jsonl --coord .\coord_read_002.csv --calls .\client_calls_실제PID_실제생성시각.jsonl --driver-name ChameleonKernelProbe.sys --client-name ChameleonCoord.exe --out .\analysis\mecha_kernel_002
```

`--calls`에는 이번 실행의 실제 파일 이름을 넣는다. 없는 경우 옵션 자체를 생략한다.
각 재시험은 새 세션/새 출력 폴더/새 CSV로 실행하며 로그를 합치지 않는다.

| 추가/변경 로그 | 기대 내용 | 잘못된 판정 예 |
|---|---|---|
| raw handle pre/post | actor TID/생성 시각, 기존 operation ID와 요청/부여 권한 | `thread_id=0`을 호출자 TID로 사용 |
| client_call begin/end | 정확한 API명/요청 마스크와 실행 구간 | API 구간 일치만으로 원인을 확정 |
| evidence.json links | 시퀀스·세대, 수명/신원, 시간 후보 연결, 근거·한계, 추가 점수 0 | 같은 basename/가까운 시각만으로 동일 원인 확정 |
| report.md/timeline.jsonl | 로드/핸들/외부 CSV 분리, 원시 행, 시간 근거 | 외부 CSV 성공을 Sentinel 직접 탐지로 표시 |
| summary/session | 직접 읽기 센서 `not_implemented`, 관측 `unavailable` | 직접 센서가 없는데 읽기 탐지 0회=읽기 없음으로 표시 |

## 4. 0x1FFFFF의 실제 호출 스택 확인

새 x64 클라이언트를 유저 모드 WinDbg로 **초기화 전에** 시작한다. 뒤늦게 attach하면 초기 핸들을 놓칠 수 있다.
인수는 기존 게임 PID·기간·CSV 경로를 준다. 초기 중단에서:

```text
.logopen /t C:\Temp\client_native_calls.txt
bp ntdll!NtOpenProcess
g
```

해당 진입에서 x64 호출 규약에 따라 DesiredAccess는 RDX, CLIENT_ID 포인터는 R9다.
현재 PID/TID, 대상 CLIENT_ID, 요청 권한과 호출 스택을 함께 저장한다.

```text
.time
~.
r rdx r9
dq @r9 L2
kb
```

CLIENT_ID의 첫 값이 게임 PID인지 확인한다. 게임 PID와 비교할 때 디버거의 16진수 표시를 고려한다.
호출을 계속 진행해 `handle_pre/post`의 시각/신원/요청 권한과 대조한다.
`0x1FFFFF`가 다시 없으면 과거 원인이 확인된 것이 아니라 이번 실행에서 재현되지 않은 것이다.
클라이언트가 직접 syscall 하거나 커널 경로에서 별도로 생성한 핸들은 이 사용자 모드 중단점으로 모두 보인다고 보장할 수 없다.
그 경우 커널 디버거로 범위를 넓히거나 원인을 미확인으로 유지한다. 디버거 자체의 추가 핸들 접근도 actor로 구분한다.

Toolhelp 프레임→NtOpenProcess와 대상/권한까지 확인되면 **그 재시험 호출**에 한해 Toolhelp 경로라고 기록할 수 있다.
`0x1000` 호출만 관찰하거나 스택을 얻지 못했으면 추측으로 `0x1FFFFF`를 설명하지 않는다.
자동 분석기는 스택 파일을 해석하지 않으므로 그 결론은 별도 수동 검토 기록으로 남긴다.

공식 함수 정의: [NtOpenProcess](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/nf-ntddk-ntopenprocess).

## 5. 검증 기준

| 시험 | 합격해야 할 결과 | 직접 읽기 탐지 평가 |
|---|---|---|
| 게임+정상 수집만 | 정상 이벤트, 데이터 손실/스캔 실패 명시, 로드만으로 높은 확신 경고 없음 | 평가 불가 |
| 시험 드라이버 로드만, 클라이언트 미실행 | 로드/목록/파일 증거만; 클라이언트 읽기 추론 없음 | 관측 공백 유지 |
| 핸들만 열고 닫기 (`tools/probe_handles.py`) | 권한별 핸들 결과/기존 낮은 검토 점수 가능, 읽기 성공으로 판정하지 않음 | 실제 메모리 읽기 없음; 거짓 직접 탐지 금지 |
| 기존 읽기 시험 반복 | 외부 성공 CSV와 대상 신원·시간 일치, 핸들 구간 후보 기록 가능 | `direct_read_detection_gap=true` 유지 |
| API 로그/스택 누락 | 원인 `unidentified`; 누락을 정상 원인 확인으로 대체하지 않음 | 무관 |
| PID 재사용·다른 생성 시각·잘못된 세대/시퀀스 | 강한 연결 거부, 독립 사실 유지 | 거짓 귀속 금지 |
| 큐 손실/ETW 손실/기록 실패 | 데이터 품질 불완전, 검증 불충분 표시 | 해당 구간 탐지율 주장 금지 |
| 별도 커널 디버거 또는 향후 협력적 계측 | 특정 복사의 대상 신원/주소/요청/결과 바이트 및 호출 근거 확보 | 외부 정답 자료의 관측. Sentinel 독립 센서와 분리 |

읽기 탐지 기능의 합격을 주장하려면 외부 정답 요청과 독립 센서 이벤트를 대응시켜야 한다.
현재 버전은 그 센서가 없으므로 높은 탐지율 또는 "커널 읽기 탐지 성공"을 산출하지 않는다.
센서 개발 후에도 검증 단위는 좌표 요청/단계별 복사/바이트 접근 중 하나로 미리 고정한다.
예를 들어 하나의 좌표 요청에는 여러 포인터 읽기가 포함되므로 CSV 102행을 메모리 접근 102회로 단정하지 않는다.

성능은 계측/디버거 없는 v0.2.1과 v0.2.2를 같은 시간·게임 동작·스캔 주기로 비교한다.
수집 주기 p50/p95, 로그량, CPU, 게임 프레임 시간, 손실/실패를 기록하고 최소 3회 반복해 분산을 함께 본다.
사용 중인 팀 성능 예산과 정상 실행 변동 폭을 기준으로 통과 여부를 정한다. 이 패키지는 미측정 오버헤드 비율을 제시하지 않는다.
새 커널 필드는 이벤트 크기를 늘리지 않고 스택 수집/디스크 I/O를 콜백에 추가하지 않았지만, 실제 지연과 안정성은 Windows 실행으로 확인해야 한다.
