"""Local integration harness. C owns the production server/main.py."""

import os
from pathlib import Path

from fastapi import FastAPI

from server.receiver import create_heartbeat_router, create_router
from server.receiver.heartbeat_store import HeartbeatStore
from server.receiver.router import bearer_token_verifier
from server.scoring import main as scoring_queries
from shared.config import WriterConfig
from shared.storage import DetectionWriter
from .router import create_dashboard_router
from .service import DashboardService


def recover(writer, scoring):
    cursor = scoring.get_recovery_cursor()
    while True:
        batch = writer.iter_stored(after_sequence=cursor, limit=500)
        if not batch:
            return
        for item in batch:
            scoring.process_event(item.result, event_id=item.event_id, sequence=item.sequence)
            scoring.advance_recovery_cursor(item.sequence)
            cursor = item.sequence


def create_app():
    """Use explicit environment tokens; no credentials are shipped in code."""
    detection_token = os.environ["GZZ_TELEMETRY_TOKEN"]
    heartbeat_token = os.environ["MECCHA_HEARTBEAT_TOKEN"]
    dashboard_token = os.environ["GZZ_DASHBOARD_TOKEN"]
    root = Path(os.environ.get("GZZ_DASHBOARD_DEMO_ROOT", "work/dashboard-demo"))
    writer = DetectionWriter(WriterConfig(root=root / "detections"))
    scoring = scoring_queries.configure_scoring(root / "scoring.sqlite3")
    heartbeat = HeartbeatStore(root / "heartbeat.sqlite3")
    recover(writer, scoring)
    service = DashboardService(writer=writer, scoring=scoring, index_path=root / "dashboard.sqlite3",
                               heartbeat_store=heartbeat, cursor_secret=dashboard_token,
                               verdict_provider=scoring_queries.get_player_final_verdict)
    app = FastAPI(title="8-B local integration harness")
    app.include_router(create_router(writer.write_detection, scoring.process_event,
                                    verify_token=bearer_token_verifier(detection_token)))
    app.include_router(create_heartbeat_router(heartbeat, verify_token=bearer_token_verifier(heartbeat_token)))
    app.include_router(create_dashboard_router(service, verify_token=bearer_token_verifier(dashboard_token)))
    return app
