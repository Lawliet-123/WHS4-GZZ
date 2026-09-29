import unittest

from pydantic import ValidationError

from server.receiver.models import DetectionResult


def valid_result(**changes):
    result = {
        "session_id": "round_12",
        "player_id": "player_042",
        "module": "aimbot",
        "timestamp_ms": 507000,
        "evidence": {"hidden_target_shots": 3},
        "reasons": ["Repeated Precise Shots Toward Hidden Target"],
        "raw_score": 3,
    }
    result.update(changes)
    return result


class DetectionResultTests(unittest.TestCase):
    def test_common_format_is_accepted(self):
        result = DetectionResult(**valid_result())
        self.assertEqual(result.module, "aimbot")
        self.assertEqual(result.raw_score, 3)

    def test_extra_detector_metadata_is_preserved(self):
        result = DetectionResult(**valid_result(status="SUSPICIOUS", window_id="w1"))
        self.assertEqual(result.as_payload()["status"], "SUSPICIOUS")
        self.assertEqual(result.as_payload()["window_id"], "w1")

    def test_missing_required_field_is_rejected(self):
        payload = valid_result()
        del payload["player_id"]
        with self.assertRaises(ValidationError):
            DetectionResult(**payload)

    def test_invalid_score_is_rejected(self):
        with self.assertRaises(ValidationError):
            DetectionResult(**valid_result(raw_score=True))


if __name__ == "__main__":
    unittest.main()
