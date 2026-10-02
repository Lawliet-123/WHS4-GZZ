import tempfile
import unittest
from pathlib import Path

from anti_esp.sensors.file_identity import FileIdentityEnricher
from anti_esp.sensors.signature import SignatureCheck


class FakeSignatures:
    def verify(self, path):
        return SignatureCheck(
            path=str(path),
            status="unsigned",
            checked_at=5.0,
            backend="fake",
            native_code=0x800B0100,
        )


class FileIdentityTests(unittest.TestCase):
    def test_hash_and_signature_are_factual_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "module.dll"
            path.write_bytes(b"module")
            result = FileIdentityEnricher(signatures=FakeSignatures()).inspect(str(path))
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(result["signature_status"], "unsigned")
        self.assertNotIn("suspicious", result)
        self.assertNotIn("score", result)


if __name__ == "__main__":
    unittest.main()
