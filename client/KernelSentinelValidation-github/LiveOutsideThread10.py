"""Case 10: real outside-module system thread, with owned signed fixture cleanup."""
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

CASE = '10.live_outside_thread'
REASON = 'system_thread_start_outside_listed_modules_persistent'
SERVICE = 'KsOutsideThreadProbe'


def module_for(modules, name):
    matches = [m for m in modules if ntpath.basename(m['path']).lower() == name.lower()]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one {name} module, found {len(matches)}')
    return matches[0]


def ranges(modules):
    return {(m['base'], m['size'], m['path'].lower()) for m in modules}


def verify_fixture(state, before, after):
    if before['module_status'] or after['module_status']:
        raise ValueError('Module-list query failed')
    if any(ntpath.basename(m['path']).lower() == 'ksoutsidethreadprobe.sys'
           for m in before['modules']):
        raise ValueError('Fixture already loaded before owned start')
    fixture = module_for(after['modules'], 'KsOutsideThreadProbe.sys')
    stub = state['stub']
    if (state['version'] != 1 or not state['tid'] or not state['start'] or
            len(stub) != 12 or stub[:2] != bytes.fromhex('48b8') or
            stub[10:] != bytes.fromhex('ffe0')):
        raise ValueError('Fixture state, thread id, or 12-byte trampoline invalid')
    destination = int.from_bytes(stub[2:10], 'little')
    if not fixture['base'] <= destination < fixture['base'] + fixture['size']:
        raise ValueError('Fixture trampoline does not target its loaded image')
    if any(m['base'] <= state['start'] < m['base'] + m['size'] for m in after['modules']):
        raise ValueError('Fixture start address is still inside a listed module')
    return fixture


def verify_sample(before, snapshot, after, events, state, fixture, expected_streak):
    if before['module_status'] or after['module_status'] or ranges(before['modules']) != ranges(after['modules']):
        raise ValueError('Kernel module set changed across thread snapshot')
    if module_for(after['modules'], 'KsOutsideThreadProbe.sys') != fixture:
        raise ValueError('Fixture module identity changed')
    if snapshot['status'] or snapshot['flags'] or snapshot['os_build'] != 19045:
        raise ValueError('Thread sensor status/flags/OS build are not supported')
    matches = [t for t in snapshot['threads'] if t['tid'] == state['tid'] and
               t['start'] == state['start'] and t['status'] == 0 and
               t['flags'] == 1 and t['create_time']]
    if len(matches) != 1:
        raise ValueError('Exact real fixture thread/start is missing or ambiguous')
    origins = [e for e in events if e['type'] == 'kernel_thread_origin' and
               e['tid'] == state['tid'] and e['start'] == state['start'] and
               e['create_time'] == matches[0]['create_time'] and
               e['classification'] == 'outside_listed_modules']
    if len(origins) != 1 or origins[0]['consecutive'] != expected_streak:
        raise ValueError('Outside-module finding did not persist for exact thread identity')
    if REASON not in dict(__import__('agent.analysis', fromlist=['findings']).findings(origins[0], {})) and expected_streak >= 3:
        raise ValueError('Production analysis did not produce persistent outside-thread finding')
    if any(e['type'] == 'coverage_gap' for e in events):
        raise ValueError('Thread scan contained a coverage gap')
    return matches[0], origins[0]


class Evidence:
    def __init__(self, folder):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=False)
        self.data = dict(mode='live-outside-thread-10', case=CASE, result='INCONCLUSIVE',
                         detail='Not started', cleanup_confirmed=False, results=[],
                         scope='One owned system thread whose real start address is an '
                               'executable pool trampoline outside loaded module ranges')
        self.save()

    def save(self):
        temp = self.folder / 'report.json.tmp'
        temp.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        temp.replace(self.folder / 'report.json')
        (self.folder / 'report.md').write_text(
            f"# Live case 10\n\n[{self.data['result']}] {CASE}: {self.data['detail']}\n\n"
            f"Cleanup confirmed: {self.data['cleanup_confirmed']}\n\n"
            f"Scope: {self.data['scope']}\n", encoding='utf-8')

    def record(self, phase, value):
        with (self.folder / 'raw_events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(phase=phase, time_utc=datetime.now(timezone.utc).isoformat(),
                                         value=value)) + '\n')

    def state(self, phase, detail):
        self.data.update(phase=phase, detail=detail)
        self.save()
        print(f'[{phase}] {detail}', flush=True)


class Fixture:
    def __init__(self, sys_path, api, evidence):
        self.path, self.api, self.evidence = sys_path.resolve(strict=True), api, evidence
        if self.path.name.lower() != SERVICE.lower() + '.sys':
            raise ValueError('Fixture path must name KsOutsideThreadProbe.sys')
        self.created = False
        self.sc = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/sc.exe')

    def command(self, *args):
        output = subprocess.run([self.sc, *args], capture_output=True, text=True,
                                errors='replace', timeout=30, creationflags=0x08000000)
        self.evidence.record('service', dict(args=list(args), returncode=output.returncode,
                                             stdout=output.stdout, stderr=output.stderr))
        return output

    def start(self):
        from validation.probe_service import driver_image_path
        query = self.command('query', SERVICE)
        if query.returncode != 1060:
            raise ValueError('Fixture service already exists or query failed; no existing service will be modified')
        image = driver_image_path(self.path)
        created = self.command('create', SERVICE, 'type=', 'kernel', 'start=', 'demand', 'binPath=', image)
        if created.returncode:
            raise RuntimeError(f'Fixture service create failed: {created.returncode}')
        self.created = True
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           'SYSTEM\\CurrentControlSet\\Services\\' + SERVICE) as key:
            registered, kind = winreg.QueryValueEx(key, 'ImagePath')
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or driver_image_path(registered).lower() != image.lower():
            raise ValueError(f'Fixture ImagePath differs from requested path: {registered}')
        started = self.command('start', SERVICE)
        if started.returncode:
            raise RuntimeError(f'Fixture load failed: sc.exe exit={started.returncode}. '
                               'For 577, sign this SYS with the VM test certificate.')
        handle = self.api.create(r'\\.\KsOutsideThreadProbe', 0x80000000, 0, None, 3, 0, None)
        if handle in (None, C.c_void_p(-1).value):
            raise C.WinError(C.get_last_error())
        try:
            output, size = C.create_string_buffer(32), W.DWORD()
            ioctl = (0x8367 << 16) | (1 << 14) | (0x800 << 2)
            if not self.api.control(handle, ioctl, None, 0, output, 32, C.byref(size), None):
                raise C.WinError(C.get_last_error())
            if size.value != 32:
                raise ValueError('Fixture state size mismatch')
            version, tid, start, stub, reserved = struct.unpack('<IIQ12sI', output.raw)
            if reserved:
                raise ValueError('Fixture returned nonzero reserved field')
            return dict(version=version, tid=tid, start=start, stub=stub)
        finally:
            self.api.close(handle)

    def close(self):
        if not self.created:
            return False
        stopped = self.command('stop', SERVICE)
        if stopped.returncode not in (0, 1062):
            raise RuntimeError(f'Fixture stop failed: sc.exe exit={stopped.returncode}')
        for _ in range(30):
            query = self.command('query', SERVICE)
            if query.returncode == 0 and 'STOPPED' in query.stdout:
                deleted = self.command('delete', SERVICE)
                if deleted.returncode:
                    raise RuntimeError(f'Fixture delete failed: sc.exe exit={deleted.returncode}')
                return True
            time.sleep(.1)
        raise RuntimeError('Fixture did not reach STOPPED state')


def run(args):
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / 'KernelSentinel'))
    sys.path.insert(0, str(root))
    from agent.windows import Windows, Driver
    from agent.sensors import ThreadOrigins
    from agent.analysis import findings
    output = Path(args.out) if args.out else root / 'runs' / ('live10_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    evidence = Evidence(output.resolve())
    driver, fixture = None, None
    ready = False
    try:
        for name in ('windows.py', 'protocol.py', 'analysis.py', 'sensors.py'):
            file = root / 'KernelSentinel' / 'agent' / name
            evidence.data.setdefault('agent_sha256', {})[name] = hashlib.sha256(file.read_bytes()).hexdigest()
        sys_path = Path(args.fixture_sys) if args.fixture_sys else root / 'KsOutsideThreadProbe.sys'
        if not sys_path.is_file():
            raise FileNotFoundError(f'Missing signed fixture SYS: {sys_path}')
        api = Windows()
        driver = Driver(api)
        status = driver.status()
        if status['pid'] or status['lease_active']:
            raise ValueError('Stop watch/Run.ps1 before this test; leave KernelSentinel loaded')
        before = driver.diagnostics()
        evidence.record('before', before)
        if before['module_status']:
            raise ValueError('Preload kernel module list failed')
        fixture = Fixture(sys_path, api, evidence)
        state = fixture.start()
        # bytes cannot be serialized as JSON; keep exact hex evidence.
        evidence.record('fixture_state', {**state, 'stub': state['stub'].hex()})
        after = driver.diagnostics()
        evidence.record('loaded', after)
        module = verify_fixture(state, before, after)
        evidence.data.update(fixture_tid=state['tid'], fixture_start=state['start'],
                             fixture_module=module)
        evidence.state('FIXTURE_LOADED', f'Exact thread {state["tid"]}, outside start={state["start"]:#x}')
        origins = ThreadOrigins()
        create_time = None
        for expected in (1, 2, 3):
            first = driver.diagnostics()
            snapshot = driver.thread_scan()
            last = driver.diagnostics()
            events = origins.compare(first, snapshot, last)
            evidence.record(f'snapshot_{expected}', dict(before=first, snapshot=snapshot,
                                                        after=last, findings=events))
            thread, event = verify_sample(first, snapshot, last, events, state, module, expected)
            if create_time is None:
                create_time = thread['create_time']
            elif thread['create_time'] != create_time:
                raise ValueError('Fixture thread creation time changed across observations')
            if expected == 3 and REASON not in dict(findings(event, {})):
                raise ValueError('Production analysis did not report persistent outside-module thread')
            time.sleep(.1)
        ready = True
        evidence.state('CHANGE_OBSERVED', 'Same real thread/start classified outside module in 3 snapshots.')
    except (Exception, KeyboardInterrupt) as exc:
        evidence.data.update(result='ERROR' if isinstance(exc, (OSError, KeyboardInterrupt)) else 'FAIL',
                             detail=f'{type(exc).__name__}: {exc}')
    finally:
        if fixture:
            try:
                cleaned = fixture.close()
                evidence.data['cleanup_confirmed'] = cleaned
                evidence.record('cleanup', dict(stopped=cleaned, deleted=cleaned))
            except Exception as exc:
                evidence.data.update(result='ERROR', detail=f'Fixture cleanup failed: {exc}')
                evidence.record('cleanup', dict(error=str(exc)))
        if ready and evidence.data['cleanup_confirmed']:
            try:
                post = driver.diagnostics()
                evidence.record('after_cleanup', post)
                if post['module_status'] or any(ntpath.basename(m['path']).lower() == 'ksoutsidethreadprobe.sys'
                        for m in post['modules']):
                    raise ValueError('Fixture module still listed after unload')
                evidence.data.update(result='PASS', detail='Real system thread started outside every loaded '
                                     'module range; exact identity persisted 3 scans, production finding '
                                     'appeared, fixture service stopped/deleted and module absent afterward.')
            except Exception as exc:
                evidence.data.update(result='FAIL', detail=f'After-cleanup verification failed: {exc}')
        if driver:
            try:
                driver.close()
            except Exception as exc:
                evidence.data.update(result='ERROR', detail=f'KernelSentinel device close failed: {exc}')
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
