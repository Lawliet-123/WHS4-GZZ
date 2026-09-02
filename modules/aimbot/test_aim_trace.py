import unittest

from step9_aim_trace import normalize_angle, shortest_delta, step_rotation


class AngleTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_angle(180.0), -180.0)
        self.assertEqual(normalize_angle(540.0), -180.0)
        self.assertEqual(normalize_angle(-181.0), 179.0)

    def test_shortest_wraparound_delta(self):
        self.assertEqual(shortest_delta(179.0, -179.0), 2.0)
        self.assertEqual(shortest_delta(-179.0, 179.0), -2.0)

    def test_speed_limit(self):
        result = step_rotation((0.0, 0.0, 0.0), (90.0, -90.0), 30.0, 0.5)
        self.assertEqual(result, (15.0, -15.0, 0.0))

    def test_reaches_near_target(self):
        result = step_rotation((10.0, 20.0, 0.0), (11.0, 18.0), 100.0, 1.0)
        self.assertEqual(result, (11.0, 18.0, 0.0))


if __name__ == "__main__":
    unittest.main()
