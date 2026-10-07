"""Shared facade adapter. Import alone never configures or sends telemetry."""
import importlib
import os


class Telemetry:
    def __init__(self):
        if not os.environ.get('GZZ_TELEMETRY_OUTBOX'):
            raise ValueError('Launcher must provide a dedicated GZZ_TELEMETRY_OUTBOX')
        self.api = importlib.import_module('shared.logger')
        self.api.configure_client(self.api.ClientConfig.from_env())

    def send(self, result):
        # Shared generates the canonical UUID; queued is not a server ACK.
        return self.api.send_detection(result)

    def close(self):
        try:
            flushed = bool(self.api.flush_client(timeout=5.0))
        finally:
            closed = bool(self.api.shutdown_client(timeout=5.0))
        return flushed, closed
