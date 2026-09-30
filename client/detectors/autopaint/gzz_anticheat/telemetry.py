"""Forward existing Events through shared; no HTTP or scoring policy here."""
from __future__ import annotations

import sys


class TelemetryForwarder:
    """Own one standalone sender, or borrow the integrated app's sender."""

    def __init__(self, mode: str):
        if mode not in {"managed", "external", "off"}:
            raise ValueError("unknown telemetry mode")
        self.mode = mode
        self.logger = None
        self.errors = ()
        self.queued = 0
        self.enqueue_failed = 0
        self._last_problem = None

    @staticmethod
    def report(message: str) -> None:
        print(f"[gzz telemetry] {message}", file=sys.stderr, flush=True)

    def __enter__(self):
        if self.mode == "off":
            self.report("OFF: local recording only")
            return self
        try:
            from shared import logger
            from shared.config import ClientConfig
            from shared.errors import SharedError
        except ImportError as exc:
            raise RuntimeError("shared package unavailable; add the team project root to PYTHONPATH") from exc
        self.errors = (SharedError, OSError)
        try:
            if self.mode == "managed":
                logger.configure_client(ClientConfig.from_env())
            else:
                state = logger.get_client_status()
                if state.closed or not state.worker_alive:
                    raise RuntimeError("external shared sender is not running")
        except self.errors as exc:
            # Exception text, config objects and credentials must not reach logs.
            raise RuntimeError(
                f"telemetry startup failed ({type(exc).__name__}); check GZZ_TELEMETRY settings "
                "and sender ownership; use --telemetry off for local-only testing"
            ) from None
        self.logger = logger
        self.report(f"mode={self.mode}; queued does not mean server-acknowledged")
        return self

    def send(self, result: dict) -> None:
        if self.logger is None:
            return
        try:
            self.logger.send_detection(result)
        except self.errors as exc:
            self.enqueue_failed += 1
            self.report(
                f"ENQUEUE_FAILED {type(exc).__name__} t={result['timestamp_ms']}ms; "
                "Event remains in local events.jsonl; not accepted by shared"
            )
        else:
            self.queued += 1
        # Background delivery errors are separate from successful enqueue calls.
        try:
            state = self.logger.get_client_status()
            problem = (state.failed, state.worker_alive, state.closed, state.last_code)
            if state.failed or not state.worker_alive or state.closed:
                if problem != self._last_problem:
                    self.report(f"shared failed={state.failed} worker_alive={state.worker_alive} "
                                f"closed={state.closed} code={state.last_code}")
                self._last_problem = problem
            else:
                self._last_problem = None
        except self.errors as exc:
            self.report(f"STATUS_UNAVAILABLE {type(exc).__name__}")

    def __exit__(self, *_):
        if self.logger is None:
            return False
        if self.mode == "external":
            self.report(f"queued={self.queued} enqueue_failed={self.enqueue_failed}; "
                        "shared lifecycle remains with the integrated app")
            return False
        flushed, stopped = False, False
        try:
            try:
                flushed = self.logger.flush_client(timeout=3)
            except (Exception, KeyboardInterrupt) as exc:
                self.report(f"FLUSH_INTERRUPTED {type(exc).__name__}")
            try:
                state = self.logger.get_client_status()
                self.report(f"shared pending={state.pending} failed={state.failed} "
                            f"acknowledged_this_run={state.acknowledged_this_run}")
            except (Exception, KeyboardInterrupt) as exc:
                self.report(f"STATUS_UNAVAILABLE {type(exc).__name__}")
        finally:
            # Always attempt shutdown, including sensor/Lua/file errors and Ctrl+C.
            try:
                stopped = self.logger.shutdown_client(timeout=5)
            except (Exception, KeyboardInterrupt) as exc:
                self.report(f"SHUTDOWN_INTERRUPTED {type(exc).__name__}")
            self.report(f"queued={self.queued} enqueue_failed={self.enqueue_failed} "
                        f"flushed={flushed} stopped={stopped}")
            if not flushed:
                self.report("delivery not confirmed for all queued records; inspect the retained shared outbox")
            if not stopped:
                self.report("sender shutdown incomplete; do not open a second sender on the same outbox")
        return False
