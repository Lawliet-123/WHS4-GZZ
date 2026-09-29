"""Public failures callers can handle without depending on implementation details."""


class SharedError(Exception):
    pass


class ConfigurationError(SharedError, ValueError):
    pass


class ValidationError(SharedError, ValueError):
    pass


class QueueFullError(SharedError):
    """The result was NOT accepted. Existing records have not been evicted."""


class IdempotencyConflict(SharedError):
    """One event ID was used with two different payloads."""


class StorageError(SharedError):
    pass


class ResourceBusyError(SharedError):
    pass


class ClientClosedError(SharedError):
    pass


class EventAlreadyFailedError(SharedError):
    """Explicit retry_failed is required; enqueue did not restart this event."""
