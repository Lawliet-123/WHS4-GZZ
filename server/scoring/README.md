# Server scoring (B)

`server/scoring` is the B-side adapter between the durable receiver log and the
future risk/decision policy.

The first implementation deliberately establishes delivery/state correctness
before choosing normalization weights or final cheat thresholds:

- `event_id` is the scoring idempotency key. A retry of the same stored event is
  a no-op; the same ID with different input fails closed.
- A new sample with the same `raw_score` is **not** added to the old score. The
  latest state is kept per `(session_id, player_id, module)`.
- Event time is `timestamp_ms`. Server `sequence` is only the durable receiver
  feed order. A late older event is recorded as processed but does not replace a
  newer module state.
- Live request processing does not advance the recovery cursor. Startup recovery
  uses `writer.iter_stored(...)` in order and advances the cursor only after each
  record has been processed successfully.

## Receiver adapter

A/C can inject B exactly as documented by the receiver:

```python
from server.scoring.main import process

app.include_router(create_router(
    write_detection,
    process,
    verify_token=...,
))
```

`process(payload, event_id=..., sequence=...)` lazily creates the default DB at
`server/logs/scoring/scoring.sqlite3`. Set `GZZ_SCORING_DB` or call
`configure_scoring(path)` before the first event to choose another path.

## Crash recovery

After the shared writer is configured, C can replay anything B may have missed:

```python
from server.scoring.main import recover_from_writer

recover_from_writer(writer)
```

This is safe to run repeatedly because `event_id` makes replay idempotent.

## Policy is intentionally separate

This commit does **not** invent cross-module weights or a final ban/verdict
threshold. Current detector scales differ substantially (for example Noclip,
Aimbot, AutoPaint and LocalGuard do not share one raw-score range), and the
repository does not yet define one agreed normalization formula. The next B step
should add a policy layer on top of `get_player_snapshot()` after those rules are
agreed/validated with ReplayAnalyzer data.

## Detector score audit / B2a preview

See `MODULE_INVENTORY.md` for source-code-audited module names, raw-score ranges,
transmission semantics, unreviewed ESP, and policy blockers. `policy.py` adds
`inspect_event()` and `get_player_signal_inventory()` as **inspection-only**
helpers. They deliberately do **not** assign cross-module weights or final
player risk. In particular `godmode` sends per-event deltas, and several other
modules send only positive results; B1's latest-state table cannot be treated
as a complete active-risk picture for those modules.

## B2b-1: Godmode 사건별 이력 보존 (최종 scoring 공식 아님)

최신 Event 한 개로 전체 사건을 복원할 수 없는 `godmode`는 별도
`event_delta_history`에 **모든 수신 이벤트를 event_id 기준으로 한 번씩**
보존한다. B1 `latest_state`도 기존과 동일하게 갱신하며, 원본 7필드 형식이나
Receiver의 `process(payload, event_id=..., sequence=...)` 연결을 바꾸지 않는다.

```python
from server.scoring.main import get_event_delta_history

# sequence는 서버 영구 저장 순서이며 timestamp_ms(게임 시간)와 다르다.
rows = get_event_delta_history("session1", "player1", module="godmode", limit=100)
for item in rows:
    print(item.event_id, item.timestamp_ms, item.raw_score, item.reasons)
```

**주의:** 여기서 raw_score는 Godmode가 보고한 새 사건의 증분이다.
현재 정책은 기록만 보존하며, 여러 이벤트를 단순 합산하거나 최종 위험도와
치트 판정을 계산하지 않는다. 특히 detector가 재시작돼 동일 reason을 새로운
`event_id`로 보낼 경우, 전송 중복은 구별할 수 있지만 실제 사건 중복 여부는
추가 고유 사건 식별자 없이 판단할 수 없다. 추후 detector 팀과 규칙 합의가 필요하다.

### 기존 B1 DB에서 업그레이드할 경우

DB 초기화 시 새 테이블을 자동 추가한다. 하지만 이미 과거 B1에서 처리 완료한
Godmode 사건은 `latest_state`에 마지막 1건만 남아 있으므로, **Shared 원본
로그를 백업·보존한 상태에서** 다음을 한 번 실행해야 사건 이력을 채울 수 있다.

```python
from server.scoring.main import backfill_event_delta_history_from_writer

backfill_event_delta_history_from_writer(writer)  # configure_scoring() 완료 후
```

백필은 Shared의 처음부터 읽고 동일 ID의 처리 이력을 검증하며 빠진 사건만
채운다. 일반 장애 복구용 `recover_from_writer(writer)`는 기존과 동일하게
별도로 사용한다. 실제 서버 시작 시 언제 실행할지는 A/C 담당자와 합의해야 한다.
Shared 원본이 없으면 과거 누락 사건을 만들어낼 수 없다.

새 사건 이력은 현재 단일 서버용 SQLite에 누적되며, 보관 기간·용량 제한·
정규화 가중치·LocalGuard 상관관계 제거·ESP 정책은 아직 미구현이다.
