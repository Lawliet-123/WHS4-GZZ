"""POST /api/heartbeat: v3 status messages never enter detection scoring."""

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .heartbeat_schema import HeartbeatValidationError, decode_heartbeat
from .heartbeat_store import HeartbeatConflict, HeartbeatStorageError, HeartbeatStore
from .router import TokenVerifier, _read_limited_body


def create_heartbeat_router(
    store: HeartbeatStore, *, verify_token: TokenVerifier,
    max_heartbeat_bytes: int = 256 * 1024,
) -> APIRouter:
    """C injects persistent storage and a verifier for MECCHA_HEARTBEAT_TOKEN.

    Heartbeats do not use detection Idempotency-Key or X-GZZ-Protocol-Version
    headers. Their identity is session_id/client_id/sequence in the body.
    """
    if type(max_heartbeat_bytes) is not int or max_heartbeat_bytes <= 0:
        raise ValueError("max_heartbeat_bytes must be a positive integer")
    router = APIRouter(prefix="/api", tags=["heartbeats"])

    @router.post("/heartbeat")
    async def receive_heartbeat(request: Request) -> JSONResponse:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
        if not verify_token(request.headers.get("authorization", "")):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid heartbeat credentials")
        try:
            payload = decode_heartbeat(await _read_limited_body(request, max_heartbeat_bytes))
        except HeartbeatValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        try:
            receipt = await run_in_threadpool(store.accept, payload)
        except HeartbeatConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except (HeartbeatStorageError, OSError) as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "heartbeat storage is unavailable") from exc
        return JSONResponse(status_code=200, content=receipt.to_ack())

    return router
