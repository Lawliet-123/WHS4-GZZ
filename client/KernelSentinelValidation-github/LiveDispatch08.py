"""Live case 08: collect real IOCTL evidence around a manual WinDbg pointer edit.

This program never writes kernel memory. Keep its exclusive device handle open
through all three phases. WinDbg commands are bound to this boot's symbol values.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import ntpath
from pathlib import Path
import sys
import time

REASON = 'sentinel_dispatch_pointer_changed_since_driver_load'
CASE = '08.live_dispatch_mutation'


def address(value):
    result = int(value.replace('`', ''), 16)
    if not 0xffff800000000000 <= result <= 0xffffffffffffffff:
        raise argparse.ArgumentTypeError('Expected an x64 kernel address from this boot')
    return result


def sentinel(event):
    if event['module_status'] != 0:
        raise ValueError('Kernel module list query failed')
    matches = [m for m in event['modules']
               if ntpath.basename(m['path']).lower() == 'kernelsentinel.sys']
    if len(matches) != 1:
        raise ValueError('Expected exactly one KernelSentinel.sys module')
    return matches[0]


def validate_before(event, original, replacement, analyze):
    module = sentinel(event)
    if original == replacement:
        raise ValueError('Original and replacement must differ')
    if not all(module['base'] <= p < module['base'] + module['size']
               for p in (original, replacement)):
        raise ValueError('Addresses do not belong to this loaded KernelSentinel')
    if len(event['dispatch_pointers']) != 3 or event['dispatch'] != 0:
        raise ValueError('Dispatch baseline is not clean')
    if any(p != {'baseline': original, 'current': original}
           for p in event['dispatch_pointers']):
        raise ValueError('This build does not have the expected three KsDispatch pointers')
    if REASON in dict(analyze(event, {})):
        raise ValueError('Unexpected dispatch finding before mutation')


def validate_phase(event, before, replacement, mutated, analyze):
    first, now = sentinel(before), sentinel(event)
    if (first['base'], first['size']) != (now['base'], now['size']):
        raise ValueError('Driver identity changed during test')
    wanted = [dict(p) for p in before['dispatch_pointers']]
    if mutated:
        wanted[0]['current'] = replacement
    if event['dispatch_pointers'] != wanted:
        raise ValueError('Actual pointer values/baselines do not match this phase')
    if event['dispatch'] != (1 if mutated else 0):
        raise ValueError('Expected only CREATE change bit, or zero after restore')
    if (REASON in dict(analyze(event, {}))) != mutated:
        raise ValueError('Production analysis finding does not match this phase')


def debugger_command(original, replacement, restore=False):
    # Resolve the slot from PDB types, never from a guessed DRIVER_OBJECT offset.
    source, target = (replacement, original) if restore else (original, replacement)
    return (
        f'.if ((KernelSentinel!KsDispatch == {original:#x}) and '
        f'(KernelSentinel!KsOpenClose == {replacement:#x})) {{ '
        'r @$t8 = @@c++(&((KernelSentinel!g_Device)->DriverObject->MajorFunction[0])); '
        f'.if (poi(@$t8) == {source:#x}) {{ eq @$t8 {target:#x}; dq @$t8 L1 }} '
        '.else { .echo ABORT_UNEXPECTED_SLOT_VALUE } '
        '} .else { .echo ABORT_SYMBOLS_OR_BOOT_CHANGED }'
    )


class Evidence:
    def __init__(self, directory, original, replacement):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.data = dict(mode='live-dispatch-08', case=CASE, result='INCONCLUSIVE',
                         detail='Not started', restoration_confirmed=False,
                         original=original, replacement=replacement, results=[],
                         scope='Actual installed KernelSentinel CREATE dispatch pointer; '
                               'does not cover outside-module provenance or cases 06/07/09/10')
        self.save()

    def save(self):
        pending = self.directory / 'report.json.tmp'
        pending.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        pending.replace(self.directory / 'report.json')
        (self.directory / 'report.md').write_text(
            '# KernelSentinel live case 08\n\n'
            f"[{self.data['result']}] {CASE}: {self.data['detail']}\n\n"
            f"Restoration confirmed: {self.data['restoration_confirmed']}\n\n"
            f"Scope: {self.data['scope']}\n", encoding='utf-8')

    def state(self, phase, detail):
        self.data.update(phase=phase, detail=detail)
        self.save()
        print(f'[{phase}] {detail}', flush=True)

    def sample(self, phase, event, analyze):
        with (self.directory / 'raw_events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(phase=phase, time_utc=datetime.now(timezone.utc).isoformat(),
                                   event=event, findings=analyze(event, {}))) + '\n')


def run(args):
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'KernelSentinel'))
    from agent.windows import Windows, Driver
    from agent.analysis import findings
    from validation.debug_bridge import Bridge
    bridge = Bridge(args.bridge) if args.bridge else None

    destination = Path(args.out) if args.out else root / 'runs' / (
        'live08_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    evidence = Evidence(destination.resolve(), args.original, args.replacement)
    for name in ('windows.py', 'protocol.py', 'analysis.py'):
        file = root / 'KernelSentinel' / 'agent' / name
        evidence.data.setdefault('agent_sha256', {})[name] = hashlib.sha256(file.read_bytes()).hexdigest()
    recovery = '.expr /s masm\n' + debugger_command(args.original, args.replacement, True) + '\n'
    (evidence.directory / 'RESTORE_WINDBG.txt').write_text(recovery, encoding='utf-8')
    driver = None
    armed = False
    try:
        driver = Driver(Windows())
        status = driver.status()
        if status['pid'] or status['lease_active']:
            raise ValueError('Stop the existing watch/validation session before this test')
        before = driver.diagnostics()
        evidence.sample('before', before, findings)
        validate_before(before, args.original, args.replacement, findings)
        evidence.state('BASELINE_OK', 'Same device handle stays open until restoration is checked.')
        armed = True
        evidence.state('WAIT_MUTATION', 'No PASS yet. Restore with RESTORE_WINDBG.txt if interrupted.')
        if bridge:
            evidence.data['bridge_mutation'] = bridge.request('DISPATCH_MUTATE')
        else:
            print('\nHOST WinDbg: Debug > Break, then paste these two lines:', flush=True)
            print('.expr /s masm\n' + debugger_command(args.original, args.replacement), flush=True)
            print('\nIf ABORT or another error appears, do not improvise an address.\n'
                  'After checking the displayed pointer, enter g in WinDbg.\n'
                  'Do not unload/restart KernelSentinel or close this VM terminal during the test.', flush=True)
            input('VM: after WinDbg g, press Enter here to read the actual changed pointer: ')
        mutation_error = None
        try:
            for _ in range(3):
                event = driver.diagnostics()
                evidence.sample('mutated', event, findings)
                validate_phase(event, before, args.replacement, True, findings)
                time.sleep(0.15)
            evidence.state('CHANGE_OBSERVED', 'Three real IOCTL samples: CREATE changed, analysis finding present.')
        except Exception as exc:
            mutation_error = str(exc)
            evidence.state('CHANGE_NOT_VERIFIED', mutation_error)

        if bridge:
            evidence.data['bridge_restoration'] = bridge.request('DISPATCH_RESTORE')
        else:
            print('\nHOST WinDbg: Debug > Break, then restore with:', flush=True)
            print(recovery, flush=True)
            print('Then enter g in WinDbg. If the slot is already original, the guard will refuse another write.', flush=True)
            input('VM: after restoration and WinDbg g, press Enter here: ')
        for _ in range(3):
            event = driver.diagnostics()
            evidence.sample('restored', event, findings)
            validate_phase(event, before, args.replacement, False, findings)
            time.sleep(0.15)
        evidence.data['restoration_confirmed'] = True
        armed = False
        if mutation_error:
            raise ValueError('Restored, but mutation evidence failed: ' + mutation_error)
        evidence.data.update(result='PASS', detail='Actual CREATE pointer changed; mask=1 and finding observed '
                             'in 3 samples; exact original pointers, mask=0 and finding cleared in 3 restored samples.')
    except (Exception, KeyboardInterrupt) as exc:
        evidence.data.update(result='ERROR' if isinstance(exc, (OSError, KeyboardInterrupt)) else 'FAIL',
                             detail=f'{type(exc).__name__}: {exc}')
        if armed and bridge:
            try:
                evidence.data['bridge_restoration'] = bridge.request('DISPATCH_RESTORE')
                evidence.data['restoration_confirmed'] = True
                armed = False
            except Exception as restore_exc:
                evidence.data['detail'] += f'; automatic restoration failed: {restore_exc}'
        if armed:
            print('\nRESTORATION NOT CONFIRMED. In host WinDbg, Break and use:\n' + recovery,
                  flush=True)
            print('Do not unload the driver while its pointer is changed.', flush=True)
    finally:
        if driver:
            try:
                driver.close()
            except Exception as exc:
                evidence.data.update(result='ERROR', detail=f'Device close failed: {exc}')
        evidence.data['results'] = [dict(case=CASE, result=evidence.data['result'],
                                         detail=evidence.data['detail'], layer='live')]
        evidence.save()
        print(f"[{evidence.data['result']}] {CASE}: {evidence.data['detail']}", flush=True)
        print('Reports:', evidence.directory, flush=True)
    return 0 if evidence.data['result'] == 'PASS' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', required=True, type=address, help='Current boot: x KernelSentinel!KsDispatch')
    parser.add_argument('--replacement', required=True, type=address, help='Current boot: x KernelSentinel!KsOpenClose')
    parser.add_argument('--bridge', help='Host debugger bridge configuration; runs mutation/restoration automatically')
    parser.add_argument('--out')
    args = parser.parse_args()
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
