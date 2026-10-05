"""Smoke helper handshake regression, including the Windows venv redirector."""

import os
from pathlib import Path
import struct
import tempfile
import unittest

from ..smoke_test import _ready_pid, run_smoke_test


class SmokeTestTests(unittest.TestCase):
    def test_ready_reply_uses_the_interpreter_reported_pid(self):
        self.assertEqual(_ready_pid("READY\t1234"), 1234)

    def test_invalid_ready_replies_fail_closed(self):
        for reply in ("READY", "LOADED", "READY\t0", "READY\t-1",
                      "READY\t１２３", "READY\t4294967296", "READY\t12\t34"):
            with self.subTest(reply=reply), self.assertRaises(RuntimeError):
                _ready_pid(reply)

    @unittest.skipUnless(os.name == "nt" and struct.calcsize("P") == 8,
                         "real Toolhelp smoke requires 64-bit Windows")
    def test_system_dll_load_is_seen_in_the_actual_helper_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path, event = run_smoke_test(Path(directory) / "smoke.jsonl")
            self.assertTrue(path.is_file())
            self.assertEqual(event["module"], "external_access")
            self.assertEqual(event["evidence"]["submodule"], "module_integrity")
            self.assertEqual(event["evidence"]["change_type"], "added")


if __name__ == "__main__":
    unittest.main()
