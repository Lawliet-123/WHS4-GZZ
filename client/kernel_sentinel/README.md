# KernelSentinel — Launcher / Shared integration

## v4 테스트 환경 자동 준비

이번 테스트 배포는 사용자의 명시적 요청에 따라 Launcher에
`--prepare-test-environment`를 추가합니다. 최초 실행에서 번들 SYS 해시와 공개 인증서
thumbprint를 확인한 후 **TESTSIGNING 활성화 및 해당 인증서의 LocalMachine Root /
TrustedPublisher 등록**을 수행합니다. 기존 "보안 설정을 자동 변경하지 않는다"는
설명은 이 옵션을 사용하지 않는 기본 설치 경로에만 해당합니다.
이 옵션을 포함한 Launcher는 일반 사용자 배포용이 아니라 테스트 전용입니다.
재부팅이 필요하면 REBOOT_REQUIRED로 수집을 중단합니다. 작업을 저장하고 Windows를
재부팅한 뒤 새 세션으로 다시 실행하세요. 강제 재부팅은 없습니다.
Secure Boot가 BCD 변경을 거부하면 중단하며 Secure Boot/Defender/BitLocker/메모리
무결성/영구 실행 정책을 자동 변경하지 않습니다. 준비·설치용 자식 PowerShell에만 임시 Bypass를 적용합니다. 옵션을 제거하면 기존 엄격한 설치 경로로 돌아갑니다.

실행 위치는 저장소의 `client/kernel_sentinel`입니다. 원본 드라이버/센서 소스는
KernelSentinel 0.2.3이며, 이번 변경은 Python 연동과 승인 드라이버 자동 설치입니다.
기존 `KernelSentinelValidation-github` 실험 자료는 삭제하지 않습니다.

## 최초 실행에서 PowerShell 실행 정책으로 차단될 때

준비/설치 스크립트가 `PSSecurityException`, "스크립트를 실행할 수 없습니다" 등의
오류로 차단될 수 있습니다. 최신 호출부는 준비와 설치용 자식 PowerShell에만
`-ExecutionPolicy Bypass`를 전달합니다. Launcher 등록부를 수정할 필요는 없으며,
사용자의 CurrentUser/LocalMachine 실행 정책은 변경하지 않습니다. SYS 고정 해시와
서명·인증서 검증은 기존대로 수행합니다.

수정 전 버전이나 직접 실행에서는 **관리자 PowerShell의 같은 창**에서 다음을
실행한 뒤 기존 Launcher/준비 명령을 실행하세요.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
# 이어서 같은 창에서 기존 Launcher/준비 명령 실행
```

이 설정은 현재 PowerShell 세션에만 적용되며 창을 닫으면 사라집니다. 직접 다른
PowerShell을 호출하는 명령에 `-ExecutionPolicy RemoteSigned`가 있다면 이 값이
별도 자식 정책을 지정하므로 아래처럼 해당 호출을 바꿔야 합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <준비_스크립트.ps1> <기존_인자>
```

조직의 MachinePolicy/UserPolicy가 적용된 PC에서는 Process/호출 인자로 이를
덮어쓸 수 없습니다. 계속 차단되면 `Get-ExecutionPolicy -List`를 확인하고 관리자에게
승인된 실행 방법을 요청하세요. 그룹 정책을 자동 변경하지 않습니다.

`TEST_SIGNING_REBOOT_REQUIRED`는 정책 차단과 다른 상태입니다. 해당 메시지면 작업을
저장하고 Windows를 재부팅한 뒤 새 세션으로 다시 실행하세요. 사용자 보고에서는
재부팅 후 정상 실행됐지만, 다른 PC에서의 설치/로드 성공까지 검증된 것은 아닙니다.

공식 정책 범위/우선순위:
https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_execution_policies

## 준비 및 최초 설정

**배포 ZIP은 최초 실행 자동 설치를 지원합니다.** `artifacts`에 SYS/INF/CAT과 고정 해시
manifest가 함께 포함된 배포 묶음을 적용하면 경로/해시 환경변수 없이 Launcher를
관리자로 실행하면 됩니다. 서명·해시 확인 후 설치/로드를 수행합니다. 첨부 SYS는 사용자
제공 테스트 인증서 빌드이며 조직 승인·실제 Windows 로드 검증을 주장하지 않습니다.
일반 새 PC에서 테스트 인증서를 신뢰하지 않으면 실행을 중단합니다. 인증서/OS 보호를
자동 변경하지 않습니다. 아래 빌드/환경변수 절차는 소스만 받은 PC나 다른 승인 빌드를
사용하는 경우에 해당합니다. SYS 경로/해시를 명시하면 번들 기본값보다 우선합니다.

Windows x64, 64비트 Python 3.10+, 관리자 권한이 필요합니다. 드라이버 빌드는
Visual Studio C++와 연동된 Windows SDK/WDK가 필요합니다. `.sys`와 인증서 개인키는
소스 PR에는 포함하지 않습니다. 배포 ZIP에는 사용자 제공 SYS/INF/CAT을 포함하며,
인증서 개인키는 포함하지 않습니다. 일반 배포는 서명된 승인 빌드를 사용해야 합니다.

```powershell
cd <저장소>\client\kernel_sentinel
.\scripts\Build.ps1 -Configuration Debug
Get-ChildItem .\build\x64\Debug -Recurse -Filter KernelSentinel.sys
```

빌드 출력에서 실제 사용할 SYS를 선택하고 관리자 PowerShell에서 아래를 설정합니다.
파일의 출처와 서명을 확인한 **해당 빌드**의 해시를 사용해야 합니다. 해시는 재빌드마다
바뀔 수 있습니다. 계산한 해시만으로 파일 출처가 신뢰된다고 판단하지 않습니다.

```powershell
$env:GZZ_KERNEL_DRIVER_PATH=(Resolve-Path '<서명된 KernelSentinel.sys 실제 경로>').Path
Get-AuthenticodeSignature -LiteralPath $env:GZZ_KERNEL_DRIVER_PATH | Format-List
$env:GZZ_KERNEL_DRIVER_SHA256=(Get-FileHash -LiteralPath $env:GZZ_KERNEL_DRIVER_PATH -Algorithm SHA256).Hash.ToLowerInvariant()
```

`EnsureDriver.ps1`은 관리자 권한, 고정 해시, `Authenticode Status=Valid`를 확인하고,
Windows drivers 폴더로 복사·재검증한 뒤 수동 시작 서비스를 등록/시작합니다.
이미 실행 중인 서비스는 실제 서비스 경로 파일의 해시·서명을 검사하고 재사용합니다.
실행 중인 다른 버전을 강제로 중지/교체하지 않습니다. 업데이트 시에는 기존 수집기를
종료하고 `sc.exe stop KernelSentinel`을 실행한 후 재시작하세요.

테스트 인증서가 검증되지 않거나 OS가 로드를 거부하면 중단합니다. TESTSIGNING,
Secure Boot, 메모리 무결성, 인증서 신뢰, 실행 정책은 자동 변경하지 않습니다.
서명/로드 조건은 별도 테스트 환경에서 준비해야 합니다. Authenticode 확인은 OS의
최종 커널 로드 허용과 별개이며 `sc.exe start` 실패도 실행 실패로 처리합니다.

## Launcher 실행

동일 관리자 PowerShell에서 저장소 루트로 이동하고 팀 서버 환경변수를 설정한 후:

```powershell
py -3 client\Launcher\main.py --session kernel_e2e_001 --player player_001 --only kernel_watcher --no-launch-game
```

Launcher 등록 이름은 **kernel_watcher**를 유지하고 공통 Event의 module은
**kernel_sentinel**입니다. 최신 Dashboard의 기존 alias에 맞춘 것이며, 서로 다른
드라이버를 두 개 실행하는 설정이 아닙니다. 실행 경로는 새 폴더입니다.
Launcher는 공통 `--t0`, `--telemetry managed/off`, PID, 세션, 플레이어,
전용 OUTBOX, 저장소 PYTHONPATH를 전달하고 `--install-driver`를 사용합니다.
19045 이외의 Windows 빌드는 Launcher가 전용 스레드 센서를 비활성화합니다.
종료 시 수집기 정책 해제·Shared flush/shutdown을 수행합니다. 드라이버 서비스는
재사용을 위해 로드된 상태로 남습니다. 필요 없으면 수집기 종료 후 수동으로 중지합니다.

직접 실행하는 경우 작업 폴더에서:

```powershell
py -3 -m agent.main watch --install-driver --pid 1234 --mode observe --session-id kernel_e2e_001 --player-id player_001 --t0 <Launcher의 Unix초 t0> --telemetry managed --out runs/kernel_e2e_001
```

직접 실행에는 `shared`를 찾도록 저장소 루트의 PYTHONPATH와 전용
GZZ_TELEMETRY_OUTBOX도 필요합니다. 서버를 사용하지 않으면 `--telemetry off`.
출력 폴더는 새 폴더여야 합니다. 자동 재시작은 비활성화되어 있습니다.

## 출력 및 해석

- `raw_events.jsonl`: 기존 센서 기록 + 전송/종료 진단
- `common_events.jsonl`: 공통 7필드, 원시 reason 점수 보존
- `feature_status.json`, `feature_status.md`: 종료 후 센서 상태표

각 진단 사이클마다 점수 이벤트를 추가하며 정상 관측의 0점도 전송합니다.
사이클 점수는 해당 사이클 양수 근거의 최대값이며 누적 밴 점수가 아닙니다.
읽기/검사 실패와 관측 공백은 `measurement_valid=false`로 표시합니다.
정상 0점은 그 사이클에 설정된 관측 범위의 무일치이며 시스템 전체의 무결성 보장이 아닙니다.
Shared send 전에 로컬 공통 파일을 flush하고, Shared가 canonical UUID를 생성합니다.
`server_queued`는 로컬 대기열 저장입니다. 실제 수신은 서버 기록과 종료 flush로 확인하세요.
네트워크 지속 재시도가 필요하면 `GZZ_TELEMETRY_RETRY_MODE=persistent`를 설정합니다.

기존 findings는 5초 rate limit을 유지합니다. cycle 결과는 rate limit 없이 계산 시점마다
기록합니다. `timestamp_ms`는 Launcher t0 기준이고 드라이버 이벤트는 취득 시각을
사용합니다. 추가 메타데이터는 evidence 안에만 넣습니다. 실제 cheat ON 시각을 추정하지 않습니다.

## 검증 범위와 남은 연동

이 패키지는 Python/프로토콜/휴대 C 테스트 84개와 Launcher 인자 계약을 검증했습니다.
합성 센서 입력 → Shared 실제 대기열 → loopback HTTP Receiver → Scoring DB 저장도 검증했습니다.
Windows WDK 빌드·SYS 서명·자동 설치 실행·실게임·Windows Ctrl+Break는 여기서 검증하지 않았습니다.

별도 서버 수정본에는 `kernel_sentinel`의 소스 기준 잠정 Scoring 정책이 추가되었습니다.
적용 여부와 해석은 `server/scoring/policies/KERNEL_SENTINEL_POLICY.md`를 참고하세요.
서버 수정본을 적용하지 않았다면 `UNKNOWN_MODULE` 등 미해결 상태가 남을 수 있습니다.
실제 Windows 전체 Launcher·운영 서버의 최종 판정 E2E 완료를 주장하지 않습니다.
승인된 방어 모듈 접근 분류와 남은 확인 사항은 `docs/APPROVED_ACCESS_VALIDATION.md`를 참고하세요.

## 회귀 테스트

```powershell
# cwd: client/kernel_sentinel, PYTHONPATH: 저장소 루트
py -3 -m pip install -r ..\..\server\requirements-dev.txt
py -3 -m unittest discover -s tests -p 'test_*.py'
```

원본 센서 범위·한계는 LEGACY_README_v0.2.3.md와 docs/를 참고하세요.

시작 실패는 stderr 및 `runs/<session>.startup.jsonl`에서 단계명과 WinError를 확인하세요.
승인 바이너리 준비·전체 Launcher 동시 실행·SelfDefense·양성 검증의 미완료 범위는
APPROVAL_AND_LIVE_VALIDATION.md에 정리되어 있습니다.
