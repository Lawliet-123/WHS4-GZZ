import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from agent.startup import StartupDiagnostics, StartupError
from agent.analysis import findings


class StartupTests(unittest.TestCase):
    def test_failures_identify_stage_and_preserve_winerror(self):
        for stage in ('config_read','target_process_query','driver_device_open','driver_status_query'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                output=Path(tmp)/'run';diag=StartupDiagnostics(output)
                error=OSError('synthetic missing file');error.winerror=2
                def fail():raise error
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaisesRegex(StartupError,stage):diag.call(stage,fail)
                rows=[json.loads(s) for s in diag.path.read_text().splitlines()]
                self.assertEqual(rows[-1]['stage'],stage)
                self.assertEqual(rows[-1]['winerror'],2)
                self.assertFalse(output.exists())

    def test_selfdefense_query_rights_are_not_sensitive_findings(self):
        for rights in (0x1000|0x100000, 0x0400|0x100000):
            event=dict(type='handle_post',flags=0,status=0,before_access=rights,granted_access=rights)
            self.assertEqual(findings(event,{}),[])

    def test_vm_read_write_positive_and_failed_request_are_distinct(self):
        event=dict(type='handle_post',flags=0,status=0,before_access=0x28,granted_access=0x28)
        self.assertEqual(findings(event,{}),[('sensitive_handle_rights_granted',1)])
        event['status']=0xc0000022
        self.assertEqual(findings(event,{}),[('sensitive_handle_request_failed',0)])
