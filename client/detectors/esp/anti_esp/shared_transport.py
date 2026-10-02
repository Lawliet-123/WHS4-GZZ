"""Bridge ESP common events to the repository's shared 0.2.0 client."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from shared.config import ClientConfig
from shared.errors import SharedError
from shared.logger import (
    configure_client,
    flush_client,
    send_detection,
    shutdown_client,
)
from shared.schema import encode_event


def validate_common_event(result: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach one exact seven-field shared Event."""
    payload = dict(result)
    encode_event(payload)
    return payload


class SharedEventSink:
    """Own the shared sender lifecycle for one ESP detector process."""

    @classmethod
    def from_environment(cls) -> "SharedEventSink":
        configure_client(ClientConfig.from_env())
        return cls()

    @staticmethod
    def _event_id(outbox_id: str) -> str:
        # The detector's SQLite outbox ID is content-derived. Mapping it to a
        # UUID keeps shared retries idempotent across process restarts.
        return str(uuid5(NAMESPACE_URL, f"gzz-esp:{outbox_id}"))

    def queue(self, result: Mapping[str, Any], outbox_id: str) -> bool:
        try:
            payload = validate_common_event(result)
            receipt = send_detection(
                payload,
                event_id=self._event_id(outbox_id),
            )
        except SharedError as error:
            # Never print configuration values because they may contain a token.
            print(f"[shared] ESP event not queued: {type(error).__name__}")
            return False
        print(f"[shared] ESP event {receipt.status}: {receipt.event_id}")
        return True

    def close(self) -> None:
        try:
            delivered = flush_client(timeout=3)
            print(f"[shared] flush delivered={delivered}")
        except SharedError as error:
            print(f"[shared] flush failed: {type(error).__name__}")
        try:
            stopped = shutdown_client(timeout=5)
            print(f"[shared] shutdown complete={stopped}")
        except SharedError as error:
            print(f"[shared] shutdown failed: {type(error).__name__}")


__all__ = ["SharedEventSink", "validate_common_event"]
