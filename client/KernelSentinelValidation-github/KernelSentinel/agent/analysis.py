"""Evidence rules. A score is a review priority, not proof of cheating."""
import fnmatch
import ntpath

PROCESS_MASK = 0x87b
THREAD_MASK = 0x1b

def validate_config(config):
    allowed = {'process_patterns', 'driver_patterns', 'module_patterns', 'driver_sha256', 'module_sha256'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('unknown policy keys')
    for key, values in config.items():
        if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values):
            raise ValueError(f'{key}: expected a list of nonempty strings')
        if key.endswith('sha256') and any(len(x) != 64 or any(c not in '0123456789abcdefABCDEF' for c in x) for x in values):
            raise ValueError(f'{key}: expected 64-digit SHA-256 values')
    return config

def match_path(path, patterns):
    path = path.replace('/', '\\').lower()
    for pattern in patterns:
        p = pattern.replace('/', '\\').lower()
        if fnmatch.fnmatchcase(path if '\\' in p else ntpath.basename(path), p):
            return pattern
    return None

def findings(event, config):
    """Return (reason, score) pairs. Do not double-score pre/post operations."""
    kind = event['type']
    result = []
    key = {'process_create': 'process_patterns', 'kernel_image': 'driver_patterns',
           'driver_snapshot': 'driver_patterns', 'game_image': 'module_patterns'}.get(kind)
    if key and not event.get('flags', 0) & 16:
        match = match_path(event.get('path', ''), config.get(key, []))
        if match:
            result.append((f'name_pattern_match:{match}', 2))
    if kind == 'handle_post' and not event['flags'] & 3:
        # NT_SUCCESS means signed NTSTATUS >= 0. Failure is not successful access.
        mask = THREAD_MASK if event['flags'] & 8 else PROCESS_MASK
        if event['status'] & 0x80000000:
            if event['before_access'] & mask:
                result.append(('sensitive_handle_request_failed', 0))
        elif event['flags'] & 4:
            result.append(('sensitive_handle_rights_restricted', 1))
        elif event['granted_access'] & mask:
            result.append(('sensitive_handle_rights_granted', 1))
    if kind == 'handle_pre' and event['flags'] & 64:
        result.append(('handle_post_context_unavailable', 0))
    if kind == 'kernel_diagnostics':
        if event['changed']:
            result.append(('kernel_entry_bytes_changed_since_driver_load', 3))
        if event['dispatch']:
            result.append(('sentinel_dispatch_pointer_changed_since_driver_load', 3))
        if event['failed'] or event['valid'] != 15 or event['module_status'] != 0:
            result.append(('kernel_diagnostics_incomplete', 0))
        # Pointer provenance requires a successful complete module list. Unknown != malicious.
        if event['module_status'] == 0 and event['modules']:
            for i, ptr in enumerate(event['dispatch_pointers']):
                if not any(m['base'] <= ptr['current'] < m['base'] + m['size'] for m in event['modules']):
                    result.append((f'sentinel_dispatch_outside_listed_modules:{i}', 2))
    if kind == 'file_evidence':
        key = 'driver_sha256' if event['image_kind'] == 'kernel_image' else 'module_sha256'
        digest = event.get('sha256')
        if digest and digest.lower() in {x.lower() for x in config.get(key, [])}:
            result.append(('configured_file_sha256_match', 4))
        # Invalid/unknown/unsigned signatures are stored, never equated to cheats.
    if kind == 'kernel_code_scan':
        if event['status'] != 0:
            result.append(('kernel_code_scan_incomplete', 0))
        elif event['flags'] & 2:
            result.append(('kernel_executable_sections_changed_since_first_scan', 3))
    if kind == 'kernel_thread_origin' and event.get('status') == 0:
        if event.get('classification') == 'outside_listed_modules' and event.get('consecutive', 0) >= 3:
            result.append(('system_thread_start_outside_listed_modules_persistent', 3))
    if kind == 'callback_health':
        if event['outcome'] == 'missing' and event['consecutive_misses'] >= 3:
            result.append(('object_callback_probe_missing_persistent', 2))
        elif event['outcome'] == 'inconclusive':
            result.append(('object_callback_probe_inconclusive', 0))
    if kind in ('coverage_gap', 'driver_view_mismatch', 'target_exit'):
        result.append((kind, 0))
    return result

class CrossView:
    """Bracket PSAPI with two AuxKlib snapshots; require 3 consecutive differences."""
    def __init__(self):
        self.streak = {}
        self.last_status = {'type':'driver_cross_view_status','outcome':'inconclusive','reason':'not_run'}
    def compare(self, before, user, after):
        self.last_status = {'type':'driver_cross_view_status','outcome':'inconclusive','reason':'source_unavailable'}
        if before['module_status'] or after['module_status'] or user is None:
            self.streak.clear()
            return []
        b = {m['base'] for m in before['modules']}
        a = {m['base'] for m in after['modules']}
        u = {m['base'] for m in user}
        if not b or not a or not u or 0 in b | a | u:
            self.streak.clear()
            self.last_status['reason']='empty_or_invalid_module_list'
            return []
        candidates = {('aux_only', x) for x in (b & a) - u}
        candidates |= {('psapi_only', x) for x in u - (a | b)}
        self.streak = {key: self.streak.get(key, 0)+1 for key in candidates}
        self.last_status.update(outcome='inconclusive' if b!=a else ('difference' if candidates else 'matched'),
            reason='module_churn' if b!=a else 'stable_lists_compared',
            aux_before=len(b),aux_after=len(a),psapi=len(u),candidate_count=len(candidates),
            persistent_count=sum(n>=3 for n in self.streak.values()))
        return [{'type': 'driver_view_mismatch', 'direction': key[0], 'base': key[1],
                 'consecutive': count, 'conclusion': 'inconclusive_not_proof_of_hidden_driver'}
                for key, count in self.streak.items() if count >= 3]
