"""Authenticated sync routes run disk and scoring queries in FastAPI's pool."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shared.errors import SharedError
from .service import DashboardService
from .central_client import CentralQueryError


def create_dashboard_router(service: DashboardService, *, verify_token):
    bearer = HTTPBearer(auto_error=False)

    def authenticate(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not verify_token(request.headers.get("authorization", "")):
            raise HTTPException(401, "invalid Dashboard credentials")

    router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(authenticate)])

    def current_service():
        # C builds the service during lifespan, after recovery has finished.
        return service() if callable(service) else service

    def call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CentralQueryError as exc:
            messages = {401: "central server authentication failed", 404: "central data not found", 422: "central query rejected", 503: "central server unavailable", 502: "invalid central server response"}
            raise HTTPException(exc.status_code, messages[exc.status_code]) from exc
        except ValueError as exc:
            raise HTTPException(422, "invalid query or cursor") from exc
        except (OSError, RuntimeError, SharedError, sqlite3.Error) as exc:
            raise HTTPException(503, "Dashboard data is unavailable") from exc

    @router.get("/overview")
    def overview(after_session: str = Query("", max_length=80), limit: int = Query(100, ge=1, le=200)):
        return call(current_service().overview, after_session=after_session, limit=limit)

    @router.get("/events")
    def events(session_id: str | None = Query(None, max_length=80), player_id: str | None = Query(None, max_length=100), module: str | None = Query(None, max_length=80), submodule: str | None = Query(None, max_length=100), q: str | None = Query(None, max_length=200), cursor: str | None = Query(None, max_length=4096), limit: int = Query(100, ge=1, le=200), after_sequence: int = Query(0, ge=0)):
        filters = {"session_id": session_id, "player_id": player_id, "module": module, "submodule": submodule, "q": q}
        return call(current_service().events, filters, cursor=cursor, limit=limit, after_sequence=after_sequence)

    @router.get("/events/{event_id}")
    def event_detail(event_id: str):
        if len(event_id) > 100:
            raise HTTPException(422, "invalid event identifier")
        result = call(current_service().detail, event_id)
        if result is None:
            raise HTTPException(404, "event not found")
        return result

    @router.get("/sessions/{session_id}/players/{player_id}/snapshot")
    def snapshot(session_id: str, player_id: str):
        return call(current_service().snapshot, session_id, player_id)

    @router.get("/sessions/{session_id}/players/{player_id}/history")
    def history(session_id: str, player_id: str, module: str = "godmode", after_sequence: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        return call(current_service().history, session_id, player_id, module=module, after_sequence=after_sequence, limit=limit)

    @router.get("/sessions/{session_id}/players/{player_id}/status")
    def component_status(session_id: str, player_id: str):
        return call(current_service().status, session_id, player_id)

    return router
