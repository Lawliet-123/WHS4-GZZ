# KernelSentinel 0.2.3 — 기능별 실행 상태표

> 이 문서는 KernelSentinel의 버전별 원본 설명입니다. 공개용 저장소 구성과 이후 검증 범위는 [저장소 루트 README](../README.md)를 먼저 확인하세요.

**0.2.3 안내: [FEATURE_STATUS_v0.2.3.md](FEATURE_STATUS_v0.2.3.md).**
수집 종료 시 기능별 상태표를 출력하고 `feature_status.md`와 `feature_status.json`을 저장합니다.
기존 로그도 `python .\tools\summarize_run.py --raw <raw_events.jsonl> --format table`로 확인합니다.
이번 변경은 Python 수집/요약 기능이며 드라이버 소스는 0.2.2와 동일합니다. 하이퍼바이저 개발은 보류했습니다.

**이번 변경은 [PATCH_v0.2.2.md](PATCH_v0.2.2.md)를 먼저 읽으세요.**
로드·핸들 접근·외부 읽기 보고를 분리하는 분석기, 요청 TID/프로세스 생성 시각,
재시험용 클라이언트 API 구간 기록 도구를 추가했습니다.
**정상 로드된 다른 드라이버의 게임 메모리 읽기를 직접 관측하는 센서는 미구현입니다.**
실험 분석은 [docs/EXPERIMENT_REVIEW.md](docs/EXPERIMENT_REVIEW.md),
관측 방법 검토는 [docs/OBSERVATION_METHODS.md](docs/OBSERVATION_METHODS.md),
재시험 절차는 [docs/RETEST.md](docs/RETEST.md)에 있습니다.

**커널 담당 독립 실행은 [KERNEL_ROLE_v0.2.1.md](KERNEL_ROLE_v0.2.1.md)를 읽으세요.**
`watch --kernel-only`로 게임 PID 없이 기존 커널 센서를 실행합니다. 포함된 커널
드라이버는 0.2.2에서 핸들 이벤트의 호출 실행 문맥 필드를 추가했습니다.

**0.2 적용 안내는 [PATCH_v0.2.md](PATCH_v0.2.md)를 먼저 읽으세요.** 커널 시스템 스레드 시작 주소 검사와 OB 콜백 동작 점검을 추가했습니다. 새 스레드 센서는 Windows 10 빌드 19045 x64 전용입니다. 이 전체 패키지는 이전 캐시 및 Windows 10 호환성 수정을 포함합니다. 아래는 기존 기능의 설명이며, 새 버전의 Windows 빌드·실제 치트 탐지 검증은 아직 완료되지 않았습니다.

기존 KernelWatcher를 덮어쓰지 않는 새 프로젝트입니다. **C 커널 드라이버 + Python 제어·수집기**가 한 세트이며, 게임 SDK·오프셋·pymem 없이 실행 중인 게임 PID를 지정합니다. 게임이 설치된 PC에서만 해당 게임을 보호합니다.

**소스 패키지입니다. Windows WDK 빌드·드라이버 로드·게임 호환성 검증은 아직 하지 못했습니다.** 이 패키지에서 수행한 검증은 Python 분석·프로토콜 테스트와 휴대 가능한 C 권한 정책/구조체 크기 검사입니다. `.sys` 바이너리는 포함하지 않았습니다.

## 1. 구현한 기능과 정확한 범위

| 기능 | 구현 | 해석·한계 |
|---|---|---|
| 게임 등록 | PID + 생성 시각 검증, 참조된 프로세스 객체로 보호 대상 비교 | PID 재사용으로 다른 게임을 보호하는 문제를 줄임. Python도 실제 프로세스 핸들을 보유 |
| 프로세스 생성·종료 | `PsSetCreateProcessNotifyRoutineEx` | 생성 이름 패턴 검사; 이름만 일치하면 검토 신호이며 핵 확정 아님 |
| 게임 핸들 감시 | `ObRegisterCallbacks`, 프로세스·스레드 핸들 생성/복제 pre/post | 요청 권한과 실제 부여 권한을 구분. 성공/실패, 커널 핸들, 자기 접근, 복제 수신 PID 기록 |
| 게임 핸들 제한 | 명시적 `enforce` 모드에서 일부 위험 권한 제거 | 이미 보유한 핸들 회수, 커널 직접 메모리 접근 차단, 모든 주입 차단은 제공하지 않음 |
| 게임 DLL·드라이버 로드 | `PsSetLoadImageNotifyRoutine` | 정상 로더가 알려주는 매핑 알림. DLL 로드만으로 인젝션 확정 불가. 수동 매핑 전부 탐지 불가 |
| 게임 스레드 생성·종료 | `PsSetCreateThreadNotifyRoutine` | 스레드 변화 기록. 스레드 시작 주소나 원격 주입 확정 기능 아님 |
| 커널 함수 진입부 변화 | 고정된 4개 export 첫 32바이트 비교 | 드라이버 로드 시점 대비 변화. 모든 함수·SSDT·IDT·전체 커널 메모리를 검사하는 것은 아님 |
| 커널 모듈 실행 코드 변화 | 로드된 모듈을 순환하며 **메모리에 있는 비폐기 실행 섹션의 SHA-256** 비교 | 첫 성공 검사 대비 변화. 정상 핫패치 등도 변화로 나올 수 있음. 섹션 설명자도 해시에 포함 |
| 자체 디스패치 변조 | 자신의 CREATE/CLOSE/DEVICE_CONTROL 포인터 기준값 및 소속 모듈 확인 | 다른 모든 드라이버 디스패치나 콜백 테이블 감시 아님 |
| 드라이버 목록 교차 검사 | AuxKlib → PSAPI → AuxKlib, 3회 연속 차이 기록 | **조회 불일치 조사 기능. 은닉 드라이버 확정 탐지 아님** |
| 알려진 파일 식별 | 사용자 정책의 이름 패턴·SHA-256 비교 | 빈 기본 정책 제공. 확인된 샘플 해시는 사용자가 등록 |
| 디지털 서명 참고 정보 | `--verify-signatures`로 파일 Authenticode 결과 비동기 수집 | 신뢰·서명 상태가 핵 여부를 결정하지 않음 |
| 단독 로그 및 팀 연계 | raw JSONL + 기존 공통 이벤트 형식 | 점수는 검토 우선순위이며 자동 밴 없음 |

### 요청한 커널 변조·후킹·은닉 탐지와 연결되는 부분

1. `kernel_entry_bytes_changed_since_driver_load`: 선택한 커널 함수의 진입부 변경입니다. 실제 메모리를 읽은 결과이며 인라인 패치 조사에 사용할 수 있습니다. 원인과 악성 여부는 별도 분석해야 합니다.
2. `kernel_executable_sections_changed_since_first_scan`: 로드된 커널 모듈의 실행 섹션 전체 범위에서 SHA-256이 달라진 경우입니다. 디스크 파일 해시와 달리 **커널 메모리의 코드 내용**을 검사합니다. 주소 재배치가 반영된 메모리 기준이므로 디스크 해시와 직접 비교하지 않습니다.
3. `sentinel_dispatch_pointer_changed_since_driver_load`: 이 드라이버 자신의 IRP 디스패치 포인터 변경입니다.
4. `sentinel_dispatch_outside_listed_modules`: 자체 포인터가 조회된 모듈 범위 밖을 가리키는 경우입니다. 목록 누락 또는 변조 조사 신호이지 곧바로 숨겨진 드라이버를 발견했다는 뜻은 아닙니다.
5. `driver_view_mismatch`: 커널/유저 조회 결과의 지속적 차이입니다. 두 조회가 같은 기반 목록의 영향을 받을 수 있어, 양쪽에서 함께 숨긴 드라이버는 잡지 못합니다. 일반 목록에 전혀 등장하지 않는 수동 매핑 코드도 이 검사와 모듈 해시 검사에서 빠질 수 있습니다.

**이 버전은 임의 커널 데이터 변조·전체 후킹·완전한 은닉 드라이버 탐지를 해결한 제품이 아닙니다.** 커널 권한의 공격자가 이 드라이버·수집기까지 변조하면 동일 권한의 관찰 결과를 완전히 신뢰할 수 없습니다.

## 2. 빌드 PC에서 할 일

요구 사항: x64 Visual Studio C++ + 해당 Visual Studio에 연동된 WDK, 서로 맞는 Windows SDK/WDK 헤더·라이브러리. 실행 대상은 **Windows 10 2004 이상 x64**입니다(`ExAllocatePool2` 사용). Python은 64비트 3.10 이상, 외부 패키지는 없습니다.

1. 압축을 새 `KernelSentinel` 폴더로 풉니다.
2. `KernelSentinel.sln`을 열고 `Debug / x64`를 선택합니다.
3. 프로젝트의 Windows SDK 버전을 이전에 빌드 성공한 SDK/WDK 버전으로 선택하고 빌드합니다.

또는 프로젝트 루트에서 PowerShell:

```powershell
.\scripts\Build.ps1
# 자동 선택이 원하는 버전과 다르면 실제 설치된 버전 지정
.\scripts\Build.ps1 -SdkVersion 10.0.26100.0
```

예시 버전은 고정 요구 사항이 아닙니다. `Build.ps1`은 설치된 `km\ntddk.h`, UCRT, 커널 라이브러리가 함께 있는 버전을 찾습니다. WDK 도구 집합 자체가 Visual Studio에 연동되지 않은 문제까지 설치·수정하는 스크립트는 아닙니다.

예상 결과:

```text
build\x64\Debug\KernelSentinel.sys
build\x64\Debug\KernelSentinel\KernelSentinel.sys  (WDK 패키지 출력이 있을 때)
```

링크 의존성 `Aux_Klib.lib`, `Wdmsec.lib`, `Cng.lib`, `/INTEGRITYCHECK`, SHA256 테스트 서명, 패키지 SYS 포함 설정을 프로젝트에 넣었습니다. 기존 `KernelWatcher.vcxproj`를 교체할 필요는 없습니다.

## 3. VMware에서 설치·실행

현재 쓰던 **테스트 서명 설정된 VM**으로 빌드 결과와 프로젝트의 `agent`, `config`, `scripts`, `tools` 폴더를 복사합니다. 드라이버 검증 전에 VM 스냅샷을 남깁니다. 기존 KernelWatcher 수집기를 종료하고 해당 서비스를 중지한 상태로 새 버전을 검증하세요.

관리자 PowerShell에서 새 프로젝트 루트로 이동한 뒤:

```powershell
# SysPath는 실제 복사한 파일 위치로 맞추기
.\scripts\Install.ps1 -SysPath .\KernelSentinel.sys
sc.exe query KernelSentinel
```

설치 스크립트는 수동 시작 서비스만 등록합니다. 인증서 신뢰 설정, 테스트 서명, Secure Boot, 메모리 무결성 설정을 바꾸지 않습니다. 기존 테스트 인증서 신뢰/서명 조건을 만족해야 로드됩니다. `RUNNING`은 로드 성공이며 탐지 정확성 검증과는 별개입니다.

게임 실행 후 실제 게임 프로세스를 확인합니다. 런처 PID와 구분하세요.

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*Meccha*' } |
    Select-Object Id, ProcessName, Path
```

처음은 감시 모드로 실행합니다. 아래 `1234`는 실제 게임 PID로 바꿉니다.

```powershell
python -m agent.main watch --pid 1234 --mode observe --session-id normal_001 --player-id player_local --out .\runs\normal_001
```

종료: **Ctrl+C**. 종료 시 정책을 해제하고 장치 핸들을 닫습니다. 드라이버 서비스는 계속 로드되어 있으므로 더 이상 필요 없으면:

```powershell
sc.exe stop KernelSentinel
# 서비스 등록도 지울 때만
sc.exe delete KernelSentinel
```

수집기가 죽거나 닫히면 장치 CLEANUP에서 보호 대상이 해제됩니다. 핸들이 남은 채 수집 프로세스가 멈추는 경우에도 heartbeat가 10초 끊기면 권한 제한이 중단됩니다. 이는 게임/OS 동작을 우선하는 fail-open 동작이며 수집기 자기 보호/우회 방지 기능은 아닙니다.

## 4. 권한 제한 모드

감시 모드에서 정상 게임·런처·오버레이 영향을 먼저 확인한 다음, 같은 게임의 **정확한 실행 파일 경로**를 지정합니다.

```powershell
python -m agent.main watch --pid 1234 --mode enforce --expected-image "C:\Games\Meccha\Game.exe" --session-id enforce_001 --out .\runs\enforce_001
```

제거하는 권한:

- 프로세스: `TERMINATE`, `CREATE_THREAD`, `VM_OPERATION`, `VM_WRITE`, `DUP_HANDLE`, `SUSPEND_RESUME`.
- 스레드: `TERMINATE`, `SUSPEND_RESUME`, `SET_CONTEXT`.
- 커널 핸들과 게임의 자기 자신에게 부여되는 접근은 제한하지 않습니다. 게임에서 외부 프로세스로 복제하는 경우는 수신 대상도 확인합니다.
- `PROCESS_VM_READ`는 감시만 합니다. 이 구현은 문서화된 OB 수정 가능 권한에 맞춰 제한하므로 메모리 읽기 기반 ESP 전체 차단을 주장하지 않습니다.
- 핸들 열기가 성공해도 일부 권한이 제거되었을 수 있습니다. pre의 요청값만 보지 말고 post의 `status`와 `granted_access`를 확인합니다.
- 감시 시작 전에 획득한 핸들, 게임 내부 기존 코드, 커널 드라이버 직접 접근, 일부 상속 경로 등은 이 정책만으로 해결되지 않습니다.

## 5. 로그 읽기와 검사 주기

```powershell
Get-Content .\runs\normal_001\common_events.jsonl

Get-Content .\runs\normal_001\raw_events.jsonl |
    ConvertFrom-Json |
    Where-Object type -eq 'kernel_code_scan' |
    Select-Object path, status, flags, bytes_hashed, module_count

Get-Content .\runs\normal_001\raw_events.jsonl |
    ConvertFrom-Json |
    Where-Object type -eq 'kernel_diagnostics' |
    Select-Object module_status, valid, changed, failed, dispatch
```

`kernel_code_scan`:

| 값 | 의미 |
|---|---|
| `status=0, flags=4` | 첫 성공 검사로 기준 생성. 아직 이전 기준과 비교한 결과가 아님 |
| `status=0, flags=1` | 기존 기준과 비교했고 동일 |
| `status=0, flags=3` | 기존 기준과 달라짐. 검토 필요 |
| `status!=0` | 읽기 실패·모듈 변동·범위 제한 등으로 검사 불완전. 정상/악성 판정에 사용하지 않음 |

기본 5초 주기마다 모듈 2개를 순환 검사합니다. 모듈 수가 200개이면 단순 계산으로 한 바퀴 약 500초, 실제로는 처리 시간과 로드 변화에 따라 더 길어집니다. **첫 바퀴는 기준 수집이므로 변화 비교는 다음 방문부터** 가능합니다. 부하를 보면서 `--interval 2 --code-per-cycle 4` 등으로 조정할 수 있습니다. `--code-per-cycle 0`이면 전체 실행 섹션 해시 검사를 끄고 나머지 진단은 유지합니다.

한 모듈당 해시 대상 실행 섹션 합계 최대 16 MiB, 섹션 최대 96개, 모듈/기준 캐시 최대 1024개입니다. 폐기 가능한 INIT 같은 섹션은 제외합니다. 파일 내용이 아니라 메모리에서 각 비폐기 실행 섹션의 `VirtualSize` 범위를 읽습니다. 페이지 읽기 실패나 순회 중 언로드/변경은 불완전 상태로 기록합니다. 스냅샷은 원자적이지 않으므로 일시 변경과 원상복구를 놓치거나, 검사 중 변화 때문에 일관되지 않은 결과가 나올 수 있습니다. 새 드라이버 로드 알림에서는 해당 베이스의 이전 기준을 무효화합니다.

`kernel_diagnostics`의 `valid=15`는 4개 export 기준을 읽었다는 비트마스크입니다. `changed=0`은 선택 범위에서 기준 이후 변화가 없다는 뜻이며 전체 시스템 무결성을 증명하지 않습니다. export를 못 찾은 환경에서는 `valid`가 작거나 `failed` 비트가 설정될 수 있습니다.

## 6. 이름·해시 정책

`config/policy.json`의 배열에 **실제로 확인한 샘플**의 이름 패턴/해시를 넣습니다. 이름은 대소문자를 구분하지 않고 경로 구분자가 없는 패턴은 파일명에 적용합니다. 이름이 바뀌면 이름 규칙은 우회할 수 있고, 파일 내용이 바뀌면 해시도 달라집니다. 해시 입력은 SHA-256 64자리 16진수입니다.

```powershell
Get-FileHash 'C:\samples\your_sample.sys' -Algorithm SHA256
```

서명 수집은 `watch` 뒤에 `--verify-signatures`를 추가합니다. 디스크 파일 분석은 최대 256 MiB이며 별도 작업 스레드에서 실행합니다. 수집된 파일은 메모리에 로드된 원본과 동일하다고 보장할 수 없습니다. 파일 변경 검사도 완전한 TOCTOU 방지는 아닙니다. 서명 결과의 숫자 `Status`는 PowerShell SignatureStatus이며, 0 Valid / 1 UnknownError / 2 NotSigned / 3 HashMismatch / 4 NotTrusted / 5 NotSupportedFileFormat / 6 Incompatible 순서입니다.

`smoke_names.json`은 메모장 이름 매칭 동작만 확인하는 별도 정책입니다. 메모장을 핵 목록으로 운영하라는 뜻이 아닙니다. 보호 대상을 게임으로 유지한 상태에서 이 정책으로 수집하고 새 메모장을 열면 공통 이름 이벤트가 나와야 합니다.

## 7. 공통 이벤트와 오프라인 재분석

```json
{"session_id":"normal_001","player_id":"player_local","module":"kernel_sentinel","timestamp_ms":1200,"evidence":{"type":"handle_post","actor_pid":4321,"target_pid":1234,"granted_access":40},"reasons":["sensitive_handle_rights_granted"],"raw_score":1}
```

이는 축약된 형식 예시이며 실행 결과가 아닙니다. `timestamp_ms`는 세션 시작 대비 밀리초, 원시 이벤트의 `unix_ms`/`timestamp_100ns`는 절대 시각입니다. 프로세스 이벤트의 `process_create_time`은 PID 재사용을 구분하는 Windows 생성 시각입니다. `operation_id`로 핸들 pre/post를 연결하고 `generation`으로 보호 세션 변경을 구분합니다.

동일 유형의 공통 이벤트는 5초간 중복 억제합니다. 원시 로그는 억제하지 않습니다. 점수 0인 `coverage_gap`/조회 불일치는 수집 품질·분석용 정보입니다. 실패·미수집을 악성으로 가산하지 않습니다. 기본 점수는 아직 정상/치트 실험으로 보정하지 않은 연구용 값입니다.

```powershell
python -m agent.main replay --raw .\runs\normal_001\raw_events.jsonl --config .\config\policy.json
python -m unittest discover -s tests -v
```

`replay`는 기록된 원시 이벤트에서 규칙별 횟수를 다시 계산합니다. 커널/메모리에 재접근하지 않습니다. 원시 기준이라 실시간 공통 로그의 중복 억제 횟수와 다를 수 있습니다.

## 8. 다음 검증 순서

자세한 표는 [VALIDATION.md](VALIDATION.md)를 봅니다.

1. 빌드 성공 → VM 로드 → 감시 모드로 정상 게임 10분 이상 수집.
2. 별도 창에서 `python .\tools\probe_handles.py --pid 1234` 실행. 메모리에 읽기/쓰기를 하지 않고 핸들만 요청합니다. `handle_post`의 실제 권한 확인.
3. 감시 세션 종료 후 새 제한 세션에서 같은 검사를 반복. `0x28` 권한이 제거되었는지 확인.
4. 코드 스캔 `flags=4` 기준 생성 뒤 `flags=1` 비교 결과가 나오는지 충분한 시간 동안 확인.
5. 제어된 VM의 정상 로그와 기존 팀 샘플 실험 로그를 별도 세션으로 비교. 정상/치트 시작 시각을 따로 기록.

이 절차의 1~4는 기능 동작 검증입니다. **실제 악성 커널 변조·후킹·은닉 드라이버 탐지 성공은 아직 검증하지 않았습니다.** 그 검증은 허가된 기존 샘플/메모리 덤프와 별도 디버거 관찰로 정답을 확보한 뒤 수행해야 합니다. 이 패키지에는 OS 후킹·은닉을 일으키는 치트 드라이버를 넣지 않았습니다.

## 9. 오류별 확인

- `WindowsKernelModeDriver10.0` 없음: WDK Visual Studio 연동 문제. `.vcxproj`의 도구 집합을 일반 C++로 바꾸지 말고 이전 빌드가 되던 WDK 설치/확장 상태 확인.
- `ntddk.h` 없음: SDK/WDK 버전 짝을 확인. 여러 버전의 `km`/`ucrt` 경로를 수동 혼합하지 않음.
- AuxKlib/Wdmlib/BCrypt 외부 기호: `Aux_Klib.lib`, `Wdmsec.lib`, **커널용 `Cng.lib`** 확인. 사용자 모드 `bcrypt.lib`로 대체하지 않음.
- Inf2Cat SYS 누락: `FilesToPackage`와 실제 빌드 성공 여부 확인. 컴파일 실패 상태의 후속 Inf2Cat 에러만 고치지 않음.
- 미래 DriverVer: 빌드 PC/VM 날짜·시간과 UTC/로컬 차이, StampInf 생성된 INF 확인. 프로젝트에 Inf2Cat 로컬 시각 설정 포함.
- 서명 오류/577: 새 SYS의 서명 및 VM 테스트 인증서 신뢰 상태 확인. 기존 KernelWatcher 인증서와 새 빌드 인증서가 같다고 가정하지 않음.
- 콜백 등록 실패/altitude 충돌: 연구용 altitude `386410.7811`을 사용. 생산 배포에는 적절한 고도 할당과 정식 서명/호환성 검증 필요. 드라이버가 보호 등록에 실패하면 로드 자체를 실패시킴.
- 장치 열기 실패: 관리자 권한, `sc.exe query KernelSentinel`, 이미 실행 중인 수집기 확인. 장치는 한 수집기만 열도록 설정.
- PSAPI 주소가 모두 0: 권한/OS 제약 가능성. `SeDebugPrivilege` 활성화 여부 기록. 은닉 탐지로 판단하지 않음.
- 공통 이벤트 파일이 비어 있음: 원시 로그와 상태를 먼저 확인. 기본 정책이 비어 있고 이상 신호가 없으면 정상적으로 비어 있을 수 있음.
- 재실행 시 폴더 존재 오류: 로그 덮어쓰기 방지 동작. `normal_002`처럼 새 `--out` 경로 사용.

## 10. 소스 위치와 참고 문서

- `driver/KernelSentinel.c`: 장치, 정책, heartbeat, OS 콜백, 이벤트 큐.
- `driver/integrity.h`: export/자체 포인터/커널 모듈 목록 진단.
- `driver/code_scan.h`: 커널 모듈 실행 섹션 SHA-256 순회 검사.
- `driver/access_policy.h`: 휴대 가능한 권한 제한 정책.
- `agent/`: Win32 통신, 로그, 규칙, 파일 분석, 재분석.
- `tests/`: 오프라인 검증. WDK 컴파일이나 악성 샘플 검증의 대체가 아님.

Microsoft 문서:

- [ObRegisterCallbacks](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-obregistercallbacks)
- [수정 가능한 핸들 접근 권한](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/ns-wdm-_ob_pre_create_handle_information)
- [PsSetLoadImageNotifyRoutine](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/nf-ntddk-pssetloadimagenotifyroutine)
- [AuxKlibQueryModuleInformation](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/aux_klib/nf-aux_klib-auxklibquerymoduleinformation)
- [MmCopyMemory와 일관성/수명 제한](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/nf-ntddk-mmcopymemory)
- [BCryptOpenAlgorithmProvider 커널 사용](https://learn.microsoft.com/en-us/windows/win32/api/bcrypt/nf-bcrypt-bcryptopenalgorithmprovider)
- [EnumDeviceDrivers 권한 조건](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-enumdevicedrivers)


## 2026-09-27 빌드 호환성 수정
`driver/thread_scan.h`에 `THREAD_QUERY_INFORMATION`이 정의되지 않은 경우에만
문서화된 값 `0x0040UL`을 정의하도록 추가했다. 이전의 드라이버 바이트 동일 설명은
이 수정 전 상태를 가리킨다. 테스트 대역의 매크로 정의를 제거해 누락 조건으로
42개 오프라인 테스트를 다시 통과했다. Windows WDK 빌드는 여전히 미실시다.
