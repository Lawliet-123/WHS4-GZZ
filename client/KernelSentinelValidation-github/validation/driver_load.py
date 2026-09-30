"""Actual fixture load -> installed Sentinel event -> unload. No simulated events."""
import ntpath
from pathlib import Path
from validation.probe_service import ProbeService


def default_probe(root):
    # Preserve and prefer an existing (possibly signed) fixture.
    existing = Path(root)/'bin/driver/KsValidationProbe.sys'
    return existing if existing.is_file() else Path(root)/'driver-load-fixture/KsValidationProbe.sys'


def assess_load(events, before, after, path, started, lost, resolve):
    wanted = ntpath.normcase(ntpath.normpath(str(path)))
    def matches(value):
        try:
            return ntpath.normcase(ntpath.normpath(resolve(value))) == wanted
        except (ValueError, OSError):
            return False
    if lost:
        return 'INCONCLUSIVE', f'Event queue lost {lost} records during fixture load', []
    if before['module_status'] or after['module_status']:
        return 'INCONCLUSIVE', 'Cannot verify load against before/after kernel module snapshots', []
    old = [m for m in before['modules'] if matches(m['path'])]
    modules = [m for m in after['modules'] if matches(m['path'])]
    if old:
        return 'ERROR', 'Fixture was already present before this test; fresh load not proven', []
    if len(modules) != 1 or not modules[0]['base'] or modules[0]['size'] <= 0:
        return 'FAIL', 'Started fixture is missing or ambiguous in the kernel module snapshot', []
    module = modules[0]
    hits = [e for e in events if e['type'] == 'kernel_image' and e['timestamp_100ns'] >= started
            and matches(e['path']) and e['image_base'] == module['base'] and e['image_size'] == module['size']]
    detail = f"Fresh fixture load: matching events={len(hits)}, base={module['base']:#x}, size={module['size']}, path={path}"
    return ('PASS' if len(hits) == 1 else 'FAIL'), detail, hits


def run_driver_load(path, api, driver, session, report, clock, service_factory=ProbeService):
    fixture = None
    recorded = False
    stage = 'prepare'
    try:
        path = Path(path).resolve()
        report.note(dict(type='driver_load_plan', path=str(path), operation='create/start/observe/stop/delete fixture service'))
        if not path.is_file():
            raise FileNotFoundError(f'Missing fixture SYS: {path}; extract the driver-load fix or build the fixture')
        session.beat(); session.drain()
        before = driver.diagnostics(); report.event(before)
        if before['module_status']:
            report.add('05.driver_load', 'INCONCLUSIVE', 'Pre-load kernel module enumeration failed')
            return
        if any(ntpath.basename(m['path']).casefold() == 'ksvalidationprobe.sys' for m in before['modules']):
            raise RuntimeError('KsValidationProbe is already loaded; no existing driver/service will be stopped or overwritten')
        fixture = service_factory(path, api, report)
        initial_lost = session.lost
        started = clock()
        stage = 'start fixture'
        tid = fixture.start()
        stage = 'observe fresh load'
        session.wait(.2)
        after = driver.diagnostics(); report.event(after)
        result, detail, hits = assess_load(session.events, before, after, path, started,
                                            session.lost-initial_lost, api.local_path)
        report.add('05.driver_load', result, detail); recorded = True
        if result == 'PASS':
            stage = 'read fixture thread'
            snapshot = driver.thread_scan(); report.event(snapshot)
            own = [t for t in snapshot['threads'] if t['tid'] == tid and t['status'] == 0 and t['flags'] == 1]
            good = snapshot['status'] == 0 and not snapshot['flags'] and len(own) == 1 and any(
                e['image_base'] <= own[0]['start'] < e['image_base'] + e['image_size'] for e in hits)
            report.add('10.fixture_system_thread', 'PASS' if good else 'INCONCLUSIVE',
                       f'Known fixture system thread tid={tid}; start must be inside its loaded module')
    except Exception as exc:
        case = '10.fixture_system_thread' if recorded else '05.driver_load'
        report.add(case, 'ERROR', f'{stage}: {type(exc).__name__}: {exc}')
    finally:
        if fixture is not None:
            try:
                fixture.close()
            except Exception as exc:
                report.add('05.probe_cleanup', 'ERROR', f'{type(exc).__name__}: {exc}; inspect KsValidationProbe service')
