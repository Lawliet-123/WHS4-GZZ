"""FastAPI router implementing the shared telemetry protocol v1."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from shared import PROTOCOL_VERSION
from shared.errors import (
    ConfigurationError,
    IdempotencyConflict,
    ResourceBusyError,
    StorageError,
    ValidationError,
)
from shared.schema import decode_event, validate_event_id
from shared.storage import WriteReceipt

DetectionWriter = Callable[..., WriteReceipt]
ScoringSink = Callable[[dict[str, Any]], Any]
TokenVerifier = Callable[[str], bool]


def bearer_token_verifier(expected_token: str) -> TokenVerifier:
    """Return a constant-time verifier for the server's configured token."""
    if not isinstance(expected_token, str) or not expected_token:
        raise ValueError("expected_token must be non-empty text")

    def verify(authorization: str) -> bool:
        prefix = "Bearer "
        if not authorization.startswith(prefix):
            return False
        return hmac.compare_digest(authorization[len(prefix):], expected_token)

    return verify


async def _read_limited_body(request: Request, max_event_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
            if declared_length < 0:
                raise ValueError("negative Content-Length")
            if declared_length > max_event_bytes:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "request body is too large")
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid Content-Length") from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_event_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "request body is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def create_router(
    write_detection: DetectionWriter,
    submit_to_scoring: ScoringSink | None = None,
    *,
    verify_token: TokenVerifier,
    max_event_bytes: int = 256 * 1024,
) -> APIRouter:
    """Create the ``/api/detection`` router.

    ``shared.logger.write_detection`` is injected by ``server/main.py``.
    The receiver validates the request independently of the client, stores it
    using its original Idempotency-Key, then passes the stored event to B's
    scoring adapter. It never makes the cheat verdict itself.
    """

    if not isinstance(max_event_bytes, int) or isinstance(max_event_bytes, bool) or max_event_bytes <= 0:
        raise ValueError("max_event_bytes must be a positive integer")

    router = APIRouter(prefix="/api", tags=["detections"])

    @router.post(
        "/detection",
        status_code=status.HTTP_200_OK,
    )
    async def receive_detection(request: Request) -> JSONResponse:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "application/json":
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")

        if request.headers.get("x-gzz-protocol-version") != PROTOCOL_VERSION:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported telemetry protocol version")

        if not verify_token(request.headers.get("authorization", "")):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid telemetry credentials")

        try:
            event_id = validate_event_id(request.headers.get("idempotency-key"))
            payload = decode_event(await _read_limited_body(request, max_event_bytes), max_bytes=max_event_bytes)
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

        try:
            receipt = await run_in_threadpool(write_detection, payload, event_id=event_id)
        except IdempotencyConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "event ID conflicts with an existing event") from exc
        except (StorageError, ResourceBusyError, ConfigurationError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="detection storage is unavailable",
            ) from exc

        if submit_to_scoring is not None:
            try:
                await run_in_threadpool(
                    submit_to_scoring,
                    payload,
                    event_id=receipt.event_id,
                    sequence=receipt.sequence,
                )
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="scoring is unavailable",
                ) from exc

        return JSONResponse(status_code=status.HTTP_200_OK, content=receipt.to_ack())

    return router
