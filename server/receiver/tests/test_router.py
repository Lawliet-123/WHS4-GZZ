import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.receiver import create_router
from server.receiver.router import bearer_token_verifier
from shared.storage import WriteReceipt


def valid_payload(**changes):
    payload = {
        "session_id": "round_12",
        "player_id": "player_042",
        "module": "aimbot",
        "timestamp_ms": 507000,
        "evidence": {"hidden_target_shots": 3},
        "reasons": ["Repeated Precise Shots Toward Hidden Target"],
        "raw_score": 3,
    }
    payload.update(changes)
    return payload


class ReceiverRouterTests(unittest.TestCase):
    def setUp(self):
        self.stored = []
        self.scored = []

        def writer(payload, *, event_id):
            self.stored.append((payload, event_id))
            return WriteReceipt(event_id, "stored", 1, Path("test.jsonl"))

        def scoring(payload, *, event_id, sequence):
            self.scored.append((payload, event_id, sequence))

        app = FastAPI()
        app.include_router(
            create_router(
                writer,
                scoring,
                verify_token=bearer_token_verifier("test-token"),
            )
        )
        self.client = TestClient(app)

    @staticmethod
    def headers(event_id="f4a62003-2fbd-4d9b-bb99-4eab8d0e8164"):
        return {
            "Authorization": "Bearer test-token",
            "Idempotency-Key": event_id,
            "X-GZZ-Protocol-Version": "1",
        }

    def test_valid_result_is_stored_then_sent_to_scoring(self):
        response = self.client.post("/api/detection", json=valid_payload(), headers=self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"event_id": self.headers()["Idempotency-Key"], "status": "stored"})
        self.assertEqual(self.stored[0][0]["module"], "aimbot")
        self.assertEqual(self.scored[0][2], 1)

    def test_invalid_result_is_not_stored(self):
        payload = valid_payload()
        del payload["raw_score"]

        response = self.client.post("/api/detection", json=payload, headers=self.headers())

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.stored, [])
        self.assertEqual(self.scored, [])

    def test_extra_root_field_is_rejected(self):
        response = self.client.post(
            "/api/detection",
            json=valid_payload(status="SUSPICIOUS"),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.stored, [])

    def test_missing_or_invalid_token_is_rejected(self):
        response = self.client.post(
            "/api/detection",
            json=valid_payload(),
            headers={
                "Idempotency-Key": self.headers()["Idempotency-Key"],
                "X-GZZ-Protocol-Version": "1",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.stored, [])


if __name__ == "__main__":
    unittest.main()
