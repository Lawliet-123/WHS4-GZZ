"""Replace this adapter when the team's HTTP stack changes; leave Events unchanged."""
from __future__ import annotations

import json
import math
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Literal, Protocol

from . import PROTOCOL_VERSION
from .config import ClientConfig


@dataclass(frozen=True)
class DeliveryOutcome:
    disposition: Literal["accepted", "retry", "rejected"]
    code: str
    retry_after_seconds: float | None = None


class DeliveryTransport(Protocol):
    def send(self, payload: bytes, event_id: str) -> DeliveryOutcome: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


class HttpTransport:
    def __init__(self, config: ClientConfig):
        self.config = config
        context = ssl.create_default_context()
        context.set_alpn_protocols(["http/1.1"])
        proxy = urllib.request.ProxyHandler() if config.use_environment_proxy else urllib.request.ProxyHandler({})
        self._opener = urllib.request.build_opener(proxy, _NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def send(self, payload: bytes, event_id: str) -> DeliveryOutcome:
        request = urllib.request.Request(self.config.endpoint, data=payload, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Authorization": "Bearer " + self.config.api_token,
            "Idempotency-Key": event_id, "X-GZZ-Protocol-Version": PROTOCOL_VERSION,
        })
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                # A successful HTTP status alone does NOT acknowledge durable acceptance.
                data = response.read(16385)
                if response.status != 200 or len(data) > 16384:
                    return DeliveryOutcome("retry", "invalid_ack")
                if response.headers.get_content_type() != "application/json":
                    return DeliveryOutcome("retry", "invalid_ack")
                try:
                    ack = json.loads(data)
                except (ValueError, UnicodeError, RecursionError):
                    return DeliveryOutcome("retry", "invalid_ack")
                if (not isinstance(ack, dict) or ack.get("event_id") != event_id or not isinstance(ack.get("status"), str)
                        or ack.get("status") not in {"stored", "duplicate"}):
                    return DeliveryOutcome("retry", "invalid_ack")
                return DeliveryOutcome("accepted", ack["status"])
        except urllib.error.HTTPError as exc:
            status, retry_after = exc.code, _retry_after(exc.headers.get("Retry-After"))
            exc.close()
            if status in {408, 425, 429} or 500 <= status <= 599:
                return DeliveryOutcome("retry", f"http_{status}", retry_after)
            return DeliveryOutcome("rejected", f"http_{status}")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                return DeliveryOutcome("rejected", "tls_verification_failed")
            return DeliveryOutcome("retry", "network_error")
        except (TimeoutError, ConnectionError, OSError):
            return DeliveryOutcome("retry", "network_error")
