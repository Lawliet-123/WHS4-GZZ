import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot_contract import verify_snapshot


class SnapshotContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "registry.py"
        self.source.write_bytes(b"# deliberately not executed\n")
        self.pin = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.commit = "a" * 40

    def test_matching_reviewed_snapshot(self):
        self.assertEqual(verify_snapshot(self.source, self.pin, self.commit), self.pin)

    def test_modified_source_rejected(self):
        self.source.write_bytes(b"# changed")
        with self.assertRaises(ValueError):
            verify_snapshot(self.source, self.pin, self.commit)

    def test_missing_source_rejected(self):
        with self.assertRaises(OSError):
            verify_snapshot(self.source.with_name("missing.py"), self.pin, self.commit)

    def test_short_hash_rejected(self):
        with self.assertRaises(ValueError):
            verify_snapshot(self.source, self.pin[:7], self.commit)

    def test_malformed_hash_rejected(self):
        with self.assertRaises(ValueError):
            verify_snapshot(self.source, "z" * 64, self.commit)

    def test_short_commit_rejected(self):
        with self.assertRaises(ValueError):
            verify_snapshot(self.source, self.pin, self.commit[:7])

    def test_branch_alias_rejected(self):
        with self.assertRaises(ValueError):
            verify_snapshot(self.source, self.pin, "main")

    def test_wrong_pin_rejected(self):
        with self.assertRaises(ValueError):
            verify_snapshot(self.source, "0" * 64, self.commit)


if __name__ == "__main__":
    unittest.main()
