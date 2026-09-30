"""Case 09: live AuxKlib/PSAPI discrepancy for an actual loaded fixture driver.

The process-local PSAPI response wrapper removes only the owned fixture base.
This proves mismatch reporting, not detection of a driver absent from both APIs.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import ntpath
from pathlib import Path
import sys
import time

CASE = '09.live_cross_view_discrepancy'
FIXTURE = 'KsValidationProbe.sys'


def fixture_module(modules):
    found = [m for m in modules if ntpath.basename(m['path']).lower() == FIXTURE.lower()]
    if len(found) != 1 or not found[0]['base'] or not found[0]['size']:
        raise ValueError('Expected exactly one real fixture module with base and size')
    return found[0]


def filtered_enum(real_function, suppressed_base):
    def enum(values, size, needed):
        ok = real_function(values, size, needed)
        if not ok:
            return ok
        amount = C.cast(needed, C.POINTER(W.DWORD)).contents.value
        slot_size = C.sizeof(C.c_void_p)
        if amount > size or amount % slot_size:
            return ok
        count = amount // slot_size
        kept = [values[i] for i in range(count) if values[i] != suppressed_base]
        for index, base in enumerate(kept):
            values[index] = base
        for index in range(len(kept), count):
            values[index] = None
        C.cast(needed, C.POINTER(W.DWORD)).contents.value = len(kept) * slot_size
        return ok
    return enum


def verify_cycle(before, user, after, fixture, events, streak):
    if before['module_status'] or after['module_status']:
        raise ValueError('AuxKlib module query failed')
    first = {m['base'] for m in before['modules']}
    last = {m['base'] for m in after['modules']}
    viewed = {m['base'] for m in user}
    if first != last:
        raise ValueError('Module list changed across bracketed PSAPI call')
    if fixture['base'] not in first & last or fixture['base'] in viewed:
        raise ValueError('Fixture was not present in both real AuxKlib snapshots and absent in PSAPI')
    if first - viewed != {fixture['base']} or viewed - last:
        raise ValueError('Other module discrepancy makes this cycle ambiguous')
    if streak < 3 and events:
        raise ValueError('CrossView reported before the persistence threshold')
    if streak >= 3:
        relevant = [e for e in events if e['type'] == 'driver_view_mismatch' and
                    e['direction'] == 'aux_only' and e['base'] == fixture['base'] and
                    e['consecutive'] == streak and
                    e['conclusion'] == 'inconclusive_not_proof_of_hidden_driver']
        if len(relevant) != 1 or len(events) != 1:
            raise ValueError('CrossView did not report the exact persistent fixture discrepancy')


class Evidence:
    def __init__(self, folder):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=False)
        self.data = dict(mode='live-cross-view-09', case=CASE, result='INCONCLUSIVE',
                         detail='Not started', cleanup_confirmed=False, results=[],
                         scope='A real signed fixture is loaded, then only this process\'s '
                               'PSAPI response is filtered; both-view kernel hiding is not tested')
        self.save()

    def save(self):
        temp = self.folder / 'report.json.tmp'
        temp.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        temp.replace(self.folder / 'report.json')
        (self.folder / 'report.md').write_text(
            f"# Live case 09\n\n[{self.data['result']}] {CASE}: {self.data['detail']}\n\n"
            f"Cleanup confirmed: {self.data['cleanup_confirmed']}\n\n"
            f"Scope: {self.data['scope']}\n", encoding='utf-8')

    def record(self, phase, value):
        with (self.folder / 'raw_events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(phase=phase, time_utc=datetime.now(timezone.utc).isoformat(),
                                         value=value)) + '\n')

    # ProbeService records through the existing report.note/add interface.
    def note(self, value):
        self.record('service', value)

    def add(self, case, result, detail):
        self.record('cleanup', dict(case=case, result=result, detail=detail))
        if result == 'ERROR':
            self.data.update(result='ERROR', detail=detail)


def run(args):
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'KernelSentinel'))
    sys.path.insert(0, str(root))
    from agent.windows import Windows, Driver
    from agent.analysis import CrossView
    from validation.probe_service import ProbeService
    from validation.driver_load import default_probe
    folder = Path(args.out) if args.out else root / 'runs' / ('live09_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    evidence = Evidence(folder.resolve())
    driver, service, api, original = None, None, None, None
    observed = False
    view_restored = False
    try:
        for name in ('windows.py', 'protocol.py', 'analysis.py'):
            file = root / 'KernelSentinel' / 'agent' / name
            evidence.data.setdefault('agent_sha256', {})[name] = hashlib.sha256(file.read_bytes()).hexdigest()
        path = Path(args.fixture_sys) if args.fixture_sys else default_probe(root)
        if not path.is_file():
            raise FileNotFoundError(f'Missing signed fixture SYS: {path}')
        api = Windows()
        driver = Driver(api)
        status = driver.status()
        if status['pid'] or status['lease_active']:
            raise ValueError('Stop watch/Run.ps1; leave KernelSentinel driver loaded')
        pre = driver.diagnostics()
        evidence.record('before', pre)
        if pre['module_status'] or any(ntpath.basename(m['path']).lower() == FIXTURE.lower()
                                        for m in pre['modules']):
            raise ValueError('Fixture already listed or AuxKlib query failed')
        service = ProbeService(path, api, evidence)
        tid = service.start()
        loaded = driver.diagnostics()
        evidence.record('loaded', loaded)
        fixture = fixture_module(loaded['modules'])
        evidence.data.update(fixture_base=fixture['base'], fixture_tid=tid,
                             fixture_path=str(path))
        api.enable_debug_privilege()
        unfiltered = api.drivers()
        evidence.record('unfiltered_psapi', unfiltered)
        if fixture['base'] not in {m['base'] for m in unfiltered}:
            raise ValueError('Unmodified PSAPI does not show this fixture; cannot establish suppression')
        cross = CrossView()
        original = api.enum_drivers
        api.enum_drivers = filtered_enum(original, fixture['base'])
        for number in (1, 2, 3):
            before = driver.diagnostics()
            user = api.drivers()
            after = driver.diagnostics()
            events = cross.compare(before, user, after)
            evidence.record(f'filtered_cycle_{number}', dict(before=before, user=user,
                                                               after=after, events=events))
            verify_cycle(before, user, after, fixture, events, number)
            if cross.streak.get(('aux_only', fixture['base'])) != number:
                raise ValueError('Production CrossView persistence counter did not advance')
            time.sleep(.1)
        observed = True
        print('[CHANGE_OBSERVED] Exact real fixture omitted from PSAPI for 3 bracketed cycles.', flush=True)
        api.enum_drivers = original
        original = None
        restored_user = api.drivers()
        restored_before = driver.diagnostics()
        restored_after = driver.diagnostics()
        restored_events = cross.compare(restored_before, restored_user, restored_after)
        evidence.record('restored_view', dict(user=restored_user, before=restored_before,
                                             after=restored_after, events=restored_events))
        if fixture['base'] not in {m['base'] for m in restored_user} or restored_events or cross.streak:
            raise ValueError('Unmodified PSAPI/CrossView did not return to normal')
        view_restored = True
    except (Exception, KeyboardInterrupt) as exc:
        evidence.data.update(result='ERROR' if isinstance(exc, (OSError, KeyboardInterrupt)) else 'FAIL',
                             detail=f'{type(exc).__name__}: {exc}')
    finally:
        if original is not None and api is not None:
            api.enum_drivers = original
        if service:
            try:
                service.close()
                if evidence.data['result'] != 'ERROR':
                    evidence.data['cleanup_confirmed'] = True
            except Exception as exc:
                evidence.data.update(result='ERROR', detail=f'Fixture cleanup failed: {exc}')
        if observed and view_restored and evidence.data['cleanup_confirmed']:
            try:
                after_unload = driver.diagnostics()
                evidence.record('after_unload', after_unload)
                if after_unload['module_status'] or any(ntpath.basename(m['path']).lower() == FIXTURE.lower()
                        for m in after_unload['modules']):
                    raise ValueError('Fixture module still listed after service stop')
                evidence.data.update(result='PASS', detail='Real fixture was present in AuxKlib and '
                    'unmodified PSAPI; process-local filtered PSAPI omitted only its base for '
                    '3 cycles, production mismatch event persisted, then unmodified view and '
                    'service cleanup were verified. Does not prove general hidden-driver detection.')
            except Exception as exc:
                evidence.data.update(result='FAIL', detail=f'After-cleanup check failed: {exc}')
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
    parser.add_argument('--fixture-sys')
    parser.add_argument('--out')
    return run(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
