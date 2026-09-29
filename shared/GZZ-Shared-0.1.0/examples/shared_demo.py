"""Loopback-only sender/writer integration demo, using synthetic Events."""
from __future__ import annotations

import argparse
import hmac
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from shared.client import DetectionClient
from shared.config import ClientConfig, WriterConfig
from shared.errors import IdempotencyConflict, SharedError, ValidationError
from shared.schema import decode_event
from shared.storage import DetectionWriter


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("shared_demo_output"))
    args = parser.parse_args(argv)
    root = args.output_dir.resolve() / ("demo_" + uuid.uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    writer = DetectionWriter(WriterConfig(root / "server"))
    token = "loopback-demo-token"  # Synthetic credential, never for public deployment.
    request_count, lost_ack = [0], [False]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request_count[0] += 1
            status, value = 200, {}
            try:
                if self.path != "/api/detection":
                    status, value = 404, {"error": "not_found"}
                elif not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    status, value = 401, {"error": "unauthorized"}
                else:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 262144:
                        raise ValidationError("invalid body length")
                    result = decode_event(self.rfile.read(size))
                    receipt = writer.write_detection(result, event_id=self.headers.get("Idempotency-Key"))
                    value = receipt.to_ack()
                    # Simulate a lost successful response after storing the first Event.
                    if not lost_ack[0]:
                        lost_ack[0] = True
                        status, value = 503, {"error": "simulated_lost_ack"}
            except IdempotencyConflict:
                status, value = 409, {"error": "idempotency_conflict"}
            except (ValidationError, ValueError):
                status, value = 422, {"error": "invalid_event"}
            except SharedError:
                status, value = 503, {"error": "storage_unavailable"}
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    worker.start()
    try:
        config = ClientConfig(server_url=f"http://127.0.0.1:{server.server_port}", api_token=token,
                              allow_insecure_loopback=True, outbox_path=root / "outbox.sqlite3",
                              retry_base_seconds=0.1, retry_max_seconds=0.2)
        with DetectionClient(config) as client:
            for score, timestamp in ((0, 1000), (15, 2000)):
                client.send_detection({"session_id": "synthetic_demo", "player_id": "player_demo",
                                       "module": "autopaint", "timestamp_ms": timestamp,
                                       "evidence": {"synthetic": 1, "behavior_score": score},
                                       "reasons": [] if score == 0 else ["Synthetic demonstration"],
                                       "raw_score": score})
            delivered = client.flush(timeout=5)
            status = client.status()
        records = writer.iter_stored()
        passed = delivered and len(records) == 2 and sorted(r.result["raw_score"] for r in records) == [0, 15]
        print(json.dumps({"result": "PASS" if passed else "FAIL", "synthetic_data_only": True,
                          "output_dir": str(root), "http_requests": request_count[0],
                          "stored_unique_events": len(records), "pending": status.pending,
                          "failed": status.failed, "lost_ack_simulated": lost_ack[0]}, indent=2))
        return 0 if passed else 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


if __name__ == "__main__":
    raise SystemExit(main())
