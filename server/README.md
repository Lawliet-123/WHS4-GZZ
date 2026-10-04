# MECCHA CHAMELEON Central Server

## C Server Integration
- FastAPI central server
- Shared Writer initialization
- Scoring initialization and recovery
- Detection Receiver integration
- Heartbeat Receiver integration
- Dashboard query APIs

## API Endpoints
- GET /health
- POST /api/detection
- POST /api/heartbeat
- GET /api/dashboard/heartbeat/{session_id}/{client_id}
- GET /api/dashboard/verdict/{session_id}/{player_id}

## Environment Variables
- MECCHA_HEARTBEAT_DB: Heartbeat database path
- MECCHA_HEARTBEAT_TOKEN: Heartbeat authentication
- GZZ_TELEMETRY_TOKEN: Detection authentication
- GZZ_DASHBOARD_TOKEN: Dashboard authentication
- GZZ_SCORING_DB: Optional scoring database path
- GZZ_TELEMETRY_LOG_ROOT: Optional Shared storage path

## Startup
1. Initialize Dashboard authentication
2. Initialize Shared Writer
3. Configure Scoring
4. Recover stored events
5. Start accepting HTTP requests

## Verification
- Local Receiver to Shared to Scoring test passed
- Dashboard Heartbeat and Final Verdict queries passed
- Dashboard authentication and missing-data tests passed
- Data remained available after server restart

Production deployment and live-game E2E testing remain pending.

## Scoring Integration Notes

- Dashboard verdict returns HTTP 404 when no scoring snapshot exists for the requested session/player.
- Time-based overlap correlation is not enabled because the team has not agreed on a default time window.
- max_time_distance_ms is intentionally omitted.
- Once the team agrees on a time window, C will expose it as a configurable setting.
