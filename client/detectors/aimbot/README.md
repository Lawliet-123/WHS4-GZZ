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

## Shared telemetry status

`main.py` keeps printing the detector's existing seven-field result and also
passes that result to `shared.logger.send_detection()`. The shared client is
configured once at startup from `GZZ_TELEMETRY_URL` and
`GZZ_TELEMETRY_TOKEN`; shutdown calls `flush_client()` and
`shutdown_client()`. The UE4SS raw telemetry JSONL and detector scoring are
not modified by this forwarding step.

This integration is **not yet confirmed to reach the central server**:

- **Player/session IDs (9/30, wired through the launcher).** The detector
  itself still uses the UE actor path from `GetFullName()` as `player_id`.
  That value is ~180 chars with spaces, `/` and `:`, so the shared schema
  rejects it locally, and it also changes every round because the Hunter actor
  is respawned. The launcher now passes `--session-id` and `--player-id`;
  `main.py`'s `to_launcher_ids()` puts those at the top level of the outbound
  result and keeps the originals as `evidence.source_attacker_id` and
  `evidence.source_session_id`. Detection logic, scores and reasons are
  unchanged.
- **Attribution assumption (unverified).** Every scored signal needs shot
  records (signal 12's +3 needs only three shots, no aim trace and no
  confirmed find). Shots come from `SpawnShotEffect(Local)`, and aimed
  candidates / LOS are computed from this PC's local controller, so a result
  is treated as this PC's. But that hook does not check that the shooter is the
  local pawn. If the `SpawnShotEffect(Client)` multicast also calls `(Local)`
  on other PCs, a remote Hunter's shots would be scored with this PC's camera
  and attributed to this PC. The current recordings (two players, one Hunter
  per round) cannot tell. A multi-Hunter round measurement, or recording an
  `is_local` flag in the Lua collector, is needed. The original actor path is
  kept in `evidence.source_attacker_id` so such cases can be separated later.
- `KillPlayer` is a server RPC (SDK dump), so on the host it may also fire for
  remote Hunters and on guests it may not fire at all. Outcome-only windows
  score 0, but their evidence would carry the host PC's `player_id`.
- **`--from-end` (launcher).** The telemetry log is only truncated when the mod
  loads, so a previous game's records can still be in it. Reading from the start
  would send them under the current launcher session and this PC's id, and a
  restart would send them again with new event ids. With `--from-end` the
  detector skips what is already in the file and keeps its first line, so a mod
  reload that truncates the file is still detected and read from the start.
- Run directly without those options and the old behavior remains (reads from
  the start; UE values at the top level, so shared rejects them locally with a
  warning). Existing `ReplayAnalyzer/replay-data/aimbot` events were not
  rewritten.
- The launcher gives each module its own shared outbox
  (`client/Launcher/logs/outbox/<module>/`). Otherwise aimbot and
  external_access would share `telemetry-outbox/` under the repo root, and
  shared allows only one sender per outbox.
- Known issue, not changed here: when `GetRoundId()` is nil the Lua writes the
  string `"nil"`, which the Python side treats as a real round, so the
  "don't score an unconfirmed round" gate does not apply.
- A `queued` receipt only means the shared client accepted the event into its
  local outbox; it is not proof that the receiver stored it. The `/api/detection`
  end-to-end delivery and durable-storage response still need testing after the
  server endpoint is integrated and credentials are configured.

For the shared-client API and environment settings, see
[`shared/README.md`](../../../shared/README.md).
