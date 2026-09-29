"""Stable facade for team integrations. Keep policies in the smaller modules.

Client: configure_client -> send_detection -> flush_client -> shutdown_client.
Server: configure_writer -> write_detection. Import alone does none of these.
For multiple independent instances prefer DetectionClient / DetectionWriter.
"""
from __future__ import annotations

import threading

from .client import ClientStatus, DetectionClient, QueuedReceipt
from .config import ClientConfig, WriterConfig
from .errors import ConfigurationError, ResourceBusyError
from .storage import DetectionSink, DetectionWriter, WriteReceipt

_guard = threading.RLock()
_client: DetectionClient | None = None
_writer: DetectionSink | None = None


def configure_client(config: ClientConfig) -> DetectionClient:
    global _client
    with _guard:
        if _client is not None:
            raise ResourceBusyError("call shutdown_client before replacing client configuration")
        _client = DetectionClient(config)
        return _client


def _get_client() -> DetectionClient:
    with _guard:
        if _client is None:
            raise ConfigurationError("configure_client must be called explicitly first")
        return _client


def send_detection(result, *, event_id: str | None = None) -> QueuedReceipt:
    return _get_client().send_detection(result, event_id=event_id)


def get_client_status() -> ClientStatus:
    return _get_client().status()


def flush_client(timeout: float = 5.0) -> bool:
    return _get_client().flush(timeout)


def shutdown_client(timeout: float = 5.0) -> bool:
    global _client
    with _guard:
        if _client is None:
            return True
        if not _client.close(timeout):
            return False
        _client = None
        return True


def configure_writer(config: WriterConfig | None = None, *, writer: DetectionSink | None = None) -> DetectionSink:
    """Inject another sink (e.g. durable cloud storage) without changing detectors."""
    global _writer
    if (config is None) == (writer is None):
        raise ConfigurationError("provide either WriterConfig or a writer adapter, not both")
    replacement = writer if writer is not None else DetectionWriter(config)
    with _guard:
        _writer = replacement
    return replacement


def write_detection(result, *, event_id: str) -> WriteReceipt:
    with _guard:
        writer = _writer
    if writer is None:
        raise ConfigurationError("configure_writer must be called explicitly first")
    return writer.write_detection(result, event_id=event_id)
