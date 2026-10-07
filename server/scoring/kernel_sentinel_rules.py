"""Source-audited provisional rules; no replay-calibrated accuracy claim."""
from collections.abc import Mapping

VERSION = 'kernel-source-provisional-v1'
STRONG = {
    'kernel_entry_bytes_changed_since_driver_load': ('kernel_diagnostics', 'changed'),
    'sentinel_dispatch_pointer_changed_since_driver_load': ('kernel_diagnostics', 'dispatch'),
    'kernel_executable_sections_changed_since_first_scan': ('kernel_code_scan', 'flags'),
    'system_thread_start_outside_listed_modules_persistent': ('kernel_thread_origin', 'consecutive'),
}
WEAK = {
    'sensitive_handle_rights_granted', 'sensitive_handle_rights_restricted',
    'sensitive_handle_request_failed', 'object_callback_probe_missing_persistent',
}
ZERO = {'approved_defense_module_access'}
ACCESS_SCOPES = {'hide_anywhere': 0x101010, 'esp': 0x1040, 'autopaint': 0x1000}


def classify(raw_score, evidence, reasons):
    """Return normal/strong/advisory/pending/unavailable without changing raw."""
    if evidence.get('measurement_valid') is False or evidence.get('status') in ('ERROR', 'OFFLINE'):
        return 'unavailable'
    if evidence.get('measurement_valid') is not True:
        return 'pending'
    errors = evidence.get('measurement_errors')
    if not isinstance(errors, list) or errors:
        return 'pending'
    if type(raw_score) not in (int, float) or raw_score not in (0, 1, 2, 3, 4):
        return 'pending'
    if not isinstance(reasons, (list, tuple)) or any(not isinstance(r, str) for r in reasons):
        return 'pending'
    reasons = set(reasons)
    kind = evidence.get('type')
    cycle = kind == 'sensor_cycle' and evidence.get('sample_kind') == 'sensor_cycle'
    counts = evidence.get('access_approval_counts')
    if cycle:
        if not isinstance(counts, Mapping) or any(type(counts.get(k)) is not int or counts[k] < 0
                for k in ('approved_observation', 'unresolved')):
            return 'pending'
    known = all(r in STRONG or r in WEAK or r in ZERO or r == 'configured_file_sha256_match'
                or r.startswith(('name_pattern_match:', 'sentinel_dispatch_outside_listed_modules:'))
                for r in reasons)
    if not known:
        return 'pending'
    if raw_score == 0:
        if evidence.get('status') != 'NORMAL':
            return 'pending'
        if cycle:
            return 'normal' if not reasons and counts['unresolved'] == 0 else 'pending'
        approval = evidence.get('access_approval')
        if kind == 'handle_post' and reasons == ZERO and isinstance(approval, Mapping):
            mask = approval.get('allowed_process_access')
            if (approval.get('classification') == 'approved_observation' and approval.get('approval_valid') is True
                    and approval.get('source_sha256_verified') is True and type(mask) is int and mask >= 0
                    and approval.get('actor_module') in ACCESS_SCOPES
                    and mask == ACCESS_SCOPES[approval['actor_module']]
                    and all(type(evidence.get(k)) is int and evidence[k] >= 0 and not evidence[k] & ~mask
                            for k in ('original_access', 'before_access', 'granted_access'))):
                return 'normal'
        return 'pending'
    if not reasons:
        return 'pending'
    if raw_score == 4:
        if 'configured_file_sha256_match' not in reasons:
            return 'pending'
        if cycle:
            return 'strong'
        digest = evidence.get('sha256')
        if (kind == 'file_evidence' and evidence.get('image_kind') in ('game_image', 'kernel_image')
                and isinstance(digest, str) and len(digest) == 64
                and all(c in '0123456789abcdefABCDEF' for c in digest)):
            return 'strong'
        return 'pending'
    strong = reasons & STRONG.keys()
    if raw_score == 3:
        if not strong:
            return 'pending'
        if cycle:
            return 'strong'
        for reason in strong:
            expected_kind, field = STRONG[reason]
            value = evidence.get(field)
            if kind != expected_kind or type(value) is not int:
                continue
            if (kind == 'kernel_diagnostics' and value > 0 and evidence.get('valid') == 15
                    and evidence.get('failed') == 0 and evidence.get('module_status') == 0):
                return 'strong'
            if kind == 'kernel_code_scan' and evidence.get('status') == 'DETECTED' and value & 2 and evidence.get('source_status') == 0:
                return 'strong'
            if (kind == 'kernel_thread_origin' and value >= 3
                    and evidence.get('classification') == 'outside_listed_modules' and evidence.get('source_status') == 0):
                return 'strong'
        return 'pending'
    if strong or 'configured_file_sha256_match' in reasons:
        return 'pending'  # Inconsistent producer score; never correct/rewrite it.
    if reasons & {'sensitive_handle_rights_granted', 'sensitive_handle_rights_restricted'}:
        return 'pending'  # Actor/scope unresolved is not clean, and does not prove cheating.
    return 'advisory'
