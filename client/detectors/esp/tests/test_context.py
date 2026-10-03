import unittest

from anti_esp.core.context import ProcessTarget, SensorContext


class SensorContextTests(unittest.TestCase):
    def test_subject_identity_uses_pid_and_creation_time(self):
        target = ProcessTarget(77, r"C:\Game\game.exe", created_at=1_700_000_000.125)
        self.assertEqual(target.subject_id, "game-process:77:1700000000125")

    def test_missing_creation_time_is_explicit(self):
        target = ProcessTarget(77, r"C:\Game\game.exe")
        self.assertEqual(target.subject_id, "game-process:77:unknown")

    def test_duplicate_target_pid_is_rejected(self):
        target = ProcessTarget(77, r"C:\Game\game.exe")
        with self.assertRaises(ValueError):
            SensorContext("session", (target, target), observed_at=1.0)

    def test_target_mapping_is_detached(self):
        target = ProcessTarget(77, r"C:\Game\game.exe")
        context = SensorContext("session", (target,), observed_at=1.0)
        mapping = context.target_by_pid
        mapping.clear()
        self.assertEqual(context.target_by_pid[77], target)


if __name__ == "__main__":
    unittest.main()
