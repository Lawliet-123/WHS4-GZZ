import os

from fastapi import FastAPI

from server.receiver import create_heartbeat_router
from server.receiver.heartbeat_store import HeartbeatStore
from server.receiver.router import bearer_token_verifier


app = FastAPI()


@app.get("/health")
def health():
    return {
        "status": "ok",
    }


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