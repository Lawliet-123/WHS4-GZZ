# MECCHA CHAMELEON GodMode Anti-Cheat

MECCHA CHAMELEON 4.0.2용 GodMode 탐지 모듈입니다.

UE4SS Lua가 플레이어 상태를 수집하고 Python Detector가 비정상 생존, 체력 복구, Invincible 상태 등을 분석합니다.

탐지 결과는 기존 로컬 JSONL에 저장하며, 팀 공통 `shared` 모듈을 통해 중앙 서버 전송 큐에도 전달합니다.

---

## 탐지 항목

- Invincible 비정상 지속
- Invincible 상태에서 피격 후 생존
- Kill 이후 Death 상태 미진입
- Kill 이후 플레이어 생존
- Heal 이벤트 없이 Health 증가
- Health / ChangeBeforeHealth 강제 MaxHealth 복구
- Respawn 없이 Dead -> Alive 전환
- Health > MaxHealth

---

## 판정 기준

- 0 ~ 4: `NORMAL`
- 5 ~ 9: `SUSPICIOUS`
- 10 이상: `DETECTED`

점수와 판정 로직은 기존 GodMode Detector 기준을 그대로 사용합니다.

Shared 연동은 점수나 탐지 기준을 수정하지 않고, 기존 탐지 결과를 중앙 서버로 전달하는 역할만 수행합니다.

---

## 프로젝트 구조

```text
client/detectors/godmode/
├─ main.py
├─ core/
│  └─ models.py
├─ detector/
│  └─ godmode_detector.py
├─ sensors/
│  ├─ game_state_sensor.py
│  └─ meccha_telemetry_sensor.py
├─ telemetry_mod/
│  └─ GodModeTelemetry/
│     └─ Scripts/
│        └─ main.lua
└─ tests/
   ├─ simulate_live_telemetry.py
   ├─ test_godmode_detector.py
   ├─ test_godmode_event_result.py
   └─ test_telemetry_pipeline.py
```

---

## 동작 흐름

```text
MECCHA CHAMELEON
        ↓
UE4SS GodModeTelemetry
        ↓
Telemetry JSONL
        ↓
MecchaTelemetrySensor
        ↓
GodModeDetector
        ↓
공통 7필드 Event 생성
        ↓
├─ replay_exports/.../events.jsonl
└─ shared.send_detection()
        ↓
Shared Outbox
        ↓
중앙 Receiver
```

기존 로컬 기록 기능은 그대로 유지되며 Shared는 추가 전송 경로로 동작합니다.

---

## 공통 Event 형식

중앙 서버로 전달하는 데이터는 팀 공통 7필드 형식을 유지합니다.

```json
{
  "session_id": "godmode_001",
  "player_id": "player_001",
  "module": "godmode",
  "timestamp_ms": 2700,
  "evidence": {
    "health": 100.0,
    "max_health": 100.0,
    "dead": false,
    "invincible": true,
    "change_before_health": 100.0,
    "kill_event": false,
    "death_event": false,
    "heal_event": false,
    "respawn_event": false
  },
  "reasons": [
    "Death state was not reached after kill event",
    "Player survived expected death event"
  ],
  "raw_score": 9
}
```

기존 Event의 다음 7개 필드는 변경하지 않습니다.

```text
session_id
player_id
module
timestamp_ms
evidence
reasons
raw_score
```

`raw_score`와 `reasons`에는 해당 Snapshot에서 새로 발생한 탐지 결과가 기록됩니다.

누적 점수와 전체 사유는 Detector 내부의 `score`, `reasons`에서 별도로 관리합니다.

---

## Telemetry 파일 경로

Lua와 Python Sensor는 기본적으로 다음 파일을 사용합니다.

```text
%LOCALAPPDATA%\MECCHA-GZZ-godmode-telemetry.jsonl
```

Windows 사용자명이나 프로젝트 설치 위치가 달라도 동일한 방식으로 경로를 결정합니다.

직접 경로를 지정하려면 다음 환경변수를 사용할 수 있습니다.

```powershell
$env:GZZ_GODMODE_TELEMETRY_PATH = "C:\원하는경로\meccha_telemetry.jsonl"
```

---

## 실제 게임 사용법

### 1. UE4SS Mod 설치

다음 폴더를 UE4SS의 `Mods` 폴더에 복사합니다.

```text
telemetry_mod\GodModeTelemetry
```

최종 구조 예시:

```text
Mods\
└─ GodModeTelemetry\
   └─ Scripts\
      └─ main.lua
```

게임 실행 후 GodModeTelemetry가 활성화되면 플레이어 상태가 JSONL에 기록됩니다.

주요 수집 값:

- Health
- MaxHealth
- Dead
- Invincible
- ChangeBeforeHealth
- Kill Event
- Death Event
- Heal Event
- Respawn Event

---

## Shared 설정

GodMode Detector는 프로그램 시작 시 Shared Client를 한 번 설정합니다.

필수 환경변수:

```powershell
$env:GZZ_TELEMETRY_URL = "https://중앙서버주소"
$env:GZZ_TELEMETRY_TOKEN = "발급받은토큰"
```

필요한 경우 detector 전용 Outbox 경로를 지정할 수 있습니다.

```powershell
$env:GZZ_TELEMETRY_OUTBOX = "C:\원하는경로\godmode-outbox.sqlite3"
```

여러 detector 프로세스를 동시에 실행하는 경우 동일한 Outbox를 공유하지 않아야 합니다.

Launcher에서 모듈별 `GZZ_TELEMETRY_OUTBOX`를 지정하는 경우 해당 설정을 그대로 사용합니다.

---

## GodMode Detector 실행

PowerShell에서 repository 기준 GodMode detector 폴더로 이동합니다.

```powershell
cd "client\detectors\godmode"
```

Python 모듈 경로를 설정합니다.

```powershell
$env:PYTHONPATH = (Get-Location).Path
```

실행:

```powershell
python ".\main.py" <session_id> <player_id>
```

예:

```powershell
python ".\main.py" godmode_001 player_001
```

정상 실행 시 다음과 같은 로그가 출력됩니다.

```text
[Telemetry] shared client configured

[INFO] Waiting for MECCHA telemetry...
[INFO] Telemetry connected.
[INFO] GodMode detection started.
```

---

## Shared 전송

GodMode 탐지가 발생하면 기존 공통 Event를 로컬 JSONL에 저장한 뒤 같은 Event를 `send_detection()`으로 전달합니다.

예:

```text
[Telemetry] queued event_id=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx status=queued
```

`queued`는 중앙 서버 저장 완료를 의미하지 않습니다.

이는 Event가 Shared의 로컬 Outbox에 정상 등록되었다는 의미입니다.

실제 서버 저장 성공 여부는 Receiver가 응답한 뒤 Shared sender가 확인합니다.

---

## 전송 실패 처리

Shared 설정이나 중앙 서버 전송에 문제가 발생하더라도 기존 GodMode 탐지는 계속 동작합니다.

예:

```text
[Telemetry] send failed: ...
```

Shared 전송 실패를 GodMode 점수로 처리하지 않으며 Detector 자체를 종료하지 않습니다.

기존 로컬 JSONL 및 Replay Export는 계속 저장됩니다.

---

## 종료 처리

프로그램 종료 시 다음 순서로 Shared 전송 큐를 정리합니다.

```text
flush_client()
shutdown_client()
```

예:

```text
[Telemetry] flush delivered=False
[Telemetry] shutdown stopped=True
```

`flush delivered=False`는 아직 전송되지 않았거나 실패 상태인 Event가 남아 있다는 의미입니다.

`shutdown stopped=True`는 Shared sender가 정상 종료되었다는 의미입니다.

미전송 Event는 Outbox에 보관되며 임의로 삭제하지 않습니다.

---

## Native GodMode 로그

필요한 경우 Native GodMode 로그 경로를 직접 지정할 수 있습니다.

```powershell
$env:GZZ_GODMODE_NATIVE_LOG_PATH = "C:\경로\GodModeHost402.log"
```

Native 로그가 없어도 기본 Telemetry 기반 GodMode 탐지는 계속 동작합니다.

---

## 게임 없이 Detector 테스트

```powershell
cd "client\detectors\godmode"
```

```powershell
$env:PYTHONPATH = (Get-Location).Path
```

다음 테스트를 실행합니다.

```powershell
python ".\tests\test_godmode_detector.py"
python ".\tests\test_godmode_event_result.py"
python ".\tests\test_telemetry_pipeline.py"
```

현재 확인된 결과:

```text
Normal Kill / Death        -> NORMAL / 0
GodMode Kill Survival      -> DETECTED / 11
Normal Heal                -> NORMAL / 0
Normal Respawn             -> NORMAL / 0
Forced Health Restore      -> SUSPICIOUS / 7
Telemetry Pipeline Normal  -> NORMAL / 0
Telemetry Pipeline GodMode -> DETECTED / 11
```

---

## Offline 실시간 Pipeline 테스트

첫 번째 PowerShell:

```powershell
cd "client\detectors\godmode"
```

```powershell
$env:PYTHONPATH = (Get-Location).Path
```

```powershell
python ".\main.py" godmode_shared_test_001 player_001
```

두 번째 PowerShell:

```powershell
cd "client\detectors\godmode"
```

```powershell
$env:PYTHONPATH = (Get-Location).Path
```

```powershell
python ".\tests\simulate_live_telemetry.py"
```

확인된 탐지 흐름:

```text
NORMAL 0
→ SUSPICIOUS 5
→ DETECTED 14
→ DETECTED 16
```

Shared 연동 테스트에서는 다음 3개의 Event가 Outbox에 정상 등록되는 것을 확인했습니다.

```text
raw_score = 5
raw_score = 9
raw_score = 2
```

각 Event에서 다음 로그가 정상 출력되었습니다.

```text
[Telemetry] queued event_id=... status=queued
```

---

## 현재 검증 범위

현재까지 확인된 항목:

- GodMode 기존 Detector 회귀 테스트 통과
- 기존 점수 및 판정 결과 유지
- 기존 공통 7필드 Event 유지
- 기존 로컬 JSONL 저장 유지
- Shared Client 초기화 확인
- `send_detection()` Outbox 등록 확인
- Shared 전송 실패 시 Detector 계속 실행 확인
- `flush_client()` / `shutdown_client()` 종료 처리 확인

실제 중앙 Receiver의 `/api/detection` 저장까지 포함한 E2E 테스트는 중앙 서버 환경이 완성된 뒤 별도로 확인해야 합니다.

---

## 대상 버전

```text
MECCHA CHAMELEON 4.0.2
```