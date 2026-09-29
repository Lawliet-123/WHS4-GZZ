"""Explicit configuration; no environment reads or network activity on import."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigurationError


def _positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{name} must be finite and positive")


def _positive_int(name: str, value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ClientConfig:
    server_url: str
    api_token: str = field(repr=False)
    outbox_path: Path = Path("telemetry-outbox/client.sqlite3")
    detection_path: str = "/api/detection"
    timeout_seconds: float = 3.0
    max_attempts: int = 8
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 60.0
    retry_jitter_ratio: float = 0.2
    max_queue_events: int = 10000
    max_queue_bytes: int = 32 * 1024 * 1024
    max_event_bytes: int = 256 * 1024
    allow_insecure_loopback: bool = False
    use_environment_proxy: bool = False

    def __post_init__(self) -> None:
        if type(self.allow_insecure_loopback) is not bool or type(self.use_environment_proxy) is not bool:
            raise ConfigurationError("transport flags must be booleans")
        if not isinstance(self.server_url, str) or any(c.isspace() for c in self.server_url):
            raise ConfigurationError("server_url must be an absolute URL without whitespace")
        try:
            parsed = urlsplit(self.server_url)
            parsed.port
        except ValueError as exc:
            raise ConfigurationError("invalid server_url") from exc
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ConfigurationError("server_url must contain only scheme, host and optional port")
        if parsed.scheme != "https":
            if not (parsed.scheme == "http" and self.allow_insecure_loopback
                    and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
                raise ConfigurationError("HTTPS required; explicit loopback-only HTTP is for tests")
        if (not isinstance(self.api_token, str) or not self.api_token
                or any(ord(c) < 33 or ord(c) > 126 for c in self.api_token)):
            raise ConfigurationError("api_token must be a nonempty printable ASCII token without spaces")
        if (not isinstance(self.detection_path, str) or not self.detection_path.startswith("/")
                or self.detection_path.startswith("//")
                or any(ord(c) < 33 or ord(c) > 126 for c in self.detection_path)
                or "?" in self.detection_path or "#" in self.detection_path
                or "\\" in self.detection_path):
            raise ConfigurationError("detection_path must be an absolute URL path")
        for name in ("timeout_seconds", "retry_base_seconds", "retry_max_seconds"):
            _positive(name, getattr(self, name))
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ConfigurationError("retry_max_seconds must be >= retry_base_seconds")
        if (isinstance(self.retry_jitter_ratio, bool) or not isinstance(self.retry_jitter_ratio, (int, float))
                or not 0 <= self.retry_jitter_ratio <= 1):
            raise ConfigurationError("retry_jitter_ratio must be between 0 and 1")
        for name in ("max_attempts", "max_queue_events", "max_queue_bytes", "max_event_bytes"):
            _positive_int(name, getattr(self, name))
        object.__setattr__(self, "outbox_path", Path(self.outbox_path).expanduser().resolve())

    @property
    def endpoint(self) -> str:
        return self.server_url.rstrip("/") + self.detection_path

    @classmethod
    def from_env(cls, prefix: str = "GZZ_TELEMETRY_") -> ClientConfig:
        """Only the caller's explicit invocation reads environment variables."""
        values = {"server_url": os.environ.get(prefix + "URL", ""),
                  "api_token": os.environ.get(prefix + "TOKEN", "")}
        options = {
            "OUTBOX": ("outbox_path", Path), "DETECTION_PATH": ("detection_path", str),
            "TIMEOUT_SECONDS": ("timeout_seconds", float), "MAX_ATTEMPTS": ("max_attempts", int),
            "RETRY_BASE_SECONDS": ("retry_base_seconds", float),
            "RETRY_MAX_SECONDS": ("retry_max_seconds", float),
            "RETRY_JITTER_RATIO": ("retry_jitter_ratio", float),
            "MAX_QUEUE_EVENTS": ("max_queue_events", int),
            "MAX_QUEUE_BYTES": ("max_queue_bytes", int), "MAX_EVENT_BYTES": ("max_event_bytes", int),
        }
        for suffix, (name, convert) in options.items():
            if prefix + suffix in os.environ:
                try:
                    values[name] = convert(os.environ[prefix + suffix])
                except ValueError as exc:
                    raise ConfigurationError(f"invalid setting: {prefix + suffix}") from exc
        for suffix, name in (("ALLOW_INSECURE_LOOPBACK", "allow_insecure_loopback"),
                             ("USE_ENVIRONMENT_PROXY", "use_environment_proxy")):
            if prefix + suffix in os.environ:
                value = os.environ[prefix + suffix].lower()
                if value not in {"true", "false", "1", "0"}:
                    raise ConfigurationError(f"invalid boolean setting: {prefix + suffix}")
                values[name] = value in {"true", "1"}
        return cls(**values)


@dataclass(frozen=True)
class WriterConfig:
    root: Path = Path("server/logs/detections")
    max_event_bytes: int = 256 * 1024
    lock_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        _positive_int("max_event_bytes", self.max_event_bytes)
        _positive("lock_timeout_seconds", self.lock_timeout_seconds)
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @classmethod
    def from_env(cls, prefix: str = "GZZ_TELEMETRY_") -> WriterConfig:
        try:
            return cls(root=Path(os.environ.get(prefix + "LOG_ROOT", "server/logs/detections")),
                       max_event_bytes=int(os.environ.get(prefix + "MAX_EVENT_BYTES", "262144")),
                       lock_timeout_seconds=float(os.environ.get(prefix + "WRITE_LOCK_TIMEOUT_SECONDS", "3")))
        except ValueError as exc:
            raise ConfigurationError("invalid writer environment configuration") from exc
