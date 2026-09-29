# MECCHA CHAMELEON GodMode Anti-Cheat

MECCHA CHAMELEON 4.0.2용 GodMode 탐지 모듈입니다. UE4SS Lua가 플레이어 상태를 수집하고 Python Detector가 비정상 생존, 체력 복구, Invincible 상태를 판정합니다.

## 탐지 항목
- Invincible 비정상 지속
- Invincible 상태에서 피격 후 생존
- Kill 이후 Death 상태 미진입 및 생존
- Heal 없이 Health 증가
- Health / ChangeBeforeHealth 강제 MaxHealth 복구
- Respawn 없이 Dead -> Alive 전환
- Health > MaxHealth

## 판정 기준
- 0~4: NORMAL
- 5~9: SUSPICIOUS
- 10 이상: DETECTED

## Telemetry 경로
Lua와 Python은 기본적으로 같은 파일을 사용합니다.

`%LOCALAPPDATA%\MECCHA-GZZ-godmode-telemetry.jsonl`

직접 지정:
`$env:GZZ_GODMODE_TELEMETRY_PATH = "C:\원하는경로\meccha_telemetry.jsonl"`

## 실제 게임 사용법
1. `telemetry_mod\GodModeTelemetry`를 UE4SS의 `Mods` 폴더에 복사합니다.
2. UE4SS에서 GodModeTelemetry를 활성화하고 게임을 실행합니다.
3. PowerShell에서 실행합니다.

`cd "client\detectors\godmode"`

`$env:PYTHONPATH = (Get-Location).Path`

`python ".\main.py" <session_id> <player_id>`

예:
`python ".\main.py" godmode_001 player_001`

정상 연결:
- `[INFO] Telemetry connected.`
- `[INFO] GodMode detection started.`

## 게임 없이 테스트
`python ".\tests\test_godmode_detector.py"`

`python ".\tests\test_godmode_event_result.py"`

`python ".\tests\test_telemetry_pipeline.py"`

확인된 결과:
- Normal Kill / Death -> NORMAL / 0
- GodMode Kill Survival -> DETECTED / 11
- Normal Heal -> NORMAL / 0
- Normal Respawn -> NORMAL / 0
- Forced Health Restore -> SUSPICIOUS / 7
- Telemetry Pipeline GodMode -> DETECTED / 11

## 실시간 Offline Pipeline 테스트
첫 번째 PowerShell:
`python ".\main.py" godmode_offline_001 player_001`

두 번째 PowerShell:
`python ".\tests\simulate_live_telemetry.py"`

정상 흐름:
`NORMAL -> SUSPICIOUS -> DETECTED`

현재 테스트에서 `NORMAL 0 -> SUSPICIOUS 5 -> DETECTED 14 -> DETECTED 16`을 확인했습니다.

## Native 로그
필요 시:
`$env:GZZ_GODMODE_NATIVE_LOG_PATH = "C:\경로\GodModeHost402.log"`

Native 로그가 없어도 기본 telemetry 탐지는 동작합니다.

## 결과 필드
- `score`, `reasons`: 세션 누적 결과
- `new_score`, `new_reasons`: 현재 Snapshot 신규 결과
- 중앙 Event의 `raw_score`, `reasons`에는 신규 결과가 기록됩니다.

## 대상 버전
MECCHA CHAMELEON 4.0.2
