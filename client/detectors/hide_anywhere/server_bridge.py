"""Adapter for the team's logger facade; no guessed transport protocol."""
import importlib
import os


class ServerBridge:
    def __init__(self, emit):
        self.emit = emit
        self.api = importlib.import_module('shared.logger')
        if not os.environ.get('GZZ_TELEMETRY_OUTBOX'):
            raise ValueError('Launcher must supply a dedicated GZZ_TELEMETRY_OUTBOX path')
        self.api.configure_client(self.api.ClientConfig.from_env())

    def send(self, event):
        try:
            receipt = self.api.send_detection(event)
            self.emit('server_queued',
                      event_id=receipt.event_id,
                      message='Stored in local outbox; server receipt not confirmed',
                      timestamp_ms=event['timestamp_ms'], raw_score=event['raw_score'])
        except Exception as exc:
            # Keep local collection running; failed events remain in events.jsonl.
            self.emit('server_enqueue_error',
                      error_type=type(exc).__name__,
                      message='Not queued; event remains in local events.jsonl')

    def close(self):
        try:
            flushed = bool(self.api.flush_client(timeout=5.0))
            self.emit('server_flush', complete=flushed)
        except Exception as exc:
            self.emit('server_flush_error', error_type=type(exc).__name__)
        finally:
            try:
                closed = bool(self.api.shutdown_client(timeout=5.0))
                self.emit('server_shutdown', complete=closed)
            except Exception as exc:
                self.emit('server_shutdown_error', error_type=type(exc).__name__)
