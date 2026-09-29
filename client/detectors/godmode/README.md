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