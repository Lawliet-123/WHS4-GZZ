from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


PROFILE = Path(__file__).resolve().parents[1] / "sysmon-realgame-only.xml"
DISABLED_TAGS = {
    "ProcessCreate", "FileCreateTime", "NetworkConnect", "ProcessTerminate",
    "DriverLoad", "ImageLoad", "CreateRemoteThread", "RawAccessRead", "FileCreate",
    "RegistryEvent", "FileCreateStreamHash", "PipeEvent", "WmiEvent", "DnsQuery",
    "FileDelete", "ClipboardChange", "ProcessTampering", "FileDeleteDetected",
    "FileBlockExecutable", "FileBlockShredding", "FileExecutableDetected",
}


class RealGameSysmonProfileTests(unittest.TestCase):
    """Static policy checks only; never install or execute Sysmon."""

    def setUp(self):
        self.root = ET.parse(PROFILE).getroot()

    def test_process_access_is_restricted_to_game_target(self):
        access = self.root.findall(".//ProcessAccess")
        self.assertEqual(len(access), 1)
        self.assertEqual(access[0].attrib, {"onmatch": "include"})
        self.assertEqual(len(access[0]), 1)
        target = access[0][0]
        self.assertEqual(target.tag, "TargetImage")
        self.assertEqual(target.attrib, {"condition": "end with"})
        self.assertEqual(target.text, r"\PenguinHotel-Win64-Shipping.exe")

    def test_unrelated_logging_and_blocking_have_empty_include_rules(self):
        filters = self.root.find("EventFiltering")
        rules = [node for node in filters.iter() if "onmatch" in node.attrib]
        self.assertEqual({node.tag for node in rules}, DISABLED_TAGS | {"ProcessAccess"})
        self.assertEqual(len(rules), len(DISABLED_TAGS) + 1)
        for node in rules:
            if node.tag == "ProcessAccess":
                continue
            with self.subTest(tag=node.tag):
                self.assertEqual(node.attrib, {"onmatch": "include"})
                self.assertEqual(len(node), 0)
                self.assertFalse((node.text or "").strip())

    def test_profile_does_not_add_global_capture_or_change_driver_identity(self):
        self.assertEqual(self.root.tag, "Sysmon")
        self.assertEqual(self.root.attrib, {"schemaversion": "4.91"})
        self.assertEqual([node.tag for node in self.root], ["HashAlgorithms", "EventFiltering"])
        self.assertEqual(self.root.findtext("HashAlgorithms"), "SHA256")


if __name__ == "__main__":
    unittest.main()
