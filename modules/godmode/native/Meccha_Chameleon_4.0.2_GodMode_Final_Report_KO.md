# MECCHA CHAMELEON 4.0.2 God Mode 구현 보고서

## 1. 프로젝트 개요

본 프로젝트는 팀 내부 비공개 환경에서 게임 핵과 안티치트의 동작 원리를 학습하고 발표하기 위한 보안 실습이다. 본 보고서에서는 안티치트 구현에 앞서, Survivor가 Hunter의 총격을 받아도 사망하지 않도록 만드는 God Mode의 구현 과정을 다룬다. 공개 매치나 제3자의 방에서는 사용하지 않으며, 실험자가 직접 만든 비공개 방의 호스트 환경만 대상으로 한다.

대상 게임 버전은 MECCHA CHAMELEON 4.0.2이며, 분석 자료로 `5.6.1-0+UE5-Chameleon_main.zip` SDK 덤프를 사용하였다.

## 2. 구현 목표

초기 목표는 다음과 같다.

1. Survivor가 Hunter의 총알에 맞아도 사망하지 않는다.
2. `Dead` 값이 `false`로 유지된다.
3. `Health`가 최대 체력으로 유지된다.
4. 서버의 사망 확정 함수가 실행되기 전에 차단한다.
5. 기능은 사용자가 명시적으로 활성화한 비공개 호스트 환경에서만 동작한다.

## 3. 초기 Lua 구현

UE4SS Lua 모드에서 로컬 Survivor Pawn을 찾은 다음 다음 값을 반복적으로 설정하였다.

- `Invincible = true`
- `Dead = false`
- `Health = MaxHealth`

사용자 명령은 `godmode on`, `godmode off`, `godmode status`이며 F6 토글과 F7 상태 확인도 지원하도록 구성하였다. 접속 직후 로그에서 `Invincible=true`, `Dead=false`, `Health=100`, `MaxHealth=100`이 출력되는 것으로 로컬 메모리 쓰기는 확인하였다.

## 4. Lua 방식이 실제 총격에서 실패한 이유

멀티플레이 사망 판정은 게스트의 로컬 체력만으로 결정되지 않는다. Hunter의 명중 요청은 호스트 서버로 전달되고, 서버가 `KillPlayer`를 실행하여 사망을 확정한다. 따라서 게스트 또는 로컬 Lua에서 체력을 복구하더라도 서버의 즉사 판정이 먼저 처리되면 관전자 상태로 전환된다.

또한 UE4SS Lua의 Blueprint 함수 hook은 해당 Blueprint 함수에서 실행 전 취소가 보장되지 않았다. 이 때문에 Lua의 사후 체력 복구 루프만으로는 `KillPlayer` 실행을 안정적으로 막을 수 없었다.

## 5. 4.0.2 SDK 덤프 분석

4.0.2 덤프에서 다음 네트워크 함수를 확인하였다.

- `Hunter.AntiChatTrace(Start, End, Target)`: `NetServer`, `Reliable`
- `Hunter.KillPlayer(Target, SourcePlayerState)`: `NetServer`, `Reliable`
- `GameMode.KillPlayer(Target, SourcePlayerState)`: 서버 GameMode의 사망 처리
- `Hunter.HitSuccess(Target)`: 명중 결과의 클라이언트 알림

주요 필드 오프셋은 다음과 같다.

| 필드 | 오프셋 |
|---|---:|
| `Dead` | `0x05AA` |
| `Invincible` | `0x05AB` |
| `Health` | `0x0638` |
| `MaxHealth` | `0x0640` |

주요 엔진 오프셋은 다음과 같다.

| 항목 | 오프셋 |
|---|---:|
| `GObjects` | `0x095DC5A0` |
| `GNames` | `0x0977D900` |
| `AppendString` | `0x01392470` |
| `ProcessEvent` | `0x015AFFE0` |
| `ProcessEvent` 가상 함수 인덱스 | `0x4C` |

## 6. 네이티브 God Mode 설계

서버의 사망 처리 전에 개입하기 위해 x64 C++ DLL을 제작하였다. DLL의 동작 순서는 다음과 같다.

1. DLL이 게임 프로세스에 로드된다.
2. 별도 작업 스레드가 4.0.2의 `GObjects`를 순회한다.
3. `AppendString`을 이용하여 Unreal 객체와 함수 이름을 해석한다.
4. Hunter와 GameMode 인스턴스의 가상 함수 테이블에서 `ProcessEvent` 항목을 hook한다.
5. 호출 함수가 `AntiChatTrace` 또는 `KillPlayer`인지 검사한다.
6. 대상 Survivor의 `Invincible` 값이 `true`이면 원래 함수를 호출하지 않고 반환한다.
7. 나머지 모든 함수는 원래 `ProcessEvent`로 전달한다.

DLL과 같은 폴더에 `GodModeHost402.on` 파일이 있을 때만 차단 기능이 켜지도록 안전 스위치를 추가하였다.

## 7. 버전별 문제와 수정 과정

### 7.1 초기 Lua 버전

로컬 필드 쓰기는 성공했지만 서버의 `KillPlayer`를 막지 못해 실제 총격에서 사망하였다.

### 7.2 네이티브 초기 버전

처음 빌드한 DLL은 WinLibs UCRT 및 `libmcfgthread-2.dll`에 의존하였다. 인젝터 프로세스에서는 실행됐지만 게임 프로세스가 의존 파일을 찾지 못해 `LoadLibraryW failed`, 오류 코드 6이 발생하였다.

해결 방법으로 MSVCRT 기반 x64 컴파일러를 사용하고 완전 정적 링크를 적용하였다. 최종 DLL의 외부 의존성은 Windows 기본 `KERNEL32.dll`과 `msvcrt.dll`만 남겼다.

### 7.3 v2: 작업 스레드 진단 추가

`DLL_PROCESS_ATTACH`, 작업 스레드 시작, 이름 초기화 상태를 로그에 기록하도록 변경하였다. 이 과정에서 DLL 로드는 성공했지만 이름 초기화 이후 로그가 나오지 않는 문제가 확인됐다.

### 7.4 v3: 이름 해석 방식 수정

덤프의 `GNames`는 단순 `FNamePool`이 아니라 간접 컨테이너였다. 직접 디코딩을 제거하고 덤프에서 제공한 `FName::AppendString` 엔진 함수를 사용하도록 변경하였다. 이름 결과는 캐시하여 반복 호출과 불필요한 메모리 사용을 줄였다.

### 7.5 v4: GObjects 청크 오프셋 수정

읽기 전용 진단 결과, 4.0.2의 `GObjects`에서 객체 청크 포인터는 `+0x00`에 있고 객체 개수 정보는 `+0x10`에 있었다. 이전 DLL은 `+0x10`을 청크 포인터로 해석하여 Hunter와 GameMode를 찾지 못했다. v4에서 청크 포인터 위치를 `+0x00`으로 수정하고, hook 설치 시 `ProcessEvent hook installed; total=N` 로그를 추가하였다.

## 8. 제작된 파일

- `godmode_host_402.cpp`: 네이티브 God Mode 전체 소스
- `GodModeHost402_v4.dll`: 4.0.2용 x64 네이티브 DLL
- `godmode-simple-injector.exe`: 프로세스 이름과 DLL 경로를 받는 x64 인젝터
- `RUN_GODMODE_402_FIXED.cmd`: 경로 확인 및 인젝터 실행 파일
- `GodModeHost402.on`: 활성화 안전 스위치
- `GodModeHost402.log`: 단계별 진단 및 차단 결과 로그
- `Mods/GodMode/Scripts/main.lua`: 로컬 Pawn의 God Mode 상태 설정

## 9. 처음부터 실행하는 방법

### 9.1 사전 준비

1. 게임과 UE4SS를 모두 종료한다.
2. 작업 관리자에서 `PenguinHotel-Win64-Shipping.exe`가 없는지 확인한다.
3. 게임 화면에서 대상 버전이 4.0.2인지 확인한다.
4. `GodModeHost402_v4.dll`, `godmode-simple-injector.exe`, `RUN_GODMODE_402_FIXED.cmd`, `GodModeHost402.on`이 같은 `native` 폴더에 있는지 확인한다.
5. 이전 `GodModeHost402.log`를 삭제하여 이번 시험 로그와 구분한다.

### 9.2 게임과 Lua God Mode 실행

1. 게임을 실행한다.
2. 실험자가 직접 비공개 방을 만든다.
3. 실험자는 호스트이면서 Survivor 역할을 선택한다.
4. 팀원 한 명은 Hunter 역할을 선택한다.
5. Survivor Pawn이 완전히 생성된 다음 UE4SS Console에서 `godmode on`을 입력한다.
6. `godmode status`를 입력하거나 접속 로그를 확인하여 `Invincible=true`, `Dead=false`, `Health=100`인지 확인한다.

### 9.3 네이티브 DLL 로드

1. `RUN_GODMODE_402_FIXED.cmd`를 한 번만 실행한다.
2. `[SUCCESS] GodModeHost402.dll was loaded.` 메시지를 확인한다.
3. 같은 게임 실행 중에는 인젝터를 다시 실행하지 않는다.
4. `GodModeHost402.log`에서 다음 로그를 확인한다.

```text
GodModeHost402 DLL_PROCESS_ATTACH
GodModeHost402 worker started
GodModeHost402 AppendString initialized
GodModeHost402 loaded; create GodModeHost402.on beside DLL to enable
GodMode ON
ProcessEvent hook installed; total=1
```

`ProcessEvent hook installed`가 없으면 실제 사망 차단이 준비되지 않은 상태이므로 총격 시험을 중단한다.

### 9.4 총격 시험

1. Hunter와 Survivor가 가까운 거리에서 정지한다.
2. Hunter가 Survivor에게 한 발만 발사한다.
3. Survivor가 사망, 관전, 라운드 제거 상태로 전환되는지 확인한다.
4. 로그에서 다음 줄이 생성되는지 확인한다.

```text
blocked server death call; total=1
```

5. 성공 시 추가 사격으로 `total` 값이 증가하는지 확인한다.
6. 실패하거나 게임이 종료되면 재주입하지 않고 GodMode 로그와 UE4SS 로그를 보존한다.

## 10. 성공 및 실패 판정 기준

성공 조건은 다음과 같다.

- Lua 로그에서 `Invincible=true`, `Dead=false`가 확인된다.
- 네이티브 로그에 `ProcessEvent hook installed`가 확인된다.
- 총격 후 `blocked server death call`이 기록된다.
- Survivor가 사망하거나 관전자로 전환되지 않는다.

실패 유형은 다음과 같이 구분한다.

| 현상 | 의미 |
|---|---|
| DLL 로그가 없음 | DLL이 로드되지 않음 |
| `worker started`까지만 기록 | 이름 초기화 실패 |
| `GodMode ON`은 있으나 hook 로그 없음 | 객체 탐색 또는 hook 설치 실패 |
| hook 로그는 있으나 차단 로그 없음 | 다른 사망 경로가 사용됨 |
| 차단 로그는 있으나 사망 | 추가 서버 사망 처리 경로 존재 |

## 11. 안전 및 제한 사항

본 구현은 4.0.2 오프셋에 고정되어 있으므로 다른 버전에서 실행하면 안 된다. 공개 매치 또는 타인의 호스트 환경에서는 사용하지 않는다. DLL은 한 게임 실행당 한 번만 로드하며, 충돌 발생 시 재주입하지 않는다.

## 12. 현재 산출물 검증 정보

`GodModeHost402_v4.dll`은 PEI x86-64 형식이며 Windows 기본 `KERNEL32.dll`, `msvcrt.dll`만 의존한다.

SHA-256:

```text
8D48B94CA79D63BC5F459A7166097E2486AF60245A7C28DF787C1AF6068F2809
```

## 13. 최종 시험 결과

v4를 사용한 비공개 호스트 시험에서 다음 단계를 확인하였다.

1. DLL 주입 성공
2. `DLL_PROCESS_ATTACH` 및 작업 스레드 시작 확인
3. `AppendString` 이름 해석 초기화 성공
4. `GodMode ON` 활성화 확인
5. `ProcessEvent` hook 설치 확인
6. Hunter의 총격에 대한 서버 사망 함수 차단 확인
7. 총격 후 Survivor 생존 확인

따라서 4.0.2 비공개 호스트 환경을 기준으로, Hunter의 총격을 받아도 Survivor가 사망하지 않는 God Mode의 최종 동작을 확인하였다.
