"""Adapter for the team's logger facade; no guessed transport protocol."""
import importlib
import json
import uuid


class ServerBridge:
    def __init__(self, module_name, config_path, emit):
        self.emit = emit
        self.run_id = uuid.uuid4().hex
        self.sequence = 0
        self.api = importlib.import_module(module_name)
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict):
            raise ValueError('ClientConfig JSON must be an object')
        self.api.configure_client(self.api.ClientConfig(**config))

    def send(self, event):
        self.sequence += 1
        event_id = f'{self.run_id}:{self.sequence}'
        try:
            self.api.send_detection(event, event_id=event_id)
            self.emit('server_queued', event_id=event_id,
                      timestamp_ms=event['timestamp_ms'], raw_score=event['raw_score'])
        except Exception as exc:
            # Keep local collection running; failed events remain in events.jsonl.
            self.emit('server_enqueue_error', event_id=event_id,
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
