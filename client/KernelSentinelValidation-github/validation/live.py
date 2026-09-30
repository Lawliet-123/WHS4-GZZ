"""Own-process Windows integration tests for the unmodified KernelSentinel v0.2.1.

Observe/enforce temporarily create/load/remove the fixture driver service.
No OS setting changes or kernel patching is performed here.
The device is exclusive: stop agent.main watch before starting this runner.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'KernelSentinel'))
sys.path.insert(0, str(ROOT))
from agent.windows import Windows, Driver, bind
from agent import protocol as P
from agent.analysis import CrossView, findings
from agent.sensors import ThreadOrigins, CallbackHealth
from agent.files import FileWorker
from validation.checks import check_handle, PROCESS_STRIP, THREAD_STRIP
from validation.driver_load import default_probe, run_driver_load
from validation.live_evidence import append_results

CREATE_NO_WINDOW = 0x08000000
INITIAL = b'KS_VALIDATION_INITIAL_0123456789'
CHANGED = b'KS_VALIDATION_CHANGED_0123456789'


def stamp():
    # Older Python versions use a coarser wall clock on Windows. Match the
    # driver's KeQuerySystemTimePrecise instead of rejecting valid callbacks.
    if not hasattr(stamp, 'clock'):
        stamp.clock = bind(C.WinDLL('kernel32'), 'GetSystemTimePreciseAsFileTime', None, [C.POINTER(W.FILETIME)])
    value = W.FILETIME()
    stamp.clock(C.byref(value))
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


class Api(Windows):
    def __init__(self):
        super().__init__()
        k = C.WinDLL('kernel32', use_last_error=True)
        self.open_thread = bind(k, 'OpenThread', W.HANDLE, [W.DWORD, W.BOOL, W.DWORD])
        self.duplicate = bind(k, 'DuplicateHandle', W.BOOL,
            [W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD])
        self.read = bind(k, 'ReadProcessMemory', W.BOOL,
            [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)])
        self.write = bind(k, 'WriteProcessMemory', W.BOOL,
            [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)])
        self.query = bind(C.WinDLL('ntdll'), 'NtQueryObject', C.c_long,
            [W.HANDLE, W.ULONG, C.c_void_p, W.ULONG, C.POINTER(W.ULONG)])

    def granted(self, handle):
        # PUBLIC_OBJECT_BASIC_INFORMATION: ULONG Attributes, ACCESS_MASK GrantedAccess,
        # ULONG HandleCount, ULONG PointerCount, ULONG Reserved[10].
        info, size = (W.ULONG * 14)(), W.ULONG()
        status = self.query(handle, 0, info, C.sizeof(info), C.byref(size))
        return status & 0xFFFFFFFF, int(info[1])


def target():
    api = Api()
    buffer = C.create_string_buffer(INITIAL, 128)
    keep_running = threading.Event()
    ready = threading.Event()
    thread_id = []

    def worker():
        thread_id.append(threading.get_native_id())
        ready.set()
        keep_running.wait()

    worker_thread = threading.Thread(target=worker)
    worker_thread.start()
    ready.wait(5)
    h, created, image = api.process(os.getpid())
    api.close(h)
    libraries = []
    print(json.dumps(dict(pid=os.getpid(), tid=thread_id[0], created=created,
                          image=image, address=C.addressof(buffer))), flush=True)
    try:
        for line in sys.stdin:
            try:
                command = json.loads(line)
                action = command['action']
                result = {'ok': True}
                if action == 'thread':
                    ids = []
                    t = threading.Thread(target=lambda: ids.append(threading.get_native_id()))
                    t.start(); t.join(5)
                    if t.is_alive():
                        raise RuntimeError('Test thread did not finish')
                    result['tid'] = ids[0]
                elif action == 'image':
                    path = str((ROOT / 'bin' / 'KsValidationFixture.dll').resolve())
                    lib = C.WinDLL(path)
                    lib.KsValidationMarker.restype = W.ULONG
                    libraries.append(lib)
                    result.update(path=path, base=lib._handle, marker=lib.KsValidationMarker())
                elif action == 'buffer':
                    result['data'] = buffer.raw[:len(INITIAL)].hex()
                elif action == 'reset':
                    C.memmove(buffer, INITIAL, len(INITIAL))
                elif action == 'exit':
                    print(json.dumps(result), flush=True)
                    break
                else:
                    raise ValueError('Unknown target action')
                print(json.dumps(result), flush=True)
            except Exception as exc:
                print(json.dumps({'ok': False, 'error': str(exc)}), flush=True)
    finally:
        keep_running.set()
        worker_thread.join(5)


class Child:
    def __init__(self):
        self.proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--target'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', creationflags=CREATE_NO_WINDOW)
        self.lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.identity = self.response()
        except BaseException:
            self.proc.kill(); self.proc.wait(timeout=10)
            raise

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def response(self):
        line = self.lines.get(timeout=10)
        if line is None:
            raise RuntimeError('Test target exited: ' + self.proc.stderr.read()[-1000:])
        result = json.loads(line)
        if result.get('ok') is False:
            raise RuntimeError(result['error'])
        return result

    def command(self, action):
        self.proc.stdin.write(json.dumps({'action': action}) + '\n')
        self.proc.stdin.flush()
        return self.response()

    def close(self):
        if self.proc.poll() is None:
            try:
                self.command('exit')
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill(); self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()


class Report:
    def __init__(self, folder, mode):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=False)
        self.raw = (self.folder / 'raw_events.jsonl').open('w', encoding='utf-8')
        self.truth = (self.folder / 'truth.jsonl').open('w', encoding='utf-8')
        self.results = []
        self.mode = mode

    def event(self, event):
        self.raw.write(json.dumps(event, ensure_ascii=False) + '\n'); self.raw.flush()

    def note(self, event):
        self.truth.write(json.dumps(event, ensure_ascii=False) + '\n'); self.truth.flush()

    def add(self, case, result, detail, layer=None):
        item = dict(case=case, result=result, detail=detail,
                    layer=layer or ('live' if self.mode != 'smoke' else 'stimulus'))
        self.results.append(item)
        self.note(item)
        print(f'[{result}] {case}: {detail}', flush=True)

    def expect(self, case, condition, detail):
        self.add(case, 'PASS' if condition else 'FAIL', detail)

    def close(self):
        result = {'mode': self.mode, 'scope': 'Current own-process checks plus explicitly labeled historical live evidence when supplied; kernel-level driver hiding remains untested',
                  'results': self.results}
        (self.folder / 'report.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        rows = ['# KernelSentinel validation', '', f'Mode: {self.mode}', '',
                '| Case | Result | Detail |', '|---|---|---|']
        for r in self.results:
            rows.append('| ' + ' | '.join(str(r[k]).replace('|', '/').replace('\n', ' ') for k in ('case', 'result', 'detail')) + ' |')
        (self.folder / 'report.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')
        self.raw.close(); self.truth.close()


class Session:
    def __init__(self, driver, report):
        self.driver, self.report = driver, report
        self.events = []
        self.lost = 0

    def drain(self):
        for _ in range(64):
            batch, lost = self.driver.events()
            self.lost += lost
            if lost:
                self.report.event(dict(type='coverage_gap', reason='queue_overflow', dropped=lost))
            for e in batch:
                self.report.event(e)
            self.events.extend(batch)
            if not batch:
                return
        raise RuntimeError('Event queue did not drain; results are incomplete')

    def beat(self):
        self.driver.call(P.IO_HEARTBEAT)

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.beat(); self.drain()
            time.sleep(min(.1, max(0, end - time.monotonic())))
        self.drain()


def probe(api, child, access, is_thread=False, source=None):
    identity = child.identity
    record = dict(type='handle_truth', actor_pid=os.getpid(), target_pid=identity['pid'],
                  tid=identity['tid'] if is_thread else 0, requested=access,
                  thread=is_thread, duplicate=source is not None, begin=stamp())
    C.set_last_error(0)
    if source is not None:
        out = W.HANDLE()
        ok = api.duplicate(W.HANDLE(-1), source, W.HANDLE(-1), C.byref(out), access, False, 0)
        h = out.value if ok else None
    else:
        h = api.open_thread(access, False, identity['tid']) if is_thread else api.open_process(access, False, identity['pid'])
    record.update(opened=bool(h), error=C.get_last_error() if not h else 0, end=stamp())
    if h:
        try:
            record['query_status'], record['granted'] = api.granted(h)
        finally:
            api.close(h)
    return record


def memory_checks(api, child, report, enforce):
    identity = child.identity
    child.command('reset')
    for operation, rights in [('read', 0x1010), ('write', 0x1028)]:
        h = api.open_process(rights, False, identity['pid'])
        if not h:
            report.add('04.memory_' + operation, 'INCONCLUSIVE', 'Could not obtain the test handle')
            continue
        try:
            transferred = C.c_size_t()
            data = C.create_string_buffer(CHANGED if operation == 'write' else len(INITIAL))
            C.set_last_error(0)
            ok = (api.write if operation == 'write' else api.read)(h, identity['address'], data,
                                                                 len(INITIAL), C.byref(transferred))
            error = C.get_last_error() if not ok else 0
            actual = bytes.fromhex(child.command('buffer')['data'])
            expected_block = operation == 'write' and enforce
            good = (not ok and error == 5 and transferred.value == 0 and actual == INITIAL) if expected_block else (
                bool(ok) and transferred.value == len(INITIAL) and
                (data.raw[:len(INITIAL)] == INITIAL if operation == 'read' else actual == CHANGED))
            report.note(dict(type='memory_truth', operation=operation, success=bool(ok), error=error,
                             bytes=transferred.value, target_pid=identity['pid'], data=actual.hex()))
            report.expect('04.memory_' + operation, good,
                          f'success={bool(ok)}, error={error}, bytes={transferred.value}; own target buffer')
        finally:
            api.close(h)
            child.command('reset')


def run(args):
    report = Report(args.out, args.mode)
    api = child = driver = session = None
    held = []
    try:
        api = Api()
        if args.mode != 'smoke':
            driver = Driver(api)
            session = Session(driver, report)
            session.drain()  # Separate old queued observations from this run.
            session.events.clear()
            session.lost = 0
        child = Child()
        identity = child.identity
        report.note(dict(type='target_identity', **identity))
        creation_handle, creation, image = api.process(identity['pid'])
        held.append(creation_handle)
        if creation != identity['created'] or os.path.normcase(image) != os.path.normcase(identity['image']):
            raise RuntimeError('Target identity mismatch')
        process_source = api.open_process(0x1000 | PROCESS_STRIP | 0x10, False, identity['pid'])
        thread_source = api.open_thread(0x800 | THREAD_STRIP | 8, False, identity['tid'])
        if process_source: held.append(process_source)
        if thread_source: held.append(thread_source)
        generation = None
        if driver:
            prior = driver.status()
            try:
                driver.policy(identity['pid'], 1, creation + 1)
            except OSError as exc:
                current = driver.status()
                report.expect('01.reject_wrong_creation', exc.winerror == 87 and all(current[k] == prior[k] for k in ('pid', 'mode', 'generation', 'create_time')),
                              f'Win32 error={exc.winerror}; policy identity must remain unchanged')
            else:
                report.add('01.reject_wrong_creation', 'FAIL', 'Mismatched creation time was accepted')
                driver.policy()
            driver.policy(identity['pid'], 2 if args.mode == 'enforce' else 1, creation)
            state = driver.status(); generation = state['generation']
            report.event(dict(type='driver_status', **state))
            report.expect('01.register_valid', state['pid'] == identity['pid'] and state['create_time'] == creation and
                          state['mode'] == (2 if args.mode == 'enforce' else 1) and state['lease_active'] == 1,
                          f'pid={identity["pid"]}, generation={generation}, lease={state["lease_active"]}')
            session.wait(.2)
            report.expect('02.process_create', any(e['type'] == 'process_create' and e['target_pid'] == identity['pid'] and
                          e['process_create_time'] == creation for e in session.events), 'Match exact target PID and creation time')
        else:
            report.add('01.registration', 'NOT_RUN', 'Smoke mode does not open KernelSentinel')

        for is_thread, requests, source in (
            (False, [0x1000, 0x1010, 0x1028, 0x1002, 0x1040, 0x1801, 0x187B], process_source),
            (True, [0x800, 0x808, 0x810, 0x802, 0x801, 0x81B], thread_source)):
            for duplicate in (False, True):
                if duplicate and not source:
                    report.add('03.duplicate_source', 'INCONCLUSIVE', 'Pre-registration source handle unavailable')
                    continue
                for rights in requests:
                    if session:
                        session.beat(); session.drain()
                    before = len(session.events) if session else 0
                    lost_before = session.lost if session else 0
                    truth = probe(api, child, rights, is_thread, source if duplicate else None)
                    report.note(truth)
                    name = f'03.{"thread" if is_thread else "process"}.{"duplicate" if duplicate else "open"}.{rights:04x}'
                    if session:
                        session.wait(.1)
                        result, detail = check_handle(truth, session.events[before:], args.mode, generation,
                                                      session.lost - lost_before)
                        report.add(name, result, detail)
                    else:
                        report.expect(name, truth['opened'] and truth.get('query_status') == 0 and
                                      truth.get('granted', 0) & rights == rights, 'Stimulus only; no anticheat verdict')

        if session: session.beat()
        memory_checks(api, child, report, args.mode == 'enforce')
        if process_source:
            data, count = C.create_string_buffer(len(INITIAL)), C.c_size_t()
            ok = api.read(process_source, identity['address'], data, len(INITIAL), C.byref(count))
            report.expect('04.preexisting_handle_read', bool(ok) and data.raw[:len(INITIAL)] == INITIAL,
                          'Pre-registration read handle remains usable; this is a documented limitation')
        thread = child.command('thread')
        report.note(dict(type='thread_truth', target_pid=identity['pid'], tid=thread['tid']))
        if session:
            session.wait(.2)
            for kind in ('thread_create', 'thread_exit'):
                report.expect('02.' + kind, any(e['type'] == kind and e['target_pid'] == identity['pid'] and
                              e['thread_id'] == thread['tid'] and e['generation'] == generation for e in session.events),
                              f'Exact test thread {thread["tid"]}')
        else:
            report.expect('02.thread_stimulus', thread['tid'] > 0, 'Own target created and joined a thread')
        dll = ROOT / 'bin' / 'KsValidationFixture.dll'
        if dll.exists():
            loaded = child.command('image')
            report.note(dict(type='image_truth', **loaded))
            if session:
                session.wait(.2)
                images = [e for e in session.events if e['type'] == 'game_image' and e['target_pid'] == identity['pid']
                          and e['generation'] == generation and e['image_base'] == loaded['base']
                          and e['image_size'] > 0 and e['path'].casefold().endswith('ksvalidationfixture.dll')]
                report.expect('05.dll_load', bool(images) and loaded['marker'] == 0x4B535654,
                              'Newly loaded fixture path, base, nonzero image size and export marker')
            else:
                report.expect('05.dll_stimulus', loaded['marker'] == 0x4B535654, 'Loaded own fixture DLL')
            worker = FileWorker(api, signatures=args.signatures)
            try:
                worker.submit(dict(type='game_image', path=str(dll), generation=generation, sequence=1))
                deadline, evidence = time.monotonic() + 20, None
                while time.monotonic() < deadline:
                    try:
                        evidence = worker.results.get_nowait(); break
                    except queue.Empty:
                        if session: session.wait(.1)
                        else: time.sleep(.1)
                if evidence is None:
                    report.add('12.file_hash', 'INCONCLUSIVE', 'File worker timed out')
                else:
                    report.event(evidence)
                    digest = hashlib.sha256(dll.read_bytes()).hexdigest()
                    matches = findings(evidence, {'module_sha256': [digest]})
                    report.expect('12.file_hash', evidence['sha256'] == digest and
                                  ('configured_file_sha256_match', 4) in matches, 'Actual FileWorker hash and configured rule')
                    report.expect('12.signature_not_cheat', findings(evidence, {}) == [], 'Signature alone produces no cheat finding')
                    if args.signatures:
                        signature = evidence.get('signature')
                        unsigned = (isinstance(signature, dict) and signature.get('Status') == 2 and
                                    signature.get('StatusName', 'NotSigned') == 'NotSigned' and
                                    not signature.get('SignerThumbprint'))
                        report.add('12.signature_collection', 'PASS' if unsigned else 'INCONCLUSIVE',
                                   'Unsigned DLL classified as NotSigned (Status=2)' if unsigned else
                                   f'Expected unsigned DLL; observed signature={signature!r}')
                        signed_path = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'kernel32.dll'
                        if Path(signed_path).is_file():
                            worker.submit(dict(type='game_image', path=str(signed_path),
                                               generation=generation, sequence=2))
                            try:
                                signed = worker.results.get(timeout=20)
                                report.event(signed)
                                signer = signed.get('signature')
                                valid = (isinstance(signer, dict) and signer.get('Status') == 0 and
                                         signer.get('StatusName', 'Valid') == 'Valid' and
                                         bool(signer.get('SignerThumbprint')))
                                report.add('12.signature_valid_control', 'PASS' if valid else 'INCONCLUSIVE',
                                           f'Signed Windows DLL control: status={signer.get("StatusName", signer.get("Status"))}, '
                                           f'thumbprint={signer.get("SignerThumbprint")}' if isinstance(signer, dict)
                                           else f'Signed Windows DLL control signature unavailable: {signer!r}')
                            except queue.Empty:
                                report.add('12.signature_valid_control', 'INCONCLUSIVE', 'Signed Windows DLL signature query timed out')
                        else:
                            report.add('12.signature_valid_control', 'NOT_RUN', 'Signed Windows DLL control missing')
            finally:
                worker.close()
        else:
            report.add('05.dll_load', 'NOT_RUN', 'Run Build.ps1 to build the fixture DLL first')

        if driver:
            if getattr(args, 'skip_driver_load', False):
                report.add('05.driver_load', 'NOT_RUN', 'Explicit --skip-driver-load request')
            else:
                run_driver_load(args.probe_sys or default_probe(ROOT), api, driver, session, report, stamp)
            kernel_checks(api, driver, session, report, identity, generation, args.code_scans)
            # Close independent handles before graceful target exit. No TerminateProcess.
            for h in held: api.close(h)
            held.clear()
            child.close(); child = None
            session.wait(.2)
            report.expect('02.target_exit', any(e['type'] == 'target_exit' and e['target_pid'] == identity['pid'] and
                          e['process_create_time'] == creation and e['generation'] == generation for e in session.events),
                          'Protected-target exit event, exact PID and creation time')
            state = driver.status()
            report.expect('01.exit_clears_policy', state['pid'] == 0 and state['mode'] == 0,
                          'Policy cleared after target exit')
            report.add('00.collection_loss', 'PASS' if session.lost == 0 else 'INCONCLUSIVE', f'Dropped during run: {session.lost}')
        if not driver:
            report.add('05.driver_load', 'NOT_RUN', 'Smoke mode does not load kernel drivers')
        if not args.defer_live:
            append_results(report, args.live_evidence_root if driver else None)
    except Exception as exc:
        report.add('00.runner', 'ERROR', f'{type(exc).__name__}: {exc}')
    finally:
        if driver:
            try:
                driver.policy()
            except Exception as exc:
                report.add('00.cleanup', 'ERROR', str(exc))
            driver.close()
        if api:
            for h in held: api.close(h)
        if child: child.close()
        report.close()
    return 1 if any(r['result'] in ('FAIL', 'ERROR') for r in report.results) else 2 if any(
        r['result'] in ('NOT_RUN', 'INCONCLUSIVE') for r in report.results) else 0


def kernel_checks(api, driver, session, report, identity, generation, code_scans):
    session.beat()
    before = driver.diagnostics(); report.event(before)
    report.add('06.entry_read', 'PASS' if before['valid'] == 15 and before['failed'] == 0 else 'INCONCLUSIVE',
               f"valid={before['valid']:#x}, changed={before['changed']:#x}, failed={before['failed']:#x}; read coverage only")
    if before['changed']:
        report.add('06.unexpected_entry_change', 'INCONCLUSIVE', 'An uncontrolled change exists; inspect raw evidence')
    pointers = before['dispatch_pointers']
    provenance = before['module_status'] == 0 and bool(before['modules'])
    inside = provenance and all(any(m['base'] <= p['current'] < m['base'] + m['size'] for m in before['modules']) for p in pointers)
    report.add('08.dispatch_baseline', 'PASS' if inside and not before['dispatch'] else 'INCONCLUSIVE',
               'Unchanged own pointers inside module ranges' if inside and not before['dispatch'] else 'Unexpected change or incomplete module data')
    cross = CrossView()
    api.enable_debug_privilege()
    for index in range(3):
        session.beat()
        first = driver.diagnostics()
        try:
            user = api.drivers()
        except Exception as exc:
            report.add('09.cross_view', 'INCONCLUSIVE', str(exc)); break
        after = driver.diagnostics()
        differences = cross.compare(first, user, after)
        report.note(dict(type='cross_view_truth', cycle=index, before=first, user=user, after=after, differences=differences))
        if (first['module_status'] or after['module_status'] or not first['modules'] or not after['modules'] or
                any(not m['base'] for m in first['modules'] + after['modules'])):
            report.add('09.cross_view', 'INCONCLUSIVE', 'Kernel module query failed or returned unusable addresses'); break
        if differences:
            report.add('09.cross_view', 'INCONCLUSIVE', 'Persistent unexplained discrepancy; inspect truth.jsonl'); break
        if index == 2:
            report.add('09.cross_view', 'PASS', 'Three real bracketed snapshots; no persistent discrepancy')
        session.wait(.1)
    session.beat()
    before = driver.diagnostics()
    snapshot = driver.thread_scan(); report.event(snapshot)
    after = driver.diagnostics()
    events = ThreadOrigins().compare(before, snapshot, after)
    for event in events: report.event(event)
    report.add('10.thread_snapshot', 'PASS' if snapshot['status'] == 0 and not snapshot['flags'] and
               any(e['type'] == 'kernel_thread_scan_summary' and e['checked'] > 0 and e['query_failed'] == 0 and
                   e['lookup_failed'] == 0 for e in events) else 'INCONCLUSIVE',
               f"OS build={snapshot['os_build']}, status={snapshot['status']:#x}; supported target is 19045 x64")
    health = CallbackHealth(os.getpid(), identity['pid'])
    session.beat(); session.drain()
    start, now, offset, lost_before = stamp(), time.monotonic(), len(session.events), session.lost
    opened = driver.probe_callback(identity['pid'])
    health.begin(now, start, generation, opened)
    session.wait(5.1)
    for event in session.events[offset:]: health.observe(event)
    if session.lost != lost_before: health.invalidate()
    result = health.finish(time.monotonic()); report.event(result)
    report.add('11.callback_health', {'observed': 'PASS', 'missing': 'FAIL', 'inconclusive': 'INCONCLUSIVE'}[result['outcome']],
               json.dumps(result))
    compared, baselines, failed, changed = 0, 0, 0, 0
    for _ in range(code_scans):
        session.beat()
        result = driver.code_scan(); report.event(result)
        if result['status']: failed += 1
        elif result['flags'] == 4: baselines += 1
        elif result['flags'] == 1: compared += 1
        elif result['flags'] & 2: changed += 1
        session.drain()
        if compared: break
    report.add('07.live_code_comparison', 'PASS' if compared and not changed else 'INCONCLUSIVE',
               f'comparisons={compared}, new_baselines={baselines}, failed_scans={failed}, changes={changed}; bounded scan, no mutation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--mode', choices=['smoke', 'observe', 'enforce'], default='smoke')
    parser.add_argument('--out', help='New report directory; existing directories are refused')
    parser.add_argument('--signatures', action='store_true')
    parser.add_argument('--code-scans', type=int, default=520)
    parser.add_argument('--probe-sys', help='Override the default fixture SYS; observe/enforce now run the real driver-load test by default')
    parser.add_argument('--skip-driver-load', action='store_true', help='Explicitly skip the real fixture-driver load')
    parser.add_argument('--live-evidence-root', help='Import latest separate live06..live10 VM reports from this runs directory as historical evidence')
    parser.add_argument('--defer-live', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.target:
        target(); return 0
    if not args.out: parser.error('--out is required')
    if not 0 <= args.code_scans <= 4096: parser.error('--code-scans must be 0..4096')
    if args.probe_sys and args.mode == 'smoke': parser.error('--probe-sys requires observe or enforce')
    if args.probe_sys and args.skip_driver_load: parser.error('--probe-sys cannot be combined with --skip-driver-load')
    if args.live_evidence_root and args.mode == 'smoke': parser.error('--live-evidence-root requires observe or enforce')
    if args.live_evidence_root and args.defer_live: parser.error('--live-evidence-root cannot be combined with --defer-live')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
