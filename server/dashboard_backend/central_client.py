"""Server-side client for C's existing APIs; never ships tokens to the UI."""

import json
import math
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from shared.schema import validate_identifier


class CentralQueryError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__("central server query failed")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class CentralDashboardClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 3):
        parsed = urlsplit(base_url)
        parsed.port  # Reject malformed/invalid ports before any request.
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query
                or parsed.fragment or parsed.path not in ("", "/")
                or any(character.isspace() for character in base_url)):
            raise ValueError("central URL must be an origin")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ValueError("HTTPS required except loopback testing")
        if not token or any(ord(character) < 33 or ord(character) > 126 for character in token):
            raise ValueError("invalid central Dashboard token")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid timeout")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _get(self, path: str, *, authenticated=True):
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self._token
        request = urllib.request.Request(self.base_url + path, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise CentralQueryError(502)
            value = json.loads(data)
            if not isinstance(value, dict):
                raise CentralQueryError(502)
            return value
        except urllib.error.HTTPError as exc:
            exc.close()
            raise CentralQueryError(exc.code if exc.code in (401, 404, 422, 503) else 502) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CentralQueryError(503) from None
        except (ValueError, UnicodeError):
            raise CentralQueryError(502) from None

    def health(self):
        return self._get("/health", authenticated=False)

    def verdict(self, session_id: str, player_id: str):
        return self._get(f"/api/dashboard/verdict/{validate_identifier(session_id)}/{validate_identifier(player_id)}")

    def heartbeat(self, session_id: str, client_id: str):
        return self._get(f"/api/dashboard/heartbeat/{validate_identifier(session_id)}/{validate_identifier(client_id)}")
