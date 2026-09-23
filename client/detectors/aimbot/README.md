# MECCHA aimbot anti-cheat telemetry

This module records local Hunter shot attempts and confirmed find outcomes
through UE4SS, then evaluates three raw aimbot signals in Python. It never
makes the final ban decision; a central TelemetryServer combines this module's
evidence with the other anti-cheat modules.

## Layout

- `main.lua`: UE4SS collector. Hunter 상태에서 `ControlRotation`과 게임 입력
  delta를 약 30Hz로 짧게 보관하고, 발사 시 직전 조준 궤적을 `shot_attempt`
  JSONL에 함께 기록한다.
- `sensors/`: converts JSONL records into typed telemetry values.
- `detector/`: calculates raw score, reasons, and evidence.
- `capture_test_session.py`: packages raw logs and detector snapshots for the
  ReplayAnalyzer test format.

## Current collection model

Every game client installs the collector. A Hunter's client records its own
shots through `SpawnShotEffect(Local)`, and records a confirmed find only when
`KillPlayer` fires. `IsHit` and `HitSuccess` are deliberately not used as
success signals: the former was true for an empty-space test shot, and the
latter does not prove that the hidden player was eliminated or converted. The
host is not expected to observe every player's combat event.

## Current signal status

- Signal 5, snap-to-lock: a confirmed outcome와 연결된 발사 직전 궤적에서,
  조준 오차가 큰 상태에서 목표 방향으로 급격히 붙은 뒤 저오차로 유지되는지를
  본다. 동일 현상이 두 번 이상 반복돼야 점수화한다.
- Signal 5-2, inputless rotation: 게임 입력 delta가 없는데 ControlRotation이
  목표 방향으로 크게 변하면 독립적인 강한 근거로 점수화한다. LocalGuard Raw
  Input 연동 전에는 게임 입력 delta만 사용하므로 실전 검증이 필요하다.
- Signal 7, distance-priority switching: 후보는 `LiveSurvivors_PlayerState`로
  제한해 evidence만 기록한다. 거리 우선 선택 자체는 에임봇 증거가 약하므로
  단독 raw_score에는 더하지 않는다.
- Signal 12, confirmed find without LOS: line trace output is logged; blocked-LOS
  validation is still required.
- Round scope: the `SetTimerNumber` candidate hook still needs live validation.
  Until it registers and provides a verified round boundary, signals 5 and 7
  intentionally remain unscored to prevent cross-round false positives.

## Test

Run from this folder:

```text
python -m unittest discover -q
```

Runtime telemetry, backups, and captured test sessions are intentionally
ignored by Git because they may be large or machine-specific.
