import unittest

from step8_fov_target import FovTargetSelector, camera_axes, world_to_screen


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.camera = {"location": (0.0, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0), "fov": 90.0}

    def test_axes_are_orthogonal(self):
        forward, right, up = camera_axes((17.0, -42.0, 11.0))
        self.assertAlmostEqual(sum(a * b for a, b in zip(forward, right)), 0.0, places=7)
        self.assertAlmostEqual(sum(a * b for a, b in zip(forward, up)), 0.0, places=7)
        self.assertAlmostEqual(sum(a * b for a, b in zip(right, up)), 0.0, places=7)

    def test_forward_projects_to_centre(self):
        point = world_to_screen((100.0, 0.0, 0.0), self.camera, 1920, 1080)
        self.assertEqual(point, (960.0, 540.0, 100.0))

    def test_right_projects_right(self):
        point = world_to_screen((100.0, 100.0, 0.0), self.camera, 1920, 1080)
        self.assertAlmostEqual(point[0], 1920.0)
        self.assertAlmostEqual(point[1], 540.0)

    def test_behind_camera_is_rejected(self):
        self.assertIsNone(world_to_screen((-1.0, 0.0, 0.0), self.camera, 1920, 1080))


class SelectorTests(unittest.TestCase):
    def test_selects_closest_inside_circle(self):
        selector = FovTargetSelector(100.0)
        selected = selector.choose(
            [
                {"pawn": 1, "screen_dist": 70.0},
                {"pawn": 2, "screen_dist": 30.0},
                {"pawn": 3, "screen_dist": 110.0},
            ]
        )
        self.assertEqual(selected["pawn"], 2)

    def test_lock_hysteresis_and_release(self):
        selector = FovTargetSelector(100.0)
        selector.locked_pawn = 7
        self.assertEqual(selector.choose([{"pawn": 7, "screen_dist": 110.0}])["pawn"], 7)
        self.assertIsNone(selector.choose([{"pawn": 7, "screen_dist": 120.0}]))

    def test_distance_priority_selects_nearest_world_target(self):
        selector = FovTargetSelector(500.0, priority="distance")
        selected = selector.choose(
            [
                {"pawn": 1, "screen_dist": 20.0, "dist": 1000.0},
                {"pawn": 2, "screen_dist": 300.0, "dist": 100.0},
            ]
        )
        self.assertEqual(selected["pawn"], 2)


if __name__ == "__main__":
    unittest.main()
