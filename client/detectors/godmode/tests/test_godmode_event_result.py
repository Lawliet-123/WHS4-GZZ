import unittest

from core.models import PlayerSnapshot
from detector.godmode_detector import GodModeDetector


class GodModeEventResultTest(unittest.TestCase):

    def test_health_100_to_100_does_not_trigger_restore(self):
        detector = GodModeDetector()

        first = PlayerSnapshot(
            timestamp=0.0,
            health=100.0,
            max_health=100.0,
            dead=False,
            invincible=False,
            change_before_health=100.0,
        )

        second = PlayerSnapshot(
            timestamp=0.25,
            health=100.0,
            max_health=100.0,
            dead=False,
            invincible=False,
            change_before_health=100.0,
        )

        detector.process(first)
        result = detector.process(second)

        self.assertNotIn(
            "Health restored without heal event",
            result.new_reasons,
        )

        self.assertEqual(
            result.new_score,
            0,
        )

    def test_invincible_event_is_new_only_once(self):
        detector = GodModeDetector()

        detector.process(
            PlayerSnapshot(
                timestamp=0.0,
                health=100.0,
                max_health=100.0,
                dead=False,
                invincible=False,
                change_before_health=100.0,
            )
        )

        detector.process(
            PlayerSnapshot(
                timestamp=1.0,
                health=100.0,
                max_health=100.0,
                dead=False,
                invincible=True,
                change_before_health=100.0,
            )
        )

        triggered = detector.process(
            PlayerSnapshot(
                timestamp=2.6,
                health=100.0,
                max_health=100.0,
                dead=False,
                invincible=True,
                change_before_health=100.0,
            )
        )

        self.assertEqual(
            triggered.new_score,
            2,
        )

        self.assertEqual(
            triggered.new_reasons,
            [
                "Invincible abnormal persistence"
            ],
        )

        repeated = detector.process(
            PlayerSnapshot(
                timestamp=3.0,
                health=100.0,
                max_health=100.0,
                dead=False,
                invincible=True,
                change_before_health=100.0,
            )
        )

        self.assertEqual(
            repeated.new_score,
            0,
        )

        self.assertEqual(
            repeated.new_reasons,
            [],
        )

        self.assertEqual(
            repeated.score,
            2,
        )

        self.assertEqual(
            repeated.reasons,
            [
                "Invincible abnormal persistence"
            ],
        )


if __name__ == "__main__":
    unittest.main()