from pathlib import Path
import types
import unittest
from unittest.mock import patch
from agent.driver_setup import prepare_test_environment


class TestSetupTests(unittest.TestCase):
    def test_reboot_required_stops_install_path(self):
        with patch('agent.driver_setup.test_signing_active',return_value=False), \
             patch('agent.driver_setup.subprocess.run',return_value=types.SimpleNamespace(returncode=3010)) as run:
            with self.assertRaisesRegex(RuntimeError,'REBOOT_REQUIRED'):
                prepare_test_environment(Path('test.sys'),'a'*64)
        argv=run.call_args.args[0]
        self.assertEqual(argv[argv.index('-EffectiveTestSigning')+1],'false')

    def test_secure_boot_rejection_is_not_bypassed(self):
        with patch('agent.driver_setup.test_signing_active',return_value=False), \
             patch('agent.driver_setup.subprocess.run',return_value=types.SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError,'not installed'):
                prepare_test_environment(Path('test.sys'),'a'*64)

    def test_existing_test_mode_continues(self):
        with patch('agent.driver_setup.test_signing_active',return_value=True), \
             patch('agent.driver_setup.subprocess.run',return_value=types.SimpleNamespace(returncode=0)) as run:
            prepare_test_environment(Path('test.sys'),'a'*64)
        argv=run.call_args.args[0]
        self.assertEqual(argv[argv.index('-EffectiveTestSigning')+1],'true')
