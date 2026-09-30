"""Stateful defensive observations; anomalies are evidence, not cheat verdicts."""

class ThreadOrigins:
    def __init__(self):
        self.streak = {}

    def compare(self, before, snapshot, after):
        def modules(d):
            values = d.get('modules', [])
            if d.get('module_status') != 0 or not values:
                return None
            if any(m['base'] <= 0 or m['size'] <= 0 or
                   m['base'] + m['size'] > 1 << 64 for m in values):
                return None
            return {(m['base'], m['size'], m['path'].casefold()) for m in values}

        b, a = modules(before), modules(after)
        if snapshot['status'] != 0 or snapshot['flags'] or b is None or a is None or b != a:
            self.streak.clear()
            return [{'type': 'coverage_gap', 'reason': 'kernel_thread_scan_inconclusive',
                     'scan_status': snapshot['status'], 'module_sets_stable': b is not None and b == a}]
        candidates, events = {}, []
        checked = failed = 0
        for thread in snapshot['threads']:
            start = thread['start']
            if thread['status'] != 0 or thread['flags'] != 1 or not start or not thread['create_time']:
                failed += 1
                continue
            checked += 1
            if any(base <= start < base + size for base, size, _ in a):
                continue
            key = (thread['pid'], thread['tid'], thread['create_time'], start)
            # One observation per identity per scan even if a corrupt snapshot repeats it.
            count = self.streak.get(key, 0) + 1
            if key in candidates:
                continue
            candidates[key] = count
            events.append({'type': 'kernel_thread_origin', **thread,
                'classification': 'outside_listed_modules', 'consecutive': count,
                'scope': 'system_thread_start_address_not_current_instruction',
                'module_count': len(a), 'os_build': snapshot['os_build']})
        self.streak = candidates
        events.append({'type': 'kernel_thread_scan_summary', 'checked': checked,
            'query_failed': failed, 'lookup_failed': snapshot['lookup_failed'],
            'outside': len(candidates), 'duration_ms': snapshot['duration_ms'],
            'os_build': snapshot['os_build']})
        if failed or snapshot['lookup_failed']:
            events.append({'type': 'coverage_gap', 'reason': 'kernel_thread_queries_incomplete',
                'query_failed': failed, 'lookup_failed': snapshot['lookup_failed']})
        return events


class CallbackHealth:
    """Match this collector's read-only handle probe to successful OB pre/post events.
    No writes, no injected code. Missing events can also indicate a broken pipeline.
    """
    def __init__(self, actor_pid, target_pid):
        self.actor, self.target = actor_pid, target_pid
        self.pending = None
        self.misses = 0

    def begin(self, now, timestamp_100ns, generation, opened):
        if self.pending is not None:
            raise RuntimeError('callback health probe already pending')
        self.pending = dict(began=now, timestamp=timestamp_100ns, generation=generation,
                            opened=opened, pre=set(), post=set(), lost=False)

    def invalidate(self):
        if self.pending is not None:
            self.pending['lost'] = True
        self.misses = 0

    def observe(self, event):
        p = self.pending
        if p is None:
            return
        if (event.get('actor_pid') != self.actor or event.get('target_pid') != self.target or
            event.get('generation') != p['generation'] or
            event.get('timestamp_100ns', 0) < p['timestamp'] or
            event.get('flags', 0) & (1 | 2 | 8 | 128) or
            event.get('original_access') != 0x1000):
            return
        kind, op = event['type'], event.get('operation_id')
        if not op:
            return
        if kind == 'handle_pre':
            p['pre'].add(op)
        elif kind == 'handle_post' and event['status'] & 0x80000000 == 0:
            p['post'].add(op)

    def finish(self, now, drained=True):
        p = self.pending
        if p is None or now - p['began'] < 5:
            return None
        self.pending = None
        if not p['opened'] or p['lost'] or not drained or now - p['began'] > 15:
            outcome = 'inconclusive'
            self.misses = 0
        elif p['pre'] & p['post']:
            outcome = 'observed'
            self.misses = 0
        else:
            outcome = 'missing'
            self.misses += 1
        return {'type': 'callback_health', 'outcome': outcome, 'consecutive_misses': self.misses,
                'actor_pid': self.actor, 'target_pid': self.target, 'generation': p['generation'],
                'probe_access': 0x1000, 'pre_count': len(p['pre']), 'post_count': len(p['post']),
                'scope': 'collector_handle_callback_path_not_entire_callback_table'}
