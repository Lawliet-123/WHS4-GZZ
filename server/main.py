import os
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Request
from shared.config import WriterConfig
from shared.errors import ValidationError
from shared.logger import configure_writer, write_detection
from shared.schema import validate_identifier

from server.receiver import create_router, create_heartbeat_router
from server.receiver.heartbeat_store import (
    HeartbeatStore,
    HeartbeatStorageError,
)
from server.receiver.router import bearer_token_verifier

from server.scoring.main import (
    configure_scoring,
    process,
    recover_from_writer,
    get_player_snapshot,
    get_player_final_verdict,
)
from server.scoring import main as scoring_queries
from server.dashboard_backend import DashboardService, create_dashboard_router


# -------------------------------------------------
# Writer/Receiver 공통 요청 크기 설정
# -------------------------------------------------

writer_config = WriterConfig.from_env()


# -------------------------------------------------
# 서버 시작 및 종료
# -------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dashboard 조회 API 전용 토큰 필수
    dashboard_token = os.environ.get("GZZ_DASHBOARD_TOKEN")
    if not dashboard_token:
        raise RuntimeError(
            "GZZ_DASHBOARD_TOKEN must be configured"
        )

    app.state.verify_dashboard = bearer_token_verifier(
        dashboard_token
    )

    # 1. Shared Writer 초기화
    writer = configure_writer(writer_config)

    # 2. Scoring 초기화
    configure_scoring()

    # 3. 미처리 이벤트 복구
    recovery_cursor = recover_from_writer(writer)

    app.state.dashboard = DashboardService(
        writer=writer,
        scoring=scoring_queries,
        heartbeat_store=heartbeat_store,
        verdict_provider=get_player_final_verdict,
        index_path=os.environ.get("GZZ_DASHBOARD_INDEX", "server/logs/dashboard/dashboard.sqlite3"),
        cursor_secret=os.environ.get("GZZ_DASHBOARD_CURSOR_SECRET", dashboard_token),
        stale_after_ms=int(os.environ.get("GZZ_DASHBOARD_STALE_AFTER_MS", "30000")),
    )

    print("[Server] Shared Writer configured")
    print("[Server] Scoring configured")
    print(
        "[Server] Scoring recovery complete: "
        f"cursor={recovery_cursor}"
    )

    # 복구 완료 후 요청 수신
    yield


app = FastAPI(lifespan=lifespan)

# Additional read APIs; C's existing heartbeat/verdict endpoints remain intact.
app.include_router(create_dashboard_router(
    lambda: app.state.dashboard,
    verify_token=lambda authorization: app.state.verify_dashboard(authorization),
))


# -------------------------------------------------
# 공통 상태 확인
# -------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
    }


# -------------------------------------------------
# Heartbeat
# Scoring과 별도로 처리
# -------------------------------------------------

heartbeat_store = HeartbeatStore(
    os.environ["MECCHA_HEARTBEAT_DB"]
)

app.include_router(
    create_heartbeat_router(
        heartbeat_store,
        verify_token=bearer_token_verifier(
            os.environ["MECCHA_HEARTBEAT_TOKEN"]
        ),
    )
)


# -------------------------------------------------
# Detection Receiver -> Shared Writer -> Scoring
# -------------------------------------------------

app.include_router(
    create_router(
        write_detection,
        submit_to_scoring=process,
        verify_token=bearer_token_verifier(
            os.environ["GZZ_TELEMETRY_TOKEN"]
        ),
        max_event_bytes=writer_config.max_event_bytes,
    )
)


# -------------------------------------------------
# Dashboard API 공통 인증 및 입력 검사
# -------------------------------------------------

def verify_dashboard_request(request: Request):
    verifier = request.app.state.verify_dashboard

    if not verifier(
        request.headers.get("authorization", "")
    ):
        raise HTTPException(
            status_code=401,
            detail="invalid dashboard credentials",
        )


def check_identifier(value: str) -> str:
    try:
        return validate_identifier(value)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


# -------------------------------------------------
# Dashboard: 최신 Heartbeat 조회
# -------------------------------------------------

@app.get(
    "/api/dashboard/heartbeat/{session_id}/{client_id}",
    tags=["dashboard"],
)
def dashboard_heartbeat(
    session_id: str,
    client_id: str,
    request: Request,
):
    verify_dashboard_request(request)

    session_id = check_identifier(session_id)
    client_id = check_identifier(client_id)

    try:
        latest = heartbeat_store.latest(
            session_id,
            client_id,
        )
    except HeartbeatStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail="heartbeat storage unavailable",
        ) from exc

    if latest is None:
        raise HTTPException(
            status_code=404,
            detail="heartbeat not found",
        )

    return {
        "session_id": session_id,
        "client_id": client_id,
        **latest,
    }


# -------------------------------------------------
# Dashboard: B Scoring 최종 판정 조회
# -------------------------------------------------

@app.get(
    "/api/dashboard/verdict/{session_id}/{player_id}",
    tags=["dashboard"],
)
def dashboard_verdict(
    session_id: str,
    player_id: str,
    request: Request,
):
    verify_dashboard_request(request)

    session_id = check_identifier(session_id)
    player_id = check_identifier(player_id)

    try:
        # 기록 자체가 없는 플레이어와 평가 가능한 기록이 있는
        # 플레이어의 NO_ACTIVE_EVIDENCE 판정을 구분한다.
        snapshot = get_player_snapshot(
            session_id,
            player_id,
        )
        if not snapshot:
            raise HTTPException(
                status_code=404,
                detail="scoring data not found",
            )

        # 합의된 시간 창이 없어 max_time_distance_ms는 지정하지 않는다.
        verdict = get_player_final_verdict(
            session_id,
            player_id,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="scoring unavailable",
        ) from exc

    return asdict(verdict)
