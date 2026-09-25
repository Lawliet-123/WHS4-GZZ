import unittest
from pathlib import Path

from client.LocalGuard.external_access.common.models import ArtifactInfo
from client.LocalGuard.external_access.process_access.access_rights import (
    PROCESS_VM_OPERATION,
    PROCESS_VM_READ,
    PROCESS_VM_WRITE,
    describe_access_mask,
)
from client.LocalGuard.external_access.process_access.detector import ProcessAccessDetector
from client.LocalGuard.external_access.process_access.models import (
    ExternalHandleObservation,
    ScanContext,
)


class ProcessAccessDetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = ProcessAccessDetector()
        self.context = ScanContext("round_12", "player_042", 1500)

    def test_vm_write_and_unsigned_artifact_produces_explainable_result(self):
        observation = ExternalHandleObservation(
            source_pid=1234,
            source_name="unknown.exe",
            source_path=Path("C:/temp/unknown.exe"),
            granted_access=PROCESS_VM_WRITE,
            artifact=ArtifactInfo(
                path=Path("C:/temp/unknown.exe"),
                sha256="abc123",
                signature_status="unsigned",
                publisher=None,
            ),
        )

        result = self.detector.evaluate(observation, self.context)

        self.assertEqual(result["module"], "localguard")
        self.assertEqual(result["raw_score"], 3)  # VM_WRITE 2 + unsigned 1
        self.assertEqual(result["evidence"]["submodule"], "external_process")
        self.assertEqual(result["evidence"]["access_rights"], ["PROCESS_VM_WRITE"])
        self.assertIn("External process opened PROCESS_VM_WRITE handle", result["reasons"])
        self.assertIn("Process executable signature is unsigned", result["reasons"])

    def test_vm_read_only_is_recorded_by_sensor_but_not_scored_as_alert(self):
        observation = ExternalHandleObservation(
            source_pid=1234,
            source_name="overlay.exe",
            source_path=None,
            granted_access=PROCESS_VM_READ,
        )
        self.assertIsNone(self.detector.evaluate(observation, self.context))

    def test_multiple_risky_rights_preserve_all_evidence(self):
        rights = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.assertEqual(
            describe_access_mask(rights),
            ["PROCESS_VM_READ", "PROCESS_VM_WRITE", "PROCESS_VM_OPERATION"],
        )
        observation = ExternalHandleObservation(
            source_pid=777,
            source_name="python.exe",
            source_path=Path("C:/Python/python.exe"),
            granted_access=rights,
            artifact=ArtifactInfo(
                path=Path("C:/Python/python.exe"),
                sha256="def456",
                signature_status="trusted",
                publisher="CN=Python Software Foundation",
            ),
        )

        result = self.detector.evaluate(observation, self.context)

        self.assertEqual(result["raw_score"], 4)  # VM_WRITE 2 + VM_OPERATION 2
        self.assertEqual(
            result["evidence"]["access_rights"],
            ["PROCESS_VM_READ", "PROCESS_VM_WRITE", "PROCESS_VM_OPERATION"],
        )
        self.assertEqual(result["evidence"]["signature_status"], "trusted")

    def test_unknown_signature_is_weak_supporting_evidence(self):
        observation = ExternalHandleObservation(
            source_pid=888,
            source_name="tool.exe",
            source_path=Path("C:/Program Files/Tool/tool.exe"),
            granted_access=PROCESS_VM_WRITE,
            artifact=ArtifactInfo(
                path=Path("C:/Program Files/Tool/tool.exe"),
                sha256="123456",
                signature_status="unknown",
                publisher=None,
            ),
        )

        result = self.detector.evaluate(observation, self.context)

        self.assertEqual(result["raw_score"], 3)  # VM_WRITE 2 + unknown signature 1
        self.assertIn("Process executable signature is unknown", result["reasons"])

    def test_user_writable_path_is_weak_supporting_evidence(self):
        detector = ProcessAccessDetector(
            environment={
                "USERPROFILE": "C:/Users/tester",
                "LOCALAPPDATA": "C:/Users/tester/AppData/Local",
            }
        )
        observation = ExternalHandleObservation(
            source_pid=999,
            source_name="python.exe",
            source_path=Path("C:/Users/tester/AppData/Local/Programs/Python/python.exe"),
            granted_access=PROCESS_VM_WRITE,
            artifact=ArtifactInfo(
                path=Path("C:/Users/tester/AppData/Local/Programs/Python/python.exe"),
                sha256="abcdef",
                signature_status="trusted",
                publisher="CN=Python Software Foundation",
            ),
        )

        result = detector.evaluate(observation, self.context)

        self.assertEqual(result["raw_score"], 3)  # VM_WRITE 2 + user-writable path 1
        self.assertEqual(result["evidence"]["path_risk"], "user_writable_location")
        self.assertIn("Process executable is located in a user-writable directory", result["reasons"])


if __name__ == "__main__":
    unittest.main()
