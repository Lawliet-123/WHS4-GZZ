"""Run original Python cases and their C cases with explicitly built x64 binaries."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'KernelSentinel'))
sys.path.insert(0, str(ROOT))

# These three original wrappers invoke whichever `gcc` happens to be on PATH.
# Their four unmodified C files are instead built by Build.ps1 with MSVC x64,
# and executed below. No C coverage is silently skipped.
REPLACED = {
    'test_sentinel.IntegrationTests.test_c_policy_and_abi',
    'test_sensors.NativeTests.test_native_layout_bounds_and_cache_regression',
    'test_sensors.NativeTests.test_kernel_collector_with_api_doubles',
}


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    original = list(flatten(unittest.defaultTestLoader.discover(str(ROOT / 'KernelSentinel' / 'tests'))))
    extra = list(flatten(unittest.TestLoader().discover(str(ROOT / 'validation' / 'tests'))))
    log = io.StringIO()
    selected = [t for t in original if t.id() not in REPLACED] + extra
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.TestSuite(selected))
    (out / 'python-tests.txt').write_text(log.getvalue(), encoding='utf-8')
    native = []
    for name in ('policy_test', 'cache_test', 'thread_layout_test', 'thread_scan_test', 'native_sensor_test'):
        executable = ROOT / 'bin' / (name + '.exe')
        try:
            run = subprocess.run([str(executable)], capture_output=True, text=True, errors='replace', timeout=30)
            native.append(dict(name=name, result='PASS' if run.returncode == 0 else 'FAIL',
                               exit_code=run.returncode, stdout=run.stdout, stderr=run.stderr))
        except (OSError, subprocess.TimeoutExpired) as exc:
            native.append(dict(name=name, result='ERROR', error=str(exc)))
    summary = dict(layer='offline algorithms with OS API doubles; not live kernel validation',
                   python_cases=result.testsRun, original_python_cases=len(original) - len(REPLACED),
                   additional_python_cases=len(extra), failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), replaced_compiler_wrappers=sorted(REPLACED), native=native,
                   passed=result.wasSuccessful() and not result.skipped and all(t['result'] == 'PASS' for t in native))
    (out / 'report.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    if not result.wasSuccessful(): print(log.getvalue())
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
