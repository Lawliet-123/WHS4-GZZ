"""Queue-first API and one bounded-retry worker. No heartbeat or detection policy."""
from __future__ import annotations

import math
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass

from ._locking import FileLock
from .config import ClientConfig
from .errors import ClientClosedError
from .outbox import SQLiteOutbox
from .schema import encode_event, validate_event_id
from .transport import DeliveryOutcome, DeliveryTransport, HttpTransport


@dataclass(frozen=True)
class QueuedReceipt:
    event_id: str
    status: str = "queued"  # This is not a server receipt.


@dataclass(frozen=True)
class ClientStatus:
    pending: int
    failed: int
    payload_bytes: int
    worker_alive: bool
    closed: bool
    last_delivery: str
    last_code: str | None
    last_event_id: str | None
    acknowledged_this_run: int


class DetectionClient:
    def __init__(self, config: ClientConfig, *, transport: DeliveryTransport | None = None):
        self.config = config
        self._owner = FileLock(config.outbox_path.with_suffix(config.outbox_path.suffix + ".sender.lock"))
        self._owner.acquire()
        try:
            self._outbox = SQLiteOutbox(config)
            self._transport = transport if transport is not None else HttpTransport(config)
            self._stop, self._wake = threading.Event(), threading.Event()
            self._state_lock = threading.Lock()
            self._closed = False
            self._last_delivery, self._last_code, self._last_event_id = "IDLE", None, None
            self._acknowledged = 0
            self._thread = threading.Thread(target=self._run, name="gzz-telemetry-sender", daemon=True)
            self._thread.start()
        except BaseException:
            self._owner.release()
            raise

    def send_detection(self, result, *, event_id: str | None = None) -> QueuedReceipt:
        payload = encode_event(result, max_bytes=self.config.max_event_bytes)
        key = validate_event_id(event_id) if event_id is not None else str(uuid.uuid4())
        with self._state_lock:
            if self._closed or not self._thread.is_alive():
                raise ClientClosedError("sender is closed or stopped; inspect status before restarting")
            self._outbox.enqueue(key, payload)
        self._wake.set()
        return QueuedReceipt(key)

    def status(self) -> ClientStatus:
        pending, failed, size = self._outbox.counts()
        with self._state_lock:
            return ClientStatus(pending, failed, size, self._thread.is_alive(), self._closed,
                                self._last_delivery, self._last_code, self._last_event_id, self._acknowledged)

    def failures(self, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be 1..10000")
        return self._outbox.failures(limit)

    def retry_failed(self, event_id: str | None = None) -> int:
        if event_id is not None:
            validate_event_id(event_id)
        with self._state_lock:
            if self._closed or not self._thread.is_alive():
                raise ClientClosedError("sender is closed or stopped")
            count = self._outbox.retry_failed(event_id)
        self._wake.set()
        return count

    def flush(self, timeout: float = 5.0) -> bool:
        """True only when no pending OR failed records remain. Never deletes failures."""
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        deadline = time.monotonic() + timeout
        self._wake.set()
        while True:
            state = self.status()
            if state.pending == 0:
                return state.failed == 0
            if not state.worker_alive or time.monotonic() >= deadline:
                return False
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def close(self, timeout: float = 5.0) -> bool:
        """Stop worker; queued data stays on disk. False means an in-flight call remains."""
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        with self._state_lock:
            self._closed = True
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _state(self, state: str, key: str | None = None, code: str | None = None) -> None:
        # Custom transports must return public codes, not tokens or response bodies.
        if code is not None and not re.fullmatch(r"[a-z0-9_]{1,64}", code):
            code = "transport_error"
        with self._state_lock:
            self._last_delivery, self._last_code, self._last_event_id = state, code, key

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.clear()
                item = self._outbox.next_due()
                if item is None:
                    self._wake.wait(0.1)
                    continue
                if item.attempts >= self.config.max_attempts:
                    self._outbox.fail(item.event_id, "retry_exhausted")
                    self._state("FAILED", item.event_id, "retry_exhausted")
                    continue
                self._outbox.begin_attempt(item.event_id)
                self._state("SENDING", item.event_id)
                try:
                    outcome = self._transport.send(item.payload, item.event_id)
                    if not isinstance(outcome, DeliveryOutcome) or outcome.disposition not in {"accepted", "retry", "rejected"}:
                        raise ValueError("invalid transport outcome")
                    if outcome.retry_after_seconds is not None and (type(outcome.retry_after_seconds) not in (int, float)
                            or not math.isfinite(outcome.retry_after_seconds) or outcome.retry_after_seconds < 0):
                        raise ValueError("invalid retry delay")
                except Exception:
                    outcome = DeliveryOutcome("retry", "transport_error")
                code = outcome.code if isinstance(outcome.code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", outcome.code) else "transport_error"
                if outcome.disposition == "accepted":
                    self._outbox.acknowledge(item.event_id)
                    with self._state_lock:
                        self._acknowledged += 1
                    self._state("DELIVERED", item.event_id, code)
                elif outcome.disposition == "rejected" or item.attempts + 1 >= self.config.max_attempts:
                    self._outbox.fail(item.event_id, code)
                    self._state("FAILED", item.event_id, code)
                else:
                    delay = min(self.config.retry_max_seconds, self.config.retry_base_seconds * 2**min(item.attempts, 30))
                    delay *= 1 + random.uniform(0, self.config.retry_jitter_ratio)
                    if outcome.retry_after_seconds is not None:
                        delay = max(delay, outcome.retry_after_seconds)
                    delay = min(self.config.retry_max_seconds, delay)
                    self._outbox.retry_later(item.event_id, delay, code)
                    self._state("RETRYING", item.event_id, code)
        except Exception:
            self._state("ERROR", code="outbox_error")
        finally:
            self._owner.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
