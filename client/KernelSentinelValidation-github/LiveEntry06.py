"""Case 06, revision 1: real NtQuerySystemInformation entry-byte test.

Only the reviewed 45 33 d2 -> 45 31 d2 encoding is supported, at offset 6.
Both encode xor r10d,r10d. The debugger changes only byte +7 while halted.
This collector issues read-only IOCTLs and never changes kernel memory itself.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

CASE = '06.live_entry_mutation'
REASON = 'kernel_entry_bytes_changed_since_driver_load'
SYMBOL = 'nt!NtQuerySystemInformation'
PREFIX = bytes.fromhex('40534883ec304533d2')
NAMES = ['NtOpenProcess', 'NtQuerySystemInformation', 'MmCopyVirtualMemory', 'PsLookupProcessByProcessId']


def address(value):
    number = int(value.replace('`', ''), 16)
    if not 0xffff800000000000 <= number <= 0xffffffffffffffff:
        raise argparse.ArgumentTypeError('Expected this boot\'s x64 kernel function address')
    return number


def replacement_bytes(original):
    if len(original) != 32 or not original.startswith(PREFIX):
        raise ValueError('Unsupported entry bytes: expected reviewed prologue and xor r10d,r10d at +6')
    result = bytearray(original)
    result[7] = 0x31
    return bytes(result)


def validate_before(event, expected_address, analyze):
    if event['valid'] != 15 or event['failed'] or event['changed'] or event['dispatch']:
        raise ValueError('Baseline is not clean or all four entry probes are not readable')
    if [p['name'] for p in event['probes']] != NAMES:
        raise ValueError('Unexpected probe layout')
    if any(p['read_status'] or len(bytes.fromhex(p['baseline'])) != 32 or
           p['baseline'] != p['current'] for p in event['probes']):
        raise ValueError('Entry baseline/current bytes or read status mismatch')
    if event['probes'][1]['address'] != expected_address:
        raise ValueError('Address differs from the current WinDbg session')
    if event['module_status'] or not any(m['base'] <= expected_address and
            expected_address + 32 <= m['base'] + m['size'] for m in event['modules']):
        raise ValueError('Target address not covered by a successful module snapshot')
    if any(p['baseline'] != p['current'] for p in event['dispatch_pointers']):
        raise ValueError('Restore case 08 before starting case 06')
    replacement_bytes(bytes.fromhex(event['probes'][1]['current']))
    if REASON in dict(analyze(event, {})):
        raise ValueError('Unexpected entry-change finding before mutation')


def validate_phase(event, before, mutated, analyze):
    if event['valid'] != 15 or event['failed'] or event['module_status']:
        raise ValueError('Incomplete diagnostic read during test')
    if event['dispatch'] or event['dispatch_pointers'] != before['dispatch_pointers']:
        raise ValueError('Unrelated dispatch change during entry test')
    expected = copy.deepcopy(before['probes'])
    if mutated:
        expected[1]['current'] = replacement_bytes(bytes.fromhex(expected[1]['current'])).hex()
    # Compare all four addresses, immutable baselines, exact 32-byte reads and statuses.
    if event['probes'] != expected:
        raise ValueError('Exact entry bytes, baseline, addresses or another probe differ from this phase')
    if event['changed'] != (2 if mutated else 0):
        raise ValueError('Expected only NtQuerySystemInformation change bit (2), or zero after restore')
    if (REASON in dict(analyze(event, {}))) != mutated:
        raise ValueError('Production analysis finding does not match the real entry change')


def byte_guard(location, data):
    return ' and '.join(f'(qwo({location:#x}+{i:#x}) == {int.from_bytes(data[i:i+8], "little"):#x})'
                        for i in range(0, 32, 8))


def debugger_command(location, original, restore=False):
    changed = replacement_bytes(original)
    source, target = (changed, original) if restore else (original, changed)
    identity = f'({SYMBOL} == {location:#x}) and (poi(KernelSentinel!g_ProbeAddresses+0x8) == {location:#x})'
    already = (f'.elsif ({byte_guard(location, original)}) {{ .echo ALREADY_RESTORED }} ' if restore else '')
    return (f'.if ({identity}) {{ .if ({byte_guard(location, source)}) {{ '
            f'eb {location+7:#x} {target[7]:02x}; '
            f'.if ({byte_guard(location, target)}) {{ .echo WRITE_VERIFIED }} '
            '.else { .echo WRITE_FAILED_DO_NOT_CONTINUE }; '
            f'db {location+6:#x} L0n3; u {location+6:#x} L0n1 '
            f'}} {already}.else {{ .echo ABORT_ENTRY_BYTES_DIFFER }} '
            '} .else { .echo ABORT_SYMBOL_OR_PROBE_ADDRESS_CHANGED }')


class Evidence:
    def __init__(self, directory, location):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.data = dict(mode='live-entry-06', revision=1, case=CASE, result='INCONCLUSIVE',
                         phase='starting', detail='Not started', restoration_confirmed=False,
                         function=SYMBOL, function_address=location, byte_offset=7,
                         scope='Actual second entry probe, equivalent XOR encoding at +6; '
                               'not all four probes and not live code-hash case 07', results=[])
        self.save()

    def save(self):
        pending = self.directory / 'report.json.tmp'
        pending.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        pending.replace(self.directory / 'report.json')
        (self.directory / 'report.md').write_text(
            f"# Live case 06\n\n[{self.data['result']}] {CASE}: {self.data['detail']}\n\n"
            f"Restoration confirmed: {self.data['restoration_confirmed']}\n\n"
            f"Scope: {self.data['scope']}\n", encoding='utf-8')

    def state(self, phase, detail):
        self.data.update(phase=phase, detail=detail)
        self.save()
        print(f'[{phase}] {detail}', flush=True)

    def sample(self, phase, event, analyze):
        with (self.directory / 'raw_events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(phase=phase, time_utc=datetime.now(timezone.utc).isoformat(),
                                         event=event, findings=analyze(event, {}))) + '\n')


def run(args):
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'KernelSentinel'))
    from agent.windows import Windows, Driver
    from agent.analysis import findings
    from validation.debug_bridge import Bridge
    bridge = Bridge(args.bridge) if args.bridge else None
    output = Path(args.out) if args.out else root / 'runs' / ('live06_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    evidence = Evidence(output.resolve(), args.function_address)
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
            raise RuntimeError('KernelSentinel device \\.\\KernelSentinel is unavailable. '
                               'This demand-start driver may be stopped after VM reboot. '
                               'In the VM run sc.exe query KernelSentinel; if STOPPED, '
                               'run sc.exe start KernelSentinel, then retry.') from exc
        status = driver.status()
        if status['pid'] or status['lease_active']:
            raise ValueError('Stop watch/Run.ps1 before starting; leave the driver loaded')
        before = driver.diagnostics()
        evidence.sample('before', before, findings)
        validate_before(before, args.function_address, findings)
        original = bytes.fromhex(before['probes'][1]['current'])
        evidence.data.update(original_bytes=original.hex(), changed_bytes=replacement_bytes(original).hex())
        recovery = '.expr /s masm\n' + debugger_command(args.function_address, original, True) + '\n'
        mutation = '.expr /s masm\n' + debugger_command(args.function_address, original) + '\n'
        (evidence.directory / 'RESTORE_WINDBG.txt').write_text(recovery, encoding='utf-8')
        (evidence.directory / 'MUTATE_WINDBG.txt').write_text(mutation, encoding='utf-8')
        evidence.state('BASELINE_OK', 'All four probe baselines match. Revision 1 uses MASM and, not &&.')
        armed = True
        evidence.state('WAIT_MUTATION', 'Leave this handle open; finish restoration in the same session.')
        if bridge:
            evidence.data['bridge_mutation'] = bridge.request('ENTRY_MUTATE')
        else:
            print('\nHOST WinDbg: Debug > Break; paste these two lines:\n' + mutation, flush=True)
            print('Require WRITE_VERIFIED and 45 31 d2 / xor r10d,r10d.\n'
                  'If ABORT, WRITE_FAILED or a debugger error appears, stop and report it.\n'
                  'Then enter g in WinDbg and promptly continue below.', flush=True)
            input('VM: after WinDbg g, press Enter here to collect changed bytes: ')
        mutation_error = None
        try:
            for _ in range(3):
                event = driver.diagnostics()
                evidence.sample('mutated', event, findings)
                validate_phase(event, before, True, findings)
                time.sleep(0.1)
            evidence.state('CHANGE_OBSERVED', 'Exact byte +7 changed; mask=2 and production finding in 3 reads.')
        except Exception as exc:
            mutation_error = str(exc)
            evidence.state('CHANGE_NOT_VERIFIED', mutation_error)
        if bridge:
            evidence.data['bridge_restoration'] = bridge.request('ENTRY_RESTORE')
        else:
            print('\nRESTORE NOW: HOST WinDbg Debug > Break; paste:\n' + recovery, flush=True)
            print('Require WRITE_VERIFIED (or ALREADY_RESTORED); original 45 33 d2. Then WinDbg g.', flush=True)
            input('VM: after restoring and WinDbg g, press Enter here: ')
        for _ in range(3):
            event = driver.diagnostics()
            evidence.sample('restored', event, findings)
            validate_phase(event, before, False, findings)
            time.sleep(0.1)
        evidence.data['restoration_confirmed'] = True
        armed = False
        if mutation_error:
            raise ValueError('Restored, but mutation evidence failed: ' + mutation_error)
        evidence.data.update(result='PASS', phase='complete', detail='Actual NtQuerySystemInformation +7 byte '
            '33->31 detected in 3 reads (mask=2); exact original bytes and finding clearance confirmed in 3 restored reads.')
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
        print('Reports:', evidence.directory, flush=True)
    return 0 if evidence.data['result'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--function-address', type=address, required=True)
    parser.add_argument('--bridge', help='Host debugger bridge configuration; runs mutation/restoration automatically')
    parser.add_argument('--out')
    return run(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
