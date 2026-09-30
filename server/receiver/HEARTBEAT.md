# Heartbeat receiver (A → C handoff)

`POST /api/heartbeat` accepts the agreed `meccha-heartbeat-3` status message.
This is a separate endpoint from `/api/detection`. It does not call
`shared.logger.write_detection()` or scoring: Shared's seven-field Event
contract cannot represent the heartbeat message.

## Verified v3 schema

`server/receiver/heartbeat.schema.json` was compared with the sender's v3 schema
merged in [PR #56](https://github.com/Lawliet-123/WHS4-GZZ/pull/56), main commit
`eee51e9`. Required fields, types, status enums, identifier lengths, component
limits and nested constraints match. The receiver additionally enforces the
server limits below. It requires the 11 fields in the v3 example and rejects
HWID and unknown fields.

Server implementation limits: body <= 256 KiB (configurable), JSON depth <= 12,
<= 10,000 values, and sequence/timestamp_ms <= 2^63-1. Duplicate JSON keys,
invalid UTF-8, non-finite numbers, and boolean/float integer fields are rejected.
`sent_at_utc` must be a valid timezone-aware ISO/RFC3339-style date-time; it is
preserved, but it does not determine server liveness.

## Wire contract

The client sets `Content-Type: application/json` and
`Authorization: Bearer <heartbeat-token>`. C configures the matching token;
the client uses `MECCHA_HEARTBEAT_TOKEN`. Unlike detection requests, no
`Idempotency-Key` or `X-GZZ-Protocol-Version` header is required.
Use HTTPS for the deployed endpoint; HTTP loopback is for local tests.

Successful storage and duplicate requests both return HTTP 200 with exactly:

```json
{"accepted":true,"session_id":"yara_test_001","client_id":"8e6c1d9a0f3246ac9b754b738ce6ad92","sequence":2}
```

All three identifiers match the request. ACK is emitted only after the database
transaction completes. No extra `duplicate` field is added to the wire ACK.

| Request relative to the last accepted `(session_id, client_id)` | Result |
| --- | --- |
| First request, or greater sequence (gaps allowed) | Store and return 200; update server acceptance time |
| Same last sequence, same JSON content | Return same 200 ACK; no storage or liveness update |
| Same last sequence, different JSON content | 409 |
| Lower sequence, even if stored in history | 409 |

JSON key order/whitespace do not change content identity. The entire body is
compared, including `transport` and `sent_at_utc`. A retry must preserve its
body and sequence. The current client makes a new snapshot and sequence after
a failure; skipped sequences are therefore expected. A process resetting its
sequence to 1 must use a new `client_id`, as agreed with the sender.

Other errors: 401 credentials, 415 content type, 413 size, 422 JSON/schema,
503 unavailable storage. Heartbeat absence represents a monitoring gap;
this receiver does not issue cheat verdicts.

## Storage and C integration

`HeartbeatStore` stores accepted history and server acceptance timestamps in
SQLite, separately from detection JSONL. Its primary key is
`(session_id, client_id, sequence)`. Sequence comparison and insert share a
transaction, protecting against simultaneous requests. Duplicate/conflicting
requests do not change the stored latest state. Data survives server restart.

C adds the following alongside the existing detection router in
`server/main.py` (that entrypoint is not created by A):

```python
import os
from fastapi import FastAPI
from server.receiver import create_heartbeat_router
from server.receiver.heartbeat_store import HeartbeatStore
from server.receiver.router import bearer_token_verifier

app = FastAPI()  # Use the existing app when integrating.
heartbeat_store = HeartbeatStore(os.environ["MECCHA_HEARTBEAT_DB"])
app.include_router(create_heartbeat_router(
    heartbeat_store,
    verify_token=bearer_token_verifier(os.environ["MECCHA_HEARTBEAT_TOKEN"]),
))
```

Example database path: `server/logs/heartbeats/heartbeat.sqlite3`.
Set `MECCHA_HEARTBEAT_DB` to a path on **persistent local storage**. This
SQLite implementation assumes a single server host with a shared local file.
Separate replicas with independent or ephemeral files cannot share sequence
state; C must adapt storage for that deployment if needed. Configure the token
through the server environment; do not commit it.

`heartbeat_store.latest(session_id, client_id)` returns `sequence`,
`received_at_utc` (server acceptance time), and `payload`, or `None`.
C/Dashboard can use it for a status-query API. Automatic timeout alerts,
the query HTTP API, launcher aggregation, and cloud deployment are not
implemented by this receiver.

## Verification

From repository root, using the server Python environment:

```powershell
python -m pip install -r server/requirements-dev.txt
python -m unittest discover -s server/receiver/tests -v
```

Tests cover the supplied v3 example, exact ACK, gaps, duplicate liveness,
conflicts, restart persistence, simultaneous requests, schema/auth/size
rejection, storage failure/rollback, and separation from detection scoring.
The actual v3 HeartbeatClient from main `eee51e9` was also checked against this
receiver over real loopback HTTP: successful ACK, duplicate liveness, a failed
sequence followed by a successful higher sequence, 409 conflicts, persistence,
and new-client restart all passed. C's server wiring, launcher configuration,
and deployed-server verification remain pending.

Local verification: Python 3.13, 26 heartbeat tests plus 4 existing detection
receiver tests passed (30 total).
