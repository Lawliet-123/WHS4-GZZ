import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.receiver import create_router


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
        app = FastAPI()
        app.include_router(
            create_router(self.stored.append, self.scored.append)
        )
        self.client = TestClient(app)

    def test_valid_result_is_stored_then_sent_to_scoring(self):
        response = self.client.post("/api/detection", json=valid_payload())

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["accepted"])
        self.assertEqual(self.stored[0]["module"], "aimbot")
        self.assertEqual(self.scored, self.stored)

    def test_invalid_result_is_not_stored(self):
        payload = valid_payload()
        del payload["raw_score"]

        response = self.client.post("/api/detection", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.stored, [])
        self.assertEqual(self.scored, [])


if __name__ == "__main__":
    unittest.main()
