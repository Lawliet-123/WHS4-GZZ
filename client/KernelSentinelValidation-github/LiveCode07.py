"""Live case 07: verify installed KernelSentinel code hash on a real kernel module.

Uses the reviewed equivalent XOR encoding from LiveEntry06 as the one-byte
stimulus. KernelSentinel's production code_scan() does all hashing and caching.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import ntpath
from pathlib import Path
import sys

from LiveEntry06 import address, debugger_command, validate_before, validate_phase

CASE = '07.live_code_mutation'
FINDING = 'kernel_executable_sections_changed_since_first_scan'


def target_module(diagnostics, target):
    modules = [m for m in diagnostics['modules']
               if m['base'] <= target < m['base'] + m['size'] and
               ntpath.basename(m['path']).lower() in ('ntoskrnl.exe', 'ntkrnlmp.exe')]
    if len(modules) != 1:
        raise ValueError('Target function is not inside exactly one NT kernel module')
    return modules[0]


def same_module(result, module):
    return (result['base'] == module['base'] and result['size'] == module['size'] and
            ntpath.basename(result['path']).lower() == ntpath.basename(module['path']).lower())


def validate_scan(result, module, baseline_hash=None, mutated=False, analyze=None):
    if not same_module(result, module) or result['status'] != 0 or result['bytes_hashed'] <= 0:
        raise ValueError('NT module identity, scan status, or hashed byte count failed')
    if result['flags'] not in (1, 3, 4):
        raise ValueError('Unexpected code cache flags')
    if len(bytes.fromhex(result['baseline'])) != 32 or len(bytes.fromhex(result['current'])) != 32:
        raise ValueError('Unexpected SHA-256 hash length')
    if baseline_hash is None:
        if result['flags'] not in (1, 4) or result['baseline'] != result['current']:
            raise ValueError('NT code baseline is already changed or incomplete')
    else:
        if result['baseline'] != baseline_hash:
            raise ValueError('Stored NT baseline changed during test')
        if mutated and (result['flags'] != 3 or result['current'] == baseline_hash):
            raise ValueError('Actual executable code hash change not observed')
        if not mutated and (result['flags'] != 1 or result['current'] != baseline_hash):
            raise ValueError('NT code hash did not return to original baseline')
        if analyze is not None and (FINDING in dict(analyze(result, {}))) != mutated:
            raise ValueError('Production code-integrity finding did not match phase')


class Evidence:
    def __init__(self, folder, address):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=False)
        self.data = dict(mode='live-code-07', case=CASE, result='INCONCLUSIVE',
                         phase='starting', detail='Not started', restoration_confirmed=False,
                         function_address=address, scan_counts={}, results=[],
                         scope='Actual NT executable-section hash change after first baseline; '
                               'one reviewed equivalent encoding, not all kernel modules')
        self.save()

    def save(self):
        temporary = self.folder / 'report.json.tmp'
        temporary.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        temporary.replace(self.folder / 'report.json')
        (self.folder / 'report.md').write_text(
            f"# Live case 07\n\n[{self.data['result']}] {CASE}: {self.data['detail']}\n\n"
            f"Restoration confirmed: {self.data['restoration_confirmed']}\n\n"
            f"Scope: {self.data['scope']}\n", encoding='utf-8')

    def record(self, phase, event, analyze):
        with (self.folder / 'raw_events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(phase=phase, time_utc=datetime.now(timezone.utc).isoformat(),
                                         event=event, findings=analyze(event, {}))) + '\n')

    def state(self, phase, detail):
        self.data.update(phase=phase, detail=detail)
        self.save()
        print(f'[{phase}] {detail}', flush=True)


def scan_until_nt(driver, evidence, module, phase, analyze, max_scans, wanted_flags):
    counts = evidence.data['scan_counts']
    for attempt in range(1, max_scans + 1):
        result = driver.code_scan()
        evidence.record(phase, result, analyze)
        if same_module(result, module):
            counts[phase] = attempt
            evidence.save()
            if result['status']:
                raise ValueError(f'NT code scan failed: status={result["status"]:#x}')
            if result['flags'] not in wanted_flags:
                raise ValueError(f'NT code scan flags={result["flags"]:#x}; expected {wanted_flags}')
            return result
        if attempt % 64 == 0:
            print(f'{phase}: scanned {attempt} modules; waiting for the same NT module', flush=True)
    raise TimeoutError(f'{phase}: NT module not selected within {max_scans} scans')


def run(args):
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'KernelSentinel'))
    from agent.windows import Windows, Driver
    from agent.analysis import findings
    from validation.debug_bridge import Bridge
    bridge = Bridge(args.bridge) if args.bridge else None
    folder = Path(args.out) if args.out else root / 'runs' / ('live07_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    evidence = Evidence(folder.resolve(), args.function_address)
    driver, armed, recovery = None, False, ''
    try:
        for name in ('windows.py', 'protocol.py', 'analysis.py'):
            file = root / 'KernelSentinel' / 'agent' / name
            evidence.data.setdefault('agent_sha256', {})[name] = hashlib.sha256(file.read_bytes()).hexdigest()
        try:
            driver = Driver(Windows())
        except FileNotFoundError as exc:
            if getattr(exc, 'winerror', None) != 2:
                raise
            raise RuntimeError('KernelSentinel device unavailable; in VM check sc.exe query KernelSentinel') from exc
        status = driver.status()
        if status['pid'] or status['lease_active']:
            raise ValueError('Stop watch/Run.ps1; leave KernelSentinel loaded')
        before = driver.diagnostics()
        evidence.record('before_diagnostics', before, findings)
        validate_before(before, args.function_address, findings)
        module = target_module(before, args.function_address)
        original = bytes.fromhex(before['probes'][1]['current'])
        evidence.data.update(module=module, original_bytes=original.hex())
        # A module can have an existing baseline from a previous run. In either
        # case get a real flags=1 comparison before modifying its executable page.
        first = scan_until_nt(driver, evidence, module, 'baseline_or_comparison', findings,
                              args.max_scans, (1, 4))
        validate_scan(first, module)
        if first['flags'] == 4:
            first = scan_until_nt(driver, evidence, module, 'baseline_comparison', findings,
                                  args.max_scans, (1,))
            validate_scan(first, module)
        baseline_hash = first['baseline']
        evidence.data['baseline_hash'] = baseline_hash
        evidence.state('BASELINE_OK', 'Real NT executable hash comparison flags=1; unchanged baseline.')
        recovery = '.expr /s masm\n' + debugger_command(args.function_address, original, True) + '\n'
        mutation = '.expr /s masm\n' + debugger_command(args.function_address, original) + '\n'
        (evidence.folder / 'RESTORE_WINDBG.txt').write_text(recovery, encoding='utf-8')
        (evidence.folder / 'MUTATE_WINDBG.txt').write_text(mutation, encoding='utf-8')
        armed = True
        evidence.state('WAIT_MUTATION', 'Change one byte in host WinDbg, then promptly collect the real hash.')
        if bridge:
            evidence.data['bridge_mutation'] = bridge.request('ENTRY_MUTATE')
        else:
            print('\nHOST WinDbg Debug > Break; paste:\n' + mutation, flush=True)
            print('Require WRITE_VERIFIED and 45 31 d2 / xor r10d,r10d; then WinDbg g.', flush=True)
            input('VM: after WinDbg g, press Enter to scan for changed NT code: ')
        mutation_error = None
        try:
            diag = driver.diagnostics()
            evidence.record('mutated_diagnostics', diag, findings)
            validate_phase(diag, before, True, findings)
            changed = scan_until_nt(driver, evidence, module, 'mutated_scan', findings,
                                    args.max_scans, (3,))
            validate_scan(changed, module, baseline_hash, True, findings)
            evidence.data['changed_hash'] = changed['current']
            evidence.state('CHANGE_OBSERVED', 'Real NT code hash differs; flags=3 and production finding observed.')
        except Exception as exc:
            mutation_error = str(exc)
            evidence.state('CHANGE_NOT_VERIFIED', mutation_error)
        if bridge:
            evidence.data['bridge_restoration'] = bridge.request('ENTRY_RESTORE')
        else:
            print('\nRESTORE NOW: HOST WinDbg Debug > Break; paste:\n' + recovery, flush=True)
            print('Require WRITE_VERIFIED (or ALREADY_RESTORED), original 45 33 d2; then WinDbg g.', flush=True)
            input('VM: after restoring and WinDbg g, press Enter to confirm the original hash: ')
        restored_diag = driver.diagnostics()
        evidence.record('restored_diagnostics', restored_diag, findings)
        validate_phase(restored_diag, before, False, findings)
        restored = scan_until_nt(driver, evidence, module, 'restored_scan', findings,
                                 args.max_scans, (1,))
        validate_scan(restored, module, baseline_hash, False, findings)
        evidence.data['restoration_confirmed'] = True
        armed = False
        if mutation_error:
            raise ValueError('Restored, but mutation evidence failed: ' + mutation_error)
        evidence.data.update(result='PASS', phase='complete', detail='Actual NT executable code hash '
            'changed after one-byte equivalent XOR encoding (flags=3 and production finding); '
            'original 32-byte entry and baseline hash restored (flags=1).')
    except (Exception, KeyboardInterrupt) as exc:
        evidence.data.update(result='ERROR' if isinstance(exc, (OSError, KeyboardInterrupt)) else 'FAIL',
                             phase='interrupted' if armed else 'failed', detail=f'{type(exc).__name__}: {exc}')
        if armed and bridge:
            try:
                evidence.data['bridge_restoration'] = bridge.request('ENTRY_RESTORE')
                evidence.data['restoration_confirmed'] = True
                armed = False
            except Exception as restore_exc:
                evidence.data['detail'] += f'; automatic restoration failed: {restore_exc}'
        if armed:
            print('\nRESTORATION NOT CONFIRMED. Host WinDbg Break; use:\n' + recovery, flush=True)
    finally:
        if driver:
            try:
                driver.close()
            except Exception as exc:
                evidence.data.update(result='ERROR', detail=f'Device close failed: {exc}')
        evidence.data['results'] = [dict(case=CASE, result=evidence.data['result'], detail=evidence.data['detail'], layer='live')]
        evidence.save()
        print(f"[{evidence.data['result']}] {CASE}: {evidence.data['detail']}", flush=True)
        print('Reports:', evidence.folder, flush=True)
    return 0 if evidence.data['result'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--function-address', type=address, required=True)
    parser.add_argument('--bridge', help='Host debugger bridge configuration; runs mutation/restoration automatically')
    parser.add_argument('--max-scans', type=int, default=768)
    parser.add_argument('--out')
    args = parser.parse_args()
    if not 1 <= args.max_scans <= 4096:
        parser.error('--max-scans must be 1..4096')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
