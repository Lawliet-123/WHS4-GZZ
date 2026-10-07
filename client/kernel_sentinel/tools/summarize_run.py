"""Read raw JSONL; report observations and missing coverage without cheat verdicts."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.feature_status import FeatureStatus, render_table

def summarize(lines):
    kinds, outcomes, scan_status, findings = Counter(), Counter(), Counter(), Counter()
    scans = checked = failed = outside = 0
    durations = []
    handle_results = Counter()
    features = FeatureStatus()
    for number, line in enumerate(lines, 1):
        if not line.strip(): continue
        try: e = json.loads(line)
        except ValueError as ex: raise ValueError(f'line {number}: invalid JSON') from ex
        kind = e.get('type', 'unknown')
        features.observe(e)
        kinds[kind] += 1
        if kind == 'handle_post':
            key = (f"actor={e.get('actor_pid')}, target={e.get('target_pid')}, "
                   f"object={'thread' if e.get('flags',0)&8 else 'process'}, "
                   f"granted=0x{e.get('granted_access',0):X}, status=0x{e.get('status',0):08X}")
            handle_results[key] += 1
        if kind == 'kernel_thread_snapshot':
            scans += 1
            scan_status[f"0x{e['status']:08X}"] += 1
        elif kind == 'kernel_thread_scan_summary':
            checked += e['checked']; failed += e['query_failed']; outside += e['outside']
        elif kind == 'callback_health':
            outcomes[e['outcome']] += 1
            if e['outcome'] == 'missing' and e['consecutive_misses'] >= 3:
                findings['persistent_callback_probe_missing'] += 1
        elif kind == 'kernel_thread_origin' and e.get('status') == 0 and e.get('consecutive', 0) >= 3:
            findings['persistent_thread_origin_outside_modules'] += 1
        elif kind == 'kernel_code_scan' and e.get('status') == 0 and e.get('flags', 0) & 2:
            findings['kernel_code_changed'] += 1
        elif kind == 'sensor_cycle':
            durations.append(e['duration_ms'])
    durations.sort()
    return {'feature_status': features.report(), 'event_counts': dict(kinds), 'thread_snapshots': scans, 'thread_scan_status': dict(scan_status),
        'thread_checks_not_unique_threads': checked, 'thread_query_failures': failed,
        'outside_observations_not_unique_threads': outside, 'callback_health': dict(outcomes),
        'raw_repeated_findings': dict(findings),
        'raw_repeated_findings_scope': 'thread persistence, callback health, code changes only; not all common_events findings',
        'separate_facts': {
            'driver_load_event_count': kinds['kernel_image'],
            'handle_operation_results_not_memory_reads': dict(handle_results),
            'direct_kernel_memory_read': {'sensor': 'not_implemented', 'observation': 'unavailable'},
            'driver_load_or_handle_grant_proves_memory_read': False},
        'cycle_ms_p95': durations[math.ceil(len(durations)*.95)-1] if durations else None,
        'interpretation': 'No alerts does not establish absence of kernel cheats. Runtime validation requires independent ground truth.'}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw', required=True)
    p.add_argument('--format', choices=('json','table','both'), default='json', help='JSON remains the default for compatibility')
    p.add_argument('--out', help='optional NEW output file (UTF-8); never overwrite existing output')
    args = p.parse_args()
    with open(args.raw, encoding='utf-8-sig') as f: result = summarize(f)
    parts=[]
    if args.format in ('json','both'):parts.append(json.dumps(result, indent=2, ensure_ascii=False))
    if args.format in ('table','both'):parts.append(render_table(result['feature_status']))
    output='\n\n'.join(parts)
    if args.out:
        with open(args.out,'x',encoding='utf-8') as f:f.write(output+'\n')
    print(output)

if __name__ == '__main__': main()
