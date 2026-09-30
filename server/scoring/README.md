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
