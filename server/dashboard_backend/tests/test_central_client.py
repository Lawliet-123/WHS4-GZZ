import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from server.dashboard_backend.central_client import CentralDashboardClient, CentralQueryError


class CentralClientTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.mode = "normal"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("Authorization")))
                if owner.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/target")
                    self.end_headers()
                    return
                self.send_response(503 if owner.mode == "failure" else 200)
                self.end_headers()
                self.wfile.write(b"not JSON" if owner.mode == "malformed" else b'{"status":"ok"}')

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.client = CentralDashboardClient(f"http://127.0.0.1:{self.server.server_port}", "synthetic-secret")
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def test_token_only_attached_to_authenticated_queries(self):
        self.client.health()
        self.client.verdict("session", "pc")
        self.assertEqual(self.requests[0], ("/health", None))
        self.assertEqual(self.requests[1], ("/api/dashboard/verdict/session/pc", "Bearer synthetic-secret"))

    def test_redirect_not_followed_and_token_not_forwarded(self):
        self.mode = "redirect"
        with self.assertRaises(CentralQueryError) as result:
            self.client.verdict("session", "pc")
        self.assertEqual(result.exception.status_code, 502)
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("synthetic-secret", str(result.exception))

    def test_malformed_response_and_storage_failure_remain_distinct(self):
        for mode, code in (("malformed", 502), ("failure", 503)):
            self.mode = mode
            with self.assertRaises(CentralQueryError) as result:
                self.client.health()
            self.assertEqual(result.exception.status_code, code)

    def test_invalid_configuration_and_identifier_rejected(self):
        for url in ("http://remote.example", "https://user:pass@remote.example", "https://remote.example?token=test", "https://remote.example:wrong"):
            with self.assertRaises(ValueError):
                CentralDashboardClient(url, "synthetic-secret")
        with self.assertRaises(ValueError):
            self.client.verdict("../invalid", "pc")
        self.assertEqual(self.requests, [])
