# Central receiver

`server/receiver` is the first server-side step for every detector result. It
implements **shared telemetry protocol v1**: validates `POST /api/detection`,
stores the validated JSON through the shared logger, then hands the same event
and transmission ID to scoring.

## Accepted result format

Every request must contain these fields.

```json
{
  "session_id": "round_12",
  "player_id": "player_042",
  "module": "aimbot",
  "timestamp_ms": 507000,
  "evidence": {"hidden_target_shots": 3},
  "reasons": ["Repeated Precise Shots Toward Hidden Target"],
  "raw_score": 3
}
```

The body has **only these seven fields**. `window_id`, `sample_id`, `status`
and transmission metadata cannot be added at the root. `session_id`,
`player_id`, and `module` use the safe identifier rules in `shared/schema.py`.
`timestamp_ms` is an unchanged, non-negative integer elapsed from session
start; `raw_score` is an unchanged finite non-negative number. `reasons` may
be empty because a 0-point result is still a valid event.

The request also needs these headers:

```text
Authorization: Bearer <client-token>
Idempotency-Key: <canonical lowercase UUID>
X-GZZ-Protocol-Version: 1
```

On a durable write, the receiver returns HTTP `200` and one of these exact
response bodies:

```json
{"event_id":"same UUID sent by client","status":"stored"}
```

```json
{"event_id":"same UUID sent by client","status":"duplicate"}
```

It returns `422` for invalid Event data, `401` for a bad token, `409` when one
ID is reused with different content, and `503` when durable storage or scoring
is temporarily unavailable.

## Integration point for B and C

The receiver does not import unfinished modules itself. C connects the agreed
functions in `server/main.py`:

```python
import os

from fastapi import FastAPI

from server.receiver import create_router
from shared.config import WriterConfig
from shared.logger import configure_writer, write_detection
from server.receiver.router import bearer_token_verifier
from server.scoring.main import process

app = FastAPI()
configure_writer(WriterConfig.from_env())
app.include_router(create_router(
    write_detection,
    process,
    verify_token=bearer_token_verifier(os.environ["GZZ_TELEMETRY_TOKEN"]),
))
```

- `shared.logger.write_detection(payload, event_id=...)` records JSONL and the
  idempotency ledger under `server/logs/detections/`, then returns a receipt.
- `server.scoring.main.process(payload, event_id=..., sequence=...)` is B's
  adapter. B must use `event_id` to prevent a retry from adding score twice.

This split lets A implement and test request validation before the shared
logger and scoring module are completed.

## Separate heartbeat endpoint

`create_heartbeat_router()` implements `POST /api/heartbeat` using the verified
HWID-free v3 contract. Its persistent sequence/liveness store is separate from
detection JSONL and scoring. See [HEARTBEAT.md](HEARTBEAT.md) for the verified
schema, ACK and conflict rules, tests, and C's integration instructions.
