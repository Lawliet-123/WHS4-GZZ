# Central receiver

`server/receiver` is the first server-side step for every detector result.
It validates a request sent to `POST /api/detection`, stores the validated
JSON through the shared logger, then hands the same result to scoring.

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

`session_id`, `player_id`, and `module` must be non-empty text.
`timestamp_ms` and `raw_score` must be non-negative numbers. `evidence` must
be an object and `reasons` must be a list of non-empty text. Extra fields are
kept with the stored event so individual detectors can retain useful metadata.

Invalid input receives FastAPI's `422` response. A successful request receives
`202 Accepted`; this means the receiver validated and recorded the event. It is
not a cheat verdict.

## Integration point for B and C

The receiver does not import unfinished modules itself. C connects the agreed
functions in `server/main.py`:

```python
from fastapi import FastAPI

from server.receiver import create_router
from shared.logger import write_detection
from server.scoring.main import ingest_detection

app = FastAPI()
app.include_router(create_router(write_detection, ingest_detection))
```

- `shared.logger.write_detection(payload)` records validated JSONL under
  `server/logs/detections/` and may return the written path.
- `server.scoring.main.ingest_detection(payload)` receives that same validated
  result and performs B's player/session score handling.

This split lets A implement and test request validation before the shared
logger and scoring module are completed.
