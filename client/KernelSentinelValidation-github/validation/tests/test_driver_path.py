from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from validation.probe_service import ProbeService, driver_image_path


class DriverPathTests(unittest.TestCase):
    def test_plain_space_unicode_and_native_paths(self):
        for value in (r'C:\Users\Example\Desktop\KernelSentinelValidation\bin\driver\KsValidationProbe.sys',
                      r'C:\Test Folder\드라이버\KsValidationProbe.sys'):
            self.assertEqual(driver_image_path(value), '\\??\\'+value)
            self.assertEqual(driver_image_path('\\??\\'+value), '\\??\\'+value)
            self.assertNotIn('"', driver_image_path(value))

    def test_quotes_relative_and_unc_rejected(self):
        for value in ('"C:\\fixture.sys"', r'C:fixture.sys', r'fixture.sys', r'\\server\share\fixture.sys'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                driver_image_path(value)

    def test_subprocess_argument_transport_with_spaces(self):
        # CommandLineToArgvW round-trip only; no service is created.
        import ctypes as C
        from ctypes import wintypes as W
        argv = ['sc.exe','create','KsValidationProbe','binPath=',driver_image_path(r'C:\Test Folder\KsValidationProbe.sys')]
        parse = C.WinDLL('shell32').CommandLineToArgvW
        parse.argtypes = [W.LPCWSTR, C.POINTER(C.c_int)]; parse.restype = C.POINTER(W.LPWSTR)
        free = C.WinDLL('kernel32').LocalFree
        free.argtypes = [C.c_void_p]; free.restype = C.c_void_p
        count = C.c_int(); values = parse(subprocess.list2cmdline(argv), C.byref(count))
        try:
            self.assertTrue(values)
            self.assertEqual([values[i] for i in range(count.value)], argv)
        finally:
            if values: free(values)

    def test_create_uses_native_path_without_literal_quotes(self):
        with tempfile.TemporaryDirectory(prefix='driver path ') as folder:
            path = Path(folder)/'KsValidationProbe.sys'; path.write_bytes(b'test')
            svc = ProbeService(path, Mock(), Mock())
            svc.verify_registered_path = Mock()
            svc.command = Mock(side_effect=[SimpleNamespace(returncode=n) for n in (1060,0,577)])
            with self.assertRaisesRegex(RuntimeError, '577'):
                svc.start()
            expected = driver_image_path(path)
            self.assertEqual(svc.command.call_args_list[1].args[-1], expected)
            svc.verify_registered_path.assert_called_once_with(expected)
            self.assertTrue(svc.created)

    def test_registry_quotes_rejected_before_start(self):
        import winreg
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'KsValidationProbe.sys'; path.write_bytes(b'test')
            svc = ProbeService(path, Mock(), Mock())
            svc.command = Mock(side_effect=[SimpleNamespace(returncode=n) for n in (1060,0)])
            with patch.object(winreg,'OpenKey'), patch.object(winreg,'QueryValueEx',return_value=('"'+str(path)+'"',winreg.REG_EXPAND_SZ)):
                with self.assertRaises(ValueError): svc.start()
            self.assertTrue(svc.created)
            self.assertFalse(any(c.args[0]=='start' for c in svc.command.call_args_list))

    def test_registry_match_is_recorded(self):
        import winreg
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'KsValidationProbe.sys'; path.write_bytes(b'test')
            svc = ProbeService(path, Mock(), Mock()); expected = driver_image_path(path)
            with patch.object(winreg,'OpenKey'), patch.object(winreg,'QueryValueEx',return_value=(expected,winreg.REG_EXPAND_SZ)):
                svc.verify_registered_path(expected)
            self.assertEqual(svc.report.note.call_args.args[0]['actual'], expected)


if __name__ == '__main__':
    unittest.main()
