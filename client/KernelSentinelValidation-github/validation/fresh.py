"""Run new measurements in this invocation and consolidate their child reports."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation.debug_bridge import Bridge
DEBUG_CASES = (
    ('06.live_entry_mutation', 'LiveEntry06.py', 'live06', 'restoration_confirmed'),
    ('07.live_code_mutation', 'LiveCode07.py', 'live07', 'restoration_confirmed'),
    ('08.live_dispatch_mutation', 'LiveDispatch08.py', 'live08', 'restoration_confirmed'),
)
CASES = (
    ('09.live_cross_view_discrepancy', 'LiveCrossView09.py', 'live09', 'cleanup_confirmed'),
    ('10.live_outside_thread', 'LiveOutsideThread10.py', 'live10', 'cleanup_confirmed'),
)


def read_result(folder, case, cleanup_key):
    source = folder / 'report.json'
    raw = folder / 'raw_events.jsonl'
    if not source.is_file() or not raw.is_file() or raw.stat().st_size == 0:
        raise ValueError(f'Child report or measured raw events missing: {folder}')
    report = json.loads(source.read_text(encoding='utf-8-sig'))
    if report.get('case') != case or not report.get(cleanup_key):
        raise ValueError(f'Child identity or restoration missing: {source}')
    rows = report.get('results')
    if report.get('result') != 'PASS' or not isinstance(rows, list) or len(rows) != 1 or rows[0].get('result') != 'PASS':
        raise ValueError(f'Child did not PASS: {source}')
    return dict(case=case, result='PASS', layer='new_live',
                detail=report.get('detail'), source=str(source),
                report_sha256=hashlib.sha256(source.read_bytes()).hexdigest())


def write_report(folder, mode, rows):
    data = dict(mode=mode, scope='New measurements in this invocation; each child report has its own raw evidence',
                timestamp_utc=datetime.now(timezone.utc).isoformat(), results=rows)
    (folder / 'report.json').write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    markdown = ['# KernelSentinel fresh validation', '', f'Mode: {mode}', '',
                '| Case | Result | Measurement |', '|---|---|---|']
    for row in rows:
        markdown.append(f"| {row['case']} | {row['result']} | {row['detail'].replace('|', '/')} |")
    (folder / 'report.md').write_text('\n'.join(markdown) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('observe', 'enforce'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--signatures', action='store_true')
    parser.add_argument('--probe-sys')
    parser.add_argument('--skip-driver-load', action='store_true')
    parser.add_argument('--code-scans', type=int, default=520)
    parser.add_argument('--bridge-config', type=Path, help='Defaults to bridge.json beside Run.ps1')
    args = parser.parse_args()
    folder = args.out.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    rows = []
    bridge_config = args.bridge_config or ROOT / 'bridge.json'
    try:
        addresses = Bridge(bridge_config).info()
        (folder / 'bridge_preflight.json').write_text(json.dumps(dict(
            host_debugger_connected=True, addresses={key: hex(value) for key, value in addresses.items()}),
            indent=2), encoding='utf-8')
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        rows.append(dict(case='00.bridge_preflight', result='ERROR',
                         detail=f'Host debugger bridge unavailable: {type(exc).__name__}: {exc}', layer='new_live'))
        write_report(folder, args.mode, rows)
        return 1
    command = [sys.executable, '-u', str(ROOT / 'validation/live.py'), '--mode', args.mode,
               '--out', str(folder / 'basic'), '--code-scans', str(args.code_scans), '--defer-live']
    if args.signatures: command.append('--signatures')
    if args.probe_sys: command += ['--probe-sys', args.probe_sys]
    if args.skip_driver_load: command.append('--skip-driver-load')
    base = subprocess.run(command, check=False)
    basic_file = folder / 'basic/report.json'
    if not basic_file.is_file():
        rows.append(dict(case='00.basic', result='ERROR', detail='Basic run did not produce report.json', layer='new_live'))
        write_report(folder, args.mode, rows)
        return 1
    basic = json.loads(basic_file.read_text(encoding='utf-8-sig'))
    rows.extend(basic.get('results', []))
    if base.returncode not in (0, 2) or any(r['result'] in ('FAIL', 'ERROR') for r in rows):
        rows.append(dict(case='00.fresh_sequence', result='ERROR', detail='Basic run failed; fixture runs were not started', layer='new_live'))
        write_report(folder, args.mode, rows)
        return 1
    for case, filename, subfolder, cleanup_key in DEBUG_CASES + CASES:
        target = folder / subfolder
        command = [sys.executable, '-u', str(ROOT / filename), '--out', str(target)]
        if case.startswith(('06.', '07.')):
            command += ['--function-address', hex(addresses['entry']), '--bridge', str(bridge_config)]
        elif case.startswith('08.'):
            command += ['--original', hex(addresses['dispatch']), '--replacement',
                        hex(addresses['replacement']), '--bridge', str(bridge_config)]
        child = subprocess.run(command, check=False)
        try:
            if child.returncode:
                raise ValueError(f'Live child returned exit code {child.returncode}; see {target}')
            row = read_result(target, case, cleanup_key)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            rows.append(dict(case=case, result='ERROR', detail=str(exc), layer='new_live'))
            write_report(folder, args.mode, rows)
            return 1
        rows.append(row)
        print(f"[PASS] {case}: new measurement in {target}", flush=True)
        write_report(folder, args.mode, rows)
    rows.append(dict(case='09.live_hidden_driver', result='NOT_RUN',
                     detail='The current sensor compares AuxKlib and PSAPI; a driver missing from both does not create a discrepancy',
                     layer='new_live'))
    write_report(folder, args.mode, rows)
    return 2 if any(r['result'] in ('NOT_RUN', 'INCONCLUSIVE') for r in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
