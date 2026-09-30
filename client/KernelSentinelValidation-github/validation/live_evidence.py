"""Import completed, separately run VM mutation checks as historical evidence.

These checks cannot run inside the ordinary observe/enforce session: they require
an interactive kernel debugger or a separately loaded fixture driver. Importing
their reports never starts a fixture, patches memory, or claims a current-run test.
"""
import hashlib
import json
from pathlib import Path


CASES = {
    '06.live_entry_mutation': ('live06_', 'live-entry-06', 'restoration_confirmed',
                               {'before': 1, 'mutated': 3, 'restored': 3}),
    '07.live_code_mutation': ('live07_', 'live-code-07', 'restoration_confirmed',
                              {'baseline_or_comparison': 1, 'mutated_scan': 1, 'restored_scan': 1}),
    '08.live_dispatch_mutation': ('live08_', 'live-dispatch-08', 'restoration_confirmed',
                                  {'before': 1, 'mutated': 3, 'restored': 3}),
    '09.live_cross_view_discrepancy': ('live09_', 'live-cross-view-09', 'cleanup_confirmed',
                                       {'unfiltered_psapi': 1, 'filtered_cycle_1': 1,
                                        'filtered_cycle_2': 1, 'filtered_cycle_3': 1,
                                        'restored_view': 1, 'after_unload': 1}),
    '10.live_outside_thread': ('live10_', 'live-outside-thread-10', 'cleanup_confirmed',
                               {'fixture_state': 1, 'snapshot_1': 1, 'snapshot_2': 1,
                                'snapshot_3': 1, 'after_cleanup': 1}),
}


def read_case(root, case):
    prefix, mode, cleanup_key, required = CASES[case]
    candidates = sorted((p for p in root.glob(prefix + '*') if p.is_dir()),
                        key=lambda p: p.name, reverse=True)
    if not candidates:
        return 'NOT_RUN', 'No separate VM report found', None
    folder = candidates[0]
    source = folder / 'report.json'
    raw = folder / 'raw_events.jsonl'
    try:
        if not source.is_file() or not raw.is_file():
            raise ValueError('Missing report.json or raw_events.jsonl')
        data = json.loads(source.read_text(encoding='utf-8-sig'))
        if data.get('case') != case or data.get('mode') != mode:
            raise ValueError('Report case/mode does not match the expected live validator')
        if data.get('result') != 'PASS':
            return 'INCONCLUSIVE', f'Latest separate report is {data.get("result")}: {folder}', None
        if data.get(cleanup_key) is not True:
            raise ValueError('Mutation/fixture restoration was not confirmed')
        rows = data.get('results')
        if not isinstance(rows, list) or len(rows) != 1 or any(
                r.get('case') != case or r.get('result') != 'PASS' or r.get('layer') != 'live'
                for r in rows):
            raise ValueError('Embedded live result is missing or inconsistent')
        counts = {name: 0 for name in required}
        with raw.open(encoding='utf-8-sig') as stream:
            for line in stream:
                if not line.strip():
                    continue
                item = json.loads(line)
                phase = item.get('phase')
                if phase in counts:
                    counts[phase] += 1
        if any(counts[name] < amount for name, amount in required.items()):
            raise ValueError(f'Missing measured phases: {counts}')
        if case == '07.live_code_mutation':
            if data.get('baseline_hash') == data.get('changed_hash') or not data.get('scan_counts'):
                raise ValueError('Changed executable hash or scan counts missing')
        if case == '09.live_cross_view_discrepancy' and 'both-view kernel hiding is not tested' not in data.get('scope', ''):
            raise ValueError('Cross-view report lacks the explicit hiding limitation')
        detail = (f'Historical separate VM PASS ({folder.name}); {data.get("detail", "")} '
                  f'SHA-256 report={hashlib.sha256(source.read_bytes()).hexdigest()}')
        return 'PASS', detail, str(folder)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return 'INCONCLUSIVE', f'Could not verify latest separate report {folder}: {type(exc).__name__}: {exc}', None


def append_results(report, evidence_root=None):
    root = Path(evidence_root).resolve() if evidence_root else None
    if root and not root.is_dir():
        raise ValueError(f'Live evidence directory does not exist: {root}')
    for case in CASES:
        if root:
            result, detail, source = read_case(root, case)
        else:
            result, detail, source = ('NOT_RUN', 'Use -LiveEvidenceRoot to import a separately completed VM report', None)
        report.add(case, result, detail, layer='historical_live' if source else 'live')
        if source:
            report.note(dict(type='imported_live_evidence', case=case, source=source))
    # The controlled process-local PSAPI discrepancy does not prove a driver
    # hidden at kernel level. Preserve this separate, honest coverage limit.
    report.add('09.live_hidden_driver', 'NOT_RUN',
               'Kernel-level driver hiding was not performed; controlled PSAPI omission is a narrower check')
