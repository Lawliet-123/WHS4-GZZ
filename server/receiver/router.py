"""FastAPI router for receiving validated detector results."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from .models import DetectionResult

DetectionWriter = Callable[[dict[str, Any]], Path | str | None]
ScoringSink = Callable[[dict[str, Any]], Any]


class DetectionAccepted(BaseModel):
    accepted: bool = True
    session_id: str
    player_id: str
    module: str
    stored_at: str | None = None


def create_router(
    write_detection: DetectionWriter,
    submit_to_scoring: ScoringSink | None = None,
) -> APIRouter:
    """Create the ``/api/detection`` router.

    ``shared.logger.write_detection`` is injected by ``server/main.py``.
    The receiver owns validation and dispatch only; it does not decide whether
    the player used a cheat. ``submit_to_scoring`` is supplied by the scoring
    module once that module is ready.
    """

    router = APIRouter(prefix="/api", tags=["detections"])

    @router.post(
        "/detection",
        response_model=DetectionAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def receive_detection(result: DetectionResult) -> DetectionAccepted:
        payload = result.as_payload()

        try:
            stored_at = write_detection(payload)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="detection storage is unavailable",
            ) from exc

        if submit_to_scoring is not None:
            try:
                submit_to_scoring(payload)
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="scoring is unavailable",
                ) from exc

        return DetectionAccepted(
            session_id=result.session_id,
            player_id=result.player_id,
            module=result.module,
            stored_at=str(stored_at) if stored_at is not None else None,
        )

    return router
